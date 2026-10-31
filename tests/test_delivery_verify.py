# -*- coding: utf-8 -*-
"""manifest v1 编解码与版本裁决（T012；FR-006 / FR-011④ / plan §8）。"""
from __future__ import annotations

import copy
import json

import pytest

from quantstudio.delivery import manifest as M
from quantstudio.delivery.constants import (
    FailureKind,
    MANIFEST_SCHEMA_VERSION,
    TABLE_SET_RULE,
)


def _valid_manifest() -> dict:
    files = [
        {
            "path": "qfq_aux_mcp_gen1.db",
            "kind": "sqlite",
            "bytes": 53248,
            "sha256": "a" * 64,
            "sqlite_header": "SQLite format 3",
        },
        {"path": "qfq_aux.db", "kind": "sqlite", "bytes": 1024, "sha256": "b" * 64},
        {"path": "quantstudio.db", "kind": "duckdb", "bytes": 4096, "sha256": "c" * 64},
    ]
    tables = [
        {
            "table": "stock_daily",
            "source": "questdb",
            "freq": "daily",
            "date_basis_col": "trade_date",
            "basis_source": "questdb_table_map.json",
            "row_count": 10,
            "row_count_method": "exact",
            "max_date": "2026-10-30",
            "watermark_last_date": "2026-10-30",
            "state": "OK",
        }
    ]
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": "2026-10-31T03:15:00+08:00",
        "producer": {
            "app": "QuantStudio",
            "version": "0.0.0-test",
            "python": "3.11",
            "duckdb": "1.5.6",
            "sqlite": "3.0",
            "platform": "win32-nt",
        },
        "files": files,
        "main_db": {"path": "quantstudio.db", "schema_status": "complete_2_1"},
        "qfq": {
            "released": False,
            "qfq_active_cutover": None,
            "aux_route_step6_expect": {"legacy": "qfq_aux.db", "gen1": "qfq_aux_mcp_gen1.db"},
        },
        "table_set": {
            "rule": TABLE_SET_RULE,
            "count": 1,
            "empty_count": 0,
            "unknown_basis": [],
            "out_of_map": [],
        },
        "tables": tables,
        "row_count_degraded": False,
        "row_count_degraded_tables": [],
        "self_check": {
            "red_line": {
                "outside_whitelist": 0,
                "env_hits": 0,
                "shm_hits": 0,
                "log_hits": 0,
                "zero_leak": True,
            },
            "db_open": [{"path": "quantstudio.db", "select1": "ok"}],
            "wal_zero": [{"path": "quantstudio.db.wal", "present": False, "bytes": 0}],
        },
        "export": {
            "lock_task_id": "delivery_export",
            "duration_sec": 12.5,
            "checkpoint": {"duckdb": "ok", "sqlite": []},
            "copy_bytes_total": 54280,
            "copy_sha256_pass": True,
        },
    }


# ── 正向 ────────────────────────────────────────────────────────────────────
def test_parse_valid_manifest_ok():
    read = M.parse_manifest(M.canonical_dumps(_valid_manifest()))
    assert read.ok, read.reasons
    assert read.failure_kind is None
    assert read.warnings == []


def test_canonical_dumps_is_stable_and_sorted():
    doc = _valid_manifest()
    first = M.canonical_dumps(doc)
    second = M.canonical_dumps(json.loads(first))
    assert first == second
    keys = list(json.loads(first).keys())
    assert keys == sorted(keys), "规范化输出必须按键排序（SC-009 的可比性基础）"
    assert first.endswith("\n")


def test_unknown_top_field_tolerated_with_warning():
    doc = _valid_manifest()
    doc["future_field"] = {"x": 1}
    read = M.parse_manifest(json.dumps(doc))
    assert read.ok
    assert any("未知字段" in w for w in read.warnings)


# ── 版本裁决（plan §8 四步）─────────────────────────────────────────────────
def test_version_too_new_is_manifest_version_kind():
    doc = _valid_manifest()
    doc["manifest_schema_version"] = 2
    read = M.parse_manifest(json.dumps(doc))
    assert not read.ok
    assert read.failure_kind == FailureKind.MANIFEST_VERSION
    assert "更新程序" in read.reasons[0]
    assert read.doc is None, "版本过高时不得返回已解析的其余字段（禁按旧格式解析）"


def test_version_missing_is_package_kind():
    doc = _valid_manifest()
    doc.pop("manifest_schema_version")
    read = M.parse_manifest(json.dumps(doc))
    assert read.failure_kind == FailureKind.PACKAGE


def test_version_wrong_type_is_package_kind():
    doc = _valid_manifest()
    doc["manifest_schema_version"] = "1"
    read = M.parse_manifest(json.dumps(doc))
    assert read.failure_kind == FailureKind.PACKAGE


def test_version_old_is_program_version_kind():
    doc = _valid_manifest()
    doc["manifest_schema_version"] = 0
    read = M.parse_manifest(json.dumps(doc))
    assert read.failure_kind == FailureKind.PROGRAM_VERSION


def test_broken_json_is_package_kind_without_crash():
    read = M.parse_manifest("{not json")
    assert read.failure_kind == FailureKind.PACKAGE
    assert read.reasons


