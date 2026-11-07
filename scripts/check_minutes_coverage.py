# -*- coding: utf-8 -*-
"""分钟数据逐日覆盖对账（1.4 + 1.5 的现场执行体）。

用法：
    python scripts/check_minutes_coverage.py                      # 默认近 20 个交易日
    python scripts/check_minutes_coverage.py --days 2026-11-01:2026-11-07
    python scripts/check_minutes_coverage.py --table stock_minutes --json

判定（复用 quantstudio.pipeline.minute_batch_guard）：
- 1.4 逐日覆盖：每个交易日的（行数, 证券数）与中位数比对
- 1.5 分片下限：按「该日分片数」是否达标的等价判据，用行数口径代理
  （分片数 = ceil(行数 / 50000)，50000 为服务端分片粒度实证值）

只读，不写库。退出码：0=全绿，1=有缺口，2=执行错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from quantstudio.pipeline.minute_batch_guard import (  # noqa: E402
    SHARDS_PER_TRADING_DAY,
    is_shard_complete,
    min_shards_required,
)

CST = timezone(timedelta(hours=8))
SHARD_ROWS = 50_000  # 服务端分片粒度（实证：part_00000 rows=50000）


def _load_calendar(con, start: str, end: str) -> List[str]:
    rows = con.execute(
        "SELECT cal_date FROM trade_calendar WHERE cal_date >= ? AND cal_date <= ? "
        "ORDER BY cal_date",
        [_to_ms(start), _to_ms(end)]).fetchall()
    return [datetime.fromtimestamp(int(r[0]) / 1000, CST).strftime("%Y-%m-%d") for r in rows]


def _to_ms(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=CST).timestamp() * 1000)


def check(table: str, start: str, end: str, lookback: int) -> Tuple[int, List[Dict]]:
    import duckdb

    db = REPO / "data" / "quantstudio.db"
    con = duckdb.connect(str(db), read_only=True)
    try:
        if not (start and end):
            # 默认：取日历最后 lookback 个交易日
            rows = con.execute(
                "SELECT cal_date FROM trade_calendar ORDER BY cal_date DESC LIMIT ?",
                [lookback]).fetchall()
            if not rows:
                raise RuntimeError("trade_calendar 为空")
            days = [datetime.fromtimestamp(int(r[0]) / 1000, CST).strftime("%Y-%m-%d")
                    for r in reversed(rows)]
            start, end = days[0], days[-1]

        cal = _load_calendar(con, start, end)
        if not cal:
            raise RuntimeError(f"窗口 [{start}, {end}] 内无交易日")

        # 按日统计
        rows = con.execute(
            f"SELECT CAST(to_timestamp(time/1000) AS DATE) d, COUNT(*) n, "
            f"COUNT(DISTINCT code) c FROM {table} "
            f"WHERE to_timestamp(time/1000) >= TIMESTAMP '{start} 00:00:00' "
            f"AND to_timestamp(time/1000) <= TIMESTAMP '{end} 23:59:59' "
            f"GROUP BY 1 ORDER BY 1").fetchall()
        stats = {str(r[0]): (int(r[1]), int(r[2])) for r in rows}
    finally:
        con.close()

    # 中位数（用于覆盖率参照）
    counts = sorted(s[1] for s in stats.values()) or [0]
    med_codes = counts[len(counts) // 2]

    report = []
    issues = 0
    for d in cal:
        n, c = stats.get(d, (0, 0))
        shards = (n + SHARD_ROWS - 1) // SHARD_ROWS if n else 0
        needed = min_shards_required(1)  # 单日下限
        complete = is_shard_complete(shards, 1)
        cov = (c / med_codes) if med_codes else 0.0

        level = "OK"
        if n == 0:
            level = "ERROR_ZERO_ROWS"           # 3.1 整日 0 行
            issues += 1
        elif not complete:
            level = "ERROR_SHARDS"              # 3.4/1.5 分片不足
            issues += 1
        elif med_codes and cov < 0.95:
            level = "ERROR_COVERAGE"            # 3.3 <95%
            issues += 1
        elif med_codes and cov < 0.99:
            level = "WARNING_COVERAGE"          # 3.2 <99%
            issues += 1

        report.append({
            "date": d, "rows": n, "codes": c, "shards": shards,
            "shards_needed": needed, "shards_ok": complete,
            "code_coverage": round(cov, 4), "level": level,
        })
    return issues, report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="stock_minutes",
                    choices=["stock_minutes", "etf_minutes"])
    ap.add_argument("--days", default="", help="YYYY-MM-DD:YYYY-MM-DD，缺省取近 20 交易日")
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    start = end = ""
    if a.days:
        if ":" not in a.days:
            print("--days 格式应为 YYYY-MM-DD:YYYY-MM-DD", file=sys.stderr)
            return 2
        start, end = a.days.split(":", 1)

    try:
        issues, report = check(a.table, start, end, a.lookback)
    except Exception as e:
        print(f"执行失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps({"table": a.table, "issues": issues, "days": report},
                         ensure_ascii=False, indent=2))
    else:
        print(f"=== {a.table} 逐日覆盖对账 ===")
        print(f"{'日期':<12}{'行数':>12}{'证券数':>8}{'分片':>6}{'下限':>5}"
              f"{'覆盖率':>8}  级别")
        for r in report:
            print(f"{r['date']:<12}{r['rows']:>12,}{r['codes']:>8}{r['shards']:>6}"
                  f"{r['shards_needed']:>5}{r['code_coverage']:>8.2%}  {r['level']}")
        print(f"\n问题日数: {issues}")
    return 0 if issues == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
