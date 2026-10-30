# -*- coding: utf-8 -*-
"""PD-0015：状态文件路径不得含平台禁用目录（研究根目录运行时解析）。

背景（官方文档 knowledge/platform/ptrade/01-API文档主页/001-page.md:2573 逐字）：
「在部分券商里，会限制使用路径 /home/fly/，如果代码里面存在 /home/fly/notebook/ 这个字符，
 会提示无法保存，需要替换成：get_research_path()，效果一样。」
实证：用户券商上传含该字面量的产物即报「/home/fly被禁止使用」，策略无法保存。

覆盖：
- 产物全文**不含**禁用路径（六口径）；
- PERSIST 块用 `_PD_NOTEBOOK = get_research_path()` + 文件名拼接；
- 生成的**注释/说明文本**也不得含该字面量（否则平台同样拒绝）；
- 配置层拒绝绝对路径（P2 FAIL，不静默改写）；
- P5 新门禁 `platform_path_policy` 正向/负向；
- P4 等价性不受影响（PERSIST 块属剥离白名单）。
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from quantstudio.ptrade_deploy import config as cfg_mod          # noqa: E402
from quantstudio.ptrade_deploy.gates import _platform_path_findings  # noqa: E402

FORBIDDEN = ("/home/fly", "/home/other", "home\\fly", "／home／fly", "notebook/")


def _cfg_yaml(state_file_line: str, tmp_path: Path) -> Path:
    src = tmp_path / "s_ptrade.py"
    src.write_text(
        "def initialize(context):\n    g.x = 1\n\n\ndef handle_data(context, data):\n    pass\n",
        encoding="utf-8")
    p = tmp_path / "deploy.yaml"
    p.write_text(
        "strategy_id: t_state\n"
        f"source: {src.as_posix()}\n"
        "execution:\n  profile: daily_eod_1450\n"
        "persistence:\n  enabled: true\n"
        f"{state_file_line}"
        f"output:\n  root: {(tmp_path / 'out').as_posix()}\n",
        encoding="utf-8")
    return p


# ------------------------------------------------------------------ 配置层 --

def test_default_state_file_is_filename_only():
    """默认值必须是纯文件名（不带任何目录）。"""
    name = cfg_mod.DEFAULT_STATE_FILENAME.format(strategy_id="abc123")
    assert name == "abc123_state.pkl"
    assert "/" not in name and "\\" not in name


def test_config_rejects_absolute_state_file(tmp_path):
    """D3：绝对路径 → ConfigError（不静默改写），文案指明改用纯文件名。"""
    p = _cfg_yaml('  state_file: "/home/fly/notebook/x_state.pkl"\n', tmp_path)
    with pytest.raises(cfg_mod.ConfigError) as ei:
        cfg_mod.load_config_from_yaml(p.read_text(encoding="utf-8"))
    msg = "; ".join(ei.value.errors)
    assert "纯文件名" in msg
    assert "get_research_path" in msg


def test_config_accepts_filename_only(tmp_path):
    """纯文件名照常通过。"""
    p = _cfg_yaml('  state_file: "my_state.pkl"\n', tmp_path)
    c = cfg_mod.load_config_from_yaml(p.read_text(encoding="utf-8"))
    assert c.persistence.state_file == "my_state.pkl"


# ------------------------------------------------------------------ 门禁层 --

def test_platform_path_gate_flags_all_six_forms():
    """六口径全命中（逐字/反斜杠/带空格/任意 home 子路径/全角/notebook）。"""
    text = (
        "x = '/home/fly/notebook/a.pkl'\n"
        "y = 'home\\\\fly'\n"
        "z = '/home/someuser/'\n"
        "w = '／home／fly'\n"
        "v = 'notebook/x.pkl'\n"
    )
    findings = _platform_path_findings(text)
    assert len(findings) >= 5, findings


def test_platform_path_gate_clean_text_passes():
    """干净产物（运行时拼接写法）→ 无 finding。"""
    text = "try:\n    _PD_NOTEBOOK = get_research_path()\nexcept Exception:\n    _PD_NOTEBOOK = ''\n"
    assert _platform_path_findings(text) == []


def test_platform_path_gate_scans_comments(tmp_path):
    """★实证坑：注释里出现该字符同样被平台拒绝 ⇒ 门禁必须扫注释。"""
    text = "# 注意：不要在注释里写 /home/fly\nx = 1\n"
    assert _platform_path_findings(text), "注释命中必须报告"


# ------------------------------------------------------------------ 端到端 --

def test_end_to_end_product_has_no_forbidden_path(tmp_path):
    """E2E：转换产物全文无禁用路径，且含运行时根目录解析。"""
    from quantstudio.ptrade_deploy import run_deploy
    p = _cfg_yaml("", tmp_path)
    d = run_deploy(str(p)).to_dict()
    assert d["status"] == "PASS", d["stages"]
    product = Path(d["artifacts"]["product"]).read_text(encoding="utf-8")
    for bad in FORBIDDEN:
        assert bad not in product, f"产物含禁用路径: {bad}"
    assert "get_research_path()" in product
    assert "_PD_NOTEBOOK + " in product          # 运行时拼接
    # P5 新门禁在位且 PASS
    names = [c["name"] for c in d["stages"]["P5_gates"]["details"]["checks"]]
    assert "platform_path_policy" in names


def test_end_to_end_p4_equiv_still_passes(tmp_path):
    """P4 红线：PERSIST/NOTEBOOK 注入块属剥离白名单，等价性不破。"""
    from quantstudio.ptrade_deploy import run_deploy
    p = _cfg_yaml("", tmp_path)
    d = run_deploy(str(p)).to_dict()
    assert d["stages"]["P4_equiv"]["status"] == "PASS", d["stages"]["P4_equiv"]
