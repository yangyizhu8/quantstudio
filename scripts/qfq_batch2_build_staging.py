#!/usr/bin/env python
"""QFQ 批次2 canary —— staging 环境构建（仅含 canary ETF 证券）。

构建规则（铁律：staging 副本 / 不写正式库）：
- 主库 DuckDB：ATTACH 正式行情库只读，拷贝行情表（仅 canary ETF + 近期窗口 cutoff 起，
  提速真实取数）；元数据/目录表全量拷贝；QFQ 内部表由 init_schema 建空（本环境从未初始化）。
- 辅助库 SQLite：在线备份（sqlite3.backup，一致性快照）后原样保留（ETF 无 fund_adj → 无观察 → ratio=1.0）。
- 写入 STAGING_MARKER.txt + staging_manifest.json 便于人工辨识。

用法：
    python scripts/qfq_batch2_build_staging.py [--cutoff 20240101]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantstudio._paths import db_path  # noqa: E402
from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema  # noqa: E402

BJ_TZ = timezone(timedelta(hours=8))

# canary：5 只 TICK_TOLERANCE（用户重点核验 eps 生效）+ 4 只常规 ETF
# 另含 1 只精度探针 159915（已知有 1 行 |Δ|>0.001 真实差异，预期被门控拦截）
# 全 ETF → 无股票分红 → effective_dates=[] → ratio=1.0 → front 不变（守恒清晰）
CANARY_TICK = ["159205", "159215", "159218", "588200", "159740"]
CANARY_NORMAL = ["510300", "159919", "510500", "512100"]
CANARY_PROBE = ["159915"]
CANARY_ALL = CANARY_TICK + CANARY_NORMAL + CANARY_PROBE

# 行情表（按 code + 近期窗口过滤，提速真实取数；time 列为 epoch-ms）
PRICE_TABLES = ["etf_daily", "etf_minutes"]
META_TABLES_FULL = [  # 全量拷贝的目录/元数据表（resolve_ts_codes 需要；存在的才拷贝）
    "etf_basic", "stock_basic", "source_watermark",
]
# 仅建空表（编排器会查询，ETF 无数据但不允许缺表）
SCHEMA_ONLY_TABLES = ["stock_dividend"]


def staging_dir() -> Path:
    d = ROOT / "data" / "staging_batch2_20260730"
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_main(formal: Path, staging: Path, cutoff_ms: int) -> dict:
    import duckdb

    if staging.exists():
        staging.unlink()
    conn = duckdb.connect(str(staging))
    conn.execute(f"ATTACH '{str(formal)}' AS f (READ_ONLY)")
    # 行情表（canary + cutoff_ms 起；time 列为 epoch-ms）
    rep_price = {}
    for t in PRICE_TABLES:
        try:
            conn.execute(f'CREATE TABLE "{t}" AS SELECT * FROM f."{t}" '
                         f"WHERE code IN ({','.join(['?']*len(CANARY_ALL))}) "
                         f"AND time >= ?", CANARY_ALL + [cutoff_ms])
            n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            rep_price[t] = n
        except Exception as e:  # noqa: BLE001
            rep_price[t] = f"ERR {e!r}"
    # 元数据全量拷贝（存在的才拷贝）
    rep_meta = []
    for t in META_TABLES_FULL:
        try:
            conn.execute(f'CREATE TABLE "{t}" AS SELECT * FROM f."{t}"')
            rep_meta.append(t)
        except Exception as e:  # noqa: BLE401
            rep_meta.append(f"{t}:ERR {e!r}")
    # 仅建空表（编排器查询需要，ETF 无数据）
    rep_schema = []
    for t in SCHEMA_ONLY_TABLES:
        try:
            conn.execute(f'CREATE TABLE "{t}" AS SELECT * FROM f."{t}" WHERE 1=0')
            rep_schema.append(t)
        except Exception as e:  # noqa: BLE401
            rep_schema.append(f"{t}:ERR {e!r}")
    # QFQ 内部表（建空）
    init_duckdb_schema(conn)
    conn.close()
    return {"price": rep_price, "meta": rep_meta, "schema_only": rep_schema}


def populate_calendar(staging: Path, cutoff_ms: int) -> int:
    """用 miniQMT 的 xtdata.get_trading_dates 填充 trade_calendar（完整自然日 open/closed）。

    关键：cal_date 必须为上海午夜（与编排器 _day_midnight_ms 同口径）；
    xtdata.get_trading_dates 返回的正是上海午夜，故直接复用。
    """
    import time as _time
    from quantstudio.pipeline.qfq_calendar import (
        CalendarService, _day_midnight_ms, _iter_natural_days)
    from quantstudio.pipeline.qfq_fresh_capture import XtquantFreshFetcher
    import duckdb

    f = XtquantFreshFetcher()
    xt = f._ensure()
    f._ensure_connected(xt)
    trading = set(int(d) for d in xt.get_trading_dates(market="SH"))
    now_ms = int(_time.time() * 1000)
    lo = _day_midnight_ms(cutoff_ms)        # 上海午夜
    hi = _day_midnight_ms(now_ms)           # 上海午夜
    natural = list(_iter_natural_days(lo, hi))
    open_ms = [d for d in natural if d in trading]
    closed_ms = [d for d in natural if d not in trading]
    cal = CalendarService(main_db=str(staging))
    conn = duckdb.connect(str(staging))
    ts = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    cal.persist_trade_days_on_conn(
        conn, open_ms, closed_ms, source="xtdata", updated_at=ts)
    conn.close()
    return len(natural)


def build_aux(formal_aux: Path, staging_aux: Path) -> None:
    if staging_aux.exists():
        staging_aux.unlink()
    src = sqlite3.connect(str(formal_aux))
    dst = sqlite3.connect(str(staging_aux))
    src.backup(dst)
    src.close()
    dst.close()


def write_marker(d: Path, formal: Path, aux: Path, cutoff: str) -> None:
    (d / "STAGING_MARKER.txt").write_text(
        "QFQ 批次2 CANARY STAGING 副本\n"
        "用途：仅在 staging 副本上验证 TICK_TOLERANCE ETF 在引擎 raw 对齐 eps=1e-3 下能否 committed。\n"
        "严禁指向正式库。本副本与正式库物理隔离。\n"
        f"正式主库: {formal}\n正式 aux: {aux}\n"
        f"canary ETF({len(CANARY_ALL)}): {CANARY_ALL}\n"
        f"近期窗口 cutoff: {cutoff}\n"
        f"构建时间: {datetime.now(BJ_TZ).isoformat(timespec='seconds')}\n",
        encoding="utf-8")
    manifest = {
        "purpose": "qfq_batch2_canary",
        "canary_tick_tolerance": CANARY_TICK,
        "canary_normal_etf": CANARY_NORMAL,
        "canary_all": CANARY_ALL,
        "cutoff": cutoff,
        "formal_main": str(formal),
        "formal_aux": str(aux),
        "staging_main": str(d / "quantstudio.db"),
        "staging_aux": str(d / "qfq_aux.db"),
        "built_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
    }
    (d / "staging_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="20240101")
    args = ap.parse_args()
    # cutoff → epoch-ms（北京时区 00:00）
    cutoff_dt = datetime(int(args.cutoff[:4]), int(args.cutoff[4:6]), int(args.cutoff[6:8]),
                         tzinfo=BJ_TZ)
    cutoff_ms = int(cutoff_dt.timestamp() * 1000)

    formal = db_path("quantstudio.db")
    formal_aux = db_path("qfq_aux.db")
    d = staging_dir()
    staging = d / "quantstudio.db"
    staging_aux = d / "qfq_aux.db"
    print(f"[build] 正式主库: {formal} (exists={formal.exists()})")
    print(f"[build] 正式 aux : {formal_aux} (exists={formal_aux.exists()})")
    print(f"[build] staging  : {staging}  cutoff={args.cutoff} ({cutoff_ms})")

    rep = build_main(formal, staging, cutoff_ms)
    build_aux(formal_aux, staging_aux)
    n_cal = populate_calendar(staging, cutoff_ms)
    write_marker(d, formal, formal_aux, args.cutoff)

    print("\n=== 行情表(canary+cutoff) ===")
    for k, v in rep["price"].items():
        print(f"  {k}: {v}")
    print(f"\n=== 元数据全量拷贝({len(rep['meta'])}) ===")
    print("  " + ", ".join(rep["meta"]))
    print(f"\n=== 仅建空表({len(rep['schema_only'])}) ===")
    print("  " + ", ".join(rep["schema_only"]))
    print(f"\n=== trade_calendar 填充: {n_cal} 自然日（来自 xtdata.get_trading_dates）===")
    print(f"\n[build] QFQ 内部表已 init_schema（空）。staging 目录: {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
