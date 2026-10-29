# -*- coding: utf-8 -*-
"""duckdb 版本闸契约测试（spec 002-duckdb-1.5-upgrade · FR-009）。

放行集合：{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}（唯一判定源
`quantstudio/pipeline/duckdb_version_policy.py`）。

判据：
  C1 集合内版本（1.4.5 / 1.4.9 / 1.5.6）→ 放行，不抛异常
  C2 集合外版本（1.4.4 / 1.5.0 / 1.5.5 / 1.6.0 / 2.0.1 / 2.1）→ `SystemExit(3)`（拒启）
  C3 逃生阀 `QS_DUCKDB_VERSION_GATE=0` → 放行（且留警告）
  C4 无法导入 duckdb → 视为不合规（拒启，不静默放行）
  C5 版本串不可解析 → 视为不合规（fail-closed）
  C6 **单源同一性**：门禁模块与判定源共用同一个 `ALLOWED_RANGES` 对象
     （杜绝「两处各持一份规则」的漂移）

注：V7 机制的真实进程触发（GUI / daemon / 子进程三形态）在实施验收时另行实测，
   本文件只固化机制（「验证器过 ≠ 闸过」）。
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

from quantstudio.pipeline import duckdb_version_gate as gate
from quantstudio.pipeline import duckdb_version_policy as policy

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_FILE = REPO_ROOT / "quantstudio" / "pipeline" / "duckdb_version_policy.py"

# 契约点（业务期望，非规则表副本）：(版本, 是否放行)
CONTRACT_POINTS = [
    ("1.4.5", True),    # 集合下界（含端点）
    ("1.4.9", True),
    ("1.5.6", True),    # 精确放行点
    ("1.4.4", False),   # 集合下界外
    ("1.5.0", False),
    ("1.5.5", False),   # 事故版本（09 月 FATAL 实证版本）
    ("1.6.0", False),
    ("2.0.1", False),   # 第二跳（已降级为可选项，仍不在首跳集合）
    ("2.1", False),
]


def _fake_duckdb(version: str):
    mod = types.ModuleType("duckdb")
    mod.__version__ = version
    return mod


@pytest.mark.parametrize("version,allowed", CONTRACT_POINTS)
def test_c1_c2_contract_points(monkeypatch, version, allowed):
    """C1/C2：集合内放行、集合外拒启（拒启退出码固定为 3）。"""
    monkeypatch.setitem(sys.modules, "duckdb", _fake_duckdb(version))
    monkeypatch.delenv(gate.GATE_ENV, raising=False)
    ok, ver = gate.check_duckdb_version()
    assert ok is allowed, f"{version} 判定与契约不符（期望 allowed={allowed}）"
    assert ver == version
    if allowed:
        gate.require_duckdb_version("test")          # 不应抛异常
    else:
        with pytest.raises(SystemExit) as ei:
            gate.require_duckdb_version("test")
        assert ei.value.code == 3


def test_c3_escape_hatch_allows(monkeypatch):
    """C3：显式放行开关语义不变（放行 + 留警告）。"""
    monkeypatch.setitem(sys.modules, "duckdb", _fake_duckdb("1.5.5"))
    monkeypatch.setenv(gate.GATE_ENV, "0")
    gate.require_duckdb_version("test")              # 显式放行：不应抛异常


def test_c4_import_failure_refuses(monkeypatch):
    """C4：依赖不可导入 ⇒ 无法判定即拒绝（fail-closed）。"""
    monkeypatch.setitem(sys.modules, "duckdb", None)  # `import duckdb` 抛 ImportError
    monkeypatch.delenv(gate.GATE_ENV, raising=False)
    ok, detail = gate.check_duckdb_version()
    assert ok is False and "无法导入" in detail
    with pytest.raises(SystemExit):
        gate.require_duckdb_version("test")


@pytest.mark.parametrize("bad", ["unknown", "1.5", "not-a-version", "1.5.6.dev0", "1.5.6rc1"])
def test_c5_unparsable_refuses(monkeypatch, bad):
    """C5：版本串不可解析 ⇒ 判为不合规（fail-closed，不按前缀猜测）。"""
    monkeypatch.setitem(sys.modules, "duckdb", _fake_duckdb(bad))
    monkeypatch.delenv(gate.GATE_ENV, raising=False)
    ok, ver = gate.check_duckdb_version()
    assert ok is False and ver == bad
    with pytest.raises(SystemExit):
        gate.require_duckdb_version("test")


def test_c5b_empty_version_string_refuses(monkeypatch):
    """C5b：版本串为空 ⇒ 判为不合规（fail-closed）。"""
    monkeypatch.setitem(sys.modules, "duckdb", _fake_duckdb(""))
    monkeypatch.delenv(gate.GATE_ENV, raising=False)
    ok, detail = gate.check_duckdb_version()
    assert ok is False and "版本串为空" in detail
    with pytest.raises(SystemExit):
        gate.require_duckdb_version("test")


# 形态分层矩阵（H1 · 2026-10-29 总调度裁定「严格三段 + fail-closed 优先」）：
#   严格三段 → 按集合判定（0/1）；两段或带预发布/构建后缀 ⇒ 可识别但非严格形态 ⇒ FAIL(1)；
#   无法解析 ⇒ ERROR(2)。
SHAPE_CASES = [
    ("1.4.5", 0), ("1.5.6", 0),
    ("1.5.6.dev0", 1), ("1.5.6.post1", 1), ("1.5", 1), ("2.1", 1),
    ("1.5.6rc1", 2), ("unknown", 2), ("not-a-version", 2),
]


@pytest.mark.parametrize("version,rc", SHAPE_CASES)
def test_cli_shape_layers(version, rc):
    """C7：版本串形态分层的退出码与首词（strict 0/1；非严格形态 1；不可解析 2）。"""
    proc = subprocess.run(
        [sys.executable, str(POLICY_FILE), "--check", version],
        capture_output=True, text=True)
    assert proc.returncode == rc, f"{version}: rc={proc.returncode} 期望 {rc}"
    first = proc.stdout.strip().splitlines()[0]
    assert first.startswith({0: "PASS ", 1: "FAIL ", 2: "ERROR "}[rc]), first


def test_c6_single_source_identity():
    """C6：门禁模块与判定源共用同一份 `ALLOWED_RANGES`（杜绝第二份规则）。"""
    assert gate.ALLOWED_RANGES is policy.ALLOWED_RANGES
    assert gate.is_allowed is policy.is_allowed
    assert gate.parse_version is policy.parse_version


@pytest.mark.parametrize("version,allowed", CONTRACT_POINTS)
def test_cli_matches_in_process(version, allowed):
    """CLI 与进程内判定逐项一致（退出码 0/1 对应放行/拒绝）。"""
    proc = subprocess.run(
        [sys.executable, str(POLICY_FILE), "--check", version],
        capture_output=True, text=True)
    expected_rc = 0 if allowed else 1
    assert proc.returncode == expected_rc, f"{version}: rc={proc.returncode} 期望 {expected_rc}"
    first = proc.stdout.strip().splitlines()[0]
    assert first.startswith("PASS " if allowed else "FAIL ")


def test_cli_unparsable_exit_code():
    """CLI：不可解析 ⇒ 退出码 2 且 stdout 以 ERROR 开头（fail-closed）。"""
    proc = subprocess.run(
        [sys.executable, str(POLICY_FILE), "--check", "unknown"],
        capture_output=True, text=True)
    assert proc.returncode == 2
    assert proc.stdout.strip().startswith("ERROR ")


def test_policy_describe_mentions_both_branches():
    """放行集合描述须同时含区间分支与精确点分支（文案漂移防线）。"""
    text = policy.describe_policy()
    assert "1.4.5" in text and "1.5" in text and "1.5.6" in text
