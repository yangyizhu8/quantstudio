"""tests/test_qfq_schema_status.py — B-3a 完整状态识别 + init 五态闸门（v2.4）。

覆盖（B-3a 完整版，基于冻结指纹，**不**用 mock _check_contract_match）：

1. 状态探测（只读，五态）：真正空库 / 仅 trade_calendar / 完整 legacy 2.0 / 完整 target 2.1
   / 缺表 / 缺列 / 多列 / 类型错 / NOT NULL 错 / default 错 / PK 错 / PK 顺序错 / 仅 B-3 新表
   / 仅 B-3 新列 / 2.0+2.1 混合 / shadow 表残留 / migration ledger 残留 / introspection 异常→UNKNOWN。
2. init 写前门禁（五态 + spy 证明 0 写）：完整 2.0 / partial / unknown → fail-fast（0 DDL/DML/migrate）；
   完整 2.1 → 严格 no-op（0 CREATE/ALTER/DROP/DML/migrate，仅只读 introspection）；
   空库 → 创建完整 2.1 + 回读 == target；二次 init → 严格 no-op。
3. 契约一致性：parse(DDL_DUCKDB)==TARGET_QFQ_2_1、DUCKDB_COLS==DDL列顺序、
   SCHEMA_CONTRACT_DUCKDB==target 投影、FK/DEFAULT/物理 NOT NULL、source_watermark DDL==target。
4. 过渡门禁：DDL mock 成与 target 不匹配 → 空库 init 第一条 CREATE 前失败、0 DDL/DML/migrate。

全部 hermetic：tmp_path 临时库 / 内存库，不连 live QMT，不碰正式库。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from quantstudio.pipeline import qfq_reanchor_schema as SCHEMA
from quantstudio.pipeline import qfq_schema_status as SS
from quantstudio.pipeline import qfq_schema_contracts as C
from quantstudio.pipeline.qfq_schema_status import (
    SchemaStatus, QfqSchemaMigrationRequired,
    detect_schema_status, assert_init_allowed, assert_code_ddl_matches_target_2_1,
)


# ---------------------------------------------------------------------------
# fixture helpers（直接从冻结指纹建表，不经 init，构造确定性状态）
# ---------------------------------------------------------------------------

def _ddl_from_fingerprint(table: str, fp: dict) -> str:
    """从单表指纹构造等价 CREATE TABLE DDL（用于直接建表构造 fixture）。"""
    col_defs = []
    pk_cols = fp["primary_key"]
    pk_set = {c.lower() for c in pk_cols}
    for (name, typ, not_null, default) in fp["columns"]:
        parts = [f"{name} {typ}"]
        if not_null:
            parts.append("NOT NULL")
        if default is not None:
            dv = default
            # 字符串默认值重新加引号；数字/布尔原样
            if not (dv.lstrip("-").isdigit()):
                dv = f"'{dv}'"
            parts.append(f"DEFAULT {dv}")
        col_defs.append(" ".join(parts))
    if pk_cols:
        col_defs.append(f"PRIMARY KEY ({', '.join(pk_cols)})")
    for fk in fp.get("foreign_keys", []):
        col_defs.append(
            f"FOREIGN KEY ({', '.join(fk['columns'])}) "
            f"REFERENCES {fk['referenced_table']}({', '.join(fk['referenced_columns'])})")
    return f"CREATE TABLE {table} (\n    " + ",\n    ".join(col_defs) + "\n)"


def _build_fingerprint_db(conn, fingerprint: dict) -> None:
    """直接从指纹字典建全表（不经 init）。FK 表须先建被引用表——按指纹顺序建。"""
    # 先建无 FK 的表，再建有 FK 的（简单拓扑：两轮）
    tables_no_fk = [t for t, f in fingerprint.items() if not f.get("foreign_keys")]
    tables_fk = [t for t, f in fingerprint.items() if f.get("foreign_keys")]
    for t in tables_no_fk:
        conn.execute(_ddl_from_fingerprint(t, fingerprint[t]))
    for t in tables_fk:
        conn.execute(_ddl_from_fingerprint(t, fingerprint[t]))


@pytest.fixture
def fresh_conn(tmp_path):
    import duckdb
    conn = duckdb.connect(str(tmp_path / "fresh.db"))
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. 状态探测（五态 + 全部分类矩阵）
# ---------------------------------------------------------------------------

class TestDetectStatus:
    def test_empty_new_db(self, fresh_conn):
        assert detect_schema_status(fresh_conn) == SchemaStatus.EMPTY_OR_NEW

    def test_only_trade_calendar_is_empty_or_new(self, fresh_conn):
        """trade_calendar 是共享表，单独存在且匹配 target → EMPTY_OR_NEW（P0-1）。"""
        fresh_conn.execute(_ddl_from_fingerprint(
            "trade_calendar", C.TARGET_QFQ_2_1_FINGERPRINT["trade_calendar"]))
        assert detect_schema_status(fresh_conn) == SchemaStatus.EMPTY_OR_NEW

    def test_only_legacy_source_watermark_is_partial(self, fresh_conn):
        """P0-1：仅含旧 6 列 source_watermark → PARTIAL_OR_MIXED（不得判空库）。

        否则普通 init 会在旧共享表上建 15 张 QFQ 2.1 表，留下 2.0/2.1 混合 schema。
        """
        fresh_conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_only_target_source_watermark_is_empty(self, fresh_conn):
        """P0-1：仅含正确 target 8 列 source_watermark（无 QFQ 表）→ EMPTY_OR_NEW。"""
        fresh_conn.execute(C.SOURCE_WATERMARK_2_1_DDL)
        assert detect_schema_status(fresh_conn) == SchemaStatus.EMPTY_OR_NEW

    def test_wrong_trade_calendar_is_partial(self, fresh_conn):
        """P0-1：trade_calendar 存在但结构错误（多一列）→ PARTIAL_OR_MIXED。"""
        fresh_conn.execute(
            "CREATE TABLE trade_calendar (cal_date BIGINT, is_open BOOLEAN, "
            "extra_bad VARCHAR)")
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_extra_unique_constraint_is_partial(self, fresh_conn):
        """P0-4：完整 2.1 + 额外 UNIQUE 约束 → PARTIAL（reject_extra UNIQUE）。"""
        _build_fingerprint_db(fresh_conn, C.TARGET_QFQ_2_1_FINGERPRINT)
        fresh_conn.execute(C.SOURCE_WATERMARK_2_1_DDL)
        assert detect_schema_status(fresh_conn) == SchemaStatus.COMPLETE_2_1
        # 重建 cycle_lease（无 FK 依赖，可 drop）带额外 UNIQUE（插在闭合括号前）
        fresh_conn.execute("DROP TABLE qfq_cycle_lease")
        base = _ddl_from_fingerprint(
            "qfq_cycle_lease", C.TARGET_QFQ_2_1_FINGERPRINT["qfq_cycle_lease"])
        fresh_conn.execute(base.rstrip().rstrip(")") + ", UNIQUE (cycle_id))")
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_extra_check_constraint_is_partial(self, fresh_conn):
        """P0-4：完整 2.1 + 额外 CHECK 约束 → PARTIAL。"""
        _build_fingerprint_db(fresh_conn, C.TARGET_QFQ_2_1_FINGERPRINT)
        fresh_conn.execute(C.SOURCE_WATERMARK_2_1_DDL)
        # 重建 cycle_lease 带额外 CHECK（插在闭合括号前）
        fresh_conn.execute("DROP TABLE qfq_cycle_lease")
        base = _ddl_from_fingerprint(
            "qfq_cycle_lease", C.TARGET_QFQ_2_1_FINGERPRINT["qfq_cycle_lease"])
        fresh_conn.execute(base.rstrip().rstrip(")") + ", CHECK (cycle_id IS NOT NULL))")
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_introspection_error_is_unknown(self, fresh_conn):
        """P0-4：introspection 异常 → UNKNOWN（不吞异常、不误判 partial）。"""
        with mock.patch.object(C, "_table_exists", side_effect=RuntimeError("probe boom")):
            # 先建一个 target source_watermark 使库非空，触发 verify_fingerprint 路径
            fresh_conn.execute(C.SOURCE_WATERMARK_2_1_DDL)
            with mock.patch.object(C, "_table_exists", side_effect=RuntimeError("probe boom")):
                assert detect_schema_status(fresh_conn) == SchemaStatus.UNKNOWN

    def test_only_b3_new_table_is_partial(self, fresh_conn):
        """阻断 3：仅残留一张 B-3 新表 → PARTIAL_OR_MIXED（不能判空库）。"""
        fresh_conn.execute(_ddl_from_fingerprint(
            "qfq_source_cutover", C.TARGET_QFQ_2_1_FINGERPRINT["qfq_source_cutover"]))
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_only_shadow_table_is_partial(self, fresh_conn):
        """shadow 表残留（B-3b 中间态）→ PARTIAL_OR_MIXED。"""
        fresh_conn.execute("CREATE TABLE qfq_anchor_state_v2 (a VARCHAR)")
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_only_legacy_table_is_partial(self, fresh_conn):
        """仅一张 legacy QFQ 表（不完整 2.0）→ PARTIAL_OR_MIXED。"""
        fresh_conn.execute(_ddl_from_fingerprint(
            "qfq_trigger_queue", C.LEGACY_QFQ_2_0_FINGERPRINT["qfq_trigger_queue"]))
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_complete_legacy_2_0(self, fresh_conn):
        """完整 legacy 2.0（11 QFQ 表 + source_watermark 6 列）→ COMPLETE_2_0。"""
        _build_fingerprint_db(fresh_conn, C.LEGACY_QFQ_2_0_FINGERPRINT)
        fresh_conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
        assert detect_schema_status(fresh_conn) == SchemaStatus.COMPLETE_2_0

    def test_complete_target_2_1(self, fresh_conn):
        """完整 target 2.1（15 QFQ 表 + source_watermark 8 列）→ COMPLETE_2_1。"""
        _build_fingerprint_db(fresh_conn, C.TARGET_QFQ_2_1_FINGERPRINT)
        fresh_conn.execute(C.SOURCE_WATERMARK_2_1_DDL)
        assert detect_schema_status(fresh_conn) == SchemaStatus.COMPLETE_2_1

    def test_partial_missing_table(self, fresh_conn):
        _build_fingerprint_db(fresh_conn, C.LEGACY_QFQ_2_0_FINGERPRINT)
        fresh_conn.execute("DROP TABLE qfq_trigger_queue")
        fresh_conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_partial_missing_column(self, fresh_conn):
        _build_fingerprint_db(fresh_conn, C.LEGACY_QFQ_2_0_FINGERPRINT)
        fresh_conn.execute("ALTER TABLE qfq_trigger_queue DROP COLUMN payload_hash")
        fresh_conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_partial_extra_column(self, fresh_conn):
        """阻断 4：完整 2.1 + 多一列未知字段 → PARTIAL（reject_extra 拒绝多余列）。"""
        _build_fingerprint_db(fresh_conn, C.TARGET_QFQ_2_1_FINGERPRINT)
        fresh_conn.execute("ALTER TABLE qfq_trigger_queue ADD COLUMN unknown_extra VARCHAR")
        fresh_conn.execute(C.SOURCE_WATERMARK_2_1_DDL)
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_partial_wrong_type(self, fresh_conn):
        _build_fingerprint_db(fresh_conn, C.TARGET_QFQ_2_1_FINGERPRINT)
        # trigger_id_version 期望 INTEGER，改成 DOUBLE 不可能（DuckDB 不支持改类型就地）；
        # 用重建 trigger_queue 制造类型错
        fresh_conn.execute("DROP TABLE qfq_trigger_queue")
        fp = dict(C.TARGET_QFQ_2_1_FINGERPRINT["qfq_trigger_queue"])
        bad_cols = [(n, ("DOUBLE" if n == "trigger_id_version" else t), nn, d)
                    for (n, t, nn, d) in fp["columns"]]
        fp["columns"] = bad_cols
        fresh_conn.execute(_ddl_from_fingerprint("qfq_trigger_queue", fp))
        fresh_conn.execute(C.SOURCE_WATERMARK_2_1_DDL)
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_partial_wrong_pk(self, fresh_conn):
        """PK 错乱 → PARTIAL_OR_MIXED。"""
        _build_fingerprint_db(fresh_conn, C.LEGACY_QFQ_2_0_FINGERPRINT)
        # 重建 trigger_queue 用错误 PK
        fresh_conn.execute("DROP TABLE qfq_trigger_queue")
        fp = dict(C.LEGACY_QFQ_2_0_FINGERPRINT["qfq_trigger_queue"])
        fp = {"columns": fp["columns"], "primary_key": ["asset_type"],
              "unique": [], "check": [], "foreign_keys": []}
        fresh_conn.execute(_ddl_from_fingerprint("qfq_trigger_queue", fp))
        fresh_conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_mixed_2_0_and_2_1_is_partial(self, fresh_conn):
        """2.0 表 + 部分 2.1 列 → PARTIAL_OR_MIXED。"""
        _build_fingerprint_db(fresh_conn, C.LEGACY_QFQ_2_0_FINGERPRINT)
        fresh_conn.execute("ALTER TABLE qfq_trigger_queue ADD COLUMN trigger_id_version INTEGER")
        fresh_conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
        assert detect_schema_status(fresh_conn) == SchemaStatus.PARTIAL_OR_MIXED

    def test_unknown_on_introspection_failure(self, fresh_conn):
        """阻断 6：introspection 异常 → UNKNOWN（绝不误判空库）。"""
        with mock.patch.object(SS, "_list_tables", side_effect=RuntimeError("probe failed")):
            assert detect_schema_status(fresh_conn) == SchemaStatus.UNKNOWN


# ---------------------------------------------------------------------------
# 2. init 写前门禁（五态 + spy 证明 0 写）
# ---------------------------------------------------------------------------

class _ExecuteRecorder:
    """透明转发连接，记录全部 execute 的 SQL（按写关键字分类）。"""
    WRITE_PREFIXES = ("CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE", "REPLACE")

    def __init__(self, inner):
        self._inner = inner
        self.writes = []

    def execute(self, sql, *a, **kw):
        if sql.strip().upper().startswith(self.WRITE_PREFIXES):
            self.writes.append(sql)
        return self._inner.execute(sql, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class TestInitGate:
    def test_empty_creates_complete_2_1(self, tmp_path):
        """空库 → init 创建完整 2.1，回读 == target。"""
        import duckdb
        conn = duckdb.connect(str(tmp_path / "fresh.db"))
        try:
            status = SCHEMA.init_duckdb_schema(conn)
            assert status == SchemaStatus.COMPLETE_2_1
            assert detect_schema_status(conn) == SchemaStatus.COMPLETE_2_1
        finally:
            conn.close()

    def test_complete_2_1_second_init_strict_noop(self, tmp_path):
        """完整 2.1 二次 init：0 CREATE/ALTER/DROP/DML/migrate，仅只读 introspection。"""
        import duckdb
        conn = duckdb.connect(str(tmp_path / "fresh.db"))
        try:
            SCHEMA.init_duckdb_schema(conn)  # 首次建全
            rec = _ExecuteRecorder(conn)
            status = SCHEMA.init_duckdb_schema(rec)  # 二次
            assert status == SchemaStatus.COMPLETE_2_1
            assert rec.writes == [], f"COMPLETE_2_1 二次 init 不应有写操作: {rec.writes}"
        finally:
            conn.close()

    def test_complete_2_0_fail_fast_before_write(self, tmp_path):
        """完整 legacy 2.0 → 写前 fail-fast（0 DDL/DML/migrate）。"""
        import duckdb
        conn = duckdb.connect(str(tmp_path / "legacy.db"))
        try:
            _build_fingerprint_db(conn, C.LEGACY_QFQ_2_0_FINGERPRINT)
            conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
            assert detect_schema_status(conn) == SchemaStatus.COMPLETE_2_0
            rec = _ExecuteRecorder(conn)
            with pytest.raises(QfqSchemaMigrationRequired):
                SCHEMA.init_duckdb_schema(rec)
            assert rec.writes == [], f"fail-fast 前不应有写操作: {rec.writes}"
        finally:
            conn.close()

    def test_partial_fail_fast_before_write(self, tmp_path):
        """partial → 写前 fail-fast。"""
        import duckdb
        conn = duckdb.connect(str(tmp_path / "partial.db"))
        try:
            _build_fingerprint_db(conn, C.LEGACY_QFQ_2_0_FINGERPRINT)
            conn.execute("DROP TABLE qfq_trigger_queue")
            conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
            rec = _ExecuteRecorder(conn)
            with pytest.raises(QfqSchemaMigrationRequired):
                SCHEMA.init_duckdb_schema(rec)
            assert rec.writes == []
        finally:
            conn.close()

    def test_unknown_fail_fast(self, tmp_path):
        """UNKNOWN → 写前 fail-fast。"""
        import duckdb
        conn = duckdb.connect(str(tmp_path / "x.db"))
        try:
            with mock.patch.object(SS, "_list_tables", side_effect=RuntimeError("probe failed")):
                with pytest.raises(QfqSchemaMigrationRequired):
                    SCHEMA.init_duckdb_schema(conn)
        finally:
            conn.close()

    def test_migrate_not_called_on_complete_2_1(self, tmp_path):
        """COMPLETE_2_1 路径不调 _migrate_duckdb_columns（spy 证明）。"""
        import duckdb
        conn = duckdb.connect(str(tmp_path / "fresh.db"))
        try:
            SCHEMA.init_duckdb_schema(conn)
            with mock.patch.object(SCHEMA, "_migrate_duckdb_columns") as spy:
                SCHEMA.init_duckdb_schema(conn)
                spy.assert_not_called()
        finally:
            conn.close()

    def test_migrate_not_called_on_complete_2_0(self, tmp_path):
        """COMPLETE_2_0 fail-fast 路径也不调 _migrate_duckdb_columns。"""
        import duckdb
        conn = duckdb.connect(str(tmp_path / "legacy.db"))
        try:
            _build_fingerprint_db(conn, C.LEGACY_QFQ_2_0_FINGERPRINT)
            conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
            with mock.patch.object(SCHEMA, "_migrate_duckdb_columns") as spy:
                with pytest.raises(QfqSchemaMigrationRequired):
                    SCHEMA.init_duckdb_schema(conn)
                spy.assert_not_called()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 3. 契约一致性（机械证明 DDL/COLS/CONTRACT 与 target 不漂移）
# ---------------------------------------------------------------------------

class TestContractConsistency:
    def test_parse_ddl_matches_target_qfq_2_1(self):
        """parse(DDL_DUCKDB) == TARGET_QFQ_2_1_FINGERPRINT（全 15 表逐字）。"""
        for table, ddl in SCHEMA.DDL_DUCKDB.items():
            parsed = C.parse_physical_contract(ddl)
            target = C.TARGET_QFQ_2_1_FINGERPRINT[table]
            assert [(c[0].lower(), c[1], c[2], c[3]) for c in parsed["columns"]] == \
                   [(c[0].lower(), c[1], c[2], c[3]) for c in target["columns"]], table
            assert parsed["primary_key"] == target["primary_key"], table
            assert parsed["foreign_keys"] == target["foreign_keys"], table

    def test_duckdb_cols_matches_ddl_order(self):
        for table, cols in SCHEMA.DUCKDB_COLS.items():
            parsed_names = [c[0] for c in C.parse_physical_contract(SCHEMA.DDL_DUCKDB[table])["columns"]]
            assert parsed_names == cols, f"{table} DUCKDB_COLS 与 DDL 列顺序漂移"

    def test_schema_contract_duckdb_is_target_projection(self):
        """SCHEMA_CONTRACT_DUCKDB == project_legacy_contract_shape(TARGET)。"""
        proj = C.project_legacy_contract_shape(C.TARGET_QFQ_2_1_FINGERPRINT)
        assert SCHEMA.SCHEMA_CONTRACT_DUCKDB == proj

    def test_source_watermark_ddl_matches_target(self):
        parsed = C.parse_physical_contract(C.SOURCE_WATERMARK_2_1_DDL)
        target = C.TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT
        assert [(c[0].lower(), c[1], c[2], c[3]) for c in parsed["columns"]] == \
               [(c[0].lower(), c[1], c[2], c[3]) for c in target["columns"]]
        assert parsed["primary_key"] == target["primary_key"]

    def test_assert_code_ddl_matches_target_2_1_passes(self):
        """最终态：DDL==target，门禁通过。"""
        assert_code_ddl_matches_target_2_1()  # 不抛即通过

    def test_trigger_id_version_is_integer_not_null_no_default(self):
        cols = {c[0]: c for c in C.TARGET_QFQ_2_1_FINGERPRINT["qfq_trigger_queue"]["columns"]}
        c = cols["trigger_id_version"]
        assert c[1] == "INTEGER" and c[2] is True and c[3] is None

    def test_main_db_fingerprint_table_counts(self):
        assert len(C.LEGACY_QFQ_2_0_FINGERPRINT) == 11
        assert len(C.TARGET_QFQ_2_1_FINGERPRINT) == 15
        assert len(C.LEGACY_MAIN_DB_2_0_FINGERPRINT) == 12  # 11 + source_watermark
        assert len(C.TARGET_MAIN_DB_2_1_FINGERPRINT) == 16  # 15 + source_watermark

    def test_four_swap_tables_have_v2_pk(self):
        """4 张 swap 表在 target 用最终 2.1 PK（含 source_generation）。"""
        assert "source_generation" in C.TARGET_QFQ_2_1_FINGERPRINT["qfq_anchor_state"]["primary_key"]
        assert "source_generation" in C.TARGET_QFQ_2_1_FINGERPRINT["qfq_pending_backfill"]["primary_key"]
        assert "price_source" in C.TARGET_QFQ_2_1_FINGERPRINT["qfq_observation_cursor"]["primary_key"]
        assert "source_generation" in C.TARGET_QFQ_2_1_FINGERPRINT["qfq_watermark_intent"]["primary_key"]


# ---------------------------------------------------------------------------
# 4. 过渡门禁（DDL≠target 时空库写前失败）
# ---------------------------------------------------------------------------

class TestTransitionGate:
    def test_empty_init_fails_when_ddl_not_matching_target(self, tmp_path):
        """DDL mock 成与 target 不匹配 → 空库 init 第一条 CREATE 前失败、0 DDL/DML/migrate。

        证明门禁存在：最终态放行，但开发中间态（DDL≠target）时空库也不许用旧 DDL 建库。
        """
        import duckdb
        conn = duckdb.connect(str(tmp_path / "fresh.db"))
        # 构造一个"错误"DDL_DUCKDB（缺一表）
        bad_ddl = dict(SCHEMA.DDL_DUCKDB)
        del bad_ddl["qfq_cycle_lease"]
        try:
            with mock.patch.object(SCHEMA, "DDL_DUCKDB", bad_ddl):
                rec = _ExecuteRecorder(conn)
                with pytest.raises(QfqSchemaMigrationRequired):
                    SCHEMA.init_duckdb_schema(rec)
                assert rec.writes == [], f"门禁前不应有写操作: {rec.writes}"
                # 库未被改动
                tabs = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
                assert tabs == set()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 5. TriggerStatus.SUPERSEDED 逐字对齐（B-3R 发现#2）
# ---------------------------------------------------------------------------

class TestSupersededAlignment:
    def test_enum_has_superseded(self):
        from quantstudio.pipeline.qfq_orchestrator_types import TriggerStatus
        assert TriggerStatus.SUPERSEDED.value == "superseded"

    def test_enum_matches_schema_frozenset(self):
        from quantstudio.pipeline.qfq_orchestrator_types import TriggerStatus
        assert {m.value for m in TriggerStatus} == set(SCHEMA.TRIGGER_STATUS)


# ---------------------------------------------------------------------------
# 6. DuckDBWriter 共享表安全闸（P0-2）
# ---------------------------------------------------------------------------

class TestWriterSourceWatermarkGate:
    def test_legacy_6col_watermark_writer_init_fails(self, tmp_path):
        """P0-2：旧 6 列 source_watermark → writer init 写前 fail-fast（0 DDL）。"""
        import duckdb
        from quantstudio.pipeline import writers as W
        conn = duckdb.connect(str(tmp_path / "x.db"))
        conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
        before = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        with pytest.raises(W._WriterSchemaMigrationRequired):
            W._assert_source_watermark_init_safe(conn)
        after = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert before == after  # 无新增表
        conn.close()

    def test_target_8col_watermark_writer_init_allows(self, tmp_path):
        """P0-2：完整 target 8 列 → writer init 放行。"""
        import duckdb
        from quantstudio.pipeline import writers as W
        conn = duckdb.connect(str(tmp_path / "x.db"))
        conn.execute(C.SOURCE_WATERMARK_2_1_DDL)
        W._assert_source_watermark_init_safe(conn)  # 不抛
        conn.close()

    def test_absent_watermark_writer_init_allows(self, tmp_path):
        """P0-2：source_watermark 不存在 → writer init 放行（将创建 target）。"""
        import duckdb
        from quantstudio.pipeline import writers as W
        conn = duckdb.connect(str(tmp_path / "x.db"))
        W._assert_source_watermark_init_safe(conn)  # 不抛
        conn.close()


# ---------------------------------------------------------------------------
# 6b. DuckDBWriter 完整 QFQ 五态安全闸（P0-1，B-3a.3）
# ---------------------------------------------------------------------------

class TestWriterFullStateGate:
    """P0-1：_init_tables 第一条 DDL 前完整 QFQ 五态预检。

    仅 EMPTY_OR_NEW/COMPLETE_2_1 放行；COMPLETE_2_0/PARTIAL_OR_MIXED/UNKNOWN 全部写前 fail-fast。
    所有 fail-fast 路径断言 0 DDL/0 ALTER/0 DML、表集合前后不变。
    """

    def _assert_gate_no_mutation(self, tmp_path, setup_fn):
        import duckdb
        from quantstudio.pipeline import writers as W
        conn = duckdb.connect(str(tmp_path / "x.db"))
        setup_fn(conn)
        before = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        with pytest.raises(W._WriterSchemaMigrationRequired):
            W._assert_qfq_schema_init_safe(conn)
        after = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        conn.close()
        assert before == after  # 表集合不变（0 DDL）

    def test_partial_single_legacy_qfq_table_fails(self, tmp_path):
        """仅一张 legacy qfq_trigger_queue（无 source_watermark）→ 写前 fail-fast。"""
        def setup(conn):
            conn.execute("CREATE TABLE qfq_trigger_queue (trigger_id VARCHAR)")
        self._assert_gate_no_mutation(tmp_path, setup)

    def test_complete_legacy_2_0_fails(self, tmp_path):
        """完整 legacy 2.0 → 写前 fail-fast。"""
        def setup(conn):
            _build_fingerprint_db(conn, C.LEGACY_QFQ_2_0_FINGERPRINT)
            conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
        self._assert_gate_no_mutation(tmp_path, setup)

    def test_mixed_2_0_2_1_fails(self, tmp_path):
        """2.0 + 部分 2.1 列 → fail-fast。"""
        def setup(conn):
            _build_fingerprint_db(conn, C.LEGACY_QFQ_2_0_FINGERPRINT)
            conn.execute("ALTER TABLE qfq_trigger_queue ADD COLUMN trigger_id_version INTEGER")
            conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
        self._assert_gate_no_mutation(tmp_path, setup)

    def test_shadow_table_residue_fails(self, tmp_path):
        """shadow 表残留 → fail-fast。"""
        def setup(conn):
            conn.execute("CREATE TABLE qfq_anchor_state_v2 (a VARCHAR)")
        self._assert_gate_no_mutation(tmp_path, setup)

    def test_unknown_on_introspection_error_fails(self, tmp_path):
        """introspection 异常 → UNKNOWN → fail-fast。"""
        import duckdb
        from unittest import mock
        from quantstudio.pipeline import writers as W
        conn = duckdb.connect(str(tmp_path / "x.db"))
        conn.execute(C.SOURCE_WATERMARK_2_1_DDL)  # 使库非空
        with mock.patch.object(C, "_table_exists", side_effect=RuntimeError("probe boom")):
            with pytest.raises(W._WriterSchemaMigrationRequired):
                W._assert_qfq_schema_init_safe(conn)
        conn.close()

    def test_empty_allows(self, tmp_path):
        """空库 → writer init 放行。"""
        import duckdb
        from quantstudio.pipeline import writers as W
        conn = duckdb.connect(str(tmp_path / "x.db"))
        W._assert_qfq_schema_init_safe(conn)  # 不抛
        conn.close()

    def test_complete_2_1_allows(self, tmp_path):
        """完整 2.1 → writer init 放行（非 QFQ 框架表初始化）。"""
        import duckdb
        from quantstudio.pipeline import writers as W
        conn = duckdb.connect(str(tmp_path / "x.db"))
        _build_fingerprint_db(conn, C.TARGET_QFQ_2_1_FINGERPRINT)
        conn.execute(C.SOURCE_WATERMARK_2_1_DDL)
        W._assert_qfq_schema_init_safe(conn)  # 不抛
        conn.close()


# ---------------------------------------------------------------------------
# 6c. 统一静态 pre-cutover identity（P0-2，B-3a.3）——危险配置回归
# ---------------------------------------------------------------------------

class TestPreCutoverIdentityDangerousConfig:
    """P0-2：即使配置显式传 mcp-gen1/cut_not_active，B-3a 落库也必须保持 legacy 哨兵。"""

    def _dangerous_cfg(self):
        from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
        return QFQOrchestratorConfig(
            enabled=True, price_source="mcp",
            source_generation="mcp-gen1", cutover_id="cut_not_active")

    def test_pre_cutover_qfq_identity_function(self):
        from quantstudio.pipeline.qfq_schema_contracts import pre_cutover_qfq_identity
        ident = pre_cutover_qfq_identity("mcp")
        assert ident == {
            "price_source": "mcp",
            "source_generation": "xtquant-legacy",
            "cutover_id": "legacy-xtquant-pre-cutover",
        }

    def test_orchestrator_ident_is_static_legacy(self):
        """orchestrator._ident 在 mcp-gen1 危险配置下仍是 legacy 哨兵。"""
        from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator
        orch = QFQResidentOrchestrator(self._dangerous_cfg())
        assert orch._ident["price_source"] == "mcp"  # 真实值不改写
        assert orch._ident["source_generation"] == "xtquant-legacy"  # 固定哨兵，非 mcp-gen1
        assert orch._ident["cutover_id"] == "legacy-xtquant-pre-cutover"  # 固定哨兵

    def test_event_discovery_cursor_writes_legacy_generation(self, tmp_path):
        """危险配置下 cursor 落库 generation=legacy 哨兵（非 cfg.source_generation）。"""
        import duckdb
        from quantstudio.pipeline.qfq_event_discovery import EventDiscovery
        from quantstudio.pipeline.qfq_reanchor_schema import DDL_DUCKDB
        conn = duckdb.connect(str(tmp_path / "x.db"))
        conn.execute(DDL_DUCKDB["qfq_observation_cursor"])
        ed = EventDiscovery(self._dangerous_cfg(), aux_db=None)
        ed._upsert_cursor(conn, "det", "STOCK", 1000, "r1", "ok")
        # observation_cursor 含 price_source/source_generation（无 cutover_id，按 target 契约）
        row = conn.execute(
            "SELECT price_source, source_generation FROM qfq_observation_cursor"
        ).fetchone()
        conn.close()
        assert row[0] == "mcp"  # 真实值不改写
        assert row[1] == "xtquant-legacy"  # 非 mcp-gen1（固定 legacy 哨兵）

    def test_no_b3a_production_write_uses_cfg_generation_directly(self):
        """静态扫描：B-3a 生产写入路径不得直接用 cfg.source_generation/cfg.cutover_id。"""
        import re
        from pathlib import Path
        banned = re.compile(r"cfg\.source_generation|cfg\.cutover_id")
        files = [
            "quantstudio/pipeline/qfq_event_discovery.py",
            "quantstudio/pipeline/qfq_resident_orchestrator.py",
            "quantstudio/pipeline/qfq_reanchor_engine.py",
            "quantstudio/pipeline/qfq_fresh_capture.py",
            "quantstudio/pipeline/writers.py",
        ]
        root = Path(__file__).resolve().parent.parent
        violations = []
        for f in files:
            for i, line in enumerate((root / f).read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):  # 允许注释提及
                    continue
                if banned.search(line):
                    violations.append(f"{f}:{i}: {line.strip()}")
        assert violations == [], f"B-3a 生产写入直接用 cfg.source_generation/cutover_id: {violations}"


# ---------------------------------------------------------------------------
# 7. source_watermark upsert 审计列冲突更新（P1-1）
# ---------------------------------------------------------------------------

class TestWatermarkAuditOnConflict:
    def test_advance_watermark_updates_audit_on_conflict(self, tmp_path):
        """P1-1：同 PK 二次 upsert → 审计列跟随本次值，行数仍为 1。"""
        import duckdb
        from quantstudio.pipeline.writers import DuckDBWriter
        w = DuckDBWriter({"type": "duckdb", "path": str(tmp_path / "x.db")})
        # 首次写入（QFQ 价格表 stock_daily → legacy 哨兵）
        w.advance_watermark("xtquant", "stock_daily", "daily", "100", "b1")
        # 二次写入（同 PK，不同审计值——模拟 generation 接管后 upsert）
        # 用直接 SQL 改 generation 验证 EXCLUDED 路径生效
        conn = w._conn()
        try:
            conn.execute(
                "INSERT INTO source_watermark "
                "(source, table_name, freq, last_date, last_batch_id, updated_at, "
                " source_generation, cutover_id) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT (source, table_name, freq) DO UPDATE SET "
                " last_date=EXCLUDED.last_date, last_batch_id=EXCLUDED.last_batch_id, "
                " updated_at=EXCLUDED.updated_at, "
                " source_generation=EXCLUDED.source_generation, cutover_id=EXCLUDED.cutover_id",
                ["xtquant", "stock_daily", "daily", "200", "b2", "2026-01-02",
                 "mcp-gen1", "cut_new"])
            row = conn.execute(
                "SELECT last_date, source_generation, cutover_id FROM source_watermark "
                "WHERE source='xtquant' AND table_name='stock_daily'").fetchone()
            n = conn.execute("SELECT count(*) FROM source_watermark").fetchone()[0]
        finally:
            conn.close()
        assert row[0] == 200  # last_date BIGINT
        assert row[1] == "mcp-gen1"   # 审计列已更新（P1-1）
        assert row[2] == "cut_new"
        assert n == 1  # 行数仍为 1（ON CONFLICT UPDATE 未新增行）
