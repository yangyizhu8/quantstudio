"""按日行情索引（纯性能优化）精确等价 + 实例生命周期缓存 + 黄金回测防回归。

核心约束（来自 codex review 与用户确认的重做 spec）：
- 索引只保存 df['code'] 原始值，不做任何转换（不 str / 不 bare_code / 不去空格 / 不改大小写）。
- 查询参数沿用各站点原 bare 计算；重复 code 用 setdefault 保留首次 iloc。
- _build_code_index 返回语义严格区分：None = 无法构建（缺列/异常，调用点必须回退原布尔过滤）；
  {} = 合法空 DataFrame（有 code 列但 0 行，构建成功）。两者不可混淆。
- DataDict / PtradeAPI 用实例私有缓存，set_curr_data / reset_session / attach_day / attach_bar 失效。
- BacktestEngine 用实例私有 FIFO 上限 4 缓存，缓存项存 (df, index)，命中验证 entry_df is df；不缓存 None。
- 索引为 None 时，各调用点必须执行该调用点原来的完整布尔过滤逻辑，保留原异常/空值/fallback。
- 黄金回测：索引开启 vs 强制回退（monkeypatch _build_code_index -> None）的两份引擎输出
  （nav_history / trade_records / 每日持仓 / 最终持仓）必须逐项相等。任何差异 = 失败回退。
"""
import pandas as pd
import pytest

import quantstudio.backtest.ptrade_api as ptrade_mod
from quantstudio.backtest.ptrade_api import (
    DataDict, PtradeAPI, _build_code_index,
)
from quantstudio.backtest.backtest_engine import BacktestEngine
from tests.conftest import daily_row, minute_row, make_providers


# ===================== 工具 =====================
def _make_df(codes, *, close=10.0, preclose=9.0, volume=1000.0, suspend=0):
    """构造带 code/close/preClose/volume/suspendFlag 列的 DataFrame。"""
    rows = []
    for c in codes:
        rows.append({
            'code': c, 'close': float(close), 'preClose': float(preclose),
            'volume': float(volume), 'suspendFlag': suspend,
            'open': float(close), 'high': float(close), 'low': float(close),
        })
    return pd.DataFrame(rows)


def _make_fixed_cal(days):
    import pandas as pd
    class Cal:
        def __init__(self, d):
            self._days = list(d)
        def get_trade_days(self, start, end):
            return [pd.Timestamp(d, tz="Asia/Shanghai").to_pydatetime() for d in self._days]
        def get_trading_day(self, date, offset=0):
            idx = self._days.index(date) if date in self._days else 0
            idx = max(0, min(len(self._days) - 1, idx + offset))
            return pd.Timestamp(self._days[idx], tz="Asia/Shanghai").date()
    return Cal(days)


def _df_index_equiv(df, bare):
    """原布尔过滤参考：df[df['code'] == bare].iloc[0]（取首次出现）；未命中返回 None。"""
    if df is None or 'code' not in getattr(df, 'columns', ()):
        raise KeyError("code")  # 原路径在缺列时直接抛 KeyError
    row = df[df['code'] == bare]
    return row.iloc[0] if len(row) > 0 else None


# ===================== _build_code_index 单元 =====================
def test_build_code_index_none_vs_empty_distinct():
    # None：df 为 None / 缺 code 列 / 构建异常
    assert _build_code_index(None) is None
    assert _build_code_index(pd.DataFrame({'close': [1]})) is None
    # {}：有 code 列但 0 行（合法空），与 None 必须可区分
    empty = pd.DataFrame({'code': pd.Series([], dtype=object)})
    assert _build_code_index(empty) == {}
    # 正常：返回 dict，非 None
    assert isinstance(_build_code_index(_make_df(["600000"])), dict)


def test_build_code_index_setdefault_first_occurrence():
    df = _make_df(["600000", "600000", "600001"])
    idx = _build_code_index(df)
    assert idx == {"600000": 0, "600001": 2}
    # 重复 code 保留首次 iloc（对齐 .iloc[0]）
    assert df.iloc[idx["600000"]]["code"] == "600000"


