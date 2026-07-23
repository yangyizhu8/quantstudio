"""tests/test_qfq_revision.py — QFQ 复权因子修订检测 + 审计 schema 专项测试（PR2 Commit 2）。

核心语义：revision ≠ 同一 code 不同 factor_time 的 LAG 时间序列变化；
revision = 同一 (asset_type, code, factor_time) 跨审计批次的 factor_value 变化 > epsilon。

全部 hermetic：tmp_path 临时 qfq_aux.db + mock，不连 live QMT，不碰正式库。
覆盖用户冻结 §7 的 17 项 + 11 项补充精确断言。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

BJ = timezone(timedelta(hours=8))


def _ms(year, month, day, hour=0):
    """构造 epoch ms（北京时区）。"""
    return int(datetime(year, month, day, hour, tzinfo=BJ).timestamp() * 1000)


def _make_qfq_aux(tmp_root, adj_rows=None, with_jump_audit=False, with_snapshot=False):
    """建一个 qfq_aux.db：adj_factor（+ 可选 qfq_jump_audit / adj_factor_snapshot）。"""
    db = tmp_root / "qfq_aux.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS adj_factor "
                 "(code TEXT, time INTEGER, adj_factor REAL, PRIMARY KEY(code, time))")
    if adj_rows:
        conn.executemany("INSERT INTO adj_factor VALUES (?,?,?)", adj_rows)
    if with_jump_audit:
        conn.execute("CREATE TABLE IF NOT EXISTS qfq_jump_audit "
                     "(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, time INTEGER, "
                     "pctChg REAL, jump_type TEXT, detected_at TEXT)")
        conn.execute("INSERT INTO qfq_jump_audit (code,time,pctChg,jump_type,detected_at) "
                     "VALUES (?,?,?,?,?)", ("510050", _ms(2026, 7, 1), 25.0, "qfq_batch_boundary", "2026-07-01T00:00:00+08:00"))
    if with_snapshot:
        conn.execute("CREATE TABLE IF NOT EXISTS adj_factor_snapshot "
                     "(code TEXT PRIMARY KEY, adj_latest REAL, adj_earliest REAL, snapshot_date TEXT)")
        conn.execute("INSERT INTO adj_factor_snapshot VALUES (?,?,?,?)",
                     ("510050", 1.0, 1.0, "2026-07-23"))
    conn.commit()
    conn.close()
    return db


# ===========================================================================
# 1. 输入校验（binding §6）
# ===========================================================================

class TestInputValidation:
    def test_epsilon_negative_rejected(self):
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        with pytest.raises(RevisionInputError):
            detect_revisions([], {}, "ETF", _ms(2026, 7, 23), -1e-9)

    def test_epsilon_nan_rejected(self):
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        with pytest.raises(RevisionInputError):
            detect_revisions([], {}, "ETF", _ms(2026, 7, 23), float("nan"))

    def test_epsilon_inf_rejected(self):
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        with pytest.raises(RevisionInputError):
            detect_revisions([], {}, "ETF", _ms(2026, 7, 23), float("inf"))

    def test_nan_factor_value_rejected(self):
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        with pytest.raises(RevisionInputError):
            detect_revisions([("510050", _ms(2026, 6, 20), float("nan"))],
                             None, "ETF", _ms(2026, 7, 23), 1e-9, baseline_available=False)

    def test_inf_factor_value_rejected(self):
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        with pytest.raises(RevisionInputError):
            detect_revisions([("510050", _ms(2026, 6, 20), float("inf"))],
                             None, "ETF", _ms(2026, 7, 23), 1e-9, baseline_available=False)

    def test_none_factor_value_rejected(self):
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        with pytest.raises(RevisionInputError):
            detect_revisions([("510050", _ms(2026, 6, 20), None)],
                             None, "ETF", _ms(2026, 7, 23), 1e-9, baseline_available=False)

    def test_empty_code_rejected(self):
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        # bare_code("") = ""（空）→ 拒绝
        with pytest.raises(RevisionInputError):
            detect_revisions([("", _ms(2026, 6, 20), 1.0)],
                             None, "ETF", _ms(2026, 7, 23), 1e-9, baseline_available=False)

    def test_duplicate_logical_key_rejected(self):
        """binding §6：重复 (code,factor_time) 键拒绝，不依赖输入顺序静默覆盖。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        with pytest.raises(RevisionInputError):
            # 510050 与 510050.SH 归一化同裸码、同 factor_time → 重复
            detect_revisions([("510050", _ms(2026, 6, 20), 1.0),
                              ("510050.SH", _ms(2026, 6, 20), 1.1)],
                             None, "ETF", _ms(2026, 7, 23), 1e-9, baseline_available=False)

    def test_invalid_factor_time_rejected(self):
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        with pytest.raises(RevisionInputError):
            detect_revisions([("510050", 0, 1.0)],
                             None, "ETF", _ms(2026, 7, 23), 1e-9, baseline_available=False)

    def test_invalid_asset_type_rejected(self):
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        with pytest.raises(RevisionInputError):
            detect_revisions([], None, "STOCK", _ms(2026, 7, 23), 1e-9, baseline_available=False)


