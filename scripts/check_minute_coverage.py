# -*- coding: utf-8 -*-
"""回测前分钟数据覆盖闸——独立 CLI 入口（复用 libs/coverage_gate 唯一实现，禁双份逻辑）。

用途：
  1. 回测前后独立核查任一 DuckDB（主库/副本）的分钟覆盖（三型判据见
     quantstudio/backtest/libs/coverage_gate.py 模块 docstring，QD-2026-0015）；
  2. 副本陈旧型（③）在 daemon 独占主库时会被回测前置检查跳过——可在停服窗用本 CLI 补跑；
  3. 回填/修复数据后复验「窗口内零整日缺」（方案 §五-3 验收项）。

用法：
  python scripts/check_minute_coverage.py --start 2026-01-05 --end 2026-08-13 \
      [--db <path>] [--main-db <path>] [--profile minute-bar-v1] [--json out.json]
  缺省 --db = quantstudio._paths.db_path()（主库）；--profile 缺省 minute-bar-v1
  （本 CLI 的存在目的就是分钟覆盖核查）。

退出码：0 = PASS/WARN/SKIPPED（WARN 不阻断）；2 = BLOCK（整日缺且 STRICT，fail-closed）；
        3 = ERROR（闸自身出错）。开关环境变量与回测前置检查同源：
        QS_MINUTE_COVERAGE_SKIP / QS_MINUTE_COVERAGE_STRICT / QS_MINUTE_COVERAGE_MIN_RATIO。

数据访问纪律：read_only 打开，禁写禁 CHECKPOINT，不停 daemon。
性能：方案拟预算 60s/窗口（拟值）；实测值以 --json 报告的 timings/elapsed_sec 字段为准，
      不得把拟值当实测（②审条件①）。2026-10-30 验收实测读数见
      docs/evidence/ 与 tests/test_coverage_gate.py 模块注释的回填记录。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.backtest.libs.coverage_gate import (  # noqa: E402
    MINUTE_PROFILE, check_minute_coverage, format_summary,
)

EXIT_OK = 0
EXIT_BLOCK = 2
EXIT_ERROR = 3


def _default_db() -> Path:
    from quantstudio._paths import db_path
    return db_path()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="分钟数据覆盖闸（三型判据 QD-2026-0015；只读检查）")
    ap.add_argument("--db", default=None,
                    help="所用库路径（缺省 = 主库 quantstudio._paths.db_path()）")
    ap.add_argument("--start", required=True, help="窗口起始日 YYYY-MM-DD（闭区间）")
    ap.add_argument("--end", required=True, help="窗口结束日 YYYY-MM-DD（闭区间）")
    ap.add_argument("--profile", default=MINUTE_PROFILE,
                    help=f"引擎 profile（缺省 {MINUTE_PROFILE}；非分钟 profile 闸不适用）")
    ap.add_argument("--main-db", default=None,
                    help="主库路径（type ③ 副本陈旧对拍；缺省自动解析，主库不可读则跳过 ③）")
    ap.add_argument("--json", default=None, help="报告 JSON 落盘路径（UTF-8 无 BOM）")
    args = ap.parse_args(argv)

    db = Path(args.db) if args.db else _default_db()
    main_db = Path(args.main_db) if args.main_db else None

    try:
        report = check_minute_coverage(db, args.start, args.end, args.profile,
                                       main_db_path=main_db)
    except Exception as e:  # noqa: BLE001  闸约定不抛判定类异常；此处兜底意外错误
        print(f"[minute-gate] ❌ ERROR: {type(e).__name__}: {e}")
        return EXIT_ERROR

    for line in format_summary(report):
        print(line)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"[minute-gate] 报告已落盘: {out}")

    verdict = report.get("verdict")
    if verdict == "BLOCK":
        return EXIT_BLOCK
    if verdict == "ERROR":
        print(f"[minute-gate] ❌ ERROR: {report.get('error')}")
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
