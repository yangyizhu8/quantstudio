"""PD-2026-0018 元测试：deploy 固件的 source 必须取第一跳产物（禁裸策略源码）。

两跳链路：第一跳 convert_source（source_import）注入 _QS_ORDER_SPLIT_EXT 等订单包装块；
第二跳 run_deploy（ptrade_deploy/transformer）注入 GUARD / PERSIST / MOUNT。
固件若把裸策略源码喂给 run_deploy，只覆盖第二跳 ⇒ 只在第一跳落地的改动被系统性漏检
（PD-0018 F1/F2、PD-0019 的 context.portfolio 代理均在第一跳）。
"""
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1] / "tests"

# 白名单：历史固件（刻意只测第二跳——被测对象是 deploy 阶段注入/门禁，不含第一跳注入块）。
# 新增固件不得再入此名单。
_LEGACY_RAW_SOURCE = {
    "test_deploy_cli.py", "test_deploy_closedloop.py", "test_deploy_contract_getorder.py",
    "test_deploy_equiv.py", "test_deploy_gates.py", "test_deploy_gates_unknown.py",
    "test_deploy_guard_context.py", "test_deploy_gui.py", "test_deploy_mixedform.py",
    "test_deploy_nested_gate.py", "test_deploy_opendir_button.py", "test_deploy_pd0017.py",
    "test_deploy_pipeline.py", "test_deploy_quickmode.py", "test_deploy_rundaily.py",
    "test_deploy_statepath.py", "test_deploy_vault.py",
}

_HOP1_MARKERS = ("convert_source(", "SourceConverter(", "run_import(")


def _deploy_fixtures():
    for p in sorted(TESTS.glob("test_deploy_*.py")):
        src = p.read_text(encoding="utf-8", errors="replace")
        if "run_deploy(" in src:
            yield p.name, src


def test_fixture_covers_two_hop_pipeline():
    offenders = [n for n, s in _deploy_fixtures()
                 if n not in _LEGACY_RAW_SOURCE and not any(k in s for k in _HOP1_MARKERS)]
    assert not offenders, (
        "以下 deploy 固件未覆盖第一跳（PD-2026-0018 两跳盲区）：" + ", ".join(offenders)
        + " —— fixture 内须先用 convert_source 产出第一跳产物再 run_deploy；"
          "若确实只需测第二跳，须在 _LEGACY_RAW_SOURCE 显式登记并说明理由。")


def test_pd0018_fixture_is_two_hop():
    src = (TESTS / "test_deploy_f1f2f3_guard.py").read_text(encoding="utf-8")
    assert "convert_source(" in src, "PD-0018 fixture 未调用第一跳 convert_source"
    assert "run_deploy(" in src, "PD-0018 fixture 未调用第二跳 run_deploy"