# ── 路径守卫（FR-006）───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "bad",
    ["/abs/quantstudio.db", "C:/data/quantstudio.db", "data\\quantstudio.db",
     "../quantstudio.db", "//server/share/quantstudio.db", ""],
)
def test_path_guard_rejects_bad_paths(bad):
    assert M.path_error(bad), f"{bad!r} 应被判非法"


@pytest.mark.parametrize("good", ["quantstudio.db", "sub/qfq_aux.db"])
def test_path_guard_accepts_relative(good):
    assert M.path_error(good) is None


def test_validate_rejects_absolute_path_in_files():
    doc = _valid_manifest()
    doc["files"][0]["path"] = "/tmp/qfq_aux_mcp_gen1.db"
    errors = M.validate_v1(doc)
    assert any("path 非法" in e for e in errors)


# ── 字段级校验 ──────────────────────────────────────────────────────────────
def test_validate_rejects_non_whitelist_file():
    doc = _valid_manifest()
    doc["files"].append({"path": "quarantine.db", "kind": "sqlite", "bytes": 1, "sha256": "d" * 64})
    errors = M.validate_v1(doc)
    assert any("不在白名单" in e for e in errors)


def test_validate_rejects_missing_whitelist_file():
    doc = _valid_manifest()
    doc["files"] = [f for f in doc["files"] if f["path"] != "qfq_aux.db"]
    errors = M.validate_v1(doc)
    assert any("缺白名单文件" in e for e in errors)


def test_validate_rejects_unknown_basis_with_max_date():
    doc = _valid_manifest()
    doc["tables"][0].update(
        {"date_basis_col": "unknown", "basis_source": "none", "max_date": "2026-10-30"}
    )
    errors = M.validate_v1(doc)
    assert any("禁止猜" in e for e in errors)


def test_validate_rejects_bad_state():
    doc = _valid_manifest()
    doc["tables"][0]["state"] = "WEIRD"
    assert any("state 非法" in e for e in M.validate_v1(doc))


def test_validate_enforces_degraded_consistency():
    doc = _valid_manifest()
    doc["tables"][0]["row_count_method"] = "estimate"
    errors = M.validate_v1(doc)
    assert any("row_count_degraded_tables" in e for e in errors)
    assert any("row_count_degraded" in e for e in errors)

    doc["row_count_degraded"] = True
    doc["row_count_degraded_tables"] = ["stock_daily"]
    assert M.validate_v1(doc) == []


def test_validate_rejects_table_set_count_mismatch():
    doc = _valid_manifest()
    doc["table_set"]["count"] = 5
    assert any("table_set.count" in e for e in M.validate_v1(doc))


def test_validate_rejects_wrong_table_set_rule():
    doc = _valid_manifest()
    doc["table_set"]["rule"] = "all_tables"
    assert any("table_set.rule" in e for e in M.validate_v1(doc))


def test_build_manifest_raises_on_invalid():
    doc = _valid_manifest()
    with pytest.raises(ValueError):
        M.build_manifest(
            generated_at=doc["generated_at"],
            producer=doc["producer"],
            files=doc["files"][:1],       # 缺白名单文件
            main_db=doc["main_db"],
            qfq=doc["qfq"],
            table_set=doc["table_set"],
            tables=doc["tables"],
            self_check=doc["self_check"],
            export=doc["export"],
        )


def test_build_manifest_sets_degraded_flags():
    doc = _valid_manifest()
    doc["tables"][0]["row_count_method"] = "estimate"
    built = M.build_manifest(
        generated_at=doc["generated_at"],
        producer=doc["producer"],
        files=doc["files"],
        main_db=doc["main_db"],
        qfq=doc["qfq"],
        table_set=doc["table_set"],
        tables=doc["tables"],
        self_check=doc["self_check"],
        export=doc["export"],
    )
    assert built["row_count_degraded"] is True
    assert built["row_count_degraded_tables"] == ["stock_daily"]


def test_bundle_id_ignores_export_time_fields():
    doc = _valid_manifest()
    id1 = M.bundle_id_of(copy.deepcopy(doc))
    doc2 = copy.deepcopy(doc)
    doc2["export"]["lock_acquired_at"] = "2026-10-31T03:00:00+08:00"
    doc2["export"]["lock_released_at"] = "2026-10-31T03:10:00+08:00"
    doc2["generated_at"] = "2026-10-31T09:00:00+08:00"
    assert M.bundle_id_of(doc2) == id1

# ══════════════════════════════════════════════════════════════════════════
# W2：只读校验流程（T031–T035；SC-005 / FR-010 / FR-011 / FR-038 / FR-019）
# ══════════════════════════════════════════════════════════════════════════
from tests.fixtures.delivery import (  # noqa: E402
    Env,
    build_main_db,
    copy_package,
    refresh_manifest_files,
    rewrite_manifest,
    seed_cutover,
    write_config,
)
from quantstudio.delivery.constants import FailureKind  # noqa: E402


