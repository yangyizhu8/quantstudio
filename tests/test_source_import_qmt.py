"""tests for quantstudio.strategy_compiler.source_import_qmt（M2b 块1/块2/块3）。

覆盖面（任务书 E 五组 + 1 项任务 C 交叉锚点）：
1. 正例：最小 PTrade 风格源码经 QmtSourceConverter.convert 后的产物形态断言
   （AST 合法 / 首行 #coding:gbk / init+handlebar / 三视图注入 / .SS→.SH 归一）；
2. 负例·编码：产物含 gbk 不可编码字符 → encode('gbk') 必须失败（fail-closed，
   不静默替换——写盘点 _write_qmt_product 的语义前提）；
3. 负例·未定义裸名：产物 AST 检查 context/g/data/log 引用必须在产物内有定义
   （防裸引用残留）；
4. 负例·DENY 剥除：set_benchmark/set_commission 调用消失 + QS_QMT_DENY_REMOVED
   审计行在位；
5. 负例·生命周期缺件：源无 handle_data（S3 形态）→ 不报错（不得误杀）；
6. （附加）任务 C 交叉锚点：正例产物过 validate_qmt_portability 全绿——
   QMT 白名单面与 source 注入面逐一对应的回归锚点。

运行（由验收方执行）：repo 根目录 `pytest tests/test_source_import_qmt.py`。
无外部数据依赖（不触 DB / 不写盘；QmtSourceConverter 直取源码文本，
strategy_path=None → 不解析 design metadata）。
"""

import ast
import re

import pytest

from quantstudio.strategy_compiler.source_import_qmt import (
    QmtSourceConverter,
    QS_QMT_PRODUCT_PHYSICAL_ENCODING,
)

# 最小 PTrade 风格源码：生命周期双件 + g. 赋值 + context.portfolio.total_value +
# log.info + get_history 调用 + .SS 后缀证券代码字面量（归一面）。
_POSITIVE_SOURCE = (
    "def initialize(ctx):\n"
    "    g.pool = ['600000.SS']\n"
    "    log.info('init')\n"
    "\n"
    "\n"
    "def handle_data(ctx, data):\n"
    "    hist = get_history('600000.SS', 5, '1d', ['close'])\n"
    "    total = context.portfolio.total_value\n"
    "    log.info('total=%s' % total)\n"
)


def _defined_names(tree: ast.AST) -> set:
    """产物内全部绑定名（与 source_import_qmt._collect_defined_names 同配方）。"""
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                defined.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                defined.add(a.asname or a.name)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
    return defined


def _bare_called_names(tree: ast.AST) -> set:
    return {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


# ---------------------------------------------------------------------------
# 1. 正例
# ---------------------------------------------------------------------------

def test_positive_minimal_ptrade_source_converts():
    result = QmtSourceConverter(_POSITIVE_SOURCE).convert()
    assert result.errors == []
    code = result.converted_code

    # 产物语法合法（AST 可解析）
    ast.parse(code)

    # 首行 PEP 263 gbk 声明
    first_line = code.splitlines()[0]
    assert re.match(r"^#.*coding[:=][ \t]*gbk", first_line)
    assert first_line.strip() == "#coding:gbk"

    # QMT innerApi 生命周期在位
    assert "def init(" in code
    assert "def handlebar(" in code

    # 机制①视图注入：context / data / log
    assert "_qs_context_view" in code
    assert "_qs_data_view" in code
    assert "_qs_log_view" in code  # log 视图（模块级成品）

    # (d) 证券代码归一：.SS → .SH
    assert "600000.SH" in code
    assert "600000.SS" not in code

    # 无 gbk 外字符时产物可整体物理编码（写盘点 fail-closed 的对偶正例）
    assert QS_QMT_PRODUCT_PHYSICAL_ENCODING == "gbk"
    data = code.encode("gbk")
    assert data.startswith(b"#coding:gbk")


# ---------------------------------------------------------------------------
# 2. 负例·编码（fail-closed）
# ---------------------------------------------------------------------------

def test_negative_gbk_fail_closed():
    # 源含 gbk 不可编码字符（emoji 不在 GBK 码表内）→ 产物保留原字符
    src = _POSITIVE_SOURCE.replace("log.info('init')", "log.info('启动\U0001F600')")
    result = QmtSourceConverter(src).convert()
    assert result.errors == []
    assert "\U0001F600" in result.converted_code

    # fail-closed 语义：物理编码必须失败，不静默替换（写盘点据此 BLOCK）
    assert QS_QMT_PRODUCT_PHYSICAL_ENCODING == "gbk"
    with pytest.raises(UnicodeEncodeError):
        result.converted_code.encode("gbk")


# ---------------------------------------------------------------------------
# 3. 负例·未定义裸名（防裸引用残留）
# ---------------------------------------------------------------------------

def test_negative_no_undefined_bare_alias_refs():
    result = QmtSourceConverter(_POSITIVE_SOURCE).convert()
    assert result.errors == []
    tree = ast.parse(result.converted_code)
    defined = _defined_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in ("context", "g", "data", "log"):
                assert node.id in defined, (
                    "bare reference %r not defined in product (line %s)"
                    % (node.id, getattr(node, "lineno", "?"))
                )


# ---------------------------------------------------------------------------
# 4. 负例·DENY 剥除
# ---------------------------------------------------------------------------

_DENY_SOURCE = (
    "def initialize(ctx):\n"
    "    set_benchmark('000300.SH')\n"
    "    set_commission(commission_ratio=0.0003)\n"
    "\n"
    "\n"
    "def handle_data(ctx, data):\n"
    "    log.info('tick')\n"
)


def test_negative_deny_stripped_with_audit_line():
    result = QmtSourceConverter(_DENY_SOURCE).convert()
    assert result.errors == []
    code = result.converted_code

    # 审计行在位
    assert "QS_QMT_DENY_REMOVED: set_benchmark" in code
    assert "QS_QMT_DENY_REMOVED: set_commission" in code

    # 调用消失（AST 级：裸名调用集合不含 DENY 名——print 审计行内的名字是字符串
    # 常量，不构成调用）
    called = _bare_called_names(ast.parse(code))
    assert "set_benchmark" not in called
    assert "set_commission" not in called


# ---------------------------------------------------------------------------
# 5. 负例·生命周期缺件（S3 形态不误杀）
# ---------------------------------------------------------------------------

def test_negative_lifecycle_missing_handle_data_not_fatal():
    src = (
        "def initialize(ctx):\n"
        "    g.ready = True\n"
        "    log.info('ready')\n"
    )
    result = QmtSourceConverter(src).convert()
    assert result.errors == []  # S3 缺件不报错（不得误杀）
    code = result.converted_code
    ast.parse(code)
    assert "def init(" in code


# ---------------------------------------------------------------------------
# 6.（附加）任务 C 交叉锚点：正例产物过 QMT 白名单面校验
# ---------------------------------------------------------------------------

def test_converted_product_passes_qmt_portability_whitelist():
    from quantstudio.strategy_compiler.portability_rules import validate_qmt_portability
    result = QmtSourceConverter(_POSITIVE_SOURCE).convert()
    assert result.errors == []
    ok, violations, _warnings = validate_qmt_portability(result.converted_code)
    assert ok, "QMT whitelist must cover the source-path injected surface: %r" % (
        [str(v) for v in violations],)