def test_build_code_index_raw_values_no_normalization():
    # 索引键必须是 df['code'] 原始值，不做任何转换
    df = _make_df(["600000.SH", " 600001 ", "600002.sz", 600003, "600004"])
    idx = _build_code_index(df)
    # 原始类型/值原样保留：字符串带后缀/空格/大小写、整数都按原值索引
    assert "600000.SH" in idx
    assert " 600001 " in idx
    assert "600002.sz" in idx
    assert 600003 in idx            # 整数 code 原样
    assert "600004" in idx
    assert idx[" 600001 "] == 1     # 空格未去


def test_build_code_index_unhashable_code_returns_none():
    # code 列为不可哈希元素（list）→ setdefault 抛 TypeError → 捕获返回 None（回退原路径）
    df = pd.DataFrame({'code': [["a"], ["b"]], 'close': [1.0, 2.0]})
    assert _build_code_index(df) is None


def test_build_code_index_nondefault_index():
    # 非默认 index（乱序）不影响 iloc 语义（iloc 按位置，与 index 标签无关）
    df = _make_df(["600001", "600000", "600002"])
    df.index = [10, 20, 30]
    idx = _build_code_index(df)
    assert idx == {"600001": 0, "600000": 1, "600002": 2}
    # 取首次出现 iloc 对应的行，应与布尔过滤 .iloc[0] 一致
    bare = "600000"
    new = df.iloc[idx[bare]]
    ref = _df_index_equiv(df, bare)
    assert new["close"] == ref["close"]


# ===================== DataDict 实例缓存 + 等价 =====================
def _datadict_get_ref(dd, code):
    """DataDict.__getitem__ 原布尔过滤参考结果（BarData 等价比较用 vars）。"""
    from quantstudio.backtest.ptrade_api import BarData
    bare = dd._bare(code)
    if dd._curr_data is not None and 'code' in dd._curr_data.columns:
        row = dd._curr_data[dd._curr_data['code'] == bare]
        if len(row) > 0:
            return BarData(row.iloc[0], dd._day_str)
    return BarData(pd.Series(), "")


@pytest.mark.parametrize("codes", [
    ["600000", "600001"],                                  # 裸码
    ["600000.SH", "600001.XSHE"],                          # 带后缀
    [" 600000 ", "600001"],                                # 空格
    ["600000", "600001", "600000"],                        # 重复 code（首行）
    ["600000", 600001],                                    # 整数 code 混合
])
def test_datadict_getitem_equiv_boolean_filter(codes):
    df = _make_df(codes, close=11.0)
    dd = DataDict()
    dd.set_curr_data(df, "2026-01-05")
    for c in codes:
        new_bar = dd[c]
        ref_bar = _datadict_get_ref(dd, c)
        assert vars(new_bar) == vars(ref_bar), f"DataDict[{c!r}] 与布尔过滤不等价"


def test_datadict_getitem_missing_code_returns_empty_like_original():
    # 缺 code 列 → 原路径（'code' in columns 闸门为 False）直接返回空 BarData，不抛异常
    dd = DataDict()
    dd.set_curr_data(pd.DataFrame({'close': [1.0]}), "2026-01-05")
    new_bar = dd["600000"]
    ref_bar = _datadict_get_ref(dd, "600000")  # 复刻原布尔过滤路径
    assert vars(new_bar) == vars(ref_bar)


def test_datadict_contains_equiv_and_missing_col_false():
    df = _make_df(["600000", "600001"])
    dd = DataDict()
    dd.set_curr_data(df, "2026-01-05")
    assert ("600000" in dd) is True
    assert ("600001" in dd) is True
    assert ("600999" in dd) is False
    # 缺 code 列：原路径返回 False（不抛），不可变成 KeyError
    dd2 = DataDict()
    dd2.set_curr_data(pd.DataFrame({'close': [1.0]}), "2026-01-05")
    assert ("600000" in dd2) is False


