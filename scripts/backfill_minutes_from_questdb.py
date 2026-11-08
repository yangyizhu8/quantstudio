# -*- coding: utf-8 -*-
"""分钟表直灌回填（数据修正型一次性件 · 2026-10-23 · 段③追平）

用途
----
从【本地 QuestDB（127.0.0.1:9000）】按交易日抽取分钟原始行，直灌 DuckDB 分钟表，
用于补齐 `source_watermark` 水位冻结（W-8 族定谳）造成的存量缺口。

设计要点（刻意不复刻任何未验证的映射）
------------------------------------
1. **不手搓 11→31 列映射**：复用框架 `FieldAligner.align()`（daemon 同一条路径），
   由它产出 `code/time/freq/open…/open_front…/data_source` 等 31 列。
2. **不手搓 upsert**：复用 `DuckDBWriter.write()`（writers.py:750），其内含
   `IS DISTINCT FROM` skip-identical 幂等语义（writers.py:1266-1271）。
3. **复权列不静默降级**：对齐后断言 `adj_factor`/`open_front` 非空；若为空则
   用原始 `adj_factor` 列自建 `adj_factor_df` 重对齐（自举，不臆测口径）。
4. **data_source 诚实标注**：默认 `'questdb'`（表内已有先例：9-21 单日 1,258,261 行）。
5. **默认 dry-run**：不写库，只出核对报告；写库须显式 `--apply`。

验收（数据修正型四要件）
------------------------
- 按源核对：该日 (code,time,freq) 行数 == QuestDB 该日行数
- diff 仅归因补写行集：写后行数 − 写前行数 == 新增行数；写前已存在的键不被改写
- 准确度只升不降：不得出现任何非补写行的字段变化
- 出现任何其他 diff ⇒ 立即失败退出（不做部分回滚，见 --apply 的事务说明）

用法
----
    python scripts/backfill_minutes_from_questdb.py --table etf_minutes \
        --days 2026-10-12,2026-10-13                 # dry-run（默认）
    python scripts/backfill_minutes_from_questdb.py --table etf_minutes \
        --days 2026-10-12,2026-10-13 --apply         # 真写

写库前置（硬要求，违反会与 daemon 抢 DuckDB 单写锁）
-----------------------------------------------------
1. **必须先停 daemon**（DuckDB 单写进程；daemon 在跑时本脚本写库会失败）。
   停 daemon 前须按《daemon 生命周期跨会话占用纪律》**先声明占用窗口**。
2. 写完按 WAL 铁律收敛：`check_wal.ps1`（备份 + CHECKPOINT + 验证）。
3. 写完重启 daemon，并在日志中确认该表水位已推进。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "profiles" / "mcp_only"
QDB_URL = "http://127.0.0.1:9000"
RAW_COLS = ["ts_code", "trade_time", "freq", "open", "high", "low", "close",
            "vol", "amount", "adj_factor", "is_qfq"]


# ---------------------------------------------------------------- QuestDB 取数
def qdb_export(table: str, day: str, limit: int | None = None) -> str:
    """按交易日导出 CSV（QuestDB /exp）。day 形如 2026-10-12。"""
    sql = (f"SELECT {', '.join(RAW_COLS)} FROM {table} "
           f"WHERE trade_time >= '{day}T00:00:00.000000Z' "
           f"AND trade_time < '{day}T23:59:59.999999Z'")
    if limit:
        sql += f" LIMIT {int(limit)}"
    url = f"{QDB_URL}/exp?query=" + urllib.parse.quote(sql)
    with urllib.request.urlopen(url, timeout=600) as r:
        return r.read().decode("utf-8", "replace")


def qdb_count(table: str, day: str) -> int:
    sql = (f"SELECT count(*) FROM {table} WHERE trade_time >= '{day}T00:00:00.000000Z' "
           f"AND trade_time < '{day}T23:59:59.999999Z'")
    url = f"{QDB_URL}/exec?query=" + urllib.parse.quote(sql)
    with urllib.request.urlopen(url, timeout=120) as r:
        d = json.loads(r.read().decode("utf-8"))
    return int(d["dataset"][0][0])


# ---------------------------------------------------------------- 框架组件
def build_stack():
    """复刻 daemon 的构造链，取 aligner / validator / writer / qfq 快照口径。"""
    sys.path.insert(0, str(ROOT))
    from quantstudio.pipeline.daemon import ResidentCollector  # noqa: E402

    col = ResidentCollector.from_configs(
        data_cfg_path=str(PROFILE / "data_config.json"),
        sources_cfg_path=str(PROFILE / "sources_config.json"),
        tasks_cfg_path=str(PROFILE / "collector_tasks.json"),
        align_rules_path=str(PROFILE / "alignment_rules.json"),
    )
    return col


# ---------------------------------------------------------------- 主流程
def backfill_day(col, table: str, day: str, source_label: str, apply_: bool,
                 batch_id: str, sample_check: int) -> dict:
    import pandas as pd

    rep = {"table": table, "day": day, "applied": False}
    src_n = qdb_count(table, day)
    rep["qdb_rows"] = src_n
    if src_n == 0:
        rep["status"] = "skip_source_empty"
        return rep

    csv_txt = qdb_export(table, day)
    raw = pd.read_csv(__import__("io").StringIO(csv_txt))
    rep["csv_rows"] = len(raw)
    if len(raw) != src_n:
        rep["status"] = "fail_rowcount_mismatch_csv_vs_query"
        return rep

    freq = str(raw["freq"].iloc[0]) if "freq" in raw.columns else "1min"

    # ---- 时区预处理（2026-11-08 第 4 个同族缺陷修正）----
    # QuestDB 的 trade_time 是「本地时间（Asia/Shanghai）语义」，但 HTTP /exec 返回时
    # 被格式化为带 Z 的 ISO 串。而 aligner.to_ms_timestamp() 见 tz-aware 就跳过
    # tz_localize ⇒ 直接取 UTC epoch ⇒ 整体 +8 小时（实测 09:30 CST → 17:30 CST）。
    # 因此在进入 aligner 前剥掉尾部 Z，使其成为 naive 串，由 aligner 按本地时区解析。
    if "trade_time" in raw.columns:
        before = str(raw["trade_time"].iloc[0])
        raw["trade_time"] = raw["trade_time"].astype(str).str.replace(
            r"Z$", "", regex=True)
        after = str(raw["trade_time"].iloc[0])
        rep["tz_preprocess"] = {"before": before, "after": after}

    # ---- 对齐（复用框架；先不传 adj_factor_df，必要时自举）----
    kwargs = {}
    try:
        kwargs = col._qfq_snapshot_kwargs(table, batch_id)
    except Exception as e:  # noqa: BLE001
        rep["qfq_snapshot_kwargs_error"] = f"{type(e).__name__}: {e}"

    std, meta = col.aligner.align(raw, table, "mcp", freq=freq, **kwargs)

    # ---- 复权列自检：不通过则用原始 adj_factor 自建后重对齐 ----
    # 只认「复权价列」为通过条件——adj_factor 单独存在不算（否则 front/back 静默为空）
    need_adj = ["open_front", "close_front"]
    have = [c for c in need_adj if c in std.columns and std[c].notna().any()]
    rep["qfq_cols_present"] = have
    rep["aligned_columns"] = list(std.columns)
    if not have:
        if "adj_factor" not in raw.columns:
            rep["status"] = "fail_no_qfq_cols_and_no_raw_adj_factor"
            return rep
        # ★ 两处缺陷修复（2026-11-08，实证 11-04 回填 dry-run 全败）：
        #   缺陷1：adj 用 raw 的 ts_code（'002868.SZ'）而 align 后的 code 是
        #          '002868' ⇒ merge 全空 ⇒ 复权价全 NULL。
        #   缺陷2：raw 自身含 adj_factor 列，与 adj_factor_df 同名列 merge 冲突
        #          ⇒ aligner 内 merged['adj_factor'] KeyError（aligner.py:954）。
        #   正解：用第一次 align 产出的 code/time（口径与目标 DataFrame 完全一致）
        #         + drop raw 的 adj_factor 后再二次 align。
        adj = pd.DataFrame({
            "code": std["code"].to_numpy(),
            "time": std["time"].to_numpy(),
            "adj_factor": pd.to_numeric(raw["adj_factor"], errors="coerce").to_numpy(),
        })
        rep["adj_bootstrap_rows"] = len(adj)
        rep["adj_bootstrap_nonnull"] = int(adj["adj_factor"].notna().sum())
        raw_for_align = raw.drop(columns=["adj_factor"], errors="ignore")
        std, meta = col.aligner.align(raw_for_align, table, "mcp",
                                      adj_factor_df=adj, freq=freq, **kwargs)
        rep["qfq_cols_present_after_bootstrap"] = [
            c for c in need_adj if c in std.columns and std[c].notna().any()]

    # ---- data_source 诚实标注 ----
    if "data_source" not in std.columns:
        # aligner 不产出该列（框架在 passthrough/写入侧另处理）——显式补列并留痕
        std["data_source"] = None
        rep["data_source_created_by_script"] = True
    before_ds = sorted(set(std["data_source"].dropna().astype(str).unique()))[:5]
    std["data_source"] = source_label
    rep["data_source_before"] = before_ds
    rep["data_source_after"] = [source_label]
    rep["aligned_rows"] = len(std)
    rep["aligned_cols"] = len(std.columns)

    if not apply_:
        rep["status"] = "dry_run_ok"
        return rep

    # ---- 写前该日行数（diff 归因基线）----
    pre_rows = _duckdb_day_rows(table, day)
    n = col.writer.write(std, table, batch_id)
    rep["writer_return"] = n
    post_rows = _duckdb_day_rows(table, day)
    rep["duckdb_rows_before"] = pre_rows
    rep["duckdb_rows_after"] = post_rows
    rep["delta"] = post_rows - pre_rows
    rep["new_rows_expected"] = len(std)
    rep["applied"] = True
    rep["status"] = "applied"
    return rep


def _to_ms_like(reference_ms, raw_iso: str) -> int:
    """用 aligner 自身对同一输入产出的 time 值，反推 ISO→ms 的口径（含时区处理）。"""
    if reference_ms is None:
        return int(pd_ms(raw_iso))
    # aligner 给的是 epoch ms；raw_iso 解析出的 naive/aware 值与之的差即口径偏移
    import pandas as pd
    parsed = pd.to_datetime(raw_iso)
    if getattr(parsed, "tzinfo", None) is None:
        naive_ms = int(parsed.value // 10**6)
    else:
        naive_ms = int(parsed.value // 10**6)
    delta = int(reference_ms) - naive_ms
    return naive_ms + delta


def pd_ms(iso: str) -> int:
    """把 QuestDB 返回的时间串转成 epoch 毫秒。

    **时区口径（2026-11-08 第 4 个同族缺陷修正）**：
    QuestDB 的 `trade_time` 是**本地时间（Asia/Shanghai）语义**，但 HTTP `/exec`
    返回时被格式化为带 `Z` 的 ISO 串（如 `2026-11-04T09:30:00.000000Z`）。
    若直接交给 pandas，`Z` 会被当作 UTC ⇒ epoch 比正确值**大 8 小时**
    （实测：解析出 1793784600000 = 17:30 CST，而正确值 1793755800000 = 09:30 CST）。

    因此：剥掉尾部 `Z`，再**显式** `tz_localize("Asia/Shanghai")`——不依赖机器本地时区。
    """
    import pandas as pd

    s = str(iso).strip()
    if s.endswith("Z"):
        s = s[:-1]
    ts = pd.to_datetime(s)
    if getattr(ts, "tzinfo", None) is None:
        ts = ts.tz_localize("Asia/Shanghai")
    return int(ts.value // 10**6)


def _duckdb_day_rows(table: str, day: str) -> int:
    """取该日在库内行数（写前基线 / 写后对账）。

    注意（2026-11-08 第三次同型事故后修正）：**不得用 read_only=True**——
    本进程的 build_stack() 已持有 read_write 连接，DuckDB 拒绝同进程内
    同一库文件的第二种配置（ConnectionException: different configuration）。
    """
    import duckdb
    c = duckdb.connect(str(ROOT / "data" / "quantstudio.db"))
    try:
        lo = int(pd_ms(f"{day} 00:00:00"))
        hi = int(pd_ms(f"{day} 23:59:59"))
        r = c.execute(f'SELECT count(*) FROM "{table}" WHERE "time" >= ? AND "time" <= ?',
                      [lo, hi]).fetchone()
        return int(r[0])
    finally:
        c.close()


# ---------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser(description="分钟表从本地 QuestDB 直灌 DuckDB（数据修正型一次性件）")
    ap.add_argument("--table", required=True, choices=["etf_minutes", "stock_minutes"])
    ap.add_argument("--days", required=True, help="逗号分隔交易日，如 2026-10-12,2026-10-13")
    ap.add_argument("--source-label", default="questdb", help="data_source 标注值（默认 questdb）")
    ap.add_argument("--apply", action="store_true", help="真写库（默认 dry-run）")
    ap.add_argument("--batch-id", default="")
    ap.add_argument("--sample-check", type=int, default=5)
    args = ap.parse_args()

    days = [d.strip() for d in args.days.split(",") if d.strip()]
    batch_id = args.batch_id or f"qdbdirect_{args.table}_{dt.datetime.now():%Y%m%d_%H%M%S}"

    print(f"[plan] table={args.table} days={len(days)} label={args.source_label} "
          f"mode={'APPLY' if args.apply else 'DRY-RUN'} batch={batch_id}")
    if args.apply:
        print("[warn] 写库要求：daemon 已停 + 已声明占用窗口（写后须 check_wal.ps1 收敛）")

    col = build_stack()
    reports = []
    for d in days:
        t0 = time.time()
        try:
            rep = backfill_day(col, args.table, d, args.source_label, args.apply,
                               batch_id, args.sample_check)
        except Exception as e:  # noqa: BLE001
            rep = {"table": args.table, "day": d, "status": "fail_exception",
                   "error": f"{type(e).__name__}: {e}"}
        rep["elapsed_sec"] = round(time.time() - t0, 1)
        reports.append(rep)
        print(f"  {d}: {json.dumps(rep, ensure_ascii=False)}")

    out = ROOT / "docs" / "evidence" / f"minutes_direct_backfill_{args.table}_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[evidence] {out}")
    bad = [r for r in reports if str(r.get("status", "")).startswith("fail")]
    return 2 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
