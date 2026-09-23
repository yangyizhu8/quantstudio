# -*- coding: utf-8 -*-
"""FM 代答两工件导出（线调度 FM 需求规格 v1；2026-09-22）。

落裁依据：日历 §二三七 落裁② —— FM 输入改由 daemon 侧导出轻量工件，彻底解耦
DuckDB 单写者锁（固定空闲窗依赖节律不可靠，已实证不采）。L1 导出源不并入。

设计要点（与方案件 docs/handoff/fm-export-two-artifacts-plan-20260922.md 一致）：
  · 导出在 daemon 进程内、复用其既有 writer.shared_conn() ⇒ **零新增读连接、
    零读窗依赖、不触单写者锁**；
  · 工件①：stock_daily（全字段）+ stock_daily_valuation（全字段）· as_of 日全截面
    → parquet 按表分件，文件名含 as_of；
  · 工件②：四表 MAX(time) + **逐月行数矩阵**（案例十口径：strftime('%Y-%m') GROUP BY，
    明文禁 min/max 范围式表述）→ json 单件；
  · 失败必告警（禁静默）：失败不覆盖旧件 + _status.json + ERROR 日志；
  · 保留 N 份轮转（默认 10）；三件同批同 N 防错位。

as_of 口径（线调度 2026-09-22 四问作答）：
  (a) 倒数第二个交易日  (b) 按**交易日**计  (c) 末日次日未到 ⇒ 回退 D−1
  (d) **不要求全 code 无缺**：照库实况导出，勿补全勿裁剪 ⇒ resolve_as_of 不附一致性检查

四态健康标注（随件带上；定义见 _health_of，供消费方复核）：
  complete            as_of（本表自身 d1）== 参考表（stock_daily）的 d1
  fallback_prev_day   本表 d1 落后于参考表 d1（已回退，标注）
  empty               本表在库 0 行
  single_day          本表仅 1 个交易日
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

LOG = logging.getLogger("quantstudio.pipeline.fm_export")

FM_EXPORT_ENV = "QS_FM_EXPORT"              # 默认 1=启用；0/false/off=关闭（等效旧行为）
FM_EXPORT_DIR_ENV = "QS_FM_EXPORT_DIR"      # 落点覆盖（测试隔离用）
FM_EXPORT_KEEP_ENV = "QS_FM_EXPORT_KEEP"    # 保留份数（默认 10）
DEFAULT_KEEP = 10
REFERENCE_TABLE = "stock_daily"             # 交易日历参照（与 as_of 定义一致）
ARTIFACT1_TABLES = ("stock_daily", "stock_daily_valuation")
WATERMARK_TABLES = ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes")
HEALTH_COMPLETE = "complete"
HEALTH_FALLBACK = "fallback_prev_day"
HEALTH_EMPTY = "empty"
HEALTH_SINGLE = "single_day"


def fm_export_enabled() -> bool:
    """默认启用；=0/false/off 关闭（回退开关，等效旧行为）。"""
    v = os.environ.get(FM_EXPORT_ENV, "").strip().lower()
    return v not in ("0", "false", "off")


def fm_export_dir() -> Path:
    p = os.environ.get(FM_EXPORT_DIR_ENV, "").strip()
    if p:
        return Path(p)
    return Path(__file__).resolve().parents[2] / "data" / "fm_export"


def fm_export_keep() -> int:
    v = os.environ.get(FM_EXPORT_KEEP_ENV, "").strip()
    try:
        n = int(v) if v else DEFAULT_KEEP
    except ValueError:
        n = DEFAULT_KEEP
    return max(1, n)


def _ms_to_date(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")


def _distinct_days(conn, table: str, limit: int = 3) -> List[int]:
    """该表最近的 limit 个交易日（降序，BIGINT ms）。"""
    try:
        rows = conn.execute(
            'SELECT DISTINCT time FROM "%s" ORDER BY time DESC LIMIT %d' % (table, limit)
        ).fetchall()
    except Exception as e:
        LOG.warning("[fm_export] %s 交易日探测失败: %s", table, e)
        return []
    return [int(r[0]) for r in rows if r and r[0] is not None]


def _health_of(days: List[int], ref_d1: Optional[int]) -> Tuple[str, Dict]:
    """四态健康标注（定义见模块 docstring）。"""
    if not days:
        return HEALTH_EMPTY, {"reason": "该表在库 0 行"}
    if len(days) == 1:
        return HEALTH_SINGLE, {"reason": "该表仅 1 个交易日", "only_day": _ms_to_date(days[0])}
    d1 = days[1]
    if ref_d1 is not None and d1 < ref_d1:
        return HEALTH_FALLBACK, {
            "reason": "本表 d1 落后于参考表（stock_daily）d1 ⇒ 已回退，照库实况标注",
            "table_d1": _ms_to_date(d1), "ref_d1": _ms_to_date(ref_d1)}
    return HEALTH_COMPLETE, {"reason": "as_of = 本表倒数第二个交易日，与参考表一致"}


def resolve_as_of(conn, table: str = REFERENCE_TABLE) -> Dict:
    """解析 as_of：**倒数第二个交易日**（存在次日数据的最晚交易日）。

    返回 {as_of_ms, as_of_date, latest_ms, health, detail, table}。
    注：按线调度 (d) 作答，**不附带 code 完整性检查**（照库实况，勿补全勿裁剪）。
    """
    days = _distinct_days(conn, table)
    ref_days = days if table == REFERENCE_TABLE else _distinct_days(conn, REFERENCE_TABLE)
    ref_d1 = ref_days[1] if len(ref_days) >= 2 else None
    health, detail = _health_of(days, ref_d1)
    as_of_ms = days[1] if len(days) >= 2 else (days[0] if days else None)
    return {"table": table, "as_of_ms": as_of_ms, "as_of_date": _ms_to_date(as_of_ms),
            "latest_ms": days[0] if days else None, "health": health, "detail": detail}


def _count_day(conn, table: str, ms: int) -> int:
    try:
        return int(conn.execute('SELECT count() FROM "%s" WHERE time = ?' % table, [ms]).fetchone()[0])
    except Exception as e:
        LOG.warning("[fm_export] %s 计数失败: %s", table, e)
        return -1


def export_table(conn, table: str, as_of_ms: int, out_dir: Path) -> Dict:
    """工件①单表：as_of 日**全字段全截面** → parquet（.tmp → 原子 replace）。"""
    import pyarrow as pa
    import pyarrow.parquet as pq
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _count_day(conn, table, as_of_ms)
    df = conn.execute('SELECT * FROM "%s" WHERE time = ?' % table, [as_of_ms]).df()
    dest = out_dir / ("fm_%s_asof_%s.parquet" % (table, _ms_to_date(as_of_ms).replace("-", "")))
    tmp = dest.with_suffix(".parquet.tmp")
    tbl = pa.Table.from_pandas(df, preserve_index=False)
    meta = dict(tbl.schema.metadata or {})
    meta.update({
        b"as_of": str(_ms_to_date(as_of_ms)).encode(),
        b"as_of_ms": str(int(as_of_ms)).encode(),
        b"rows": str(len(df)).encode(),
        b"generated_at": datetime.now().astimezone().isoformat().encode(),
        b"generator": b"quantstudio.fm_export/v1",
    })
    tbl = tbl.replace_schema_metadata(meta)
    pq.write_table(tbl, str(tmp))
    os.replace(str(tmp), str(dest))
    LOG.info("[fm_export] 工件① %s → %s rows=%d cols=%d", table, dest.name, len(df), df.shape[1])
    return {"table": table, "file": dest.name, "rows": len(df), "cols": int(df.shape[1]),
            "count_db": rows, "as_of_ms": int(as_of_ms)}


def export_watermark(conn, as_of_ms: int, out_dir: Path) -> Dict:
    """工件②：四表 MAX(time) + **逐月行数矩阵**（案例十口径）→ json 单件。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    tables: Dict[str, Dict] = {}
    for t in WATERMARK_TABLES:
        try:
            mx = conn.execute('SELECT max(time) FROM "%s"' % t).fetchone()[0]
        except Exception as e:
            LOG.warning("[fm_export] 水位 %s 失败: %s", t, e)
            mx = None
        monthly: Dict[str, int] = {}
        try:
            # 注意：本 SQL 含字面 %Y-%%m，与 Python %-格式化冲突 ⇒ 用 %% 转义
            rows = conn.execute(
                "SELECT strftime(to_timestamp(time/1000), '%%Y-%%m') AS ym, count() AS n "
                'FROM "%s" GROUP BY ym ORDER BY ym' % t).fetchall()
            monthly = {str(r[0]): int(r[1]) for r in rows if r and r[0] is not None}
        except Exception as e:
            LOG.warning("[fm_export] 逐月矩阵 %s 失败: %s", t, e)
        days = _distinct_days(conn, t)
        ref_days = _distinct_days(conn, REFERENCE_TABLE)
        ref_d1 = ref_days[1] if len(ref_days) >= 2 else None
        health, detail = _health_of(days, ref_d1)
        tables[t] = {"max_time_ms": int(mx) if mx is not None else None,
                     "max_time_iso": _ms_to_date(mx), "health": health, "detail": detail,
                     "monthly": monthly}
    payload = {
        "as_of": _ms_to_date(as_of_ms), "as_of_ms": int(as_of_ms),
        "generated_at": datetime.now().astimezone().isoformat(),
        "generator": {"name": "quantstudio.fm_export", "version": "v1"},
        "note": "逐月行数矩阵（案例十口径）：禁用 min/max 范围式表述，防范围连续掩蔽中段空洞",
        "tables": tables,
    }
    dest = out_dir / ("fm_watermark_asof_%s.json" % _ms_to_date(as_of_ms).replace("-", ""))
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(dest))
    LOG.info("[fm_export] 工件② → %s 表数=%d", dest.name, len(tables))
    return {"file": dest.name, "tables": len(tables)}


