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

#: PD-0016 D3（测试健壮性）：真实产物可能被清理（output/ 在 .gitignore 内）。
#: 用到该产物的用例统一 _needs_tech 跳过，避免环境缺失被误报成代码回归。
TECH_SRC = (REPO / "output" / "ptrade_export" / "tech_etf_mvo_rotation"
            / "tech_etf_mvo_rotation_ptrade.py")

def _has_verify_product() -> bool:
    """是否存在 `_verify` 对照产物（`qs-compile import` 的对照/校验产物）。

    PD-0016 D3：该用例的真实前置是「有 _verify 产物」，而非「有 tech 产物」——
    笼统复用 _needs_tech 会在 tech 恢复后错误执行（实测踩到）。无此类产物时 skip，
    **不伪造测试前提**（手工造 _verify 目录等于给测试喂假环境）。
    """
    base = REPO / "output" / "ptrade_export"
    return base.is_dir() and any(
        "verify" in p.name.lower() for p in base.rglob("*_ptrade.py"))


#: 依赖真实产物 tech_etf_mvo_rotation 的用例（产物缺失时跳过而非报错）
_needs_tech = pytest.mark.skipif(
    not TECH_SRC.exists(),
    reason="依赖的真实 PTrade 产物缺失（output/ptrade_export 被清理）；"
           "需先 `qs-compile import` 重建 tech_etf_mvo_rotation 产物")


#: 全进程常驻 QApplication（见 test_deploy_tab_quick_widgets 注释）
_QT_APP = None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------ D5′ 发现 --

@_needs_tech
def test_discover_recursive_and_filters_verify():
    names = {it["name"] for it in quickmode.discover_strategies()}
    assert "tech_etf_mvo_rotation" in names
    assert "CANSLIM突破成长选股策略" in names           # 中文目录名
    assert any("F-Score" in n for n in names)           # 两层嵌套（fscore_rsrs/…）
    assert not any(n.startswith(("_verify", "_probe")) for n in names)


@pytest.mark.skipif(not _has_verify_product(),
                    reason="需 _verify 对照产物（当前仓无此类产物，非代码问题；"
                           "由 qs-compile import 的对照产物提供后自动恢复）")
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

@_needs_tech
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


@_needs_tech
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


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _deploy_twice_capture(tmp_path: Path, src: Path, **cfg_kwargs):
    """两次 deploy，返回 (target, r1, r2, first_run_files)。

    first_run_files = 第一次 deploy 后 out_dir 根下 {文件名: sha256}——
    用于断言「归档进 v001 的确实是**上一版**内容」而非新版拷贝。
    """
    out = tmp_path / "out"
    cfg = tmp_path / "c.yaml"
    cfg.write_text(quickmode.build_quick_config(
        "tech_etf_mvo_rotation", str(src),
        output_root=str(out), dir_name="mystrat", **cfg_kwargs), encoding="utf-8")
    r1 = run_deploy(str(cfg))
    target = out / "mystrat"
    first = {q.name: _sha(q) for q in target.iterdir() if q.is_file()}
    r2 = run_deploy(str(cfg))
    return target, r1, r2, first


def _assert_two_runs_ok(r1, r2) -> None:
    """⑤ 两次 deploy 显式成功（不以文件数代替成功性）。"""
    for tag, r in (("r1", r1), ("r2", r2)):
        st = r.to_dict().get("status") if hasattr(r, "to_dict") else getattr(r, "status", None)
        assert st != "FAIL", f"{tag} status=FAIL: {getattr(r, 'to_dict', lambda: r)()}"


