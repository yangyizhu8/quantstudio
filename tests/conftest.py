"""PR4 测试共享 fixture：完整的临时 DuckDB（真实 DDL + 合成分钟/日线数据）。

所有分钟引擎测试通过此 fixture 获得表结构正确的临时库。
用真实 DDL（writers.DDL_DUCKDB）建表，用 helper 生成完整列数的行，避免列数不匹配。
"""
import pytest
import pandas as pd
import duckdb


TZ = "Asia/Shanghai"


def ms(day_str, hh=0, mm=0, ss=0):
    """Asia/Shanghai 时区的 epoch 毫秒戳（13 位）"""
    ts = pd.Timestamp(f"{day_str} {hh:02d}:{mm:02d}:{ss:02d}").tz_localize(TZ)
    return int(ts.value // 10**6)


def minute_row(code, day, hh, mm, close, freq='1min', open_p=None, high=None, low=None,
               volume=1000.0, amount=1000.0, preclose=0.9, suspend=0):
    """生成一行完整的 stock_minutes/etf_minutes 数据（32 列）"""
    o = open_p if open_p is not None else close
    h = high if high is not None else close
    lo = low if low is not None else close
    return {
        'code': code, 'time': ms(day, hh, mm), 'freq': freq,
        'open': o, 'high': h, 'low': lo, 'close': close,
        'volume': volume, 'amount': amount, 'preClose': preclose,
        'suspendFlag': suspend, 'settelementPrice': 0.0, 'openInterest': 0.0,
        'open_front': o, 'high_front': h, 'low_front': lo, 'close_front': close,
        'open_back': o, 'high_back': h, 'low_back': lo, 'close_back': close,
        'open_front_ratio': 1.0, 'high_front_ratio': 1.0, 'low_front_ratio': 1.0, 'close_front_ratio': 1.0,
        'open_back_ratio': 1.0, 'high_back_ratio': 1.0, 'low_back_ratio': 1.0, 'close_back_ratio': 1.0,
        'dividend_type': 'none', 'update_time': day,
    }


def daily_row(code, day, close, open_p=None, high=None, low=None, preclose=None,
              pctchg=0.0, volume=10000.0, suspend=0):
    """生成一行完整的 stock_daily/etf_daily 数据（用真实 DDL 的列）。

    用 dict 形式，由 pandas DataFrame 补齐缺失列为 NULL。
    """
    o = open_p if open_p is not None else close
    h = high if high is not None else close
    lo = low if low is not None else close
    pc = preclose if preclose is not None else close
    return {
        'code': code, 'time': ms(day, 0, 0),
        'open': o, 'high': h, 'low': lo, 'close': close,
        'volume': volume, 'amount': volume * close, 'preClose': pc,
        'suspendFlag': suspend, 'settelementPrice': 0.0, 'openInterest': 0.0,
        'open_front': o, 'high_front': h, 'low_front': lo, 'close_front': close,
        'open_back': o, 'high_back': h, 'low_back': lo, 'close_back': close,
        'open_front_ratio': 1.0, 'high_front_ratio': 1.0, 'low_front_ratio': 1.0, 'close_front_ratio': 1.0,
        'open_back_ratio': 1.0, 'high_back_ratio': 1.0, 'low_back_ratio': 1.0, 'close_back_ratio': 1.0,
        'turn': 1.0, 'pctChg': pctchg, 'peTTM': 0.0, 'psTTM': 0.0, 'pcfNcfTTM': 0.0, 'pbMRQ': 0.0,
        'isST': 0,
        'is_st_reliable': False, 'is_st_reliable_source': 'none',
        'is_delisting_risk': False, 'is_delisting_risk_source': 'none',
        'dividend_type': 'none', 'update_time': day,
    }


@pytest.fixture
def build_db(tmp_path):
    """工厂 fixture：构造完整临时 DuckDB。

    用法：
        def test_x(build_db):
            db = build_db(stock_minutes=[minute_row(...)], etf_daily=[daily_row(...)])
    """
    def _impl(stock_minutes=None, etf_minutes=None, stock_daily=None, etf_daily=None):
        from quantstudio.pipeline.writers import DDL_DUCKDB
        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        for tbl in ("stock_daily", "etf_daily", "stock_minutes", "etf_minutes"):
            if tbl in DDL_DUCKDB:
                con.execute(DDL_DUCKDB[tbl])
        for tbl, rows in (("stock_minutes", stock_minutes), ("etf_minutes", etf_minutes),
                          ("stock_daily", stock_daily), ("etf_daily", etf_daily)):
            if rows:
                df = pd.DataFrame(rows)
                # 只插入 df 与表共有的列（stock_daily 41 列 vs etf_daily 30 列结构不同）
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


@pytest.fixture
def cal():
    """简易 calendar provider"""
    class Cal:
        def __init__(self, days=("2026-01-05",)):
            self._days = days
        def get_trade_days(self, start, end):
            return [pd.Timestamp(d, tz=TZ).to_pydatetime() for d in self._days]
        def get_trading_day(self, date, offset=0):
            return pd.Timestamp(self._days[0] if self._days else "2026-01-05",
                                tz=TZ).date()
    return Cal()


def make_providers(db_path, calendar):
    """构造完整 DataProviderRegistry 风格的对象（market 用真实 DuckDBMarketDataProvider）"""
    from quantstudio.backtest.providers.duckdb_provider import DuckDBMarketDataProvider
    market = DuckDBMarketDataProvider(db_path, calendar_provider=calendar)
    return type("P", (), {
        "market": market,
        "fundamental": None, "reference": None, "calendar": calendar,
    })()
