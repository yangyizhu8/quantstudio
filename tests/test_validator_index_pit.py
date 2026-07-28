"""
测试 validator 通用 index 修复 + PIT update_flag 去重（W1.8）。

精确行为断言：非连续 index、孔洞 index、reject mapping、update_flag 去重、PIT 多公告日。
"""
import json
import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from quantstudio.pipeline.validator import PreIngestValidator, ValidationResult


def _make_validator():
    rules_path = "config/alignment_rules.json"
    schemas = json.loads(Path(rules_path).read_text(encoding="utf-8"))["schemas"]
    return PreIngestValidator(schemas)


def test_non_contiguous_index_stock_daily():
    """index [5, 9] 不触发 IndexError"""
    validator = _make_validator()
    df = pd.DataFrame({
        "code": ["000001", "600000"],
        "time": [1700000000000, 1700000000000],
        "open": [10.0, 20.0], "high": [11.0, 21.0], "low": [9.0, 19.0],
        "close": [10.5, 20.5], "volume": [1000000, 2000000],
        "amount": [10500000, 41000000], "preClose": [10.0, 20.0],
        "suspendFlag": [0, 0], "dividend_type": ["none", "none"],
    }, index=[5, 9])
    result = validator.validate(df, "stock_daily", "batch_test", "xtquant")
    assert len(result.passed_df) > 0


def test_gappy_index_no_pre_reset():
    """df.drop(index=1) 产生 [0, 2] 孔洞 index，不在 validator 前 reset"""
    validator = _make_validator()
    df = pd.DataFrame({
        "code": ["000001", "600000", "000002"],
        "time": [1700000000000, 1700000000000, 1700000000000],
        "open": [10.0, 20.0, 30.0], "high": [11.0, 21.0, 31.0],
        "low": [9.0, 19.0, 29.0], "close": [10.5, 20.5, 30.5],
        "volume": [1000000, 2000000, 3000000],
        "amount": [10500000, 41000000, 91500000], "preClose": [10.0, 20.0, 30.0],
        "suspendFlag": [0, 0, 0], "dividend_type": ["none", "none", "none"],
    })
    df_filtered = df.drop(index=1)
    # 确认 index 是 [0, 2] 的孔洞
    assert list(df_filtered.index) == [0, 2]
    result = validator.validate(df_filtered, "stock_daily", "batch_test", "xtquant")
    assert len(result.passed_df) == 2


def test_reject_mapping_precise():
    """被拒行精确为 code='' 的那条"""
    validator = _make_validator()
    df = pd.DataFrame({
        "code": ["000001", ""],
        "time": [1700000000000, 1700000000000],
        "open": [10.0, 10.0], "high": [11.0, 11.0], "low": [9.0, 9.0],
        "close": [10.5, 10.5], "volume": [1000000, 1000000],
        "amount": [10500000, 10500000], "preClose": [10.0, 10.0],
        "suspendFlag": [0, 0], "dividend_type": ["none", "none"],
    }, index=[100, 200])
    result = validator.validate(df, "stock_daily", "batch_test", "xtquant")
    assert len(result.rejected_rows) == 1
    # 被拒的是 code="" 的那条
    assert result.rejected_rows[0]["code"] == ""


def test_duplicate_pk_update_flag_precise():
    """相同 code/end_date/ann_date，update_flag=0 和 1 → 只保留 flag=1"""
    validator = _make_validator()
    df = pd.DataFrame({
        "code": ["000001", "000001"],
        "ann_date": [1700000000000, 1700000000000],
        "end_date": [1690000000000, 1690000000000],
        "eps": [1.5, 1.6],
        "bps": [10.0, 10.5],
        "roe": [15.0, 16.0],
        "pe_ttm": [None, None],
        "pb": [None, None],
        "ps_ttm": [None, None],
        "np_yoy": [None, None],
        "update_flag": [0, 1],
    })
    result = validator.validate(df, "fin_indicator", "batch_test", "tushare")
    # 精确：passed_df 行数 = 1，update_flag = 1，eps = 修订版值
    assert len(result.passed_df) == 1
    row = result.passed_df.iloc[0]
    assert row["update_flag"] == 1
    assert row["eps"] == pytest.approx(1.6)
    assert result.fixed_count == 1


def test_fin_indicator_pit_multiple_ann_dates_precise():
    """三个不同 ann_date → 精确保留 3 条"""
    validator = _make_validator()
    df = pd.DataFrame({
        "code": ["000001", "000001", "000001"],
        "ann_date": [1700000000000, 1710000000000, 1720000000000],
        "end_date": [1690000000000, 1690000000000, 1690000000000],
        "eps": [1.5, 1.55, 1.6],
        "bps": [10.0, 10.2, 10.5],
        "roe": [15.0, 15.5, 16.0],
        "pe_ttm": [None, None, None],
        "pb": [None, None, None],
        "ps_ttm": [None, None, None],
        "np_yoy": [None, None, None],
        "update_flag": [0, 0, 1],
    })
    result = validator.validate(df, "fin_indicator", "batch_test", "tushare")
    # 精确保留 3 条
    assert len(result.passed_df) == 3
    # ann_date 集合与输入一致
    ann_set = set(result.passed_df["ann_date"].values)
    assert ann_set == {1700000000000, 1710000000000, 1720000000000}
