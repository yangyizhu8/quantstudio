# -*- coding: utf-8 -*-
"""PD-0017：qs-deploy 交易态四缺陷批回归固件。

四个缺陷（2026-11-02 模拟盘 14:50 实跑归因）：

- **A** `run_daily(context, func, time)` 的 func 签名是 `Callable[[Context], None]`
  （Context7 `/kay-ou/ptradeapi`）—— **只传 context**。挂载包装
  `_deploy_daily_exec(ctx, data=None)` 的 data 恒 None ⇒ 源策略在 handle_data 体内
  `security in data` 抛 TypeError 被 except 吞掉 ⇒ 落到持仓价 ⇒ 首日 0.0 ⇒ 整轮调仓
  中止（四象限ETF 512890.SS 实测）。修法：注入无状态惰性代理作为 data 默认值。
- **B** `get_snapshot(security)` 返回**二层嵌套** `{code: {last_px: ...}}`；护栏原实现
  `_snap.get('last_px')` 在外层找字段名 ⇒ 恒 None。修法：一层/二层双形态兼容。
- **C** 拆单 k 笔只把末笔 `_ids[-1]` 交回策略 ⇒ `g.pending_order_audit` 漏记 k-1 笔 ⇒
  `audit_pending_orders` 永不核验（当时 15 笔挂单 14 笔未审计）。修法：shim 全量登记。
- **D** 未成交挂单无处置 ⇒ 跨日双买风险。修法：陈旧挂单巡检（默认只告警）。
- **E′（实施中新增发现）** 幂等台账 `orders_done` 存于 `g.state`、随 pickle 跨日持久化、
  全文无清空逻辑 ⇒ **次日首笔下单即被判「当日重复下单」拦死**。修法：恢复后清空。
"""

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from quantstudio.ptrade_deploy import run_deploy  # noqa: E402
from quantstudio.ptrade_deploy import gates as gates_mod  # noqa: E402

#: handle_data 体内**按 data 取价**（镜像四象限 ETF 的 get_market_price 形态）
SRC_PRICE_BY_DATA = '''\
"""PD-0017 A 固件：handle_data 体内按 data 取价（交易态 data=None 则整链失效）。"""


def initialize(context):
    g.done = False


def get_market_price(security, data, position=None):
    try:
        if security in data:
            bar = data[security]
            try:
                px = float(bar['close'])
            except Exception:
                px = float(bar.close)
            if px > 0:
                return px
    except Exception:
        pass
    if position is not None:
        px2 = float(getattr(position, 'last_sale_price', 0) or 0)
        if px2 > 0:
            return px2
    return 0.0


def handle_data(context, data):
    px = get_market_price('512890.SS', data)
    if px > 0:
        order_target_value('512890.SS', 500000)
    g.done = True
'''


def _write_cfg(tmp_path: Path, extra: str = "") -> Path:
    src = tmp_path / "s_ptrade.py"
    src.write_text(SRC_PRICE_BY_DATA, encoding="utf-8")
    p = tmp_path / "deploy.yaml"
    p.write_text(
        "strategy_id: t_pd0017\n"
        f"source: {src.as_posix()}\n"
        "execution:\n  profile: daily_eod_1450\n"
        "persistence:\n  enabled: true\n"
        + extra
        + f"output:\n  root: {(tmp_path / 'out').as_posix()}\n",
        encoding="utf-8")
    return p


@pytest.fixture(scope="module")
def product_text(tmp_path_factory):
    d = tmp_path_factory.mktemp("pd0017")
    r = run_deploy(str(_write_cfg(d))).to_dict()
    assert r["status"] == "PASS", r["stages"]
    return Path(r["artifacts"]["product"]).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def deploy_result(tmp_path_factory):
    d = tmp_path_factory.mktemp("pd0017r")
    return run_deploy(str(_write_cfg(d))).to_dict()


# ================================================================ A：data 代理 ==

def test_A_product_has_data_proxy_default(product_text):
    """A：挂载包装的 data 默认值必须是 `_qs_data_proxy()`（不再是 None）。"""
    tree = ast.parse(product_text)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)
           and n.name == "_deploy_daily_exec"]
    assert len(fns) == 1, "应恰有一个挂载包装"
    fn = fns[0]
    names = [a.arg for a in fn.args.args]
    assert names == ["context", "data"], names
    assert len(fn.args.defaults) == 1, "data 必须有默认值"
    dflt = fn.args.defaults[0]
    assert isinstance(dflt, ast.Call), ast.dump(dflt)
    assert getattr(dflt.func, "id", "") == "_qs_data_proxy", ast.unparse(dflt)


