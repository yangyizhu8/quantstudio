"""PD-0019 期 1 · 多策略同账户隔离固件（行为级 · 三态＋记账口径＋fail-loud）。

背景：期 1 隔离的落地位置是**下单入口 + 虚拟台账层**（裁定甲，2026-11-08），不替换
``context.portfolio``。P0 两项硬前置：
- **P0-1** 虚拟持仓读数覆盖 ``_qs_pos_amount``（杜绝 ``get_position`` 真实持仓混入 delta）；
- **P0-2** ``isolation.enabled=true`` 时 ``target_orig`` 兜底全禁（防虚拟额度 < 真实持仓
  ⇒ delta 恒负 ⇒ 回落平台原生态折算 ⇒ **把真实持仓卖掉**，不可逆）。

本固件把注入块的 ``_QS_ISO_HEAD`` 源码 exec 进**沙箱命名空间**（提供 ``g`` / ``log`` /
``_qs_last_close_lookup`` 桩）后做行为验证——按既定纪律「桩/沙箱验证必须带依赖直读自检」，
每个用例先断言桩的直读值符合预期，避免"沙箱缺依赖 ⇒ 静默 0.0"被误读为被测缺陷。
"""

from __future__ import annotations

import ast

import pytest

from quantstudio.ptrade_deploy.equiv import _strip_iso_nodes
from quantstudio.ptrade_deploy.templates import isolation_template as tpl


def _sandbox(capital=100000.0, policy="strict", quotes=None):
    """把 ISO HEAD 源码 exec 进沙箱；返回 (namespace, g, logs, quotes)。"""
    src = (tpl._QS_ISO_HEAD
           .replace("__CAPITAL__", repr(float(capital)))
           .replace("__POLICY__", repr(str(policy)))
           .replace("__MARKET_SPLIT__", repr(True))
           .replace("__RESERVE_BUFFER__", repr(0.001)))

    class _G:
        pass

    g = _G()
    g.state = {}
    logs = []

    class _Log:
        def info(self, msg):
            logs.append(("INFO", msg))

        def warning(self, msg):
            logs.append(("WARN", msg))

        def error(self, msg):
            logs.append(("ERR", msg))

    _quotes = dict(quotes or {})

    def _qs_last_close_lookup(code):
        return _quotes.get(code, 0.0)

    ns = {"g": g, "log": _Log(), "_qs_last_close_lookup": _qs_last_close_lookup}
    exec(compile(src, "<qs_iso_head>", "exec"), ns)  # noqa: S102 —— 固件内自造沙箱
    return ns, g, logs, _quotes


def test_sandbox_selfcheck_quote_stub_is_live():
    """桩自检：报价桩必须真的返回给定值（防"沙箱缺依赖 ⇒ 恒 0.0"被误读为缺陷）。"""
    ns, _g, _logs, _q = _sandbox(quotes={"512890.SS": 1.237})
    assert ns["_qs_last_close_lookup"]("512890.SS") == pytest.approx(1.237)
    assert ns["_qs_last_close_lookup"]("999999.SS") == 0.0
    assert ns["_QS_ISO_ENABLED"] is True
    assert ns["_QS_ISO_CAPITAL"] == pytest.approx(100000.0)


def test_virtual_position_reads_back():
    """P0-1：虚拟持仓台账写入后可被 ``_qs_iso_pos_amount`` 读回（真实持仓不参与）。"""
    ns, g, _logs, _q = _sandbox()
    iso = ns["_qs_iso_state"]()
    assert iso is not None, "台账须能初始化（g.state 为 dict）"
    iso["pos"]["512890.SS"] = 800
    assert ns["_qs_iso_pos_amount"]("512890.SS") == pytest.approx(800.0)
    assert ns["_qs_iso_pos_amount"]("518880.SS") == pytest.approx(0.0)


