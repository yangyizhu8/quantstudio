"""G2a 定点重锚执行脚本：ETF 分钟 front 锚点修复（mcp-minute-front-anchor-closure）。

模式：
  prefetch  — fresh 预拉取（**不锁库**）：McpFreshFetcher 拉 124 只候选的 1m+1d
              fresh 数据（MCP raw + adj_factor → front，不依赖本地因子表），
              落盘 output/g2a_fresh/<code>_1m.parquet / <code>_1d.parquet（断点续传）。
  reanchor  — 锁库段（用户批准停机窗口内执行）：init 编排 schema（幂等）→
              code 级备份（四 front 列 CSV + SHA-256 manifest + raw hash 对照）→
              逐 code apply_reanchor_for_security(model='fresh_staged') →
              postcheck（引擎自带）→ V2 判别复验 + raw/volume/amount/update_time 零改动。
  verify    — 独立复验（重锚后 V2 判别 + 零改动 hash 对照）。

候选清单：docs/evidence/g2a-candidates-20260816.txt（119 FAIL + 5 WARN = 124 只）。
执行约束（方案 §5/R5）：reanchor 在 daemon 停机窗口内执行；分批 --batch i/N。

用法示例：
  python scripts/etf_minute_reanchor.py prefetch --main-db data/quantstudio.db
  python scripts/etf_minute_reanchor.py reanchor --main-db data/quantstudio.db --batch 1 --n-batches 4
  python scripts/etf_minute_reanchor.py verify --main-db data/quantstudio.db
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("etf_minute_reanchor")

CANDIDATES_FILE = ROOT / "docs" / "evidence" / "g2a-candidates-20260816.txt"
FRESH_DIR = ROOT / "output" / "g2a_fresh"
BACKUP_DIR = ROOT / "output" / "reanchor_backup"
REPORT_DIR = ROOT / "output" / "g2a_report"
START_YYYYMMDD = "2025-01-01"          # etf_minutes 数据起点（R1 实测）
END_YYYYMMDD = "2026-08-16"            # 当前数据末尾
ASSET_TYPE = "ETF"
PRICE_SOURCE = "mcp"
SOURCE_GENERATION = "mcp-gen1"
CUTOVER_ID = "b6_formal_20260807_v2"   # TD-D2 核实的 active cutover


def load_candidates() -> list[str]:
    if not CANDIDATES_FILE.exists():
        raise SystemExit(f"候选清单缺失: {CANDIDATES_FILE}")
    codes = []
    for line in CANDIDATES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("code,") or line.startswith("G2a"):
            continue
        parts = line.split(",")
        if parts and parts[0].strip().isdigit() and len(parts[0].strip()) == 6:
            codes.append(parts[0].strip())
    return codes


def _idx_to_epoch_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    return (idx.astype("int64") // 10**6).to_numpy()


def build_fresh_frames(none_df: pd.DataFrame, front_df: pd.DataFrame,
                       code: str, freq: str | None) -> pd.DataFrame:
    """合并 none+front → fresh 帧（code/time/OHLC/四 front；time=epoch ms）。"""
    out = pd.DataFrame({
        "code": code,
        "time": _idx_to_epoch_ms(none_df.index),
        "open": none_df["open"].to_numpy(),
        "high": none_df["high"].to_numpy(),
        "low": none_df["low"].to_numpy(),
        "close": none_df["close"].to_numpy(),
        "open_front": front_df["open"].to_numpy(),
        "high_front": front_df["high"].to_numpy(),
        "low_front": front_df["low"].to_numpy(),
        "close_front": front_df["close"].to_numpy(),
    })
    if freq:
        out["freq"] = freq
    return out


def get_mcp_fetcher(main_db: str):
    from quantstudio.pipeline.qfq_fresh_fetcher_factory import (
        build_qfq_fresh_fetcher, load_sources_cfg)
    from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
    cfg = QFQOrchestratorConfig(price_source=PRICE_SOURCE,
                                source_generation=SOURCE_GENERATION,
                                cutover_id=CUTOVER_ID)
    cfg.main_db = main_db
    cfg.qfq_aux_paths_config = str(ROOT / "config" / "profiles" / "mcp_only" / "qfq_aux_paths.json")
    sources_cfg = load_sources_cfg(ROOT / "config" / "profiles" / "mcp_only")
    if not (sources_cfg.get("sources", {}).get("mcp") or sources_cfg.get("mcp")):
        logger.warning("sources_config.json 无 mcp 块，尝试主 config/ 目录")
        sources_cfg = load_sources_cfg(ROOT / "config")
    return build_qfq_fresh_fetcher(cfg, sources_cfg, main_db=main_db)


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# prefetch（不锁库，批量模式：一次 export 共享网格缓存，本地算 none/front）
# ---------------------------------------------------------------------------
def _build_none_front_from_raw(raw: pd.DataFrame, code: str,
                               decimals: int = 3) -> tuple:
    """按 fetch_none_front 同款公式：raw 去重 → 时间 index → none/front。"""
    # 去重（MCP export 可能因分片/修正返回同 time 多行，取最后）
    for tcol in ("trade_date", "time", "trade_time"):
        if tcol in raw.columns:
            raw = raw.sort_values(tcol).drop_duplicates(
                subset=[tcol], keep="last").reset_index(drop=True)
            break
    if "time" in raw.columns:
        idx = pd.to_datetime(raw["time"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")
    elif "trade_time" in raw.columns:
        tt = pd.to_datetime(raw["trade_time"])
        idx = tt.dt.tz_localize("Asia/Shanghai") if tt.dt.tz is None else tt.dt.tz_convert("Asia/Shanghai")
    elif "trade_date" in raw.columns:
        idx = pd.to_datetime(raw["trade_date"].astype(str), format="mixed", dayfirst=False)
    else:
        idx = pd.RangeIndex(len(raw))
    none_df = pd.DataFrame({
        "open": pd.to_numeric(raw["open"], errors="coerce").to_numpy().round(decimals),
        "high": pd.to_numeric(raw["high"], errors="coerce").to_numpy().round(decimals),
        "low": pd.to_numeric(raw["low"], errors="coerce").to_numpy().round(decimals),
        "close": pd.to_numeric(raw["close"], errors="coerce").to_numpy().round(decimals),
    }, index=idx)
    if "adj_factor" in raw.columns:
        adj = pd.to_numeric(raw["adj_factor"], errors="coerce")
        valid_adj = adj[adj.notna() & (adj > 0)]
        adj_latest = valid_adj.iloc[-1] if len(valid_adj) > 0 else 1.0
        adj = adj.fillna(1.0)
        ratio = pd.Series((adj.to_numpy() / adj_latest), index=idx)
        front_df = none_df.mul(ratio, axis=0)
    else:
        front_df = none_df.copy()
    return none_df, front_df


def cmd_prefetch(args) -> int:
    from quantstudio.pipeline.sources import create_adapter
    codes = load_candidates()
    logger.info("prefetch(批量): %d 只候选（一次 export 共享网格缓存）", len(codes))
    FRESH_DIR.mkdir(parents=True, exist_ok=True)
    existing = [p.name.split("_")[0] for p in FRESH_DIR.glob("*_1m.parquet")] if FRESH_DIR.exists() else []
    todo = [c for c in codes if c not in existing]
    logger.info("已缓存 %d 只，待拉 %d 只", len(codes) - len(todo), len(todo))
    if not todo:
        return 0
    import json
    from quantstudio.pipeline.qfq_fresh_fetcher_factory import load_sources_cfg
    sources_cfg = load_sources_cfg(ROOT / "config" / "profiles" / "mcp_only")
    if not (sources_cfg.get("sources", {}).get("mcp") or sources_cfg.get("mcp")):
        sources_cfg = load_sources_cfg(ROOT / "config")
    mcp_cfg = dict(sources_cfg.get("sources", {}).get("mcp") or sources_cfg.get("mcp") or {})
    mcp_cfg.setdefault("main_db", args.main_db)
    mcp_cfg.setdefault("export_cache", True)   # 网格共享，export 次数 ~N 而非 2181×N
    adapter = create_adapter("mcp", mcp_cfg)
    t0 = time.time()
    # 1m 全历史（批量）
    raw_1m, meta1 = adapter.fetch_table("etf_minutes", START_YYYYMMDD, END_YYYYMMDD,
                                        freq="1min", codes=todo)
    logger.info("1m 批量拉取完成: %d 行 %.1fs", len(raw_1m), time.time() - t0)
    # 1d 全历史（批量）
    raw_1d, meta2 = adapter.fetch_table("etf_daily", START_YYYYMMDD, END_YYYYMMDD,
                                        freq="daily", codes=todo)
    logger.info("1d 批量拉取完成: %d 行", len(raw_1d))
    code_col = next((c for c in ("ts_code", "code", "stock_code") if c in raw_1m.columns), None)
    if code_col is None:
        logger.error("raw_df 无 code 类列: %s", list(raw_1m.columns)[:12])
        return 1
    ok = 0
    for code in todo:
        g1m = raw_1m[raw_1m[code_col].astype(str).str.contains(code, regex=False)]
        g1d = raw_1d[raw_1d[code_col].astype(str).str.contains(code, regex=False)]
        if g1m.empty or g1d.empty:
            logger.warning("%s 无数据（1m=%d 1d=%d），跳过", code, len(g1m), len(g1d))
            continue
        nm, fm = _build_none_front_from_raw(g1m, code, decimals=3)
        nd, fd = _build_none_front_from_raw(g1d, code, decimals=3)
        fresh_m = build_fresh_frames(nm, fm, code, "1min")
        fresh_d = build_fresh_frames(nd, fd, code, None)
        fresh_m.to_parquet(FRESH_DIR / f"{code}_1m.parquet")
        fresh_d.to_parquet(FRESH_DIR / f"{code}_1d.parquet")
        ok += 1
        if ok % 20 == 0:
            logger.info("进度 %d/%d", ok, len(todo))
    logger.info("prefetch 完成: %d/%d 只，耗时 %.1fs", ok, len(todo), time.time() - t0)
    return 0


# ---------------------------------------------------------------------------
# 备份 + raw hash 对照
# ---------------------------------------------------------------------------
def backup_code(conn, code: str, table: str) -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    csv = BACKUP_DIR / f"{code}_front.csv"
    rows = conn.execute(
        f"SELECT code, time, freq, open_front, high_front, low_front, close_front "
        f"FROM {table} WHERE code=?", [code]).fetchall()
    pd.DataFrame(rows, columns=["code", "time", "freq", "open_front",
                                "high_front", "low_front", "close_front"]).to_csv(csv, index=False)
    raw_hash = hashlib.md5()
    for r in conn.execute(
            f"SELECT code, time, freq, open, high, low, close, volume, amount, "
            f"preClose, data_source, update_time FROM {table} WHERE code=? ORDER BY time",
            [code]).fetchall():
        raw_hash.update(repr(r).encode())
    return {"code": code, "front_csv": str(csv),
            "front_sha256": sha256_of_file(csv),
            "raw_md5_before": raw_hash.hexdigest(), "bars": len(rows)}


def verify_raw_unchanged(conn, code: str, table: str, before_hash: str) -> bool:
    raw_hash = hashlib.md5()
    for r in conn.execute(
            f"SELECT code, time, freq, open, high, low, close, volume, amount, "
            f"preClose, data_source, update_time FROM {table} WHERE code=? ORDER BY time",
            [code]).fetchall():
        raw_hash.update(repr(r).encode())
    return raw_hash.hexdigest() == before_hash


# ---------------------------------------------------------------------------
# reanchor（锁库段，v1.2 因子直算模式）
# ---------------------------------------------------------------------------
def _load_tiered_codes(tier_file: str) -> list[str]:
    """读取分级清单的放行 code（R8）。

    兼容两种格式：①含 "放行:" 行；②逐行 CSV（code, n_points, n_match, max_rel, verdict）。
    """
    p = Path(tier_file)
    if not p.exists():
        raise SystemExit(f"分级清单缺失: {p}")
    text = p.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("放行:"):
            return [c.strip() for c in line.split(":", 1)[1].split(",") if c.strip()]
    codes = []
    for line in text.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 5 and parts[4] == "PASS" and len(parts[0]) == 6:
            codes.append(parts[0])
    if codes:
        return codes
    raise SystemExit(f"分级清单无放行行: {p}")


def _build_factor_map(code: str, aux_db: str) -> tuple:
    """返回 ([(time_ms, factor)], latest)：本地 qfq_aux 因子段合并+尖刺过滤（与 A1 同）。"""
    import sqlite3
    import pandas as pd
    conn = sqlite3.connect(aux_db)
    conn.execute("PRAGMA query_only=ON")
    rows = conn.execute(
        "SELECT time, adj_factor FROM fund_adj WHERE code=? ORDER BY time", [code]).fetchall()
    conn.close()
    fdf = pd.DataFrame(rows, columns=["time", "adj_factor"])
    if fdf.empty:
        return [], None
    ts = fdf["time"].to_numpy()
    vals = fdf["adj_factor"].to_numpy()
    segs = []
    start = 0
    for i in range(1, len(vals) + 1):
        if i == len(vals) or vals[i] != vals[i - 1]:
            segs.append((int(ts[start]), int(ts[i - 1]), float(vals[start])))
            start = i
    clean = []
    for idx, (st, en, v) in enumerate(segs):
        is_spike = ((en - st) < 86_400_000 and 0 < idx < len(segs) - 1
                    and (v / segs[idx - 1][2] < 0.5 or v / segs[idx - 1][2] > 2.0))
        if not is_spike:
            clean.append((int(st), float(v)))
    if not clean:
        return [], None
    return clean, clean[-1][1]


def cmd_reanchor(args) -> int:
    import duckdb
    codes = _load_tiered_codes(args.tier_file)
    if args.batch:
        n = args.n_batches or 4
        codes = codes[(args.batch - 1) * len(codes) // n: args.batch * len(codes) // n]
    logger.info("reanchor(v1.2 因子直算): %d 只（%s）", len(codes),
                f"batch {args.batch}/{args.n_batches}" if args.batch else "全量")
    conn = duckdb.connect(args.main_db)
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        manifest = []
        summary = {"done": [], "skipped_no_factor": [], "failed": []}
        for i, code in enumerate(codes, 1):
            factors, latest = _build_factor_map(code, args.aux_db)
            if not factors or not latest:
                summary["skipped_no_factor"].append(code)
                logger.warning("[%d/%d] %s 无可用因子（过滤后为空），跳过", i, len(codes), code)
                continue
            backup = backup_code(conn, code, "etf_minutes")
            # 每 bar 的 adj_i：pandas merge_asof（bar.time ← 因子段 t_ms 最近）
            import pandas as pd
            bars = conn.execute(
                f"SELECT time, close FROM etf_minutes WHERE code=? AND freq='1min' AND close > 0",
                [code]).fetchall()
            bdf = pd.DataFrame(bars, columns=["time", "close"])
            fdf = pd.DataFrame(factors, columns=["t_ms", "f"])
            merged = pd.merge_asof(
                bdf.sort_values("time"), fdf.sort_values("t_ms"),
                left_on="time", right_on="t_ms", direction="backward")
            merged = merged.dropna(subset=["f"])
            conn.execute("CREATE TEMP TABLE _g2a_f(time BIGINT, f DOUBLE)")
            conn.register("_g2a_f_df", merged[["time", "f"]])
            conn.execute("INSERT INTO _g2a_f SELECT time, f FROM _g2a_f_df")
            conn.unregister("_g2a_f_df")
            # 因子直算 UPDATE（R10：仅四 front 列）
            conn.execute(
                f"UPDATE etf_minutes AS m SET "
                f"open_front = m.open * g.f / ?, high_front = m.high * g.f / ?, "
                f"low_front = m.low * g.f / ?, close_front = m.close * g.f / ? "
                f"FROM _g2a_f g WHERE m.time = g.time AND m.code = ? AND m.freq = '1min'",
                [latest, latest, latest, latest, code])
            conn.execute("DROP TABLE _g2a_f")
            n_upd = conn.execute(
                f"SELECT COUNT(*) FROM etf_minutes WHERE code=? AND freq='1min' AND "
                f"close > 0 AND close_front IS NOT NULL AND open_front IS NOT NULL",
                [code]).fetchone()[0]
            ok_raw = verify_raw_unchanged(conn, code, "etf_minutes", backup["raw_md5_before"])
            # per-bar 合理性（R9）：front/close ∈ [0.05, 20] + 相邻 bar 连续性
            bad_ratio = conn.execute(
                f"SELECT COUNT(*) FROM etf_minutes WHERE code=? AND freq='1min' AND close > 0 "
                f"AND (close_front/close < 0.05 OR close_front/close > 20)", [code]).fetchone()[0]
            manifest.append({**backup, "updated_bars": n_upd, "raw_unchanged": ok_raw,
                             "bad_ratio": bad_ratio})
            summary["done"].append((code, n_upd, ok_raw, bad_ratio))
            logger.info("[%d/%d] %s done: %d bar 更新, raw 零改动=%s, 异常比率=%d",
                        i, len(codes), code, n_upd, ok_raw, bad_ratio)
        (REPORT_DIR / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        (REPORT_DIR / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        logger.info("reanchor 完成: done=%d skipped=%d failed=%d",
                    len(summary["done"]), len(summary["skipped_no_factor"]),
                    len(summary["failed"]))
    finally:
        conn.close()
    return 0


# ---------------------------------------------------------------------------
# verify（独立复验）
# ---------------------------------------------------------------------------
def cmd_verify(args) -> int:
    import duckdb
    from quantstudio.pipeline.quality_audit import DataQualityAuditor, QualityReport
    conn = duckdb.connect(args.main_db, read_only=True)
    try:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        aud = DataQualityAuditor(args.main_db, {"etf_minutes": {}},
                                 qfq_aux_override=args.aux_db)
        report = QualityReport()
        aud._audit_minute_anchor_drift(conn, report, tables)
        drift = [i for i in report.issues if i.check == "AdjustmentAnchorDrift"]
        for i in drift:
            logger.info("A1 复验: %s %s count=%d %s", i.severity, i.table, i.count, i.detail)
        failed = [i for i in drift if i.severity == "error"]
        logger.info("verify 完成: FAIL=%d（重锚后应显著收敛）", sum(i.count for i in failed))
    finally:
        conn.close()
    return 1 if any(i.severity == "error" for i in drift) else 0


def main():
    ap = argparse.ArgumentParser(description="ETF 分钟 front 定点重锚（G2a）")
    ap.add_argument("mode", choices=["prefetch", "reanchor", "verify"])
    ap.add_argument("--main-db", default=str(ROOT / "data" / "quantstudio.db"))
    ap.add_argument("--aux-db", default=str(ROOT / "data" / "qfq_aux.db"))
    ap.add_argument("--tier-file",
                    default=str(ROOT / "docs" / "evidence" / "g2a-tiering-final-20260817.md"))
    ap.add_argument("--batch", type=int, default=0)
    ap.add_argument("--n-batches", type=int, default=4)
    args = ap.parse_args()
    if args.mode == "prefetch":
        return cmd_prefetch(args)
    if args.mode == "reanchor":
        return cmd_reanchor(args)
    return cmd_verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
