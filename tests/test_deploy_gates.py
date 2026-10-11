# -*- coding: utf-8 -*-
"""P5 平台门禁测试（设计文档 §5.2 P5 / §12 V5）。

- 正常转换产物：run_gates 全 PASS；
- 违规产物逐项被拦：未门控回测专用 API / 定时任务注册 6 次（F4 上限 5）/ g 状态含类实例。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantstudio.ptrade_deploy.config import load_config_from_yaml  # noqa: E402
from quantstudio.ptrade_deploy.gates import run_gates  # noqa: E402
from quantstudio.ptrade_deploy.preflight import load_contract  # noqa: E402
from quantstudio.ptrade_deploy.profiler import build_profile  # noqa: E402
from quantstudio.ptrade_deploy.transformer import transform  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "deploy_fixtures"


def _product(case: str = "deploy_daily_eod.yaml") -> str:
    src = (FIXTURE_DIR / "fixture_daily_strategy.py").read_text(encoding="utf-8")
    cfg = load_config_from_yaml((FIXTURE_DIR / case).read_text(encoding="utf-8"))
    contract = load_contract()
    return transform(src, build_profile(src, contract), cfg, contract).product_text


def test_normal_product_passes_gates():
    result = run_gates(_product(), load_contract())
    assert result.passed, result.to_dict()


def _all_findings(result) -> str:
    return "\n".join(f for c in result.to_dict()["checks"]
                     for f in (c["findings"] or []))


def test_ungated_backtest_only_api_fails():
    product = _product() + "\n\ndef _bad(context):\n    set_commission(0.0003)\n"
    result = run_gates(product, load_contract())
    assert not result.passed
    joined = _all_findings(result)
    assert "set_commission" in joined
    assert "未门控" in joined


def test_six_scheduled_tasks_fail():
    regs = "\n".join(
        f"    run_daily(context, _f{i}, time='10:0{i % 10}')" for i in range(6))
    product = _product() + (
        "\n\ndef _reg_all(context):\n" + regs + "\n"
        + "\n".join(f"def _f{i}(context):\n    pass\n" for i in range(6)))
    result = run_gates(product, load_contract())
    assert not result.passed
    joined = _all_findings(result)
    assert "run_daily" in joined or "定时" in joined


def test_class_instantiation_in_g_state_fails():
    product = _product() + (
        "\n\nclass _Widget(object):\n    pass\n\n"
        "def _init_w(context):\n    g.w = _Widget()\n")
    result = run_gates(product, load_contract())
    assert not result.passed
    joined = _all_findings(result)
    assert "g.w" in joined or "序列化" in joined or "类实例" in joined
