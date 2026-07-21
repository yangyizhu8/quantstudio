"""PR4 契约测试【修正缺口 1】：分钟查询不含未来 bar。

这是 PR4 最关键的一条测试（方案要求最先写红灯）。

缺口 1 背景：PR3 的 get_history/get_price 分钟查询锚定到当日零点（pd.Timestamp(self._current_date)），
compute_intraday_windows 返回全天时段，end_cutoff_ms 只剔除 15:00 bar。在分钟 Profile 下，
10:00 bar 的策略调 get_history(60, '1m') 会拿到 10:01-14:59 的未来 bar——这正是 PR2 消灭的
穿越的日内版本。

修正：attach_bar 注入 _current_bar_ts；分钟 Profile 下 get_history/get_price 的分钟查询
锚定到 _current_bar_ts，当日窗口按 bar 截断（含当前 bar，因为 end-labeled 下当前 bar 已完成）。

"当前 bar 可见"语义（方案 3.3）：
- 分钟 Profile：end-labeled 下 10:00 事件触发时 10:00 bar 已是已完成历史（覆盖 09:59:01-10:00:00），
  是可见的。故 get_history 在 10:00 bar 处含 10:00 bar 本身。
- 日线 Profile：PR3 的"剔除当前 bar"是日级 now 未定义的保守选择。两 Profile 语义不同。
"""
import pytest
import pandas as pd
import duckdb


