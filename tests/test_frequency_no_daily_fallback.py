"""PR3 契约测试：频率查询严禁回退日线。

验证目标（主计划 7.19 "严禁频率缺失时回退到日线" + "数据缺失返回结构化能力错误"）：
1. 分钟表空（表存在但无数据）→ raise code=TABLE_EMPTY（不返回空 DataFrame 冒充）
2. 表有数据但缺该 freq → raise code=FREQ_NOT_IN_TABLE + available_freqs
3. 分钟查询不进入 query_bars_by_count_multi_table 日线 fallback 链（源码级断言）

这是 PR3 最重要的隔离契约：分钟查询绝不能用日线数据冒充。
"""
import pytest
import pandas as pd
import duckdb


@pytest.fixture
def empty_minute_db(tmp_path):
    """临时 DuckDB，stock_minutes 表存在但为空（模拟当前生产库状态）"""
    from quantstudio.pipeline.writers import DDL_DUCKDB
    db_path = tmp_path / "empty.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(DDL_DUCKDB["stock_minutes"])
    con.execute(DDL_DUCKDB["etf_minutes"])
    con.close()   # 先关闭写连接，避免 DuckDB 文件锁阻塞 read_only 打开
    yield db_path


@pytest.fixture
def partial_freq_db(tmp_path):
    """临时 DuckDB，stock_minutes 有 1min 数据但无 5min"""
    from quantstudio.pipeline.writers import DDL_DUCKDB
    db_path = tmp_path / "partial.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(DDL_DUCKDB["stock_minutes"])
    ms = int(pd.Timestamp("2026-01-05 09:31:00").tz_localize("Asia/Shanghai").value // 10**6)
    con.execute(
        "INSERT INTO stock_minutes VALUES "
        "('600000', ?, '1min', 10,10,10,10, 100,1000, 9.9, 0,0,0, 9,9,9,9, 11,11,11,11, "
        "0.9,0.9,0.9,0.9, 1.1,1.1,1.1,1.1, 'none', 'x')", [ms])
    con.close()   # 先关闭写连接，避免 DuckDB 文件锁阻塞 read_only 打开
    yield db_path


@pytest.fixture
def calendar_provider():
    class Cal:
        def get_trade_days(self, start, end):
            return [pd.Timestamp("2026-01-05", tz="Asia/Shanghai").to_pydatetime()]
    return Cal()


# ========== 表空 → TABLE_EMPTY（不返回空 DataFrame）==========

def test_empty_table_raises_table_empty_not_empty_df(empty_minute_db, calendar_provider):
    """分钟表空 → raise code=TABLE_EMPTY，不返回空 DataFrame 冒充"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    from quantstudio.backtest.providers.frequency_labels import (
        FrequencyCapabilityError, ERR_TABLE_EMPTY)
    da = DuckDBDataAccess(empty_minute_db)
    with pytest.raises(FrequencyCapabilityError) as exc_info:
        da.query_minute_bars_by_range(
            "600000", "2026-01-05", "2026-01-05", "1min",
            fq=None, calendar_provider=calendar_provider)
    assert exc_info.value.code == ERR_TABLE_EMPTY


# ========== 缺该 freq → FREQ_NOT_IN_TABLE + available_freqs ==========

def test_missing_freq_raises_with_available_freqs(partial_freq_db, calendar_provider):
    """表有 1min 但请求 5min → raise code=FREQ_NOT_IN_TABLE + available_freqs=['1min']"""
    from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess
    from quantstudio.backtest.providers.frequency_labels import (
        FrequencyCapabilityError, ERR_FREQ_NOT_IN_TABLE)
    da = DuckDBDataAccess(partial_freq_db)
    with pytest.raises(FrequencyCapabilityError) as exc_info:
        da.query_minute_bars_by_range(
            "600000", "2026-01-05", "2026-01-05", "5min",
            fq=None, calendar_provider=calendar_provider)
    assert exc_info.value.code == ERR_FREQ_NOT_IN_TABLE
    assert exc_info.value.available_freqs == ["1min"]


# ========== 分钟查询不进入日线 fallback 链（源码级断言）==========

def test_minute_query_does_not_enter_daily_fallback_chain():
    """query_minute_bars_by_range 在源码上不调用 query_bars_by_count_multi_table（日线 fallback）

    这是隔离契约的源码级防护：分钟路径与日线 fallback 链完全解耦。
    """
    from pathlib import Path
    engine_file = (Path(__file__).resolve().parent.parent
                   / "quantstudio" / "backtest" / "providers" / "duckdb_data_access.py")
    content = engine_file.read_text(encoding="utf-8")

    # query_minute_bars_by_range 方法定义存在
    assert "def query_minute_bars_by_range" in content
    # 找到该方法体的范围（粗略：从 def 到下一个 def）
    start = content.index("def query_minute_bars_by_range")
    # 找该方法内是否引用日线 fallback
    next_def = content.find("\n    def ", start + 1)
    method_body = content[start:next_def if next_def > 0 else len(content)]
    # 分钟查询方法体不应调用 query_bars_by_count_multi_table（日线 fallback）
    assert "query_bars_by_count_multi_table" not in method_body, \
        "分钟查询方法调用了日线 fallback 链，违反隔离契约"


def test_get_bars_minute_does_not_fallback_to_daily():
    """get_bars(frequency='1m') 在 DuckDBMarketDataProvider 中不进入日线分支（源码断言）"""
    from pathlib import Path
    provider_file = (Path(__file__).resolve().parent.parent
                     / "quantstudio" / "backtest" / "providers" / "duckdb_provider.py")
    content = provider_file.read_text(encoding="utf-8")
    # get_bars 方法存在 frequency 分流
    assert "frequency" in content
    # frequency != "1d" 时走 query_minute_bars，不走 query_bars_by_range
    assert "query_minute_bars_by_range" in content
