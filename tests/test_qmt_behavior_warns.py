# -*- coding: utf-8 -*-
"""QMT 行为差异 WARN 层测试（DEAL-07 §4.1，2026-10-24）。

覆盖契约：portability_rules.scan_behavior_warns / BEHAVIOR_WARN_RULES ——
纯 WARN（零 BLOCK）。防恒绿设计：8 条规则各有 1 个「应命中」＋1 个「应不命中」
用例成对出现（命中侧证明检测有效、不命中侧证明非恒真）。
"""
import ast

from quantstudio.strategy_compiler.portability_rules import (
    BEHAVIOR_WARN_RULES,
    scan_behavior_warns,
)


def _ids(source: str, backtest_range=None) -> list[str]:
    tree = ast.parse(source)
    return [w["rule_id"] for w in scan_behavior_warns(tree, source, backtest_range)]


# ---------------------------------------------------------------------------
# 元契约：表结构 / 输出形态
# ---------------------------------------------------------------------------

def test_behavior_warn_rules_table_shape():
    assert len(BEHAVIOR_WARN_RULES) == 8
    rule_ids = [r[0] for r in BEHAVIOR_WARN_RULES]
    assert len(set(rule_ids)) == 8                     # 唯一
    for rule_id, trigger, advice in BEHAVIOR_WARN_RULES:
        assert rule_id.startswith("QMT-") and "-" in rule_id[4:]
        assert isinstance(trigger, str) and trigger
        assert isinstance(advice, str) and advice


def test_scan_output_shape_and_warn_only():
    src = (
        "def initialize(context):\n"
        "    set_commission(commission_ratio=0.00035, stamp_tax=0.001)\n"
        "    pos = get_position('510300.SH')\n"
        "    v = pos.today_long_amount\n"
    )
    warns = scan_behavior_warns(ast.parse(src), src)
    assert warns, "应至少命中一条 WARN"
    for w in warns:
        assert set(w) == {"rule_id", "line", "message", "grade"}
        assert w["grade"] == "WARN"                    # 零 BLOCK
        assert isinstance(w["line"], int)
        assert "双端差异" in w["message"] and "建议" in w["message"]


# ---------------------------------------------------------------------------
# 规则 1：QMT-POS-FIELD-UNIMPL（POS 7 缺口字段）
# ---------------------------------------------------------------------------

def test_pos_field_unimpl_hit():
    src = "v = pos.today_long_amount\n"
    assert "QMT-POS-FIELD-UNIMPL" in _ids(src)


def test_pos_field_unimpl_no_hit():
    src = "v = pos.amount\nw = pos.cost_basis\nx = pos.enable_amount\n"
    assert "QMT-POS-FIELD-UNIMPL" not in _ids(src)


# ---------------------------------------------------------------------------
# 规则 2：QMT-FEE-COMMISSION-TRI（set_commission → 三项同值映射）
# ---------------------------------------------------------------------------

def test_fee_commission_tri_hit():
    src = "set_commission(commission_ratio=0.00035, min_commission=5.0)\n"
    assert "QMT-FEE-COMMISSION-TRI" in _ids(src)


def test_fee_commission_tri_no_hit():
    src = "x = 1\ny = order_target_value('510300.SH', 10000)\n"
    ids = _ids(src)
    assert "QMT-FEE-COMMISSION-TRI" not in ids


# ---------------------------------------------------------------------------
# 规则 3：QMT-FEE-STAMP-BUY（set_commission 语义含买入印花）
# ---------------------------------------------------------------------------

def test_fee_stamp_buy_hit():
    src = "set_commission(commission_ratio=0.00035, stamp_tax=0.001)\n"
    ids = _ids(src)
    assert "QMT-FEE-STAMP-BUY" in ids
    assert "QMT-FEE-COMMISSION-TRI" in ids            # 同一调用仍触发规则 2


def test_fee_stamp_buy_no_hit():
    src = "set_commission(commission_ratio=0.00035, min_commission=5.0)\n"
    ids = _ids(src)
    assert "QMT-FEE-STAMP-BUY" not in ids
    assert "QMT-FEE-COMMISSION-TRI" in ids            # 对照：规则 2 仍命中


