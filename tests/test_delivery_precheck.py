# -*- coding: utf-8 -*-
"""前置探测与配置（T008 / T013 / T014；FR-020/021/035、FR-038、plan §7）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quantstudio.delivery import config as C
from quantstudio.delivery import probe as P
from quantstudio.delivery import winenv as W


# ── 配置（FR-020/021、SC-010）───────────────────────────────────────────────
def test_load_missing_config_is_not_an_error(tmp_path):
    cfg, warnings = C.load(tmp_path)
    assert cfg.export_dir == "" and cfg.import_dir == ""
    assert warnings == []


def test_load_corrupted_config_degrades_with_warning(tmp_path):
    (tmp_path / "delivery_config.json").write_text("{broken", encoding="utf-8")
    cfg, warnings = C.load(tmp_path)
    assert cfg.export_dir == ""
    assert warnings and "无法解析" in warnings[0]


def test_config_has_exactly_two_paths_and_no_backup_mode(tmp_path):
    C.save(C.DeliveryConfig(export_dir=str(tmp_path / "e"), import_dir=str(tmp_path / "i")), tmp_path)
    doc = json.loads((tmp_path / "delivery_config.json").read_text(encoding="utf-8"))
    assert set(doc) == {"config_version", "export_dir", "import_dir"}
    assert "backup_mode" not in doc, "v3 复审裁定：不得提供备份方式开关"


def test_config_roundtrip_and_buttons(tmp_path):
    cfg = C.DeliveryConfig(export_dir=str(tmp_path / "e"), import_dir="")
    C.save(cfg, tmp_path)
    loaded, warnings = C.load(tmp_path)
    assert loaded.export_dir == cfg.export_dir
    assert warnings == []
    assert C.buttons_enabled(loaded) == (True, False)


def test_legacy_backup_mode_field_warns_but_loads(tmp_path):
    doc = {
        "config_version": 1,
        "export_dir": "",
        "import_dir": "",
        "backup_mode": "copy",
    }
    (tmp_path / "delivery_config.json").write_text(json.dumps(doc), encoding="utf-8")
    _, warnings = C.load(tmp_path)
    assert any("backup_mode" in w for w in warnings)


# ── 路径互斥（FR-035）───────────────────────────────────────────────────────
def test_paths_disjoint_detects_containment(tmp_path):
    data_root = tmp_path / "data"
    export_dir = data_root / "export"          # 落在 data/ 内 ⇒ 违规
    import_dir = tmp_path / "incoming"
    violations = P.paths_disjoint(
        {"DATA_ROOT": data_root, "export_dir": export_dir, "import_dir": import_dir}
    )
    assert violations and "export_dir" in violations[0]


@pytest.mark.parametrize("relation", ["child", "parent", "same"])
def test_paths_disjoint_rejects_all_nesting(tmp_path, relation):
    a = tmp_path / "a"
    if relation == "child":
        b = a / "b"
    elif relation == "parent":
        b = tmp_path
    else:
        b = a
    assert P.paths_disjoint({"a": a, "b": b})


def test_paths_disjoint_accepts_siblings(tmp_path):
    assert P.paths_disjoint({"a": tmp_path / "a", "b": tmp_path / "b"}) == []


# ── 空间门槛（FR-015 / SC-011）──────────────────────────────────────────────
def test_space_requirements_follow_contract():
    assert P.required_export_bytes([1000]) == int(1000 * 1.1) + P.SPACE_MARGIN_BYTES
    same = P.required_import_bytes(1000, same_volume=True)
    cross = P.required_import_bytes(1000, same_volume=False, backup_bytes=800)
    assert cross > same, "跨卷必须把备份体积计入门槛（SC-011）"
    assert P.disk_free_bytes(Path.cwd()) > 0


def test_same_volume_true_within_tmp(tmp_path):
    a = tmp_path / "x"
    b = tmp_path / "y"
    a.mkdir()
    b.mkdir()
    assert P.same_volume(a, b) is True


# ── 包定位（FR-038）─────────────────────────────────────────────────────────
def test_locate_package_zero_one_many(tmp_path):
    bundle, problems = P.locate_package(tmp_path)
    assert bundle is None and problems

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "manifest.json").write_text("{}", encoding="utf-8")
    bundle, problems = P.locate_package(tmp_path)
    assert bundle == (tmp_path / "pkg") and problems == []

    (tmp_path / "pkg2").mkdir()
    (tmp_path / "pkg2" / "manifest.json").write_text("{}", encoding="utf-8")
    bundle, problems = P.locate_package(tmp_path)
    assert bundle is None and problems and "多个" in problems[0]


# ── daemon 判定（fail-closed）───────────────────────────────────────────────
def test_daemon_absent_when_status_missing(tmp_path):
    state = P.daemon_running(tmp_path / "daemon_status.json")
    assert state.running is False


def test_daemon_status_unparsable_is_fail_closed(tmp_path):
    bad = tmp_path / "daemon_status.json"
    bad.write_text("{broken", encoding="utf-8")
    state = P.daemon_running(bad)
    assert state.running is True, "status 损坏不得判为未运行（fail-closed）"


# ── Windows 探针降级（W-1/W-2/W-3）──────────────────────────────────────────
def test_probe_dir_writable_on_tmp(tmp_path):
    ok, detail = W.probe_dir_writable(tmp_path)
    assert ok, detail


def test_probe_file_handle_on_existing_file(tmp_path):
    target = tmp_path / "quantstudio.db"
    target.write_bytes(b"x" * 8)
    ok, detail = W.probe_file_handle(target)
    assert ok, detail


def test_probe_file_handle_missing_file_fails(tmp_path):
    ok, _ = W.probe_file_handle(tmp_path / "nope.db")
    assert ok is False


def test_detect_sync_dir_by_path_feature(tmp_path):
    fake = tmp_path / "OneDrive - Acme" / "delivery"
    fake.mkdir(parents=True)
    hit, reason = W.detect_sync_dir(fake)
    assert hit and "同步盘" in reason


def test_detect_sync_dir_plain_path_is_clean(tmp_path):
    hit, _ = W.detect_sync_dir(tmp_path)
    assert hit is False


def test_check_path_length_warns_on_long_path(tmp_path):
    long_path = tmp_path / ("x" * 240)
    ok, detail = W.check_path_length(long_path)
    assert ok is False and "MAX_PATH" in detail
    ok_short, _ = W.check_path_length(tmp_path / "short")
    assert ok_short is True

def test_locate_package_accepts_bundle_dir_itself(tmp_path):
    """热修（2026-11-01）：用户直接指向**包目录本身**也必须可用（实跑报错复现点）。"""
    pkg = tmp_path / "20261101_002051"
    pkg.mkdir()
    (pkg / "manifest.json").write_text("{}", encoding="utf-8")
    bundle, problems = P.locate_package(pkg)
    assert bundle == pkg and problems == [], "指向包目录本身时应直接命中"
    # 上一层仍可用（行为不回退）
    bundle2, problems2 = P.locate_package(tmp_path)
    assert bundle2 == pkg and problems2 == []


def test_locate_package_error_message_mentions_both_ways(tmp_path):
    """报错文案须同时给出两种可选方式（避免像旧文案那样把人引向唯一解）。"""
    bundle, problems = P.locate_package(tmp_path)
    assert bundle is None
    assert "manifest.json" in problems[0]
    assert "上一层" in problems[0]

def test_resolve_config_root_accepts_both_forms(tmp_path):
    """热修（2026-11-01）：--config-dir 传「配置根」或「profile 目录」都必须归一正确。"""
    from quantstudio.delivery import config as C

    root = tmp_path / "config"
    prof = root / "profiles" / "mcp_only"
    prof.mkdir(parents=True)
    (prof / "questdb_table_map.json").write_text("{}", encoding="utf-8")

    assert C.resolve_config_root(root) == root
    assert C.resolve_config_root(prof) == root, "传 profile 目录时应退两级回到 config/"


def test_locate_and_config_root_end_to_end_via_cli(tmp_path, monkeypatch):
    """两个热修点的组合复现：包目录直选 + profile 目录作 --config-dir ⇒ 必须 exit 0。"""
    from quantstudio.delivery import cli
    from quantstudio.delivery.constants import Exit
    from tests.fixtures.delivery import Env

    server = Env(tmp_path / "server", monkeypatch)
    bundle = server.make_package()
    prof_dir = str(server.config_root / "profiles" / "mcp_only")

    monkeypatch.setattr("quantstudio._paths.DATA_ROOT", server.root, raising=False)
    monkeypatch.setenv("QUANTSTUDIO_DATA_ROOT", str(server.root))
    monkeypatch.setenv("QS_WRITE_LOCK_DIR", str(tmp_path / "_lock"))

    assert cli.main(["verify", "--import-dir", str(bundle), "--config-dir", prof_dir,
                     "--quiet", "--json"]) == Exit.OK
