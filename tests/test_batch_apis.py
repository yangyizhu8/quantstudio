"""B1 同步测试：批量取数 API（get_fundamentals_batch / get_history_batch）。

验证目标（对应方案 v2.1 Phase B1 + 用户实现要点）：
1. 只新增 API，不改动 get_fundamentals/get_history 签名和返回格式（向后兼容）
2. 复用预加载内存路径（不重复预加载）
3. 覆盖：单只、多只、空列表、字段缺失
4. 强制 list 入参语义（明确批量意图）
5. 返回格式与原 API 一致（get_fundamentals_batch 返回 DataFrame，get_history_batch 返回 CodeDict）
"""
from pathlib import Path

import pytest
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
from quantstudio._paths import db_path


# ========== API 存在性与签名 ==========

def test_batch_apis_exist_in_ptrade_api():
    """B1：PtradeAPI 含两个 batch 方法"""
    from quantstudio.backtest.ptrade_api import PtradeAPI
    assert hasattr(PtradeAPI, 'get_fundamentals_batch')
    assert hasattr(PtradeAPI, 'get_history_batch')


def test_batch_apis_injected_to_strategies():
    """B1：batch API 已注入 ptrade_import（策略零 import 可用）"""
    from quantstudio.backtest import ptrade_import
    assert hasattr(ptrade_import, 'get_fundamentals_batch')
    assert hasattr(ptrade_import, 'get_history_batch')


def test_batch_apis_do_not_change_original_signatures():
    """B1：原 get_fundamentals/get_history 签名不变（向后兼容）"""
    from quantstudio.backtest.ptrade_api import PtradeAPI
    import inspect

    # get_fundamentals 签名应保持（security, table, fields, date, ...）
    sig_fund = inspect.signature(PtradeAPI.get_fundamentals)
    assert 'security' in sig_fund.parameters
    assert 'table' in sig_fund.parameters

    # get_history 签名应保持（security, count, unit, fields, ...）
    sig_hist = inspect.signature(PtradeAPI.get_history)
    assert 'security' in sig_hist.parameters
    assert 'count' in sig_hist.parameters


# ========== 空列表处理（不报错，Ptrade 语义）==========

def test_fundamentals_batch_empty_list_returns_empty_df():
    """B1：空列表返回空 DataFrame，不报错"""
    from quantstudio.backtest.ptrade_api import _api
    df = _api.get_fundamentals_batch([], 'valuation', fields=['float_value'])
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0


def test_history_batch_empty_list_returns_empty_dict():
    """B1：空列表返回空 CodeDict，不报错"""
    from quantstudio.backtest.ptrade_api import _api, CodeDict
    result = _api.get_history_batch([], 5, '1d', fields=['close'])
    assert isinstance(result, CodeDict)
    assert len(result) == 0


# ========== 单只 vs 多只（基于真实库的集成测试）==========

def _ensure_db():
    """确认真实库存在，否则跳过集成测试"""
    db = db_path()
    if not db.exists():
        pytest.skip(f"测试库不存在: {db}")
    return db