def test_cash_avail_is_capital_minus_reserved():
    """虚拟可用现金 = cash_base − reserved；reserved 跨标的累计、不得为负。"""
    ns, _g, _logs, _q = _sandbox(capital=100000.0)
    iso = ns["_qs_iso_state"]()
    iso["cash_base"] = 100000.0
    iso["reserved"] = 0.0
    assert ns["_qs_iso_cash_avail"]() == pytest.approx(100000.0)
    iso["reserved"] = 30000.0
    assert ns["_qs_iso_cash_avail"]() == pytest.approx(70000.0)
    iso["reserved"] = 999999.0  # 超额预扣 ⇒ 钳到 0，不得返回负数
    assert ns["_qs_iso_cash_avail"]() == pytest.approx(0.0)


def test_strict_rejects_target_orig_and_counts():
    """P0-2（strict）：``target_orig`` 被禁 ⇒ 返回 None、拒单计数 +1、落 ERROR 日志。

    这是"防真实清仓"的核心防线：虚拟额度 < 真实持仓时 delta 恒负，原实现会回落平台
    原生按**真实**持仓折算 ⇒ 必须拒。
    """
    ns, _g, logs, _q = _sandbox(policy="strict")
    iso = ns["_qs_iso_state"]()
    before = int(iso.get("rejects", 0))
    ret = ns["_qs_iso_target_orig_blocked"]("512890.SS", 100000.0)
    assert ret is None, "strict 下不得放行到平台原生"
    assert int(ns["_qs_iso_state"]().get("rejects", 0)) == before + 1
    assert any(k == "ERR" and "QS_ISO_TARGET_ORIG_BLOCKED" in m for k, m in logs)


def test_soft_warns_but_still_counts():
    """P0-2（soft）：告警但仍计数；返回 None（不自行下单，交由上层决定）。"""
    ns, _g, logs, _q = _sandbox(policy="soft")
    ret = ns["_qs_iso_target_orig_blocked"]("512890.SS", 100000.0)
    assert ret is None
    assert any(k == "WARN" and "QS_ISO_TARGET_ORIG_SOFT" in m for k, m in logs)
    assert int(ns["_qs_iso_state"]().get("rejects", 0)) >= 1


def test_cross_day_reset_uses_prev_close_and_never_zero_fallback():
    """§11.3 跨日重置：基准 = capital − 隔夜市值；缺价**不得回退 0**（0 会致额度虚增）。"""
    # 持仓 800 股 × 昨收 50 = 40,000 ⇒ 基准 = 100000 − 40000 = 60000
    ns, _g, logs, _q = _sandbox(capital=100000.0, quotes={"512890.SS": 50.0})
    iso = ns["_qs_iso_state"]()
    iso["pos"] = {"512890.SS": 800}
    iso["epoch"] = "19700101"  # 伪造昨日 ⇒ 触发重置
    iso2 = ns["_qs_iso_state"]()
    assert iso2["cash_base"] == pytest.approx(60000.0)
    assert iso2["reserved"] == pytest.approx(0.0)

    # 缺价场景：另一标的无报价 ⇒ 该标的不计入市值，且必须落 WARN（不得静默按 0 计）
    ns3, _g3, logs3, _q3 = _sandbox(capital=100000.0, quotes={})
    iso3 = ns3["_qs_iso_state"]()
    iso3["pos"] = {"000001.SZ": 1000}
    iso3["epoch"] = "19700101"
    iso4 = ns3["_qs_iso_state"]()
    assert iso4["cash_base"] == pytest.approx(100000.0)  # 缺价 ⇒ 不减（不是按 0 减）
    assert any(k == "WARN" and "QS_ISO_PX_MISSING" in m for k, m in logs3)


def test_negative_base_clamped_to_zero_with_warning():
    """隔夜市值 > capital ⇒ 基准取 0 并告警（不得为负）。"""
    ns, _g, logs, _q = _sandbox(capital=10000.0, quotes={"512890.SS": 100.0})
    iso = ns["_qs_iso_state"]()
    iso["pos"] = {"512890.SS": 900}  # 90,000 > 10,000
    iso["epoch"] = "19700101"
    iso2 = ns["_qs_iso_state"]()
    assert iso2["cash_base"] == pytest.approx(0.0)
    assert any(k == "WARN" and "QS_ISO_CAPITAL_NEGATIVE" in m for k, m in logs)


