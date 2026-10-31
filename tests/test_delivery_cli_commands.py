# -*- coding: utf-8 -*-
"""CLI 壳层真实执行（hotfix 2026-11-01：堵住「壳层调用点零执行覆盖」的洞）。

背景：`verify` / `import` 两处调用点把 `_Progress(quiet=...)` 写成 `_Progress(enabled=...)`，
用户真跑 verify 时抛 `TypeError: __init__() got an unexpected keyword argument 'enabled'`；
而当时的 CLI smoke 只断言 `--help` 退出码 0 ⇒ **handler 一行未执行**，测试全绿却带病交付。

本文件的最低判据（审核方已定为后续特性验收的 CLI smoke 下限）：
**每个子命令都在真实路径上跑一次**，断言退出码；**不 mock `_Progress`、不 mock handler**。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quantstudio.delivery import cli
from quantstudio.delivery.constants import Exit
from tests.fixtures.delivery import Env


def test_cli_all_six_subcommands_run_real(tmp_path, monkeypatch):
    server = Env(tmp_path / "server", monkeypatch)
    bundle = server.make_package()
    client = server.make_client("update")
    cfg = str(server.config_root)

    # 导出侧用「三件齐全」的源根；导入侧用只带两件的客户端根（贴近真实：客户缺一台）
    monkeypatch.setattr("quantstudio._paths.DATA_ROOT", server.root, raising=False)
    monkeypatch.setenv("QUANTSTUDIO_DATA_ROOT", str(server.root))
    monkeypatch.setenv("QS_WRITE_LOCK_DIR", str(tmp_path / "_lock"))

    out_dir = tmp_path / "out"

    # ① export（六相位全程）
    assert cli.main(["export", "--export-dir", str(out_dir), "--config-dir", cfg,
                     "--quiet", "--json"]) == Exit.OK
    bundles = [p for p in out_dir.iterdir() if (p / "manifest.json").exists()]
    assert len(bundles) == 1
    produced = bundles[0]

    # ② status
    assert cli.main(["status", "--config-dir", cfg, "--json"]) == Exit.OK

    # ③ verify（本次 hotfix 的直接复现点：曾在此抛 TypeError）
    assert cli.main(["verify", "--import-dir", str(out_dir), "--config-dir", cfg,
                     "--quiet", "--json"]) == Exit.OK

    # ④ import --dry-run（只读演练，不写盘）
    assert cli.main(["import", "--import-dir", str(out_dir), "--config-dir", cfg,
                     "--dry-run", "--quiet", "--json"]) == Exit.OK

    # ⑤ import 真跑（另一处曾抛 TypeError 的调用点）
    monkeypatch.setattr("quantstudio._paths.DATA_ROOT", client, raising=False)
    monkeypatch.setenv("QUANTSTUDIO_DATA_ROOT", str(client))
    assert cli.main(["import", "--import-dir", str(out_dir), "--config-dir", cfg,
                     "--quiet", "--json"]) == Exit.OK

    # ⑥ set（配置两键）
    assert cli.main(["set", "--export-dir", str(out_dir), "--import-dir", str(out_dir),
                     "--config-dir", cfg, "--json"]) == Exit.OK

    # ⑦ recover（无残留 journal ⇒ 明确的非零退出码，仍是真实执行）
    assert cli.main(["recover", "--action", "rollback", "--config-dir", cfg,
                     "--json"]) == Exit.RECOVERY_NEEDED


def test_cli_progress_signature_matches_call_sites():
    """静态门：所有 `_Progress(` 调用点只允许 `quiet=` 关键字（防同类签名漂移再发生）。"""
    import re

    src = (Path(cli.__file__)).read_text(encoding="utf-8")
    calls = re.findall(r"_Progress\(([^)]*)\)", src)
    assert calls, "至少应有 3 处调用点"
    for args in calls:
        assert "enabled" not in args, f"发现漂移调用点：_Progress({args})"
        if args.strip():
            assert "quiet=" in args, f"调用点参数可疑：_Progress({args})"

def test_cli_verify_accepts_bundle_dir_directly(tmp_path, monkeypatch):
    """用户实跑路径复现：`verify --import-dir <包目录>`（而非上一层）必须通过。"""
    server = Env(tmp_path / "server", monkeypatch)
    bundle = server.make_package()
    cfg = str(server.config_root)

    monkeypatch.setattr("quantstudio._paths.DATA_ROOT", server.root, raising=False)
    monkeypatch.setenv("QUANTSTUDIO_DATA_ROOT", str(server.root))
    monkeypatch.setenv("QS_WRITE_LOCK_DIR", str(tmp_path / "_lock"))

    assert cli.main(["verify", "--import-dir", str(bundle), "--config-dir", cfg,
                     "--quiet", "--json"]) == Exit.OK
    # 上一层仍可用（不回退）
    assert cli.main(["verify", "--import-dir", str(bundle.parent), "--config-dir", cfg,
                     "--quiet", "--json"]) == Exit.OK

def test_cli_verify_prints_state_summary_and_attention(tmp_path, monkeypatch, capsys):
    """四态汇总必须出现在人读输出里（一行 groupby），且 JSON 带 state_counts / attention。"""
    from quantstudio.delivery import cli
    from quantstudio.delivery.constants import Exit
    from tests.fixtures.delivery import Env

    server = Env(tmp_path / "server", monkeypatch)
    bundle = server.make_package()
    cfg = str(server.config_root)
    monkeypatch.setattr("quantstudio._paths.DATA_ROOT", server.root, raising=False)
    monkeypatch.setenv("QUANTSTUDIO_DATA_ROOT", str(server.root))
    monkeypatch.setenv("QS_WRITE_LOCK_DIR", str(tmp_path / "_lock"))

    assert cli.main(["verify", "--import-dir", str(bundle), "--config-dir", cfg, "--quiet"]) == Exit.OK
    out = capsys.readouterr().out
    assert "状态汇总：" in out, out
    assert "OK=" in out or "NO-WATERMARK-SYSTEM=" in out

    capsys.readouterr()
    assert cli.main(["verify", "--import-dir", str(bundle), "--config-dir", cfg,
                     "--quiet", "--json"]) == Exit.OK
    payload = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith('{"ok"')][-1]
    import json as _json

    doc = _json.loads(payload)["result"]
    counts = doc["state_counts"]
    assert sum(counts.values()) == len(doc["tables"]), "各态之和必须等于表数"
    assert set(counts) >= {"OK", "BEHIND", "AHEAD", "MISSING", "UNKNOWN-BASIS", "NO-WATERMARK-SYSTEM"}
    assert isinstance(doc["attention"], list)