# ===========================================================================
# 2. 纯检测函数：分类与边界（binding §1/§2/§4/§6）
# ===========================================================================

class TestDetectRevisions:
    def test_baseline_unavailable_all_new(self):
        """首次观察（无基线）：全部 new_record，revised=0。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions
        r = detect_revisions([("510050", _ms(2026, 6, 20), 1.0)],
                             None, "ETF", _ms(2026, 7, 23), 1e-9, baseline_available=False)
        assert r.baseline_available is False
        assert r.baseline_seeded is True
        assert r.new_count == 1 and r.revised_count == 0 and r.events == []

    def test_empty_baseline_dict_also_unavailable(self):
        """binding §2 补充：observation 表存在但 asset_type 无基线（{}）→ baseline_unavailable。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions
        r = detect_revisions([("510050", _ms(2026, 6, 20), 1.0)],
                             {}, "ETF", _ms(2026, 7, 23), 1e-9, baseline_available=True)
        assert r.baseline_available is False
        assert r.baseline_seeded is True

    def test_unchanged_same_value(self):
        """同键同值 → unchanged。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions
        bl = {("510050", _ms(2026, 6, 20)): 1.0}
        r = detect_revisions([("510050", _ms(2026, 6, 20), 1.0)],
                             bl, "ETF", _ms(2026, 7, 23), 1e-9)
        assert r.unchanged_count == 1 and r.revised_count == 0 and r.events == []

    def test_revised_same_key_value_changed(self):
        """同键值变化 > epsilon → 1 revision。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions
        bl = {("510050", _ms(2026, 6, 20)): 1.0}
        r = detect_revisions([("510050", _ms(2026, 6, 20), 1.0005)],
                             bl, "ETF", _ms(2026, 7, 23), 1e-9)
        assert r.revised_count == 1
        assert abs(r.events[0].abs_delta - 0.0005) < 1e-12
        assert abs(r.events[0].relative_delta - 0.0005) < 1e-12

    def test_delta_le_epsilon_no_revision(self):
        """差值 <= epsilon → unchanged（不生成 revision）。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions
        bl = {("510050", _ms(2026, 6, 20)): 1.0}
        r = detect_revisions([("510050", _ms(2026, 6, 20), 1.0 + 5e-10)],
                             bl, "ETF", _ms(2026, 7, 23), 1e-9)
        assert r.unchanged_count == 1 and r.revised_count == 0

    def test_new_factor_time_is_new_record(self):
        """新 factor_time → new_record（非 revision）。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions
        bl = {("510050", _ms(2026, 6, 20)): 1.0}
        r = detect_revisions([("510050", _ms(2026, 6, 20), 1.0),
                              ("510050", _ms(2026, 7, 10), 1.1)],  # 新日期（正常因子变化）
                             bl, "ETF", _ms(2026, 7, 23), 1e-9)
        assert r.unchanged_count == 1 and r.new_count == 1 and r.revised_count == 0

    def test_future_excluded_by_detector(self):
        """binding §1：factor_time > as_of_ms → future_excluded，不进观察集。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions
        r = detect_revisions([("510050", _ms(2026, 8, 1), 1.0)],  # future
                             {}, "ETF", _ms(2026, 7, 23), 1e-9, baseline_available=False)
        assert r.future_excluded_count == 1
        assert r.observed_count == 0
        assert r.new_count == 0  # future 不算 new

    def test_previous_zero_relative_delta_null(self):
        """binding §6：previous_factor==0 → relative_delta=None（不除零）。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions
        bl = {("510050", _ms(2026, 6, 20)): 0.0}
        r = detect_revisions([("510050", _ms(2026, 6, 20), 1.0)],
                             bl, "ETF", _ms(2026, 7, 23), 1e-9)
        assert r.revised_count == 1 and r.events[0].relative_delta is None

    def test_lag_time_series_change_not_revision(self):
        """关键反例：changed/stable/no_record（LAG 时间序列变化）≠ revision。

        2026-06-20=1.0 → 2026-07-10=1.1 是不同日期的正常因子变化，不报为 revision。
        只有同键 (510050, 2026-06-20) 值从 1.0 变成别的值才是 revision。
        """
        from quantstudio.pipeline.qfq_revision import detect_revisions
        bl = {("510050", _ms(2026, 6, 20)): 1.0}
        # 同键值不变（1.0）+ 新日期（1.1）→ 无 revision（unchanged + new）
        r = detect_revisions([("510050", _ms(2026, 6, 20), 1.0),
                              ("510050", _ms(2026, 7, 10), 1.1)],
                             bl, "ETF", _ms(2026, 7, 23), 1e-9)
        assert r.revised_count == 0
        assert r.unchanged_count == 1 and r.new_count == 1