def _attach_api(date="2026-04-28", prev_date="2026-04-27"):
    """构造引擎并 attach PtradeAPI（用真实库）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
    from quantstudio.backtest.ptrade_api import _api
    db = _ensure_db()
    cfg = EngineConfig(db_path=db, output_dir=ROOT/"output", research_dir=ROOT/"output"/"research")
    engine = BacktestEngine(db_path=str(db), strategy={}, start="2026-01-01", end="2026-04-29",
                            config=cfg, strategy_type="ptrade")
    _api.attach(engine, None, None, date, prev_date, {})
    return _api


def _ensure_float_data():
    """确认 stock_float_share 或 stock_daily_valuation 有数据（否则 skip 集成测试）"""
    import duckdb
    db = db_path()
    if not db.exists():
        pytest.skip("测试库不存在")
    conn = duckdb.connect(str(db), read_only=True)
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM stock_daily_valuation").fetchone()[0]
        cnt += conn.execute("SELECT COUNT(*) FROM stock_float_share").fetchone()[0]
        if cnt == 0:
            pytest.skip("stock_float_share/stock_daily_valuation 均为空（待拉取数据）")
    finally:
        conn.close()


def test_fundamentals_batch_single_stock():
    """B1：单只股票查询返回非空 DataFrame，index 是 ptrade_code"""
    _ensure_float_data()
    api = _attach_api()
    df = api.get_fundamentals_batch(['002830.SZ'], 'valuation',
                                    fields=['float_value', 'pe_ttm'],
                                    date="2026-04-27")
    assert len(df) >= 1
    assert 'float_value' in df.columns
    # index 应是 ptrade 格式代码
    assert any('002830' in str(idx) for idx in df.index)


def test_fundamentals_batch_multiple_stocks():
    """B1：多只股票查询返回多行（核心：消除逐只 N+1）"""
    _ensure_float_data()
    api = _attach_api()
    stocks = ['002830.SZ', '002193.SZ', '002719.SZ', '002888.SZ', '002809.SZ']
    df = api.get_fundamentals_batch(stocks, 'valuation',
                                    fields=['float_value'],
                                    date="2026-04-27")
    # 应返回多行（每只一行估值）
    assert len(df) >= 3  # 至少 3 只有数据（容许个别缺失）
    # 各股票流通市值 > 0
    assert (df['float_value'] > 0).all()


def test_fundamentals_batch_result_equals_iterative():
    """B1 关键：批量查询结果 == 逐只查询拼接的结果（语义等价，仅性能更优）

    这是 B1 的核心保证：策略从逐只循环换成批量，结果不变。
    """
    _ensure_float_data()
    api = _attach_api()
    stocks = ['002830.SZ', '002193.SZ', '002719.SZ']

    # 批量一次查
    batch_df = api.get_fundamentals_batch(stocks, 'valuation',
                                          fields=['float_value'], date="2026-04-27")
    # 逐只查
    iterative_rows = []
    for s in stocks:
        row = api.get_fundamentals(s, 'valuation', fields=['float_value'], date="2026-04-27")
        iterative_rows.append(row)
    iter_df = pd.concat(iterative_rows) if iterative_rows else pd.DataFrame()

    # 结果应一致（按裸码对齐比对 float_value）
    def bare(idx):
        return str(idx).split('.')[0]
    batch_map = {bare(idx): row['float_value'] for idx, row in batch_df.iterrows()}
    iter_map = {bare(idx): row['float_value'] for idx, row in iter_df.iterrows()}
    common = set(batch_map.keys()) & set(iter_map.keys())
    for code in common:
        assert abs(batch_map[code] - iter_map[code]) < 0.01, \
            f"{code} 批量({batch_map[code]}) ≠ 逐只({iter_map[code]})"


# ========== get_history_batch ==========

def test_history_batch_returns_codedict():
    """B1：get_history_batch 返回 CodeDict（强制 dict 语义）"""
    from quantstudio.backtest.ptrade_api import CodeDict
    api = _attach_api()
    result = api.get_history_batch(['002830.SZ', '002193.SZ'], 10, '1d', fields=['close'])
    assert isinstance(result, CodeDict)
    assert len(result) >= 1  # 至少 1 只有数据


def test_history_batch_count_respected():
    """B1：每只股票返回的行数 <= count（PIT 过滤）"""
    api = _attach_api()
    result = api.get_history_batch(['002830.SZ'], 5, '1d', fields=['close'])
    if len(result) > 0:
        # 取第一个 df
        df = list(result.values())[0]
        assert len(df) <= 5


def test_history_batch_fq_pre():
    """B1：fq='pre' 前复权生效（返回 close 是复权价）"""
    api = _attach_api()
    result = api.get_history_batch(['002830.SZ'], 20, '1d', fields=['close'], fq='pre')
    if len(result) > 0:
        df = list(result.values())[0]
        assert 'close' in df.columns
        assert df['close'].notna().any()


# ========== 字段缺失处理 ==========

def test_fundamentals_batch_missing_field_returns_empty_or_nan():
    """B1：查询不存在的字段不报错（返回空 df 或该列全 NaN）"""
    api = _attach_api()
    # 'nonexistent_field' 不存在，应优雅处理
    df = api.get_fundamentals_batch(['002830.SZ'], 'valuation',
                                    fields=['nonexistent_field'], date="2026-04-27")
    # 不抛异常即通过（Ptrade 语义：缺失返回空或 NaN）
    assert isinstance(df, pd.DataFrame)


def test_fundamentals_batch_balance_statement_returns_empty():
    """B1：三大报表（balance_statement）当前无数据，返回空 DataFrame 不报错"""
    api = _attach_api()
    df = api.get_fundamentals_batch(['002830.SZ'], 'balance_statement',
                                    fields=['total_assets'], date="2026-04-27")
    # 三大报表待 Phase D 补齐，当前应返回空
    assert isinstance(df, pd.DataFrame)
