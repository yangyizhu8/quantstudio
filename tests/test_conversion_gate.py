# -*- coding: utf-8 -*-
"""转换门禁测试（2026-09-09 重写解锁版，docs/index-bar-rewrite-rule-design.md v3）。

覆盖：
- d/e：误杀面回归（内置裸名/helper/np.pd 属性/log 方法/df 方法/未知属性方法 → 零 BLOCK）
- f 系：机器门禁判定矩阵（daily+handle=SHIM / premarket BLOCK / minute BLOCK / profile 缺失 BLOCK /
  helper 链 SHIM / 收盘 run_daily SHIM / 盘前 run_daily BLOCK / time 不可解析 BLOCK / 双可达 BLOCK）
- h：高阶模式零误杀（形参调用/lambda/for/walrus）
- i：别名引用 daily → SHIM（重写解锁后）
- g：注册表一致性（local_only_symbols ∈ 已知分类 ∪ LOCAL_ONLY_PASSTHROUGH_BLOCK，常设）
"""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quantstudio.strategy_compiler.source_import import convert_source  # noqa: E402
from quantstudio.strategy_compiler import portability_rules as pr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "quantstudio-strategy-compiler"


def _convert_code(code, strategy_id="gate_test", engine_profile=None):
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"{strategy_id}.py"
        p.write_text(code, encoding="utf-8")
        return convert_source(p, engine_profile=engine_profile)


def _blocked_apis(result):
    return {a.api_name for a in result.actions if a.action_type == "BLOCK"}


API_HANDLE = (
    "def initialize(context):\n"
    "    pass\n"
    "def handle_data(context, data):\n"
    "    idx = get_index_day_bar('000001.SS', count=2, fields=['pctChg'])\n"
    "    return\n"
)


# ---- d：误杀面回归 ----
def test_d_no_false_positive_on_normal_patterns():
    code = (
        "import numpy as np\n"
        "import pandas as pd\n"
        "def _ensure_runtime_state():\n"
        "    if not hasattr(g, 'ready'):\n"
        "        g.ready = True\n"
        "def helper(x):\n"
        "    return len(x)\n"
        "def initialize(context):\n"
        "    _ensure_runtime_state()\n"
        "    set_benchmark('000300.SS')\n"
        "def handle_data(context, data):\n"
        "    _ensure_runtime_state()\n"
        "    df = pd.DataFrame()\n"
        "    arr = np.asarray([1.0, 2.0])\n"
        "    n = len(arr)\n"
        "    m = min(arr)\n"
        "    s = sorted([1, 2, 3])\n"
        "    log.info('msg %s', n)\n"
        "    log.warning('warn %s', m)\n"
        "    v = helper(df)\n"
        "    return v\n"
    )
    result = _convert_code(code, engine_profile="daily-bar-v1")
    assert _blocked_apis(result) == set(), result.errors


# ---- e：未知属性方法不 BLOCK（限定 1）----
def test_e_unknown_attribute_method_not_blocked():
    code = (
        "def initialize(context):\n"
        "    pass\n"
        "def handle_data(context, data):\n"
        "    foo.bar_unknown()\n"
        "    return\n"
    )
    result = _convert_code(code, engine_profile="daily-bar-v1")
    assert _blocked_apis(result) == set(), result.errors


# ---- f 系：机器门禁判定矩阵 ----
def test_f_daily_handle_data_shims():
    """daily-bar-v1 + handle_data → SHIM 注入（重写解锁；平台 field 契约断言）。"""
    result = _convert_code(API_HANDLE, engine_profile="daily-bar-v1")
    assert "get_index_day_bar" not in _blocked_apis(result), result.errors
    assert "def get_index_day_bar" in result.converted_code, "expect shim injected"
    # shim v2（2026-09-09 平台实跑修正）：请求本地字段（含 pctChg），由注入 wrapper 完成
    # amount->money 映射 + pctChg 剔除 + preclose 注入 + 返回侧合成 + 列名归一——shim 消费归一结果
    shim_part = result.converted_code.split("def get_index_day_bar", 1)[1]
    assert "pctChg" in shim_part, "shim must request local pctChg (wrapper synthesizes)"
    assert "'amount'" in shim_part, "shim must request local amount (wrapper maps to money)"
    assert "_qs_preclose" not in shim_part, "shim v2 must not self-synthesize (wrapper owns pctChg)"


def test_f2_premarket_reachable_blocks():
    code = (
        "def initialize(context):\n"
        "    pass\n"
        "def before_trading_start(context, data):\n"
        "    idx = get_index_day_bar('000001.SS', count=2)\n"
        "def handle_data(context, data):\n"
        "    return\n"
    )
    result = _convert_code(code, engine_profile="daily-bar-v1")
    assert "get_index_day_bar" in _blocked_apis(result), result.errors


def test_f3_minute_profile_blocks():
    result = _convert_code(API_HANDLE, engine_profile="minute-bar-v1")
    assert "get_index_day_bar" in _blocked_apis(result), result.errors


def test_f4_missing_profile_blocks():
    """profile 缺失 → BLOCK（fail-closed，禁默认 daily）。"""
    result = _convert_code(API_HANDLE, engine_profile=None)
    assert "get_index_day_bar" in _blocked_apis(result), result.errors