@_needs_tech
def test_archive_with_sidecar_five_checks(tmp_path):
    """态 A：源**有**伴生 .gen.json ⇒ 归档四件（含认证 sidecar）。

    核①名称 ②旧版内容 ③归档位置 ④保留份数 ⑤两次成功。
    """
    target, r1, r2, first = _deploy_twice_capture(tmp_path, TECH_SRC)
    _assert_two_runs_ok(r1, r2)                           # ⑤

    v001 = target / "_archive" / "v001"
    assert v001.is_dir()                                  # ③ 归档位置

    names = sorted(q.name for q in v001.iterdir() if q.is_file())
    assert names == sorted(["tech_etf_mvo_rotation_trade.py",      # ① 逐名集合，不数总数
                            "tech_etf_mvo_rotation_trade.py.gen.json",
                            "report.md",
                            "runbook.md"]), names

    # ② 归档的是**上一版**内容（与第一次 deploy 产物逐位同 sha）
    arch_prod = v001 / "tech_etf_mvo_rotation_trade.py"
    assert arch_prod.is_file()
    assert _sha(arch_prod) == first["tech_etf_mvo_rotation_trade.py"]
    # 附注（可选核，用户 2026-11-12 小注）：report/runbook 各核一件
    for extra in ("report.md", "runbook.md"):
        assert _sha(v001 / extra) == first[extra], extra

    # ③ 归档后 out_dir 根下只余本次产物（同样含 sidecar）
    root_now = sorted(q.name for q in target.iterdir() if q.is_file())
    assert root_now == names, root_now

    # ④ keep_versions=None ⇒ 不限份数（第三次 deploy 后 v001/v002 并存）
    run_deploy(str(tmp_path / "c.yaml"))
    arch = target / "_archive"
    assert (arch / "v001").is_dir() and (arch / "v002").is_dir()