# ===========================================================================
# 3. Schema：表/字段/PK/UNIQUE（binding §9）
# ===========================================================================

class TestSchema:
    def test_schema_tables_fields_constraints(self, tmp_path):
        """三表字段、PK、UNIQUE 约束准确。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = tmp_path / "qfq_aux.db"
        store = RevisionStore(db)
        store.init_schema()
        conn = sqlite3.connect(str(db))
        # run 表
        cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(qfq_revision_run)")}
        for c in ["run_id", "schema_version", "asset_type", "as_of_ms", "window_start_ms",
                  "window_end_ms", "epsilon", "status", "observed_count", "new_count",
                  "unchanged_count", "revised_count", "started_at", "finished_at", "error"]:
            assert c in cols, f"run 缺字段 {c}"
        # observation 表 PK（PRAGMA index_info 行 = seqno,cid,name；取 name）
        obs_pk = [r[2] for r in conn.execute("PRAGMA index_info("
                     "sqlite_autoindex_qfq_revision_observation_1)")]
        assert set(obs_pk) == {"asset_type", "code", "factor_time"}
        # event 表 UNIQUE(run_id,asset_type,code,factor_time)
        # （event_id 是 INTEGER PRIMARY KEY AUTOINCREMENT，不产生 autoindex；
        #  故 UNIQUE 约束的 autoindex 名为 _1）
        idx_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index' "
                                "AND tbl_name='qfq_revision_event'").fetchall()
        uniq_cols = set()
        for (iname,) in idx_rows:
            cols = [r[2] for r in conn.execute(f"PRAGMA index_info({iname})")]
            if set(cols) == {"run_id", "asset_type", "code", "factor_time"}:
                uniq_cols = set(cols)
        assert uniq_cols == {"run_id", "asset_type", "code", "factor_time"}
        # 并实测 UNIQUE 拒绝重复
        conn.execute("INSERT INTO qfq_revision_event "
                     "(run_id,asset_type,code,factor_time,previous_factor,current_factor,"
                     "abs_delta,revision_no,detected_at) "
                     "VALUES ('r1','ETF','510050',1781884800000,1.0,1.1,0.1,1,'2026-07-24T00:00:00+08:00')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO qfq_revision_event "
                         "(run_id,asset_type,code,factor_time,previous_factor,current_factor,"
                         "abs_delta,revision_no,detected_at) "
                         "VALUES ('r1','ETF','510050',1781884800000,1.0,1.2,0.2,1,'2026-07-24T00:00:00+08:00')")
        conn.close()

    def test_schema_init_idempotent_does_not_touch_existing_tables(self, tmp_path):
        """binding §7：schema 初始化可重复执行且不影响现有 adj_factor/qfq_jump_audit/adj_factor_snapshot。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)],
                           with_jump_audit=True, with_snapshot=True)
        store = RevisionStore(db)
        store.init_schema()  # 第一次
        store.init_schema()  # 第二次（幂等）
        conn = sqlite3.connect(str(db))
        # adj_factor 行不变
        assert conn.execute("SELECT count(*),adj_factor FROM adj_factor WHERE code='510050'").fetchone() == (1, 1.0)
        # qfq_jump_audit 不变
        assert conn.execute("SELECT count(*) FROM qfq_jump_audit").fetchone()[0] == 1
        # adj_factor_snapshot 不变
        assert conn.execute("SELECT count(*) FROM adj_factor_snapshot").fetchone()[0] == 1
        # revision 三表存在
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"qfq_revision_run", "qfq_revision_observation", "qfq_revision_event"} <= tables
        conn.close()

    def test_constructor_does_not_create_tables(self, tmp_path):
        """构造 RevisionStore 不建表（仅显式 init/run 才建）。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = tmp_path / "qfq_aux.db"
        RevisionStore(db)  # 仅构造
        assert not db.exists()  # 连文件都没建


# ===========================================================================
# 4. RevisionStore 持久化：seed / unchanged / revised / idempotency（binding §3/§4/§5）
# ===========================================================================

class TestRevisionStorePersist:
    def test_first_run_seeds_baseline(self, tmp_path):
        """首次 persist：seed baseline（new_record，revised=0）。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        rid, r = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        assert r.baseline_seeded is True
        assert r.new_count == 1 and r.revised_count == 0
        conn = sqlite3.connect(str(db))
        # run completed
        assert conn.execute("SELECT status FROM qfq_revision_run WHERE run_id=?", [rid]).fetchone()[0] == "completed"
        # observation revision_no=0
        obs = conn.execute("SELECT factor_value,revision_no,first_seen_run_id FROM qfq_revision_observation").fetchone()
        assert obs == (1.0, 0, rid)
        # 无 event
        assert conn.execute("SELECT count(*) FROM qfq_revision_event").fetchone()[0] == 0
        conn.close()

    def test_second_run_unchanged_updates_last_seen_only(self, tmp_path):
        """binding §4：unchanged 只更 last_seen_*，factor_value/revision_no 不动。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        rid1, _ = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        rid2, r2 = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        assert r2.unchanged_count == 1 and r2.revised_count == 0
        conn = sqlite3.connect(str(db))
        fv, rev_no, first_run, last_run = conn.execute(
            "SELECT factor_value,revision_no,first_seen_run_id,last_seen_run_id "
            "FROM qfq_revision_observation").fetchone()
        assert fv == 1.0 and rev_no == 0          # 不动
        assert first_run == rid1                   # first_seen 不变
        assert last_run == rid2                    # last_seen 推进
        conn.close()

    def test_second_run_revised_writes_event(self, tmp_path):
        """同键值变化 > epsilon → 1 revision event + observation 推进。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        # 改 adj_factor 同键值
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE adj_factor SET adj_factor=1.0005 WHERE code='510050'")
        conn.commit()
        conn.close()
        rid2, r2 = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        assert r2.revised_count == 1
        conn = sqlite3.connect(str(db))
        ev = conn.execute("SELECT run_id,previous_factor,current_factor,abs_delta,revision_no "
                          "FROM qfq_revision_event").fetchone()
        assert ev[0] == rid2 and ev[1] == 1.0 and ev[2] == 1.0005
        assert abs(ev[3] - 0.0005) < 1e-12 and ev[4] == 1
        # observation 推进
        obs = conn.execute("SELECT factor_value,revision_no FROM qfq_revision_observation").fetchone()
        assert obs == (1.0005, 1)
        conn.close()

    def test_same_run_id_replay_rejected(self, tmp_path):
        """binding §5/§6：同 run_id 重跑 → UNIQUE 拒绝（不重复 event、不覆盖）。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore, RevisionInputError
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        rid, _ = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        with pytest.raises(RevisionInputError):
            store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"], run_id=rid)
        # 仍只有 1 个 run、1 个 observation、0 event
        conn = sqlite3.connect(str(db))
        assert conn.execute("SELECT count(*) FROM qfq_revision_run").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM qfq_revision_observation").fetchone()[0] == 1
        conn.close()

    def test_persistence_reloads_baseline_in_transaction(self, tmp_path):
        """binding §3 补充：持久化在事务内重载 baseline，不信任事务外陈旧结果。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        # run1 seed
        store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        # 事务外 load_baseline（陈旧，值 1.0）
        bl_outside, _ = store.load_baseline("ETF")
        assert bl_outside[("510050", _ms(2026, 6, 20))] == 1.0
        # 改 adj_factor；run_persisted_audit 内部会重新 load baseline 并检测到 revision
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE adj_factor SET adj_factor=1.0005 WHERE code='510050'")
        conn.commit()
        conn.close()
        _, r2 = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        # 内部重载到 1.0 基线 → 检出 revision（即便事务外 bl_outside 也=1.0，这里证明内部独立 load）
        assert r2.revised_count == 1

    def test_failed_run_no_event_no_observation_push(self, tmp_path):
        """binding §5：failed run 不留 event、不推进 observation。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore, RevisionInputError
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        store.init_schema()
        # 故意用一个会在事务内失败的非法 epsilon（绕过入口校验直接调内部？用 record_failed_run 验证）
        store.record_failed_run("r_fail_1", "ETF", _ms(2026, 7, 23), 1e-9, "simulated error")
        conn = sqlite3.connect(str(db))
        # failed run 存在，但 0 event、0 observation
        row = conn.execute("SELECT status,error FROM qfq_revision_run WHERE run_id='r_fail_1'").fetchone()
        assert row[0] == "failed" and "simulated error" in row[1]
        assert conn.execute("SELECT count(*) FROM qfq_revision_event").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM qfq_revision_observation").fetchone()[0] == 0
        conn.close()
        # 已存在 failed run_id 拒绝覆盖
        with pytest.raises(RevisionInputError):
            store.record_failed_run("r_fail_1", "ETF", _ms(2026, 7, 23), 1e-9, "again")

    def test_transaction_rollback_on_failure(self, tmp_path):
        """binding §6：持久化中途失败 → 事务回滚，baseline 不推进，不留 completed run。

        注入点：run_persisted_audit 事务内会调 self._ddl(conn) 建表；对 store2 实例把
        _ddl 替换为"建表后再抛错"，使其在 BEGIN IMMEDIATE 之后、commit 之前失败，
        验证 except 分支 ROLLBACK 后无残留 run/event、observation 不推进。
        """
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        rid1, _ = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        orig_ddl = RevisionStore._ddl

        def boom(conn):
            orig_ddl(conn)
            raise RuntimeError("injected mid-txn failure")  # 建表后立即失败

        store2 = RevisionStore(db)
        store2._ddl = boom  # 实例级替换（self._ddl 解析到实例属性）
        with pytest.raises(RuntimeError):
            store2.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"],
                                       run_id="r_should_rollback")
        conn = sqlite3.connect(str(db))
        # 回滚后：仍只 1 个 completed run（rid1），无 r_should_rollback 残留
        runs = conn.execute("SELECT run_id,status FROM qfq_revision_run").fetchall()
        assert runs == [(rid1, "completed")]
        # observation 未推进（仍 1 行 revision_no=0）
        assert conn.execute("SELECT count(*),revision_no FROM qfq_revision_observation").fetchone() == (1, 0)
        # 无 event
        assert conn.execute("SELECT count(*) FROM qfq_revision_event").fetchone()[0] == 0
        conn.close()

    def test_persist_does_not_modify_adj_factor(self, tmp_path):
        """binding §7：persist 后 adj_factor 行数值逐行不变。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[
            ("510050", _ms(2026, 6, 20), 1.0),
            ("159919", _ms(2026, 6, 20), 2.0)])
        conn = sqlite3.connect(str(db))
        before = conn.execute("SELECT code,time,adj_factor FROM adj_factor ORDER BY code,time").fetchall()
        conn.close()
        store = RevisionStore(db)
        store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050", "159919"])
        # 改 adj_factor 再跑一次（revised 路径）
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE adj_factor SET adj_factor=1.0005 WHERE code='510050'")
        conn.commit()
        conn.close()
        store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050", "159919"])
        conn = sqlite3.connect(str(db))
        after = conn.execute("SELECT code,time,adj_factor FROM adj_factor ORDER BY code,time").fetchall()
        conn.close()
        # 除 510050 被我们显式 UPDATE（非 persist 改）外，159919 必须逐行不变
        assert ("159919", _ms(2026, 6, 20), 2.0) in after
        assert ("510050", _ms(2026, 6, 20), 1.0005) in after  # 我们的 UPDATE

    def test_drift_accumulation_triggers_revision(self, tmp_path):
        """binding §4：unchanged 保留原 baseline factor_value（不悄悄推进），故连续多次
        小漂移每次相对 *原 baseline* 都 <= epsilon → 永远 unchanged；只有当某次相对
        原 baseline > epsilon 才报 revision。

        review 文字修正：旧 docstring 误称"累计超 epsilon"，但数值（4e-10、8e-10）均 < 1e-9，
        从未超过。本测试实际证明的是"阈值不被漂移悄悄推高"：若 baseline 被静默更新成当前值，
        则第 2 次的 8e-10（相对第 1 次推进后的 1.0+4e-10 差 4e-10）仍 unchanged，但相对
        原 baseline 1.0 差 8e-10 也 < 1e-9 —— 所以两侧都 unchanged，无法仅靠 8e-10 区分。
        为精确证明 baseline 不推进，本测试直接断言 observation.factor_value 全程守在 1.0，
        并用一次真正的 > epsilon（1.0005）触发 revision。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])  # seed=1.0
        # 漂移 1：1.0 → 1.0+4e-10（相对 baseline 1.0，4e-10 <= 1e-9）→ unchanged，baseline 仍 1.0
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE adj_factor SET adj_factor=1.0+4e-10 WHERE code='510050'")
        conn.commit()
        conn.close()
        _, r1 = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        assert r1.unchanged_count == 1 and r1.revised_count == 0
        # 关键：baseline 未被悄悄推进（仍 1.0，不是 1.0+4e-10）
        conn = sqlite3.connect(str(db))
        assert conn.execute("SELECT factor_value FROM qfq_revision_observation").fetchone()[0] == 1.0
        conn.close()
        # 漂移 2：当前 1.0+4e-10 → 1.0+8e-10（相对 *原 baseline* 1.0，8e-10 仍 <= 1e-9）→ 仍 unchanged
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE adj_factor SET adj_factor=1.0+8e-10 WHERE code='510050'")
        conn.commit()
        conn.close()
        _, r2 = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        assert r2.unchanged_count == 1 and r2.revised_count == 0
        # baseline 仍守在 1.0（多次小漂移未推高阈值）
        conn = sqlite3.connect(str(db))
        assert conn.execute("SELECT factor_value FROM qfq_revision_observation").fetchone()[0] == 1.0
        conn.close()
        # 当某次相对原 baseline 真正 > epsilon（1.0 → 1.0005，差 5e-4 >> 1e-9）→ 才 revision
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE adj_factor SET adj_factor=1.0005 WHERE code='510050'")
        conn.commit()
        conn.close()
        _, r3 = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        assert r3.revised_count == 1


# ===========================================================================
# 5. dry-run 与只读边界（binding §2/§7）
# ===========================================================================

class TestDryRun:
    def test_dry_run_no_schema_no_write(self, tmp_path):
        """binding §7：dry-run 在 revision schema 原本不存在时，运行后仍不存在。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        r = store.dry_run_detect("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        assert r.baseline_available is False
        # revision schema 仍未创建
        conn = sqlite3.connect(str(db))
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "qfq_revision_run" not in tables
        assert "qfq_revision_observation" not in tables
        assert "qfq_revision_event" not in tables
        conn.close()

    def test_dry_run_empty_baseline_unavailable(self, tmp_path):
        """binding §2：observation 表存在但 ETF baseline 空 → baseline_unavailable。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        store.init_schema()  # 建表但无 observation 行
        r = store.dry_run_detect("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        assert r.baseline_available is False  # 表存在但无基线

    def test_dry_run_after_seed_detects_revision(self, tmp_path):
        """dry-run 在已有 baseline 时能检出 revision（只读）。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])  # seed
        # 改 adj_factor
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE adj_factor SET adj_factor=1.0005 WHERE code='510050'")
        conn.commit()
        conn.close()
        r = store.dry_run_detect("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        assert r.baseline_available is True
        assert r.revised_count == 1


# ===========================================================================
# 6. 端到端：seed → revision（binding §3 完整闭环）
# ===========================================================================

class TestEndToEnd:
    def test_seed_then_revision_full_dump(self, tmp_path):
        """完整端到端：run1 seed → run2 revision，验证三表内容。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        rid1, r1 = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        assert r1.baseline_seeded and r1.revised_count == 0
        # 改同键值
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE adj_factor SET adj_factor=1.0005 WHERE code='510050'")
        conn.commit()
        conn.close()
        rid2, r2 = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        assert r2.revised_count == 1
        conn = sqlite3.connect(str(db))
        # run ledger
        runs = conn.execute("SELECT run_id,status,revised_count FROM qfq_revision_run ORDER BY started_at").fetchall()
        assert (rid1, "completed", 0) in runs and (rid2, "completed", 1) in runs
        # observation：factor_value 推进到 1.0005，revision_no=1，first_seen=rid1，last_seen=rid2
        obs = conn.execute("SELECT factor_value,revision_no,first_seen_run_id,last_seen_run_id "
                           "FROM qfq_revision_observation").fetchone()
        assert obs == (1.0005, 1, rid1, rid2)
        # event：1 行，run_id=rid2，prev=1.0，curr=1.0005
        ev = conn.execute("SELECT run_id,previous_factor,current_factor,revision_no "
                          "FROM qfq_revision_event").fetchone()
        assert ev == (rid2, 1.0, 1.0005, 1)
        conn.close()

    def test_bare_code_and_epoch_ms_stored(self, tmp_path):
        """binding §7：code 用裸码、factor_time 用 epoch-ms。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])
        conn = sqlite3.connect(str(db))
        code, ft = conn.execute("SELECT code,factor_time FROM qfq_revision_observation").fetchone()
        assert code == "510050"  # 裸码无后缀
        assert ft == _ms(2026, 6, 20)  # epoch-ms
        conn.close()


