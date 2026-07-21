"""PR3 契约测试：分钟 bar 查询（合成 stock_minutes 数据）。

验证目标（主计划 7.21 验收标准）：
1. get_bars(frequency='1m') 真实读取 stock_minutes 表
2. 返回 bar 的时刻全部落在交易时段 [09:31-11:30] ∪ [13:01-15:00]（本轮修正核心）
3. 字段映射：分钟表无 turn/pctChg/peTTM/pbMRQ（与日线分离）
4. fq='pre' 用前复权列（open_front 等）替换 OHLC
5. fixture 与真实表逐列一致（32 列 + BIGINT 毫秒戳 time + freq 列）

【补齐 1 标注约定】假设 end-labeled：bar 标注时刻 = 分钟结束时刻。
  - 09:31 bar = 09:30:01-09:31:00 累积；15:00 bar = 14:59:01-15:00:00。
  - 查询窗口 [09:31, 11:30] ∪ [13:01, 15:00]（含两端）。
  - 此约定无法对真实数据验证（表空），列为 pr3 报告"真实数据冒烟首要验证项"。
"""
import pytest
import pandas as pd
import duckdb


def _ms(day_str, hh, mm, ss=0):
    """生成 Asia/Shanghai 时区的 epoch 毫秒戳（13 位）"""
    ts = pd.Timestamp(f"{day_str} {hh:02d}:{mm:02d}:{ss:02d}").tz_localize("Asia/Shanghai")
    return int(ts.value // 10**6)


@pytest.fixture
def minute_db(tmp_path):
    """临时 DuckDB，含真实 schema 的 stock_minutes + 合成 1min 数据。

    用 writers.DDL_DUCKDB["stock_minutes"] 真实 DDL（32 列）建表，
    插入一个交易日的边界 bar：09:31/11:30/13:01/15:00 + 中间若干 bar。
    """
    from quantstudio.pipeline.writers import DDL_DUCKDB
    db_path = tmp_path / "minute.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(DDL_DUCKDB["stock_minutes"])

    day = "2026-01-05"
    bars = []
    # 边界 bar + 中间样本（end-labeled：09:31 是第一根，15:00 是最后一根）
    times = [(9, 31), (9, 32), (10, 0), (11, 30), (13, 1), (13, 30), (14, 59), (15, 0)]
    for hh, mm in times:
        ms = _ms(day, hh, mm)
        bars.append({
            'code': '600000', 'time': ms, 'freq': '1min',
            'open': 10.0, 'high': 10.5, 'low': 9.8, 'close': 10.2,
            'volume': 10000.0, 'amount': 100000.0, 'preClose': 9.9,
            'suspendFlag': 0, 'settelementPrice': 0.0, 'openInterest': 0.0,
            'open_front': 9.0, 'high_front': 9.5, 'low_front': 8.8, 'close_front': 9.2,
            'open_back': 11.0, 'high_back': 11.5, 'low_back': 10.8, 'close_back': 11.2,
            'open_front_ratio': 0.9, 'high_front_ratio': 0.9, 'low_front_ratio': 0.9, 'close_front_ratio': 0.9,
            'open_back_ratio': 1.1, 'high_back_ratio': 1.1, 'low_back_ratio': 1.1, 'close_back_ratio': 1.1,
            'dividend_type': 'none', 'update_time': '2026-01-05',
        })
    df = pd.DataFrame(bars)
    con.register('df', df)
    con.execute("INSERT INTO stock_minutes SELECT * FROM df")
    con.unregister('df')
    con.close()   # 先关闭写连接，避免 DuckDB 文件锁阻塞 DuckDBDataAccess 的 read_only 打开
    yield db_path


@pytest.fixture
def calendar_provider():
    """简易 calendar provider，只返回 2026-01-05 一个交易日"""
    class Cal:
        def get_trade_days(self, start, end):
            return [pd.Timestamp("2026-01-05", tz="Asia/Shanghai").to_pydatetime()]
    return Cal()


# ========== 基础查询：返回正确 bar ==========

def test_get_bars_1m_returns_minute_data(minute_db, calendar_provider):
    """get_bars(frequency='1m') 读取 stock_minutes 返回正确分钟 bar"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    da = DuckDBDataAccess(minute_db)
    df = da.query_minute_bars_by_range(
        "600000", "2026-01-05", "2026-01-05", "1min",
        fq=None, calendar_provider=calendar_provider)
    assert len(df) == 8  # 全部 8 个 bar


def test_minute_bars_all_within_trading_session(minute_db, calendar_provider):
    """返回 bar 的时刻全部落在 [09:31-11:30] ∪ [13:01-15:00]（本轮修正核心）

    验证时段过滤用 Python 侧 epoch 毫秒窗口正确，没有 time%N 算术的错位。
    """
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    da = DuckDBDataAccess(minute_db)
    df = da.query_minute_bars_by_range(
        "600000", "2026-01-05", "2026-01-05", "1min",
        fq=None, calendar_provider=calendar_provider)
    times = pd.to_datetime(df['time'], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai')
    hhmm = times.dt.hour * 100 + times.dt.minute
    for t in hhmm:
        in_morning = 931 <= t <= 1130
        in_afternoon = 1301 <= t <= 1500
        assert in_morning or in_afternoon, f"bar {t} 落在交易时段外"


def test_minute_bars_exclude_midday_gap(minute_db, calendar_provider):
    """午休时段（11:31-12:59）不返回 bar（无虚假 bar）"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    # 插入一个午休时段的"垃圾"bar，验证被过滤
    con = duckdb.connect(str(minute_db), read_only=False)
    junk_ms = _ms("2026-01-05", 12, 0)
    con.execute(
        "INSERT INTO stock_minutes VALUES "
        "('600000', ?, '1min', 10,10,10,10, 100,1000, 9.9, 0,0,0, 9,9,9,9, 11,11,11,11, "
        "0.9,0.9,0.9,0.9, 1.1,1.1,1.1,1.1, 'none', 'x')", [junk_ms])
    con.close()

    da = DuckDBDataAccess(minute_db)
    df = da.query_minute_bars_by_range(
        "600000", "2026-01-05", "2026-01-05", "1min",
        fq=None, calendar_provider=calendar_provider)
    times = pd.to_datetime(df['time'], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai')
    hhmm = times.dt.hour * 100 + times.dt.minute
    # 不含 12:00 的垃圾 bar
    assert all(~((hhmm > 1130) & (hhmm < 1301)))


# ========== 字段映射：分钟表无日线专有字段 ==========

def test_minute_bars_have_no_daily_only_fields(minute_db, calendar_provider):
    """分钟查询返回的列不含 turn/pctChg/peTTM/pbMRQ（与日线分离）"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    da = DuckDBDataAccess(minute_db)
    df = da.query_minute_bars_by_range(
        "600000", "2026-01-05", "2026-01-05", "1min",
        fq=None, calendar_provider=calendar_provider)
    for daily_only in ('turn', 'pctChg', 'peTTM', 'psTTM', 'pcfNcfTTM', 'pbMRQ'):
        assert daily_only not in df.columns, f"分钟查询不应返回日线专有字段 {daily_only}"


# ========== 复权：fq='pre' 用 front 列 ==========

def test_minute_bars_qfq_pre_uses_front_columns(minute_db, calendar_provider):
    """fq='pre'：OHLC 被 *_front 列替换（补齐 3：preClose 保持原始，已知简化）"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    da = DuckDBDataAccess(minute_db)
    df = da.query_minute_bars_by_range(
        "600000", "2026-01-05", "2026-01-05", "1min",
        fq='pre', calendar_provider=calendar_provider)
    # close_front=9.2（前复权），原始 close=10.2
    assert (df['close'] == 9.2).all()
    assert (df['open'] == 9.0).all()
    # preClose 保持原始（已知简化）
    assert (df['preClose'] == 9.9).all()


# ========== fixture 与真实表逐列一致 ==========

def test_synthetic_fixture_matches_real_schema_32_columns(minute_db):
    """合成 fixture 的表结构与真实 DDL 一致（32 列）【落实要求 ①】"""
    con = duckdb.connect(str(minute_db), read_only=True)
    cols = con.execute("DESCRIBE stock_minutes").fetchall()
    con.close()
    col_names = [c[0] for c in cols]
    # 真实表 32 列（见 writers.py DDL）
    expected = ['code', 'time', 'freq', 'open', 'high', 'low', 'close',
                'volume', 'amount', 'preClose', 'suspendFlag', 'settelementPrice', 'openInterest',
                'open_front', 'high_front', 'low_front', 'close_front',
                'open_back', 'high_back', 'low_back', 'close_back',
                'open_front_ratio', 'high_front_ratio', 'low_front_ratio', 'close_front_ratio',
                'open_back_ratio', 'high_back_ratio', 'low_back_ratio', 'close_back_ratio',
                'dividend_type', 'update_time']
    assert col_names == expected
    # time 是 BIGINT 毫秒戳
    time_type = [c[1] for c in cols if c[0] == 'time'][0]
    assert 'BIGINT' in time_type.upper()
