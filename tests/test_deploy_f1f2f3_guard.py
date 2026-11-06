# -*- coding: utf-8 -*-
"""PD-0018：护栏幂等自撞（F1/F2）＋拆单现金累计与受理核验（F3）回归固件。

五个负向对照（对应 2026-11-06 14:50 模拟盘实跑失败态，方案件
docs/ptrade-deploy-pd0018-guard-idempotency-cash-repair-design-20261106.md §四）：

- **N1** 幂等拦截/子委托失败分支不得抛 `UnboundLocalError`（11-06 全球资产轮动
  加仓 8/8 全灭形态：`_ids.append(_id2)` 在 else 分支引用未赋值变量）；
- **N2** 拆单 k>1 时第 2..k 笔不得被自我拦截（513500 拆 4 笔、循环第 2 笔即被
  幂等台账自撞的形态；修复后实际提交笔数 == 拆单笔数）；
- **N3** 策略直接调 `order()` 同标的同方向第二次 ⇒ 仍被幂等拦（order() 路径
  补幂等的正证——2026-11-06 前该路径完全无幂等保护）；
- **N4** 逻辑层重复下单（跨调用、同标的同方向）⇒ 仍被拦（既有语义不放宽）；
- **N5** 平台拒单（桩 `get_order` 返回 None / status=废单）⇒ 部分失败分列可见，
  且不出现「提交状态=成功」（11-06 四象限静默少建 ≈649 万、框架却报成功形态）。

另附正向/结构性对照：护栏幂等段只读化（不再登记）、F6 `str(status)` 归一
（int/str 双版本终态兼容）、shim 内无裸订单家族调用（护栏不可包裹 ⇒ 自撞结构
不可再生）、产物级可观测行在位。
"""

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import quantstudio.strategy_compiler.source_import as si  # noqa: E402
from quantstudio.ptrade_deploy import run_deploy  # noqa: E402


# ---------------------------------------------------------------- 桩环境 --

class _Log(object):
    """收集 warning/info（模板侧 % 格式化在调用方完成或传参元组）。"""

    def __init__(self):
        self.warnings = []
        self.infos = []

    def _fmt(self, msg, a):
        try:
            return msg % a if a else msg
        except Exception:
            return msg

    def warning(self, msg, *a):
        self.warnings.append(self._fmt(msg, a))

    def info(self, msg, *a):
        self.infos.append(self._fmt(msg, a))

    def all_msgs(self):
        return self.warnings + self.infos


class _G(object):
    pass


def _shim_ns(price=10.0, cash=10_000_000.0, order_impl=None,
             get_order_impl=None, with_g=True):
    """exec 渲染后的 _QS_ORDER_SPLIT_EXT → 命名空间（假平台 API，pd0017 同风格）。

    - order_impl(security, amount) -> oid|None：桩原生 order（None = 平台拒单/异常）；
    - get_order_impl(oid)：受理核验桩（None / status 废单对象等）；
    - with_g=False：复现无 g 命名空间的 fail-open 约束（compliance/pd12 同款）。"""
    log = _Log()
    issued = []

    def _order(security, amount, *a, **k):
        oid = order_impl(security, amount) if order_impl is not None \
            else "oid-%d" % (len(issued) + 1)
        issued.append((security, amount, oid))
        return oid

    ns = {
        "order_target_value": lambda *a, **k: "REAL_T",
        "order": _order,
        "order_value": lambda *a, **k: "REAL_V",
        "order_percent": lambda *a, **k: "REAL",
        "order_target_percent": lambda *a, **k: "REAL",
        "get_history": lambda *a, **k: None,
        "get_positions": lambda: [],
        "get_position": lambda s: None,
        "get_stock_status": lambda *a, **k: {},
        "log": log,
    }
    if get_order_impl is not None:
        ns["get_order"] = get_order_impl
    if with_g:
        g = _G()
        g.state = {"version": 1}
        g.pending_order_audit = {}
        ns["g"] = g
    exec(compile(si._QS_ORDER_SPLIT_EXT.format(marker=""), "<shim-pd0018>", "exec"), ns)
    ns["_log"] = log
    ns["_issued"] = issued
    # 桩注入（exec 后覆写，pd0017 同法）
    ns["_qs_px_exec"] = lambda s: price
    ns["_qs_last_close_lookup"] = lambda s: price
    ns["_qs_pos_amount"] = lambda s: 0
    ns["_qs_cash_avail"] = lambda: cash
    return ns


