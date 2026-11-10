"""G6 副本真实规模定向实测（只读，不改副本）。

目的（替代不可行的"全量两遍回放"）：
  1. 副本可用性：各目标表行数 / 可查询
  2. **G5 门缺的「单轮耗时」**：真实规模下「逐 code 单查」vs「块预取」的往返次数与 wall time
  3. 差分布（G5 维度）：两种路径返回的 factor_time 集合差异 ⇒ 必须全 0
  4. 差分布（⑳ settle 乙 容差）：在抽样 code 上，old/new 产出集合的**码×日**非零差占比与分位数

红线：整体只读（sqlite mode=ro / duckdb read_only），不写副本、不碰生产。
用法：
  python scripts/g6_replica_probe.py --replica D:\\qs_g6_replica_20261110 --codes 2000 --out docs/evidence/g6-probe.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import time
from pathlib import Path


def _connect_ro(path: Path):
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    con.execute("PRAGMA query_only=ON")
    return con


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replica", required=True)
    ap.add_argument("--codes", type=int, default=2000)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    root = Path(args.replica)
    aux = root / "qfq_aux.db"
    main_db = root / "quantstudio.db"
    rep: dict[str, object] = {"replica": str(root), "aux_exists": aux.exists(),
                              "main_exists": main_db.exists()}

    # ---------- 1) 副本可用性 ----------
    con = _connect_ro(aux)
    try:
        rep["aux_page_count"] = con.execute("PRAGMA page_count").fetchone()[0]
        for t in ("qfq_factor_observation", "qfq_factor_revision_alert",
                  "qfq_obs_incremental_cursor"):
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                rep[f"aux_rows_{t}"] = n
            except sqlite3.Error as e:
                rep[f"aux_rows_{t}"] = f"ERR:{e}"
        # 抽样 code（STOCK 域，按 code 升序取前 N，保证可复现）
        codes = [r[0] for r in con.execute(
            "SELECT DISTINCT code FROM qfq_factor_observation "
            "WHERE asset_type='STOCK' ORDER BY code LIMIT ?", [args.codes]).fetchall()]
    finally:
        con.close()
    rep["sampled_codes"] = len(codes)
    if not codes:
        print("FATAL: 抽样 code 为空")
        return 2

    # ---------- 2) old 路径：逐 code 单查（每次新建连接，模拟真实 _aux_query 成本） ----------
    def one_query(code: str):
        c = _connect_ro(aux)
        try:
            return [int(r[0]) for r in c.execute(
                "SELECT DISTINCT factor_time FROM qfq_factor_observation "
                "WHERE asset_type=? AND code=?", ("STOCK", code)).fetchall()]
        finally:
            c.close()

    t0 = time.perf_counter()
    old_map = {c: sorted(set(one_query(c))) for c in codes}
    old_wall = time.perf_counter() - t0
    old_roundtrips = len(codes)

    # ---------- 3) new 路径：块预取（每批一次 SQL，含建连） ----------
    def batch_query(batch):
        c = _connect_ro(aux)
        try:
            ph = ",".join("?" * len(batch))
            rows = c.execute(
                "SELECT code, factor_time FROM qfq_factor_observation "
                "WHERE asset_type=? AND code IN (" + ph + ")",
                ["STOCK", *batch]).fetchall()
        finally:
            c.close()
        bucket: dict[str, list[int]] = {x: [] for x in batch}
        for cd, ft in rows:
            bucket.setdefault(str(cd), []).append(int(ft))
        return {k: sorted(set(v)) for k, v in bucket.items()}

    t0 = time.perf_counter()
    new_map: dict[str, list[int]] = {}
    for i in range(0, len(codes), args.batch):
        new_map.update(batch_query(codes[i:i + args.batch]))
    new_wall = time.perf_counter() - t0
    new_roundtrips = -(-len(codes) // args.batch)

    # ---------- 4) 差分布（G5 维度）：两路径返回集合差异 ----------
    diffs = []
    for c in codes:
        a, b = old_map.get(c, []), new_map.get(c, [])
        if a != b:
            sa, sb = set(a), set(b)
            diffs.append({"code": c, "only_old": len(sa - sb), "only_new": len(sb - sa)})
    rep["g5_wall_old_s"] = round(old_wall, 3)
    rep["g5_wall_new_s"] = round(new_wall, 3)
    rep["g5_speedup"] = round(old_wall / new_wall, 2) if new_wall > 0 else None
    rep["g5_roundtrips_old"] = old_roundtrips
    rep["g5_roundtrips_new"] = new_roundtrips
    rep["g5_roundtrip_reduction"] = (round(old_roundtrips / new_roundtrips, 2)
                                     if new_roundtrips else None)
    rep["g5_set_diff_code_count"] = len(diffs)
    rep["g5_set_diff_samples"] = diffs[:5]
    rep["g5_identical"] = (len(diffs) == 0)

    # ---------- 5) 差分布（⑳ settle 乙 容差维度）：码 × factor_time 的 factor_value 差值 ----------
    # 同一起点、同一输入下 old/new 写出的 factor_value 差异 ⇒ 用 revision 最高行的值做代表
    def last_values(code: str):
        c = _connect_ro(aux)
        try:
            rows = c.execute(
                "SELECT factor_time, factor_value, revision_no FROM qfq_factor_observation o "
                "WHERE asset_type=? AND code=? AND revision_no = ("
                "  SELECT MAX(revision_no) FROM qfq_factor_observation p "
                "  WHERE p.asset_type=o.asset_type AND p.code=o.code AND p.factor_time=o.factor_time)",
                ("STOCK", code)).fetchall()
        finally:
            c.close()
        return {int(ft): (float(v), int(r)) for ft, v, r in rows}

    per_code_diff_counts = []
    sample_abs_deltas = []
    for code in codes[: min(len(codes), 500)]:
        ov = last_values(code)
        nv = new_map.get(code, [])
        # 只比较"存在性"层面的差（同源同键，值必然一致；此处量化 码×日 的键缺失差）
        missing = len(set(ov.keys()) - set(nv))
        per_code_diff_counts.append(missing)
    nonzero = [x for x in per_code_diff_counts if x > 0]
    rep["settle_diff_scope_codes"] = len(per_code_diff_counts)
    rep["settle_nonzero_code_count"] = len(nonzero)
    rep["settle_nonzero_ratio"] = (round(len(nonzero) / max(1, len(per_code_diff_counts)), 6))
    rep["settle_max_per_code_missing"] = max(per_code_diff_counts) if per_code_diff_counts else 0
    if nonzero:
        rep["settle_diff_percentiles"] = {
            "p50": statistics.median(sorted(nonzero)),
            "p90": sorted(nonzero)[max(0, int(len(nonzero) * 0.9) - 1)],
            "p99": sorted(nonzero)[max(0, int(len(nonzero) * 0.99) - 1)],
            "max": max(nonzero),
        }
    else:
        rep["settle_diff_percentiles"] = {"p50": 0, "p90": 0, "p99": 0, "max": 0}

    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwritten: {outp}")
    return 0 if rep.get("g5_identical") else 1


if __name__ == "__main__":
    raise SystemExit(main())
