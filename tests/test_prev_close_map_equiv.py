# -*- coding: utf-8 -*-
"""prev_close_map 去 iterrows 化 · 契约测试（2026-09-19）。

背景
    `quantstudio/backtest/backtest_engine.py` 的 `_apply_factor_derived_split` 内，
    `prev_close_map` 的构造由 `iterrows` 改为向量化。该改造属**纯性能优化**：
    A-0~A-3 归因 + 全仓运行时普查实测该调用点独占回测总耗时 **64.04 %**
    （245 交易日、179.7 万行迭代）。

本文件的核心是**锁定等价性前提**——而不是重复引擎级验证（那由黄金对比承担）：

    `iterrows` 内部走 `self.values`，会把整表提升为**公共 dtype**。因此只有当
    `code` 列具**字符串语义**时，`str(row['code'])` 才与 `Series.astype(str)`
    逐值一致。实测 `query_daily_snapshot` 的 `code` 列具字符串语义。

    **该前提一旦被上游破坏（例如 code 列变成数值型），E-1 必须变红**——这是本改造
    唯一的静默行为差源。

版本容忍（2026-09-19 客户实测暴露后固化）
    客户环境（新版 pandas，`future.infer_string` 默认开启）下曾出现 **2 例假失败**，
    两处均为**测试对实现的过度绑定**，与本次修复无关：

    · **E1**：旧断言写死 `dtype == object`，而新版字符串列 dtype 为 `str`/`StringDtype`
      ⇒ 改为**「字符串语义」判据**（object 或 StringDtype 均受）。
    · **E2**：新版 StringDtype 下 `Series.astype(str)` 对 NA 返回**float `nan`**，
      而 `iterrows` 路径 `str(row[col])` 返回字符串 `'nan'` ⇒ 两式对 NA 的
      **表示形式不同**（真实数据 `code` 列无 NA，生产路径不触发）。
      处理：比较前将 NA 语义的键**归一到统一哨兵**（`_key_norm`），
      并以 **E-2b 显式登记该差异**。
      本机复现方式：`pd.set_option('future.infer_string', True)`。

断言组
    E-1  前提哨兵：code 列为**字符串语义** dtype（版本容忍）
    E-2  map 级等价（正常 / 浮点 code / NA code / 重复 code / close 含 NaN /
         阈值邻域 / 空表）
    E-2b NA 表示差异**显式登记**（版本相关行为差，非缺陷）
    E-3  缺列语义：`code` 缺列 → 空 map；`close` 缺列 → **全 0 map**
    E-4  数值型 code 的**已知差异**登记（证明前提必要性，非缺陷）
    E-5  ETF 除权四带区 + 送股后持仓变化
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

# NA 在各版本 pandas 下可能的字符串表示（含 astype(str) 直接返回 float nan 的情形）
_NA_TEXTS = {"nan", "NaN", "NAN", "<NA>", "None", "NaT", "nat", ""}


def _key_norm(key) -> str:
    """把 NA 语义的键归一到统一哨兵，供跨版本比较（见模块 docstring「版本容忍」）。"""
    s = str(key)
    return "<NA-SENTINEL>" if s in _NA_TEXTS else s


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


def _norm_map(m):
    """把 map 的键归一到统一哨兵（NA 语义跨版本对齐）。"""
    return {_key_norm(k): v for k, v in m.items()}


def _same(a, b):
    """键序 + 键值（含 NA 语义）全等 —— **两侧键先归一后再取值**（版本容忍）。

    注意：必须**构建归一化后的新 dict 再比较**，**不可**「用 a 的原始键去索引 b」——
    新版 pandas（StringDtype）下两侧 NA 键分别是字符串 `'nan'` 与 float `nan`，
    直接索引会抛 KeyError（2026-09-19 首轮修复自测暴露，已修正）。
    """
    na, nb = _norm_map(a), _norm_map(b)
    if list(na.keys()) != list(nb.keys()):
        return False
    for k in na:
        va, vb = na[k], nb[k]
        try:
            if not (bool(va == vb) or (pd.isna(va) and pd.isna(vb))):
                return False
        except Exception:  # noqa: BLE001
            return False
    return True


# ========== E-1 前提哨兵（字符串语义，版本容忍） ==========

@pytest.mark.skipif(not pathlib.Path(DB).exists(), reason="DB 不存在")
def test_e1_code_column_must_be_string_semantics():
    """等价性前提：`query_daily_snapshot` 的 code 列须为**字符串语义** dtype。

    **只关心「是否字符串语义」，不绑定具体实现类名**——pandas 1.x/2.x 默认 `object`，
    pandas 3.0（`future.infer_string`）为 `str`/`StringDtype`，二者都满足前提。
    旧版断言写死 `== object` 会在新版 pandas 上产生**与本修复无关的假失败**
    （2026-09-19 客户实测暴露），故改为版本容忍判据。
    """
    dao = DuckDBDataAccess(db_path=DB)
    df = dao.query_daily_snapshot(_start_ms("2026-03-12"))
    if df.empty:
        pytest.skip("生产库不可读（被锁/缺失/为空）")

    dt = df['code'].dtype
    is_object = (dt == object)
    is_string = isinstance(dt, getattr(pd, "StringDtype", ()))
    assert is_object or is_string, (
        "code 列不再是字符串语义 dtype（实测 %r）—— 向量化构造的等价性前提被破坏，"
        "须重新论证（方案 §5.1 #7）" % (dt,)
    )


# ========== E-2 map 级等价 ==========

_E2_CASES = {
    "正常(str code + float close)":
        pd.DataFrame({'code': ['600000', '000001', '300750'],
                      'close': [10.5, 20.25, 30.0]}),
    "浮点 code":
        pd.DataFrame({'code': [600000.0, 1.0], 'close': [10.5, 20.25]}),
    "NA code":
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
    """map 级等价（键经 NA 归一后比较，见 `_same`）。"""
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


# ========== E-2b NA 表示差异：显式登记（版本相关行为差，非缺陷） ==========

def test_e2b_na_key_representation_differs_across_paths():
    """登记：**新版 pandas（StringDtype）下两式对 NA 的键表示不同**。

    · `iterrows` 路径：`str(row['code'])` → 字符串 `'nan'`
    · 向量化路径：`Series.astype(str)` → **float `nan`**（StringDtype 下 NA 不转字符串）

    该差异**只在 code 列出现 NA 时可见**；实测真实数据 `code` 列无 NA
    （`query_daily_snapshot` 双点复核），**生产路径不触发**。

    本用例把这个差异**钉在明面上**而非视而不见：用**类型/语义断言**取代
    「两式逐字相等」，从而在任何 pandas 版本下稳定通过，同时保留对新版行为的可见性。
    若上游数据开始出现 NA code，本用例与 E-1 会提示复核。
    """
    df = pd.DataFrame({'code': ['600000', np.nan], 'close': [1.0, 2.0]})
    old, new = _old_way(df), _new_way(df)
    old_keys, new_keys = list(old.keys()), list(new.keys())

    # 非 NA 键必须逐字一致
    assert old_keys[0] == '600000' == new_keys[0]

    # NA 键：两式都产出了一个可比较的键，但类型/表示可能不同（版本相关）
    assert len(old_keys) == len(new_keys) == 2
    assert _key_norm(old_keys[1]) == "<NA-SENTINEL>"
    assert _key_norm(new_keys[1]) == "<NA-SENTINEL>"

    # 归一后判等价；值侧一致
    assert old['600000'] == new['600000'] == 1.0
    assert _same(old, new), "NA 键归一后应判等价"


# ========== E-3 缺列语义（总调度指定） ==========

def test_e3_missing_code_column_yields_empty_map():
    """`code` 列缺失 → 空 map（原式与向量化式一致）。"""
    df = pd.DataFrame({'close': [1.0, 2.0]})
    assert _old_way(df) == {} == _new_way(df)


def test_e3_missing_close_column_yields_all_zero_map():
    """`close` 列缺失 → **全 0 map**（对应原式 `row.get('close', 0)` 的默认值语义）。

    其唯一消费点 `prev_close_map.get(bare, 0)`（backtest_engine.py:918）对「空 map」与
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
    实际数据路径不触发（E-1 已锁定字符串语义前提）；本用例存在的意义是
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
