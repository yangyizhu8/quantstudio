# -*- coding: utf-8 -*-
"""PD-0006 run_daily 源形态支持测试（方案 docs/ptrade-deploy-rundaily-design.md §7）。

覆盖：P1 识别（单挂载/时点解析/多挂载警告）、P2 放宽与拒绝边界（D3/D4）、
P3 挂载（源注册替换+重挂档位时点+函数体内联）、P4 等价（第 5 类白名单）、
端到端 7 阶段 PASS、多挂载源 fail-closed、非挂载无 handle_data 源仍 FAIL（D4）。
"""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantstudio.ptrade_deploy import run_deploy  # noqa: E402
from quantstudio.ptrade_deploy.profiler import build_profile  # noqa: E402
from quantstudio.ptrade_deploy.config import (  # noqa: E402
    load_config_from_yaml, cross_validate)
from quantstudio.ptrade_deploy.preflight import load_contract  # noqa: E402
from quantstudio.ptrade_deploy.transformer import transform  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "deploy_fixtures"
RD_SOURCE = (FIXTURE_DIR / "fixture_rundaily_strategy.py").read_text(encoding="utf-8")
RD_CFG = load_config_from_yaml(
    (FIXTURE_DIR / "deploy_rundaily.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- P1 识别 --

def test_profiler_identifies_single_mount():
    profile = build_profile(RD_SOURCE)
    mounts = profile["run_daily_mounts"]
    assert mounts == [{"func": "weekly_rebalance", "time": "15:00"}], mounts


def test_profiler_no_mount_for_handle_data_source():
    src = (FIXTURE_DIR / "fixture_daily_strategy.py").read_text(encoding="utf-8")
    assert build_profile(src)["run_daily_mounts"] == []


def test_profiler_multi_mount_warning():
    src = RD_SOURCE + "\n\ndef extra_task(context):\n    pass\n"
    # 在 initialize 追加第二个挂载（模拟多策略错峰形态）
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "initialize":
            node.body.append(ast.parse(
                "run_daily(context, extra_task, time='09:35')", mode="exec").body[0])
    import quantstudio.ptrade_deploy.profiler as prof
    mounts = prof._run_daily_mounts(tree)
    assert len(mounts) == 2
    profile = build_profile(ast.unparse(tree))
    assert any("多挂载源暂不支持" in w for w in profile["warnings"])


# ---------------------------------------------------------------- P2 校验 --

def test_p2_single_mount_daily_profile_passes():
    profile = build_profile(RD_SOURCE)
    assert cross_validate(RD_CFG, profile) == []


def test_p2_multi_mount_rejected():
    profile = build_profile(RD_SOURCE)
    profile["run_daily_mounts"] = [
        {"func": "a", "time": "09:35"}, {"func": "b", "time": "15:00"}]
    errs = cross_validate(RD_CFG, profile)
    assert any("多挂载源暂不支持" in e for e in errs), errs


def test_p2_no_handle_no_mount_still_rejected():
    """D4：非 run_daily 形态（纯 initialize）FAIL 边界不回退。"""
    profile = build_profile("def initialize(context):\n    g.x = 1\n")
    assert profile["run_daily_mounts"] == []
    errs = cross_validate(RD_CFG, profile)
    assert any("handle_data" in e for e in errs), errs


def test_p2_hybrid_form_explicitly_rejected():
    """PD-0006 边界（PD-0007 后收窄）：非 stub 的 handle_data + 挂载并存仍显式拒绝。

    背景更正（2026-10-19）：混合形态系上游**既有**产出形态（mtime 2026-08-27），
    非「在途重写」。PD-0007 后诊断型 stub 混合形态放行；本用例构造的是
    「handle_data 判非 stub」路径（RD_SOURCE 无 handle_data → stub=False）→
    仍拒绝，报「含实质语句」。
    """
    profile = build_profile(RD_SOURCE)
    profile["lifecycle"] = ["initialize", "handle_data"]  # 模拟混合形态
    errs = cross_validate(RD_CFG, profile)
    assert any("混合形态暂不支持" in e for e in errs), errs


# ---------------------------------------------------------------- P3 挂载 --

def test_transform_replaces_source_registration():
    profile = build_profile(RD_SOURCE)
    result = transform(RD_SOURCE, profile, RD_CFG, load_contract())
    text = result.product_text
    assert "run_daily(context, _deploy_daily_exec, time='14:50')" in text  # 重挂档位时点
    assert "weekly_rebalance, time='15:00'" not in text                    # 源注册已移除
    assert "def weekly_rebalance" not in text                              # 函数体已内联改名
    assert any(e["location"] == "weekly_rebalance" and e["kind"] == "MOUNT"
               for e in result.edits)


# ---------------------------------------------------------------- 端到端 --

def test_rundaily_end_to_end_all_stages_pass():
    d = run_deploy(str(FIXTURE_DIR / "deploy_rundaily.yaml")).to_dict()
    assert d["status"] == "PASS", d
    for stage, payload in d["stages"].items():
        assert payload["status"] == "PASS", (stage, payload)


def test_rundaily_product_registration_semantics():
    d = run_deploy(str(FIXTURE_DIR / "deploy_rundaily.yaml")).to_dict()
    text = Path(d["artifacts"]["product"]).read_text(encoding="utf-8")
    assert "time='14:50'" in text            # 部署档位时点（daily_eod_1450）
    assert "time='15:00'" not in text        # 源时点不残留
