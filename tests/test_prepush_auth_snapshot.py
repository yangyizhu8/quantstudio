# -*- coding: utf-8 -*-
"""授权快照机制验收（6 例）。对应方案 §3 + ②审三修订。

构造临时 git 仓复刻闸环境（scripts/prepush-gate.ps1 + docs/evidence/push-authorization/），
用 pwsh 调用闸脚本，断言退出码与提示文案。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "scripts" / "prepush-gate.ps1"


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                       text=True, encoding="utf-8")
    assert p.returncode == 0, f"git {args} failed: {p.stderr}"
    return p.stdout.strip()


def _mk_repo(tmp: Path, n_commits: int = 3) -> tuple[Path, list[str]]:
    """建临时仓：base 后追加 n 个提交，并把 origin/main 指向 base。"""
    (tmp / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(GATE, tmp / "scripts" / "prepush-gate.ps1")
    (tmp / "docs" / "evidence" / "push-authorization").mkdir(parents=True, exist_ok=True)
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    (tmp / "base.txt").write_text("base\n", encoding="utf-8")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", "base")
    base = _git(tmp, "rev-parse", "HEAD")
    shas = []
    for i in range(n_commits):
        (tmp / f"c{i}.txt").write_text(f"{i}\n", encoding="utf-8")
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-q", "-m", f"c{i}")
        shas.append(_git(tmp, "rev-parse", "HEAD"))
    _git(tmp, "update-ref", "refs/remotes/origin/main", base)
    return tmp, shas


def _snapshot(tmp: Path, tip: str, commits: list[str], name: str = "20261105-010000.json") -> Path:
    p = tmp / "docs" / "evidence" / "push-authorization" / name
    p.write_text(json.dumps({"authorized_at": "2026-11-05T01:00:00+08:00",
                             "authorized_by": "test", "tip": tip,
                             "commits": commits, "note": "test"},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _run_gate(tmp: Path, tip: str, base: str, *, allow: str = "", env_extra=None):
    # 注意：prepush-gate.ps1 只读 **-AllowAlso 参数**（环境变量 QS_PUSH_BUNDLE_ALLOWALSO
    # 是由 .githooks/pre-push 读取后再传参的），故此处必须以参数形式传入。
    import os
    e = dict(os.environ)
    if env_extra:
        e.update(env_extra)
    # 解释器探测（与 .githooks/pre-push 同策略：pwsh 优先，fallback powershell）
    ps = shutil.which("pwsh") or shutil.which("powershell")
    assert ps, "需要 pwsh 或 powershell"
    p = subprocess.run(
        [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(tmp / "scripts" / "prepush-gate.ps1"),
         "-Commit", tip, "-LocalSha", tip, "-RemoteSha", base,
         "-AllowAlso", (allow or "")],
        cwd=str(tmp), capture_output=True, text=True, encoding="utf-8", env=e)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _base(tmp: Path) -> str:
    return _git(tmp, "rev-parse", "refs/remotes/origin/main")


# ── V1：快照存在，tip == local ⇒ 通过 ───────────────────────────────────
def test_v1_tip_matches_local_passes(tmp_path):
    tmp, shas = _mk_repo(tmp_path, 3)
    _snapshot(tmp, shas[-1], shas)
    # 注意：快照层通过 ≠ 整体放行；C7 声明制仍要求范围其余笔被声明（两层叠加）
    rc, out = _run_gate(tmp, shas[-1], _base(tmp), allow=",".join(shas[:-1]))
    assert rc == 0, out
    assert "授权快照命中" in out


# ── V2：快照 tip 为中间笔，范围全在 commits ⇒ 通过 ──────────────────────
def test_v2_all_in_commits_passes(tmp_path):
    tmp, shas = _mk_repo(tmp_path, 3)
    _snapshot(tmp, shas[0], shas)          # tip 取最早那笔，但 commits 含全部
    rc, out = _run_gate(tmp, shas[-1], _base(tmp), allow=",".join(shas[:-1]))
    assert rc == 0, out
    assert "授权快照覆盖范围" in out


# ── V3：范围含授权外新增笔（今晚场景）⇒ exit 9 ──────────────────────────
def test_v3_unauthorized_extra_commit_blocked(tmp_path):
    tmp, shas = _mk_repo(tmp_path, 3)
    _snapshot(tmp, shas[0], shas[:2])      # 授权只含前两笔，第三笔是"授权后新增"
    rc, out = _run_gate(tmp, shas[-1], _base(tmp), allow=",".join(shas[:-1]))
    assert rc == 9, out
    assert "授权清单之外" in out
    assert "锚定授权末笔" in out, "必须给出正确动作提示"
    assert "C7 全量声明" in out, "必须重申「声明 != 授权」"


# ── V4：无快照 ⇒ 通过 + WARN（修订①） ─────────────────────────────────
def test_v4_no_snapshot_passes_with_warn(tmp_path):
    tmp, shas = _mk_repo(tmp_path, 2)
    rc, out = _run_gate(tmp, shas[-1], _base(tmp), allow=",".join(shas[:-1]))
    assert rc == 0, out
    assert "WARN 授权快照缺失" in out, "修订①：无快照必须 WARN 留痕"


# ── V5：回退开关 QS_PUSH_AUTH_SNAPSHOT=0 ⇒ 通过（不校验快照） ───────────
def test_v5_env_switch_disables_layer(tmp_path):
    tmp, shas = _mk_repo(tmp_path, 3)
    _snapshot(tmp, shas[0], shas[:2])      # 本应被 V3 拦
    rc, out = _run_gate(tmp, shas[-1], _base(tmp), allow=",".join(shas[:-1]),
                        env_extra={"QS_PUSH_AUTH_SNAPSHOT": "0"})
    assert rc == 0, out
    assert "授权快照" not in out, "开关关闭时不得执行快照层"


# ── V6：快照损坏 ⇒ fail-closed exit 9（修订②） ─────────────────────────
def test_v6_corrupt_snapshot_fails_closed(tmp_path):
    tmp, shas = _mk_repo(tmp_path, 2)
    bad = tmp / "docs" / "evidence" / "push-authorization" / "20261105-020000.json"
    bad.write_text("{ this is not json ", encoding="utf-8")
    rc, out = _run_gate(tmp, shas[-1], _base(tmp), allow=",".join(shas[:-1]))
    assert rc == 9, out
    assert "授权快照不可解析" in out


def test_v6b_missing_fields_fails_closed(tmp_path):
    """字段缺失同样 fail-closed（修订②：证据物不完整即停）。"""
    tmp, shas = _mk_repo(tmp_path, 2)
    p = tmp / "docs" / "evidence" / "push-authorization" / "20261105-030000.json"
    p.write_text(json.dumps({"authorized_by": "test", "commits": shas}), encoding="utf-8")
    rc, out = _run_gate(tmp, shas[-1], _base(tmp), allow=",".join(shas[:-1]))
    assert rc == 9, out
    assert "字段缺失" in out
