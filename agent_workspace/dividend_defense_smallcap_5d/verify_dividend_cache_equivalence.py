# -*- coding: utf-8 -*-
"""股息聚合等价性硬测（设计 A-7 性能契约①）—— R5 前执行，结果入验收证据。

断言：策略内『游标增量 + 事件缓存』得到的 Σ bonus_ps 与
     『全窗口逐日扫描』逐值严格相等（同一 asof、同一标的集合、同一窗口）。

用法：python agent_workspace/dividend_defense_smallcap_5d/verify_dividend_cache_equivalence.py
"""
import os, sys, datetime
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "quantstudio.db")

from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
from quantstudio.backtest.ptrade_api import _api
from quantstudio.backtest.strategy_runner import load_strategy

STRATEGY = os.path.join(ROOT, "agent_workspace", "dividend_defense_smallcap_5d", "strategy.py")
CODES = ["600519", "601398", "600036", "000060", "000001", "002415", "601288", "600028"]
DATE, PREV = "2026-07-30", "2026-07-29"
WINDOW_DAYS = 365


def _api_day(d):
    return str(d)[:10].replace("-", "")


def _full_scan(bare, start_api, end_api):
    """参考实现：全窗口逐日扫描（无缓存、无游标）。"""
    days = [_api_day(x) for x in _api.get_trade_days(start_date=start_api, end_date=end_api)]
    events = []
    for day in days:
        frame = _api.get_stock_exrights(bare + (".SS" if bare[0] in "56" else ".SZ"), day)
        if frame is None or len(frame) == 0:
            continue
        for _, row in frame.iterrows():
            value = row.get("bonus_ps")
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value == value and value > 0:
                events.append((day, value))
    return sorted(events)


def main():
    cfg = EngineConfig(db_path=DB, output_dir=os.path.join(ROOT, "output"),
                       research_dir=os.path.join(ROOT, "output", "research"))
    eng = BacktestEngine(db_path=DB, strategy={}, start="2026-01-01", end=DATE,
                         config=cfg, strategy_type="ptrade")
    _api.attach(eng, None, None, DATE, PREV, {})

    _funcs, mod = load_strategy(STRATEGY)
    mod._ensure_runtime_state()

    # 逐级推进 as-of（迫使游标增量：首次全扫、其后只补新增交易日）
    asof_list = [_api_day(x) for x in _api.get_trade_days(end_date=DATE, count=6)][-5:]
    fails = []
    checked = 0
    print("asof 序列:", asof_list)
    for asof in asof_list:
        start_dt = datetime.datetime.strptime(asof, "%Y%m%d") - datetime.timedelta(days=WINDOW_DAYS)
        start_api = start_dt.strftime("%Y%m%d")
        for bare in CODES:
            got = mod._dividend_sum_12m(bare, asof)
            ref_events = _full_scan(bare, start_api, asof)
            ref = 0.0
            for day, value in ref_events:
                if start_api < day <= asof:
                    ref += value
            checked += 1
            if abs(got - ref) > 1e-9:
                fails.append((asof, bare, got, ref))
            # 事件列表一致性（窗口内）
            cached = [(d, v) for d, v in mod.g.div_events.get(bare, []) if start_api < d <= asof]
            if sorted(cached) != sorted(ref_events):
                fails.append((asof, bare, "events_mismatch", len(cached), len(ref_events)))

    print("比较组数:", checked, "| 标的数:", len(CODES), "| asof 数:", len(asof_list))
    print("事件缓存条目总数:", sum(len(v) for v in mod.g.div_events.values()))
    print("比较组失败数:", len(fails))
    for item in fails[:10]:
        print("   FAIL:", item)
    print("RESULT:", "PASS - 游标增量与全窗逐日扫描逐值相等" if not fails else "FAIL")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