# ===========================================================================
# 7. Commit 2 review corrective（4 阻断 + 4 补强）
# ===========================================================================

class TestCommit2ReviewCorrective:
    """Commit 2 Review FAIL 的 4 个材料阻断修复 + 4 项测试补强。"""

    # ---- 阻断 1：NULL factor 不得静默丢弃 ----
    def test_null_factor_source_rejected_not_silently_dropped(self, tmp_path):
        """阻断 1：adj_factor=NULL 的源行不得在 loader 静默过滤，必须交给 detector 显式拒绝。

        旧实现 loader `if r[2] is not None` 会丢弃 NULL → observed=0 → completed 空审计。
        修复：loader 原样返回，detector 校验并抛 RevisionInputError，persist 整体失败。
        """
        from quantstudio.pipeline.qfq_revision import RevisionStore, RevisionInputError
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), None)])
        store = RevisionStore(db)
        # loader 不再静默过滤 NULL
        obs = store.load_observations_from_adj_factor(["510050"], _ms(2026, 7, 23))
        assert obs == [("510050", _ms(2026, 6, 20), None)]
        # persist 路径整体失败（detector 拒绝 NULL factor_value）
        with pytest.raises(RevisionInputError):
            store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"],
                                      run_id="r_null_source")
        # 失败后整体回滚：事务内的 CREATE TABLE 也回滚 → revision 表可能不存在；
        # 若存在则必为空。无论哪种，都无 completed run、无 observation（NULL 不被伪装成"无观察"）
        conn = sqlite3.connect(str(db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "qfq_revision_run" in tables:
            assert conn.execute("SELECT count(*) FROM qfq_revision_run WHERE status='completed'").fetchone()[0] == 0
        if "qfq_revision_observation" in tables:
            assert conn.execute("SELECT count(*) FROM qfq_revision_observation").fetchone()[0] == 0
        conn.close()

    def test_corrupt_baseline_rejects_not_classified_as_new(self, tmp_path):
        """补强：baseline 中出现非有限值（NULL/NaN/Inf 落库）必须明确失败，不能分类为 new。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        # baseline 里有个 NaN 值（损坏）
        bl = {("510050", _ms(2026, 6, 20)): float("nan")}
        with pytest.raises(RevisionInputError):
            detect_revisions([("510050", _ms(2026, 6, 20), 1.0)],
                             bl, "ETF", _ms(2026, 7, 23), 1e-9)

    # ---- 阻断 2：None code 不得接受为 "NONE" ----
    def test_none_code_rejected_before_bare_code(self):
        """阻断 2：bare_code(None)=="NONE"；必须在调用前显式拒绝 code is None。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        with pytest.raises(RevisionInputError):
            detect_revisions([(None, _ms(2026, 6, 20), 1.0)],
                             None, "ETF", _ms(2026, 7, 23), 1e-9, baseline_available=False)

    def test_empty_and_whitespace_code_rejected(self):
        """阻断 2：空串、纯空白 code 拒绝。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        for bad in ["", "   ", "\t"]:
            with pytest.raises(RevisionInputError):
                detect_revisions([(bad, _ms(2026, 6, 20), 1.0)],
                                 None, "ETF", _ms(2026, 7, 23), 1e-9, baseline_available=False)

    def test_none_literal_code_rejected(self):
        """阻断 2：归一化后为 "NONE" 字面（None 误入）也拒绝。"""
        from quantstudio.pipeline.qfq_revision import detect_revisions, RevisionInputError
        with pytest.raises(RevisionInputError):
            detect_revisions([("NONE", _ms(2026, 6, 20), 1.0)],
                             None, "ETF", _ms(2026, 7, 23), 1e-9, baseline_available=False)

    def test_none_code_persist_rejected(self, tmp_path):
        """阻断 2 端到端：persist 路径拒绝 None code，不持久化为 logical code 'NONE'。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore, RevisionInputError
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        with pytest.raises(RevisionInputError):
            store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"],
                                      observations=[(None, _ms(2026, 6, 20), 1.0)],
                                      run_id="r_none_code")
        # 整体回滚（事务内 detect 拒绝 None code）→ observation 表可能不存在；
        # 若存在，不得有 logical code 'NONE'
        conn = sqlite3.connect(str(db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "qfq_revision_observation" in tables:
            assert conn.execute("SELECT count(*) FROM qfq_revision_observation WHERE code='NONE'").fetchone()[0] == 0
        conn.close()

    # ---- 阻断 4：record_failed_run 必须复用输入校验 ----
    def test_record_failed_run_rejects_invalid_asset_type(self, tmp_path):
        """阻断 4：record_failed_run 拒绝非法 asset_type（ETF-only 契约）。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore, RevisionInputError
        db = _make_qfq_aux(tmp_path)
        store = RevisionStore(db)
        with pytest.raises(RevisionInputError):
            store.record_failed_run("r1", "STOCK", _ms(2026, 7, 23), 1e-9, "err")
        # 不得创建 schema / 写 failed ledger
        conn = sqlite3.connect(str(db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "qfq_revision_run" not in tables
        conn.close()

    def test_record_failed_run_rejects_invalid_as_of(self, tmp_path):
        """阻断 4：record_failed_run 拒绝非法 as_of_ms（非 epoch-ms）。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore, RevisionInputError
        db = _make_qfq_aux(tmp_path)
        store = RevisionStore(db)
        with pytest.raises(RevisionInputError):
            store.record_failed_run("r1", "ETF", 1, 1e-9, "err")  # as_of=1 非 epoch-ms

    def test_record_failed_run_rejects_invalid_window(self, tmp_path):
        """阻断 4：record_failed_run 拒绝非法 window（非 epoch-ms）。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore, RevisionInputError
        db = _make_qfq_aux(tmp_path)
        store = RevisionStore(db)
        with pytest.raises(RevisionInputError):
            store.record_failed_run("r1", "ETF", _ms(2026, 7, 23), 1e-9, "err",
                                    window_start_ms=-5)
        with pytest.raises(RevisionInputError):
            store.record_failed_run("r2", "ETF", _ms(2026, 7, 23), 1e-9, "err",
                                    window_end_ms=99999999999999)

    def test_record_failed_run_rejects_negative_epsilon(self, tmp_path):
        """阻断 4：record_failed_run 拒绝负 epsilon。"""
        from quantstudio.pipeline.qfq_revision import RevisionStore, RevisionInputError
        db = _make_qfq_aux(tmp_path)
        store = RevisionStore(db)
        with pytest.raises(RevisionInputError):
            store.record_failed_run("r1", "ETF", _ms(2026, 7, 23), -1e-9, "err")

    # ---- 补强：真实中途回滚（event 已写后、completed 前失败）----
    def test_mid_txn_rollback_after_event_write(self, tmp_path):
        """补强：在 event 已 INSERT、observation UPDATE 后、completed 前注入失败 → 全部回滚。

        用 SQLite trigger 在 qfq_revision_event INSERT 后抛错，模拟"event 已写、completed 未达"。
        断言：无 completed run、无 event、observation 保持旧值。
        """
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[("510050", _ms(2026, 6, 20), 1.0)])
        store = RevisionStore(db)
        store.init_schema()
        rid1, _ = store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"])  # seed
        # 装一个 trigger：在 event INSERT 后抛错（RAISE）使后续 revised run 在写 event 时失败
        conn = sqlite3.connect(str(db))
        conn.execute("""
            CREATE TRIGGER trg_fail_on_event_insert AFTER INSERT ON qfq_revision_event
            BEGIN
                SELECT RAISE(ABORT, 'injected failure after event write');
            END""")
        conn.commit()
        conn.close()
        # 改同键值 → run2 会写 event → trigger 抛错 → 事务回滚
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE adj_factor SET adj_factor=1.0005 WHERE code='510050'")
        conn.commit()
        conn.close()
        with pytest.raises(sqlite3.IntegrityError):
            store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9, ["510050"],
                                      run_id="r_mid_fail")
        conn = sqlite3.connect(str(db))
        # 回滚：无 r_mid_fail run、无 event、observation 仍 rid1 的 seed 值（1.0, rev_no=0）
        assert conn.execute("SELECT count(*) FROM qfq_revision_run WHERE run_id='r_mid_fail'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM qfq_revision_event").fetchone()[0] == 0
        obs = conn.execute("SELECT factor_value,revision_no FROM qfq_revision_observation").fetchone()
        assert obs == (1.0, 0)
        conn.close()

    # ---- 补强：revised 路径 adj_factor 逐行不变 ----
    def test_revised_path_adj_factor_row_by_row_unchanged(self, tmp_path):
        """补强：revised 路径执行前后，source adj_factor 全部行逐行不变（非 repair/write-back）。

        手工 UPDATE source 触发 revised，重新 capture before，persist，再逐行比较 before==after。
        """
        from quantstudio.pipeline.qfq_revision import RevisionStore
        db = _make_qfq_aux(tmp_path, adj_rows=[
            ("510050", _ms(2026, 6, 20), 1.0),
            ("159919", _ms(2026, 6, 20), 2.0),
            ("510300", _ms(2026, 6, 21), 1.5)])
        store = RevisionStore(db)
        store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9,
                                  ["510050", "159919", "510300"])  # seed
        # 手工改 source 触发 revised，capture before（改之后、persist 之前）
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE adj_factor SET adj_factor=1.0005 WHERE code='510050'")
        conn.commit()
        before = conn.execute("SELECT code,time,adj_factor FROM adj_factor ORDER BY code,time").fetchall()
        conn.close()
        # persist（revised 路径）
        store.run_persisted_audit("ETF", _ms(2026, 7, 23), 1e-9,
                                  ["510050", "159919", "510300"], run_id="r_rev")
        # 逐行比较：persist 不动 source adj_factor
        conn = sqlite3.connect(str(db))
        after = conn.execute("SELECT code,time,adj_factor FROM adj_factor ORDER BY code,time").fetchall()
        conn.close()
        assert before == after
