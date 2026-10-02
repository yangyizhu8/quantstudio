"""A 项门禁连续性判据（S-3a/S-3b）单元测试 —— V1。

依据：agent_workspace/csi300_slow_kd_reversal_r0/FIX_RECOMMENDATION.md §2（A 节）
轮次：六步③ 实施 —— V1 单测先行（红态）

覆盖：
  T1 纯函数 _snapshot_gap_intervals：连续序列 / 单超阈空档 / 阈值边界(=th 不算) / 自定义阈值
  T2 纯函数 _coverage_gaps：多指数映射，仅返回有超阈空档的指数
  T3 集成（合成库 + 真实 provider）：中段空洞序列 → index_constituents_pit 与
     index_constituents_history_coverage **均非 READY**；连续序列 → 均 READY
     且证据串含空档区间（起止 + 天数）
  T4 通用性：非 000300 指数（用 000905）同样生效
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

SPEC = importlib.util.spec_from_file_location(
    "inspect_capabilities_agate",
    Path(__file__).resolve().parent.parent
    / "skills" / "quantstudio-strategy-compiler" / "scripts" / "inspect_capabilities.py")
ic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ic)

DAY_MS = 86_400_000
T0 = 1_700_000_000_000          # 固定基日（epoch ms），避免依赖当天时间


# --------------------------------------------------------------------------
# 合成库
# --------------------------------------------------------------------------
def _make_db(path: Path, index_code: str, snaps_ms, codes_by_snap):
    """按 writers.py DDL 建最小合成库：index_constituents + ..._snapshot_meta。"""
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE index_constituents (
        index_code VARCHAR, code VARCHAR, time BIGINT, weight DOUBLE,
        PRIMARY KEY(index_code, code, time))""")
    con.execute("""CREATE TABLE index_constituents_snapshot_meta (
        index_code VARCHAR, time BIGINT,
        n_constituents INTEGER, expected_count INTEGER, status VARCHAR,
        n_duplicate_codes INTEGER, n_negative_weights INTEGER,
        n_blank_codes INTEGER, update_time VARCHAR, data_source VARCHAR,
        PRIMARY KEY(index_code, time))""")
    for t in snaps_ms:
        codes = codes_by_snap(t)
        for c in codes:
            con.execute("INSERT INTO index_constituents VALUES (?,?,?,?)",
                        [index_code, c, t, 1.0])
        con.execute("INSERT INTO index_constituents_snapshot_meta VALUES "
                    "(?,?,?,?,'complete',0,0,0,'2026-01-01','test')",
                    [index_code, t, len(codes), len(codes)])
    con.close()


def _caps_by_name(report):
    return {c["capability"]: c for c in report["capabilities"]}


def _run(tmp_path, index_code, snaps_ms, codes_by_snap, tag):
    db = tmp_path / f"{tag}.duckdb"
    _make_db(db, index_code, snaps_ms, codes_by_snap)
    return _caps_by_name(ic.inspect(db, "daily-bar-v1", f"a_gate_{tag}",
                                    out_dir=tmp_path / f"out_{tag}"))


# --------------------------------------------------------------------------
# T1 纯函数：间隔检测
# --------------------------------------------------------------------------
def test_gap_intervals_contiguous():
    """连续（每日一步）→ 无空档。"""
    snaps = [T0 + i * DAY_MS for i in range(10)]
    assert ic._snapshot_gap_intervals(snaps, 45) == []


def test_gap_intervals_single_mid_gap():
    """中段单空洞 200 天 → 恰 1 个区间，起止与天数正确。"""
    snaps = [T0, T0 + 200 * DAY_MS, T0 + 400 * DAY_MS]
    gaps = ic._snapshot_gap_intervals(snaps, 45)
    assert len(gaps) == 2, gaps                      # 两个相邻对都超阈
    start, end, days = gaps[0]
    assert days == 200.0
    assert (start, end) == ("2023-11-14", "2024-06-01") or days == 200.0


def test_gap_intervals_threshold_boundary():
    """恰等于阈值不算超阈（> th 判据）。"""
    snaps = [T0, T0 + 45 * DAY_MS]
    assert ic._snapshot_gap_intervals(snaps, 45) == []
    assert len(ic._snapshot_gap_intervals(snaps, 44)) == 1


def test_gap_intervals_custom_threshold():
    snaps = [T0, T0 + 30 * DAY_MS]
    assert ic._snapshot_gap_intervals(snaps, 45) == []
    assert len(ic._snapshot_gap_intervals(snaps, 10)) == 1


# --------------------------------------------------------------------------
# T2 纯函数：覆盖空档映射（仅返回有问题指数）
# --------------------------------------------------------------------------
def test_coverage_gaps_only_offending_indices():
    by_index = {
        "000300": [T0, T0 + 200 * DAY_MS],                      # 超阈
        "000905": [T0 + i * 5 * DAY_MS for i in range(20)],     # 连续
    }
    bad = ic._coverage_gaps(by_index, 45)
    assert set(bad) == {"000300"}
    assert bad["000300"][0][2] == 200.0


# --------------------------------------------------------------------------
# T3 集成：中段空洞 → 双 FAIL；连续 → 双 PASS
# --------------------------------------------------------------------------
def test_midgap_series_both_caps_not_ready(tmp_path):
    """中段空洞：两能力均非 READY，且证据串含空档区间（起止+天数）。"""
    snaps = [T0, T0 + 200 * DAY_MS, T0 + 400 * DAY_MS]
    sets = {snaps[0]: ["000001", "000002"],
            snaps[1]: ["000001", "000003"],
            snaps[2]: ["000001", "000004"]}
    caps = _run(tmp_path, "000300", snaps, lambda t: sets[t], "midgap")
    pit = caps["index_constituents_pit"]
    cov = caps["index_constituents_history_coverage"]
    assert pit["execution_status"] != "READY", pit.get("message")
    assert cov["execution_status"] != "READY", cov.get("message")
    ev = " ".join(pit["evidence"] + cov["evidence"])
    assert "200.0" in ev or "200" in ev, ev        # 天数
    assert "gap" in ev.lower(), ev                 # 空档标记


def test_contiguous_series_both_caps_ready(tmp_path):
    """连续覆盖 → 两能力均 READY（V4：非一刀切 FAIL）。"""
    snaps = [T0 + i * 10 * DAY_MS for i in range(41)]      # 400 天，每 10 天一个
    def codes(t):
        return ["000001", "000002"] if t == snaps[0] else ["000001", "000003"]
    caps = _run(tmp_path, "000300", snaps, codes, "contig")
    assert caps["index_constituents_pit"]["execution_status"] == "READY", \
        caps["index_constituents_pit"].get("message")
    assert caps["index_constituents_history_coverage"]["execution_status"] == "READY", \
        caps["index_constituents_history_coverage"].get("message")


def test_non_000300_index_also_enforced(tmp_path):
    """通用性：非 000300 指数（000905）同样被判定。"""
    snaps = [T0, T0 + 300 * DAY_MS]
    sets = {snaps[0]: ["000001", "000002"], snaps[1]: ["000001", "000003"]}
    caps = _run(tmp_path, "000905", snaps, lambda t: sets[t], "n300")
    assert caps["index_constituents_history_coverage"]["execution_status"] != "READY"
