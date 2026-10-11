# -*- coding: utf-8 -*-
"""P4 等价 diff 测试（设计文档 §5.4 / §12 V4）。

- 固件正常转换产物：白名单外 diff=0（passed=True）；
- 手工夹带改写（改一处信号语句）的产物：必须 FAIL——白名单机制不得成为夹带通道。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantstudio.ptrade_deploy.config import load_config_from_yaml  # noqa: E402
from quantstudio.ptrade_deploy.equiv import compare_sources  # noqa: E402
from quantstudio.ptrade_deploy.preflight import load_contract, run_preflight  # noqa: E402
from quantstudio.ptrade_deploy.profiler import build_profile  # noqa: E402
from quantstudio.ptrade_deploy.transformer import transform  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "deploy_fixtures"


def _convert(case: str):
    src = (FIXTURE_DIR / "fixture_daily_strategy.py").read_text(encoding="utf-8")
    cfg = load_config_from_yaml(
        (FIXTURE_DIR / case).read_text(encoding="utf-8"))
    contract = load_contract()
    profile = build_profile(src, contract)
    return src, transform(src, profile, cfg, contract).product_text, profile


def test_equivalent_product_passes():
    src, product, profile = _convert("deploy_daily_eod.yaml")
    result = compare_sources(src, product,
                             g_exempt=profile.get("g_exempt") or ())
    assert result.passed, (result.diffs, result.anomalies)


def test_tampered_signal_fails():
    src, product, profile = _convert("deploy_daily_eod.yaml")
    # 夹带改写：篡改信号比较符（> → >=）——非白名单类改写必须被 P4 拦截
    lines = product.split("\n")
    for i, ln in enumerate(lines):
        if "ma_short" in ln and ">" in ln and ">=" not in ln:
            lines[i] = ln.replace(">", ">=", 1)
            break
    else:
        raise AssertionError("固件中未找到可篡改的信号语句")
    tampered = "\n".join(lines)
    result = compare_sources(src, tampered,
                             g_exempt=profile.get("g_exempt") or ())
    assert not result.passed
    assert result.diffs, "夹带改写必须产生 diff 而非异常放行"


def test_extra_function_injection_fails_without_marker():
    """无标记新增函数（非 _deploy_ 注入物）必须 extra_function FAIL。"""
    src, product, profile = _convert("deploy_daily_eod.yaml")
    tampered = product + "\n\ndef _hidden_tamper(context):\n    return 1\n"
    result = compare_sources(src, tampered,
                             g_exempt=profile.get("g_exempt") or ())
    assert not result.passed
    assert any(d["kind"] == "extra_function" for d in result.diffs)


def test_preflight_rejects_backtest_only_core():
    """依赖 get_trades_file（回测专用、action=reject）核心逻辑 → P0 REJECT。"""
    src = (FIXTURE_DIR / "fixture_daily_strategy.py").read_text(encoding="utf-8")
    src += "\n\ndef _dump_trades(context):\n    p = get_trades_file()\n    return p\n"
    pre = run_preflight(src)
    assert not pre.passed
    assert pre.reject_reasons
