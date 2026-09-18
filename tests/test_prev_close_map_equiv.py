# -*- coding: utf-8 -*-
"""prev_close_map 去 iterrows 化 · 契约测试（2026-09-19）。

背景
    `quantstudio/backtest/backtest_engine.py` 的 `_apply_factor_derived_split` 内，
    `prev_close_map` 的构造由 `iterrows` 改为向量化。该改造属**纯性能优化**：
    A-0~A-3 归因 + 全仓运行时普查实测该调用点独占回测总耗时 **64.04 %**
    （245 交易日、179.7 万行迭代）。

本文件的核心是**锁定等价性前提**——而不是重复引擎级验证（那由 T5 黄金对比承担）：

    `iterrows` 内部走 `self.values`，会把整表提升为**公共 dtype**（int64 与
    float64 混排 → 全升 float64）。因此只有当 `code` 列 dtype 为 **object** 时，
    `str(row['code'])` 才与 `Series.astype(str)` 逐值一致。
    实测 `query_daily_snapshot` 的 `code` 列恒为 object，`df.values.dtype` 亦为 object。

    **该前提一旦被上游破坏（例如 code 列变成数值型），E-1 必须变红**——这是本改造
    唯一的静默行为差源，也是总调度指定的必答项（方案 §5.1 #7）。

断言组
    E-1 真库 dtype 前提（code 列 object / df.values.dtype object）
    E-2 map 级等价（正常 / 浮点 code / NaN code / 重复 code / close 含 NaN /
        阈值邻域 / 空表）
    E-3 缺列语义（总调度指定）：`code` 缺列 → 空 map；`close` 缺列 → **全 0 map**
    E-4 数值型 code 的**已知差异**登记（证明前提必要性，非缺陷）
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quantstudio.backtest.providers.duckdb_data_access import (  # noqa: E402
    DuckDBDataAccess)
from quantstudio.backtest.providers.duckdb_provider import _start_ms  # noqa: E402

DB = str(pathlib.Path(__file__).resolve().parents[1] / "data" / "quantstudio.db")


def _old_way(prev_data):
    """改造前的实现（backtest_engine.py 原 905-908 行）。"""
    m = {}
    if 'code' in prev_data.columns:
        for _, row in prev_data.iterrows():
            m[str(row['code'])] = row.get('close', 0)
    return m


def _new_way(prev_data):
    """改造后的实现（backtest_engine.py 现行）。"""
    m = {}
    if 'code' in prev_data.columns:
        codes = prev_data['code'].astype(str)
        if 'close' in prev_data.columns:
            m = dict(zip(codes, prev_data['close']))
        else:
            m = dict.fromkeys(codes, 0)
    return m


def _same(a, b):
    """键序 + 键值（含 NaN）全等。"""
    if list(a.keys()) != list(b.keys()):
        return False
    for k in a:
        va, vb = a[k], b[k]
        try:
            if not (bool(va == vb) or (pd.isna(va) and pd.isna(vb))):
                return False
        except Exception:  # noqa: BLE001
            return False
    return True


# ========== E-1 真库 dtype 前提 ==========

@pytest.mark.skipif(not pathlib.Path(DB).exists(), reason="DB 不存在")
def test_e1_code_column_must_be_object():
    """等价性前提：query_daily_snapshot 的 code 列与 df.values 必须同为 object。

    该断言是本改造的唯一静默差源哨兵——上游若把 code 列改为数值型，本测试必须失败。
    """
    dao = DuckDBDataAccess(db_path=DB)
    df = dao.query_daily_snapshot(_start_ms("2026-03-12"))
    if df.empty:
        pytest.skip("生产库不可读（被锁/缺失）")
    assert df['code'].dtype == object, \
        "code 列不再是 object —— 向量化构造的等价性前提被破坏，须重新论证（方案 §5.1 #7）"
    assert df.values.dtype == object, \
        "df.values 不再是 object —— iterrows 会提升公共 dtype，等价性前提被破坏"


# ========== E-2 map 级等价 ==========

_E2_CASES = {
    "正常(str code + float close)":
        pd.DataFrame({'code': ['600000', '000001', '300750'],
                      'close': [10.5, 20.25, 30.0]}),
    "浮点 code":
        pd.DataFrame({'code': [600000.0, 1.0], 'close': [10.5, 20.25]}),
    "NaN code":
        pd.DataFrame({'code': ['600000', np.nan, '300750'],
                      'close': [10.5, 20.25, 30.0]}),
    "重复 code(后者覆盖, 键序取首次位置)":
        pd.DataFrame({'code': ['600000', '000001', '600000'],
                      'close': [10.0, 20.0, 11.0]}),
    "close 含 NaN":
        pd.DataFrame({'code': ['600000', '000001'], 'close': [np.nan, 20.0]}),
    "空表(有列无行)":
        pd.DataFrame({'code': pd.Series([], dtype=object),
                      'close': pd.Series([], dtype=float)}),
}


@pytest.mark.parametrize("name", sorted(_E2_CASES))
def test_e2_map_level_equivalence(name):
    df = _E2_CASES[name]
    assert _same(_old_way(df), _new_way(df)), "%s：两式 map 不等价" % name


def test_e2_threshold_neighborhood():
    """带区阈值邻域（ratio 0.99 / 1.01 / 1.10 ±eps）——prev_close 的取值精度敏感区。"""
    pre = 10.0
    closes = [0.9899 * pre, 0.9901 * pre, 1.0099 * pre,
              1.0101 * pre, 1.0999 * pre, 1.1001 * pre]
    df = pd.DataFrame({'code': ['c%d' % i for i in range(len(closes))],
                       'close': closes})
    assert _same(_old_way(df), _new_way(df))


# ========== E-3 缺列语义（总调度指定） ==========

def test_e3_missing_code_column_yields_empty_map():
    """`code` 列缺失 → 空 map（原式与向量化式一致）。"""
    df = pd.DataFrame({'close': [1.0, 2.0]})
    assert _old_way(df) == {} == _new_way(df)


def test_e3_missing_close_column_yields_all_zero_map():
    """`close` 列缺失 → **全 0 map**（对应原式 `row.get('close', 0)` 的默认值语义）。

    总调度亲证：其唯一消费点 `prev_close_map.get(bare, 0)`（:918）对「空 map」与
    「全 0 map」逐位等价；本实现选择**构造全 0 map**，取实现级全等而非仅消费端等价。
    """
    df = pd.DataFrame({'code': ['600000', '000001']})
    a, b = _old_way(df), _new_way(df)
    assert a == {'600000': 0, '000001': 0}
    assert b == {'600000': 0, '000001': 0}
    assert _same(a, b)


def test_e3_missing_close_consumption_equivalent():
    """补充：即使退化为空 map，消费端 `.get(bare, 0)` 亦逐位等价（双重保险）。"""
    for m in ({}, {'600000': 0, '000001': 0}):
        assert m.get('600000', 0) == 0
        assert m.get('999999', 0) == 0


# ========== E-4 数值型 code 的已知差异登记 ==========

def test_e4_numeric_code_known_divergence():
    """数值型 code 下两式**确有差异**——登记为已知事实，而非缺陷。

    iterrows 走 df.values 会把 int64 提升为 float64 → `str()` 得 '600000.0'；
    而 astype(str) 作用在原生 int64 列上得 '600000'。
    实际数据路径不触发（E-1 已锁定 code 列为 object）；本用例存在的意义是
    **证明 E-1 前提的必要性**：一旦前提失守，等价性立即不成立。
    """
    df = pd.DataFrame({'code': [600000], 'close': [10.0]})
    old, new = _old_way(df), _new_way(df)
    assert list(old.keys()) == ['600000.0'], "iterrows 路径应将 int64 提升为 float 文本"
    assert list(new.keys()) == ['600000'], "向量化路径应保留 int64 文本"
    assert not _same(old, new), "两式在数值型 code 下不应等价（已知差异）"


# ========== E-5 ETF 除权带区行为（端到端样本未覆盖的路径） ==========

def _etf_engine():
    """构造最小引擎实例，挂一个 ETF 持仓。"""
    from quantstudio.backtest.backtest_engine import BacktestEngine, Position
    eng = BacktestEngine(db_path="unused.db", strategy={},
                         start="2026-01-02", end="2026-01-02")
    eng.account.positions = {
        '510300.SS': Position(code='510300.SS', volume=10000,
                              avg_cost=1.0, can_sell=10000),
    }
    eng.result.corporate_actions = []
    return eng


@pytest.mark.parametrize("prev_close,pre_close,expect", [
    (2.00, 1.00, 'factor_derived_split'),   # ratio=2.00 ≥ 1.10 送股/份额折算
    (1.05, 1.00, None),                     # 1.01 < ratio < 1.10 现金分红带 → 跳过 + WARN
    (1.00, 1.00, None),                     # 0.99~1.01 非除权 → 跳过
    (0.50, 1.00, 'factor_derived_merge'),   # ratio=0.50 < 0.99 份额合并
])
def test_e5_etf_factor_derived_bands(prev_close, pre_close, expect):
    """ETF 除权四带区行为 —— 覆盖端到端样本未触发的 `_apply_factor_derived_split`。

    本改造只替换 `prev_close_map` 的**构造方式**，故带区判定必须逐带区不变。
    """
    eng = _etf_engine()
    prev_data = pd.DataFrame({'code': ['510300'], 'close': [prev_close]})
    curr_data = pd.DataFrame({'code': ['510300'], 'close': [prev_close],
                              'preClose': [pre_close]})
    eng._apply_factor_derived_split(curr_data, prev_data, '2026-01-05')
    types = [a.get('type') for a in eng.result.corporate_actions]
    if expect is None:
        assert types == [], "该带区不应产生公司行为，实际 %s" % types
    else:
        assert expect in types, "应产生 %s，实际 %s" % (expect, types)


def test_e5_etf_split_updates_position():
    """送股带区：持仓股数按吸附后的 ratio 调整（行为侧断言，非仅记录）。"""
    eng = _etf_engine()
    before = eng.account.positions['510300.SS'].volume
    prev_data = pd.DataFrame({'code': ['510300'], 'close': [2.0]})
    curr_data = pd.DataFrame({'code': ['510300'], 'close': [2.0],
                              'preClose': [1.0]})
    eng._apply_factor_derived_split(curr_data, prev_data, '2026-01-05')
    after = eng.account.positions['510300.SS'].volume
    assert after > before, "ratio=2.0 应送股使持仓翻倍，实际 %d → %d" % (before, after)
