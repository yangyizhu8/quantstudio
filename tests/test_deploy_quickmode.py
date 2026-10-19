# -*- coding: utf-8 -*-
"""PD-0008 快捷模式 + 统一输出目录测试。

覆盖：
- D2 `strategy_id` 派生（ASCII 直通 / 中文稳定且 ASCII / 不同中文名不碰撞 / ≤40）；
- D5′ 发现（递归两层 / `_verify*` 过滤 / 中文目录名）；
- D3 输出根（缺省仓库根 Demo_account_Real_account / cfg 覆盖 / 相对路径按仓库根解析）；
- D4 重转归档三档（null=不限 / 0=关闭 / N>0=保留最近 N 份）；
- **②审条件③**：快捷 yaml 与等价手写 yaml → 产物 SHA256 逐位一致；
- 端到端七阶段 PASS + 产物落 `<out_root>/<策略目录名>/`；
- GUI 快捷页冒烟（双页 + 档位默认）。
"""
import hashlib
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from quantstudio.ptrade_deploy import quickmode, run_deploy  # noqa: E402
from quantstudio.ptrade_deploy.config import load_config_from_yaml  # noqa: E402
from quantstudio.ptrade_deploy.deliver import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT_NAME, resolve_out_root, safe_dir_name)

TECH_SRC = (REPO / "output" / "ptrade_export" / "tech_etf_mvo_rotation"
            / "tech_etf_mvo_rotation_ptrade.py")

#: 全进程常驻 QApplication（见 test_deploy_tab_quick_widgets 注释）
_QT_APP = None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------ D5′ 发现 --

def test_discover_recursive_and_filters_verify():
    names = {it["name"] for it in quickmode.discover_strategies()}
    assert "tech_etf_mvo_rotation" in names
    assert "CANSLIM突破成长选股策略" in names           # 中文目录名
    assert any("F-Score" in n for n in names)           # 两层嵌套（fscore_rsrs/…）
    assert not any(n.startswith(("_verify", "_probe")) for n in names)


def test_discover_include_verify_flag():
    items = quickmode.discover_strategies(include_verify=True)
    assert any("_verify" in it["product_path"] or it["name"].startswith("_verify")
               for it in items)


# ------------------------------------------------------------ D2 派生 --

def test_derive_ascii_passthrough():
    assert quickmode.derive_strategy_id("tech_etf_mvo_rotation") == "tech_etf_mvo_rotation"


def test_derive_chinese_stable_ascii_and_bounded():
    a = quickmode.derive_strategy_id("CANSLIM突破成长选股策略")
    b = quickmode.derive_strategy_id("CANSLIM突破成长选股策略")
    assert a == b and a.isascii() and a.startswith("CANSLIM_")
    assert len(a) <= 40


def test_derive_distinct_for_different_chinese_names():
    a = quickmode.derive_strategy_id("小市值策略ptrade")
    b = quickmode.derive_strategy_id("周频小市值成长动量（三层止损）")
    assert a != b and a.isascii() and b.isascii()


# ------------------------------------------------------------ D1/D3 目录 --

def test_safe_dir_name_keeps_chinese_and_caps_length():
    assert safe_dir_name("CANSLIM突破成长选股策略") == "CANSLIM突破成长选股策略"
    assert len(safe_dir_name("x" * 200)) == 40
    assert safe_dir_name('a/b:c*d') == "a_b_c_d"


def test_resolve_out_root_default_is_repo_demo_dir():
    cfg = load_config_from_yaml(
        "strategy_id: demo\nsource: x.py\nexecution:\n  profile: daily_eod_1450\n")
    assert resolve_out_root(cfg) == REPO / DEFAULT_OUTPUT_ROOT_NAME


def test_resolve_out_root_config_override_relative_to_repo():
    cfg = load_config_from_yaml(
        "strategy_id: demo\nsource: x.py\nexecution:\n  profile: daily_eod_1450\n"
        "output:\n  root: output/deploy\n")
    assert resolve_out_root(cfg) == REPO / "output" / "deploy"


def test_quick_config_parses_with_expected_fields(tmp_path):
    text = quickmode.build_quick_config(
        "CANSLIM突破成长选股策略", "output/ptrade_export/x/x_ptrade.py",
        output_root=str(tmp_path))
    cfg = load_config_from_yaml(text)
    assert cfg.strategy_id.startswith("CANSLIM_")
    assert cfg.output.dir_name == "CANSLIM突破成长选股策略"
    assert cfg.execution.profile == "daily_eod_1450"
    assert cfg.output.keep_versions is None


