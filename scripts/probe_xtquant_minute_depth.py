#!/usr/bin/env python
"""PR 分钟源切换：xtquant 分钟历史深度探测脚本（前置验证）。

在正式批量采集前，先用 1-2 只样本股验证 xtquant 的 1min 历史深度。
探测结果决定 collector_tasks.json 的 start_date（若 xtquant 缓存历史不足，
start_date 调整为实际可用起点，避免长期空拉）。

用法：
    python scripts/probe_xtquant_minute_depth.py

需要 miniQMT 客户端运行。失败（如 QMT 未启动）记录原因，不阻塞代码合并。

输出：
    - 样本股的分钟历史深度报告（最早/最晚 bar、行数、复权列完整性）
    - start_date 建议
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 样本股（覆盖股票 + ETF）
SAMPLE_CODES = ["600000.SH", "159870.SZ"]
PROBE_START = "2018-01-01"
PROBE_END = "2026-07-21"


def probe_one_code(adapter, code: str) -> dict:
    """探测单只 code 的分钟历史深度"""
    import pandas as pd
    try:
        df, meta = adapter.fetch_table(
            "stock_minutes" if not code.startswith(("159", "51")) else "etf_minutes",
            PROBE_START, PROBE_END, freq="1min", codes=[code])
    except Exception as e:
        return {"code": code, "error": str(e), "rows": 0}

    if len(df) == 0:
        return {"code": code, "error": "no data", "rows": 0}

    # 时间范围
    if "time" in df.columns:
        times = pd.to_datetime(df["time"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")
        earliest = times.min()
        latest = times.max()
    else:
        earliest = latest = "unknown"

    # 复权列完整性
    front_cols = [c for c in df.columns if c.endswith("_front")]
    back_cols = [c for c in df.columns if c.endswith("_back")]
    front_complete = all(df[c].notna().any() for c in front_cols) if front_cols else False
    back_complete = all(df[c].notna().any() for c in back_cols) if back_cols else False

    return {
        "code": code,
        "rows": len(df),
        "earliest_bar": str(earliest),
        "latest_bar": str(latest),
        "front_cols": front_cols,
        "front_complete": front_complete,
        "back_cols": back_cols,
        "back_complete": back_complete,
    }


def suggest_start_date(results: list) -> str:
    """根据探测结果建议 start_date"""
    valid = [r for r in results if r.get("rows", 0) > 0 and r.get("earliest_bar") != "unknown"]
    if not valid:
        return "无法建议（无有效数据，xtquant 可能未启动或历史缓存为空）"
    # 取最早 bar 的年份
    import re
    years = []
    for r in valid:
        m = re.search(r"(\d{4})-\d{2}-\d{2}", r["earliest_bar"])
        if m:
            years.append(int(m.group(1)))
    if not years:
        return "无法解析年份"
    earliest_year = min(years)
    return f"{earliest_year}-01-01（基于样本最早 bar 年份）"


def main():
    print("=" * 60)
    print("xtquant 分钟历史深度探测")
    print(f"样本股: {SAMPLE_CODES}")
    print(f"探测区间: {PROBE_START} ~ {PROBE_END}")
    print("=" * 60)

    try:
        from quantstudio.pipeline.sources.xtquant_adapter import XtquantAdapter
        adapter = XtquantAdapter({"name": "xtquant"})
    except Exception as e:
        print(f"\n❌ 无法初始化 xtquant adapter（miniQMT 可能未启动）: {e}")
        print("   此脚本需要 miniQMT 客户端运行。探测失败不阻塞代码合并。")
        return 1

    results = []
    for code in SAMPLE_CODES:
        print(f"\n--- 探测 {code} ---")
        r = probe_one_code(adapter, code)
        results.append(r)
        for k, v in r.items():
            print(f"  {k}: {v}")

    print("\n" + "=" * 60)
    print("start_date 建议:")
    print(f"  {suggest_start_date(results)}")
    print("=" * 60)

    # 复权完整性总结
    for r in results:
        if r.get("rows", 0) > 0:
            if not r.get("front_complete"):
                print(f"⚠️  {r['code']} front 复权列不完整！fq='pre' 查询会退回原始价")
            if not r.get("back_complete"):
                print(f"⚠️  {r['code']} back 复权列不完整（schema required=false，影响小）")

    adapter.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