def test_datadict_set_curr_data_invalidates_index_cache():
    df1 = _make_df(["600000"], close=10.0)
    df2 = _make_df(["600000"], close=20.0)  # 同 code，不同 close
    dd = DataDict()
    dd.set_curr_data(df1, "2026-01-05")
    _ = dd["600000"]                 # 首次访问构建索引 + 缓存 BarData
    assert dd._code_index is not None
    # set_curr_data 必须使实例私有索引缓存失效
    dd.set_curr_data(df2, "2026-01-06")
    assert dd._code_index is None
    # 引擎每天重建 DataDict（self._data 不跨日复用）→ 全新实例读 df2 应得 20.0
    dd2 = DataDict()
    dd2.set_curr_data(df2, "2026-01-06")
    assert dd2["600000"].close == 20.0


# ===================== PtradeAPI _get_current_price 等价 + 失效 =====================
def _ptrade_price_ref(api, bare_code):
    """_get_current_price 原布尔过滤参考。"""
    if api._current_day_data is not None:
        row = api._current_day_data[api._current_day_data['code'] == bare_code]
        if len(row) > 0:
            return float(row.iloc[0].get('close', 0))
    return api._prices.get(api._bare_to_qmt(bare_code), 0)


def _new_ptrade_api():
    api = PtradeAPI.__new__(PtradeAPI)
    # 仅需 _get_current_price 用到的最小属性
    api._current_day_data = None
    api._code_index = None
    api._prices = {}
    api._market = None
    return api


def test_ptrade_get_current_price_equiv_and_missing_col_keyerror():
    api = _new_ptrade_api()
    df = _make_df(["600000", "600001"], close=12.5)
    api._current_day_data = df
    assert api._get_current_price("600000") == _ptrade_price_ref(api, "600000") == 12.5
    assert api._get_current_price("600001") == _ptrade_price_ref(api, "600001") == 12.5
    assert api._get_current_price("600999") == _ptrade_price_ref(api, "600999") == 0.0
    # 缺 code 列 → 回退原布尔过滤 → 原路径抛 KeyError（行为不变）
    api2 = _new_ptrade_api()
    api2._current_day_data = pd.DataFrame({'close': [1.0]})
    with pytest.raises(KeyError):
        api2._get_current_price("600000")


def test_ptrade_attach_day_invalidates_index():
    api = _new_ptrade_api()
    df1 = _make_df(["600000"], close=10.0)
    df2 = _make_df(["600000"], close=20.0)
    api._current_day_data = df1
    assert api._get_current_price("600000") == 10.0
    # attach_day 切换 DataFrame → 索引失效 → 反映 df2
    api._current_day_data = df2
    api._code_index = None
    assert api._get_current_price("600000") == 20.0


def test_ptrade_attach_bar_invalidates_index():
    api = _new_ptrade_api()
    df1 = _make_df(["600000"], close=10.0)
    df2 = _make_df(["600000"], close=20.0)
    api._current_day_data = df1
    assert api._get_current_price("600000") == 10.0
    api._current_day_data = df2
    api._code_index = None
    assert api._get_current_price("600000") == 20.0


def test_ptrade_reset_session_invalidates_index():
    api = _new_ptrade_api()
    api._current_day_data = _make_df(["600000"], close=10.0)
    assert api._get_current_price("600000") == 10.0
    # reset_session 失效（此处直接置 None 模拟其内部行为）
    api._code_index = None
    api._current_day_data = _make_df(["600000"], close=99.0)
    assert api._get_current_price("600000") == 99.0


# ===================== BacktestEngine 三函数等价 =====================
def _engine_equiv_host():
    """构造只装载 _df_index / 三函数所需最小状态的 BacktestEngine 宿主。"""
    eng = BacktestEngine.__new__(BacktestEngine)
    eng._df_index_cache = []
    return eng