# ---------------------------------------------------------------------------
# 规则 4：QMT-FEE-TRANSFER-ALIAS（过户费字段/别名）
# ---------------------------------------------------------------------------

def test_fee_transfer_alias_hit():
    src = "fees = {'transfer_fee': 0.00001}\n"
    assert "QMT-FEE-TRANSFER-ALIAS" in _ids(src)


def test_fee_transfer_alias_no_hit():
    src = "fees = {'other_fee': 0.01}\n"
    assert "QMT-FEE-TRANSFER-ALIAS" not in _ids(src)


# ---------------------------------------------------------------------------
# 规则 5：QMT-DAT-NULL-TRIPLE（is None 误用于字符串字段 / truthiness 误用于持仓对象）
# ---------------------------------------------------------------------------

def test_dat_null_triple_hit_is_none_and_truthiness():
    src = (
        "if pos.delivery_date is None:\n"
        "    pass\n"
        "if get_position('510300.SH'):\n"
        "    pass\n"
    )
    ids = _ids(src)
    assert ids.count("QMT-DAT-NULL-TRIPLE") >= 2      # 两种形态各命中一次


def test_dat_null_triple_no_hit_correct_forms():
    src = (
        "if not pos.delivery_date:\n"
        "    pass\n"
        "if get_position('510300.SH').amount > 0:\n"
        "    pass\n"
        "if pos.amount == 0:\n"
        "    pass\n"
    )
    assert "QMT-DAT-NULL-TRIPLE" not in _ids(src)


# ---------------------------------------------------------------------------
# 规则 6：QMT-CODE-FORM-MASK（硬编码 .SS / 混用 .SH/.SZ 与裸码）
# ---------------------------------------------------------------------------

def test_code_form_mask_hit_dot_ss():
    src = "code = '510300.SS'\n"
    ids = _ids(src)
    assert "QMT-CODE-FORM-MASK" in ids


def test_code_form_mask_hit_mixed_forms():
    src = "a = '510300.SH'\nb = '000001'\n"           # 后缀码 + 裸码混用
    ids = _ids(src)
    assert "QMT-CODE-FORM-MASK" in ids


def test_code_form_mask_no_hit_uniform_suffixed():
    src = "a = '510300.SH'\nb = '159934.SZ'\n"        # 仅后缀码、无 .SS、无裸码
    assert "QMT-CODE-FORM-MASK" not in _ids(src)


# ---------------------------------------------------------------------------
# 规则 7：QMT-FEE-STAMP-PERIOD（跨 2023-08-28；静态不可判定，须调用方传入区间）
# ---------------------------------------------------------------------------

def test_fee_stamp_period_hit_crossing_range():
    src = "x = 1\n"
    ids = _ids(src, backtest_range=("2023-01-01", "2024-01-01"))
    assert "QMT-FEE-STAMP-PERIOD" in ids


def test_fee_stamp_period_no_hit_none_range():
    src = "x = 1\n"
    assert "QMT-FEE-STAMP-PERIOD" not in _ids(src, backtest_range=None)


def test_fee_stamp_period_no_hit_non_crossing_range():
    src = "x = 1\n"
    assert "QMT-FEE-STAMP-PERIOD" not in _ids(
        src, backtest_range=("2024-01-01", "2024-06-30"))


# ---------------------------------------------------------------------------
# 规则 8：QMT-LIMIT-BOARD-FIDELITY（check_limit × 下单分支）
# ---------------------------------------------------------------------------

def test_limit_board_fidelity_hit():
    src = (
        "d = check_limit('510300.SH', '2024-01-05')\n"
        "order('510300.SH', 100)\n"
    )
    assert "QMT-LIMIT-BOARD-FIDELITY" in _ids(src)


def test_limit_board_fidelity_no_hit_single_side():
    only_check = "d = check_limit('510300.SH', '2024-01-05')\n"
    only_order = "order('510300.SH', 100)\n"
    assert "QMT-LIMIT-BOARD-FIDELITY" not in _ids(only_check)
    assert "QMT-LIMIT-BOARD-FIDELITY" not in _ids(only_order)
