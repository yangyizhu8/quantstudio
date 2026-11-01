# -*- coding: utf-8 -*-
"""QuantStudio stock_minutes 分钟表缺口回填执行件（一次性，不入库）

说明
----
`scripts/backfill_minutes_from_questdb.py` 在本 checkout 不可用，两处缺陷（均已实测复现，
且框架自有 canonical 修法）：

  A) `KeyError: 'adj_factor'` —— 脚本 bootstrap 分支未镜像 daemon.py:1241-1242 的
     `raw_df.drop(columns=["adj_factor"])` 守卫，导致 aligner._apply_qfq 的 merge
     产生 adj_factor_x/_y 列名冲突（daemon.py:1234-1241 自述为「P3-2 第8个 bug」）。
  B) 时间整体偏移 +8h —— QuestDB /exp 把北京墙钟序列化成带 'Z' 的 UTC 串
     （'2026-10-23T09:30:00.000000Z'），aligner 按字面 UTC 解析；主库存的是真 UTC ms
     （09:30 北京 = 01:30Z = 1792719000000）。直接写会把每根 bar 放到盘中 +8h 位置。

本执行件不改 scripts/ 与 quantstudio/ 任何源码，只复用既有框架通道：
  * QuestDB 取数：复用脚本自身的 qdb_export / qdb_count
  * 复权因子：框架 quantstudio.pipeline.sources.mcp_adapter.normalize_mcp_adj_factor_df
  * 对齐：框架 aligner.align（与 daemon.py:1291-1298 同签名同参数）
  * 校验：框架 validator.validate
  * 写入：框架 DuckDBWriter.write（ON CONFLICT + IS DISTINCT FROM 幂等语义）
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))

# 复用脚本自身的 QuestDB 客户端与 stack 构造（不复制其逻辑）
_spec = importlib.util.spec_from_file_location(
    "bfs", ROOT / "scripts" / "backfill_minutes_from_questdb.py")
bfs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bfs)

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
from quantstudio.pipeline.sources.mcp_adapter import (  # noqa: E402
    normalize_mcp_adj_factor_df)

TABLE = "stock_minutes"
FREQ = "1min"
DB = ROOT / "data" / "quantstudio.db"
WAL = ROOT / "data" / "quantstudio.db.wal"
WAL_LIMIT = 256 * 1024 * 1024
OUT_DIR = ROOT / "docs" / "evidence"


def wal_size() -> int:
    try:
        return WAL.stat().st_size
    except OSError:
        return 0


def duckdb_day_rows(table: str, day: str) -> int:
    c = duckdb.connect(str(DB), read_only=True)
    try:
        lo = int(pd.Timestamp(f"{day} 00:00:00").value // 10 ** 6)
        hi = int(pd.Timestamp(f"{day} 23:59:59").value // 10 ** 6)
        r = c.execute(
            f'SELECT count(*) FROM "{table}" WHERE "time" >= ? AND "time" <= ?',
            [lo, hi]).fetchone()
        return int(r[0])
    finally:
        c.close()


def build_aligned(col, raw: pd.DataFrame, table: str, freq: str, batch_id: str):
    """返回 (aligned_df, notes)。两处修正均取框架 canonical 口径。"""
    notes = {}
    # --- C1 时区标签修正：QuestDB 用 'Z' 标注北京墙钟 ---
    # align 侧：naive 'YYYY-MM-DD HH:MM:SS'（aligner 按 Asia/Shanghai -> UTC 解析）
    raw_align = raw.copy()
    raw_align["trade_time"] = (raw_align["trade_time"].astype(str)
                                .str.replace("T", " ", regex=False)
                                .str.replace("Z", "", regex=False)
                                .str.slice(0, 19))
    # adj 侧：'YYYYMMDDHHMMSS' 字符串（命中 _mcp_time_to_utc_ms 的 14 位分支）
    raw_adj = raw.copy()
    raw_adj["trade_time"] = (pd.to_datetime(raw_adj["trade_time"].astype(str)
                                            .str.replace("Z", "", regex=False))
                             .dt.strftime("%Y%m%d%H%M%S"))
    # --- 复权因子：框架 canonical 抽取 + daemon.py:1241-1242 的 drop 守卫 ---
    adj_df = normalize_mcp_adj_factor_df(raw_adj, freq, "STOCK")
    notes["adj_factor_rows"] = int(len(adj_df))
    raw_align2 = raw_align.drop(columns=["adj_factor"]).reset_index(drop=True)
    kwargs = col._qfq_snapshot_kwargs(table, batch_id)
    std, meta = col.aligner.align(raw_align2, table, "mcp",
                                   adj_factor_df=adj_df, freq=freq, **kwargs)
    return std, notes


def process_day(col, day: str, batch_id: str, apply_: bool) -> dict:
    rep = {"table": TABLE, "day": day, "applied": False}
    t0 = time.time()
    src_n = bfs.qdb_count(TABLE, day)
    rep["qdb_rows"] = src_n
    if src_n == 0:
        rep["status"] = "skip_source_empty"
        rep["elapsed_sec"] = round(time.time() - t0, 1)
        return rep
    csv_txt = bfs.qdb_export(TABLE, day)
    raw = pd.read_csv(io.StringIO(csv_txt))
    rep["csv_rows"] = len(raw)
    if len(raw) != src_n:
        rep["status"] = "fail_rowcount_mismatch_csv_vs_query"
        rep["elapsed_sec"] = round(time.time() - t0, 1)
        return rep

    std, notes = build_aligned(col, raw, TABLE, FREQ, batch_id)
    rep.update(notes)
    rep["aligned_rows"] = int(len(std))
    rep["aligned_cols"] = int(len(std.columns))
    rep["time_min"] = int(std["time"].min())
    rep["time_max"] = int(std["time"].max())
    rep["qfq_cols_present"] = [c for c in ("open_front", "close_front")
                               if c in std.columns and std[c].notna().any()]
    rep["qfq_nonnull"] = {c: int(std[c].notna().sum())
                           for c in ("open_front", "close_front", "open_back", "close_back")
                           if c in std.columns}
    if "data_source" not in std.columns:
        std["data_source"] = None
        rep["data_source_created_by_script"] = True
    std["data_source"] = "questdb"
    rep["data_source_after"] = ["questdb"]

    if not apply_:
        rep["status"] = "dry_run_ok"
        rep["elapsed_sec"] = round(time.time() - t0, 1)
        return rep

    # --- 框架校验通道 ---
    res = col.validator.validate(std, TABLE, batch_id, "mcp", expected_freq=FREQ)
    df = res.passed_df
    rep["validator_passed_rows"] = int(len(df))
    rep["validator_rejected_rows"] = int(len(res.rejected_rows))
    rep["aligned_columns_list"] = list(std.columns)

    pre_rows = duckdb_day_rows(TABLE, day)
    n = col.writer.write(df, TABLE, batch_id)
    post_rows = duckdb_day_rows(TABLE, day)
    rep["writer_return"] = int(n)
    rep["duckdb_rows_before"] = pre_rows
    rep["duckdb_rows_after"] = post_rows
    rep["delta"] = post_rows - pre_rows
    rep["new_rows_expected"] = int(len(df))
    rep["applied"] = True
    rep["status"] = "applied"
    rep["elapsed_sec"] = round(time.time() - t0, 1)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--batch-id", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--append", action="store_true",
                    help="把本次结果追加到既有 evidence JSON（不覆盖）")
    args = ap.parse_args()

    days = [d.strip() for d in args.days.split(",") if d.strip()]
    batch_id = args.batch_id or f"qdbdirect_{TABLE}_{dt.datetime.now():%Y%m%d_%H%M%S}"
    out = Path(args.out) if args.out else OUT_DIR / (
        f"minutes_direct_backfill_{TABLE}_{dt.datetime.now():%Y%m%d_%H%M%S}.json")

    print(f"[plan] table={TABLE} days={len(days)} mode="
          f"{'APPLY' if args.apply else 'DRY-RUN'} batch={batch_id}", flush=True)
    col = bfs.build_stack()
    reports = []
    if args.append and out.exists():
        try:
            reports = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            reports = []
    for d in days:
        w0 = wal_size()
        try:
            rep = process_day(col, d, batch_id, args.apply)
        except Exception as e:  # noqa: BLE001
            rep = {"table": TABLE, "day": d, "status": "fail_exception",
                   "error": f"{type(e).__name__}: {e}"}
        rep["wal_bytes_before_day"] = w0
        rep["wal_bytes_after_day"] = wal_size()
        reports.append(rep)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(reports, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"  {d}: {json.dumps(rep, ensure_ascii=False)}", flush=True)
        if rep.get("wal_bytes_after_day", 0) > WAL_LIMIT:
            print(f"[STOP] WAL {rep['wal_bytes_after_day']} > 256MB after {d}",
                  flush=True)
            return 3
    print(f"[evidence] {out}", flush=True)
    bad = [r for r in reports if str(r.get("status", "")).startswith("fail")]
    return 2 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