# ------------------------------------------------ ②审条件③：对拍 + 端到端 --

def test_quick_equals_manual_yaml_sha(tmp_path):
    """快捷 yaml 与等价手写 yaml → 产物 SHA256 逐位一致。"""
    out = tmp_path / "out"
    quick = tmp_path / "quick.yaml"
    quick.write_text(quickmode.build_quick_config(
        "tech_etf_mvo_rotation", str(TECH_SRC), output_root=str(out)), encoding="utf-8")
    manual = tmp_path / "manual.yaml"
    manual.write_text(
        "strategy_id: tech_etf_mvo_rotation\n"
        f'source: "{TECH_SRC.as_posix()}"\n'
        "execution:\n  profile: daily_eod_1450\n"
        "persistence:\n  enabled: true\n"
        "output:\n"
        f'  root: "{out.as_posix()}"\n'
        "  dir_name: tech_etf_mvo_rotation\n"
        "  keep_versions: null\n", encoding="utf-8")

    d1 = run_deploy(str(quick)).to_dict()
    assert d1["status"] == "PASS", d1["stages"]
    h1 = _sha(Path(d1["artifacts"]["product"]))

    d2 = run_deploy(str(manual)).to_dict()
    assert d2["status"] == "PASS", d2["stages"]
    h2 = _sha(Path(d2["artifacts"]["product"]))

    assert h1 == h2, (h1, h2)


def test_quick_end_to_end_lands_in_out_root(tmp_path):
    out = tmp_path / "out"
    cfg = tmp_path / "c.yaml"
    cfg.write_text(quickmode.build_quick_config(
        "tech_etf_mvo_rotation", str(TECH_SRC),
        output_root=str(out), dir_name="tech_etf_mvo_rotation"), encoding="utf-8")
    d = run_deploy(str(cfg)).to_dict()
    assert d["status"] == "PASS"
    product = Path(d["artifacts"]["product"])
    assert product.parent == out / "tech_etf_mvo_rotation"
    assert product.name == "tech_etf_mvo_rotation_trade.py"


# ------------------------------------------------------------ D4 归档 --

def _run_twice(tmp_path: Path, **cfg_kwargs) -> Path:
    out = tmp_path / "out"
    cfg = tmp_path / "c.yaml"
    cfg.write_text(quickmode.build_quick_config(
        "tech_etf_mvo_rotation", str(TECH_SRC),
        output_root=str(out), dir_name="mystrat", **cfg_kwargs), encoding="utf-8")
    run_deploy(str(cfg))
    run_deploy(str(cfg))
    return out / "mystrat"


def test_archive_default_unlimited(tmp_path):
    target = _run_twice(tmp_path)
    v001 = target / "_archive" / "v001"
    assert v001.is_dir()
    assert len(list(v001.iterdir())) == 3          # product/report/runbook


def test_keep_versions_zero_disables_archive(tmp_path):
    target = _run_twice(tmp_path, keep_versions=0)
    assert not (target / "_archive").exists()


def test_keep_versions_n_prunes_old(tmp_path):
    out = tmp_path / "out"
    cfg = tmp_path / "c.yaml"
    cfg.write_text(quickmode.build_quick_config(
        "tech_etf_mvo_rotation", str(TECH_SRC),
        output_root=str(out), dir_name="mystrat", keep_versions=1), encoding="utf-8")
    for _ in range(3):
        run_deploy(str(cfg))
    versions = sorted(p.name for p in (out / "mystrat" / "_archive").iterdir())
    assert len(versions) == 1


# ------------------------------------------------------------ GUI 冒烟 --

def test_deploy_tab_quick_widgets():
    pytest.importorskip("PyQt6")
    pytest.importorskip("qfluentwidgets")
    from PyQt6.QtWidgets import QApplication
    from quantstudio.gui.tabs.deploy_tab import DeployTab

    # QApplication 全进程存活（qfluentwidgets 的 QConfig 为 QObject 单例，
    # app 被回收后 C++ 侧悬空 → RuntimeError）；模块级持有。
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    tab = DeployTab()
    assert tab.tabs.count() == 2
    assert tab.tabs.tabText(0) == "快捷模式"
    assert tab.strategy_combo.count() >= 1
    assert tab.profile_combo.currentText() == "daily_eod_1450"
    tab._on_start_clicked()                       # 高级页空输入提示不变
    assert tab.status_label.text() == "请先选择 deploy.yaml"
    tab.deleteLater()