def test_A_product_has_proxy_definitions(product_text):
    """A：代理类与工厂函数必须在位（否则默认值求值即 NameError）。"""
    for name in ("_QSDataItem", "_QSDataProxy", "_qs_data_proxy"):
        assert f"def {name}(" in product_text or f"class {name}(" in product_text, name


def test_A_proxy_defined_before_wrapper(product_text):
    """A：代理必须在挂载包装之前定义（默认值在 def 时求值）。"""
    tree = ast.parse(product_text)
    idxs = {}
    for i, n in enumerate(tree.body):
        if isinstance(n, ast.FunctionDef) and n.name in ("_qs_data_proxy",
                                                         "_deploy_daily_exec"):
            idxs[n.name] = i
    assert idxs["_qs_data_proxy"] < idxs["_deploy_daily_exec"], idxs


def test_A_p4_equivalence_pass(deploy_result):
    """A：注入新标记块后 P4 等价性必须仍 PASS（DATA_PROXY 块整体剔除）。"""
    stages = deploy_result["stages"]
    p4 = {k: v for k, v in stages.items() if str(k).startswith("P4")}
    assert p4, list(stages)
    assert all(v["status"] == "PASS" for v in p4.values()), p4


def _exec_shim():
    """把订单 shim 模板渲染成可执行命名空间（假平台 API）。"""
    import importlib
    si = importlib.import_module("quantstudio.strategy_compiler.source_import")
    src = si._QS_ORDER_SPLIT_EXT.format(marker="")
    return src


def _proxy_ns(price_map):
    """构造只含代理所需的命名空间（假 _qs_px_exec）。

    templates/ 无 `__init__.py`（transformer 按路径加载），故此处同样按路径 exec。
    """
    tpl_file = REPO / "quantstudio" / "ptrade_deploy" / "templates" / "data_proxy_template.py"
    ns0 = {}
    exec(compile(tpl_file.read_text(encoding="utf-8"), str(tpl_file), "exec"), ns0)
    ns = {"_qs_px_exec": lambda sec: float(price_map.get(str(sec), 0.0))}
    exec(compile(ns0["DATA_PROXY_TEMPLATE"], "<proxy>", "exec"), ns)
    ns["_price_map"] = price_map
    return ns


def test_A_proxy_contains_and_getitem():
    """A 语义：`security in data` / `data[sec]['close']` / `data[sec].close` 三形态。"""
    ns = _proxy_ns({"512890.SS": 1.234})
    data = ns["_qs_data_proxy"]()
    assert "512890.SS" in data
    assert "000001.SS" not in data
    assert abs(data["512890.SS"]["close"] - 1.234) < 1e-9
    assert abs(data["512890.SS"].close - 1.234) < 1e-9
    assert abs(data.get("512890.SS")["last_px"] - 1.234) < 1e-9


def test_A_proxy_degrade_matches_old_behavior():
    """A 降级：取不到价 ⇒ close=0.0 且 `in` 为 False（与 data=None 时同构回落）。"""
    ns = _proxy_ns({})
    data = ns["_qs_data_proxy"]()
    assert "512890.SS" not in data
    assert data["512890.SS"]["close"] == 0.0


def test_A_proxy_is_stateless():
    """A 硬约束：代理不得缓存价格（默认参数在 def 时求值一次并被共享）。"""
    price_map = {"512890.SS": 1.0}
    ns = _proxy_ns(price_map)
    data = ns["_qs_data_proxy"]()
    assert abs(data["512890.SS"]["close"] - 1.0) < 1e-9
    price_map["512890.SS"] = 2.0          # 底层价格变化
    assert abs(data["512890.SS"]["close"] - 2.0) < 1e-9, "代理缓存了旧价"


def test_A_proxy_does_not_fake_bar_universe():
    """A 负向：代理不得伪造 bar 全集（len/keys 为空，避免策略误判全集可用）。"""
    ns = _proxy_ns({"512890.SS": 1.0})
    data = ns["_qs_data_proxy"]()
    assert len(data) == 0
    assert list(data.keys()) == []


# ================================================================ B：快照二层 ==

def test_B_snapshot_two_level_compat(product_text):
    """B：护栏快照段必须兼容 `{code: {last_px: ...}}` 二层嵌套。"""
    assert "_inner = _snap.get(security)" in product_text
    assert "_inner.get('last_px')" in product_text
    # 旧缺陷形态（一层直接取字段名）不得残留
    assert "if isinstance(_snap, dict) else _snap" not in product_text