@pytest.fixture
def w2(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def _package(w2):
    return w2.make_package()


def test_w2_happy_path_ok(w2):
    bundle = _package(w2)
    report = w2.verify(bundle.parent)
    assert report.ok, report.reasons
    assert report.files_checked == 3
    assert report.exit_code == 0
    assert len(report.tables) == 1 and report.tables[0]["state"] == "OK"
    assert report.route["matched"] is True
    assert report.route["reason"] == "legacy:released=false"
    assert "legacy" in report.route["legacy_security_note"]


def test_w2_verify_leaves_package_untouched(w2):
    bundle = _package(w2)
    before = sorted(p.name for p in bundle.iterdir())
    w2.verify(bundle.parent)
    after = sorted(p.name for p in bundle.iterdir())
    assert before == after, "校验不得在包内留下任何文件（含 SQLite 边车）"


def test_w2_version_too_new_end_to_end(w2):
    bundle = _package(w2)
    rewrite_manifest(bundle, lambda d: d.__setitem__("manifest_schema_version", 2))
    client = w2.make_client()
    fp_before = w2.client_fingerprint(client)
    report = w2.verify(bundle.parent, data_root=client)
    assert not report.ok
    assert report.failure_kind == FailureKind.MANIFEST_VERSION
    assert report.exit_code == 6
    assert "更新程序" in report.message
    assert w2.client_fingerprint(client)["digest"] == fp_before["digest"]


def test_w2_fail_fast_checks_smallest_file_first(w2):
    bundle = _package(w2)
    # 三件按体积升序：qfq_aux(12288) / gen1(12288) / 主库(2.6MB)
    # ⇒ 破坏最大件时，前两件必须已校验完，第三件失败即止（证明"小文件优先"生效）
    with (bundle / "quantstudio.db").open("ab") as fh:
        fh.write(b"corrupted")
    report = w2.verify(bundle.parent)
    assert not report.ok and report.failure_kind == FailureKind.PACKAGE
    assert report.files_checked == 2, "应先校验完两个小文件，再在最大件失败即止"
    assert "quantstudio.db" in report.reasons[0]


def test_w2_fr038_zero_and_two_packages(w2, tmp_path):
    empty = tmp_path / "empty_dir"
    empty.mkdir()
    report = w2.verify(empty)
    assert not report.ok and report.failure_kind == FailureKind.PACKAGE

    first = _package(w2)
    copy_package(first, first.parent / "pkg_copy")
    report = w2.verify(first.parent)
    assert not report.ok and "多个" in report.reasons[0]


def test_w2_estimate_rows_are_labeled(w2):
    bundle = _package(w2)

    def mutate(doc):
        doc["tables"][0]["row_count_method"] = "estimate"
        doc["tables"][0]["row_count"] = 999
        doc["row_count_degraded"] = True
        doc["row_count_degraded_tables"] = [doc["tables"][0]["table"]]

    rewrite_manifest(bundle, mutate)
    report = w2.verify(bundle.parent)
    assert report.ok, report.reasons
    assert any("估算值" in w for w in report.warnings), report.warnings


def test_w2_config_mismatch_when_route_points_outside_data_root(w2, tmp_path):
    bundle = _package(w2)
    seed_cutover(bundle / "quantstudio.db")
    rewrite_manifest(
        bundle,
        lambda d: d["qfq"].__setitem__(
            "qfq_active_cutover",
            {"cutover_id": "c1", "source_generation": "b6_formal_20260807_v2", "price_source": "mcp"},
        ),
    )
    # 包内主库被写入 cutover 行 ⇒ 必须重算 manifest 的 bytes/sha（否则先撞 ①，测不到 ③）
    refresh_manifest_files(bundle)
    write_config(
        w2.config_root,
        released=True,
        generations={"b6_formal_20260807_v2": "../../../elsewhere/qfq_aux_nextgen.db"},
    )
    client = w2.make_client()
    fp_before = w2.client_fingerprint(client)
    report = w2.verify(bundle.parent, data_root=client)
    assert not report.ok
    assert report.failure_kind == FailureKind.CONFIG
    assert report.exit_code == 5
    assert w2.client_fingerprint(client)["digest"] == fp_before["digest"]


@pytest.mark.parametrize("corruption", ["sha_mismatch", "schema_mismatch"])
def test_sc005_zero_mutation_on_bad_packages(w2, corruption):
    bundle = _package(w2)
    if corruption == "sha_mismatch":
        with (bundle / "qfq_aux.db").open("ab") as fh:
            fh.write(b"tampered")
    else:
        bad = build_main_db(w2.tmp / "bad_main.db", schema="partial_or_mixed")
        (bundle / "quantstudio.db").write_bytes(bad.read_bytes())
        refresh_manifest_files(bundle)

    client = w2.make_client()
    before = w2.client_fingerprint(client)
    report = w2.verify(bundle.parent, data_root=client)
    after = w2.client_fingerprint(client)

    expected = (
        FailureKind.PACKAGE if corruption == "sha_mismatch" else FailureKind.PROGRAM_VERSION
    )
    assert not report.ok and report.failure_kind == expected, report.reasons
    assert before["digest"] == after["digest"], "坏包校验必须对 DATA_ROOT 零改动（SC-005）"
    assert before["entries"] == after["entries"]
