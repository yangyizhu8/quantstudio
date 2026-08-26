# -*- coding: utf-8 -*-
"""P-D11 持仓视图归一注入测试（WP-A1，2026-08-26）。

设计：docs/pd11-position-view-normalization-design.md（v1.2 审定冻结）
证据：docs/evidence/pd11-pos-probe-20260826.md（P-POS 平台实证 F1~F7）

矩阵（设计 §5）：
  T1 键归一（sid 锚点）          T2 sid 缺失回退（权威镜像规则）
  T3 残影过滤（amount=0 剔除）   T4 字段契约视图（含 avg_cost 别名）
  T5 get_position 输入归一/空仓  T6 get_positions(security) 过滤
  T7 结构漂移 fail-loud          T8 与本地 _get_ptrade_positions 形状同构
  T9 ETF 前缀回退（v1.1）        T10 BJ 精确表优先序（v1.2）
  T11 差分等价：模板 _qs_norm_code vs 权威 normalize_security_code（防漂移闸）
  另：门控注入/不注入/字符串字面量不触发/幂等 + 渲染含烘焙 BSE 快照。

hermetic：无 DB 依赖（T11 语料 = 分支构造码 + BSE 全表键 + 后缀变体）。
"""
import pathlib
import sys
import textwrap

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import quantstudio.strategy_compiler.source_import as si  # noqa: E402
from quantstudio.strategy_compiler.source_import import convert_source  # noqa: E402


# ========== 辅助构造 ==========

class _FakePlatformPosition:
    """模拟平台 Position（探针 F2/F3/DIR 字段集）。"""

    def __init__(self, sid, amount, cost_basis=0.0, last_sale_price=0.0,
                 enable_amount=None):
        self.sid = sid
        self.amount = amount
        self.cost_basis = cost_basis
        self.last_sale_price = last_sale_price
        self.enable_amount = (amount if enable_amount is None
                              else enable_amount)

    @property
    def market_value(self):
        return (self.last_sale_price or 0) * (self.amount or 0)


class _FakePlatformPositionNoSid(_FakePlatformPosition):
    """sid 缺失形态（T2 回退路径）。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.sid = None


def _pos_view_ns(positions=None, gp_result=None):
    """exec 渲染后的 P-D11 扩展 → 命名空间（fake 平台 orig API）。"""
    positions = {} if positions is None else positions
    captured = {"gp": []}

    def _fake_get_positions(security=None):
        return dict(positions)

    def _fake_get_position(security):
        captured["gp"].append(security)
        if gp_result is not None:
            return gp_result
        return positions.get(security.replace(".SS", ".XSHG")
                             .replace(".SZ", ".XSHE"),
                             _FakePlatformPosition(security, 0))

    shape_calls = []

    def _shape_check(api, expected, actual):
        shape_calls.append((api, expected, type(actual).__name__))
        return True

    ns = {"get_positions": _fake_get_positions,
          "get_position": _fake_get_position,
          "_qs_shape_check": _shape_check}
    exec(si._render_position_view_ext("# m"), ns)
    ns["_captured"] = captured
    ns["_shape_calls"] = shape_calls
    return ns


def _convert(code: str, name: str = "t.py"):
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / name
        p.write_text(textwrap.dedent(code), encoding="utf-8")
        r = convert_source(p)
    return r


USES_POSITION_SRC = """
def initialize(context):
    set_benchmark('000300.SS')

def handle_data(context, data):
    positions = get_positions()
    p = get_position('600000.SS')
    if getattr(p, 'amount', 0) > 0:
        order_target_value('600000.SS', 0)