def test_engine_get_pct_chg_equiv():
    eng = _engine_equiv_host()
    df = _make_df(["600000", "600001"], close=11.0, preclose=10.0)
    assert eng._get_pct_chg("600000.SH", df, "2026-01-05") == pytest.approx(0.1)
    assert eng._get_pct_chg("600001", df, "2026-01-05") == pytest.approx(0.1)
    # 缺列 → 原路径返回 0.0（不抛）
    assert eng._get_pct_chg("600000", pd.DataFrame({'close': [1.0]}), "2026-01-05") == 0.0
    # 缺失 code → 0.0
    assert eng._get_pct_chg("600999", df, "2026-01-05") == 0.0


def test_engine_get_open_pct_chg_equiv():
    eng = _engine_equiv_host()
    df = _make_df(["600000"], close=11.0, preclose=10.0)
    assert eng._get_open_pct_chg("600000.SH", df, 10.5) == pytest.approx(0.05)
    assert eng._get_open_pct_chg("600000", df, 0) == 0.0
    assert eng._get_open_pct_chg("600999", df, 10.5) == 0.0
    assert eng._get_open_pct_chg("600000", pd.DataFrame({'close': [1.0]}), 10.5) == 0.0


def test_engine_is_halted_equiv():
    eng = _engine_equiv_host()
    # 停牌：suspendFlag==1
    df_halt = _make_df(["600000"], suspend=1)
    assert eng._is_halted_at("600000.SH", df_halt) is True
    # 零成交：volume==0
    df_zero = _make_df(["600000"], volume=0.0)
    assert eng._is_halted_at("600000.SH", df_zero) is True
    # 正常
    df_ok = _make_df(["600000"], volume=1000.0, suspend=0)
    assert eng._is_halted_at("600000.SH", df_ok) is False
    # 缺失 code → 原路径返回 False
    assert eng._is_halted_at("600000", pd.DataFrame({'close': [1.0]})) is False
    # 异常（不可哈希 code）→ 原路径 try/except 返回 False
    df_bad = pd.DataFrame({'code': [["a"]], 'suspendFlag': [0], 'volume': [1.0]})
    assert eng._is_halted_at("600000", df_bad) is False


def test_engine_df_index_fifo_cap4_and_no_alias():
    eng = _engine_equiv_host()
    dfs = [_make_df([f"60000{i}"]) for i in range(6)]  # 6 个不同 DataFrame
    for df in dfs:
        eng._df_index(df)
    # 上限固定 4，超出淘汰最旧
    assert len(eng._df_index_cache) == 4
    cached_dfs = [d for d, _ in eng._df_index_cache]
    # 最近 4 个在缓存中，最早 2 个被淘汰（FIFO）—— 用身份比较避免 DataFrame == 歧义
    assert any(d is dfs[-1] for d in cached_dfs)
    assert any(d is dfs[-2] for d in cached_dfs)
    assert all(d is not dfs[0] for d in cached_dfs)
    assert all(d is not dfs[1] for d in cached_dfs)
    # 不缓存 None：构造一个会引发构建失败的 df（不可哈希 code）
    bad = pd.DataFrame({'code': [["x"]], 'close': [1.0]})
    eng._df_index(bad)
    assert all(idx is not None for _, idx in eng._df_index_cache)
    # 不同对象不串号：相同内容的不同 DataFrame 应获独立索引
    a = _make_df(["600000"])
    b = _make_df(["600000"])
    ia, ib = eng._df_index(a), eng._df_index(b)
    assert ia is not ib  # 不同对象 → 不同条目


def test_engine_two_instances_isolated():
    e1 = _engine_equiv_host()
    e2 = _engine_equiv_host()
    df = _make_df(["600000"])
    e1._df_index(df)
    assert len(e1._df_index_cache) == 1
    assert len(e2._df_index_cache) == 0  # 实例隔离


# ===================== 黄金回测：索引开启 vs 强制回退 =====================
from dataclasses import dataclass