def test_f5_helper_chain_shims():
    """helper 间接调用（handle_data → helper → API）→ SHIM（调用图追踪）。"""
    code = (
        "def _signal_fired(context):\n"
        "    idx = get_index_day_bar('000001.SS', count=2, fields=['pctChg'])\n"
        "    return idx is not None\n"
        "def initialize(context):\n"
        "    pass\n"
        "def handle_data(context, data):\n"
        "    fired = _signal_fired(context)\n"
        "    return\n"
    )
    result = _convert_code(code, engine_profile="daily-bar-v1")
    assert "get_index_day_bar" not in _blocked_apis(result), result.errors
    assert "def get_index_day_bar" in result.converted_code


def test_f6_close_run_daily_shims():
    """收盘 run_daily（time='15:00' >= 14:55）→ SHIM。"""
    code = (
        "def _close_check(context):\n"
        "    idx = get_index_day_bar('000001.SS', count=2)\n"
        "    return\n"
        "def initialize(context):\n"
        "    run_daily(context, _close_check, time='15:00')\n"
        "def handle_data(context, data):\n"
        "    return\n"
    )
    result = _convert_code(code, engine_profile="daily-bar-v1")
    assert "get_index_day_bar" not in _blocked_apis(result), result.errors


def test_f7_premarket_run_daily_blocks():
    """盘前 run_daily（time='09:31' < 14:55）→ BLOCK。"""
    code = (
        "def _am_check(context):\n"
        "    idx = get_index_day_bar('000001.SS', count=2)\n"
        "    return\n"
        "def initialize(context):\n"
        "    run_daily(context, _am_check, time='09:31')\n"
        "def handle_data(context, data):\n"
        "    return\n"
    )
    result = _convert_code(code, engine_profile="daily-bar-v1")
    assert "get_index_day_bar" in _blocked_apis(result), result.errors


def test_f8_unparseable_run_daily_blocks():
    """run_daily time 非字面量（无法静态解析）→ BLOCK。"""
    code = (
        "def _dyn_check(context):\n"
        "    idx = get_index_day_bar('000001.SS', count=2)\n"
        "    return\n"
        "def initialize(context):\n"
        "    run_daily(context, _dyn_check, time=DYNAMIC_TIME)\n"
        "def handle_data(context, data):\n"
        "    return\n"
    )
    result = _convert_code(code, engine_profile="daily-bar-v1")
    assert "get_index_day_bar" in _blocked_apis(result), result.errors


def test_f9_dual_reach_blocks():
    """双路径可达（handle_data + before_trading_start 经共享 helper）→ BLOCK。"""
    code = (
        "def _shared_check(context):\n"
        "    idx = get_index_day_bar('000001.SS', count=2)\n"
        "    return\n"
        "def initialize(context):\n"
        "    pass\n"
        "def before_trading_start(context, data):\n"
        "    _shared_check(context)\n"
        "def handle_data(context, data):\n"
        "    _shared_check(context)\n"
        "    return\n"
    )
    result = _convert_code(code, engine_profile="daily-bar-v1")
    assert "get_index_day_bar" in _blocked_apis(result), result.errors


# ---- h：高阶模式零误杀（终审补强 1）----
def test_h_higher_order_patterns_not_blocked():
    code = (
        "def apply(f, x):\n"
        "    return f(x)\n"
        "def initialize(context):\n"
        "    pass\n"
        "def handle_data(context, data):\n"
        "    def local_fn(y):\n"
        "        return y + 1\n"
        "    lam = lambda z: z * 2\n"
        "    r1 = apply(local_fn, 3)\n"
        "    r2 = apply(lam, 4)\n"
        "    if (n := len([1, 2])) > 0:\n"
        "        r3 = n\n"
        "    try:\n"
        "        raise ValueError('x')\n"
        "    except ValueError as exc:\n"
        "        r4 = str(exc)\n"
        "    return r1 + r2 + r3\n"
    )
    result = _convert_code(code, engine_profile="daily-bar-v1")
    assert _blocked_apis(result) == set(), result.errors


# ---- i：别名引用 daily → SHIM（引用级拦截已由 DENY_SHIM 承接）----
def test_i_alias_ref_daily_shims():
    code = (
        "def initialize(context):\n"
        "    pass\n"
        "def handle_data(context, data):\n"
        "    f = get_index_day_bar\n"
        "    idx = f('000001.SS', count=2)\n"
        "    return\n"
    )
    result = _convert_code(code, engine_profile="daily-bar-v1")
    assert "get_index_day_bar" not in _blocked_apis(result), result.errors


# ---- g：注册表一致性常设测试 ----
def test_g_local_only_registry_consistency():
    sig = json.loads((SKILL_DIR / "references" / "ptrade-api-signatures.json").read_text(encoding="utf-8"))
    los = set(sig.get("local_only_symbols", []))
    si_src = (ROOT / "quantstudio" / "strategy_compiler" / "source_import.py").read_text(encoding="utf-8")
    m = re.search(r"_BLOCK_API_NO_FUNCTION = frozenset\((.*?)\)", si_src, re.S)
    block_no_fn = set(re.findall(r"\"([^\"]+)\"", m.group(1))) if m else set()
    known = (set(pr.DENY_REMOVE) | set(pr.DENY_SHIM) | set(pr.PTRADE_REGISTERED_WARN)
             | set(pr.INJECTED_WRAPPER_NAMES) | set(pr.MYTT_FUNCTIONS)
             | set(pr.ASHARE_RULES_FUNCTIONS) | set(pr.LOCAL_ONLY_PASSTHROUGH_BLOCK)
             | block_no_fn)
    missing = los - known
    assert not missing, f"local_only_symbols 未在转换器分类中处理: {sorted(missing)}"