"""


# ========== T1：键归一（sid 锚点） ==========

def test_t1_sid_anchor_key_normalization():
    """XSHG 键 + sid='.SS' → 输出键 .SS（F1 双体系归一，CANSLIM 根因修复点）。"""
    ns = _pos_view_ns({"600000.XSHG":
                       _FakePlatformPosition("600000.SS", 200, 8.67, 8.65)})
    out = ns["get_positions"]()
    assert list(out.keys()) == ["600000.SS"], out.keys()
    assert out["600000.SS"].amount == 200


# ========== T2：sid 缺失回退（权威镜像规则） ==========

def test_t2_sid_missing_fallback_authority_rules():
    ns = _pos_view_ns({"600000.XSHG": _FakePlatformPositionNoSid(None, 100),
                       "000001.XSHE": _FakePlatformPositionNoSid(None, 100),
                       "515050.XSHG": _FakePlatformPositionNoSid(None, 100)})
    out = ns["get_positions"]()
    assert set(out.keys()) == {"600000.SS", "000001.SZ", "515050.SS"}, out.keys()


# ========== T3：残影过滤 ==========

def test_t3_ghost_row_filtered():
    """amount=0 残影行剔除（F4：卖出当日残留）；amount>0 保留。"""
    ns = _pos_view_ns({
        "600000.XSHG": _FakePlatformPosition("600000.SS", 0, 0, 8.69),   # 残影
        "000001.XSHE": _FakePlatformPosition("000001.SZ", 100, 10.0, 10.5),
    })
    out = ns["get_positions"]()
    assert list(out.keys()) == ["000001.SZ"]
    # 平台 amount 为字符串数字形态亦应被过滤（CSV '0.0' 形态防御）
    ns2 = _pos_view_ns({"600000.XSHG": _FakePlatformPosition("600000.SS", "0.0")})
    assert ns2["get_positions"]() == {}


# ========== T4：字段契约视图 ==========

def test_t4_field_view_contract():
    ns = _pos_view_ns({"600000.XSHG":
                       _FakePlatformPosition("600000.SS", 200, 8.675, 8.65)})
    view = ns["get_positions"]()["600000.SS"]
    assert view.sid == "600000.SS"                 # 归一键
    assert view.amount == 200
    assert view.enable_amount == 200
    assert view.cost_basis == 8.675
    assert view.last_sale_price == 8.65
    assert view.avg_cost == 8.675                  # 别名（F3）
    assert abs(view.market_value - 8.65 * 200) < 1e-9
    # 本地契约也没有的属性 → 默认 0（不 AttributeError）
    assert view.volume == 0
    assert view.value == 0.0


# ========== T5：get_position 输入归一/空仓语义 ==========

def test_t5_get_position_input_normalization_and_empty():
    ns = _pos_view_ns()
    p = ns["get_position"]("600000.XSHG")
    assert ns["_captured"]["gp"] == ["600000.SS"]   # XSHG → .SS 传给 orig
    assert p.amount == 0 and p.cost_basis == 0.0    # 空仓契约（F5）

    ns2 = _pos_view_ns()
    ns2["get_position"]("600000.SH")                # .SH → .SS（防平台崩溃）
    assert ns2["_captured"]["gp"] == ["600000.SS"]

    ns3 = _pos_view_ns()
    ns3["get_position"]("600000")                   # 裸码 → .SS（防空壳）
    assert ns3["_captured"]["gp"] == ["600000.SS"]

    ns4 = _pos_view_ns(gp_result=None)
    p4 = ns4["get_position"]("000001.SZ")
    assert p4.amount == 0                           # 未持仓 → amount=0 视图


# ========== T6：get_positions(security) 过滤 ==========

def test_t6_security_filter():
    ns = _pos_view_ns({"600000.XSHG":
                       _FakePlatformPosition("600000.SS", 200, 8.67, 8.65)})
    out = ns["get_positions"]("600000.XSHG")        # 四位入参 → 归一键输出
    assert list(out.keys()) == ["600000.SS"]
    assert ns["get_positions"]("000001.SZ") == {}


# ========== T7：结构漂移 fail-loud ==========

def test_t7_shape_violation_fail_loud():
    class _NotADict:
        pass

    ns = _pos_view_ns()
    ns["_QSPositionState"].get_positions_orig = lambda security=None: _NotADict()
    with pytest.raises(ValueError, match="QS_POS_VIEW_VIOLATION"):
        ns["get_positions"]()
    # None → 空 dict（平台空仓合法形态，非漂移）
    ns2 = _pos_view_ns()
    ns2["_QSPositionState"].get_positions_orig = lambda security=None: None
    assert ns2["get_positions"]() == {}


# ========== T8：与本地 _get_ptrade_positions 形状同构 ==========

def test_t8_local_contract_shape_isomorphism():
    """同一持仓状态：wrapper 输出 vs 本地引擎视图 → 键集/amount/cost 通道同形。"""
    from quantstudio.backtest.backtest_engine import Account, Position as EnginePos
    engine = object.__new__(sys.modules["quantstudio.backtest.backtest_engine"]
                            .BacktestEngine)
    engine.account = Account(cash=1000.0, positions={
        "600000.SH": EnginePos("600000.SH", volume=200, avg_cost=8.675, can_sell=200),
        "000001.SZ": EnginePos("000001.SZ", volume=100, avg_cost=10.0, can_sell=100),
    })
    local = engine._get_ptrade_positions({"600000.SH": 8.65, "000001.SZ": 10.5})

    ns = _pos_view_ns({
        "600000.XSHG": _FakePlatformPosition("600000.SS", 200, 8.675, 8.65),
        "000001.XSHE": _FakePlatformPosition("000001.SZ", 100, 10.0, 10.5),
    })
    remote = ns["get_positions"]()

    assert set(remote.keys()) == set(local.keys())
    for k in local:
        assert remote[k].amount == local[k].amount
        assert remote[k].cost_basis == local[k].cost_basis   # 通道同形（口径差 F7 排除数值断言）
        assert remote[k].sid == local[k].sid


# ========== T9/T10：前缀回退规则（v1.1/v1.2） ==========

def test_t9_etf_prefix_fallback():
    ns = _pos_view_ns()
    norm = ns["_qs_norm_code"]
    assert norm("515050.XSHG") == "515050.SS"   # 沪 ETF（v1.1 修复点）
    assert norm("510300") == "510300.SS"
    assert norm("511260.XSHG") == "511260.SS"
    assert norm("159915.XSHE") == "159915.SZ"   # 深 ETF


def test_t10_bse_exact_table_priority():
    ns = _pos_view_ns()
    norm = ns["_qs_norm_code"]
    # 920 新码
    assert norm("920018") == "920018.BJ"
    # legacy 表命中（430/83/87 段仅精确表成员才是 BJ）
    from quantstudio.backtest.libs.security_code_rules import BSE_LEGACY_TO_920
    legacy_key = sorted(BSE_LEGACY_TO_920)[0]
    assert norm(legacy_key) == legacy_key + ".BJ"
    # BSE 精确表优先于后缀（权威同序：430047.SH → BJ，不是 SS）
    assert norm(legacy_key + ".SH") == legacy_key + ".BJ"
    assert norm("920018.SS") == "920018.BJ"
    # 优先序钉死：921xxx（920 前缀邻域）不是 BJ → 9→SS
    assert norm("921000") == "921000.SS"
    # 43/83/88 段非表成员 → 权威兜底 SS（非 SZ）
    assert norm("439999") == "439999.SS"
    assert norm("889999") == "889999.SS"
    # 后缀优先于前缀（非 BSE 码）
    assert norm("600000.SH") == "600000.SS"
    assert norm("000001.SZ") == "000001.SZ"
    # 权威无 6 位数字门控：非标串走分支兜底（T11 镜像要求）
    assert norm("abc123") == "ABC123.SS"
    assert norm("12345") == "12345.SZ"


def test_t10b_baked_bse_snapshot_complete():
    """渲染产物内烘焙的 legacy 集与权威全量一致（无截断/无多余）。"""
    from quantstudio.backtest.libs.security_code_rules import BSE_LEGACY_TO_920
    ns = _pos_view_ns()
    assert ns["_QS_BSE_LEGACY"] == frozenset(
        str(k).split(".")[0] for k in BSE_LEGACY_TO_920)
    assert len(ns["_QS_BSE_LEGACY"]) == len(BSE_LEGACY_TO_920)


# ========== T11：差分等价（防漂移闸） ==========

def _t11_corpus():
    """分支构造码 + BSE 全表键 + 后缀变体 + 边界码。"""
    from quantstudio.backtest.libs.security_code_rules import BSE_LEGACY_TO_920
    corpus = set()
    # 各前缀分支代表
    for bare in ("600000", "688001", "510300", "515050", "900001", "920018",
                 "000001", "002458", "300750", "301210", "159915", "200001",
                 "110038", "111022", "113050", "118036", "123456", "127045",
                 "128076", "439999", "839999", "879999", "889999", "921000",
                 "921999", "430000", "830000", "870000", "880000", "400001",
                 "700001", "abc123", "12345", "1234567"):
        corpus.add(bare)
    # 权威 BSE 全表键（裸码）
    corpus |= {str(k).split(".")[0] for k in BSE_LEGACY_TO_920}
    # 后缀变体
    suffixed = set()
    for bare in ("600000", "000001", "515050", "920018", "430047", "159915"):
        for suf in (".SS", ".SZ", ".SH", ".XSHG", ".XSHE", ".BJ", ".XBJ",
                    ".XBSE", ".QQ"):
            suffixed.add(bare + suf)
    return sorted(corpus | suffixed)


def test_t11_differential_equivalence_with_authority():
    """模板 _qs_norm_code vs 权威 normalize_security_code(ptrade)：全语料零分歧。"""
    from quantstudio.backtest.libs.security_code_rules import (
        normalize_security_code)
    ns = _pos_view_ns()
    norm = ns["_qs_norm_code"]
    mismatches = []
    for code in _t11_corpus():
        got = norm(code)
        want = normalize_security_code(code, "ptrade")
        if got != want:
            mismatches.append((code, got, want))
    assert not mismatches, "模板/权威归一分歧（前 10）：%s" % mismatches[:10]


# ========== 门控/渲染集成 ==========

def test_gate_injected_when_position_api():
    r = _convert(USES_POSITION_SRC)
    assert r.errors == [], r.errors
    assert "持仓视图归一注入" in r.converted_code
    assert "_QSPositionState" in r.converted_code
    assert "_qs_norm_code" in r.converted_code
    assert "position_view" in r.coverage["injected_helpers"]


def test_gate_not_injected_when_unused():
    r = _convert("""
    def initialize(context):
        set_benchmark('000300.SS')
    def handle_data(context, data):
        order_target_value('600000.SS', 10000)
    """)
    assert r.errors == [], r.errors
    assert "持仓视图归一注入" not in r.converted_code
    assert "_QSPositionState" not in r.converted_code


def test_gate_string_literal_only_not_triggered():
    r = _convert("""
    POS_API = 'get_positions'
    def initialize(context):
        set_benchmark('000300.SS')
    """)
    assert r.errors == [], r.errors
    assert "_QSPositionState" not in r.converted_code


def test_render_bakes_bse_literal_and_count():
    rendered = si._render_position_view_ext("# m")
    assert "# m" in rendered
    assert "__QS_BSE_SET__" not in rendered and "__QS_BSE_N__" not in rendered
    assert "_QS_BSE_LEGACY = {" in rendered
    import re
    m = re.search(r"n=(\d+) 条", rendered)
    assert m and int(m.group(1)) > 200   # 248 条官方映射（探针固化）


def test_bse_loader_fail_loud_on_empty(monkeypatch):
    import quantstudio.backtest.libs.security_code_rules as rules
    monkeypatch.setattr(rules, "BSE_LEGACY_TO_920", {})
    with pytest.raises(ValueError, match="P-D11"):
        si._bse_legacy_bare_codes()


def test_product_position_view_passes_syntax():
    """渲染块可独立编译（模板语法完整性）。"""
    compile(si._render_position_view_ext("# m"), "<pd11>", "exec")


def test_local_class_instantiation_passes_whitelist():
    """P-D11 校验器补丁回归：本地 class 实例化不再被 LOCAL-API-WHITELIST 误 BLOCK
    （爆炸半径审计：strategies/ 全目录零本地类定义，仅注入模板受影响）。"""
    from quantstudio.strategy_compiler.validators.validate_local_strategy import (
        validate_local_strategy)
    src = textwrap.dedent("""
        def initialize(context):
            set_benchmark('000300.SS')

        def handle_data(context, data):
            v = _Widget('x')
            order_target_value('600000.SS', 10000)

        class _Widget:
            def __init__(self, name):
                self.name = name
        """)
    ok, violations, _ = validate_local_strategy(None, None, src, "ptrade-default")
    assert ok, violations
    # 反向：未定义的类名仍 BLOCK（白名单收紧不放松）
    src_bad = src.replace("class _Widget:", "class _Other:")
    ok2, v2, _ = validate_local_strategy(None, None, src_bad, "ptrade-default")
    assert not ok2 and any(v.rule_id == "LOCAL-API-WHITELIST" for v in v2)
