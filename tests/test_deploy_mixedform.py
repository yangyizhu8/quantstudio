# -*- coding: utf-8 -*-
"""qs-deploy PD-0007 混合形态测试（诊断型 stub handle_data + 单 run_daily 挂载）。

方案：`docs/ptrade-deploy-mixedform-design.md`（②审通过，两条件已 folded）：
- 条件一：黑名单覆盖 handle_data 体内**全部** Call 节点（含 log 参数内嵌套调用）；
- 条件二：负例必须含「参数内嵌套调用」用例（修复前红、修复后绿）。

覆盖：P1 D1′ 判据（真实产物 + 四类负例）、P2 边界（stub 放行 / 多挂载拒绝 /
非 stub 拒绝并报判据命中）、P3 结构（stub 原样保留 + 挂载目标内联并按档位重挂）、
端到端 7 阶段 PASS（真实产物）、三路 parity（内核 == CLI == PyQt）。
"""
import ast
import hashlib
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from quantstudio.ptrade_deploy import run_deploy  # noqa: E402
from quantstudio.ptrade_deploy.profiler import build_profile  # noqa: E402
from quantstudio.ptrade_deploy.config import (  # noqa: E402
    load_config_from_yaml, cross_validate)
from quantstudio.ptrade_deploy.preflight import load_contract  # noqa: E402
from quantstudio.ptrade_deploy.transformer import transform  # noqa: E402

HV = Path(__file__).resolve().parent / "deploy_fixtures" / "hv"
VOL_YAML = HV / "hv_vol_regime.yaml"
CAN_YAML = HV / "hv_canslim.yaml"
VOL_SRC_PATH = (REPO / "output" / "ptrade_export" / "vol_regime_mom_rev"
                / "vol_regime_mom_rev_ptrade.py")
CAN_SRC_PATH = next((REPO / "output" / "ptrade_export").glob(
    "CANSLIM*/CANSLIM*_ptrade.py"))
VOL_CFG = load_config_from_yaml(VOL_YAML.read_text(encoding="utf-8"))


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- P1 D1′ --

@pytest.mark.parametrize("path", [VOL_SRC_PATH, CAN_SRC_PATH],
                         ids=["vol_regime", "canslim"])
def test_p1_real_mixed_products_detected_as_stub(path):
    """真实混合形态产物（mtime 2026-08-27 上游既有形态）判为诊断型 stub。"""
    profile = build_profile(_src(path))
    assert profile["handle_data_stub"] is True, profile["handle_data_stub_reasons"]
    assert len(profile["run_daily_mounts"]) == 1, profile["run_daily_mounts"]
    assert any("诊断型 stub" in w for w in profile["warnings"])


def test_p1_nested_call_inside_log_args_is_not_stub():
    """②审条件二：log 参数内嵌套取数调用必须判 NOT_STUB（经典绕过面）。"""
    src = (
        "def initialize(context):\n"
        "    run_daily(context, _rebal, time='15:00')\n"
        "def handle_data(context, data):\n"
        "    \"\"\"诊断占位\"\"\"\n"
        "    log.debug('h %s' % (get_history('510300.SS', 5),))\n"
        "def _rebal(context):\n"
        "    order_target_value('510300.SS', 0)\n"
    )
    profile = build_profile(src)
    assert profile["handle_data_stub"] is False
    assert any("get_history" in r for r in profile["handle_data_stub_reasons"])


def test_p1_indirect_order_via_private_helper_is_not_stub():
    """二级校验：stub 调用私有 helper、helper 内下单 → 判 NOT_STUB。"""
    src = (
        "def initialize(context):\n"
        "    run_daily(context, _rebal, time='15:00')\n"
        "def handle_data(context, data):\n"
        "    _do_decide()\n"
        "def _do_decide():\n"
        "    order_target_value('510300.SS', 100)\n"
        "def _rebal(context):\n"
        "    pass\n"
    )
    profile = build_profile(src)
    assert profile["handle_data_stub"] is False
    assert any("order_target_value" in r for r in profile["handle_data_stub_reasons"])


def test_p1_assignment_blocks_stub():
    src = ("def handle_data(context, data):\n"
           "    log.debug('x')\n"
           "    g.count = 1\n")
    profile = build_profile(src)
    assert profile["handle_data_stub"] is False
    assert any("赋值" in r for r in profile["handle_data_stub_reasons"])


