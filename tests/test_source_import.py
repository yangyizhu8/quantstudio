# -*- coding: utf-8 -*-
"""source_import 转换器测试（02 规格 §7 测试 1-12）。

运行：python -m pytest tests/test_source_import.py -v
"""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantstudio.strategy_compiler.source_import import convert_source  # noqa: E402

STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "quantstudio" / "backtest" / "strategies"


def _convert_code(code: str, strategy_id: str = "test_strategy"):
    """用内存代码转换（写临时文件）。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"{strategy_id}.py"
        p.write_text(code, encoding="utf-8")
        return convert_source(p)


# ---------------------------------------------------------------------------
# 测试 1：双均线策略（最简全流程）
# ---------------------------------------------------------------------------
def test_01_dual_ma_full_flow():
    src = STRATEGIES_DIR / "双均线策略.py"
    if not src.exists():
        pytest.skip("策略文件不存在")
    result = convert_source(src)
    assert result.errors == [], result.errors
    code = result.converted_code
    # 头部含 ptrade-default 标记
    assert "ptrade-default" in code[:500]
    # fq='dypre' → 'pre'（G1：有证据的 NORMALIZE）
    assert "fq='pre'" in code and "dypre" not in code
    # helper 注入
    assert "_portfolio_total_value" in code
    # 自定义函数保留
    assert "def get_ma" in code
    # 语法自检
    ast.parse(code)
    # 生命周期保留
    assert "def initialize(context)" in code
    assert "def handle_data(context, data)" in code


# ---------------------------------------------------------------------------
# 测试 2：set_backtest 定义（FunctionDef）删除
# ---------------------------------------------------------------------------
def test_02_set_backtest_definition_removed():
    code = '''
def initialize(context):
    g.security = '600570.SS'

def set_backtest():
    pass

def handle_data(context, data):
    pass
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    assert "def set_backtest" not in result.converted_code
    removed = [a for a in result.actions if a.api_name == "set_backtest"]
    assert removed and removed[0].action_type == "REMOVE"


def test_02b_set_backtest_config_lifted():
    """T5 修复：set_backtest 函数体内的配置调用必须提升到模块级（不得丢失）。"""
    code = '''
def initialize(context):
    g.security = '600570.SS'
    if not is_trade():
        set_backtest()  # 设置回测条件

def set_backtest():
    set_limit_mode('UNLIMITED')
    set_commission(commission_ratio=0.00015, min_commission=5.0)

def handle_data(context, data):
    pass
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "def set_backtest" not in out
    # 配置调用被提升到模块级（LIFT 动作）
    assert "set_limit_mode('UNLIMITED')" in out
    assert "set_commission(commission_ratio=0.00015, min_commission=5.0)" in out
    lifted = [a for a in result.actions if a.rule_id == "DENY-SET_BACKTEST-LIFT"]
    assert lifted, "应有 LIFT 动作"


# ---------------------------------------------------------------------------
# 测试 3：小市值策略ptrade.py（set_backtest 调用 + BOM）
# ---------------------------------------------------------------------------
def test_03_bom_and_set_backtest_call():
    src = STRATEGIES_DIR / "小市值策略ptrade.py"
    if not src.exists():
        pytest.skip("策略文件不存在")
    result = convert_source(src)
    assert result.errors == [], result.errors
    code = result.converted_code
    assert "def set_backtest" not in code
    # 只查代码正文（头部注释可含 API 名说明）
    body = code[code.find("def initialize"):]
    assert "set_backtest(" not in body
    assert "set_limit_mode" in body  # 配置调用已内联（保留执行时机）
    ast.parse(code)


# ---------------------------------------------------------------------------
# 测试 4：复杂策略（ashare_manual_pool_2d_momentum_top2_quantstudio.py）
# ---------------------------------------------------------------------------
def test_04_complex_strategy():
    src = STRATEGIES_DIR / "ashare_manual_pool_2d_momentum_top2_quantstudio.py"
    if not src.exists():
        pytest.skip("策略文件不存在")
    result = convert_source(src)
    # sklearn 只 WARN 不 BLOCK（N2）
    assert result.errors == [], result.errors
    code = result.converted_code
    ast.parse(code)
    assert "ptrade-default" in code[:500]


# ---------------------------------------------------------------------------
# 测试 5：字符串字面量防误替换
# ---------------------------------------------------------------------------
def test_05_no_false_replace_in_string_literal():
    code = '''
def initialize(context):
    g.note = "get_history( 与 fq='dypre' 是字符串内容"
    g.other = 'set_backtest 文本'

def handle_data(context, data):
    pass
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    # 字符串字面量原样保留
    assert "get_history( 与 fq='dypre' 是字符串内容" in out
    assert "set_backtest 文本" in out


# ---------------------------------------------------------------------------
# 测试 6：load_research_signals → BLOCK
# ---------------------------------------------------------------------------
def test_06_load_research_signals_block():
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    rows = load_research_signals('xxx.csv')
'''
    result = _convert_code(code)
    assert result.errors, "应当 BLOCK"
    assert any("load_research_signals" in e for e in result.errors)


# ---------------------------------------------------------------------------
# 测试 7：别名调用（H3）
# ---------------------------------------------------------------------------
def test_07_alias_call_detected():
    code = '''
from quantstudio.backtest.ptrade_import import get_history as gh

def initialize(context):
    g.security = '600570.SS'

def handle_data(context, data):
    h = gh(20, '1d', field=['close'], security_list=g.security, fq='dypre', include=False)
    pass
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    # 别名调用 gh(...) 中的 fq='dypre' 应被归一化为 'pre'（别名归一化后匹配规则）
    assert "fq='dypre'" not in out


# ---------------------------------------------------------------------------
# 测试 8：REMOVE 分档（H2）——档 2 内嵌改字面量；档 3 BLOCK
# ---------------------------------------------------------------------------
def test_08_remove_banding():
    code = '''
def initialize(context):
    g.trade_mode = is_trade()
    g.flag = set_backtest(1, 2)
    g.ev = get_strategy_events('x')
    pass

def handle_data(context, data):
    if g.flag:
        pass
'''
    result = _convert_code(code)
    # is_trade() 内嵌 → False（档 2，字面量已知）
    # set_backtest() 内嵌 → None（档 2：本地 lambda 返回 None，精确等价）
    # get_strategy_events() 内嵌且无字面量映射 → BLOCK（档 3）
    assert result.errors, "get_strategy_events 内嵌且无等价字面量应当 BLOCK"
    out = result.converted_code
    # 只检查代码正文（头部注释可含 API 名说明）
    body = out[out.find("def initialize"):]
    assert "is_trade(" not in body
    assert "g.trade_mode = False" in body
    assert "g.flag = None" in body


def test_08b_rewrite_literal_inline():
    code = '''
def initialize(context):
    g.mode = is_trade()

def handle_data(context, data):
    pass
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    assert "g.mode = False" in result.converted_code


# ---------------------------------------------------------------------------
# 测试 9：幂等性（二次转换零动作）
# ---------------------------------------------------------------------------
def test_09_idempotent():
    code = '''
def initialize(context):
    g.security = '600570.SS'

def handle_data(context, data):
    pass
'''
    first = _convert_code(code)
    assert first.errors == [], first.errors
    # 二次转换
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "idem.py"
        p.write_text(first.converted_code, encoding="utf-8")
        second = convert_source(p)
    assert second.errors == [], second.errors
    # 零动作：无新 REMOVE/INJECT（helper 已注入，幂等跳过）
    assert second.coverage.get("idempotent_skip", False), "二次转换应跳过注入"
    # 内容一致（头部时间戳除外，比较动作数）
    assert len([a for a in second.actions if a.action_type == "INJECT"]) == 0


# ---------------------------------------------------------------------------
# 测试 10：shim 注入
# ---------------------------------------------------------------------------
def test_10_shim_injected():
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    df = get_history_batch(['600570.SS', '600000.SS'], 20, '1d', fields=['close'])
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "def get_history_batch" in out
    # T5 修复：shim 必须解包 is_dict=True 的返回（{code: DataFrame}），禁止整个 CodeDict 入 result
    assert "df_dict" in out
    assert "for k, df in df_dict.items()" in out
    assert any(a.action_type == "SHIM" for a in result.actions)


# ---------------------------------------------------------------------------
# 测试 11：FQ WARN_KEEP（dypost 保留原值）
# ---------------------------------------------------------------------------
def test_11_fq_warn_keep():
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h = get_history(20, '1d', field=['close'], security_list='600570.SS', fq='dypost', include=False)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    # dypost 不等价于 post → 保留原值 + WARN + approximation
    assert "fq='dypost'" in out
    assert result.coverage["fq_warn_kept"], "应有 fq_warn_kept 记录"
    assert any("WARN_KEEP" in a.message or "保留原值" in a.message for a in result.actions)


# ---------------------------------------------------------------------------
# 测试 12：BOM 文件转换成功（N1）
# ---------------------------------------------------------------------------
def test_12_bom_file(tmp_path):
    p = tmp_path / "bom_strategy.py"
    body = (
        "def initialize(context):\n"
        "    g.security = '600570.SS'\n"
        "\n"
        "def handle_data(context, data):\n"
        "    pass\n"
    )
    p.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))  # 写 BOM
    result = convert_source(p)
    assert result.errors == [], result.errors
    assert "def initialize" in result.converted_code