def test_B_snapshot_still_one_level_compat(product_text):
    """B 兼容而非替换：一层形态（异常平台版本）仍可取到。"""
    assert "_snap.get('last_px')" in product_text


# ================================================================ C：拆单全量登记 ==

def test_C_shim_defines_register_helper():
    """C：拆单 shim 必须提供全量登记辅助函数（属 import 管线，非 deploy 产物）。"""
    assert "def _qs_register_split_order(" in _exec_shim()


def test_C_all_split_paths_register():
    """C：三条拆单路径（order_target_value / order / order_value）都要登记。"""
    tree = ast.parse(_exec_shim())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_qs_register_split_order"]
    assert len(calls) >= 4, f"登记调用点过少：{len(calls)}"


def test_C_registration_records_all_split_orders():
    """C 语义：k>1 时 `g.pending_order_audit` 与 `g.state['qs_cur_orders']` 都要有 k 条。"""
    src = _exec_shim()
    issued = []

    class _G(object):
        pass

    g = _G()
    g.state = {"version": 1}
    g.pending_order_audit = {}

    ns = {"g": g, "log": type("L", (), {"warning": staticmethod(lambda *a, **k: None),
                                       "info": staticmethod(lambda *a, **k: None)})()}

    def _order_target_value(security, value, *a, **k):
        return "REAL"

    def _order(security, amount, *a, **k):
        oid = "oid-%d" % (len(issued) + 1)
        issued.append((security, amount, oid))
        return oid

    ns.update({
        "order_target_value": _order_target_value,
        "order_value": lambda *a, **k: "REAL",
        "order_percent": lambda *a, **k: "REAL",
        "order_target_percent": lambda *a, **k: "REAL",
        "order": _order,
        "get_history": lambda **k: None,
        "get_positions": lambda: [],
        "get_position": lambda s: None,
        "get_stock_status": lambda *a, **k: {},
        "check_limit": lambda *a, **k: 0,
        "is_trade": lambda: True,
        "get_snapshot": lambda s: {},
    })
    exec(compile(src, "<shim>", "exec"), ns)

    # 目标市值 2,000,000 / 价 10.0 = 200,000 股 ⇒ k = ceil(200000/49000) = 5 笔
    ns["_qs_px_exec"] = lambda s: 10.0
    ns["_qs_last_close_lookup"] = lambda s: 10.0
    ns["_qs_pos_amount"] = lambda s: 0
    ns["_qs_cash_avail"] = lambda: 10_000_000.0

    ret = ns["order_target_value"]("512890.SS", 2_000_000)

    assert len(issued) >= 2, f"未触发拆单：{issued}"
    assert ret == issued[-1][2], "返回值必须是末笔（策略语义不得改变）"
    assert len(g.pending_order_audit) == len(issued), \
        f"台账 {len(g.pending_order_audit)} 条 ≠ 委托 {len(issued)} 笔"
    assert len(g.state["qs_cur_orders"]) == len(issued), g.state["qs_cur_orders"]
    for oid in g.pending_order_audit:
        assert g.pending_order_audit[oid].get("stage") == "split"


def test_C_registration_survives_missing_g():
    """C 鲁棒：无 `g` / 无台账时静默跳过，绝不抛异常影响下单。"""
    src = _exec_shim()
    ns = {
        "log": type("L", (), {"warning": staticmethod(lambda *a, **k: None),
                              "info": staticmethod(lambda *a, **k: None)})(),
        "order_target_value": lambda *a, **k: "REAL",
        "order_value": lambda *a, **k: "REAL",
        "order_percent": lambda *a, **k: "REAL",
        "order_target_percent": lambda *a, **k: "REAL",
        "order": lambda s, a, *x, **k: "oid",
        "get_history": lambda **k: None,
        "get_positions": lambda: [],
        "get_position": lambda s: None,
    }
    exec(compile(src, "<shim>", "exec"), ns)
    ns["_qs_px_exec"] = lambda s: 10.0
    ns["_qs_last_close_lookup"] = lambda s: 10.0
    ns["_qs_pos_amount"] = lambda s: 0
    ns["_qs_cash_avail"] = lambda: 10_000_000.0
    assert ns["_qs_register_split_order"]("X", "oid-1") is None
    assert ns["order_target_value"]("X", 2_000_000) == "oid"


# ================================================================ D：陈旧挂单巡检 ==

def test_D_stale_check_present_by_default(product_text):
    """D：陈旧挂单巡检默认开启（guards.stale_order_check 默认 true）。"""
    assert "qs_prev_orders" in product_text
    assert "陈旧挂单巡检" in product_text


