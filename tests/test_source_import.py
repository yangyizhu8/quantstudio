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

def test_21_ashares_date_idempotent():
    """get_Ashares 变量形态二次转换不重复包装（幂等）。"""
    from quantstudio.strategy_compiler.source_import import convert_source
    import pathlib, tempfile
    code = """
def initialize(context):
    all_codes = get_Ashares(context.current_dt.strftime('%Y-%m-%d'))

def handle_data(context, data):
    pass
"""
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / 't.py'
        p.write_text(code, encoding='utf-8')
        r1 = convert_source(p)
        assert r1.errors == [], r1.errors
        p2 = pathlib.Path(td) / 't2.py'
        p2.write_text(r1.converted_code, encoding='utf-8')
        r2 = convert_source(p2)
        assert r2.errors == [], r2.errors
        # strftime 内联形态：一次转换直接改写为 %Y%m%d（无 isinstance 包装）
        assert "strftime('%Y%m%d')" in r1.converted_code
        # 幂等：二次转换不重复改写/包装
        assert r2.converted_code.count("strftime('%Y%m%d')") == r1.converted_code.count("strftime('%Y%m%d')")
        assert r2.converted_code.count('isinstance') <= r1.converted_code.count('isinstance')


# ---------------------------------------------------------------------------
# 测试 22-27：include 不映射（PTrade include 语义与本地一致，2026-08-13 第二次实测确认）
# PTrade include=True 含当天 / include=False 到前一交易日（日线）或前一 bar（分钟），
# 与本地完全一致——转换管线透传 include，不做任何改写（NORM-INCLUDE-PTRADE 已删除）。
# ---------------------------------------------------------------------------
def test_22_include_false_signature_a_unchanged():
    """签名 A 形态（security-first）+ include=False → 产物 include=False 保留（不改）。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h = get_history(security='510300.SS', count=20, include=False)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "include=False" in out
    assert "include=True" not in out
    assert "NORM-GETHISTORY-SIG" in [a.rule_id for a in result.actions]  # 签名改写仍发生
    assert result.coverage.get("normalized_params", 0) >= 1


def test_23_include_false_count_first_unchanged():
    """count-first 形态 + include=False → include=False 保留（不改签名、不改 include）。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h = get_history(20, frequency='1d', security_list='510300.SS', include=False)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "include=False" in out
    assert "include=True" not in out


def test_24_include_true_unchanged():
    """include=True → 产物 include=True（透传不改）。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h = get_history(20, frequency='1d', security_list='510300.SS', include=True)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "include=True" in out


def test_25_no_include_unchanged():
    """无 include 参数 → 不改（不引入 include 参数）。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h = get_history(20, frequency='1d', security_list='510300.SS')
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    tree = ast.parse(out)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "get_history"]
    assert calls, "产物应有 get_history 调用"
    assert not any(kw.arg == "include" for kw in calls[0].keywords)


def test_26_include_false_idempotent():
    """include=False 二次转换仍保留 False（透传幂等）。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h = get_history(20, frequency='1d', security_list='510300.SS', include=False)
'''
    first = _convert_code(code)
    assert first.errors == [], first.errors
    assert "include=False" in first.converted_code
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "idem_include.py"
        p.write_text(first.converted_code, encoding="utf-8")
        second = convert_source(p)
    assert second.errors == [], second.errors
    assert "include=False" in second.converted_code


def test_27_shim_default_include_false():
    """get_history_batch shim 默认参数 include=False（两端 include 语义一致）。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    df = get_history_batch(['510300.SS', '510500.SS'], 20, '1d', fields=['close'])
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "def get_history_batch" in out
    assert "include=False" in out
    assert "include=True" not in out
    assert any(a.action_type == "SHIM" for a in result.actions)


# ---------------------------------------------------------------------------
# 测试 28-33：include 不映射（所有频率透传，PTrade include 语义与本地一致，
# 2026-08-13 第二次实测含日期戳确认；NORM-INCLUDE-PTRADE 已删除）
# ---------------------------------------------------------------------------
def test_28_include_mapping_minute_1m_unchanged():
    """count-first frequency='1m' + include=False → 不改。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h = get_history(20, frequency='1m', security_list='510300.SS', include=False)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "include=False" in out


def test_29_include_mapping_minute_5m_unchanged():
    """count-first frequency='5m' + include=False → 不改。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h = get_history(20, frequency='5m', security_list='510300.SS', include=False)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    assert "include=False" in result.converted_code


