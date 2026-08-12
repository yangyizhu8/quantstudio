# -*- coding: utf-8 -*-
"""source_import 转换器测试（02 规格 §7 测试 1-12）。

运行：python -m pytest tests/test_source_import.py -v
"""
import ast
import pathlib
import sys
from pathlib import Path

import pandas as pd
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


# ---------------------------------------------------------------------------
# ETF FREEZE 档（07 规格 §6 测试 11-17）
# ---------------------------------------------------------------------------
import duckdb as _duckdb


def _make_etf_db(tmp_path: pathlib.Path) -> pathlib.Path:
    """构造最小 etf_basic/etf_daily 测试库（自包含，不依赖 staging 副本）。"""
    db = tmp_path / "etf_test.db"
    conn = _duckdb.connect(str(db))
    conn.execute("CREATE TABLE etf_basic (code VARCHAR, ts_code VARCHAR, name VARCHAR, "
                 "exchange VARCHAR, list_date BIGINT, delist_date BIGINT, etf_type VARCHAR, "
                 "tracking_index VARCHAR, is_cross_border BOOLEAN)")
    ms = lambda d: int(pd.Timestamp(d).value // 10**6)  # noqa: E731
    rows = [
        # (code, list_date, delist_date, etf_type)
        ("510300", ms("2020-01-01"), None, "equity"),   # 起始日前已上市，活跃
        ("159001", ms("2020-01-01"), None, "equity"),   # 深市
        ("588000", ms("2020-01-01"), ms("2023-06-01"), "equity"),  # 起始日后退市
        ("159915", ms("2023-01-01"), None, "equity"),   # 起始日后新上市
        ("511880", ms("2020-01-01"), None, "money"),    # 货币 ETF（equity 过滤应排除）
    ]
    conn.executemany("INSERT INTO etf_basic VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                     [(c, c, c, "X", ld, dd, et) for c, ld, dd, et in rows])
    conn.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT)")
    for c, ld, dd, et in rows:
        conn.execute("INSERT INTO etf_daily VALUES (?, ?)", [c, ms("2021-06-30")])
    conn.close()
    return db


def test_11_freeze_basic(tmp_path):
    """含 get_etf_list_local + 传起始日 → ETF_POOL_STATIC 注入 + 调用点替换。"""
    db = _make_etf_db(tmp_path)
    code = '''
def initialize(context):
    g.pool = get_etf_list_local(context.current_dt)

def handle_data(context, data):
    pass
'''
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "etf_strategy.py"
        p.write_text(code, encoding="utf-8")
        r = convert_source(p, etf_pool_start_date="2022-01-04", db_path=db)
    assert r.errors == [], r.errors
    out = r.converted_code
    assert "ETF_POOL_STATIC" in out
    assert "g.pool = ETF_POOL_STATIC" in out          # 调用点替换
    assert "510300.SS" in out and "159001.SZ" in out  # 后缀转换
    # 静态池列表本身不含起始日后新上市（注释文案可列出代码，属 07 §2.4 知情信息）
    pool_block = out[out.find("ETF_POOL_STATIC = ["):]
    pool_block = pool_block[:pool_block.find("]")]
    assert "159915" not in pool_block
    assert "511880" not in pool_block                 # money 类型被 equity 过滤
    frozen = [a for a in r.actions if a.action_type == "FREEZE"]
    assert frozen, "应有 FREEZE 动作"


def test_12_freeze_missing_start_block(tmp_path):
    """含 get_etf_list_local + 不传起始日 → BLOCK。"""
    code = '''
def initialize(context):
    g.pool = get_etf_list_local(context.current_dt)

def handle_data(context, data):
    pass
'''
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "etf_strategy.py"
        p.write_text(code, encoding="utf-8")
        r = convert_source(p)  # 不传 etf_pool_start_date
    assert r.errors, "应当 BLOCK"
    assert any("etf-pool-start-date" in e for e in r.errors)


def test_13_freeze_no_etf_basic_block(tmp_path):
    """etf_basic 缺失 → DATA_BLOCKED（禁止全 ETF 兜底）。"""
    empty_db = tmp_path / "empty.db"
    _duckdb.connect(str(empty_db)).close()  # 空库，无 etf_basic
    code = '''
def initialize(context):
    g.pool = get_etf_list_local(context.current_dt)

def handle_data(context, data):
    pass
'''
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "etf_strategy.py"
        p.write_text(code, encoding="utf-8")
        r = convert_source(p, etf_pool_start_date="2022-01-04", db_path=empty_db)
    assert r.errors, "应当 DATA_BLOCKED"
    assert any("DATA_BLOCKED" in e for e in r.errors)


def test_14_freeze_warning_text(tmp_path):
    """转换后 warnings 含 07 §2.4 三段文案。"""
    db = _make_etf_db(tmp_path)
    code = '''
def initialize(context):
    g.pool = get_etf_list_local(context.current_dt)

def handle_data(context, data):
    pass
'''
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "etf_strategy.py"
        p.write_text(code, encoding="utf-8")
        r = convert_source(p, etf_pool_start_date="2022-01-04", db_path=db)
    assert r.errors == [], r.errors
    txt = "\n".join(r.warnings)
    assert "PTrade 版基于回测起始日 2022-01-04 的 ETF 池快照生成，共 3 只" in txt
    assert "不含起始日后新上市的 ETF（1 只）" in txt and "159915" in txt
    assert "仍含起始日后退市的 ETF（1 只）" in txt and "588000" in txt
    assert "回测起始日期不得早于 2022-01-04" in txt


def test_15_freeze_suffix_conversion(tmp_path):
    """本地 bare 码 → PTrade 后缀（.SH→.SS、.SZ、.BJ 规则）。"""
    db = _make_etf_db(tmp_path)
    code = '''
def initialize(context):
    g.pool = get_etf_list_local(context.current_dt)

def handle_data(context, data):
    pass
'''
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "etf_strategy.py"
        p.write_text(code, encoding="utf-8")
        r = convert_source(p, etf_pool_start_date="2022-01-04", db_path=db)
    assert r.errors == [], r.errors
    pool_block = r.converted_code[r.converted_code.find("ETF_POOL_STATIC = ["):]
    pool_block = pool_block[:pool_block.find("]")]
    assert ".SS" in pool_block and ".SZ" in pool_block
    assert ".SH" not in pool_block  # PTrade 禁止 .SH 后缀


def test_16_no_etf_no_calendar(tmp_path):
    """不含 get_etf_list_local → 不触发 FREEZE（无静态池、无 FREEZE 动作）。"""
    code = '''
def initialize(context):
    g.security = '600570.SS'

def handle_data(context, data):
    pass
'''
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "plain_strategy.py"
        p.write_text(code, encoding="utf-8")
        r = convert_source(p, etf_pool_start_date="2022-01-04",
                           db_path=pathlib.Path(tmp_path) / "nope.db")
    assert r.errors == [], r.errors
    assert "ETF_POOL_STATIC" not in r.converted_code
    assert not any(a.action_type == "FREEZE" for a in r.actions)


def test_17_freeze_idempotent(tmp_path):
    """转换产物再次转换 → 不重复 FREEZE（幂等）。"""
    db = _make_etf_db(tmp_path)
    code = '''
def initialize(context):
    g.pool = get_etf_list_local(context.current_dt)

def handle_data(context, data):
    pass
'''
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "etf_strategy.py"
        p.write_text(code, encoding="utf-8")
        r1 = convert_source(p, etf_pool_start_date="2022-01-04", db_path=db)
    assert r1.errors == [], r1.errors
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td2:
        p2 = pathlib.Path(td2) / "etf_converted.py"
        p2.write_text(r1.converted_code, encoding="utf-8")
        r2 = convert_source(p2, etf_pool_start_date="2022-01-04", db_path=db)
    assert r2.errors == [], r2.errors
    assert r2.coverage.get("idempotent_skip", False), "二次转换应跳过 FREEZE"
    assert not any(a.action_type == "FREEZE" for a in r2.actions)


# ---------------------------------------------------------------------------
# 代码后缀规范化（聚宽 XSHG/XSHE → PTrade SS/SZ）
# ---------------------------------------------------------------------------
def test_18_code_suffix_normalization():
    """本地策略用聚宽风格 .XSHE/.XSHG → 转换产物用 .SZ/.SS（PTrade 约定）。

    背景（2026-08-11 实测）：ETF动量.py 用 .XSHE 后缀，转换产物被
    validate_local_strategy 的 PORTFOLIO-POSITIONS-EXACT-MATCH BLOCK。
    """
    code = """
def initialize(context):
    g.fund_list = ['159770.XSHE', '510300.XSHG', '510500.SH']

def handle_data(context, data):
    pass
"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p2 = pathlib.Path(td) / "suffix_strategy.py"
        p2.write_text(code, encoding="utf-8")
        r = convert_source(p2)
    assert r.errors == [], r.errors
    out = r.converted_code
    assert "159770.XSHE" not in out
    assert "159770.SZ" in out
    assert "510300.XSHG" not in out
    assert "510300.SS" in out
    # 评估结论 B（2026-08-11）：.SH 也规范化为 .SS（security_code_rules PTrade 目标=SS）
    assert "510500.SH" not in out
    assert "510500.SS" in out
    norm = [a for a in r.actions if a.rule_id == "NORM-CODE-SUFFIX"]
    assert len(norm) == 3, norm


def test_18b_index_code_suffix_normalization():
    """指数代码 .SH 同样规范化（bbi_etf_rotation 场景：000001.SH 等宽基指数）。"""
    code = """
def initialize(context):
    g.index_list = ['000001.SH', '000852.SH', '399001.SZ']

def handle_data(context, data):
    pass
"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p2 = pathlib.Path(td) / "index_suffix_strategy.py"
        p2.write_text(code, encoding="utf-8")
        r = convert_source(p2)
    assert r.errors == [], r.errors
    out = r.converted_code
    assert "000001.SH" not in out and "000001.SS" in out
    assert "000852.SH" not in out and "000852.SS" in out
    assert "399001.SZ" in out  # 深市后缀保持不变


# ---------------------------------------------------------------------------
# 测试 19：shim 注入后的 get_history_batch 调用不再被校验器误 BLOCK（zcode 2026-08-12）
# ---------------------------------------------------------------------------
def test_19_shim_passes_portability():
    """含 get_history_batch 调用的策略 → 转换产物含 shim def → validate PASS（不 BLOCK）。"""
    from quantstudio.strategy_compiler.validators.validate_ptrade_portability import (
        validate_ptrade_portability,
    )
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    df = get_history_batch(['600570.SS', '600000.SS'], 20, '1d', fields=['close'])
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "def get_history_batch" in out  # shim 已注入
    ok, violations, _ = validate_ptrade_portability(out, None, None)
    assert ok, violations
    assert not any(v.rule_id == "PORTABILITY-LOCAL-EXTENSION-BAN" for v in violations)


def test_20_shim_exception_not_for_deny_remove():
    """shim 例外只限 DENY_SHIM 类：set_backtest（DENY_REMOVE）即使有 def 定义仍 BLOCK。"""
    from quantstudio.strategy_compiler.validators.validate_ptrade_portability import (
        validate_ptrade_portability,
    )
    # 构造"产物里定义并调用 set_backtest"（转换器正常会移除，此处模拟未移除的坏产物）
    code = '''
def set_backtest(start, end):
    pass

def initialize(context):
    set_backtest('2024-01-01', '2024-06-30')
'''
    ok, violations, _ = validate_ptrade_portability(code, None, None)
    assert not ok
    assert any(v.rule_id == "PORTABILITY-LOCAL-API" for v in violations)