#: N2 场景参数（镜像 11-06 实盘：513500 目标 499,729.63 ÷ 2.581 ≈ 193,695 股 ⇒ 拆 4 笔）
N2_CODE = "513500.SS"
N2_VALUE = 499_729.63
N2_PX = 2.581


# ================================================================ N1 ==

def test_N1_fail_branch_no_unbound_local_error():
    """N1：子委托全失败（order 返回 None）不得抛 UnboundLocalError，返回可判读 None。

    11-06 失败态：护栏 else 分支下 `_id2` 未赋值即 append ⇒ 8/8 全灭。修复后
    失败只记录（QS_SPLIT_MISS）不 append，调用正常返回 None。"""
    ns = _shim_ns(order_impl=lambda s, a: None)
    ret = ns["order_target_value"](N2_CODE, 2_000_000)   # 不抛即 N1 半证
    assert ret is None, "全失败分支应返回可判读 None"
    assert any("QS_SPLIT_MISS" in w for w in ns["_log"].warnings), \
        "失败子委托必须留痕（不静默）"
    assert ns["_QS_SPLIT_DEPTH"] == 0, "深度计数必须归零（finally 兜底）"
    assert ns["g"].state.get("qs_cur_orders") in (None, [])   # 失败无登记


def test_N1_no_bare_order_call_in_shim():
    """N1 结构半证：shim 内不得再有裸订单家族调用（护栏可包裹点清零）。

    拆单循环逐笔调用全部走 `_QSOrderRefState.order_wrapped`（两层属性调用，
    transformer.call_name 不识别 ⇒ 不包护栏）⇒「else 分支未赋值」形态结构上
    不可再生。"""
    tree = ast.parse(si._QS_ORDER_SPLIT_EXT.format(marker=""))
    fam = {"order", "order_target", "order_value", "order_target_value", "order_market"}
    bare = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id in fam]
    assert bare == [], f"shim 内出现裸订单调用（会被护栏包裹）: {bare}"


# ================================================================ N2 ==

def test_N2_split_all_segments_submitted():
    """N2：拆 4 笔 ⇒ 4 笔全部提交（第 2..k 笔不被幂等台账自我拦截）。"""
    ns = _shim_ns(price=N2_PX)
    ret = ns["order_target_value"](N2_CODE, N2_VALUE)
    # 499729.63/2.581 ≈ 193,695 股 ⇒ ceil(193695/49000)=4 段
    assert len(ns["_issued"]) == 4, \
        f"提交笔数 {len(ns['_issued'])} ≠ 拆单笔数 4（第 2..k 笔被自我拦截）: {ns['_issued']}"
    assert all(amt <= 49000 for _, amt, _ in ns["_issued"])
    assert ret == ns["_issued"][-1][2], "返回值必须是末笔（策略语义不变）"
    # 逻辑单级幂等只登记一次；无 duplicate 告警
    assert ns["g"].state["orders_done"] == [[N2_CODE, "auto"]]
    assert not any("duplicate_idempotent" in w for w in ns["_log"].warnings)
    assert ns["_QS_SPLIT_DEPTH"] == 0


# ================================================================ N3 ==

def test_N3_order_path_idempotency():
    """N3：策略直接调 order() 同标的同方向第二次 ⇒ 返回 None + QS_NOOP_ORDER。"""
    ns = _shim_ns()
    first = ns["order"]("159941.SZ", 1000)
    assert first == "oid-1"
    assert len(ns["_issued"]) == 1
    second = ns["order"]("159941.SZ", 2000)          # 同标的同方向第二次
    assert second is None, "order() 路径第二次必须被幂等拦（返回 None）"
    assert any("QS_NOOP_ORDER api=order code=159941.SZ" in w
               and "reason=duplicate_idempotent" in w
               for w in ns["_log"].warnings), "必须留 QS_NOOP_ORDER 痕"
    assert len(ns["_issued"]) == 1, "被拦后不得有新委托"


def test_N3_order_internal_split_not_self_blocked():
    """N3 伴生：order() 直调超限拆多笔也不自撞（一次逻辑单一次判定）。"""
    ns = _shim_ns()
    ns["order"]("511010.SS", 83_300)                 # >49,000 ⇒ 内部拆 2 笔
    assert len(ns["_issued"]) == 2, ns["_issued"]
    assert ns["g"].state["orders_done"] == [["511010.SS", "auto"]]