def _ms(day_str, hh, mm, ss=0):
    ts = pd.Timestamp(f"{day_str} {hh:02d}:{mm:02d}:{ss:02d}").tz_localize("Asia/Shanghai")
    return int(ts.value // 10**6)


@pytest.fixture
def minute_db(tmp_path):
    """临时 DuckDB + 合成 stock_minutes 数据：一个完整交易日的 1min bar（09:31-15:00）。

    end-labeled 约定：bar 标注时刻 = 分钟结束时刻。
    """
    from quantstudio.pipeline.writers import DDL_DUCKDB
    db_path = tmp_path / "noleak.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(DDL_DUCKDB["stock_minutes"])
    day = "2026-01-05"
    bars = []
    # 上午 09:31-11:30（121 根），下午 13:01-15:00（120 根）
    times = ([(h, m) for h in range(9, 12) for m in range(0, 60)]
             + [(13, m) for m in range(0, 60)] + [(14, m) for m in range(0, 60)])
    # 过滤到交易时段：09:31-11:30, 13:01-15:00
    valid = []
    for h, m in times:
        hhmm = h * 100 + m
        if (931 <= hhmm <= 1130) or (1301 <= hhmm <= 1500):
            valid.append((h, m))
    for idx, (hh, mm) in enumerate(valid):
        ms = _ms(day, hh, mm)
        # close 价 = bar 序号，便于断言"返回了哪些 bar"
        bars.append({
            'code': '600000', 'time': ms, 'freq': '1min',
            'open': float(idx), 'high': float(idx), 'low': float(idx), 'close': float(idx),
            'volume': 10000.0, 'amount': 100000.0, 'preClose': 0.0,
            'suspendFlag': 0, 'settelementPrice': 0.0, 'openInterest': 0.0,
            'open_front': float(idx), 'high_front': float(idx), 'low_front': float(idx), 'close_front': float(idx),
            'open_back': float(idx), 'high_back': float(idx), 'low_back': float(idx), 'close_back': float(idx),
            'open_front_ratio': 1.0, 'high_front_ratio': 1.0, 'low_front_ratio': 1.0, 'close_front_ratio': 1.0,
            'open_back_ratio': 1.0, 'high_back_ratio': 1.0, 'low_back_ratio': 1.0, 'close_back_ratio': 1.0,
            'dividend_type': 'none', 'update_time': day,
        })
    df = pd.DataFrame(bars)
    con.register('df', df)
    con.execute("INSERT INTO stock_minutes SELECT * FROM df")
    con.unregister('df')
    con.close()
    yield db_path


@pytest.fixture
def calendar_provider():
    class Cal:
        def get_trade_days(self, start, end):
            return [pd.Timestamp("2026-01-05", tz="Asia/Shanghai").to_pydatetime()]
    return Cal()


# ========== 核心断言：10:00 bar 处 get_history 不含未来 bar ==========

def test_get_history_minute_at_10am_excludes_future_bars(minute_db, calendar_provider):
    """【修正缺口 1】10:00 bar 处 get_history(count, '1m') 返回的 bar 时刻全部 ≤ 10:00。

    分钟 Profile 下，attach_bar 注入 _current_bar_ts；get_history 分钟查询锚定到此，
    当日窗口截断到 10:00（含），不含 10:01 之后任何 bar。
    """
    from quantstudio.backtest.ptrade_api import _api
    from quantstudio.backtest.providers.duckdb_provider import DuckDBMarketDataProvider
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess

    provider = DuckDBMarketDataProvider(minute_db, calendar_provider=calendar_provider)
    _api._market = provider
    _api._engine = None  # 避免 attach 触发 engine 路径
    _api._query_cache = {}
    _api._current_date = "2026-01-05"
    _api._prev_date = "2026-01-04"
    # 【修正缺口 1】模拟 attach_bar 注入 _current_bar_ts = 10:00
    bar_ts = pd.Timestamp("2026-01-05 10:00:00").tz_localize("Asia/Shanghai")
    _api._current_bar_ts = bar_ts

    # get_history 60 根 1min bar，锚定到 10:00（含）
    result = _api.get_history(60, frequency='1m', field=["close"],
                              security_list=["600000.SH"], is_dict=True)

    df = result["600000.SZ"] if "600000.SZ" in result else result.get("600000.SH")
    assert df is not None and len(df) > 0
    # 验证返回的所有 bar 时刻 ≤ 10:00（无未来泄漏）
    times = pd.to_datetime(df['time'], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai')
    bar_10am_ms = _ms("2026-01-05", 10, 0)
    for t_ms in df['time']:
        assert t_ms <= bar_10am_ms, f"未来 bar 泄漏：{t_ms} > 10:00 ({bar_10am_ms})"


def test_current_bar_ts_defaults_none_in_daily_profile():
    """日线 Profile 下 _current_bar_ts 为 None（PR3 原行为不变）。

    日线 Profile 不注入 _current_bar_ts，get_history 分钟查询走 PR3 原全天窗口逻辑
    （end_cutoff 剔除 15:00 bar，这是日级 now 未定义的保守选择）。
    """
    from quantstudio.backtest.ptrade_api import _api
    # reset 后 _current_bar_ts 不存在或为 None
    _api.reset_session()
    assert getattr(_api, '_current_bar_ts', None) is None


def test_attach_bar_injects_current_bar_ts():
    """attach_bar 注入 _current_bar_ts（修正缺口 1 的实现入口）"""
    from quantstudio.backtest.ptrade_api import _api
    import pandas as pd
    bar_ts = pd.Timestamp("2026-01-05 10:00:00").tz_localize("Asia/Shanghai")
    _api.attach_bar(None, pd.DataFrame(), "2026-01-05", "2026-01-04",
                    prices={}, pct_chg_map={}, current_bar_ts=bar_ts)
    assert _api._current_bar_ts == bar_ts
    _api.reset_session()


# ========== count 语义：返回最近的 N 根（含当前 bar）==========

def test_get_history_minute_count_returns_most_recent_n(minute_db, calendar_provider):
    """get_history(5, '1m') 在 10:00 bar 处返回 09:56-10:00 共 5 根（含当前 bar）"""
    from quantstudio.backtest.ptrade_api import _api
    from quantstudio.backtest.providers.duckdb_provider import DuckDBMarketDataProvider

    provider = DuckDBMarketDataProvider(minute_db, calendar_provider=calendar_provider)
    _api._market = provider
    _api._engine = None
    _api._query_cache = {}
    _api._current_date = "2026-01-05"
    _api._prev_date = "2026-01-04"
    bar_ts = pd.Timestamp("2026-01-05 10:00:00").tz_localize("Asia/Shanghai")
    _api._current_bar_ts = bar_ts

    result = _api.get_history(5, frequency='1m', field=["close"],
                              security_list=["600000.SH"], is_dict=True)
    df = result["600000.SZ"] if "600000.SZ" in result else result.get("600000.SH")
    assert df is not None
    assert len(df) == 5   # 正好 5 根
    # 最后一根应是 10:00（含当前 bar）
    last_ms = df['time'].iloc[-1]
    assert last_ms == _ms("2026-01-05", 10, 0)