def test_p1_more_than_three_statements_is_not_stub():
    src = ("def _ensure_runtime_state():\n    pass\n"
           "def handle_data(context, data):\n"
           "    _ensure_runtime_state()\n"
           "    log.debug('a')\n"
           "    log.info('b')\n"
           "    log.warning('c')\n"
           "    log.error('d')\n")
    profile = build_profile(src)
    assert profile["handle_data_stub"] is False
    assert any("可执行语句" in r for r in profile["handle_data_stub_reasons"])


def test_p1_stub_tolerates_docstring_after_helper():
    """实证形态：docstring 写在 helper 调用之后（_verify8 变体）仍判 stub。"""
    src = ("def _ensure_runtime_state():\n    pass\n"
           "def initialize(context):\n"
           "    run_daily(context, _rebal, time='15:00')\n"
           "def handle_data(context, data):\n"
           "    _ensure_runtime_state()\n"
           "    \"\"\"Bar-level hook（决策在 run_daily 15:00）\"\"\"\n"
           "    log.debug('x %s' % (context.current_dt,))\n"
           "def _rebal(context):\n"
           "    order_target_value('510300.SS', 0)\n")
    profile = build_profile(src)
    assert profile["handle_data_stub"] is True, profile["handle_data_stub_reasons"]


# ---------------------------------------------------------------- P2 校验 --

def test_p2_stub_single_mount_passes():
    profile = build_profile(_src(VOL_SRC_PATH))
    assert cross_validate(VOL_CFG, profile) == []


def test_p2_stub_multi_mount_rejected():
    profile = build_profile(_src(VOL_SRC_PATH))
    profile["run_daily_mounts"] = [{"func": "monthly_rebalance", "time": "15:00"},
                                   {"func": "extra_task", "time": "09:35"}]
    errs = cross_validate(VOL_CFG, profile)
    assert any("多挂载源暂不支持" in e for e in errs), errs


def test_p2_non_stub_mixed_rejected_with_reason():
    profile = build_profile(_src(VOL_SRC_PATH))
    profile["handle_data_stub"] = False
    profile["handle_data_stub_reasons"] = ["get_history@L1091"]
    errs = cross_validate(VOL_CFG, profile)
    assert any("混合形态暂不支持" in e and "实质语句" in e for e in errs), errs
    assert any("get_history@L1091" in e for e in errs), errs


# ---------------------------------------------------------------- P3 结构 --

def test_transform_mixed_preserves_stub_and_rewraps_target():
    src = _src(VOL_SRC_PATH)
    profile = build_profile(src)
    result = transform(src, profile, VOL_CFG, load_contract())
    text = result.product_text

    assert "run_daily(context, _deploy_daily_exec, time='14:50')" in text
    assert "run_daily(context, monthly_rebalance" not in text
    assert "def monthly_rebalance(" not in text
    assert "def _deploy_daily_exec(" in text
    assert "def handle_data(" in text

    tree = ast.parse(text)
    hd = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "handle_data")
    body = ast.unparse(hd)
    assert "log.debug" in body and "order_target" not in body

    assert any(e["kind"] == "MOUNT" and e["location"] == "monthly_rebalance"
               for e in result.edits)


# ---------------------------------------------------------------- 端到端 --

@pytest.mark.parametrize("yaml_path", [VOL_YAML, CAN_YAML],
                         ids=["vol_regime", "canslim"])
def test_mixed_end_to_end_all_stages_pass(yaml_path):
    d = run_deploy(str(yaml_path)).to_dict()
    failed = {k: v for k, v in d["stages"].items() if v["status"] != "PASS"}
    assert d["status"] == "PASS", failed
    for stage, payload in d["stages"].items():
        assert payload["status"] == "PASS", (stage, payload)


# ------------------------------------------------------------ 三路 parity --

def test_mixed_three_way_parity_sha():
    """§7.3 完整形态：内核 == CLI == PyQt 三入口产物 SHA256 逐位一致（混合形态源）。"""
    pytest.importorskip("PyQt6")
    pytest.importorskip("qfluentwidgets")
    from quantstudio.strategy_compiler.cli import main  # noqa: E402
    from quantstudio.gui.tabs.deploy_tab import run_deploy_kernel  # noqa: E402

    d = run_deploy(str(VOL_YAML)).to_dict()
    assert d["status"] == "PASS"
    product = Path(d["artifacts"]["product"])
    digest = lambda: hashlib.sha256(product.read_bytes()).hexdigest()

    h_kernel = digest()
    assert main(["deploy", str(VOL_YAML)]) == 0
    h_cli = digest()
    gui = run_deploy_kernel(str(VOL_YAML))
    assert gui["status"] == "PASS"
    h_gui = digest()
    assert h_kernel == h_cli == h_gui, (h_kernel, h_cli, h_gui)