# ================================================================ N4 ==

def test_N4_logical_duplicate_blocked():
    """N4：逻辑层跨调用重复下单（同标的同方向）⇒ 仍被拦（语义不放宽）。"""
    ns = _shim_ns()
    r1 = ns["order_target_value"]("512890.SS", 2_000_000)
    assert r1 == ns["_issued"][-1][2]
    n_after_first = len(ns["_issued"])
    r2 = ns["order_target_value"]("512890.SS", 2_400_000)   # 第二次逻辑下单
    assert r2 is None, "跨调用重复必须被拦"
    assert any("QS_NOOP_ORDER api=order_target_value code=512890.SS" in w
               and "reason=duplicate_idempotent" in w
               for w in ns["_log"].warnings)
    assert len(ns["_issued"]) == n_after_first, "被拦后不得有新委托"
    assert ns["g"].state["orders_done"] == [["512890.SS", "auto"]]


def test_N4_cross_api_duplicate_blocked():
    """N4 补充：order_target_value 已下过单，同标的再直接 order() 也算重复。"""
    ns = _shim_ns()
    ns["order_target_value"]("159980.SZ", 500_000)   # 50,000 股 ⇒ 拆 2 笔
    assert len(ns["_issued"]) == 2
    assert ns["order"]("159980.SZ", 800) is None
    assert len(ns["_issued"]) == 2, "被拦后不得有新委托"


# ================================================================ N5 ==

def test_N5_missing_orders_reported_not_success():
    """N5a：get_order 全返回 None（平台未出现）⇒ 分列 pending 计满 + 逐笔 warning，
    且不出现「提交状态=成功」。"""
    ns = _shim_ns(price=N2_PX, get_order_impl=lambda oid: None)
    ns["order_target_value"](N2_CODE, N2_VALUE)      # 拆 4 笔 ⇒ pending=4
    msgs = ns["_log"].all_msgs()
    assert any("QS_ORDER_MISSING" in w for w in ns["_log"].warnings), \
        "平台未出现必须逐笔 warning（禁止兜底成成功）"
    assert any("QS_ORDER_ACCEPT api=order_target_value" in w and "pending=4" in w
               for w in ns["_log"].warnings), "收尾必须按笔数分列"
    assert not any("提交状态=成功" in m for m in msgs), "存在未出现时禁报成功"


def test_N5_rejected_orders_reported_not_success():
    """N5b：get_order 返回废单（status='8'）⇒ rejected 分列，不出现成功。"""
    def _rej(oid):
        return type("O", (), {"status": "8", "price": 10.0})()

    ns = _shim_ns(get_order_impl=_rej)
    ns["order_target_value"]("512890.SS", 2_000_000)
    msgs = ns["_log"].all_msgs()
    assert any("QS_ORDER_ACCEPT" in w and "rejected=5" in w
               for w in ns["_log"].warnings), msgs
    assert not any("提交状态=成功" in m for m in msgs)


def test_N5_all_final_reports_acceptance_without_success_claim():
    """N5c：全终态非废单 ⇒ info 分列（accepted=n rejected=0 pending=0），仍不用
    「提交状态=成功」措辞（可观测基线统一）。"""
    def _ok(oid):
        return type("O", (), {"status": "6", "price": 10.0})()

    ns = _shim_ns(get_order_impl=_ok)
    ns["order_target_value"]("511260.SS", 2_000_000)
    assert any("QS_ORDER_ACCEPT api=order_target_value" in i
               and "accepted=5" in i and "rejected=0" in i
               for i in ns["_log"].infos)
    assert not any("提交状态=成功" in m for m in ns["_log"].all_msgs())


# ================================================================ F3(a) ==

