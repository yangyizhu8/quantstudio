# -*- coding: utf-8 -*-
"""PR7 预加载路径等价性测试：内存缓存路径 vs 原 SQL 路径逐位相等。

铁律：性能优化不得改变 query_bars_by_count_batch 的返回值/列/行/排序/dtype/空值。
用同一 DB 两个实例（_use_sql_path True/False）对相同输入逐位对比。
"""
import pandas as pd
import pytest

from quantstudio.backtest.providers.duckdb_data_access import DuckDBDataAccess


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    """迷你三表 DB：stock/etf/index 各若干只，含 qfq front 列与缺失分支。"""
    import duckdb
    p = tmp_path_factory.mktemp("pr7") / "test.db"
    conn = duckdb.connect(str(p))
    conn.execute("""CREATE TABLE stock_daily (
        code VARCHAR, time BIGINT, open DOUBLE, high DOUBLE, low DOUBLE,
        close DOUBLE, volume DOUBLE, amount DOUBLE, pctChg DOUBLE,
        preClose DOUBLE, turn DOUBLE, peTTM DOUBLE, pbMRQ DOUBLE,
        open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE)""")
    conn.execute("""CREATE TABLE etf_daily (
        code VARCHAR, time BIGINT, open DOUBLE, high DOUBLE, low DOUBLE,
        close DOUBLE, volume DOUBLE, amount DOUBLE, pctChg DOUBLE,
        preClose DOUBLE, turn DOUBLE, peTTM DOUBLE, pbMRQ DOUBLE,
        open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE)""")
    conn.execute("""CREATE TABLE index_daily (
        code VARCHAR, time BIGINT, open DOUBLE, high DOUBLE, low DOUBLE,
        close DOUBLE, volume DOUBLE, amount DOUBLE, pctChg DOUBLE,
        preClose DOUBLE, turn DOUBLE, peTTM DOUBLE, pbMRQ DOUBLE,
        open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE)""")
    ms = lambda d: int(pd.Timestamp(d).value // 10**6)
    # stock: 600000 30 根（qfq 全有）；600888 20 根（qfq 前 10 根 NULL → 守卫边界）
    rows = []
    for i, day in enumerate(pd.date_range('2024-01-02', periods=30, freq='D')):
        t = ms(day)
        rows.append(('600000', t, 10.0, 11.0, 9.5, 10.5 + i * 0.1, 1e6, 1e7,
                     1.0, 10.0, 1.0, 10.0, 1.0, 10.0 + i * 0.1, 11.0 + i * 0.1,
                     9.5 + i * 0.1, 10.5 + i * 0.1))
    for i, day in enumerate(pd.date_range('2024-01-02', periods=20, freq='D')):
        t = ms(day)
        qfq = (None, None, None, None) if i < 10 else (20.0 + i * 0.1,) * 4
        rows.append(('600888', t, 20.0, 21.0, 19.5, 20.5 + i * 0.1, 2e6, 2e7,
                     1.0, 20.0, 1.0, 20.0, 1.0, *qfq))
    conn.executemany("INSERT INTO stock_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    # etf: 510300 25 根（qfq 全有）
    for i, day in enumerate(pd.date_range('2024-01-02', periods=25, freq='D')):
        t = ms(day)
        conn.execute("INSERT INTO etf_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ('510300', t, 3.0, 3.1, 2.9, 3.0 + i * 0.01, 1e7, 1e8,
                      1.0, 3.0, 1.0, None, None, 3.0 + i * 0.01, 3.1 + i * 0.01,
                      2.9 + i * 0.01, 3.0 + i * 0.01))
    # index: 000300 22 根（无 qfq/peTTM/pbMRQ——NULL 列）
    for i, day in enumerate(pd.date_range('2024-01-02', periods=22, freq='D')):
        t = ms(day)
        conn.execute("INSERT INTO index_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ('000300', t, 3000.0, 3010.0, 2990.0, 3000.0 + i, 1e8, 1e9,
                      1.0, None, None, None, None, None, None, None, None))
    conn.close()
    return p


def _make_pair(db_path):
    sql = DuckDBDataAccess(db_path)
    sql._use_sql_path = True
    mem = DuckDBDataAccess(db_path)
    return sql, mem


def _assert_equal_dict(sql_res, mem_res):
    assert set(sql_res.keys()) == set(mem_res.keys()), (
        f"key 集合不一致: sql={sorted(sql_res)[:5]} mem={sorted(mem_res)[:5]}")
    for code in sql_res:
        pd.testing.assert_frame_equal(
            sql_res[code], mem_res[code], check_exact=True,
            obj=f"code={code} 两路径 DataFrame 不一致")