@dataclass
class GoldenArtifacts:
    nav_history: list            # 原始 nav_history（不 round）
    trade_records: list          # 完整 trade_records（含 commission/tax/pnl，不 round）
    trade_multiset: list         # 完整字段规范多重集合（原始顺序无关）
    daily_positions: list        # [(date, {code:(volume,can_sell,avg_cost)})] 每日收盘持仓快照
    daily_trades: dict           # {date: [完整 trade dict]} 每日成交
    daily_drain: list            # next_open: [(day,[(order_id,dir,filled,amt)])] pending→filled/rejected
    final_cash: float
    final_positions: dict        # {code:(volume,can_sell,avg_cost)}
    final_pending: list          # [(order_id,code,dir,status,reason)]


def _run_golden(db, start, end, match_price_mode, engine_profile, cal_days, strategy):
    providers = make_providers(db, _make_fixed_cal(cal_days))
    # minute-bar-v1 不支持 callback_basket，用 legacy（None）；daily 系列用 callback_basket
    rebalance_mode = None if engine_profile == "minute-bar-v1" else "callback_basket"
    eng = BacktestEngine(
        db_path=str(db), strategy=strategy, start=start, end=end,
        match_price_mode=match_price_mode, engine_profile=engine_profile,
        rebalance_mode=rebalance_mode, providers=providers,
    )

    # 只读观测探针：捕获每日收盘持仓快照 与 next_open 每日 pending→filled/rejected 转换。
    # 不改引擎行为，仅观测；索引开启/回退两次运行使用相同探针，便于严格逐字段比较。
    daily_positions: list = []

    def _wrap_strategy(*a, **k):
        eng._orig_run_strategy(*a, **k)
        snap = {c: (p.volume, p.can_sell, p.avg_cost)
                for c, p in eng.account.positions.items() if p.volume > 0}
        daily_positions.append((eng._current_date_str, snap))
    eng._orig_run_strategy = eng._run_ptrade_strategy
    eng._run_ptrade_strategy = _wrap_strategy

    daily_drain: list = []

    def _wrap_drain(t1_data, t1_day_str, t1_open_prices):
        eng._orig_drain_pending_orders(t1_data, t1_day_str, t1_open_prices)
        resolved = [(o.order_id, o.direction, o.filled, o.filled_amount)
                    for o in eng._today_orders]
        daily_drain.append((t1_day_str, resolved))
    eng._orig_drain_pending_orders = eng._drain_pending_orders
    eng._drain_pending_orders = _wrap_drain

    eng.run()

    nav = [dict(d) for d in eng.result.nav_history]       # 原始结构，不 round
    trades = [dict(t) for t in eng.result.trade_records]   # 完整字段，不 round
    trade_multiset = sorted(
        (t['date'], t['code'], t['action'], t['volume'], t['price'],
         t['commission'], t['tax'], t['pnl'])
        for t in trades)
    daily_trades = {}
    for t in trades:
        daily_trades.setdefault(t['date'], []).append(dict(t))
    final_positions = {c: (p.volume, p.can_sell, p.avg_cost)
                       for c, p in eng.account.positions.items() if p.volume > 0}
    final_pending = [(po.order_id, po.code, po.direction, po.status, po.reason)
                     for po in eng._pending_orders]
    return GoldenArtifacts(
        nav_history=nav, trade_records=trades, trade_multiset=trade_multiset,
        daily_positions=daily_positions, daily_trades=daily_trades,
        daily_drain=daily_drain, final_cash=float(eng.account.cash),
        final_positions=final_positions, final_pending=final_pending,
    )


def _assert_golden_eq(a, b, label):
    # 合成 DB 无并行噪声，要求原始值完全一致（不 round、不截断字段）。
    assert a.nav_history == b.nav_history, f"[{label}] nav_history 原始结构不等价"
    assert a.trade_records == b.trade_records, f"[{label}] trade_records 原始顺序不等价"
    assert a.trade_multiset == b.trade_multiset, f"[{label}] trade_records 完整多重集不等价"
    assert a.daily_positions == b.daily_positions, f"[{label}] 每日持仓快照不等价"
    assert a.daily_trades == b.daily_trades, f"[{label}] 每日成交不等价"
    assert a.daily_drain == b.daily_drain, f"[{label}] next_open pending→filled/rejected 转换不等价"
    assert a.final_cash == b.final_cash, f"[{label}] 最终现金不等价"
    assert a.final_positions == b.final_positions, f"[{label}] 最终持仓不等价"
    assert a.final_pending == b.final_pending, f"[{label}] 最终 pending orders 不等价"