def test_iso_namespace_conflict_fails_loud():
    """§11.7.4 反向风险防御：源策略占用 ``_qs_iso_*`` 命名 ⇒ 记 anomaly，**不静默剔除**。

    否则等价性判据会把策略自己的代码当注入内容删掉 ⇒ 假 PASS（掩盖真实差异）。
    """
    src = (
        "def _qs_iso_pos_amount(security):\n"
        "    return 0.0\n"
        "\n"
        "def helper():\n"
        "    return 1\n"
    )
    tree = ast.parse(src)
    anomalies = []
    stripped = _strip_iso_nodes(tree, anomalies)
    assert stripped == 0, "冲突时不得剔除"
    assert anomalies, "冲突时必须记 anomaly（fail-loud）"
    assert "_qs_iso_pos_amount" in anomalies[0]
    # 正常注入场景（有 _QS_ISO_ENABLED 开关）⇒ 正常剔除、无 anomaly
    ok_src = (
        "_QS_ISO_ENABLED = True\n"
        "def _qs_iso_state():\n"
        "    return None\n"
        "def keep():\n"
        "    return 1\n"
    )
    tree2 = ast.parse(ok_src)
    anomalies2 = []
    stripped2 = _strip_iso_nodes(tree2, anomalies2)
    assert stripped2 == 2 and not anomalies2
    kept = [n.name for n in tree2.body if isinstance(n, ast.FunctionDef)]
    assert kept == ["keep"], "仅剔 ISO 特征节点，其余原样保留"


def test_isolation_disabled_injects_nothing():
    """纯增益：``isolation.enabled=False`` 时 ``_apply_isolation`` 零注入（tree 不变）。"""
    from quantstudio.ptrade_deploy.config import load_config_from_yaml
    from quantstudio.ptrade_deploy.transformer import _apply_isolation

    cfg = load_config_from_yaml(
        "strategy_id: t\nsource: x.py\nexecution:\n  profile: daily_eod_1450\n")
    assert cfg.isolation.enabled is False

    tree = ast.parse("def keep():\n    return 1\n")
    before = ast.dump(tree)
    edits = []
    _apply_isolation(tree, cfg, edits)
    assert ast.dump(tree) == before, "未启用时不得改动 AST"
    assert edits == []


def test_order_blocked_stub_blocks_real_orders():
    """止血桩（2026-11-09）：隔离启用时真实下单入口必须被禁（返 None ＋ 计数 ＋ ERROR）。

    事由：`_qs_cash_budget` 仍读真实全账户可用现金，而 `_qs_pos_amount` 已改读虚拟持仓
    （空仓）⇒ `_delta = value`（策略按真实组合算出的目标市值）⇒ 预算按真实现金放行
    ⇒ 调仓可能下出**真单**。故接线完成前 `order_orig`/`value_orig`/`percent_orig`
    全指向拒单桩。
    """
    ns, _g, logs, _q = _sandbox()
    before = int(ns["_qs_iso_state"]().get("rejects", 0))
    ret = ns["_qs_iso_order_blocked"]("512890.SS", 1000)
    assert ret is None, "止血桩不得返回委托 id"
    assert int(ns["_qs_iso_state"]().get("rejects", 0)) == before + 1
    assert any(k == "ERR" and "QS_ISO_ORDER_BLOCKED" in m for k, m in logs)


def test_tail_patches_all_order_entrypoints():
    """TAIL 覆盖断言：四个下单入口 orig 全部指向拒单桩（target_orig 另计）。"""
    from quantstudio.ptrade_deploy.templates import isolation_template as tpl

    tail = tpl._QS_ISO_TAIL
    for name in ("order_orig", "value_orig", "percent_orig"):
        assert f"_QSOrderRefState.{name} = _qs_iso_order_blocked" in tail, name
    assert "_QSOrderRefState.target_orig = _qs_iso_target_orig_blocked" in tail
    assert "_qs_pos_amount_legacy = _qs_pos_amount" in tail
