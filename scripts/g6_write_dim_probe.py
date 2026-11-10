"""G6 写入维差分布补测（恢复法 · 副本沙箱内 · 节点2 裁定「甲」）。

背景：G6 首窗只完成「读维」差分布（G5 维度全 0）；**写入维**（`factor_value` float）
容差未定值——因为它需要「同一起点上 old/new 各写一遍」。

方法（规避双份副本 148.8GB）：
  1. 备份副本 aux → `_aux_pristine.db`
  2. 构造输入：N 码 × M 时点，含**两分支**（新观测 / 修订）
  3. old 路径（QS_QFQ_OBS_INCREMENTAL=0）写 ⇒ 捕获新增行（rowid > before_max）
  4. **恢复** aux（从 pristine 拷回）⇒ 回到同一起点
  5. new 路径（=1）写 ⇒ 捕获新增行
  6. 比对 + 差分布：
     - 主口径：**逐行 (code, factor_time) 差值全集分位数**（分组不改变分位数）
     - 辅口径：**trade_date 计数分布**（factor_time → UTC 日期）
     - 浮点口径：**末位差单列 + 非零差占比**

红线：全程在副本目录内，绝不触生产；恢复步骤后必须校验 rowid 水位复位。
用法：
  python scripts/g6_write_dim_probe.py --replica D:\\qs_g6_replica_20261110 \
      --codes 200 --points 10 --out docs/evidence/g6-write-dim.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

RUN_ID = "g6probe"          # 两遍必须同 run_id，否则 alert_id/last_seen 不可比
EPS_MULT = 1.5              # 远超 1% epsilon ⇒ 必触发修订


def _max_rowid(con: sqlite3.Connection, table: str) -> int:
    row = con.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {table}").fetchone()
    return int(row[0])


def _capture(con: sqlite3.Connection, table: str, after_rowid: int, cols: str):
    return con.execute(
        f"SELECT {cols} FROM {table} WHERE rowid > ? ORDER BY rowid", [after_rowid]
    ).fetchall()


def _dump_last(con: sqlite3.Connection, codes: list[str]) -> dict:
    """只取**输入涉及的 code** 的最后态（逐 code 走 PK，避免全表相关子查询）。

    ⚠️ 2026-11-10 实测教训：原实现用 `revision_no = (SELECT MAX(...) ...)` 对所有
    4,500 万行做相关子查询 ⇒ CPU 烧 500s+ 仍不返回（job 被卡 22 分钟、产出未落盘）。
    改为按 code 分批查（20 个 code = 20 次 PK 查询，秒级）。
    """
    result: dict = {}
    for cd in codes:
        rows = con.execute(
            "SELECT factor_time, factor_value, revision_no FROM qfq_factor_observation o "
            "WHERE asset_type='STOCK' AND code=? AND revision_no = ("
            "  SELECT MAX(revision_no) FROM qfq_factor_observation p "
            "  WHERE p.asset_type=o.asset_type AND p.code=o.code AND p.factor_time=o.factor_time)",
            [cd]).fetchall()
        for ft, fv, rev in rows:
            result[("STOCK", cd, int(ft))] = (float(fv), int(rev))
    return result


def _percentiles(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "p50": 0.0, "p90": 0.0, "p99": 0.0, "max": 0.0}
    s = sorted(vals)
    n = len(s)
    return {
        "n": n,
        "p50": s[max(0, int(n * 0.50) - 1)],
        "p90": s[max(0, int(n * 0.90) - 1)],
        "p99": s[max(0, int(n * 0.99) - 1)],
        "max": s[-1],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replica", required=True)
    ap.add_argument("--codes", type=int, default=200)
    ap.add_argument("--points", type=int, default=10)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    root = Path(args.replica)
    aux = root / "qfq_aux.db"
    pristine = root / "_aux_pristine.db"
    rep: dict = {"replica": str(root), "codes": args.codes, "points": args.points}

    # ---------- 0) 备份 ----------
    t0 = time.perf_counter()
    if pristine.exists():
        pristine.unlink()
    shutil.copy2(aux, pristine)
    rep["backup_s"] = round(time.perf_counter() - t0, 1)
    rep["pristine_bytes"] = pristine.stat().st_size

    # ---------- 1) 构造输入（从副本真实数据取码与时点） ----------
    con = sqlite3.connect(str(aux))
    con.execute("PRAGMA query_only=ON")
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT code FROM qfq_factor_observation WHERE asset_type='STOCK' "
        "ORDER BY code LIMIT ?", [args.codes]).fetchall()]
    inputs: list[tuple] = []
    branch_counts = {"revise": 0, "new": 0}
    for cd in codes:
        ft_rows = con.execute(
            "SELECT factor_time, factor_value FROM qfq_factor_observation "
            "WHERE asset_type='STOCK' AND code=? "
            "ORDER BY factor_time DESC LIMIT ?", [cd, args.points]).fetchall()
        if not ft_rows:
            continue
        half = max(1, len(ft_rows) // 2)
        for i, (ft, fv) in enumerate(ft_rows):
            if i < half:   # 修订分支：原时点 + 放大值
                inputs.append(("STOCK", cd, int(ft), float(fv) * EPS_MULT))
                branch_counts["revise"] += 1
            else:          # 新观测分支：不存在的时点（远偏移，避免与已选时点撞键）
                inputs.append(("STOCK", cd, int(ft) + 400 * 86400_000 + i,
                               float(fv) * 1.01))
                branch_counts["new"] += 1
    con.close()

    # 去重：同 (asset_type, code, factor_time) 只保留首条（否则 record_observations
    # 会以「同一 run 内同键冲突」整批拒绝——该保护是既有 fail-safe，不得绕过）
    _seen: set[tuple] = set()
    _dedup: list[tuple] = []
    _dropped = 0
    for row in inputs:
        k = (row[0], row[1], int(row[2]))
        if k in _seen:
            _dropped += 1
            continue
        _seen.add(k)
        _dedup.append(row)
    inputs = _dedup
    rep["input_rows"] = len(inputs)
    rep["input_dropped_dup"] = _dropped
    rep["branch_counts"] = branch_counts
    if not inputs:
        print("FATAL: 输入为空")
        return 2

    # ---------- 2) old 路径 ----------
    os.environ["QS_QFQ_OBS_INCREMENTAL"] = "0"
    from quantstudio.pipeline.qfq_observation import ObservationStore

    def run_path(flag: str):
        os.environ["QS_QFQ_OBS_INCREMENTAL"] = flag
        c0 = sqlite3.connect(str(aux))
        obs_before = _max_rowid(c0, "qfq_factor_observation")
        alt_before = _max_rowid(c0, "qfq_factor_revision_alert")
        c0.close()
        store = ObservationStore(aux_db=aux)
        t = time.perf_counter()
        res = store.record_observations(inputs, RUN_ID)
        wall = time.perf_counter() - t
        c1 = sqlite3.connect(str(aux))
        new_obs = _capture(c1, "qfq_factor_observation", obs_before,
                           "asset_type, code, factor_time, factor_value, revision_no")
        new_alt = _capture(c1, "qfq_factor_revision_alert", alt_before,
                           "asset_type, code, factor_time, revision_no, status")
        last = _dump_last(c1, codes)
        c1.close()
        return {"wall_s": round(wall, 3),
                "new_obs": new_obs, "new_alt": new_alt, "last": last,
                "result": {"inserted": getattr(res, "inserted_count", None),
                           "revised": getattr(res, "revised_count", None),
                           "unchanged": getattr(res, "unchanged_count", None)}}

    old = run_path("0")

    # ---------- 3) 恢复 ----------
    t0 = time.perf_counter()
    aux.unlink()
    shutil.copy2(pristine, aux)
    rep["restore_s"] = round(time.perf_counter() - t0, 1)
    c_chk = sqlite3.connect(str(aux))
    rep["restored_obs_max_rowid"] = _max_rowid(c_chk, "qfq_factor_observation")
    c_chk.close()

    # ---------- 4) new 路径 ----------
    new = run_path("1")

    # ---------- 5) 比对 ----------
    rep["old_wall_s"] = old["wall_s"]
    rep["new_wall_s"] = new["wall_s"]
    rep["result_old"] = old["result"]
    rep["result_new"] = new["result"]

    rep["new_obs_rows_old"] = len(old["new_obs"])
    rep["new_obs_rows_new"] = len(new["new_obs"])
    rep["new_alert_rows_old"] = len(old["new_alt"])
    rep["new_alert_rows_new"] = len(new["new_alt"])

    # 行集逐位相同（按可比较键排序）
    def _norm(rows):
        return [tuple((round(x, 12) if isinstance(x, float) else x) for x in r) for r in rows]
    rep["obs_set_identical"] = (_norm(sorted(old["new_obs"], key=str))
                                == _norm(sorted(new["new_obs"], key=str)))
    rep["alert_set_identical"] = (sorted(old["new_alt"], key=str)
                                  == sorted(new["new_alt"], key=str))

    # ---------- 6) 差分布（写入维·主口径：逐行 (code,factor_time) 全集） ----------
    keys = set(old["last"].keys()) | set(new["last"].keys())
    deltas: list[float] = []
    nonzero_keys: list[str] = []
    lastbit = 0
    for k in keys:
        ov = old["last"].get(k)
        nv = new["last"].get(k)
        if ov is None or nv is None:
            deltas.append(1.0)          # 存在性差按 1.0 记（保守，单列统计）
            nonzero_keys.append(f"{k}:missing")
            continue
        d = abs(ov[0] - nv[0])
        deltas.append(d)
        if d != 0.0:
            nonzero_keys.append(f"{k}:{ov[0]!r}!={nv[0]!r}")
            # 浮点末位差判定：ulp 级（相对 1e-15）
            if d <= max(abs(ov[0]), abs(nv[0]), 1.0) * 1e-15:
                lastbit += 1
    rep["write_dim"] = {
        "compared_keys": len(keys),
        "nonzero_count": len(nonzero_keys),
        "nonzero_ratio": round(len(nonzero_keys) / max(1, len(keys)), 9),
        **{"p50": _percentiles(deltas)["p50"], "p90": _percentiles(deltas)["p90"],
           "p99": _percentiles(deltas)["p99"], "max": _percentiles(deltas)["max"]},
        "last_bit_only_count": lastbit,
        "nonzero_samples": nonzero_keys[:5],
    }

    # ---------- 7) 辅口径：trade_date 计数分布 ----------
    def _date_of(ft_ms: int) -> str:
        return datetime.fromtimestamp(ft_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
    dated: dict[str, int] = {}
    for k in nonzero_keys:
        try:
            ft = int(str(k).split(",")[2].split(")")[0].strip())
        except (ValueError, IndexError):
            continue
        dated[_date_of(ft)] = dated.get(_date_of(ft), 0) + 1
    rep["write_dim"]["trade_date_nonzero_counts"] = dict(sorted(dated.items()))

    rep["equivalent"] = bool(rep["obs_set_identical"] and rep["alert_set_identical"]
                             and len(nonzero_keys) == 0)

    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
        print(f"\nwritten: {outp}")

    # 清理备份（保留副本本体）
    try:
        if pristine.exists():
            pristine.unlink()
            rep["pristine_cleaned"] = True
    except OSError as e:
        rep["pristine_cleaned"] = f"ERR:{e}"

    return 0 if rep["equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
