# -*- coding: utf-8 -*-
"""笔5 验收（V7 机制）：duckdb 版本闸 —— 非 1.4.x 拒启。

判据：
  C1 合规版本（1.4.x）→ 放行，不抛异常
  C2 不合规（1.5.3 / 1.5.4）→ `SystemExit(3)`（拒启）
  C3 逃生阀 `QS_DUCKDB_VERSION_GATE=0` → 放行（且留警告）
  C4 无法导入 duckdb → 视为不合规（拒启，不静默放行）
注：V7 的**真实进程触发**（用 venv_miniQMT 1.5.3 实起 GUI/daemon）在实施验收时另行实测，
   本文件只固化机制（「验证器过 ≠ 闸过」）。
"""
from __future__ import annotations

import sys
import types

import pytest

from quantstudio.pipeline import duckdb_version_gate as gate


def _fake_duckdb(version: str):
    mod = types.ModuleType("duckdb")
    mod.__version__ = version
    return mod


def test_c1_compliant_passes(monkeypatch):
    monkeypatch.setitem(sys.modules, "duckdb", _fake_duckdb("1.4.5"))
    ok, ver = gate.check_duckdb_version()
    assert ok is True and ver == "1.4.5"
    gate.require_duckdb_version("test")          # 不应抛异常


@pytest.mark.parametrize("bad", ["1.5.3", "1.5.4", "1.6.0", "2.0.0", "unknown"])
def test_c2_non_compliant_refuses(monkeypatch, bad):
    monkeypatch.setitem(sys.modules, "duckdb", _fake_duckdb(bad))
    monkeypatch.delenv(gate.GATE_ENV, raising=False)
    ok, ver = gate.check_duckdb_version()
    assert ok is False and ver == bad
    with pytest.raises(SystemExit) as ei:
        gate.require_duckdb_version("test")
    assert ei.value.code == 3


def test_c3_escape_hatch_allows(monkeypatch):
    monkeypatch.setitem(sys.modules, "duckdb", _fake_duckdb("1.5.3"))
    monkeypatch.setenv(gate.GATE_ENV, "0")
    gate.require_duckdb_version("test")          # 显式放行：不应抛异常


def test_c4_import_failure_refuses(monkeypatch):
    # sys.modules[name] = None → `import duckdb` 抛 ImportError（Python 官方语义）
    monkeypatch.setitem(sys.modules, "duckdb", None)
    ok, detail = gate.check_duckdb_version()
    assert ok is False and "无法导入" in detail
    monkeypatch.delenv(gate.GATE_ENV, raising=False)
    with pytest.raises(SystemExit):
        gate.require_duckdb_version("test")
