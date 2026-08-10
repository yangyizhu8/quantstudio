# -*- coding: utf-8 -*-
"""compare_roundtrip：G2 逐位断言——原策略 vs 转换产物同窗口回测，逐位相等。

铁规（T5 审核预警）：
1. 容差 0：np.array_equal / pd.testing.assert_frame_equal 逐位相等，"差异很小"一律不过；
2. 差异处理：只能显式降级 approximation + known_limitations，禁止调容差蒙混；
3. 排除清单：FQ WARN_KEEP/外部数据源策略不进断言，但必须显式标注"未做 1:1 断言及原因"。

用法：
    python -m quantstudio.strategy_compiler.validators.compare_roundtrip \
        <strategy.py> <converted.py> --start 2026-01-01 --end 2026-04-29
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_START = "2026-01-01"
_DEFAULT_END = "2026-04-29"  # B0 标准对照区间


def _run_engine(strategy_path: Path, start: str, end: str,
                profile: str = "daily-bar-v1") -> tuple[int, str, Optional[Path]]:
    """跑引擎，返回 (exit_code, summary, output_dir)。"""
    cmd = [sys.executable, "-m", "quantstudio.backtest.run_ptrade_strategy",
           str(strategy_path), start, end, "--profile", profile]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # T5 修复：固定哈希种子保证跨进程确定性——本地引擎的 dict/set 迭代顺序受
    # PYTHONHASHSEED 影响，随机种子下浮点加法顺序变化产生 ULP 级差异（max ~4e-11，
    # 实测 72/117 天）；PYTHONHASHSEED=0 下两次运行逐位一致（0 差异）。
    env["PYTHONHASHSEED"] = "0"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env,
                              timeout=1800)
    except subprocess.TimeoutExpired:
        return 124, f"timeout: {' '.join(cmd)}", None
    out_dir = None
    m = re.search(r"结果导出:\s*(.+)", proc.stdout or "")
    if m:
        out_dir = Path(m.group(1).strip())
    tail = (proc.stdout or "")[-300:]
    return proc.returncode, tail, out_dir


def _load_csvs(out_dir: Path) -> dict[str, Optional[pd.DataFrame]]:
    result: dict[str, Optional[pd.DataFrame]] = {}
    for name in ("daily_stats.csv", "trades.csv", "benchmark.csv"):
        p = out_dir / name
        if p.exists():
            try:
                result[name] = pd.read_csv(p, dtype=str).fillna("")
            except Exception:
                result[name] = None
        else:
            result[name] = None
    return result


def compare_roundtrip(
    strategy_path: str | Path,
    converted_path: str | Path,
    *,
    start: str = _DEFAULT_START,
    end: str = _DEFAULT_END,
    profile: str = "daily-bar-v1",
    excluded: bool = False,
    exclusion_reason: str = "",
) -> dict[str, Any]:
    """G2 逐位断言：原策略 vs 转换产物（容差 0）。

    Returns:
        {
          "status": "PASS" | "FAIL" | "EXCLUDED" | "ENGINE_ERROR",
          "nav_equal": bool | None, "trades_equal": bool | None,
          "nav_diffs": int, "trades_diffs": int,
          "summary": str, "exclusion_reason": str,
        }
    """
    result: dict[str, Any] = {
        "strategy": Path(strategy_path).name,
        "converted": Path(converted_path).name,
        "window": f"{start}~{end}",
        "profile": profile,
    }
    # 排除清单（铁规 3）：显式标注，不静默跳过
    if excluded:
        result.update(status="EXCLUDED", nav_equal=None, trades_equal=None,
                      nav_diffs=0, trades_diffs=0, exclusion_reason=exclusion_reason,
                      summary=f"未做 1:1 断言（排除）：{exclusion_reason}")
        return result

    ec1, tail1, out1 = _run_engine(Path(strategy_path), start, end, profile)
    if ec1 != 0 or out1 is None:
        result.update(status="ENGINE_ERROR", nav_equal=None, trades_equal=None,
                      nav_diffs=0, trades_diffs=0,
                      summary=f"原策略引擎未跑通 (exit={ec1}): {tail1[-200:]}")
        return result
    ec2, tail2, out2 = _run_engine(Path(converted_path), start, end, profile)
    if ec2 != 0 or out2 is None:
        result.update(status="ENGINE_ERROR", nav_equal=None, trades_equal=None,
                      nav_diffs=0, trades_diffs=0,
                      summary=f"转换产物引擎未跑通 (exit={ec2}): {tail2[-200:]}")
        return result

    c1, c2 = _load_csvs(out1), _load_csvs(out2)
    diffs: list[str] = []

    # 每日净值逐位断言（容差 0）
    nav_equal: Optional[bool] = None
    nav_diffs = 0
    if c1.get("daily_stats.csv") is not None and c2.get("daily_stats.csv") is not None:
        try:
            pd.testing.assert_frame_equal(c1["daily_stats.csv"], c2["daily_stats.csv"],
                                          check_dtype=True, check_exact=True)
            nav_equal = True
        except AssertionError as e:
            nav_equal = False
            nav_diffs = int(re.search(r"mismatch.*?(\d+)", str(e), re.S).group(1)) \
                if re.search(r"mismatch.*?(\d+)", str(e), re.S) else -1
            diffs.append(f"daily_stats 不一致: {str(e)[:300]}")
    else:
        diffs.append("daily_stats.csv 缺失（引擎未导出净值）")

    # 成交序列逐位断言（容差 0）
    trades_equal: Optional[bool] = None
    trades_diffs = 0
    if c1.get("trades.csv") is not None and c2.get("trades.csv") is not None:
        try:
            pd.testing.assert_frame_equal(c1["trades.csv"], c2["trades.csv"],
                                          check_dtype=True, check_exact=True)
            trades_equal = True
        except AssertionError as e:
            trades_equal = False
            trades_diffs = int(re.search(r"mismatch.*?(\d+)", str(e), re.S).group(1)) \
                if re.search(r"mismatch.*?(\d+)", str(e), re.S) else -1
            diffs.append(f"trades 不一致: {str(e)[:300]}")
    else:
        diffs.append("trades.csv 缺失（引擎未导出成交）")

    ok = nav_equal is True and trades_equal is True
    result.update(
        status="PASS" if ok else "FAIL",
        nav_equal=nav_equal, trades_equal=trades_equal,
        nav_diffs=nav_diffs, trades_diffs=trades_diffs,
        summary="逐位相等（容差 0）" if ok else ("; ".join(diffs) if diffs else "未知差异"),
    )
    return result


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="G2 round-trip 逐位断言")
    parser.add_argument("strategy", help="原策略 .py")
    parser.add_argument("converted", help="转换产物 .py")
    parser.add_argument("--start", default=_DEFAULT_START)
    parser.add_argument("--end", default=_DEFAULT_END)
    parser.add_argument("--profile", default="daily-bar-v1")
    args = parser.parse_args(argv)
    r = compare_roundtrip(args.strategy, args.converted,
                          start=args.start, end=args.end, profile=args.profile)
    def _safe(s: Any) -> str:
        return str(s).encode("utf-8", "replace").decode("utf-8", "replace")
    print(f"status={r['status']}")
    print(f"  nav_equal={r['nav_equal']} trades_equal={r['trades_equal']}")
    print(f"  summary={_safe(r['summary'])}")
    return 0 if r["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
