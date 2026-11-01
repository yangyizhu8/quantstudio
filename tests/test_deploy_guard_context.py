# -*- coding: utf-8 -*-
"""PD-0016：护栏首参去冗余 + PERSIST 注入保位 docstring。

两处同源缺陷（注入位置的「上下文假设」错误）：
- D1：`_deploy_guard_pass(context, security, side)` 而调用点传
  `(被包裹函数首参, security, side)` —— 对 `submit_target_value_order(security, ...)`
  即传成 `(security, security, side)`。护栏体从不使用 context ⇒ 删形参根治。
- D2：`init.body[0:0] = restore_stmts` 把 `initialize` 的 docstring 顶下去，
  使其退化为普通字符串表达式（`__doc__` 变 None）。
"""
import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from quantstudio.ptrade_deploy import run_deploy  # noqa: E402

#: 首参**不是** context 的辅助函数（原实现会把它当 context 传进护栏）
SRC_WITH_ORDER = '''\
"""模块说明（模块 docstring，须保位）。"""


def initialize(context):
    """初始化说明（函数 docstring，须保位）。"""
    g.done = False
    set_limit_mode('UNLIMITED')


def submit_target_value_order(security, target_value):
    """首参是 security 而非 context —— 护栏实参不得受此影响。"""
    order_result = order_target_value(security, target_value)
    return order_result


def handle_data(context, data):
    if not g.done:
        submit_target_value_order('511260.SS', 10000)
        g.done = True
'''


def _cfg(tmp_path: Path) -> Path:
    src = tmp_path / "s_ptrade.py"
    src.write_text(SRC_WITH_ORDER, encoding="utf-8")
    p = tmp_path / "deploy.yaml"
    p.write_text(
        "strategy_id: t_guard\n"
        f"source: {src.as_posix()}\n"
        "execution:\n  profile: daily_eod_1450\n"
        "persistence:\n  enabled: true\n"
        f"output:\n  root: {(tmp_path / 'out').as_posix()}\n",
        encoding="utf-8")
    return p


@pytest.fixture(scope="module")
def product_text(tmp_path_factory):
    d = tmp_path_factory.mktemp("pd16")
    r = run_deploy(str(_cfg(d))).to_dict()
    assert r["status"] == "PASS", r["stages"]
    return Path(r["artifacts"]["product"]).read_text(encoding="utf-8")


def _guard_calls(tree):
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id == "_deploy_guard_pass":
            out.append(n)
    return out


# ---------------------------------------------------------------- D1 --

def test_guard_helper_signature_has_two_params(product_text):
    """D1：helper 签名 = (security, side)，不得再有 context。"""
    tree = ast.parse(product_text)
    helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "_deploy_guard_pass"]
    assert len(helpers) == 1
    names = [a.arg for a in helpers[0].args.args]
    assert names == ["security", "side"], names


def test_all_guard_calls_have_two_args(product_text):
    """D1：所有护栏调用实参个数 = 2（含首参非 context 的函数内下单点）。"""
    calls = _guard_calls(ast.parse(product_text))
    assert calls, "本 fixture 含下单调用，应有护栏"
    for c in calls:
        assert len(c.args) == 2, f"行 {c.lineno}: {ast.unparse(c)}"
        assert not c.keywords


def test_guard_not_fed_with_function_first_param(product_text):
    """D1 负向：不得出现 `(security, security, ...)` 这类伪造 context 形态。"""
    for c in _guard_calls(ast.parse(product_text)):
        dumped = ast.dump(c)
        assert "args=[Name(id='security'), Name(id='security')" not in dumped, \
            f"疑似仍按被包裹函数首参传 context: {ast.unparse(c)}"


def test_guard_checks_still_present(product_text):
    """护栏四段未削弱（停牌/涨跌停/限价快照/幂等）与拦截告警仍在。"""
    tree = ast.parse(product_text)
    helper_src = ast.unparse([n for n in tree.body if isinstance(n, ast.FunctionDef)
                              and n.name == "_deploy_guard_pass"][0])
    for key in ("get_stock_status", "check_limit", "get_snapshot", "orders_done"):
        assert key in helper_src, key
    assert "护栏拦截" in product_text
    assert "护栏参考" in product_text


# ---------------------------------------------------------------- D2 --

def test_initialize_docstring_preserved(product_text):
    """D2：initialize 首语句仍是 docstring，get_docstring 非 None。"""
    tree = ast.parse(product_text)
    init = [n for n in tree.body if isinstance(n, ast.FunctionDef)
            and n.name == "initialize"][0]
    assert isinstance(init.body[0], ast.Expr) and isinstance(init.body[0].value, ast.Constant) \
        and isinstance(init.body[0].value.value, str), \
        f"initialize 首语句不是 docstring: {ast.dump(init.body[0])[:120]}"
    assert ast.get_docstring(init) is not None
    assert "初始化说明" in ast.get_docstring(init)


def test_module_docstring_still_first(product_text):
    """回归：模块 docstring 仍在首位（注入不挤模块级 docstring）。"""
    tree = ast.parse(product_text)
    first = tree.body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
        and isinstance(first.value.value, str)
    assert ast.get_docstring(tree) is not None
