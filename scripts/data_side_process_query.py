"""数据侧任务统一查询接口（防线包 #8 层三，2026-08-28）。

治"事故 3"（终极解决会话宽关键词进程清理误杀）：守护/清理工具禁止自写
进程扫描，一律经本接口获取带锚类型的结果并按精确 pid 定点处置。

只读零副作用；复用 governance_snapshot._data_side_tasks_running()（v1.1 U10
一致性断言锚：防双实现漂移）。

用法：
    python scripts/data_side_process_query.py            # JSON 输出
    python scripts/data_side_process_query.py --summary  # 人读摘要
"""
import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import governance_snapshot as gs  # noqa: E402


def query() -> list:
    """返回当前数据侧任务命中（与 guard 同源枚举，v1.1 U10 锚）。"""
    return gs._data_side_tasks_running()


def main() -> int:
    ap = argparse.ArgumentParser(description="数据侧任务统一查询（只读）")
    ap.add_argument("--summary", action="store_true", help="人读摘要输出")
    args = ap.parse_args()
    hits = query()
    out = {
        "queried_at": datetime.now().isoformat(),
        "count": len(hits),
        "hits": hits,
        "note": ("只读查询；处置须按精确 pid 定点，禁止按关键词批量清理。"
                 "锚类型：matched_pattern=可读锚/qdb_domain:*=marker 归因/fail_closed=未归因保守项"),
    }
    if args.summary:
        print(f"data-side hits: {len(hits)}")
        for h in hits:
            print(f"  pid={h['pid']} pattern={h['matched_pattern']} cmd={h['cmd'][:90]}")
        print("处置纪律：仅可按 pid 定点；fail_closed 未归因项处置前须人工确认。")
    else:
        io.open(sys.stdout.fileno(), "w", encoding="utf-8").write(
            json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
