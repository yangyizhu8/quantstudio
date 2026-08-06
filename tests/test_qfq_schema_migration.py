"""tests/test_qfq_schema_migration.py - B-3b explicit 2.0-to-2.1 migration tests (B-3b.3 convergence).

覆盖（B-3b 工作包 §十二 + B-3b.1 补修要求）：
- dry-run 0 写 + hashes_before 全有效（64 位 SHA-256，不为空）；
- synthetic 完整 2.0→2.1；
- **逐表历史回填**（每张重建表插入代表性历史行，迁移后逐字段断言公共列保留/新列回填/原值不覆盖/新PK/旧trigger ID不重算）；
- 10 张重建表 + 4 张新表 + 4 张 PK 改变表；
- source_watermark 分类回填（QFQ 价格表 vs 非 QFQ）；
- 第二次运行幂等（0 DDL/0 DML/hash 不变）；
- partial/unknown/empty 拒绝（**UNKNOWN 用 introspection 异常 fail-closed**）；
- 正式库路径硬拒绝（绝对/相对/../大小写/**symlink/junction/hardlink/samefile 别名**/--allow-production 不绕过）+ 不变证明；
- allowed-root（外路径拒绝/边界）；
- 13 个故障点（前 12 失败后 = COMPLETE_2_0；第 13 = COMPLETE_2_1 already_current）；
- **真正 os._exit 子进程崩溃恢复**（非受控异常回滚）；
- **CLI 子进程测试**（dry-run/--apply/--allow-production 拒绝/非法 allowed-root/report JSON 字段完整/exit code）；
- **内容 hash 黄金常量**（NULL/分隔符/timestamp/float NaN/inf/-0.0 编码 + content_hash_version 在报告与 CLI JSON）；
- 最终 target fingerprint + 无 shadow/legacy 残留 + hashes_before/after 全有效。

全部 hermetic：tmp_path/内存库，不碰正式库。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest import mock

import duckdb
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from quantstudio.pipeline import qfq_schema_migration as M
from quantstudio.pipeline import qfq_schema_contracts as C
from quantstudio.pipeline import qfq_schema_status as SS
from quantstudio.pipeline.qfq_schema_migration import (
    QfqMigrationError, QfqMigrationProductionRefused, MigrationReport,
    FAILURE_POINTS, CONTENT_HASH_VERSION, _encode_row, _encode_value,
    migrate_reanchor_2_0_to_2_1,
)
from tests.test_qfq_schema_status import _build_fingerprint_db


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _seed_full_legacy(conn):
    """构造完整 legacy 2.0 库 + **每张重建表的代表性历史行**（逐表回填测试用）。"""
    _build_fingerprint_db(conn, C.LEGACY_QFQ_2_0_FINGERPRINT)
    conn.execute(C.SOURCE_WATERMARK_2_0_DDL)
    # qfq_trigger_queue：2 行（不同 status）
    conn.execute(
        "INSERT INTO qfq_trigger_queue (trigger_id, asset_type, code, trigger_type, "
        "detection_source, effective_date, payload_hash, status, created_at, updated_at) VALUES "
        "('tid_a','STOCK','600000','stock_dividend','det',100,'ph_a','pending','2026-01-01','2026-01-01'),"
        "('tid_b','ETF','510300','factor_new','det2',200,'ph_b','committed','2026-01-02','2026-01-02')")
    # qfq_reanchor_event：1 行（有 price_source，迁移后应保留）
    conn.execute(
        "INSERT INTO qfq_reanchor_event (event_id, event_type, asset_type, code, price_source, "
        "status, created_at, first_seen_at, last_seen_at) VALUES "
        "('eid1','reanchor','STOCK','600000','xtquant','committed','2026-01-01','2026-01-01','2026-01-01')")
    # qfq_pending_backfill：1 行
    conn.execute(
        "INSERT INTO qfq_pending_backfill (asset_type, code, table_name, freq, range_start, "
        "range_end, reason, status, created_at, updated_at) VALUES "
        "('STOCK','600000','stock_daily','daily',100,200,'blocked_revision','pending','2026-01-01','2026-01-01')")
    # qfq_cycle_run：1 行
    conn.execute(
        "INSERT INTO qfq_cycle_run (cycle_id, phase, status, started_at, updated_at) VALUES "
        "('cyc1','finalized','finalized','2026-01-01','2026-01-01')")
    # qfq_bootstrap_run：1 行
    conn.execute(
        "INSERT INTO qfq_bootstrap_run (bootstrap_run_id, status, schema_version) VALUES "
        "('run1','completed','reanchor-2.0')")
    # qfq_watermark_intent：1 行（有 source，迁移后保留）
    conn.execute(
        "INSERT INTO qfq_watermark_intent (cycle_id, source, table_name, freq, status) VALUES "
        "('cyc1','xtquant','stock_daily','daily','committed')")
    # qfq_fresh_capture：1 行（有 source，迁移后保留）
    conn.execute(
        "INSERT INTO qfq_fresh_capture (capture_id, asset_type, code, source, status, "
        "created_at, updated_at) VALUES "
        "('cap1','STOCK','600000','xtquant','applied','2026-01-01','2026-01-01')")
    # qfq_observation_cursor：1 行
    conn.execute(
        "INSERT INTO qfq_observation_cursor (detector_name, asset_type, updated_at) VALUES "
        "('det1','STOCK','2026-01-01')")
    # qfq_anchor_state：1 行（有 price_source，迁移后保留）
    conn.execute(
        "INSERT INTO qfq_anchor_state (asset_type, code, price_source, status, updated_at) VALUES "
        "('STOCK','600000','xtquant','ok','2026-01-01')")
    # 保留表
    conn.execute(
        "INSERT INTO qfq_bootstrap_item (bootstrap_run_id, asset_type, code, status, "
        "updated_at) VALUES ('run1','STOCK','600000','completed','2026-01-01')")
    conn.execute("INSERT INTO trade_calendar (cal_date, is_open) VALUES (20260101, 0)")
    # source_watermark：QFQ 价格表 + 非 QFQ 表
    conn.execute(
        "INSERT INTO source_watermark (source, table_name, freq, last_date, last_batch_id, "
        "updated_at) VALUES "
        "('xtquant','stock_daily','daily',100,'b1','2026-01-01'),"
        "('tushare','etf_basic','daily',200,'b2','2026-01-01'),"
        "('mcp','stock_minutes','1min',300,'b3','2026-01-01')")


@pytest.fixture
def legacy_db(tmp_path):
    db = tmp_path / "legacy.db"
    conn = duckdb.connect(str(db))
    _seed_full_legacy(conn)
    conn.close()
    return db


def _fhash(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1024 * 1024), b""):
            h.update(c)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 1. dry-run + hashes_before 全有效
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_zero_writes(self, legacy_db, tmp_path):
        h_before = _fhash(legacy_db)
        r = migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=False)
        assert r.dry_run is True and r.applied is False
        assert h_before == _fhash(legacy_db)  # 0 写

    def test_hashes_before_all_valid_64char(self, legacy_db, tmp_path):
        """P0-1：hashes_before 全为 64 位 SHA-256，不得为空。"""
        r = migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=False)
        empty = [t for t, h in r.hashes_before.items() if not h]
        assert empty == [], f"hashes_before 有空值: {empty}"
        for t, h in r.hashes_before.items():
            assert len(h) == 64, f"{t} hash 非 64 位: {h}"

    def test_content_hash_version_in_report(self, legacy_db, tmp_path):
        r = migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=False)
        assert r.content_hash_version == CONTENT_HASH_VERSION == "b3b-sha256-v1"


# ---------------------------------------------------------------------------
# 2. synthetic 2.0→2.1 + 残留/新表/保留表
# ---------------------------------------------------------------------------

class TestSyntheticMigrate:
    def test_apply_complete_2_0_to_2_1(self, legacy_db, tmp_path):
        r = migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        assert r.applied and r.target_status == "complete_2_1"

    def test_no_shadow_legacy_residue(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        residue = [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE '%__b3b%'"
        ).fetchall()]
        conn.close()
        assert residue == []

    def test_all_4_new_tables_empty(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        for t in M.NEW_TABLES:
            assert conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] == 0
        conn.close()

    def test_keep_tables_data_preserved(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        assert conn.execute("SELECT COUNT(*) FROM qfq_bootstrap_item").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM trade_calendar").fetchone()[0] == 1
        conn.close()


# ---------------------------------------------------------------------------
# 3. 逐表历史回填（P0-3：每张重建表逐字段断言）
# ---------------------------------------------------------------------------

class TestPerTableBackfill:
    def test_trigger_queue_backfill_no_recompute(self, legacy_db, tmp_path):
        """trigger_queue：新列回填 + 旧 trigger ID 不重算 + 公共列保留。"""
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        rows = conn.execute(
            "SELECT trigger_id, payload_hash, status, trigger_id_version, price_source, "
            "source_generation, cutover_id, retired_at, retire_reason FROM qfq_trigger_queue "
            "ORDER BY trigger_id").fetchall()
        conn.close()
        assert rows[0][0] == "tid_a"  # 旧 trigger ID 保留（不重算）
        assert rows[0][1] == "ph_a"   # payload_hash 保留
        assert rows[0][2] == "pending"  # status 保留
        for r in rows:
            assert r[3] == 1  # trigger_id_version
            assert r[4] == "xtquant"
            assert r[5] == "xtquant-legacy"
            assert r[6] == "legacy-xtquant-pre-cutover"
            assert r[7] is None and r[8] is None  # retired_at/retire_reason

    def test_reanchor_event_preserves_price_source(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        row = conn.execute(
            "SELECT event_id, price_source, source_generation, cutover_id FROM qfq_reanchor_event"
        ).fetchone()
        conn.close()
        assert row[0] == "eid1"
        assert row[1] == "xtquant"  # 保留原 price_source
        assert row[2] == "xtquant-legacy"
        assert row[3] == "legacy-xtquant-pre-cutover"

    def test_pending_backfill_new_pk(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        row = conn.execute(
            "SELECT price_source, source_generation, reason, status FROM qfq_pending_backfill"
        ).fetchone()
        conn.close()
        assert row[0] == "xtquant"
        assert row[1] == "xtquant-legacy"
        assert row[2] == "blocked_revision"  # 公共列保留
        assert row[3] == "pending"

    def test_cycle_run_backfill(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        row = conn.execute(
            "SELECT cycle_id, phase, price_source, source_generation, cutover_id FROM qfq_cycle_run"
        ).fetchone()
        conn.close()
        assert row[0] == "cyc1" and row[1] == "finalized"  # 公共列保留
        assert row[2:] == ("xtquant", "xtquant-legacy", "legacy-xtquant-pre-cutover")

    def test_bootstrap_run_backfill(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        row = conn.execute(
            "SELECT bootstrap_run_id, status, schema_version, price_source, source_generation, "
            "cutover_id FROM qfq_bootstrap_run").fetchone()
        conn.close()
        assert row[0] == "run1" and row[1] == "completed"  # 公共列保留
        assert row[2:] == ("reanchor-2.0", "xtquant", "xtquant-legacy", "legacy-xtquant-pre-cutover")

    def test_watermark_intent_preserves_source(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        row = conn.execute(
            "SELECT source, source_generation, cutover_id FROM qfq_watermark_intent").fetchone()
        conn.close()
        assert row[0] == "xtquant"  # 保留原 source
        assert row[1:] == ("xtquant-legacy", "legacy-xtquant-pre-cutover")

    def test_fresh_capture_preserves_source(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        row = conn.execute(
            "SELECT source, source_generation, cutover_id FROM qfq_fresh_capture").fetchone()
        conn.close()
        assert row[0] == "xtquant"  # 保留原 source
        assert row[1:] == ("xtquant-legacy", "legacy-xtquant-pre-cutover")

    def test_observation_cursor_backfill(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        row = conn.execute(
            "SELECT detector_name, price_source, source_generation FROM qfq_observation_cursor"
        ).fetchone()
        conn.close()
        assert row[0] == "det1"  # 公共列保留
        assert row[1:] == ("xtquant", "xtquant-legacy")

    def test_anchor_state_preserves_price_source(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        row = conn.execute(
            "SELECT price_source, source_generation FROM qfq_anchor_state").fetchone()
        conn.close()
        assert row[0] == "xtquant"  # 保留原 price_source
        assert row[1] == "xtquant-legacy"

    def test_source_watermark_classified_backfill(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        rows = {r[1]: r for r in conn.execute(
            "SELECT source, table_name, source_generation, cutover_id FROM source_watermark"
        ).fetchall()}
        conn.close()
        # QFQ 价格表
        assert rows["stock_daily"][2:] == ("xtquant-legacy", "legacy-xtquant-pre-cutover")
        assert rows["stock_minutes"][2:] == ("xtquant-legacy", "legacy-xtquant-pre-cutover")
        # 非 QFQ
        assert rows["etf_basic"][2:] == ("not-qfq-managed", "not-applicable")
        # source 保留
        assert rows["stock_daily"][0] == "xtquant"
        assert rows["stock_minutes"][0] == "mcp"


# ---------------------------------------------------------------------------
# 4. 幂等
# ---------------------------------------------------------------------------

class TestIdempotent:
    def test_second_run_already_current_zero_writes(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        h1 = _fhash(legacy_db)
        r = migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        assert r.already_current and not r.applied
        assert h1 == _fhash(legacy_db)


# ---------------------------------------------------------------------------
# 5. 状态门禁（partial/empty/shadow 残留 + UNKNOWN）
# ---------------------------------------------------------------------------

class TestStateGate:
    def test_empty_refused(self, tmp_path):
        db = tmp_path / "empty.db"; duckdb.connect(str(db)).close()
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path))

    def test_partial_refused(self, tmp_path):
        db = tmp_path / "partial.db"
        c = duckdb.connect(str(db)); c.execute("CREATE TABLE qfq_trigger_queue (x VARCHAR)"); c.close()
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path))

    def test_shadow_residue_refused(self, tmp_path):
        db = tmp_path / "residue.db"
        c = duckdb.connect(str(db))
        _build_fingerprint_db(c, C.LEGACY_QFQ_2_0_FINGERPRINT); c.execute(C.SOURCE_WATERMARK_2_0_DDL)
        c.execute("CREATE TABLE qfq_anchor_state__b3b_v2 (a VARCHAR)"); c.close()
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path))

    def test_unknown_fail_closed(self, tmp_path):
        """P0-3：introspection 异常 → UNKNOWN → migration fail-closed（0 写）。"""
        db = tmp_path / "unk.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        h_before = _fhash(db)
        with mock.patch.object(C, "_table_exists", side_effect=RuntimeError("probe boom")):
            with pytest.raises(QfqMigrationError):
                migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path), apply=False)
        assert h_before == _fhash(db)  # 0 写


# ---------------------------------------------------------------------------
# 6. 正式库硬拒绝（含 symlink/junction/hardlink/samefile 别名）
# ---------------------------------------------------------------------------

class TestProductionRefuse:
    @pytest.fixture
    def prod_path(self):
        from quantstudio._paths import db_path
        return Path(db_path()).resolve()

    @pytest.mark.parametrize("variant", ["absolute", "relative", "dotdot", "upper"])
    def test_production_refused_variants(self, prod_path, variant):
        if variant == "absolute": v = str(prod_path)
        elif variant == "relative": v = "data/quantstudio.db"
        elif variant == "dotdot": v = "data/../data/quantstudio.db"
        elif variant == "upper": v = str(prod_path).upper()
        with pytest.raises(QfqMigrationProductionRefused):
            migrate_reanchor_2_0_to_2_1(v, allowed_root=str(prod_path.parent))

    def test_same_volume_fake_prod_hardlink_refused(self, tmp_path):
        """P0-2：同卷 fake production db + hardlink → _is_production_db 识别拒绝。

        不对 15GB 正式库跨盘 hardlink（会 WinError 17）。在 D 盘工作区临时目录建小型
        fake prod，monkeypatch _prod_db_path 指向它，同卷 hardlink，断言拒绝。
        """
        import tempfile as _tf
        # 在工作区根（与正式库同卷 D:）建临时目录，避免跨卷 hardlink
        work_root = _ROOT / ".tmp_fake_prod_test"
        work_root.mkdir(exist_ok=True)
        try:
            fake_prod = work_root / "fake_prod.db"
            c = duckdb.connect(str(fake_prod)); c.execute("CREATE TABLE x(a INT)"); c.close()
            link = work_root / "prod_hardlink.db"
            try:
                os.link(str(fake_prod), str(link))  # 同卷 hardlink → samefile
            except OSError as e:
                pytest.skip(f"环境不支持 hardlink: {e}")
            with mock.patch.object(M, "_prod_db_path", return_value=str(fake_prod)):
                assert M._is_production_db(link) is True
                with pytest.raises(QfqMigrationProductionRefused):
                    migrate_reanchor_2_0_to_2_1(str(link), allowed_root=str(work_root))
        finally:
            import shutil as _sh
            try: _sh.rmtree(work_root)
            except OSError: pass

    def test_directory_junction_file_alias_refused(self, tmp_path):
        """P0-2：directory junction 形成同一 db 文件的别名路径 → 拒绝。

        mklink /J 创建目录 junction；junction 目录内的 db 文件路径经 resolve 后
        与真实库 samefile（os.path.samefile 跟随 junction）。
        """
        if os.name != "nt":
            pytest.skip("junction 仅 Windows")
        import tempfile as _tf
        work_root = _ROOT / ".tmp_junction_test"
        real_dir = work_root / "real_dir"
        real_dir.mkdir(parents=True, exist_ok=True)
        try:
            fake_prod = real_dir / "fake_prod.db"
            c = duckdb.connect(str(fake_prod)); c.execute("CREATE TABLE x(a INT)"); c.close()
            junction_dir = work_root / "junction_dir"
            r = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction_dir), str(real_dir)],
                capture_output=True, timeout=10)
            if r.returncode != 0:
                pytest.skip(f"mklink /J 失败: {r.stderr!r}")
            alias = junction_dir / "fake_prod.db"  # 经 junction 的别名路径
            with mock.patch.object(M, "_prod_db_path", return_value=str(fake_prod)):
                assert M._is_production_db(alias) is True
                with pytest.raises(QfqMigrationProductionRefused):
                    migrate_reanchor_2_0_to_2_1(str(alias), allowed_root=str(work_root))
        finally:
            import shutil as _sh
            try: _sh.rmtree(work_root)
            except OSError: pass

    def test_symlink_alias_refused(self, prod_path, tmp_path):
        """symlink 别名 → 拒绝。"""
        link = tmp_path / "prod_symlink.db"
        try:
            os.symlink(str(prod_path), str(link))
        except OSError as e:
            pytest.skip(f"环境不支持 symlink: {e}")
        with pytest.raises(QfqMigrationProductionRefused):
            migrate_reanchor_2_0_to_2_1(str(link), allowed_root=str(tmp_path))

    def test_production_db_invariant(self, prod_path):
        """正式库 size/mtime/SHA-256 测试前后不变。"""
        st_b = prod_path.stat(); h_b = _fhash(prod_path)
        for v in [str(prod_path), "data/quantstudio.db"]:
            try:
                migrate_reanchor_2_0_to_2_1(v, allowed_root=str(prod_path.parent))
            except QfqMigrationProductionRefused:
                pass
        st_a = prod_path.stat(); h_a = _fhash(prod_path)
        assert (st_b.st_size, st_b.st_mtime, h_b) == (st_a.st_size, st_a.st_mtime, h_a)


# ---------------------------------------------------------------------------
# 6b. report 路径安全（P0-1，B-3b.2）
# ---------------------------------------------------------------------------

class TestReportPathSafety:
    """P0-1：--report 可覆盖 db/正式库/已存在文件的高危漏洞修复。"""

    def test_report_equals_db_refused(self, tmp_path):
        """--report == --db → 拒绝（db 不变）。"""
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        hb = _fhash(db)
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path),
                                        apply=False, report_path=str(db))
        assert hb == _fhash(db)  # db 不变

    def test_report_equals_production_refused(self, tmp_path):
        """staging db + report 指向正式 quantstudio.db → ProductionRefused（正式库不变）。"""
        from quantstudio._paths import db_path
        prod = Path(db_path()).resolve()
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        hb = _fhash(prod)
        with pytest.raises(QfqMigrationProductionRefused):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path),
                                        apply=False, report_path=str(prod))
        assert hb == _fhash(prod)  # 正式库不变

    def test_report_equals_aux_refused(self, tmp_path):
        """staging db + report 指向 qfq_aux.db → 拒绝（aux 不变）。"""
        aux = _ROOT / "data" / "qfq_aux.db"
        if not aux.exists():
            pytest.skip("aux 库不存在")
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        hb = _fhash(aux)
        with pytest.raises(QfqMigrationProductionRefused):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path),
                                        apply=False, report_path=str(aux))
        assert hb == _fhash(aux)

    def test_report_outside_allowed_root_refused(self, tmp_path):
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        outside = tmp_path.parent / "outside_report.json"  # 在 allowed-root 外
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path),
                                        apply=False, report_path=str(outside))

    def test_report_existing_file_not_overwritten(self, tmp_path):
        """report 已存在 → exclusive create 拒绝（原文件 hash 不变）。"""
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        rpt = tmp_path / "existing.json"
        rpt.write_text("EXISTING_EVIDENCE", encoding="utf-8")
        hb = _fhash(rpt)
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path),
                                        apply=False, report_path=str(rpt))
        assert hb == _fhash(rpt)  # 原 report 不变
        assert rpt.read_text(encoding="utf-8") == "EXISTING_EVIDENCE"

    def test_default_report_two_runs_no_collision(self, tmp_path):
        """默认 report 名（微秒+UUID）连续两次不覆盖。"""
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        r1 = migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path), apply=False)
        r2 = migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path), apply=False)
        assert r1.report_path != r2.report_path
        assert Path(r1.report_path).exists() and Path(r2.report_path).exists()

    def test_report_db_hardlink_refused(self, tmp_path):
        """report 是 db 的 hardlink（samefile）→ 拒绝。"""
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        link = tmp_path / "report_as_hardlink.json"
        try:
            os.link(str(db), str(link))  # 同卷（tmp_path 内）hardlink → samefile
        except OSError as e:
            pytest.skip(f"hardlink 不支持: {e}")
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path),
                                        apply=False, report_path=str(link))

    def test_report_safety_runs_before_migration(self, tmp_path):
        """report 路径非法时，apply 不得执行任何迁移（db 仍是 COMPLETE_2_0）。"""
        from quantstudio.pipeline import qfq_schema_status as SS
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        hb = _fhash(db)
        # report==db 应在 apply 前拒绝
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path),
                                        apply=True, report_path=str(db))
        assert hb == _fhash(db)  # db 未被迁移
        c2 = duckdb.connect(str(db), read_only=True)
        assert SS.detect_schema_status(c2) == SS.SchemaStatus.COMPLETE_2_0
        c2.close()

    # ---- B-3b.3 frozen acceptance: final-path reservation and report states ----

    def test_report_parent_is_file_refused_before_migration(self, tmp_path):
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        before = _fhash(db)
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(
                db, allowed_root=tmp_path, apply=True, report_path=db / "r.json")
        assert _fhash(db) == before
        c2 = duckdb.connect(str(db), read_only=True)
        assert SS.detect_schema_status(c2) == SS.SchemaStatus.COMPLETE_2_0
        c2.close()

    def test_final_path_concurrent_claim_is_no_replace(self, tmp_path):
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        before = _fhash(db)
        rpt = tmp_path / "race.json"
        real_open = M.os.open
        injected = {"done": False}

        def racing_open(path, flags, mode=0o777):
            if Path(path) == rpt and not injected["done"]:
                injected["done"] = True
                rpt.write_text("DO-NOT-OVERWRITE", encoding="utf-8")
            return real_open(path, flags, mode)

        with mock.patch.object(M.os, "open", side_effect=racing_open):
            with pytest.raises(QfqMigrationError):
                migrate_reanchor_2_0_to_2_1(
                    db, allowed_root=tmp_path, apply=True, report_path=rpt)
        assert rpt.read_text(encoding="utf-8") == "DO-NOT-OVERWRITE"
        assert _fhash(db) == before

    def test_two_concurrent_reservations_exactly_one_wins(self, tmp_path):
        """Two callers racing for one final path: exactly one O_EXCL claim wins."""
        db = tmp_path / "legacy.db"
        db.write_bytes(b"placeholder")
        rpt = tmp_path / "shared.json"
        barrier = threading.Barrier(2)
        release = threading.Event()
        results = []
        lock = threading.Lock()

        def worker():
            barrier.wait()
            try:
                reservation = M._ReportReservation.reserve(
                    rpt, db, tmp_path, started_at="2026-08-06T00:00:00")
            except Exception as exc:
                with lock:
                    results.append(("error", exc))
                return
            with lock:
                results.append(("success", reservation))
            release.wait(timeout=5)
            reservation.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)
        release.set()
        for thread in threads:
            thread.join(timeout=5)

        successes = [item for kind, item in results if kind == "success"]
        errors = [item for kind, item in results if kind == "error"]
        assert len(successes) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], QfqMigrationError)
        data = json.loads(rpt.read_text(encoding="utf-8"))
        assert data["report_status"] == M.REPORT_STATUS_PENDING

    def test_reserved_path_replacement_is_denied_or_detected(self, tmp_path):
        """OS denial or identity recheck prevents a reserved-path replacement attack."""
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        before = _fhash(db)
        rpt = tmp_path / "owned.json"
        moved = tmp_path / "owned.original"
        original_reserve = M._ReportReservation.reserve
        attack = {"denied": False, "replaced": False}

        def reserve_then_attempt_replace(cls, report_path, db_path, allowed_root, *, started_at):
            reservation = original_reserve(
                report_path, db_path, allowed_root, started_at=started_at)
            try:
                Path(report_path).rename(moved)
                Path(report_path).write_text("EXTERNAL-REPLACEMENT", encoding="utf-8")
                attack["replaced"] = True
            except PermissionError:
                # Windows denies rename/delete while our descriptor is open.
                attack["denied"] = True
            return reservation

        with mock.patch.object(
                M._ReportReservation, "reserve",
                new=classmethod(reserve_then_attempt_replace)):
            if os.name == "nt":
                result = migrate_reanchor_2_0_to_2_1(
                    db, allowed_root=tmp_path, apply=True, report_path=rpt)
                assert attack["denied"] is True
                assert result.report_status == M.REPORT_STATUS_MIGRATION_COMMITTED
                data = json.loads(rpt.read_text(encoding="utf-8"))
                assert data["report_status"] == M.REPORT_STATUS_MIGRATION_COMMITTED
            else:
                with pytest.raises(QfqMigrationError):
                    migrate_reanchor_2_0_to_2_1(
                        db, allowed_root=tmp_path, apply=True, report_path=rpt)
                assert attack["replaced"] is True
                assert _fhash(db) == before
                c2 = duckdb.connect(str(db), read_only=True)
                assert SS.detect_schema_status(c2) == SS.SchemaStatus.COMPLETE_2_0
                c2.close()
                assert rpt.read_text(encoding="utf-8") == "EXTERNAL-REPLACEMENT"
                pending = json.loads(moved.read_text(encoding="utf-8"))
                assert pending["report_status"] == M.REPORT_STATUS_PENDING

    def test_no_temporary_publish_files_created(self, tmp_path):
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        rpt = tmp_path / "report.json"
        migrate_reanchor_2_0_to_2_1(
            db, allowed_root=tmp_path, apply=False, report_path=rpt)
        assert rpt.exists()
        assert list(tmp_path.glob(".*.tmp.*")) == []

    def test_precommit_report_update_failure_keeps_db_legacy(self, tmp_path):
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        before = _fhash(db)
        rpt = tmp_path / "report.json"
        original = M._ReportReservation.write_report
        calls = {"n": 0}

        def fail_terminal(self, report):
            calls["n"] += 1
            if calls["n"] == 1:
                raise QfqMigrationError("simulated precommit report failure")
            return original(self, report)

        with mock.patch.object(M._ReportReservation, "write_report", new=fail_terminal):
            with pytest.raises(QfqMigrationError):
                migrate_reanchor_2_0_to_2_1(
                    db, allowed_root=tmp_path, apply=False, report_path=rpt)
        assert _fhash(db) == before
        data = json.loads(rpt.read_text(encoding="utf-8"))
        assert data["report_status"] == M.REPORT_STATUS_FAILED_PRECHECK

    def test_committed_report_update_failure_is_explicit(self, tmp_path):
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        rpt = tmp_path / "report.json"
        original = M._ReportReservation.write_report
        calls = {"n": 0}

        def fail_first_committed_write(self, report):
            calls["n"] += 1
            if calls["n"] == 1 and report.report_status == M.REPORT_STATUS_MIGRATION_COMMITTED:
                raise QfqMigrationError("simulated committed report failure")
            return original(self, report)

        with mock.patch.object(M._ReportReservation, "write_report", new=fail_first_committed_write):
            with pytest.raises(M.MigrationCommittedReportError) as exc:
                migrate_reanchor_2_0_to_2_1(
                    db, allowed_root=tmp_path, apply=True, report_path=rpt)
        assert exc.value.migration_committed is True
        c2 = duckdb.connect(str(db), read_only=True)
        assert SS.detect_schema_status(c2) == SS.SchemaStatus.COMPLETE_2_1
        c2.close()
        data = json.loads(rpt.read_text(encoding="utf-8"))
        assert data["report_status"] == M.REPORT_STATUS_MIGRATION_COMMITTED
        assert data["report_error"]

    def test_committed_report_failure_recovers_via_already_current(self, tmp_path):
        """A committed-specific failure is recoverable with a fresh audit report."""
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        broken = tmp_path / "broken.json"
        original = M._ReportReservation.write_report
        calls = {"n": 0}

        def fail_first_committed_write(self, report):
            calls["n"] += 1
            if calls["n"] == 1 and report.report_status == M.REPORT_STATUS_MIGRATION_COMMITTED:
                raise QfqMigrationError("simulated committed report failure")
            return original(self, report)

        with mock.patch.object(M._ReportReservation, "write_report", new=fail_first_committed_write):
            with pytest.raises(M.MigrationCommittedReportError):
                migrate_reanchor_2_0_to_2_1(
                    db, allowed_root=tmp_path, apply=True, report_path=broken)

        recovered_path = tmp_path / "recovered.json"
        recovered = migrate_reanchor_2_0_to_2_1(
            db, allowed_root=tmp_path, apply=True, report_path=recovered_path)
        assert recovered.already_current is True
        assert recovered.report_status == M.REPORT_STATUS_ALREADY_CURRENT
        data = json.loads(recovered_path.read_text(encoding="utf-8"))
        assert data["validation_results"]["final_fingerprint_ok"] is True
        assert data["report_status"] == M.REPORT_STATUS_ALREADY_CURRENT

    def test_controlled_rollback_records_rolled_back(self, tmp_path):
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        rpt = tmp_path / "report.json"
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(
                db, allowed_root=tmp_path, apply=True,
                failure_injection="before_commit", report_path=rpt)
        c2 = duckdb.connect(str(db), read_only=True)
        assert SS.detect_schema_status(c2) == SS.SchemaStatus.COMPLETE_2_0
        c2.close()
        data = json.loads(rpt.read_text(encoding="utf-8"))
        assert data["report_status"] == M.REPORT_STATUS_ROLLED_BACK
        assert data["validation_results"]["migration_committed"] is False

    def test_terminal_report_states(self, tmp_path):
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        dry_path = tmp_path / "dry.json"
        dry = migrate_reanchor_2_0_to_2_1(
            db, allowed_root=tmp_path, apply=False, report_path=dry_path)
        assert dry.report_status == M.REPORT_STATUS_DRY_RUN_COMPLETE
        assert json.loads(dry_path.read_text(encoding="utf-8"))["report_status"] == M.REPORT_STATUS_DRY_RUN_COMPLETE

        apply_path = tmp_path / "apply.json"
        applied = migrate_reanchor_2_0_to_2_1(
            db, allowed_root=tmp_path, apply=True, report_path=apply_path)
        assert applied.report_status == M.REPORT_STATUS_MIGRATION_COMMITTED
        assert json.loads(apply_path.read_text(encoding="utf-8"))["report_status"] == M.REPORT_STATUS_MIGRATION_COMMITTED

        current_path = tmp_path / "current.json"
        current = migrate_reanchor_2_0_to_2_1(
            db, allowed_root=tmp_path, apply=True, report_path=current_path)
        assert current.report_status == M.REPORT_STATUS_ALREADY_CURRENT
        assert json.loads(current_path.read_text(encoding="utf-8"))["report_status"] == M.REPORT_STATUS_ALREADY_CURRENT

# ---------------------------------------------------------------------------

class TestAllowedRoot:
    def test_outside_root_refused(self, tmp_path):
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _build_fingerprint_db(c, C.LEGACY_QFQ_2_0_FINGERPRINT)
        c.execute(C.SOURCE_WATERMARK_2_0_DDL); c.close()
        other = tmp_path / "other"; other.mkdir()
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(other))

    def test_equal_to_root_refused(self, tmp_path):
        with pytest.raises(QfqMigrationError):
            M._assert_allowed_root(tmp_path, tmp_path)


# ---------------------------------------------------------------------------
# 8. 13 故障点
# ---------------------------------------------------------------------------

class TestFailureInjection:
    @pytest.mark.parametrize("fp", FAILURE_POINTS[:12])
    def test_first_12_rollback_to_2_0(self, tmp_path, fp):
        db = tmp_path / f"fault_{fp}.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path), apply=True,
                                        failure_injection=fp)
        c2 = duckdb.connect(str(db), read_only=True)
        assert SS.detect_schema_status(c2) == SS.SchemaStatus.COMPLETE_2_0
        residue = [r[0] for r in c2.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE '%__b3b%'"
        ).fetchall()]
        c2.close()
        assert residue == []

    def test_point_13_already_current(self, tmp_path):
        db = tmp_path / "fault_p13.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        with pytest.raises(QfqMigrationError):
            migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path), apply=True,
                                        failure_injection="after_commit_before_report")
        c2 = duckdb.connect(str(db), read_only=True)
        assert SS.detect_schema_status(c2) == SS.SchemaStatus.COMPLETE_2_1
        c2.close()
        h1 = _fhash(db)
        r = migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path), apply=True)
        assert r.already_current and h1 == _fhash(db)


# ---------------------------------------------------------------------------
# 9. 真正 os._exit 子进程崩溃恢复（P0-3）
# ---------------------------------------------------------------------------

class TestHardCrashRecovery:
    def test_os_exit_during_migrate_rolls_back_with_content_equivalence(self, tmp_path):
        """子进程用 os._exit(91) 强制崩溃（非受控异常）→ DuckDB 重开 = COMPLETE_2_0
        + **业务内容等价**（legacy row counts / 内容 hashes / schema fingerprint 不变）。"""
        db = tmp_path / "crash.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        # 崩溃前快照：legacy row counts + 内容 hashes（用 LEGACY 指纹）
        _managed = list(C.LEGACY_MAIN_DB_2_0_FINGERPRINT.keys())
        before_counts, before_hashes = M._snapshot(
            duckdb.connect(str(db), read_only=True), _managed, C.LEGACY_MAIN_DB_2_0_FINGERPRINT)
        # 写子进程脚本到临时文件（避免 -c 内联缩进问题）
        script = tmp_path / "hard_crash.py"
        script.write_text(
            "import sys, os\n"
            f"sys.path.insert(0, {str(_ROOT)!r})\n"
            "from quantstudio.pipeline.qfq_schema_migration import (\n"
            "    migrate_reanchor_2_0_to_2_1, _FailureInjector, QfqMigrationError)\n"
            "def hard_fire(self, point):\n"
            "    if point == 'before_commit':\n"
            "        os._exit(91)  # 硬崩溃，不走 ROLLBACK\n"
            "    if point == self.point:\n"
            "        raise QfqMigrationError('injected ' + point)\n"
            "_FailureInjector.fire = hard_fire\n"
            f"migrate_reanchor_2_0_to_2_1({str(db)!r}, allowed_root={str(tmp_path)!r}, "
            "apply=True, failure_injection='before_commit', "
            f"report_path={str(tmp_path / 'r.json')!r})\n",
            encoding="utf-8")
        result = subprocess.run([sys.executable, str(script)], capture_output=True)
        assert result.returncode == 91  # 硬崩溃退出码
        pending_report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
        assert pending_report["report_status"] == M.REPORT_STATUS_PENDING
        assert pending_report["db_path"] == str(db.resolve())
        # 重开：必须 COMPLETE_2_0 + 业务内容等价
        c2 = duckdb.connect(str(db), read_only=True)
        assert SS.detect_schema_status(c2) == SS.SchemaStatus.COMPLETE_2_0
        residue = [r[0] for r in c2.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE '%__b3b%'"
        ).fetchall()]
        # 业务内容等价：row counts + 内容 hashes 崩溃前后一致
        after_counts, after_hashes = M._snapshot(c2, _managed, C.LEGACY_MAIN_DB_2_0_FINGERPRINT)
        c2.close()
        assert residue == []
        assert before_counts == after_counts, f"row counts 崩溃前后不一致: {before_counts} vs {after_counts}"
        assert before_hashes == after_hashes, f"内容 hashes 崩溃前后不一致"

    def test_os_exit_after_commit_leaves_pending_and_recovers(self, tmp_path):
        """Hard crash after durable COMMIT, before connection cleanup/report, is recoverable."""
        db = tmp_path / "post_commit_crash.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        report_path = tmp_path / "pending_after_commit.json"
        script = tmp_path / "post_commit_crash.py"
        script.write_text(
            "import sys, os\n"
            f"sys.path.insert(0, {str(_ROOT)!r})\n"
            "from quantstudio.pipeline.qfq_schema_migration import "
            "migrate_reanchor_2_0_to_2_1, _FailureInjector\n"
            "_orig = _FailureInjector.fire\n"
            "def hard_fire(self, point):\n"
            "    if point == 'after_commit_before_report':\n"
            "        os._exit(92)\n"
            "    return _orig(self, point)\n"
            "_FailureInjector.fire = hard_fire\n"
            f"migrate_reanchor_2_0_to_2_1({str(db)!r}, allowed_root={str(tmp_path)!r}, "
            "apply=True, failure_injection='after_commit_before_report', "
            f"report_path={str(report_path)!r})\n",
            encoding="utf-8")
        result = subprocess.run([sys.executable, str(script)], capture_output=True)
        assert result.returncode == 92
        c2 = duckdb.connect(str(db), read_only=True)
        assert SS.detect_schema_status(c2) == SS.SchemaStatus.COMPLETE_2_1
        c2.close()
        pending = json.loads(report_path.read_text(encoding="utf-8"))
        assert pending["report_status"] == M.REPORT_STATUS_PENDING

        recovered_path = tmp_path / "post_commit_recovered.json"
        recovered = migrate_reanchor_2_0_to_2_1(
            db, allowed_root=tmp_path, apply=True, report_path=recovered_path)
        assert recovered.already_current is True
        assert recovered.report_status == M.REPORT_STATUS_ALREADY_CURRENT


# ---------------------------------------------------------------------------
# 10. CLI 子进程测试（P0-3）
# ---------------------------------------------------------------------------

class TestCLISubprocess:
    def _run_cli(self, args, cwd=None):
        return subprocess.run(
            [sys.executable, "-m", "quantstudio.pipeline.qfq_schema_migration"] + args,
            capture_output=True, text=True, cwd=cwd or str(_ROOT))

    def test_cli_committed_report_error_exit3(self, monkeypatch, capsys):
        """CLI has a distinct exit code when DB COMMIT is already durable."""
        def committed_error(*args, **kwargs):
            raise M.MigrationCommittedReportError(
                "committed", db_path="x.db", report_path="r.json")

        monkeypatch.setattr(M, "migrate_reanchor_2_0_to_2_1", committed_error)
        rc = M._cli_main(["--db", "x.db", "--allowed-root", ".", "--apply"])
        captured = capsys.readouterr()
        assert rc == 3
        assert "COMMITTED_REPORT_ERROR" in captured.err

    def test_cli_dry_run_exit0_and_report_json(self, tmp_path):
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        report_path = tmp_path / "report.json"
        r = self._run_cli(["--db", str(db), "--allowed-root", str(tmp_path),
                           "--report", str(report_path)])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["dry_run"] is True and data["applied"] is False
        # report 文件落盘 + 字段完整
        assert report_path.exists()
        fdata = json.loads(report_path.read_text(encoding="utf-8"))
        for key in ("hashes_before", "hashes_after", "content_hash_version",
                    "report_path", "validation_results"):
            assert key in fdata, f"report 缺字段 {key}"
        assert fdata["content_hash_version"] == "b3b-sha256-v1"
        # hashes_before 全有效
        assert all(len(h) == 64 for h in fdata["hashes_before"].values())

    def test_cli_apply_exit0(self, tmp_path):
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        r = self._run_cli(["--db", str(db), "--allowed-root", str(tmp_path), "--apply"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["applied"] is True and data["target_status"] == "complete_2_1"

    def test_cli_production_refused_exit2(self, tmp_path):
        from quantstudio._paths import db_path
        prod = str(Path(db_path()).resolve())
        r = self._run_cli(["--db", prod, "--allowed-root", str(Path(prod).parent)])
        assert r.returncode == 2
        assert "REFUSED" in r.stderr

    def test_cli_allow_production_still_refused(self, tmp_path):
        from quantstudio._paths import db_path
        prod = str(Path(db_path()).resolve())
        r = self._run_cli(["--db", prod, "--allowed-root", str(Path(prod).parent),
                           "--allow-production"])
        assert r.returncode == 2

    def test_cli_invalid_allowed_root_exit1(self, tmp_path):
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        other = tmp_path / "other"; other.mkdir()
        r = self._run_cli(["--db", str(db), "--allowed-root", str(other)])
        assert r.returncode == 1
        assert "ERROR" in r.stderr


# ---------------------------------------------------------------------------
# 11. 内容 hash 黄金常量（P0-1：编码规范化）
# ---------------------------------------------------------------------------

class TestContentHashGolden:
    def test_null_vs_real_backslash_n_distinct(self):
        """NULL 编码与真实字符串 \\N 不同（无碰撞）。"""
        backslashN = chr(92) + "N"
        assert _encode_row([None]) != _encode_row([backslashN])

    def test_golden_row_hash(self):
        """黄金常量：[1, 'x', None] 行 hash 固定。"""
        h = hashlib.sha256(_encode_row([1, "x", None]).encode("utf-8")).hexdigest()
        assert h == "1ca8895fee8931782cd84cbf015d36b4337b275eb6e4eabe1d7f66f0e37f3621"

    def test_float_normalization(self):
        assert _encode_value(0.0) == "0.0"
        assert _encode_value(-0.0) == "-0.0"
        assert _encode_value(float("nan")) == "NaN"
        assert _encode_value(float("inf")) == "Infinity"
        assert _encode_value(float("-inf")) == "-Infinity"
        assert _encode_value(1.5) == "1.5"

    def test_bool_before_int(self):
        assert _encode_value(True) == "True"
        assert _encode_value(False) == "False"

    def test_separator_no_collision(self):
        """字段值含 \\x1f/\\x1e 分隔符也不碰撞（长度前缀界定）。"""
        sep = chr(0x1F)
        row_a = _encode_row(["a" + sep + "b"])
        row_b = _encode_row(["a", "b"])
        assert row_a != row_b


# ---------------------------------------------------------------------------
# 12. 最终 integrity（行数一致 + target fingerprint + hashes 全有效）
# ---------------------------------------------------------------------------

class TestFinalIntegrity:
    def test_row_counts_preserved(self, legacy_db, tmp_path):
        r = migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        for t in M.REBUILD_QFQ_TABLES:
            if t in r.row_counts_before:
                assert r.row_counts_before[t] == r.row_counts_after.get(t)

    def test_hashes_after_all_valid(self, legacy_db, tmp_path):
        r = migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        assert all(len(h) == 64 for h in r.hashes_after.values())

    def test_final_fingerprint_matches_target(self, legacy_db, tmp_path):
        migrate_reanchor_2_0_to_2_1(str(legacy_db), allowed_root=str(tmp_path), apply=True)
        conn = duckdb.connect(str(legacy_db), read_only=True)
        assert C.verify_fingerprint(conn, C.TARGET_MAIN_DB_2_1_FINGERPRINT,
                                    reject_extra=True, strict_order=True)
        conn.close()

    def test_hash_fail_closed(self, tmp_path):
        """P0-1：内容 hash 查询失败必须 fail-closed（不静默写空串）。"""
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        # mock _content_hash 抛错 → snapshot 必须 fail-closed
        with mock.patch.object(M, "_content_hash", side_effect=RuntimeError("hash boom")):
            with pytest.raises(QfqMigrationError):
                migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path), apply=False)

    def test_row_count_fail_closed(self, tmp_path):
        """P0-2：row count 查询失败必须 fail-closed（不返 -1）。"""
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        with mock.patch.object(M, "_row_count", side_effect=RuntimeError("count boom")):
            with pytest.raises(QfqMigrationError):
                migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path), apply=False)

    def test_validation_results_contain_not_null_summary(self, tmp_path):
        """P0-2：apply 后 validation_results.per_table 含 NOT NULL 完整字段。"""
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        r = migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path), apply=True)
        per_table = r.validation_results.get("per_table", [])
        assert len(per_table) > 0
        for entry in per_table:
            assert "not_null_columns_checked" in entry
            assert "not_null_violation_count" in entry
            assert "not_null_counts_by_column" in entry
            assert entry["not_null_violation_count"] == 0  # 成功迁移无 NOT NULL 违反

    def test_already_current_report_has_audit_data(self, tmp_path):
        """P0-2：COMPLETE_2_1 幂等路径报告含只读审计（row counts/hashes/fingerprint）。"""
        db = tmp_path / "legacy.db"
        c = duckdb.connect(str(db)); _seed_full_legacy(c); c.close()
        migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path), apply=True,
                                    report_path=str(tmp_path / "r1.json"))
        r = migrate_reanchor_2_0_to_2_1(str(db), allowed_root=str(tmp_path), apply=True,
                                        report_path=str(tmp_path / "r2.json"))
        assert r.already_current is True
        assert len(r.row_counts_after) > 0  # 含 target row counts
        assert len(r.hashes_after) > 0      # 含 target hashes
        assert r.validation_results.get("final_fingerprint_ok") is True
        assert r.validation_results.get("shadow_residue_count") == 0