def rotate(out_dir: Path, keep: int) -> List[str]:
    """按 as_of 保留最新 keep 份（三件同批同 N）。返回被删除文件名。"""
    removed: List[str] = []
    groups: Dict[str, List[Path]] = {}
    for p in sorted(out_dir.glob("fm_*_asof_*.*")):
        stem = p.name
        if "_asof_" not in stem:
            continue
        asof = stem.split("_asof_")[1].split(".")[0]
        groups.setdefault(asof, []).append(p)
    for asof in sorted(groups.keys(), reverse=True)[keep:]:
        for p in groups[asof]:
            try:
                p.unlink()
                removed.append(p.name)
            except OSError:
                pass
    if removed:
        LOG.info("[fm_export] 轮转删除 %d 件（keep=%d）: %s", len(removed), keep, removed[:6])
    return removed


def write_status(out_dir: Path, *, ok: bool, as_of: Optional[str], err: Optional[str] = None,
                 health: Optional[str] = None, artifacts: Optional[Dict] = None) -> Dict:
    """_status.json：**每次尝试都写**（失败亦写 ⇒ 「旧件被当新件读」可发现）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    sp = out_dir / "_status.json"
    prev: Dict = {}
    try:
        prev = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    fails = 0 if ok else int(prev.get("consecutive_failures", 0)) + 1
    payload = {
        "last_attempt_at": datetime.now().astimezone().isoformat(),
        "last_success_asof": (as_of if ok else prev.get("last_success_asof")),
        "last_success_at": (datetime.now().astimezone().isoformat() if ok else prev.get("last_success_at")),
        "last_error": (None if ok else (err or "unknown")[:400]),
        "consecutive_failures": fails,
        "health": health if ok else prev.get("health"),
        "artifacts": artifacts if ok else prev.get("artifacts"),
    }
    tmp = sp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(sp))
    if not ok and fails >= 3:
        LOG.error("[fm_export] **FM 导出停摆**：连续失败 %d 次（last_error=%s）", fails, payload["last_error"])
    return payload


def run_fm_export(conn, out_dir: Optional[Path] = None, keep: Optional[int] = None) -> Dict:
    """顶层入口：导出两工件；**异常内部消化**（记 ERROR + _status.json），不上抛。

    返回 {ok, as_of, health, artifacts, rotated, error}。
    """
    if not fm_export_enabled():
        LOG.info("[fm_export] 已由 %s 关闭，跳过", FM_EXPORT_ENV)
        return {"ok": True, "skipped": True}
    out_dir = Path(out_dir) if out_dir else fm_export_dir()
    keep = int(keep) if keep else fm_export_keep()
    t0 = time.perf_counter()
    try:
        ref = resolve_as_of(conn)
        as_of_ms = ref["as_of_ms"]
        if as_of_ms is None:
            raise RuntimeError("无法解析 as_of（参考表 %s 无数据）" % REFERENCE_TABLE)
        artifacts: Dict[str, Dict] = {}
        healths = []
        for t in ARTIFACT1_TABLES:
            r = export_table(conn, t, as_of_ms, out_dir)
            h, detail = _health_of(_distinct_days(conn, t), ref["as_of_ms"])
            r["health"] = h
            r["health_detail"] = detail
            artifacts[t] = r
            healths.append(h)
        w = export_watermark(conn, as_of_ms, out_dir)
        artifacts["watermark"] = w
        rotated = rotate(out_dir, keep)
        agg = (HEALTH_EMPTY if all(h == HEALTH_EMPTY for h in healths)
               else (HEALTH_SINGLE if all(h == HEALTH_SINGLE for h in healths)
                     else (HEALTH_FALLBACK if HEALTH_FALLBACK in healths else HEALTH_COMPLETE)))
        st = write_status(out_dir, ok=True, as_of=ref["as_of_date"], health=agg, artifacts=artifacts)
        out = {"ok": True, "as_of": ref["as_of_date"], "as_of_ms": as_of_ms,
               "health": agg, "health_detail": ref["detail"], "artifacts": artifacts,
               "rotated": rotated, "keep": keep, "dir": str(out_dir),
               "elapsed_s": round(time.perf_counter() - t0, 2),
               "status": st}
        LOG.info("[fm_export] 完成 as_of=%s health=%s 耗时=%.2fs 轮转=%d",
                 ref["as_of_date"], agg, out["elapsed_s"], len(rotated))
        return out
    except Exception as e:      # 禁静默：失败不覆盖旧件 + 状态件 + ERROR
        LOG.error("[fm_export] 导出失败（旧件保留，未被覆盖）: %s: %s", type(e).__name__, e)
        st = write_status(out_dir, ok=False, as_of=None, err="%s: %s" % (type(e).__name__, e))
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e), "status": st,
                "dir": str(out_dir)}
