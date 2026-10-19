# -*- coding: utf-8 -*-
"""P5 未知 API 门禁误报修复测试（PD-0008 附带项①；正负双测）。

根因：`preflight.call_name` 对 `X.method()` 返回**根对象名 X**（为兼容 `log.info`
归入契约表 `log` 条目）；而 `gates._local_names` 旧实现只收 函数/类名 + 赋值目标 +
import 绑定，漏掉 7 类作用域绑定 → `history.keys()`（参数）、`ch.isdigit()`（for
目标）被误报「契约表外」。

修复：补全绑定面（参数/for/with/except/walrus/推导式/解包）+ 引擎全局 `g` 豁免。
**正例必须仍被捕获**（防反向漏报）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantstudio.ptrade_deploy.gates import run_gates  # noqa: E402
from quantstudio.ptrade_deploy.preflight import load_contract  # noqa: E402

CONTRACT = load_contract()


def _unknown(src: str):
    return run_gates(src, CONTRACT).unknown_apis


# ---------------------------------------------------------------- 负例 --

def test_param_method_call_not_reported():
    """实证形态：history 为函数参数，history.keys() 是数据对象方法。"""
    src = '''
def _norm(history):
    return list(history.keys())
'''
    assert _unknown(src) == []


def test_for_target_method_not_reported():
    """实证形态：ch 为 for 目标，ch.isdigit() 是字符串方法。"""
    src = '''
def _scan(s):
    out = []
    for ch in s:
        if ch.isdigit():
            out.append(ch)
    return out
'''
    assert _unknown(src) == []


def test_with_as_and_except_methods_not_reported():
    src = '''
def _io(path):
    try:
        with open(path) as fh:
            return fh.readlines()
    except OSError as exc:
        return exc.args
'''
    assert _unknown(src) == []


def test_comprehension_and_walrus_targets_not_reported():
    src = '''
def _agg(rows):
    vals = [r.get("k") for r in rows]
    if (n := len(vals)) > 0:
        return n
    return 0
'''
    assert _unknown(src) == []


def test_unpacked_assign_target_not_reported():
    src = '''
def _f(pair):
    a, b = pair
    return a.bit_length()
'''
    assert _unknown(src) == []


def test_engine_global_g_method_not_reported():
    """g 为引擎注入全局（PTrade 运行时对象），其方法调用不是平台 API。"""
    src = '''
def after_trading_end(context, data):
    g.reset()
'''
    assert _unknown(src) == []


# ---------------------------------------------------------------- 正例 --

def test_unknown_plain_call_still_reported():
    src = '''
def handle_data(context, data):
    unknown_api_xyz("510300.SS")
'''
    names = _unknown(src)
    assert any("unknown_api_xyz" in n for n in names), names


def test_unknown_module_attribute_call_still_reported():
    """根名完全未知（非局部绑定/非豁免/非契约表）→ 仍须报（防漏报）。"""
    src = '''
def handle_data(context, data):
    unknownmod.do_stuff(1)
'''
    names = _unknown(src)
    assert any("unknownmod" in n for n in names), names


def test_local_function_exempt_but_other_unknown_still_caught():
    """本地函数不误报，同文件另一真未知仍被抓——防「整体豁免过宽」。"""
    src = '''
def _mine(x):
    return x


def handle_data(context, data):
    _mine(1)
    mystery_call(2)
'''
    names = _unknown(src)
    assert not any("_mine" in n for n in names), names
    assert any("mystery_call" in n for n in names), names
