"""FP-5：schema 2.1 → 2.2（source_watermark.last_run_id）契约变更测试。

判据映射（②审通过的设计件 §4）：
  A29  8 列→COMPLETE_2_1；9 列→COMPLETE_2_2；结构错配→PARTIAL_OR_MIXED
  A30  安全闸对 2.1/2.2 均放行；PARTIAL_OR_MIXED 仍拒
  A31  迁移幂等：二次执行零变更；行数不变；状态 2.1→2.2
  A32  迁移失败（注入异常）→ 回退后回 COMPLETE_2_1 且列不存在（负向对照）
  A33  2.2 下提交写 last_run_id
  A33-b 2.1 模式下写入**不得**产生 last_run_id（负向对照，防旧路径漏写/误置态）
  A34  全量回归（另跑）
  A35  生产窗口取证（运维执行，不在本文件）

运行：python -m pytest tests/test_qfq_schema_22_last_run_id.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import duckdb
import pytest

from quantstudio.pipeline.qfq_reanchor_schema import (
    SCHEMA_VERSION, SCHEMA_VERSION_COMPAT, init_duckdb_schema)
from quantstudio.pipeline.qfq_schema_status import (
    SchemaStatus, detect_schema_status)
from quantstudio.pipeline.qfq_schema_contracts import (
    TARGET_SOURCE_WATERMARK_2_2_FINGERPRINT, verify_fingerprint)
import scripts.migrate_source_watermark_last_run_id as mig


def _cols(conn):
    return [r[0] for r in conn.execute(
        "select column_name from duckdb_columns() where table_name='source_watermark'").fetchall()]


@pytest.fixture()
def db21(tmp_path):
    """建一个完整 2.1 库（15 张 QFQ 表 + 8 列 source_watermark）。"""
    path = tmp_path / "qs21.db"
    conn = duckdb.connect(str(path))
    init_duckdb_schema(conn)
    # 造一行水位，验证迁移不动数据
    conn.execute(
        "INSERT INTO source_watermark (source, table_name, freq, last_date, last_batch_id, "
        "updated_at, source_generation, cutover_id) VALUES "
        "('mcp','stock_daily','daily',1792512000000,'batch-1',now(),'mcp-gen1','cut-1')")
    conn.close()
    return path


# ---------------- A29：状态判定三例 ----------------
def test_a29_eight_columns_is_complete_2_1(db21):
    conn = duckdb.connect(str(db21))
    try:
        assert len(_cols(conn)) == 8
        assert detect_schema_status(conn) == SchemaStatus.COMPLETE_2_1
    finally:
        conn.close()


def test_a29_nine_columns_is_complete_2_2(db21):
    conn = duckdb.connect(str(db21))
    try:
        conn.execute("ALTER TABLE source_watermark ADD COLUMN last_run_id VARCHAR")
        assert len(_cols(conn)) == 9
        assert detect_schema_status(conn) == SchemaStatus.COMPLETE_2_2
    finally:
        conn.close()


def test_a29_wrong_shape_is_partial_or_mixed(db21):
    conn = duckdb.connect(str(db21))
    try:
        # 错误结构：加一个契约外的列 → 必须判 partial（不得误判任一完整版本）
        conn.execute("ALTER TABLE source_watermark ADD COLUMN bogus_col INTEGER")
        assert detect_schema_status(conn) == SchemaStatus.PARTIAL_OR_MIXED
    finally:
        conn.close()


# ---------------- A30：writers 安全闸放行集 ----------------
def test_a30_writers_gate_allows_both_2_1_and_2_2(db21):
    from quantstudio.pipeline.writers import _assert_qfq_schema_init_safe
    conn = duckdb.connect(str(db21))
    try:
        _assert_qfq_schema_init_safe(conn)          # 2.1 放行
        conn.execute("ALTER TABLE source_watermark ADD COLUMN last_run_id VARCHAR")
        _assert_qfq_schema_init_safe(conn)          # 2.2 放行
    finally:
        conn.close()


def test_a30_writers_gate_still_rejects_partial(db21):
    from quantstudio.pipeline.writers import _assert_qfq_schema_init_safe
    conn = duckdb.connect(str(db21))
    try:
        conn.execute("ALTER TABLE source_watermark ADD COLUMN bogus_col INTEGER")
        with pytest.raises(Exception) as ei:
            _assert_qfq_schema_init_safe(conn)
        assert "partial_or_mixed" in str(ei.value)
    finally:
        conn.close()


# ---------------- A31：迁移成功 + 幂等 ----------------
def test_a31_migration_success_and_idempotent(db21):
    conn = duckdb.connect(str(db21))
    rows_before = conn.execute("select count(*) from source_watermark").fetchone()[0]
    last_date_before = conn.execute("select last_date from source_watermark").fetchone()[0]
    conn.close()

    assert mig.main(["--db", str(db21), "--allow-schema-contract-change", "--execute"]) == 0

    conn = duckdb.connect(str(db21))
    try:
        assert detect_schema_status(conn) == SchemaStatus.COMPLETE_2_2
        assert _cols(conn)[-1] == "last_run_id"
        assert conn.execute("select count(*) from source_watermark").fetchone()[0] == rows_before
        assert conn.execute("select last_date from source_watermark").fetchone()[0] == last_date_before
        # 版本行已同步到 2.2（不再依赖双接受窗口）
        vers = {r[0] for r in conn.execute(
            "select distinct schema_version from qfq_bootstrap_run "
            "where schema_version is not null").fetchall()}
        assert vers <= {SCHEMA_VERSION} or not vers
    finally:
        conn.close()

    # 二次执行：幂等零变更
    assert mig.main(["--db", str(db21), "--allow-schema-contract-change", "--execute"]) == 0


def test_a31_dry_run_makes_no_change(db21):
    assert mig.main(["--db", str(db21), "--dry-run"]) == 0
    conn = duckdb.connect(str(db21))
    try:
        assert detect_schema_status(conn) == SchemaStatus.COMPLETE_2_1
        assert len(_cols(conn)) == 8
    finally:
        conn.close()


# ---------------- 契约闸：未确认 / 代码版本不符 → 拒执行 ----------------
def test_gate_refuses_without_contract_flag(db21):
    assert mig.main(["--db", str(db21)]) == 5


def test_code_version_is_2_2():
    assert SCHEMA_VERSION == "reanchor-2.2"
    assert SCHEMA_VERSION_COMPAT == ("reanchor-2.1", "reanchor-2.2")


# ---------------- A32：迁移失败 → 回退复检回 2.1（负向对照） ----------------
def test_a32_failure_rolls_back_to_complete_2_1(db21, monkeypatch):
    real_connect = duckdb.connect

    class _Boom:
        """阻断**首次**版本行 UPDATE，模拟迁移中途异常；回退时的 UPDATE 放行。"""

        def __init__(self, inner):
            self._inner = inner
            self._fired = False

        def execute(self, sql, *a, **kw):
            if (not self._fired
                    and str(sql).strip().upper().startswith("UPDATE QFQ_BOOTSTRAP_RUN")):
                self._fired = True
                raise RuntimeError("injected failure")
            return self._inner.execute(sql, *a, **kw)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(mig.duckdb, "connect", lambda p, **kw: _Boom(real_connect(p, **kw)))

    rc = mig.main(["--db", str(db21), "--allow-schema-contract-change", "--execute"])
    assert rc == 7, f"回退成功应返回 7（迁移未生效），实际 {rc}"

    monkeypatch.undo()
    conn = duckdb.connect(str(db21))
    try:
        assert "last_run_id" not in _cols(conn), "回退后不得残留 last_run_id 列"
        assert detect_schema_status(conn) == SchemaStatus.COMPLETE_2_1
    finally:
        conn.close()


# ---------------- A33 / A33-b：写入路径双兼容 ----------------
class _OrchStub:
    """_advance_watermark 的最小依赖桩（不构造完整编排器）。"""

    watermark_advancer = None
    cfg = SimpleNamespace(generation_mode="pre_cutover")
    _ident = {"source_generation": "mcp-gen1", "cutover_id": "cut-1"}


def _advance(path, batch_id):
    from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator
    conn = duckdb.connect(str(path))
    try:
        QFQResidentOrchestrator._advance_watermark(
            _OrchStub(), conn, "mcp", "stock_daily", "daily", 1792512000000, batch_id)
    finally:
        conn.close()


def test_a33_write_sets_last_run_id_in_2_2(db21):
    assert mig.main(["--db", str(db21), "--allow-schema-contract-change", "--execute"]) == 0
    _advance(db21, "run-xyz")
    conn = duckdb.connect(str(db21))
    try:
        row = conn.execute(
            "select last_batch_id, last_run_id from source_watermark").fetchone()
        assert row[0] == "run-xyz"      # last_batch_id 语义/格式不变
        assert row[1] == "run-xyz"      # 2.2 下 run 语义落独立列
    finally:
        conn.close()


def test_a33b_write_in_2_1_does_not_produce_last_run_id(db21):
    """A33-b 负向对照：未迁移库（8 列）写入不得产生 last_run_id 列/值。"""
    _advance(db21, "run-abc")
    conn = duckdb.connect(str(db21))
    try:
        assert "last_run_id" not in _cols(conn), "2.1 路径不得创建 last_run_id 列"
        row = conn.execute("select last_batch_id from source_watermark").fetchone()
        assert row[0] == "run-abc"
        assert detect_schema_status(conn) == SchemaStatus.COMPLETE_2_1
    finally:
        conn.close()


def test_2_2_fingerprint_matches_ddl():
    """契约自检：2.2 DDL 与 2.2 指纹列序/类型一致（防再次出现"加列但指纹没跟上"）。"""
    conn = duckdb.connect()
    try:
        from quantstudio.pipeline.qfq_schema_contracts import SOURCE_WATERMARK_2_2_DDL
        conn.execute(SOURCE_WATERMARK_2_2_DDL)
        assert verify_fingerprint(
            conn, {"source_watermark": TARGET_SOURCE_WATERMARK_2_2_FINGERPRINT},
            reject_extra=True)
    finally:
        conn.close()
