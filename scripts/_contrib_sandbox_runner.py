#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""沙箱回测执行器（策略生态准入 · D3 内核，2026-09-19）。

由 `scripts/run_contrib_sandbox.ps1` 调用，在隔离环境（只读影子库 + 临时目录 +
代理黑洞）中对**外部贡献策略**跑一次短窗回测，输出结构化结论 JSON。

边界：本执行器**不判断安全性**——它只回答「这段代码能不能跑完、跑多久、有无异常」。
安全性由 D2 静态门 + 所有者人工审查承担（见方案 R4）。

输出（JSON）
    ok            : 是否跑完无异常
    elapsed_s     : 耗时
    nav_len       : 净值序列长度（交易日数）
    nav_last      : 末值
    trades_len    : 交易笔数
    error_type    : 异常类型（ok=false 时）
    error_message : 异常摘要（截断至 800 字符）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import traceback

ZERO = 0.0


def _fail(code: str, msg: str) -> int:
    print(json.dumps({"ok": False, "error_type": code,
                      "error_message": msg[:800]}, ensure_ascii=False))
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="贡献策略沙箱回测执行器")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--capital", type=float, default=100000.0)
    ap.add_argument("--out", required=True, help="结论 JSON 落点")
    args = ap.parse_args()

    root = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))

    st_path = pathlib.Path(args.strategy).resolve()
    db_path = pathlib.Path(args.db).resolve()
    out_path = pathlib.Path(args.out).resolve()

    if not st_path.exists():
        return _fail("StrategyNotFound", "策略文件不存在：%s" % st_path)
    if not db_path.exists():
        return _fail("ShadowDbNotFound", "影子库不存在：%s" % db_path)

    try:
        from quantstudio.backtest.backtest_engine import BacktestEngine
        from quantstudio.backtest.strategy_runner import load_strategy
    except Exception as exc:  # noqa: BLE001
        return _fail("ImportFailed", "导入回测框架失败：%r" % (exc,))

    t0 = time.perf_counter()
    try:
        functions, _mod = load_strategy(st_path)
        engine = BacktestEngine(
            db_path=str(db_path), strategy=functions,
            start=args.start, end=args.end, capital=args.capital,
            match_price_mode="next_open", engine_profile="daily-bar-v1",
        )
        result, _out = engine.run()
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - t0
        tb = traceback.format_exc()
        payload = {
            "ok": False,
            "elapsed_s": round(elapsed, 4),
            "error_type": type(exc).__name__,
            "error_message": ("%s: %s" % (type(exc).__name__, exc))[:800],
            "traceback_tail": tb[-1200:],
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return 1

    elapsed = time.perf_counter() - t0
    nav = getattr(result, "nav_history", []) or []
    trades = getattr(result, "trade_records", []) or []
    payload = {
        "ok": True,
        "elapsed_s": round(elapsed, 4),
        "nav_len": len(nav),
        "nav_last": (str(nav[-1].get("date")), float(nav[-1].get("nav", ZERO)))
                    if nav else None,
        "trades_len": len(trades),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