@_needs_tech
def test_archive_without_sidecar_five_checks(tmp_path):
    """态 B：源**无**伴生 .gen.json ⇒ 归档三件（不得出现 .gen.json）。

    构造：把源复制到 tmp（不复制伴生 .gen.json）——sidecar 写入条件见
    deliver.py「_gen_src.is_file() 才写」，故无伴生件即不产 sidecar。
    """
    src = tmp_path / "no_gen_src.py"
    src.write_text(TECH_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    assert not Path(str(src) + ".gen.json").exists()

    target, r1, r2, first = _deploy_twice_capture(tmp_path, src)
    _assert_two_runs_ok(r1, r2)                           # ⑤

    v001 = target / "_archive" / "v001"
    assert v001.is_dir()                                  # ③

    names = sorted(q.name for q in v001.iterdir() if q.is_file())
    assert names == sorted(["tech_etf_mvo_rotation_trade.py",     # ① 三件，无 gen.json
                            "report.md",
                            "runbook.md"]), names
    assert not any(n.endswith(".gen.json") for n in names), names

    # ② 旧版内容 sha 一致
    assert _sha(v001 / "tech_etf_mvo_rotation_trade.py") == \
        first["tech_etf_mvo_rotation_trade.py"]

    # ③ 根下只余本次产物
    assert sorted(q.name for q in target.iterdir() if q.is_file()) == names


@_needs_tech
def test_archive_default_unlimited(tmp_path):
    """默认 keep_versions=None ⇒ 不限份数（保留原语义，断言按名而非数总数）。"""
    target = _run_twice(tmp_path)
    v001 = target / "_archive" / "v001"
    assert v001.is_dir()
    assert len(list(v001.iterdir())) >= 3          # product/report/runbook（+ 可能 sidecar）


def test_keep_versions_zero_disables_archive(tmp_path):
    target = _run_twice(tmp_path, keep_versions=0)
    assert not (target / "_archive").exists()


@_needs_tech
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

@_needs_tech
def test_archive_identity_distinguishable(tmp_path):
    """受控扰动 fixture（2026-11-13 R-20261113-01 裁甲）：证明归档的是**上一版内容身份**。

    关键：同输入同配置两次 deploy 的产物**字节可能相同**，此时「归档内 sha == 首版 sha」
    无判别力（归档错版本也会通过）。故在两次 deploy 之间对**临时 fixture**（不改正式策略）
    注入一个**模块级变量**（非注释——注释可能被转换器剥离，变量会进产物），使 B 版字节不同于 A 版。

    本用例验证「归档版本身份」，与「同输入确定性」是**两个不同测试**，
    不构成对确定性契约的放宽。
    """
    src = tmp_path / "dist_src.py"
    base = TECH_SRC.read_text(encoding="utf-8")
    src.write_text(base, encoding="utf-8")

    out = tmp_path / "out"
    cfg = tmp_path / "c.yaml"
    cfg.write_text(quickmode.build_quick_config(
        "tech_etf_mvo_rotation", str(src),
        output_root=str(out), dir_name="mystrat"), encoding="utf-8")

    r1 = run_deploy(str(cfg))                      # 版本 A
    target = out / "mystrat"
    prod = "tech_etf_mvo_rotation_trade.py"
    a_sha = {q.name: _sha(q) for q in target.iterdir() if q.is_file()}

    # 受控扰动：追加模块级变量（会进产物；注释有被剥离风险，故不用注释）
    src.write_text(base + "\n_QS_TEST_IDENTITY_MARK = 2\n", encoding="utf-8")
    r2 = run_deploy(str(cfg))                      # 版本 B
    b_sha = {q.name: _sha(q) for q in target.iterdir() if q.is_file()}

    # ① 先断言 A ≠ B（否则后续断言无判别力）
    assert a_sha[prod] != b_sha[prod], "扰动未生效：A/B 产物字节相同，归档身份不可判别"
    assert "_QS_TEST_IDENTITY_MARK" in (target / prod).read_text(encoding="utf-8"), \
        "扰动未进产物（可能被转换器剥离）——须换可验证扰动"

    # ② 归档 = A（内容身份，非 mtime）
    v001 = target / "_archive" / "v001"
    assert v001.is_dir()
    assert _sha(v001 / prod) == a_sha[prod], "归档内容不是版本 A"

    # ③ 根目录 = B
    assert _sha(target / prod) == b_sha[prod], "根目录不是版本 B"

    # ④ 两次 deploy 均成功（不以文件数代替）
    for tag, r in (("r1", r1), ("r2", r2)):
        st = r.to_dict().get("status") if hasattr(r, "to_dict") else getattr(r, "status", None)
        assert st != "FAIL", f"{tag} status=FAIL"


@_needs_tech
def test_archive_third_round_history(tmp_path):
    """第三次 deploy：v001 保留 A、v002 保留 B（历史不被覆盖，精确内容身份）。"""
    src = tmp_path / "hist_src.py"
    base = TECH_SRC.read_text(encoding="utf-8")
    src.write_text(base, encoding="utf-8")
    out = tmp_path / "out"
    cfg = tmp_path / "c.yaml"
    cfg.write_text(quickmode.build_quick_config(
        "tech_etf_mvo_rotation", str(src),
        output_root=str(out), dir_name="mystrat"), encoding="utf-8")
    prod = "tech_etf_mvo_rotation_trade.py"

    run_deploy(str(cfg))
    target = out / "mystrat"
    a = _sha(target / prod)
    src.write_text(base + "\n_QS_TEST_IDENTITY_MARK = 2\n", encoding="utf-8")
    run_deploy(str(cfg))
    b = _sha(target / prod)
    assert a != b
    src.write_text(base + "\n_QS_TEST_IDENTITY_MARK = 3\n", encoding="utf-8")
    run_deploy(str(cfg))

    arch = target / "_archive"
    assert sorted(d.name for d in arch.iterdir() if d.is_dir()) == ["v001", "v002"]
    assert _sha(arch / "v001" / prod) == a, "v001 应为 A"
    assert _sha(arch / "v002" / prod) == b, "v002 应为 B"


@_needs_tech
def test_keep_one_keeps_latest_only(tmp_path):
    """keep_versions=1 ⇒ 只余最新一份（精确保留最新，非「留下某个」）。"""
    src = tmp_path / "k1_src.py"
    base = TECH_SRC.read_text(encoding="utf-8")
    src.write_text(base, encoding="utf-8")
    out = tmp_path / "out"
    cfg = tmp_path / "c.yaml"
    cfg.write_text(quickmode.build_quick_config(
        "tech_etf_mvo_rotation", str(src),
        output_root=str(out), dir_name="mystrat", keep_versions=1), encoding="utf-8")
    prod = "tech_etf_mvo_rotation_trade.py"

    run_deploy(str(cfg))
    target = out / "mystrat"
    src.write_text(base + "\n_QS_TEST_IDENTITY_MARK = 2\n", encoding="utf-8")
    run_deploy(str(cfg))
    b = _sha(target / prod)
    src.write_text(base + "\n_QS_TEST_IDENTITY_MARK = 3\n", encoding="utf-8")
    run_deploy(str(cfg))

    arch = target / "_archive"
    dirs = sorted(d.name for d in arch.iterdir() if d.is_dir())
    assert len(dirs) == 1, f"keep=1 应只余 1 份，实际 {dirs}"
    assert _sha(arch / dirs[0] / prod) == b, "keep=1 保留的必须是最新版（B）"
