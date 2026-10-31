# -*- coding: utf-8 -*-
"""fixture 自检（T010 / T011）：**W0 DoD④ 的落点**——夹具必须造出 `detect_schema_status`
可识别的真实状态，否则后续所有 SC 测试都在"假库"上跑。"""
from __future__ import annotations

import duckdb
import pytest

from tests.fixtures.delivery import (
    build_aux_db,
    build_main_db,
    make_client_data_root,
)


def _detect(path):
    from quantstudio.pipeline.qfq_schema_status import detect_schema_status

    con = duckdb.connect(str(path), read_only=True)
    try:
        return detect_schema_status(con)
    finally:
        con.close()


def test_fixture_complete_2_1_is_detected(tmp_path):
    from quantstudio.pipeline.qfq_schema_status import SchemaStatus

    path = build_main_db(tmp_path / "main.db", schema="complete_2_1")
    assert _detect(path) == SchemaStatus.COMPLETE_2_1


def test_fixture_empty_or_new_is_detected(tmp_path):
    from quantstudio.pipeline.qfq_schema_status import SchemaStatus

    path = build_main_db(tmp_path / "empty.db", schema="empty_or_new")
    assert _detect(path) == SchemaStatus.EMPTY_OR_NEW


def test_fixture_partial_or_mixed_is_detected(tmp_path):
    from quantstudio.pipeline.qfq_schema_status import SchemaStatus

    path = build_main_db(tmp_path / "partial.db", schema="partial_or_mixed")
    assert _detect(path) == SchemaStatus.PARTIAL_OR_MIXED


def test_fixture_zero_bytes_cannot_be_opened(tmp_path):
    path = build_main_db(tmp_path / "broken.db", schema="zero_bytes")
    assert path.stat().st_size == 0
    with pytest.raises(Exception):
        duckdb.connect(str(path), read_only=True)


def test_fixture_aux_db_is_valid_sqlite(tmp_path):
    path = build_aux_db(tmp_path / "qfq_aux.db", rows=4)
    head = path.read_bytes()[:16]
    assert head.startswith(b"SQLite format 3"), "aux fixture 必须是有效 SQLite（非空壳）"
    import sqlite3

    con = sqlite3.connect(str(path))
    try:
        assert con.execute("SELECT count(*) FROM qfq_price_adj").fetchone()[0] == 4
    finally:
        con.close()


@pytest.mark.parametrize("mode", ["install", "update", "repair"])
def test_client_data_root_keeps_existing_assets(tmp_path, mode):
    main = build_main_db(tmp_path / "src_main.db", schema="complete_2_1")
    aux = build_aux_db(tmp_path / "src_aux.db")
    root = make_client_data_root(
        tmp_path / f"client_{mode}", mode=mode, source_main=main, source_aux=aux
    )
    for name in ("snapshots", ".gitkeep", "README.md"):
        assert (root / name).exists(), f"{mode} 模式丢了既有件 {name}（FR-017）"
    if mode == "install":
        assert not (root / "quantstudio.db").exists()
    elif mode == "update":
        assert (root / "quantstudio.db").stat().st_size > 0
    else:
        assert (root / "quantstudio.db").stat().st_size == 0
