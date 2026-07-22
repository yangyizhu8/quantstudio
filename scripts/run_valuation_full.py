#!/usr/bin/env python
"""单独全量拉取 stock_daily_valuation（不触发 stock_daily 连带）。

用途：etf_daily 切源前，先把 valuation 补到最新（全量），避免 etf_daily 拉取时
被 valuation 全量前置阻塞 56 分钟。

用法：
    python scripts/run_valuation_full.py

注意：tushare daily_basic 限流 30/min，2094 个交易日约需 56 分钟。
"""
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 配置 logging 让 daemon 的进度日志显示（否则 INFO 全被吞，看起来像卡住）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s INFO %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)


def main():
    from quantstudio.pipeline.daemon import ResidentCollector

    collector = ResidentCollector.from_configs(
        str(ROOT / "config" / "data_config.json"),
        str(ROOT / "config" / "sources_config.json"),
        str(ROOT / "config" / "collector_tasks.json"),
        str(ROOT / "config" / "alignment_rules.json"),
    )

    tasks = json.loads((ROOT / "config" / "collector_tasks.json").read_text(encoding="utf-8"))
    val_task = next((t for t in tasks["tasks"] if t.get("table") == "stock_daily_valuation"), None)
    if not val_task:
        print("ERROR: valuation_daily task not found")
        return 1

    print(f"=== 全量拉取 stock_daily_valuation ===")
    print(f"  source: {val_task['source']}")
    print(f"  start_date: {val_task.get('start_date', '2018-01-01')}")
    print(f"  预计耗时: ~56 分钟（tushare 限流 30/min × 2094 交易日）")
    print()

    ok = collector.execute_task(val_task, mode="full_range", run_quality_audit=False)
    print()
    print(f"=== 结果: {'✅ 成功' if ok else '❌ 失败'} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