def test_30_include_mapping_signature_a_minute_unchanged():
    """签名 A unit='1m' + include=False → 不改（include=False 保留，无映射 action）。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h = get_history(security='510300.SS', count=20, unit='1m', include=False)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "include=False" in out


def test_31_include_mapping_unknown_freq_unchanged():
    """频率为变量 → 无法确定 → 保守不改（禁止行为 7）。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    freq = '1d' if context.run_params.frequency else '1m'
    h = get_history(20, frequency=freq, security_list='510300.SS', include=False)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "include=False" in out


def test_32_include_mapping_signature_a_daily_explicit_unit():
    """签名 A 显式 unit='1d' + include=False → include=False 保留（不映射）。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h = get_history(security='510300.SS', count=20, unit='1d', include=False)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "include=False" in out
    assert "include=True" not in out


def test_33_include_mapping_count_first_positional_daily():
    """count-first 位置参数形态 get_history(count, '1d', ...) + include=False → 不映射。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h = get_history(20, '1d', ['close'], '510300.SS', include=False)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "include=False" in out
    assert "include=True" not in out


def test_34_include_no_mapping_all_freqs():
    """日线 + 分钟 + 5m 的 include=False → 产物全部保留 include=False（不映射）。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h1 = get_history(20, frequency='1d', security_list='510300.SS', include=False)
    h2 = get_history(20, frequency='1m', security_list='510300.SS', include=False)
    h3 = get_history(20, frequency='5m', security_list='510300.SS', include=False)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert out.count("include=False") == 3
    assert "include=True" not in out


def test_35_include_true_no_mapping():
    """include=True → 产物 include=True（透传不改）。"""
    code = '''
def initialize(context):
    pass

def handle_data(context, data):
    h = get_history(20, frequency='1d', security_list='510300.SS', include=True)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "include=True" in out
    assert "include=False" not in out


# ---------------------------------------------------------------------------
# 2026-09-03 平台吸收（docs/strategy-compiler/ptrade-platform-absorptions-design.md）
# T1-T7：source_import R1/R2 吸收规则；T8：校验器配套 BLOCK（审计 P2-1/P2-4）
# ---------------------------------------------------------------------------
from quantstudio.strategy_compiler.validators.validate_ptrade_portability import (  # noqa: E402
    validate_ptrade_portability,
)

_ASHARES_SRC = '''
_EXCLUDE_BSE = True

def initialize(context):
    pass

def before_trading_start(context, data):
    codes = get_Ashares(exclude_bse=_EXCLUDE_BSE)
'''


def test_36_ashares_exclude_bse_constant_stripped_and_baked_true():
    """T1：常量 True → 调用点无 kwarg + _QS_EXCLUDE_BSE = True 烘焙 + NORM 审计。"""
    code = '''
def initialize(context):
    pass

def before_trading_start(context, data):
    codes = get_Ashares(exclude_bse=True)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "get_Ashares(exclude_bse=" not in out
    assert "_QS_EXCLUDE_BSE = True" in out
    assert any(a.rule_id == "NORM-ASHARES-EXCLUDE_BSE" for a in result.actions)
    ast.parse(out)


def test_37_ashares_exclude_bse_module_const_resolved():
    """T2：模块常量 _EXCLUDE_BSE=True 引用 → 解析烘焙 True（P2-2：末次赋值语义）。"""
    result = _convert_code(_ASHARES_SRC)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "get_Ashares(exclude_bse=" not in out
    assert "_QS_EXCLUDE_BSE = True" in out
    assert any(a.rule_id == "NORM-ASHARES-EXCLUDE_BSE" for a in result.actions)


def test_38_ashares_exclude_bse_false_bake():
    """T3：exclude_bse=False → 剥离 + 烘焙 False（源值权威）。"""
    code = '''
def initialize(context):
    pass

def before_trading_start(context, data):
    codes = get_Ashares(exclude_bse=False)
'''
    result = _convert_code(code)
    out = result.converted_code
    assert "get_Ashares(exclude_bse=" not in out
    assert "_QS_EXCLUDE_BSE = False" in out


def test_39_ashares_exclude_bse_dynamic_expr_failsoft_warn():
    """T3b：动态表达式 → 剥离 + 回退 CLI(False) + WARN（不静默丢语义）。"""
    code = '''
_G = {'exclude_bse': True}

def initialize(context):
    pass

def before_trading_start(context, data):
    codes = get_Ashares(exclude_bse=_G['exclude_bse'])
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "get_Ashares(exclude_bse=" not in out
    assert any("不可静态解析" in w for w in result.warnings), result.warnings


def test_40_set_commission_min_floor_absorb():
    """T4：min_commission=0 → 0.01；5.0 → 原样；表达式 → max(expr, 0.01)。"""
    code = '''
def initialize(context):
    set_commission(commission_ratio=0.0003, min_commission=0)
    set_commission(type='ETF', commission_ratio=0.00005, min_commission=0.5)
    set_commission(commission_ratio=CR, min_commission=MIN_COMMISSION)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "min_commission=0.01" in out          # 0 → 0.01
    assert "min_commission=0.5" in out           # >0 原样（纯增益）
    assert "max(MIN_COMMISSION, 0.01)" in out    # 动态表达式包装
    assert any(a.rule_id == "NORM-COMMISSION-MIN-FLOOR" for a in result.actions)
    ast.parse(out)


def test_41_ashares_date_plus_exclude_bse_combined():
    """T5：date+exclude_bse 组合 → 单次整重建：date 归一 + kwarg 剥离，无范围重叠损坏。"""
    code = '''
def initialize(context):
    pass

def before_trading_start(context, data):
    codes = get_Ashares(date='2021-01-04', exclude_bse=True)
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "get_Ashares('20210104')" in out or "get_Ashares(date='20210104')" in out
    assert "get_Ashares(exclude_bse=" not in out  # 精确调用点形态（shim 日志串含 exclude_bse=%s 属正常）
    assert "_QS_EXCLUDE_BSE = True" in out
    ast.parse(out)  # 结构完整（防重叠损坏）


def test_42_absorption_idempotent():
    """T6：二次转换不嵌套（max 不再包裹、kwarg 不复发、烘焙常量稳定）。"""
    src = '''
_EXCLUDE_BSE = True

def initialize(context):
    set_commission(commission_ratio=0.0003, min_commission=0)

def before_trading_start(context, data):
    codes = get_Ashares(exclude_bse=_EXCLUDE_BSE)
'''
    r1 = _convert_code(src)
    assert r1.errors == [], r1.errors
    r2 = _convert_code(r1.converted_code, strategy_id="test_strategy_2")
    assert r2.errors == [], r2.errors
    out2 = r2.converted_code
    assert "max(max(" not in out2 and "max(0.01," not in out2
    assert "get_Ashares(exclude_bse=" not in out2
    assert ast.parse(out2)


def test_43_golden_unaffected_strategies_unchanged():
    """T7：新规则未触发 = 吸收改动对未受影响策略零介入（纯增益语义证明）。

    HEAD 逐字节 golden 在共享工作区不可信：git status 显示 source_import.py 在我改动
    前即为 M（其他会话未提交平台实证：get_history include/preclose v7-v9、QSPROBE），
    其注入 shim 文本变化会使 HEAD 结论失真。本用例改为语义断言（对我的改动有效）：
    ①转换无错；②两条新规则零触发（无 NORM-ASHARES-EXCLUDE_BSE / NORM-COMMISSION-MIN-FLOOR）；
    ③关键锚点 verbatim 保留；④产物 AST 合法。逐字节 golden 待共享工作区冲突解决后
    以「改动前自基线」固化（验收证据已记录该移交条件）。
    """
    anchors = {
        "bbi_etf_rotation_quantstudio.py": "min_commission=5.0",
        "ETF动量.py": "min_commission=0.5",
        "二八轮动策略.py": "min_commission=5.0",
    }
    for name, anchor in anchors.items():
        p = STRATEGIES_DIR / name
        if not p.exists():
            pytest.skip(f"策略文件不存在 {name}")
        r = convert_source(p)
        assert r.errors == [], (name, r.errors)
        out = r.converted_code
        assert not any(a.rule_id in ("NORM-ASHARES-EXCLUDE_BSE", "NORM-COMMISSION-MIN-FLOOR")
                       for a in r.actions), name
        assert "get_Ashares(exclude_bse=" not in out, name
        assert anchor in out, name
        ast.parse(out)


def test_45_dynamic_date_expr_wrap_no_crash():
    """T9：动态 date 表达式 → 包装 replace/strftime 三元（fall_reversal 触发路径，防
    ast.Call 缺 keywords 的 AttributeError 复发），产物 AST 合法、幂等可再转。"""
    code = '''
def initialize(context):
    pass

def before_trading_start(context, data):
    codes = get_Ashares(_qs_day())
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert ".replace('-', '')" in out and "strftime('%Y%m%d')" in out
    ast.parse(out)
    r2 = _convert_code(out, strategy_id="test_strategy_2")
    assert r2.errors == [], r2.errors  # 幂等（已包装跳过）
    ast.parse(r2.converted_code)


def test_46_gf_date_synthesis_injected():
    """T10（gf-date-synthesis-design.md）：date=None 财务表调用 → 产物注入前一交易日
    拼接（_qs_prev_trade_day_str / QS_GF_DATE_SYNTH / 白名单表常量）；显式 date 调用
    不在调用点被改写（wrapper 内零触发）。"""
    code = '''
def initialize(context):
    pass

def before_trading_start(context, data):
    f = get_fundamentals(['600000.SS'], 'income_statement',
                         fields=['operating_revenue', 'end_date', 'publ_date'],
                         report_types='4')
    g2 = get_fundamentals(['600000.SS'], 'valuation', fields=['float_value'],
                          date='20210101')
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "_qs_prev_trade_day_str" in out
    assert "QS_GF_DATE_SYNTH" in out
    assert "_QS_GF_DATE_SYNTH_TABLES" in out
    assert "date='20210101'" in out  # 显式 date 调用点未被改写（拼接在 wrapper 内触发）
    ast.parse(out)


def test_47_local_strategy_nested_helper_and_import_whitelist():
    """T11：LOCAL-API-WHITELIST 全深度收集（2026-09-03 转换失败实证：position-view 嵌套
    `_attr` helper 与 gf 合成别名导入双双被误 BLOCK）。正反例：
    ① 嵌套 def helper 可调用、嵌套模块导入的属性调用可通过；② 真实未知 API 仍被 BLOCK（防放宽过度）。"""
    from quantstudio.strategy_compiler.validators.validate_local_strategy import validate_local_strategy
    from quantstudio.strategy_compiler.ir_nodes import StrategyIR
    src = '''
def initialize(context):
    pass

def handle_data(context, data):
    import datetime
    d = datetime.timedelta(days=1)
    return nested_only(context)

def nested_only(obj):
    def inner(x):
        return x
    return inner(obj)
'''
    ok, viols, _ = validate_local_strategy({}, StrategyIR(strategy_id="t", nodes=[]), src, "quantstudio")
    assert not any(v.rule_id == "LOCAL-API-WHITELIST" for v in viols), [v.message for v in viols]
    bad = "def initialize(context):\n    pass\n\ndef handle_data(context, data):\n    get_unknown_plat_api()\n"
    ok2, viols2, _ = validate_local_strategy({}, StrategyIR(strategy_id="t", nodes=[]), bad, "quantstudio")
    assert any(v.rule_id == "LOCAL-API-WHITELIST" and "get_unknown_plat_api" in v.message for v in viols2)


def test_48_gf_list_chunk_dispatch():
    """T12：wrapper 超限自递归分块（_QS_GF_LIST_CHUNK，平台 800 码空返回实证规避）。"""
    code = '''
def initialize(context):
    pass

def before_trading_start(context, data):
    f = get_fundamentals(['600000.SS', '000001.SZ'], 'income_statement',
                         fields=['operating_revenue', 'end_date'], report_types='4')
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "_QS_GF_LIST_CHUNK" in out
    assert "len(_secs) > _QS_GF_LIST_CHUNK" in out
    ast.parse(out)


def test_49_gf_statement_range_routing():
    """T13：报表表（income_statement 等）→ range 形态路由（_QS_GF_STATEMENT_RANGE；
    平台 date 形态单期、P1 range 实证多期）。"""
    code = '''
def initialize(context):
    pass

def before_trading_start(context, data):
    f = get_fundamentals(['600000.SS'], 'income_statement',
                         fields=['operating_revenue', 'end_date'], report_types='4')
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "_QS_GF_STATEMENT_TABLES" in out and "_QS_GF_STATEMENT_RANGE_YEARS" in out
    assert "QS_GF_STATEMENT_RANGE" in out
    ast.parse(out)


def test_50_gf_pit_publdate_epoch_ms():
    """T14：_qs_pit_filter publ_date 数值优先（epoch 毫秒 → YYYYMMDD；str(digits) 尾 0 爆表
    2456 实证修复标记：abs(_f) >= 1e11 / utcfromtimestamp）。"""
    code = '''
def initialize(context):
    pass

def before_trading_start(context, data):
    f = get_fundamentals(['600000.SS'], 'income_statement',
                         fields=['operating_revenue', 'end_date'], report_types='4')
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "abs(_f) >= 1e11" in out and "utcfromtimestamp" in out
    ast.parse(out)


def test_51_gf_range_local_retry():
    """T15/T16：year-only range 全链自愈标记——数值优先 publ 判据（abs(_f) >= 1e11）、
    report_types +8h UTC 偏移（+ 8 * 3600 * 1000）、allnan 重试（_qs_gf_value_cols_allnan）。"""
    code = '''
def initialize(context):
    pass

def before_trading_start(context, data):
    f = get_fundamentals(['600000.SS'], 'income_statement',
                         fields=['operating_revenue', 'end_date'], report_types='4')
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "_qs_gf_value_cols_allnan" in out and "QS_GF_RANGE_LOCAL_RETRY" in out
    assert "abs(_f) >= 1e11" in out and "+ 8 * 3600 * 1000" in out
    ast.parse(out)


def test_52_gf_valuation_field_map_and_probe():
    """T16：valuation 表平台列名映射 + 逆翻译 + 全 NaN 重试与列名探针（§16）。"""
    code = '''
def initialize(context):
    pass

def before_trading_start(context, data):
    v = get_fundamentals(['600000.SS'], 'valuation',
                         fields=['pe_ratio', 'float_value', 'turnover_ratio'])
'''
    result = _convert_code(code)
    assert result.errors == [], result.errors
    out = result.converted_code
    assert "_QS_VAL_PLATFORM_MAP" in out and "_QS_VAL_PLATFORM_REV" in out
    assert "_qs_gf_plat_field" in out and "QS_SHIM_VAL_COLS" in out
    assert "QS_SHIM_VAL_FALLBACK" in out and "_qs_val_map_enabled" in out
    # §17 实证版：判据 pe_ratio 缺失 + turnover 合成兜底 + circ_mv 推断版作废（不掩差异）
    assert "'pe_ratio' not in _cols" in out
    assert "QS_VAL_TRU_SYNTH" in out
    assert "'float_value': 'circ_mv'" not in out
    # §18：turnover_rate 直映（平台原生列实证）+ 合成诊断计数
    assert "'turnover_ratio': 'turnover_rate'" in out
    assert "'turnover_rate': 'turnover_ratio'" in out
    assert "vhit=%d chit=%d" in out
    ast.parse(out)


def test_44_ptrade_portability_guards_positive_negative():
    """T8（P2-1/P2-4）：校验器三条新 BLOCK 正反用例。"""
    # 正例：min_commission=0 → BLOCK PORTABILITY-COMMISSION-MIN-ZERO
    ok, viols, _ = validate_ptrade_portability(
        "def initialize(context):\n    set_commission(commission_ratio=0.0003, min_commission=0)\n")
    assert not ok
    assert any(v.rule_id == "PORTABILITY-COMMISSION-MIN-ZERO" for v in viols)
    # 反例：min_commission=5.0 → 无该 rule（也整体无 BLOCK 时需 MANY 断言——只查 rule 缺失）
    ok2, viols2, _ = validate_ptrade_portability(
        "def initialize(context):\n    set_commission(commission_ratio=0.0003, min_commission=5.0)\n")
    assert not any(v.rule_id == "PORTABILITY-COMMISSION-MIN-ZERO" for v in viols2)
    # 正例：exclude_bse kwarg → BLOCK PORTABILITY-ASHARES-EXCLUDE_BSE
    ok3, viols3, _ = validate_ptrade_portability(
        "def before_trading_start(context, data):\n    get_Ashares(exclude_bse=True)\n")
    assert not ok3
    assert any(v.rule_id == "PORTABILITY-ASHARES-EXCLUDE_BSE" for v in viols3)
    # 反例：get_Ashares() 无参 → 无该 rule
    ok4, viols4, _ = validate_ptrade_portability(
        "def before_trading_start(context, data):\n    get_Ashares()\n")
    assert not any(v.rule_id == "PORTABILITY-ASHARES-EXCLUDE_BSE" for v in viols4)
    # P2-4：set_commission 位置参数 → BLOCK PORTABILITY-COMMISSION-POSITIONAL
    ok5, viols5, _ = validate_ptrade_portability(
        "def initialize(context):\n    set_commission('ETF', 0.0001, 0)\n")
    assert not ok5
    assert any(v.rule_id == "PORTABILITY-COMMISSION-POSITIONAL" for v in viols5)
    # P2-4 反例：关键字调用 → 无 positional rule
    ok6, viols6, _ = validate_ptrade_portability(
        "def initialize(context):\n    set_commission(type='ETF', commission_ratio=0.0003, min_commission=0.5)\n")
    assert not any(v.rule_id == "PORTABILITY-COMMISSION-POSITIONAL" for v in viols6)