@pytest.mark.parametrize("use_qfq", [False, True])
def test_stock_etf_index_equivalent(db_path, use_qfq):
    import pandas as pd
    ms = lambda d: int(pd.Timestamp(d).value // 10**6)
    sql, mem = _make_pair(db_path)
    codes = ['600000', '600888', '510300', '000300']
    before = ms('2024-02-05 15:00:00')
    # 先各跑一遍（各自独立缓存/连接）
    r_sql = sql.query_bars_by_count_batch(codes, 15, before, use_qfq=use_qfq)
    r_mem = mem.query_bars_by_count_batch(codes, 15, before, use_qfq=use_qfq)
    _assert_equal_dict(r_sql, r_mem)
    # 每只行数正确（time<=before 最近 15 根；600888 只有 20 根 → 20 根内取 15）
    assert len(r_mem['600000']) == 15
    assert len(r_mem['600888']) == 15
    assert len(r_mem['510300']) == 15
    assert len(r_mem['000300']) == 15


def test_count_variation_uses_same_cache(db_path):
    import pandas as pd
    ms = lambda d: int(pd.Timestamp(d).value // 10**6)
    sql, mem = _make_pair(db_path)
    codes = ['600000', '510300']
    before = ms('2024-02-05 15:00:00')
    r_sql5 = sql.query_bars_by_count_batch(codes, 5, before, use_qfq=False)
    r_mem5 = mem.query_bars_by_count_batch(codes, 5, before, use_qfq=False)
    _assert_equal_dict(r_sql5, r_mem5)
    # 同一实例换 count → 缓存复用，结果仍与 SQL 版一致
    r_sql3 = sql.query_bars_by_count_batch(codes, 3, before, use_qfq=False)
    r_mem3 = mem.query_bars_by_count_batch(codes, 3, before, use_qfq=False)
    _assert_equal_dict(r_sql3, r_mem3)
    assert len(r_mem3['600000']) == 3


def test_incremental_new_code(db_path):
    import pandas as pd
    ms = lambda d: int(pd.Timestamp(d).value // 10**6)
    sql, mem = _make_pair(db_path)
    before = ms('2024-02-05 15:00:00')
    r1 = mem.query_bars_by_count_batch(['600000'], 10, before, use_qfq=False)
    # 新 code 增量加载（600888 不在缓存）——结果与 SQL 版一致
    r2_sql = sql.query_bars_by_count_batch(['600000', '600888'], 10, before, use_qfq=False)
    r2_mem = mem.query_bars_by_count_batch(['600000', '600888'], 10, before, use_qfq=False)
    _assert_equal_dict(r2_sql, r2_mem)
    assert len(r1) == 1


def test_empty_and_missing_codes(db_path):
    import pandas as pd
    ms = lambda d: int(pd.Timestamp(d).value // 10**6)
    sql, mem = _make_pair(db_path)
    before = ms('2024-02-05 15:00:00')
    # 空列表 → {}
    assert sql.query_bars_by_count_batch([], 10, before, use_qfq=False) == {}
    assert mem.query_bars_by_count_batch([], 10, before, use_qfq=False) == {}
    # 不存在的 code → 都不出现
    r_sql = sql.query_bars_by_count_batch(['999999'], 10, before, use_qfq=False)
    r_mem = mem.query_bars_by_count_batch(['999999'], 10, before, use_qfq=False)
    assert r_sql == {} and r_mem == {}


def test_before_ms_cutoff_equivalence(db_path):
    """cutoff 提前（数据不足 count）时两路径一致；行序为 time 升序。"""
    import pandas as pd
    ms = lambda d: int(pd.Timestamp(d).value // 10**6)
    sql, mem = _make_pair(db_path)
    codes = ['600000', '510300']
    early = ms('2024-01-10 15:00:00')  # 只覆盖前 7 个交易日
    r_sql = sql.query_bars_by_count_batch(codes, 70, early, use_qfq=True)
    r_mem = mem.query_bars_by_count_batch(codes, 70, early, use_qfq=True)
    _assert_equal_dict(r_sql, r_mem)
    # 测试数据是日历日：01-02~01-10 共 9 天（含周末）
    assert len(r_mem['600000']) == 9
    # time 升序断言
    times = r_mem['600000']['time'].tolist()
    assert times == sorted(times)


def test_index_etf_map_fallback(db_path):
    """指数代码 000300 在 index_daily 有数据时不需要 fallback；构造缺 index 数据的
    指数代码（000905 不在任何表）走 INDEX_ETF_MAP 代理（510500 也不存在→无结果）。"""
    import pandas as pd
    ms = lambda d: int(pd.Timestamp(d).value // 10**6)
    sql, mem = _make_pair(db_path)
    before = ms('2024-02-05 15:00:00')
    r_sql = sql.query_bars_by_count_batch(['000905'], 10, before, use_qfq=False)
    r_mem = mem.query_bars_by_count_batch(['000905'], 10, before, use_qfq=False)
    assert r_sql == {} and r_mem == {}


def test_trade_date_map_equivalence():
    """trade_date 唯一值 map 广播 vs 逐行 strftime 逐位等价（Step 1，内联旧实现对比）。"""
    import pandas as pd
    import numpy as np
    # 构造测试数据（多 code、多日、跨月边界）
    times = [1735660800000, 1735747200000, 1735833600000, 1735920000000]  # 跨年（2025-01-01 起）
    codes = ['159915', '510300', '510500']
    rows = [(c, t) for c in codes for t in times] * 3  # 重复模拟多根 bar
    df = pd.DataFrame(rows, columns=['code', 'time'])
    df['close'] = np.random.rand(len(df))

    # 旧实现（逐行 strftime）
    df_old = df.copy()
    df_old["trade_date"] = pd.to_datetime(df_old["time"], unit="ms", utc=True).dt.tz_convert(
        "Asia/Shanghai").dt.strftime("%Y-%m-%d")

    # 新实现（唯一值 map 广播，经 _post 的 _build_trade_date_map）
    from quantstudio.backtest.providers.duckdb_data_access import _build_trade_date_map
    df_new = df.copy()
    df_new["trade_date"] = _build_trade_date_map(df_new["time"])

    pd.testing.assert_series_equal(df_old["trade_date"], df_new["trade_date"], check_names=True)
    # 值形态抽查
    assert df_new["trade_date"].iloc[0] == '2025-01-01'
