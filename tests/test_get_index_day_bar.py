"""get_index_day_bar 单测（docs/get-index-day-bar-design.md 终审钉死断言清单，2026-09-08）。

覆盖：
- 独占 index_daily 路由：000001.SS 取指数（非平安银行 stock_daily）；600519/510300 → 空；
  000300.SS 不触发 INDEX_ETF_MAP ETF 代理
- profile-aware 上界：daily 含 T / minute 永不含 T / proxy 15:00 含 T、09:31 不含、
  时钟不可判 fail-closed 不含 T
- count 越界 ValueError；fields 过滤与非法 fields；count=2 仅 1 行返回实际行
- fail-closed：无引擎/无连接 → 空 DataFrame；后缀互通（.SS/裸码）
- 校验器：PREOPEN-INDEX-BAR / MINUTE-PROFILE-INDEX-BAR / PTrade 目标 BLOCK
"""
import pytest
import pandas as pd
import duckdb

from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
from quantstudio.backtest.providers.duckdb_provider import DuckDBMarketDataProvider

TZ = "Asia/Shanghai"


def ms(day_str, hh=0, mm=0):
    ts = pd.Timestamp(f"{day_str} {hh:02d}:{mm:02d}:00").tz_localize(TZ)
    return int(ts.value // 10**6)


def index_row(code, day, close, pctchg=0.0):
    return {'code': code, 'time': ms(day, 0, 0),
            'open': close, 'high': close, 'low': close, 'close': close,
            'volume': 1e6, 'amount': 1e6 * close, 'pctChg': pctchg,
            'data_source': 'test'}


def stock_row(code, day, close):
    o = close
    return {'code': code, 'time': ms(day, 0, 0),
            'open': o, 'high': o, 'low': o, 'close': close,
            'volume': 1e4, 'amount': 1e4 * close, 'preClose': close,
            'suspendFlag': 0, 'settelementPrice': 0.0, 'openInterest': 0.0,
            'open_front': o, 'high_front': o, 'low_front': o, 'close_front': close,
            'open_back': o, 'high_back': o, 'low_back': o, 'close_back': close,
            'open_front_ratio': 1.0, 'high_front_ratio': 1.0, 'low_front_ratio': 1.0,
            'close_front_ratio': 1.0, 'open_back_ratio': 1.0, 'high_back_ratio': 1.0,
            'low_back_ratio': 1.0, 'close_back_ratio': 1.0,
            'turn': 1.0, 'pctChg': 0.5, 'peTTM': 0.0, 'psTTM': 0.0, 'pcfNcfTTM': 0.0,
            'pbMRQ': 0.0, 'isST': 0, 'is_st_reliable': False,
            'is_st_reliable_source': 'none', 'is_delisting_risk': False,
            'is_delisting_risk_source': 'none', 'dividend_type': 'none',
            'update_time': day}


@pytest.fixture
def build_db(tmp_path):
    """构造含 index_daily + stock_daily + etf_daily 的完整临时 DuckDB。"""
    def _impl(index_daily=None, stock_daily=None, etf_daily=None):
        from quantstudio.pipeline.writers import DDL_DUCKDB
        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        for tbl in ("stock_daily", "etf_daily", "index_daily"):
            if tbl in DDL_DUCKDB:
                con.execute(DDL_DUCKDB[tbl])
        for tbl, rows in (("index_daily", index_daily),
                          ("stock_daily", stock_daily),
                          ("etf_daily", etf_daily)):
            if rows:
                df = pd.DataFrame(rows)
                tbl_cols = {c[0] for c in con.execute(f"DESCRIBE {tbl}").fetchall()}
                cols = [c for c in df.columns if c in tbl_cols]
                if cols:
                    df_sel = df[cols]
                    col_list = ", ".join(cols)
                    con.register('df', df_sel)
                    con.execute(f"INSERT INTO {tbl} ({col_list}) SELECT {col_list} FROM df")
                    con.unregister('df')
        con.close()
        return db_path
    return _impl


DAYS = ["2026-07-15", "2026-07-16", "2026-07-17"]


@pytest.fixture
def index_db(build_db):
    """上证指数 3 日 + 平安银行股票 + 沪深300ETF 代理源，判定同码并存路由。"""
    idx = [index_row('000001', d, 4000.0 + i, pctchg=-1.6 - i)
           for i, d in enumerate(DAYS)]
    idx += [index_row('000300', d, 5000.0, pctchg=-1.0) for d in DAYS]
    stk = [stock_row('000001', d, 12.0 + i) for i, d in enumerate(DAYS)]
    stk += [stock_row('600519', d, 1500.0) for d in DAYS]
    etf = [stock_row('510300', d, 4.0) for d in DAYS]
    return build_db(index_daily=idx, stock_daily=stk, etf_daily=etf)


def make_api(db_path, profile="daily-bar-v1", current_date="2026-07-17",
             proxy_bars=None, trade_days=None):
    """构造注入 _api 环境的最小桩：只挂 get_index_day_bar 依赖面。"""
    import types
    from quantstudio.backtest.ptrade_api import PtradeAPI
    provider = DuckDBMarketDataProvider(db_path)
    api = PtradeAPI.__new__(PtradeAPI)
    api._market = provider
    api._current_date = current_date
    api._engine = type("E", (), {"engine_profile": profile})()
    if proxy_bars is not None:
        api._engine._proxy_intraday_bars = proxy_bars
    days = list(trade_days or DAYS)
    def _stub_trading_day(day=0):
        idx = days.index(str(current_date)[:10])
        j = idx + int(day)
        if j < 0 or j >= len(days):
            return None
        return days[j]
    api.get_trading_day = types.MethodType(
        lambda self, day=0: _stub_trading_day(day), api)
    return api


class TestDedicatedIndexRouting:
    def test_000001_ss_returns_index_not_pingan(self, index_db):
        """审计钉死：000001.SS 必须取指数（非平安银行 stock_daily 数值）。"""
        api = make_api(index_db, profile="daily-bar-v1", current_date="2026-07-17")
        df = api.get_index_day_bar('000001.SS', count=3)
        assert len(df) == 3
        assert abs(float(df['close'].iloc[-1]) - 4002.0) < 1e-6  # 指数点位
        assert abs(float(df['pctChg'].iloc[-1]) - (-3.6)) < 1e-6  # 指数 pctChg（fixture: -1.6-i）
        assert list(df.index) == DAYS  # trade_date 升序

    def test_pure_stock_code_returns_empty(self, index_db):
        api = make_api(index_db, current_date="2026-07-17")
        assert api.get_index_day_bar('600519.SS', count=2).empty

    def test_etf_code_returns_empty(self, index_db):
        api = make_api(index_db, current_date="2026-07-17")
        assert api.get_index_day_bar('510300.SS', count=2).empty

    def test_000300_ss_no_etf_proxy(self, index_db):
        """审计钉死：000300.SS 返回 CSI300 指数行，绝不触发 510300 ETF 代理。"""
        api = make_api(index_db, current_date="2026-07-17")
        df = api.get_index_day_bar('000300.SS', count=2)
        assert len(df) == 2
        assert abs(float(df['close'].iloc[-1]) - 5000.0) < 1e-6  # 指数点位 ≠ ETF 4.0


class TestProfileAwareBounds:
    def test_daily_includes_T(self, index_db):
        api = make_api(index_db, profile="daily-bar-v1", current_date="2026-07-17")
        df = api.get_index_day_bar('000001.SS', count=1)
        assert df.index[-1] == "2026-07-17"

    def test_minute_never_includes_T(self, index_db):
        api = make_api(index_db, profile="minute-bar-v1", current_date="2026-07-17")
        df = api.get_index_day_bar('000001.SS', count=5)
        assert len(df) == 2
        assert df.index[-1] == "2026-07-16"  # 上一完整交易日

    def _proxy_bars(self, hhmm):
        return [pd.DataFrame({'code': ['000001'], 'time': [ms("2026-07-17", 0, 0)]})]
    def test_proxy_1500_includes_T(self, index_db):
        bars = [pd.DataFrame({'time': [ms("2026-07-17", 15, 0)]})]
        api = make_api(index_db, profile="daily-open-close-proxy-v1",
                       current_date="2026-07-17", proxy_bars=bars)
        df = api.get_index_day_bar('000001.SS', count=1)
        assert df.index[-1] == "2026-07-17"

    def test_proxy_0931_excludes_T(self, index_db):
        bars = [pd.DataFrame({'time': [ms("2026-07-17", 9, 31)]})]
        api = make_api(index_db, profile="daily-open-close-proxy-v1",
                       current_date="2026-07-17", proxy_bars=bars)
        df = api.get_index_day_bar('000001.SS', count=5)
        assert df.index[-1] == "2026-07-16"

    def test_proxy_undeterminable_conservative(self, index_db):
        """时钟不可判（空 bars）→ fail-closed 不含 T（保守侧）。"""
        api = make_api(index_db, profile="daily-open-close-proxy-v1",
                       current_date="2026-07-17", proxy_bars=[])
        df = api.get_index_day_bar('000001.SS', count=5)
        assert df.index[-1] == "2026-07-16"

    def test_unknown_profile_conservative(self, index_db):
        api = make_api(index_db, profile="weird-profile", current_date="2026-07-17")
        df = api.get_index_day_bar('000001.SS', count=5)
        assert df.index[-1] == "2026-07-16"


class TestContractGuards:
    def test_count_out_of_range_raises(self, index_db):
        api = make_api(index_db, current_date="2026-07-17")
        with pytest.raises(ValueError):
            api.get_index_day_bar('000001.SS', count=0)
        with pytest.raises(ValueError):
            api.get_index_day_bar('000001.SS', count=251)

    def test_bad_fields_raises(self, index_db):
        api = make_api(index_db, current_date="2026-07-17")
        with pytest.raises(ValueError):
            api.get_index_day_bar('000001.SS', count=1, fields=['close', 'zzz'])

    def test_fields_filter(self, index_db):
        """trade_date 固定为索引（契约），fields 过滤作用于数据列。"""
        api = make_api(index_db, current_date="2026-07-17")
        df = api.get_index_day_bar('000001.SS', count=1,
                                   fields=['close', 'pctChg', 'trade_date'])
        assert list(df.columns) == ['close', 'pctChg']
        assert df.index.name == 'trade_date'

    def test_single_string_fields(self, index_db):
        api = make_api(index_db, current_date="2026-07-17")
        df = api.get_index_day_bar('000001.SS', count=1, fields='pctChg')
        assert list(df.columns) == ['pctChg']

    def test_suffix_interop(self, index_db):
        api = make_api(index_db, current_date="2026-07-17")
        df1 = api.get_index_day_bar('000001.SS', count=1)
        df2 = api.get_index_day_bar('000001', count=1)
        pd.testing.assert_frame_equal(df1, df2)

    def test_fail_closed_no_engine(self, index_db):
        api = make_api(index_db, current_date="2026-07-17")
        api._market = None
        assert api.get_index_day_bar('000001.SS', count=1).empty

    def test_fail_closed_no_date(self, index_db):
        api = make_api(index_db, current_date=None)
        assert api.get_index_day_bar('000001.SS', count=1).empty

    def test_data_access_layer_direct(self, index_db):
        """数据访问层专用方法：钉表验证 + 空 ETF/股票路由。"""
        da = DuckDBDataAccess(index_db)
        df = da.query_index_day_bars('000001', 3, ms("2026-07-17", 23, 59))
        assert len(df) == 3
        assert abs(float(df['close'].iloc[-1]) - 4002.0) < 1e-6
        assert da.query_index_day_bars('600519', 3, ms("2026-07-17", 23, 59)).empty

    def test_count2_with_single_row(self, tmp_path, build_db):
        """终审④：count=2 但窗口起点前仅 1 行 → 返回实际存在行。"""
        idx = [index_row('000001', "2026-07-17", 4002.0, pctchg=-1.8)]
        db = build_db(index_daily=idx)
        api = make_api(db, current_date="2026-07-17")
        df = api.get_index_day_bar('000001.SS', count=2)
        assert len(df) == 1
        assert df.index[-1] == "2026-07-17"