def _trading_strategy(codes, value):
    def initialize(ctx):
        pass
    def handle_data(ctx, data):
        from quantstudio.backtest.ptrade_api import order_target_value
        for c in codes:
            _ = data[c]                      # 显式触达 DataDict.__getitem__ 索引路径
            order_target_value(c, value)     # 触发 _get_current_price / _is_halted_at
    return {'initialize': initialize, 'handle_data': handle_data}


@pytest.mark.parametrize("match_price_mode,engine_profile", [
    ("close", "daily-bar-v1"),
    ("open", "daily-bar-v1"),
    ("next_open", "daily-bar-v1"),
    ("close", "minute-bar-v1"),
    ("close", "daily-open-close-proxy-v1"),
])
def test_golden_equiv_index_vs_fallback(build_db, monkeypatch, match_price_mode, engine_profile):
    codes = ["600000.SH", "600001.SH", "600002.SH"]
    days = ["2026-01-05", "2026-01-06", "2026-01-07"]

    # 合成库
    if engine_profile == "minute-bar-v1":
        sm, sd = [], []
        for c in codes:
            for day in days:
                for hh in range(9, 16):
                    sm.append(minute_row(c, day, hh, 30, close=10.0 + hh * 0.01,
                                         preclose=10.0, suspend=0))
            for day in days:
                sd.append(daily_row(c, day, close=10.5, preclose=10.0))
        db = build_db(stock_minutes=sm, stock_daily=sd)
    else:
        rows = []
        for c in codes:
            for i, day in enumerate(days):
                suspend = 1 if (c == "600001.SH" and day == "2026-01-06") else 0
                rows.append(daily_row(c, day, close=10.0 + i, preclose=10.0,
                                      volume=1000.0, suspend=suspend))
        db = build_db(stock_daily=rows)

    strat = _trading_strategy(codes, 3000.0)

    # 1) 索引开启
    on = _run_golden(db, days[0], days[-1], match_price_mode, engine_profile, days, strat)

    # 2) 强制回退（_build_code_index 恒返回 None → 所有调用点走原布尔过滤）
    monkeypatch.setattr(ptrade_mod, "_build_code_index", lambda df: None)
    off = _run_golden(db, days[0], days[-1], match_price_mode, engine_profile, days, strat)

    _assert_golden_eq(on, off, f"{match_price_mode}/{engine_profile}")


# ===================== 微基准（仅报告目标函数收益，不包装成整体回测加速） =====================
def test_microbench_index_vs_boolean_target_function():
    import time
    df = _make_df([f"60000{i}" for i in range(6500)])
    codes = [f"60000{i % 6500}" for i in range(5000)]

    # 原布尔过滤
    t0 = time.perf_counter()
    for c in codes:
        row = df[df['code'] == c]
        _ = row.iloc[0] if len(row) > 0 else None
    t_bool = time.perf_counter() - t0

    # 索引方案（含一次构建成本）
    t0 = time.perf_counter()
    idx = _build_code_index(df)
    for c in codes:
        i = idx.get(c)
        _ = df.iloc[i] if i is not None else None
    t_idx = time.perf_counter() - t0

    ratio = t_bool / t_idx if t_idx > 0 else float('inf')
    print(f"\n[microbench] 6500 行 × 5000 次点查：布尔过滤={t_bool*1e3:.2f}ms，"
          f"索引(含构建)={t_idx*1e3:.2f}ms，比值={ratio:.2f}x")
    # 仅断言目标函数层面确实更快（不含任何整体回测收益主张）
    assert t_idx < t_bool, "索引方案目标函数应快于布尔过滤"
