"""表集合缓存（_existing_tables / _tables_cache）的正式回归测试。

覆盖 2026-07-28 门 1 收敛整改后保留的 SHOW TABLES 优化：
1. 首次 _existing_tables() 查询后缓存；重复调用不再执行 SHOW TABLES。
2. 返回防御性 set 副本；调用方修改返回集合不污染内部缓存。
3. close() 将 _tables_cache 置 None；close/reopen 后能看到新增表。
4. 缺库、空集合、重新连接行为不变。

不依赖生产大库：使用自建 mini DuckDB 副本，确定性、可重复、不触碰 data/quantstudio.db。

BACKLOG（非生产代码，仅说明）：
provider-level get_history（query_bars_by_count_multi_table）日内行情缓存已于本轮移除，
原因：小市值与双均线真实策略 get_history 命中均为 0；PtradeAPI.get_history() 已有
_query_cache 层；synthetic 86× 不构成当前生产收益证据；4096 条目上限是条目数而非字节
内存上限。后续若实现需满足 byte-bounded LRU 与真实生产命中证据。本条不保留任何生产代码
或 synthetic-only 的正式契约测试。
"""
from __future__ import annotations

import duckdb as _duckdb

import pytest

from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess

_MAKE_TABLES = (
    "CREATE TABLE stock_daily (code VARCHAR, time BIGINT)",
    "CREATE TABLE etf_daily (code VARCHAR, time BIGINT)",
    "CREATE TABLE index_daily (code VARCHAR, time BIGINT)",
)


def _make_mini_db(path):
    """自建最小 DuckDB：stock_daily/etf_daily/index_daily 三张空表。"""
    con = _duckdb.connect(str(path))
    for sql in _MAKE_TABLES:
        con.execute(sql)
    con.close()


@pytest.fixture
def access(tmp_path):
    db = tmp_path / "mini.duckdb"
    _make_mini_db(db)
    a = DuckDBDataAccess(str(db))
    yield a
    a.close()


class _ConnProxy:
    """代理 DuckDB 连接，统计 SHOW TABLES 执行次数（conn.execute 为只读属性，故用代理）。"""

    def __init__(self, conn, counter):
        self._conn = conn
        self._counter = counter

    def execute(self, sql, *args, **kwargs):
        if "show tables" in str(sql).lower():
            self._counter["show_tables"] += 1
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def exec_counter(access, monkeypatch):
    """包装 DuckDBDataAccess._get_conn，返回统计 SHOW TABLES 的代理连接。"""
    counter = {"show_tables": 0}
    orig = access._get_conn

    def _wrap():
        conn = orig()
        if conn is None:
            return None
        return _ConnProxy(conn, counter)

    monkeypatch.setattr(access, "_get_conn", _wrap)
    return counter


# ===================== 表集合缓存 =====================

def test_repeated_existing_tables_calls_do_not_rerun_show_tables(access, exec_counter):
    """首次查询后缓存；重复调用不再执行 SHOW TABLES。"""
    s1 = access._existing_tables()
    assert exec_counter["show_tables"] == 1
    assert {"stock_daily", "etf_daily", "index_daily"} <= s1
    # 重复调用：不应再次执行 SHOW TABLES
    access._existing_tables()
    access._existing_tables()
    assert exec_counter["show_tables"] == 1, "重复调用不应再次执行 SHOW TABLES"


def test_existing_tables_return_is_copy(access):
    """修改 _existing_tables() 的返回集合不得污染内部缓存。"""
    s1 = access._existing_tables()
    s1.add("__injected__")
    s2 = access._existing_tables()
    assert "__injected__" not in s2
    assert "stock_daily" in s2


def test_close_reconnect_sees_new_table(access):
    """_existing_tables → close → 外部新增表 → 重连后能看到新表。"""
    before = access._existing_tables()
    assert "stock_daily" in before
    assert "zz_extra" not in before
    access.close()
    con = _duckdb.connect(str(access._db_path))  # 默认 read_write
    con.execute("CREATE TABLE zz_extra (id INTEGER)")
    con.close()
    after = access._existing_tables()
    assert "zz_extra" in after


def test_close_resets_cache_then_reconnect_idempotent(access):
    """close 后缓存失效；重新连接返回的集合与首次一致（行为不变）。"""
    first = access._existing_tables()
    access.close()
    second = access._existing_tables()
    assert second == first


def test_missing_db_returns_empty_set(tmp_path):
    """缺库（文件不存在）时 _existing_tables() 返回空集合且不抛错。"""
    a = DuckDBDataAccess(str(tmp_path / "nope.duckdb"))
    assert a._existing_tables() == set()
    a.close()


def test_empty_database_returns_empty_set(tmp_path):
    """真实存在但无表时返回空集合。"""
    db = tmp_path / "empty.duckdb"
    con = _duckdb.connect(str(db))
    con.close()
    a = DuckDBDataAccess(str(db))
    assert a._existing_tables() == set()
    a.close()