def test_F3a_cash_reserved_accumulates_across_symbols():
    """F3(a) 正证：第一标的承诺额进入 qs_cash_reserved，第二标的预算被扣减
    （11-06 超买根因：跨标的累计不扣 ⇒ 尾笔被平台拒）。"""
    ns = _shim_ns(price=10.0, cash=10_000_000.0)

    def _px(oid):
        return type("O", (), {"status": "6", "price": 10.0})()

    ns["get_order"] = _px
    ns["order_target_value"]("512890.SS", 2_000_000)   # 5 段 × 40,000 股 × 10 = 2,000,000
    res = ns["g"].state["qs_cash_reserved"]
    assert abs(res - 2_000_000.0) < 1e-6, f"承诺额未跨标的累计: {res}"
    # 第二标的的预算行必须明示 reserved 与 basis（不静默用全账户口径）
    ns["order_target_value"]("511010.SS", 500_000)
    lines = [i for i in ns["_log"].infos if i.startswith("QS_CASH_BUDGET basis=market_pool")]
    assert len(lines) == 2, lines
    assert "reserved=2000000.0" in lines[1], lines[1]
    assert "budget=8000000.0" in lines[1], lines[1]


def test_F3a_epoch_reset_discards_stale_reserved():
    """F3(a) 生命周期：本进程首次取预算作废 pickle 恢复的跨日残留（=本次运行口径）。"""
    ns = _shim_ns(price=10.0)
    ns["g"].state["qs_cash_reserved"] = 9_999_999.0    # 模拟跨日 pickle 残留
    ns["order_target_value"]("511010.SS", 2_000_000)
    assert ns["g"].state["qs_cash_reserved"] < 9_999_999.0, "跨日残留未被作废"
    assert any("basis=epoch_reset" in i for i in ns["_log"].infos)


# ================================================================ F2 护栏 ==