def test_D_cancel_disabled_by_default(product_text):
    """D 不可逆闸门：撤单默认关闭 ⇒ 段内条件为字面量 False。"""
    assert "if False:" in product_text
    assert "cancel_order(" in product_text, "结构上应保留撤单路径（配置可开）"


def test_D_roll_hook_in_persist(product_text):
    """D：落盘时把 qs_cur_orders 滚动进 qs_prev_orders（每日一次的天然钩子）。"""
    assert "qs_prev_orders" in product_text
    assert "_q_roll" in product_text


def test_D_stale_check_can_be_disabled(tmp_path_factory):
    """D：`guards.stale_order_check: false` 时段落整体缺席。"""
    d = tmp_path_factory.mktemp("pd0017d0")
    cfg = _write_cfg(d, "guards:\n  stale_order_check: false\n")
    r = run_deploy(str(cfg)).to_dict()
    assert r["status"] == "PASS", r["stages"]
    txt = Path(r["artifacts"]["product"]).read_text(encoding="utf-8")
    assert "陈旧挂单巡检" not in txt
    assert "qs_prev_orders" not in txt


def test_D_cancel_can_be_enabled(tmp_path_factory):
    """D：显式开启 `cancel_stale_orders: true` ⇒ 段内条件为字面量 True。"""
    d = tmp_path_factory.mktemp("pd0017d1")
    cfg = _write_cfg(d, "guards:\n  cancel_stale_orders: true\n")
    r = run_deploy(str(cfg)).to_dict()
    assert r["status"] == "PASS", r["stages"]
    txt = Path(r["artifacts"]["product"]).read_text(encoding="utf-8")
    assert "if True:" in txt


# ======================================================== E′：幂等台账跨日清空 ==

def test_E_idempotency_reset_in_product(product_text):
    """E′：initialize 恢复后必须清空 orders_done（否则次日首笔即被拦）。"""
    assert "orders_done" in product_text
    idx_restore = product_text.find("g.state = _saved_state")
    idx_reset = product_text.find("g.state['orders_done'] = []")
    assert idx_restore >= 0 and idx_reset > idx_restore, (idx_restore, idx_reset)


def test_E_idempotency_reset_semantics():
    """E′ 语义：还原跨日状态后 orders_done 为空，护栏不再误拦。"""
    f = REPO / "quantstudio" / "ptrade_deploy" / "templates" / "persistence_template.py"
    ns0 = {}
    exec(compile(f.read_text(encoding="utf-8"), str(f), "exec"), ns0)
    src = (ns0["PERSIST_RESTORE_TEMPLATE"]
           .replace("__STATE_FILE__", repr("x_state.pkl"))
           .replace("__STATE_VERSION__", "1"))

    class _G(object):
        pass

    g = _G()
    g.state = {"version": 1, "orders_done": [["512890.SS", "auto"]]}
    ns = {"g": g, "pickle": __import__("pickle"),
          "log": type("L", (), {"warning": staticmethod(lambda *a, **k: None)})(),
          "_PD_NOTEBOOK": ""}
    # 只执行恢复模板的「归一化后段」——避免依赖真实 pickle 文件时可复现同一断言
    exec(compile(src, "<restore>", "exec"), ns)
    assert ns["g"].state.get("orders_done") == []


# ============================================== 门禁：缺代理必须被拦（回哺钩子）==

def test_gate_data_proxy_policy_fails_without_proxy():
    """门禁回哺：产物缺 `_qs_data_proxy()` ⇒ P5 `data_proxy_policy` FAIL。"""
    bad = (
        "def _qs_capture_ctx(context):\n"
        "    pass\n"
        "\n"
        "def _deploy_daily_exec(context, data=None):\n"
        "    pass\n"
    )
    res = gates_mod.run_gates(bad, {"api": {}})
    ck = {c.name: c for c in res.checks}
    assert "data_proxy_policy" in ck, list(ck)
    assert ck["data_proxy_policy"].status == "FAIL"
    assert res.passed is False


def test_gate_data_proxy_policy_passes_with_proxy():
    """门禁回哺（正向）：代理在位时该检查 PASS。"""
    good = (
        "class _QSDataProxy(object):\n"
        "    pass\n"
        "\n"
        "def _qs_data_proxy():\n"
        "    return _QSDataProxy()\n"
        "\n"
        "def _deploy_daily_exec(context, data=_qs_data_proxy()):\n"
        "    pass\n"
    )
    res = gates_mod.run_gates(good, {"api": {}})
    ck = {c.name: c for c in res.checks}
    assert ck["data_proxy_policy"].status == "PASS", ck["data_proxy_policy"].findings
