#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门1 单测：check-staged-scope.py（2026-11-11 派单）。

派单指定负例：故意 `git add -A` 带入他线文件 ⇒ **必须拒**。
测试方式：subprocess 调脚本 + `--index-file`（离线注入 index 清单，不依赖真实 git 状态）。
"""
import io
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check-staged-scope.py"


def run(tmp_path, index_lines, allow=(), expect=0):
    idx = tmp_path / "index.txt"
    io.open(idx, "w", encoding="utf-8", newline="\n").write("\n".join(index_lines) + "\n")
    cmd = [sys.executable, str(SCRIPT), "--index-file", str(idx), "--repo", str(REPO)]
    for a in allow:
        cmd += ["--allow", a]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return r, expect


def test_t1_all_in_scope_passes(tmp_path):
    r, _ = run(tmp_path, ["scripts/kb_hook.py", "tests/test_x.py"],
               allow=["scripts/kb_hook.py", "tests/"])
    assert r.returncode == 0, r.stdout + r.stderr


def test_t2_negative_add_A_with_foreign_file_blocks(tmp_path):
    """★派单负例：git add -A 把他线产物带入 index ⇒ 必须拒（exit 1）。"""
    r, _ = run(tmp_path,
               ["scripts/kb_hook.py", "review-kb/daily/2026-11-11-复盘报告.md"],
               allow=["scripts/kb_hook.py"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "review-kb/daily" in r.stdout, r.stdout


def test_t3_no_allowlist_fail_closed(tmp_path):
    """未声明白名单 ⇒ 拒（fail-closed），且 reason 可见。"""
    r, _ = run(tmp_path, ["scripts/kb_hook.py"], allow=[])
    assert r.returncode == 1
    assert "未声明" in r.stdout or "no-allowlist-declared" in r.stdout, r.stdout


def test_t4_explicit_star_passes(tmp_path):
    r, _ = run(tmp_path, ["anything/at/all.txt", "b.md"], allow=["*"])
    assert r.returncode == 0, r.stdout + r.stderr


def test_t5_glob_and_dir_prefix(tmp_path):
    r, _ = run(tmp_path, ["docs/handoff/x.md", "knowledge/db/DB-2026-0001.md"],
               allow=["docs/handoff/*.md", "knowledge/db/"])
    assert r.returncode == 0, r.stdout + r.stderr


def test_t6_self_test_builtin():
    """脚本内置 --self-test 五例（含 add -A 负例）必须全过。"""
    r = subprocess.run([sys.executable, str(SCRIPT), "--self-test"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SELF_TEST: PASS" in r.stdout, r.stdout