def _guard_helper_src():
    """渲染护栏 helper（幂等段只读版，transformer._build_guard_helper 同构拼装）。"""
    import importlib.util
    p = REPO / "quantstudio" / "ptrade_deploy" / "templates" / "guard_template.py"
    spec = importlib.util.spec_from_file_location("_guard_tpl_pd0018", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    body = mod.GUARD_IDEMPOTENCY_BLOCK
    return mod.GUARD_HELPER_TEMPLATE.replace("__SEGMENTS__", body.rstrip("\n"))


def test_guard_idempotency_segment_is_read_only():
    """F2 护栏面：幂等段只读判定——重复调用不再登记（消除「调用即登记」自撞源），
    台账有键时仍拦截（消费语义不削弱）。"""
    src = _guard_helper_src()
    assert "_done.append" not in src, "幂等段不得再登记"
    ns = {"g": _G(), "log": _Log()}
    ns["g"].state = {"orders_done": []}
    exec(compile(src, "<guard>", "exec"), ns)
    assert ns["_deploy_guard_pass"]("513500.SS", "auto") is True
    assert ns["_deploy_guard_pass"]("513500.SS", "auto") is True   # 第二次仍放行
    assert ns["g"].state["orders_done"] == [], "只读段不得写台账"
    # 台账有键（由 _qs_idempotent_admit 逻辑层登记）⇒ 护栏消费判定仍拦
    ns["g"].state["orders_done"] = [["513500.SS", "auto"]]
    assert ns["_deploy_guard_pass"]("513500.SS", "auto") is False
    assert any("幂等拦截" in w for w in ns["log"].warnings)


# ================================================================ F6 归一 ==

def test_F6_status_int_and_str_both_terminal():
    """F3(b)/F6：status int 8 与 str '8' 均判终态废单；int 6 与 '6' 均判终态非废单
    （V005 int / V016·V041 str 双版本兼容；守卫终态常量未改，仅读取侧 str() 归一）。"""
    ns = _shim_ns()
    for raw in (8, "8"):
        ns["get_order"] = lambda oid, r=raw: type("O", (), {"status": r, "price": 10.0})()
        st, px = ns["_qs_check_order_state"]("oid-1")
        assert st == "final_reject", f"status={raw!r} 应判终态废单: {st}"
    for raw in (6, "6"):
        ns["get_order"] = lambda oid, r=raw: type("O", (), {"status": r, "price": 10.0})()
        st, px = ns["_qs_check_order_state"]("oid-1")
        assert st == "final_ok", f"status={raw!r} 应判终态非废单: {st}"
    ns["get_order"] = lambda oid: type("O", (), {"status": "2", "price": 10.0})()
    assert ns["_qs_check_order_state"]("oid-1")[0] == "pending"   # 已报未终态


# ================================================================ fail-open ==

def test_no_g_namespace_fail_open():
    """鲁棒：无 g/无 get_order 命名空间（compliance/pd12 同款）⇒ 幂等 fail-open
    放行、不吞单、订单序列不受影响。"""
    ns = _shim_ns(with_g=False)
    ret = ns["order_target_value"]("600519.SS", 2_000_000)
    assert len(ns["_issued"]) == 5, "无 g 环境必须正常下单（fail-open）"
    assert ret == ns["_issued"][-1][2]
    assert ns["_QS_SPLIT_DEPTH"] == 0


# ================================================================ 产物级 ==

SRC_PD0018 = '''\
"""PD-0018 固件源（模块 docstring 保位）。"""


def initialize(context):
    """初始化说明（函数 docstring 保位）。"""
    g.done = False


def handle_data(context, data):
    if not g.done:
        order_target_value('512890.SS', 200000)
        g.done = True
'''


@pytest.fixture(scope="module")
def product_text(tmp_path_factory):
    d = tmp_path_factory.mktemp("pd0018")
    src = d / "s_ptrade.py"
    src.write_text(SRC_PD0018, encoding="utf-8")
    # PD-0018 修 B（PD-2026-0018 两跳盲区）：deploy 固件的 source 必须取**第一跳**
    # （convert_source / qs-compile import）产物，否则 ORDER_SPLIT 等第一跳注入块
    # 不进产物 ⇒ 会把「实现已改好、产物查不到标记」误判为固件红。
    from quantstudio.strategy_compiler.source_import import convert_source
    _hop1 = convert_source(str(src), strategy_id="t_pd0018", verbose=False)
    hop1_path = d / "s_ptrade_firsthop.py"
    hop1_path.write_text(getattr(_hop1, "converted_code"), encoding="utf-8")
    src = hop1_path
    cfg = d / "deploy.yaml"
    cfg.write_text(
        "strategy_id: t_pd0018\n"
        f"source: {src.as_posix()}\n"
        "execution:\n  profile: daily_eod_1450\n"
        "persistence:\n  enabled: true\n"
        f"output:\n  root: {(d / 'out').as_posix()}\n",
        encoding="utf-8")
    r = run_deploy(str(cfg)).to_dict()
    assert r["status"] == "PASS", r["stages"]
    return Path(r["artifacts"]["product"]).read_text(encoding="utf-8")


def test_product_has_pd0018_observability(product_text):
    """产物级：PD-0018 可观测行在位（幂等/预算/受理分列/内部下单入口）。"""
    for key in ("_QS_SPLIT_DEPTH", "_qs_idempotent_admit", "QS_CASH_BUDGET",
                "QS_ORDER_ACCEPT", "_QSOrderRefState.order_wrapped",
                "qs_cash_reserved"):
        assert key in product_text, key
    # 护栏幂等段只读化在产物面同样成立——★按函数 AST 精确定界（PD-0018 修 C）：
    # 全局 'not in product_text' 会把 _qs_idempotent_admit 内**合法**的逻辑层登记
    # （_done.append(_key)，设计如此）一并算命中 ⇒ 断言范围过宽。
    import ast as _ast
    _tree = _ast.parse(product_text)
    _guard = next(n for n in _ast.walk(_tree)
                  if isinstance(n, _ast.FunctionDef) and n.name == "_deploy_guard_pass")
    _guard_src = _ast.get_source_segment(product_text, _guard) or ""
    assert "_done.append" not in _guard_src, "护栏函数内仍有幂等登记（应只读化）"
    # 逻辑层登记必须存在（防误删成「谁都不登记」）
    assert "_qs_idempotent_admit" in product_text and "_done.append(_key)" in product_text
    # PERSIST/E′/D 语义保位（不得因本批改动漂移）
    assert "g.state['orders_done'] = []" in product_text
    assert "qs_prev_orders" in product_text
    assert "陈旧挂单巡检" in product_text


def test_product_guard_wraps_strategy_call_not_split_loop(product_text):
    """产物级：护栏包裹策略调用点（拆单入口前一次，四段不削弱），而 shim 拆单
    循环内无护栏包裹（自撞结构不可再生）。"""
    tree = ast.parse(product_text)
    # 策略 handle_data 内的下单语句必须仍被护栏包裹
    guards = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "_deploy_guard_pass"]
    assert guards, "策略调用点护栏不得缺失（四段不削弱）"
    for c in guards:
        assert len(c.args) == 2, ast.unparse(c)   # PD-0016 两参签名保持
    # shim 的拆单循环走 order_wrapped（两层属性 ⇒ 护栏不包）
    assert "_QSOrderRefState.order_wrapped(_code" in product_text
