#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门2 单测：check-evidence-naming.py 的 legacy 逐件指纹 + 探针写路径隔离（2026-11-11 派单）。

派单指定负例：改写 legacy/ 内任一文件内容 ⇒ **必须报**（原实现只数件数，violations=0 静默放过）。
"""
import importlib.util
import io
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def load_mod():
    p = REPO / "scripts" / "check-evidence-naming.py"
    spec = importlib.util.spec_from_file_location("check_evidence_naming", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = load_mod()


def make_legacy(tmp_path, files):
    d = tmp_path / "legacy"
    d.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        io.open(d / name, "w", encoding="utf-8", newline="\n").write(content)
    return str(d)


def test_t1_unregistered_reported(tmp_path):
    """未登记文件 ⇒ unregistered（防「新增后冒充历史存量」）。"""
    d = make_legacy(tmp_path, {"a.json": "x"})
    res = M.verify_legacy(d, fingerprints={})
    assert res["files"] == 1
    assert res["unregistered"] == ["a.json"], res


def test_t2_negative_content_rewrite_reported(tmp_path):
    """★派单负例：改写已登记文件内容 ⇒ mutated（sha256 变化必须报）。"""
    d = make_legacy(tmp_path, {"a.json": "original"})
    fp = {"a.json": {"sha256": M.sha256_file(os.path.join(d, "a.json")),
                     "mtime": "", "why": "baseline"}}
    ok = M.verify_legacy(d, fingerprints=fp)
    assert ok["mutated"] == [] and ok["ok"] == 1, ok

    io.open(os.path.join(d, "a.json"), "w", encoding="utf-8").write("TAMPERED")
    bad = M.verify_legacy(d, fingerprints=fp)
    assert len(bad["mutated"]) == 1, bad
    rel, old, now = bad["mutated"][0]
    assert rel == "a.json" and old != now


def test_t3_missing_registered_reported(tmp_path):
    d = make_legacy(tmp_path, {})
    res = M.verify_legacy(d, fingerprints={"gone.json": {"sha256": "0" * 64}})
    assert res["missing"] == ["gone.json"], res


def test_t4_content_same_mtime_drift_is_warn_only(tmp_path):
    """内容未变、仅 mtime 变 ⇒ 只进 mtime_drift（不进 mutated，不阻断）。"""
    d = make_legacy(tmp_path, {"a.json": "same"})
    p = os.path.join(d, "a.json")
    fp = {"a.json": {"sha256": M.sha256_file(p), "mtime": "2000-01-01 00:00:00", "why": "b"}}
    res = M.verify_legacy(d, fingerprints=fp)
    assert res["mutated"] == [], res
    assert len(res["mtime_drift"]) == 1, res


def test_t5_probe_isolation_detects_archive_write(tmp_path):
    """探针脚本写 docs/evidence ⇒ 隔离违规（归档只可由「移入」写入）。"""
    probe = tmp_path / "probe"
    probe.mkdir()
    io.open(probe / "bad_probe.py", "w", encoding="utf-8", newline="\n").write(
        'out = open("docs/evidence/probe_result.json", "w")\n')
    io.open(probe / "ok_probe.py", "w", encoding="utf-8", newline="\n").write(
        'out = open("tmp/result.json", "w")\n')
    viol = M.verify_probe_isolation(str(probe))
    files = [v[0] for v in viol]
    assert "bad_probe.py" in files and "ok_probe.py" not in files, viol


def test_t6_real_repo_probe_scan_runs():
    """真实 docs/probe 扫描可跑通（命中数如实返回，不崩）。"""
    viol = M.verify_probe_isolation(str(REPO / "docs" / "probe"))
    assert isinstance(viol, list)
    print("real probe violations=%d" % len(viol))
    for v in viol[:5]:
        print("  %s → %s" % (v[0], v[1]))
