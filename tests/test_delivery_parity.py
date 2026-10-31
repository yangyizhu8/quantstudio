# -*- coding: utf-8 -*-
"""双端对等与配置健壮性（W4；SC-009 / SC-010）。

SC-009 的机检口径：**同一输入、同一配置，两个入口产出的文件清单与逐文件 SHA256 必须全等**。
本文件用「CLI 子进程 vs 内核直调」做机检，并静态断言 GUI 薄壳调用的是同一内核函数
（薄壳不可有第二套实现，否则哈希门会被绕开）。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quantstudio.delivery import config as delivery_config
from quantstudio.delivery import export_kernel
from quantstudio.delivery import manifest as M
from tests.fixtures.delivery import Env

REPO = Path(__file__).resolve().parents[1]


def _hashes(bundle_dir: Path) -> dict:
    doc = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    return {item["path"]: (item["bytes"], item["sha256"]) for item in doc["files"]}


def test_sc009_cli_and_kernel_produce_identical_hashes(tmp_path, monkeypatch):
    server = Env(tmp_path / "server", monkeypatch)
    kernel_bundle = server.make_package()          # 内核直调
    cli_root = tmp_path / "cli_out"

    env = dict(os.environ)
    env["QUANTSTUDIO_DATA_ROOT"] = str(server.root)
    env["QS_WRITE_LOCK_DIR"] = str(tmp_path / "_lock")
    env["PYTHONPATH"] = str(REPO)
    proc = subprocess.run(
        [sys.executable, "-m", "quantstudio.delivery.cli", "export",
         "--export-dir", str(cli_root), "--config-dir", str(server.config_root),
         "--json", "--quiet"],
        capture_output=True, env=env, timeout=600,
    )
    assert proc.returncode == 0, (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace")[-800:],
        proc.stderr.decode("utf-8", "replace")[-800:],
    )
    line = [ln for ln in proc.stdout.decode("utf-8", "replace").splitlines() if ln.startswith('{"ok"')][-1]
    cli_bundle = Path(json.loads(line)["result"]["bundle_dir"])

    assert _hashes(cli_bundle) == _hashes(kernel_bundle), "CLI 与内核两次导出的文件哈希必须全等（SC-009）"


def test_sc009_gui_shell_uses_same_kernel():
    """GUI 薄壳不得有第二套实现：worker 必须直接调用内核函数，且不得自行拼 manifest。"""
    src = (REPO / "quantstudio" / "gui" / "workers.py").read_text(encoding="utf-8")
    assert "class DeliveryExportWorker" in src
    assert "export_bundle" in src, "worker 必须调用内核 export_bundle"
    assert "manifest" not in src.split("class DeliveryExportWorker")[1].split("class DeliveryVerifyWorker")[0].lower(), \
        "worker 不得自行拼装 manifest（薄壳只做参数收集与展示）"

    tab = (REPO / "quantstudio" / "gui" / "tabs" / "delivery_tab.py").read_text(encoding="utf-8")
    imports = [
        ln for ln in tab.splitlines()
        if ln.strip().startswith(("import ", "from "))
    ]
    assert not [ln for ln in imports if "delivery" in ln], \
        "GUI 页签不得直接 import 内核（必须经 worker）"


def test_sc010_corrupt_config_is_tolerated(tmp_path):
    """配置损坏/半截 JSON ⇒ 容错加载（warnings，不崩），且路径退化为 None。"""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "delivery_config.json").write_text('{"export_dir": "D:/x", ', encoding="utf-8")

    cfg, warnings = delivery_config.load(cfg_dir)
    assert warnings, "损坏配置必须给 warning（不得静默）"
    assert not cfg.export_dir and not cfg.import_dir


def test_sc010_config_only_two_keys(tmp_path):
    """配置只有两个键：多余键被忽略，非法类型退化为 None。"""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "delivery_config.json").write_text(
        json.dumps({"export_dir": "D:/out", "import_dir": 123, "evil": "x"}, ensure_ascii=False),
        encoding="utf-8",
    )
    cfg, warnings = delivery_config.load(cfg_dir)
    assert cfg.export_dir == "D:/out"
    assert not cfg.import_dir, "非字符串必须退化为空（不得使用）"
    assert "evil" not in json.dumps(cfg.__dict__, ensure_ascii=False)


def test_sc010_set_roundtrip_atomic(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg, _ = delivery_config.load(cfg_dir)
    cfg.export_dir = "D:/out"
    cfg.import_dir = "D:/in"
    path = delivery_config.save(cfg, cfg_dir)
    assert path.exists()
    again, _ = delivery_config.load(cfg_dir)
    assert (again.export_dir, again.import_dir) == ("D:/out", "D:/in")
    assert not list(cfg_dir.glob("*.tmp*")), "原子写不得残留临时件"


def test_cli_help_exit_zero(capsys):
    """CLI smoke：--help 退出码 0，且列出五个子命令。"""
    from quantstudio.delivery import cli

    code = cli.main(["--help"])
    assert code == 0
    out = capsys.readouterr().out
    for name in ("export", "verify", "import", "recover", "set"):
        assert name in out
