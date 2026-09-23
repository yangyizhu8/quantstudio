# -*- coding: utf-8 -*-
"""错误一 T3 验收：注入点修订检测（情形 B：同 (code,time) 值变化 → revision_alert outbox）。

方案：docs/error1-t3-revision-alert-outbox-design.md v1.3（情形 A 已由 factor_new 通道覆盖，不在本项）

判据：
  C1 情形 B（同 time 值变化）→ observation 新增 revision=2 + outbox 1 条 pending（outbox 0→非0）
  C2 值未变（含容差内）→ 不产生修订/告警
  C3 情形 A（新更大 time）→ 不算修订（无告警），且不干扰（本项不负责该语义）
  C4 fail-soft：检测/留痕抛错 → **快照仍写入成功**、调用不抛异常
  C5 冷启动开关关闭（QS_T3_REVISION_DETECT=0）→ 不检测、无告警
  C6 幂等：重复注入同值 → 不重复告警
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from quantstudio.pipeline.qfq_observation import ObservationStore
from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema
from quantstudio.pipeline.sources import mcp_adapter as ma

T1 = 1789488000000        # 已有 time（情形 B 用）
T2 = 1789574400000        # 更大 time（情形 A 用）
CODE = "600519"


class _StubAdapter(ma.MCPAdapter):
    """最小桩：只提供 _inject_adjfactor / _detect_and_record_revisions 所需属性。"""

    def __init__(self, aux_path):
        self.main_db = "dummy"          # 非 None 即可（真实路径不参与本用例）
        self._aux = str(aux_path)

    def _qfq_aux_path(self):
        return self._aux


@pytest.fixture()
def env(tmp_path):
    aux = tmp_path / "qfq_aux.db"
    con = sqlite3.connect(str(aux))
    init_sqlite_schema(con)
    con.execute("CREATE TABLE IF NOT EXISTS adj_factor "
                "(code TEXT, time INTEGER, adj_factor REAL, PRIMARY KEY (code, time))")
    con.commit()
    con.close()
    return _StubAdapter(aux), aux


def _conn(aux):
    c = sqlite3.connect(str(aux), timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _seed_snapshot(aux, code, t, val):
    c = _conn(aux)
    c.execute("INSERT OR REPLACE INTO adj_factor (code, time, adj_factor) VALUES (?,?,?)", (code, t, val))
    c.commit()
    c.close()


def _seed_observation(aux, code, t, val):
    """建立 observation 基线（revision=1，不产生 alert）——模拟 discovery 已观察过该键。"""
    ObservationStore(str(aux)).record_observations([("STOCK", code, t, val)], "seed-run")


def _counts(aux):
    c = _conn(aux)
    obs = c.execute("SELECT count(*) FROM qfq_factor_observation WHERE asset_type='STOCK' AND code=?",
                    (CODE,)).fetchone()[0]
    rev = c.execute("SELECT max(revision_no) FROM qfq_factor_observation WHERE asset_type='STOCK' AND code=?",
                    (CODE,)).fetchone()[0]
    alerts = c.execute("SELECT count(*) FROM qfq_factor_revision_alert").fetchone()[0]
    pending = c.execute("SELECT count(*) FROM qfq_factor_revision_alert WHERE status='pending'").fetchone()[0]
    c.close()
    return obs, rev, alerts, pending


def test_c1_same_time_change_produces_revision_and_alert(env):
    stub, aux = env
    _seed_snapshot(aux, CODE, T1, 1.0)
    _seed_observation(aux, CODE, T1, 1.0)
    assert _counts(aux)[2] == 0, "基线阶段不应有 alert"

    c = _conn(aux)
    n = stub._detect_and_record_revisions(c, "adj_factor", "STOCK",
                                          [(CODE, T1, 2.0)], "adjfactor-inject:adj_factor")
    c.commit()
    c.close()

    obs, rev, alerts, pending = _counts(aux)
    assert n == 1, f"应检出 1 条修订，实际 {n}"
    assert rev == 2, f"observation 最新 revision 应为 2，实际 {rev}"
    assert alerts == 1 and pending == 1, f"outbox 应新增 1 条 pending（0→非0），实际 alerts={alerts} pending={pending}"


def test_c2_unchanged_value_no_revision(env):
    stub, aux = env
    _seed_snapshot(aux, CODE, T1, 1.0)
    _seed_observation(aux, CODE, T1, 1.0)
    c = _conn(aux)
    n = stub._detect_and_record_revisions(c, "adj_factor", "STOCK", [(CODE, T1, 1.0)], "r")
    c.commit()
    c.close()
    assert n == 0
    assert _counts(aux)[3] == 0, "值未变不应产生 alert"


def test_c3_new_time_is_not_revision(env):
    """情形 A（新更大 time）：本项不负责（由既有 factor_new 通道覆盖）→ 不得产 alert。"""
    stub, aux = env
    _seed_snapshot(aux, CODE, T1, 1.0)
    _seed_observation(aux, CODE, T1, 1.0)
    c = _conn(aux)
    n = stub._detect_and_record_revisions(c, "adj_factor", "STOCK", [(CODE, T2, 2.0)], "r")
    c.commit()
    c.close()
    assert n == 0
    assert _counts(aux)[3] == 0, "情形 A 不应产生 revision alert（避免与 factor_new 双通道）"


def test_c4_fail_soft_snapshot_still_written(env, monkeypatch):
    """检测/留痕失败 → 快照仍写入成功、调用不抛异常（fail-soft）。"""
    stub, aux = env
    monkeypatch.setattr(
        ma, "normalize_mcp_adj_factor_df",
        lambda df, freq, asset_type: pd.DataFrame({"code": [CODE], "time": [T1], "adj_factor": [9.9]}))

    def _boom(*a, **k):
        raise RuntimeError("模拟留痕失败")

    monkeypatch.setattr(ma.MCPAdapter, "_detect_and_record_revisions", _boom)

    written = stub._inject_adjfactor(pd.DataFrame({"x": [1]}), "daily", "stock_daily")
    assert written == 1, "快照写入不应因留痕失败而中断"
    c = _conn(aux)
    got = c.execute("SELECT adj_factor FROM adj_factor WHERE code=? AND time=?", (CODE, T1)).fetchone()
    c.close()
    assert got is not None and abs(got[0] - 9.9) < 1e-9, "快照应已写入新值"


def test_c5_switch_off_disables_detection(env, monkeypatch):
    stub, aux = env
    _seed_snapshot(aux, CODE, T1, 1.0)
    _seed_observation(aux, CODE, T1, 1.0)
    monkeypatch.setenv("QS_T3_REVISION_DETECT", "0")
    c = _conn(aux)
    n = stub._detect_and_record_revisions(c, "adj_factor", "STOCK", [(CODE, T1, 2.0)], "r")
    c.commit()
    c.close()
    assert n == 0 and _counts(aux)[3] == 0, "开关关闭时不得检测/告警"


def test_c6_idempotent_repeat(env):
    stub, aux = env
    _seed_snapshot(aux, CODE, T1, 1.0)
    _seed_observation(aux, CODE, T1, 1.0)
    for _ in range(2):
        c = _conn(aux)
        stub._detect_and_record_revisions(c, "adj_factor", "STOCK", [(CODE, T1, 2.0)], "r")
        c.commit()
        c.close()
        _seed_snapshot(aux, CODE, T1, 2.0)      # 模拟随后的 INSERT OR REPLACE 生效
    obs, rev, alerts, pending = _counts(aux)
    assert rev == 2 and alerts == 1, f"重复注入同值不应重复告警（rev={rev} alerts={alerts}）"
