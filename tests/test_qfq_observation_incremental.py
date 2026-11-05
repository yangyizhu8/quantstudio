"""方案③ 增量路径 G1–G4 机检（隔离数据根：合成 aux SQLite，绝不触碰生产库）。

纪律依据：记忆 a37d3002（跑含 GUI/daemon 的 pytest 前必须先隔离数据根）。
本文件所有用例均在 tmp_path 下建合成 aux 库 ⇒ 生产 `data/qfq_aux.db` 零触碰。

判据：
- G1 批量取 last ≡ 旧路径逐条取（逐行相等）
- G2 只写变化行（新增/修订行内容与旧路径逐位相同）
- G3 未变行批量刷新（last_seen 更新、revision_no 不动、行数不变）
- G4 alert outbox 等价（行集逐位相同，含 alert_id）
- G8 回退开关（=0 ⇒ 旧路径；=1 ⇒ 新路径；两者表内容相同）
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from quantstudio.pipeline.qfq_observation import ObservationStore


def _mk_aux(tmp: Path) -> Path:
    """合成 aux 库（表结构与生产一致：由 init_sqlite_schema 建）。"""
    from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema
    db = tmp / "qfq_aux.db"
    con = sqlite3.connect(str(db))
    init_sqlite_schema(con)
    con.commit()
    con.close()
    return db


def _dump(db: Path) -> dict:
    """把观测表 + alert 表内容抽成可比较的 dict（逐行）。"""
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    obs = [dict(r) for r in con.execute(
        "SELECT asset_type, code, factor_time, factor_value, revision_no "
        "FROM qfq_factor_observation "
        "ORDER BY asset_type, code, factor_time, revision_no").fetchall()]
    alerts = [dict(r) for r in con.execute(
        "SELECT alert_id, asset_type, code, factor_time, revision_no, status "
        "FROM qfq_factor_revision_alert "
        "ORDER BY alert_id").fetchall()]
    con.close()
    return {"obs": obs, "alerts": alerts}


def _obs(at, cd, ft, fv):
    return (at, cd, ft, fv)


# 合法 factor_time（epoch-ms，2000~2100 区间内；2020-09-13）
FT = 1_600_000_000_000


# ------------------------------------------------------------------ G1
def test_g1_batch_last_equals_rowwise(tmp_path, monkeypatch):
    """G1：批量取 last 的结果 ≡ 旧路径逐条 ORDER BY revision_no DESC LIMIT 1。"""
    monkeypatch.setenv("QS_QFQ_OBS_INCREMENTAL", "0")
    db = _mk_aux(tmp_path)
    store = ObservationStore(aux_db=db)
    # 造多 revision 场景
    store.record_observations([_obs("STOCK", "000001", FT, 1.0)], "run0")
    store.record_observations([_obs("STOCK", "000001", FT, 1.5)], "run1")
    store.record_observations([_obs("STOCK", "000002", FT, 2.0)], "run1")

    con = sqlite3.connect(str(db))
    codes = ["000001", "000002"]
    last_map = store._inc_fetch_last(con, "STOCK", codes)
    con.close()
    # 逐条取（基准）
    con = sqlite3.connect(str(db))
    for cd in codes:
        row = con.execute(
            "SELECT factor_value, revision_no FROM qfq_factor_observation "
            "WHERE asset_type=? AND code=? AND factor_time=? "
            "ORDER BY revision_no DESC LIMIT 1", ["STOCK", cd, FT]).fetchone()
        assert last_map[(cd, FT)] == (row[0], row[1]), cd
    con.close()


def test_g1_negative_wrong_asset_type(tmp_path, monkeypatch):
    """G1 负向：错配 asset_type ⇒ 批量取与逐条取不相等。"""
    monkeypatch.setenv("QS_QFQ_OBS_INCREMENTAL", "0")
    db = _mk_aux(tmp_path)
    store = ObservationStore(aux_db=db)
    store.record_observations([_obs("STOCK", "000001", FT, 1.0)], "run0")
    store.record_observations([_obs("ETF", "000001", FT, 9.0)], "run0")
    con = sqlite3.connect(str(db))
    batch = store._inc_fetch_last(con, "STOCK", ["000001"])
    con.close()
    assert batch[("000001", FT)][0] == 1.0, "错配 asset_type 时不得取到 ETF 的值"


# ------------------------------------------------------------------ G2
def test_g2_new_and_revised_rows_identical(tmp_path, monkeypatch):
    """G2：新路径的新增/修订行与旧路径逐位相同。"""
    obs = [_obs("STOCK", "000001", FT, 1.0), _obs("STOCK", "000002", FT, 2.0)]
    # 旧路径
    monkeypatch.setenv("QS_QFQ_OBS_INCREMENTAL", "0")
    (tmp_path / "old").mkdir(exist_ok=True)
    db_old = _mk_aux(tmp_path / "old")
    s_old = ObservationStore(aux_db=db_old)
    s_old.record_observations(obs, "r1")
    s_old.record_observations([_obs("STOCK", "000001", FT, 1.2)], "r2")
    # 新路径
    monkeypatch.setenv("QS_QFQ_OBS_INCREMENTAL", "1")
    (tmp_path / "new").mkdir(exist_ok=True)
    db_new = _mk_aux(tmp_path / "new")
    s_new = ObservationStore(aux_db=db_new)
    s_new.record_observations(obs, "r1")
    s_new.record_observations([_obs("STOCK", "000001", FT, 1.2)], "r2")
    d_old, d_new = _dump(db_old), _dump(db_new)
    assert d_old["obs"] == d_new["obs"], "观测表内容必须逐位相同"
    assert d_old["alerts"] == d_new["alerts"], "alert 行集必须逐位相同"


def test_g2_epsilon_boundary_same_classification(tmp_path, monkeypatch):
    """G2 负向对照：epsilon 边界上的分类，旧路径与新路径**必须一致**。

    设计：旧/新各自独立库、同一输入序列 ⇒ 比较 (unchanged, revised) 分类。
    （不预设哪一侧判「未变」——边界判定方向由产品容差定义决定，
      本用例只管「两条路径分类相同」。）
    """
    seq = [[_obs("STOCK", "000001", FT, 1.0)],
           [_obs("STOCK", "000001", FT, 1.01)]]
    monkeypatch.setenv("QS_QFQ_OBS_INCREMENTAL", "0")
    (tmp_path / "old").mkdir(exist_ok=True)
    db_old = _mk_aux(tmp_path / "old")
    s_old = ObservationStore(aux_db=db_old)
    cls_old = []
    for i, batch in enumerate(seq):
        r = s_old.record_observations(batch, f"r{i}")
        cls_old.append((r.unchanged_count, r.revised_count))
    monkeypatch.setenv("QS_QFQ_OBS_INCREMENTAL", "1")
    (tmp_path / "new").mkdir(exist_ok=True)
    db_new = _mk_aux(tmp_path / "new")
    s_new = ObservationStore(aux_db=db_new)
    cls_new = []
    for i, batch in enumerate(seq):
        r = s_new.record_observations(batch, f"r{i}")
        cls_new.append((r.unchanged_count, r.revised_count))
    assert cls_old == cls_new, f"边界分类必须一致：old={cls_old} new={cls_new}"


# ------------------------------------------------------------------ G3
def test_g3_unchanged_rows_batch_touch(tmp_path, monkeypatch):
    """G3：未变行批量刷新 last_seen，revision_no 与行数不动。"""
    monkeypatch.setenv("QS_QFQ_OBS_INCREMENTAL", "1")
    db = _mk_aux(tmp_path)
    store = ObservationStore(aux_db=db)
    store.record_observations([_obs("STOCK", "000001", FT, 1.0)], "r1")
    before = _dump(db)["obs"]
    r = store.record_observations([_obs("STOCK", "000001", FT, 1.0)], "r2")
    assert r.unchanged_count == 1
    con = sqlite3.connect(str(db))
    row = con.execute(
        "SELECT last_seen_run_id, revision_no, factor_value FROM qfq_factor_observation "
        "WHERE asset_type=? AND code=? AND factor_time=?",
        ["STOCK", "000001", FT]).fetchone()
    con.close()
    assert row[0] == "r2", "last_seen_run_id 必须更新为本轮 run_id"
    assert row[1] == 1 and row[2] == 1.0, "revision_no / factor_value 不得变"
    assert len(_dump(db)["obs"]) == len(before), "行数不得变"


# ------------------------------------------------------------------ G4
def test_g4_alert_outbox_identical(tmp_path, monkeypatch):
    """G4：修订产生的 alert 行集与旧路径逐位相同。"""
    monkeypatch.setenv("QS_QFQ_OBS_INCREMENTAL", "0")
    (tmp_path / "old").mkdir(exist_ok=True)
    db_old = _mk_aux(tmp_path / "old")
    s_old = ObservationStore(aux_db=db_old)
    s_old.record_observations([_obs("STOCK", "000001", FT, 1.0)], "r1")
    s_old.record_observations([_obs("STOCK", "000001", FT, 2.0)], "r2")
    monkeypatch.setenv("QS_QFQ_OBS_INCREMENTAL", "1")
    (tmp_path / "new").mkdir(exist_ok=True)
    db_new = _mk_aux(tmp_path / "new")
    s_new = ObservationStore(aux_db=db_new)
    s_new.record_observations([_obs("STOCK", "000001", FT, 1.0)], "r1")
    s_new.record_observations([_obs("STOCK", "000001", FT, 2.0)], "r2")
    a_old = _dump(db_old)["alerts"]
    a_new = _dump(db_new)["alerts"]
    assert a_old == a_new and a_old, "alert 行集（含 alert_id/status）必须逐位相同且非空"


# ------------------------------------------------------------------ G8
def test_g8_switch_back_to_legacy_path(tmp_path, monkeypatch):
    """G8：关闭开关 ⇒ 走旧路径（行为与开关前一致）。"""
    monkeypatch.setenv("QS_QFQ_OBS_INCREMENTAL", "0")
    db = _mk_aux(tmp_path)
    store = ObservationStore(aux_db=db)
    store.record_observations([_obs("STOCK", "000001", FT, 1.0)], "r1")
    r = store.record_observations([_obs("STOCK", "000001", FT, 1.0)], "r2")
    assert r.unchanged_count == 1
    assert not getattr(store, "_inc_counters", {}), "旧路径不应产生增量计数器"


# ------------------------------------------------------------------ 计数可机检（G10 前置）
def test_inc_counters_machine_readable(tmp_path, monkeypatch):
    """G10 前置：增量路径的往返计数器可机检（chunks/fetch_rows/touched）。"""
    monkeypatch.setenv("QS_QFQ_OBS_INCREMENTAL", "1")
    db = _mk_aux(tmp_path)
    store = ObservationStore(aux_db=db)
    store.record_observations(
        [_obs("STOCK", f"{i:06d}", FT, 1.0) for i in range(50)], "r1")
    store.record_observations(
        [_obs("STOCK", f"{i:06d}", FT, 1.0) for i in range(50)], "r2")
    c = store._inc_counters
    assert c["chunks"] >= 1 and c["fetch_rows"] >= 50 and c["touched"] == 50
    assert c["inserted"] == 0 and c["alerts"] == 0
