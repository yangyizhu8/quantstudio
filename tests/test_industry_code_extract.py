# -*- coding: utf-8 -*-
"""行业码提取器（`_extract_industry_codes`）回归测试 —— ISS-004 修复（2026-09-19）。

背景
    旧判据「``ast.walk`` 遍历**全文件全部字符串常量**，凡纯 6 位数字即收」无法区分
    「行业码」与「业务哨兵 / 无关代码表」，四策略直测实测三例误提取：

    · ``断板反包策略``  → ``_QS_BSE_LEGACY``（n=248 北交所映射快照，**另一语义**）被整表收走；
    · ``连板梯队龙头打板套利策略`` → 业务哨兵 ``'999999'`` 被收走；
    · ``F-Score选股RSRS择时`` → 恰好正确（源码内只有那 4 个 6 位数字）——**属侥幸**。

    修复为「**两路锚定 + 三面排除**」（路 A 结构位 / 路 B 命名常量；
    排除 docstring·注释 / 哨兵·魔法值 / 已知无关集合），且**保守偏空**。

测试组
    I-1 真实策略回归（四策略直测，含 200 码案例与哨兵案例）
    I-2 锚定判据命中（路 A / 路 B）
    I-3 排除面（docstring / 哨兵 / BSE 类无关集合）
    I-4 保守偏空（无声明位 → 空集，不回落旧的全量扫描）
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from quantstudio.strategy_compiler.source_import import (  # noqa: E402
    _extract_industry_codes)

ROOT = pathlib.Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "quantstudio" / "backtest" / "strategies"

FSCORE_EXPECT = ('801780', '801790', '480000', '490000')


def _extract_strategy_file(name: str):
    p = STRAT_DIR / name
    if not p.exists():
        pytest.skip("策略文件缺失：%s" % name)
    return _extract_industry_codes(p.read_text(encoding="utf-8-sig"))


# ========== I-1 真实策略回归 ==========

def test_i1_fscore_unchanged_zero_delta_baseline():
    """**零变更硬门**：F-Score 提取结果必须与修复前一致（现 12 版产物即此值）。

    该策略是「修复前恰好正确」的一例——修复对它必须是**零变更**，
    故其 4 码即为等价性基准。
    """
    got = _extract_strategy_file("F-Score选股RSRS择时.py")
    assert got == FSCORE_EXPECT, "F-Score 提取结果变化（零变更硬门失败）：%s" % (got,)


def test_i1_duanbanbafan_no_bse_codes():
    """**200 码案例回归**：断板反包策略不得再收走 `_QS_BSE_LEGACY` 的北交所代码。

    修复前实测 ≈200 个 `43xxxx`/`83xxxx` 码被误收 —— 若烘焙进产物，PTrade 端会
    逐个查行业（池查询灾难）。
    """
    got = _extract_strategy_file("断板反包策略.py")
    bse = [c for c in got if c.startswith(('43', '83', '87', '92'))]
    assert not bse, "仍误收北交所映射码：%d 个，样例 %s" % (len(bse), bse[:5])
    assert len(got) < 20, "提取码数异常偏多（疑仍为全量扫描）：%d 个" % len(got)


def test_i1_lianban_no_sentinel():
    """**哨兵案例回归**：连板梯队策略不得再收走业务哨兵 `'999999'`。"""
    got = _extract_strategy_file("连板梯队龙头打板套利策略.py")
    assert '999999' not in got, "仍误收哨兵 999999：%s" % (got,)


def test_i1_dual_ma_empty():
    """无行业声明位的策略 → 空集（双均线策略）。"""
    assert _extract_strategy_file("双均线策略.py") == ()


# ========== I-2 锚定判据命中 ==========

def test_i2_route_a_compare_in_literal():
    """路 A：`<行业语义变量> in (字面量元组)` 命中。"""
    src = "def f(ic):\n    return ic in ('801780', '801790', '480000', '490000')\n"
    assert _extract_industry_codes(src) == FSCORE_EXPECT


def test_i2_route_b_named_constant():
    """路 B：`INDUSTRY_EXCLUDE = {...}` 命名常量命中。"""
    src = "INDUSTRY_EXCLUDE = {'801780', '801790'}\n"
    assert _extract_industry_codes(src) == ('801780', '801790')


def test_i2_route_b_qs_named_constant():
    """路 B：`_QS_INDUSTRY_CODES = (...)` 形态亦命中。"""
    src = "_QS_INDUSTRY_CODES = ('801780',)\n"
    assert _extract_industry_codes(src) == ('801780',)


def test_i2_preserves_order_and_dedups():
    """保序去重（模板内 `[0]` 下标与 `','.join` 依赖顺序稳定）。"""
    src = "INDUSTRY_EXCLUDE = ('801790', '801780', '801790')\n"
    assert _extract_industry_codes(src) == ('801790', '801780')


# ========== I-3 排除面 ==========

def test_i3_docstring_ignored():
    """E1：docstring 内的 6 位数字**不得**被提取（结构上排除，非补丁过滤）。"""
    src = '"""说明：行业码示例 801780 / 480000，探针实证 2026-09-01。"""\n'
    assert _extract_industry_codes(src) == ()


def test_i3_sentinels_excluded():
    """E2：哨兵/魔法值排除（999999 / 000000）。"""
    src = "INDUSTRY_EXCLUDE = ('801780', '999999', '000000')\n"
    assert _extract_industry_codes(src) == ('801780',)


def test_i3_bse_legacy_name_excluded():
    """E3：已知无关集合常量名（BSE 映射）排除。"""
    src = "_QS_BSE_LEGACY = {'430017', '830799', '870199'}\n"
    assert _extract_industry_codes(src) == ()


def test_i3_unrelated_named_set_ignored():
    """无关命名集合（非行业声明位）不得被提取。"""
    src = "STOCK_POOL = ('600000', '000001', '300750')\n"
    assert _extract_industry_codes(src) == ()


def test_i3_unrelated_compare_ignored():
    """普通股池判定（左值非行业语义）不得被提取。"""
    src = "def f(code):\n    return code in ('600000', '000001')\n"
    assert _extract_industry_codes(src) == ()


# ========== I-4 保守偏空 ==========

def test_i4_no_declaration_returns_empty():
    """无任何声明位 → 空集（**不回落**旧的全量扫描）。"""
    src = "import pandas as pd\nX = '801780'\nY = ['480000']\n"
    assert _extract_industry_codes(src) == ()


def test_i4_syntax_error_returns_empty():
    """源码语法错误 → 空集（保持既有容错语义）。"""
    assert _extract_industry_codes("def broken(:\n    pass") == ()


def test_i4_returns_tuple_type():
    """返回类型恒为 tuple（模板渲染依赖：`[0]` 下标 + `','.join` 保序）。"""
    assert isinstance(_extract_industry_codes("X = 1\n"), tuple)
    assert isinstance(_extract_industry_codes("INDUSTRY_EXCLUDE = ('801780',)\n"), tuple)
