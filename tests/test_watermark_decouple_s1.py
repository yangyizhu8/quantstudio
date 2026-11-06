# -*- coding: utf-8 -*-
"""S1（四案①·2026-11-01）：写入自锁环修复——失败分类 + 水位推进解耦 + poison 有界。

判据对齐方案 docs/design-write-selflock-and-a4-repair-20261101.md §2-S1/§2.2、§4：
  分类（V 系硬门）：own（三条件 AND + peers_n==0）/ foreign（scope 不符、键窗外、
    peers_n>0）/ unknown（信息不足 ⇒ 保守按 foreign 处置，不推进）；
  V1 自锁环可解：own + 行级校验过 ⇒ write 吞冲突解耦返回（上游正常推进水位）；
  V2 水位不虚进：校验不过（码数超差 / 中段整日缺失 / 样本不足）⇒ 照旧抛出不推进；
  V3 poison 有界：连续 own 冲突且校验不过达阈 ⇒ error 告警 + 窗口快速拒绝（不写
    不重试）；数据补齐后自愈清除；
  回退面：QS_WATERMARK_DECOUPLE=0 ⇒ 逐位回退改动前行为（冲突耗尽必抛、无统计、
    无 poison 文件）。
纯单元测试，不触生产库（tmp_path 真库 + 模块级失败槽）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from quantstudio.pipeline import writers as W
from quantstudio.pipeline.writers import (DuckDBWriter, WindowPoisonedError,
                                          WriteResult)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """用例独立：清失败槽 + 重置 S1 统计 + 默认开关/阈值 + 清相关 env。"""
    monkeypatch.delenv("QS_WATERMARK_DECOUPLE", raising=False)
    monkeypatch.delenv("QS_POISON_MAX", raising=False)
    monkeypatch.delenv("QS_WRITE_RETRY_MAX", raising=False)
    W._LAST_WRITE_CTX.clear()
    W._LAST_WRITE_FAILURE.clear()
    for k in list(W._WM_DECOUPLE_STATS):
        W._WM_DECOUPLE_STATS[k] = 0
    yield
    W._LAST_WRITE_CTX.clear()
    W._LAST_WRITE_FAILURE.clear()


def _day_ms(yyyymmdd: str) -> int:
    """本地午夜毫秒（与实现 _wm_tz_off_ms 同口径：本地日界）。"""
    return int(datetime.strptime(yyyymmdd, "%Y%m%d").timestamp() * 1000)


def _conflict_exc(key="510300, 1789090200000, daily"):
    """生产实弹形态（2026-10-31 晨轮原文）：TransactionContext 提交期写写冲突。"""
    return RuntimeError(
        'TransactionContext Error: Failed to commit: write-write conflict on key: "%s"' % key)


def _mk_writer(tmp_path, monkeypatch):
    """真库 writer（tmp 库）+ 隔离 3A 文件写锁（生产 daemon 在跑，测试不得争锁）。"""
    w = DuckDBWriter({"type": "duckdb", "path": str(Path(tmp_path) / "s1.db")})
    monkeypatch.setattr(W, "ensure_write_lock", lambda *a, **k: None)
    monkeypatch.setattr(W, "release_write_lock", lambda *a, **k: None)
    return w


def _df(day_ms: int, n_codes=50, prefix="5103"):
    return pd.DataFrame({
        "code": ["%s%02d" % (prefix, i) for i in range(n_codes)],
        "time": [day_ms] * n_codes,
        "open": [1.0] * n_codes, "high": [1.0] * n_codes,
        "low": [1.0] * n_codes, "close": [1.0] * n_codes,
    })


def _seed_health(w, days, n_codes=50):
    """健康历史：每天 n_codes 只（直接走正常写路径）。"""
    for d in days:
        w.write(_df(_day_ms(d), n_codes), "etf_daily", "seed_%s" % d)


def _seed_calendar(w, days):
    """trade_calendar 种子（cal_date=yyyymmdd int，全开市）。"""
    conn = w._conn()
    try:
        for d in days:
            conn.execute(
                "INSERT INTO trade_calendar (cal_date, is_open, source) "
                "VALUES (?, TRUE, 'test') ON CONFLICT (cal_date) DO NOTHING", [int(d)])
    finally:
        conn.close()


def _arm_own_failure(batch_id: str, day_ms: int, table="etf_daily",
                     code="510300", peers_n=0):
    """预填失败槽为 own 形态（M1 字段三条件全真；peers_n 可覆盖为 >0 造 foreign）。"""
    W._LAST_WRITE_FAILURE.clear()
    W.note_write_context(table=table, batch_id=batch_id, path="_write_locked",
                         serial=False)
    W._LAST_WRITE_FAILURE.update({
        "table": table, "batch_id": batch_id, "path": "_write_locked",
        "exc_type": "RuntimeError", "pid": 0, "thread": 0,
        "conflict_key": "%s, %d, daily" % (code, day_ms),
        "conflict_time": day_ms,
        "peers": [], "peers_n": peers_n,
        "conflict_table": table,
        "conflict_scope": "table=%s batch=%s path=_write_locked "
                          "conflict_bar=%s" % (table, batch_id,
                                               datetime.fromtimestamp(
                                                   day_ms / 1000.0).strftime(
                                                   "%Y-%m-%d %H:%M:%S")),
    })


def _mock_conflict_write(monkeypatch, w, exc, counter=None):
    """mock _write_locked 抛冲突（QS_WRITE_RETRY_MAX=0 ⇒ 单次尝试即耗尽进 S1 决策）。"""
    monkeypatch.setenv("QS_WRITE_RETRY_MAX", "0")

    def _raise(df, table, batch_id, passthrough=False):
        if counter is not None:
            counter.append(batch_id)
        raise exc

    monkeypatch.setattr(w, "_write_locked", _raise)


# ── 分类纯函数：own / foreign / unknown（§2.2 判据照抄） ─────────────────────

def test_classify_own_all_conditions_met():
    """own 正向：表同 + scope 含本批 + 键落本批窗口（code∈码集 且 time∈域）+ peers_n=0。"""
    ms = _day_ms("20260911")
    df = _df(ms)
    _arm_own_failure("b_own", ms)
    assert W.classify_write_conflict_ownership(
        "etf_daily", "b_own", df) == "own"


def test_classify_foreign_table_mismatch():
    """foreign：conflict_table != 本表（scope 不符）。"""
    ms = _day_ms("20260911")
    _arm_own_failure("b1", ms)
    info = W.last_write_failure()
    info["conflict_table"] = "stock_daily"
    assert W.classify_write_conflict_ownership(
        "etf_daily", "b1", _df(ms), failure_info=info) == "foreign"


def test_classify_foreign_scope_other_batch():
    """foreign：conflict_scope 不含本 batch（他批作用域）。

    修注（2026-11-01）：原构造 _arm_own_failure("b1")+判定传 "b_other" 触发的
    是「失败槽错配 ⇒ unknown」防线（writers L684），并非 scope 分支。
    正确构造 = 失败槽与判定同批，再覆盖 conflict_scope 使其不含本批 ⇒
    走 L700-705 的 scope 条件失败 ⇒ foreign（与 L141 的 table 错配用例同模式）。
    """
    ms = _day_ms("20260911")
    _arm_own_failure("b_other", ms)
    info = W.last_write_failure()
    info["conflict_scope"] = "table=etf_daily batch=zzz"
    assert W.classify_write_conflict_ownership(
        "etf_daily", "b_other", _df(ms), failure_info=info) == "foreign"


def test_classify_foreign_key_out_of_window():
    """foreign：冲突键 time 落在本批写入窗口之外（同 code 他窗）。"""
    ms = _day_ms("20260911")
    _arm_own_failure("b1", ms)
    info = W.last_write_failure()
    info["conflict_key"] = "510300, %d, daily" % (ms + 90 * 86400000)
    assert W.classify_write_conflict_ownership(
        "etf_daily", "b1", _df(ms), failure_info=info) == "foreign"


def test_classify_foreign_peers_present():
    """foreign：peers_n>0（登记表实条目 ⇒ 他方写者可靠指任）⇒ 不推进。"""
    ms = _day_ms("20260911")
    _arm_own_failure("b1", ms, peers_n=2)
    assert W.classify_write_conflict_ownership(
        "etf_daily", "b1", _df(ms)) == "foreign"


def test_classify_unknown_missing_m1_fields():
    """unknown：M1 字段缺失（conflict_table/scope/key 任一不可得）⇒ 保守不推进。"""
    ms = _day_ms("20260911")
    _arm_own_failure("b1", ms)
    info = W.last_write_failure()
    info.pop("conflict_scope")
    assert W.classify_write_conflict_ownership(
        "etf_daily", "b1", _df(ms), failure_info=info) == "unknown"


def test_classify_unknown_slot_mismatch():
    """unknown（错配防线）：失败槽与本批 (table, batch) 不符（并发覆盖）⇒ 保守。"""
    ms = _day_ms("20260911")
    _arm_own_failure("b1", ms)
    assert W.classify_write_conflict_ownership(
        "etf_daily", "b2", _df(ms)) == "unknown"


def test_classify_unknown_no_window():
    """unknown：本批窗口不可得（df 无 time 列）⇒ 保守。"""
    ms = _day_ms("20260911")
    _arm_own_failure("b1", ms)
    assert W.classify_write_conflict_ownership(
        "etf_daily", "b1", pd.DataFrame({"code": ["510300"]})) == "unknown"


# ── V1 自锁环可解：own + 行级校验过 ⇒ 解耦返回，水位交上游正常推进 ───────────

def test_v1_own_with_full_window_decouples_and_advances(tmp_path, monkeypatch):
    """V1 主线：数据面完整（窗口日 50 码达标）⇒ 冲突耗尽被解耦吞掉，上游正常推进。"""
    w = _mk_writer(tmp_path, monkeypatch)
    days = ["202609%02d" % d for d in range(1, 11)]      # 10 个健康日 × 50 码
    _seed_calendar(w, days + ["20260911"])
    _seed_health(w, days)
    _seed_health(w, ["20260911"])                         # 窗口日数据已完整落库

    ms = _day_ms("20260911")
    exc = _conflict_exc("510300, %d, daily" % ms)
    _arm_own_failure("b_v1", ms)
    _mock_conflict_write(monkeypatch, w, exc)

    res = w.write(_df(ms), "etf_daily", "b_v1")
    assert int(res) == 50, "own+校验过 ⇒ 按本批已完整落库返回（len(df)）"
    assert isinstance(res, WriteResult) and res.new == -1 and res.updated == -1, \
        "记账不可知 -1 哨兵（R1 口径，daemon 侧既有消费路径）"
    assert W.watermark_decouple_stats()["own_decoupled"] == 1
    # 上游成功路径（daemon._advance_or_defer_watermark → advance_watermark）正常推进
    w.advance_watermark("mcp", "etf_daily", "daily", ms, "b_v1")
    assert int(w.get_last_date("mcp", "etf_daily", "daily")) == ms, \
        "V1：水位/数据面一致（解耦后推进生效，环解）"


def test_v1_foreign_still_raises_no_advance(tmp_path, monkeypatch):
    """foreign（peers_n>0）：不解耦 ⇒ 照旧抛出，水位不推进（走 S2 让行/下轮）。"""
    w = _mk_writer(tmp_path, monkeypatch)
    days = ["202609%02d" % d for d in range(1, 11)]
    _seed_calendar(w, days + ["20260911"])
    _seed_health(w, days)
    _seed_health(w, ["20260911"])

    ms = _day_ms("20260911")
    exc = _conflict_exc("510300, %d, daily" % ms)
    _arm_own_failure("b_f", ms, peers_n=2)                # foreign：他方写者
    _mock_conflict_write(monkeypatch, w, exc)

    with pytest.raises(RuntimeError, match="write-write conflict"):
        w.write(_df(ms), "etf_daily", "b_f")
    assert W.watermark_decouple_stats()["foreign"] == 1
    assert w.get_last_date("mcp", "etf_daily", "daily") is None, "不推进"


def test_v1_unknown_conservative_no_advance(tmp_path, monkeypatch):
    """unknown（失败槽空）：保守按 foreign 处置 ⇒ 照旧抛出，不推进。"""
    w = _mk_writer(tmp_path, monkeypatch)
    days = ["202609%02d" % d for d in range(1, 11)]
    _seed_health(w, days)

    ms = _day_ms("20260911")
    _mock_conflict_write(monkeypatch, w, _conflict_exc("510300, %d, daily" % ms))
    with pytest.raises(RuntimeError, match="write-write conflict"):
        w.write(_df(ms), "etf_daily", "b_u")
    assert W.watermark_decouple_stats()["unknown"] == 1


# ── V2 水位不虚进：行级校验不过 ⇒ 不推进 ───────────────────────────────────

def test_v2_window_code_count_below_baseline_blocks(tmp_path, monkeypatch):
    """V2-a：窗口日仅 3 码（基准中位数 50，±5% 下限 47.5）⇒ 校验不过 ⇒ 抛出+poison 1。"""
    w = _mk_writer(tmp_path, monkeypatch)
    days = ["202609%02d" % d for d in range(1, 11)]
    _seed_calendar(w, days + ["20260911"])
    _seed_health(w, days)

    ms = _day_ms("20260911")
    w.write(_df(ms, n_codes=3), "etf_daily", "seed_win3")   # 窗口日仅 3 码落库
    _arm_own_failure("b_v2", ms)
    _mock_conflict_write(monkeypatch, w, _conflict_exc("510300, %d, daily" % ms))

    with pytest.raises(RuntimeError, match="write-write conflict"):
        w.write(_df(ms, n_codes=3), "etf_daily", "b_v2")
    st = W.watermark_decouple_stats()
    assert st["own_validation_blocked"] == 1 and st["own_decoupled"] == 0
    state = w._wm_poison_load()
    ent = list(state.values())[0]
    assert ent["poison_count"] == 1 and not ent["poisoned_at"], \
        "首次校验不过 ⇒ 计 1 未达阈（默认阈 3）"
    assert w.get_last_date("mcp", "etf_daily", "daily") is None, "V2：不虚进"


def test_v2_missing_open_day_blocks_even_if_code_count_ok(tmp_path, monkeypatch):
    """V2-b（按日粒度收紧的靶点）：窗口 3 日中段整日缺失，窗口整体 distinct 仍达标
    ⇒ 必须判不过（防跨日窗口虚进——方案原文「（或对应粒度）」授权内的 fail-closed）。"""
    w = _mk_writer(tmp_path, monkeypatch)
    days = ["202609%02d" % d for d in range(1, 11)]
    win = ["20260911", "20260912", "20260913"]
    _seed_calendar(w, days + win)
    _seed_health(w, days)
    _seed_health(w, ["20260911", "20260913"])             # 中段 0912 整日缺失

    ms = _day_ms("20260913")
    exc = _conflict_exc("510300, %d, daily" % ms)
    _arm_own_failure("b_v2b", ms)
    _mock_conflict_write(monkeypatch, w, exc)

    # 本批 df 恰为窗口 3 日（覆盖缺失日），冲突键落窗口内
    df = pd.concat([_df(_day_ms(d)) for d in win], ignore_index=True)
    with pytest.raises(RuntimeError, match="write-write conflict"):
        w.write(df, "etf_daily", "b_v2b")
    assert W.watermark_decouple_stats()["own_validation_blocked"] == 1
    ok, detail = w._wm_decouple_validate("etf_daily",
                                         _day_ms("20260911"), _day_ms("20260913"))
    assert not ok and detail["reason"] == "missing_open_days", \
        "中段缺失开市日必须拦下（窗口整体码数达标也不放行）"


def test_v2_insufficient_history_fail_closed(tmp_path, monkeypatch):
    """V2-c：全表样本 <8 日（铁律 25 证据量下限）⇒ 校验不过（fail-closed）。"""
    w = _mk_writer(tmp_path, monkeypatch)
    _seed_health(w, ["20260901", "20260902", "20260903"])  # 仅 3 日样本
    ok, detail = w._wm_decouple_validate("etf_daily",
                                         _day_ms("20260903"), _day_ms("20260903"))
    assert not ok and detail["reason"] == "insufficient_history"


def test_v2_zero_virtual_advance_across_cases(tmp_path, monkeypatch):
    """V2 汇总：上述全部「不完整」形态下推进水位 = 0 例（硬门判据）。"""
    w = _mk_writer(tmp_path, monkeypatch)
    days = ["202609%02d" % d for d in range(1, 11)]
    _seed_calendar(w, days + ["20260911"])
    _seed_health(w, days)
    ms = _day_ms("20260911")
    for i in range(3):
        _arm_own_failure("b_z%d" % i, ms)
        _mock_conflict_write(monkeypatch, w, _conflict_exc("510300, %d, daily" % ms))
        with pytest.raises(RuntimeError):
            w.write(_df(ms, n_codes=3), "etf_daily", "b_z%d" % i)
    assert w.get_last_date("mcp", "etf_daily", "daily") is None, \
        "三次不完整均未推进 ⇒ 虚进 0 例"


# ── V3 poison 有界：达阈 ⇒ 告警 + 快速拒绝；数据补齐 ⇒ 自愈 ─────────────────

def test_v3_poison_threshold_alarm_and_fast_reject(tmp_path, monkeypatch, caplog):
    """V3：连续 3 次 own 冲突且校验不过达阈 ⇒ error 告警；第 4 次入口即拒（不写不重试）。"""
    w = _mk_writer(tmp_path, monkeypatch)
    monkeypatch.setenv("QS_POISON_MAX", "3")
    days = ["202609%02d" % d for d in range(1, 11)]
    _seed_calendar(w, days + ["20260911"])
    _seed_health(w, days)                                 # 窗口日始终无数据 ⇒ 校验不过

    ms = _day_ms("20260911")
    calls = []
    with caplog.at_level(logging.ERROR, logger="quantstudio.pipeline.writers"):
        for i in range(3):
            _arm_own_failure("b_p%d" % i, ms)
            _mock_conflict_write(monkeypatch, w,
                                 _conflict_exc("510300, %d, daily" % ms),
                                 counter=calls)
            with pytest.raises(RuntimeError, match="write-write conflict"):
                w.write(_df(ms, n_codes=3), "etf_daily", "b_p%d" % i)
        state = w._wm_poison_load()
        ent = list(state.values())[0]
        assert ent["poison_count"] == 3 and ent["poisoned_at"], "达阈 ⇒ poisoned"
        # 第 4 次：入口 poison 门快速拒绝（_write_locked 零调用 ⇒ 不写不冲突不重试）
        with pytest.raises(WindowPoisonedError):
            w.write(_df(ms, n_codes=3), "etf_daily", "b_p3")
    assert calls[-1] == "b_p2", "第 4 次未触达 _write_locked（快速拒绝）"
    assert W.watermark_decouple_stats()["poison_rejected"] == 1
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "转 poison" in joined, "达阈转 poison 必须 error 告警（不静默）"


def test_v3_poison_self_heals_after_backfill(tmp_path, monkeypatch):
    """V3 自愈：poison 后窗口数据补齐（离线回填/人工补拉形态）⇒ 门校验过即清除放行。"""
    w = _mk_writer(tmp_path, monkeypatch)
    monkeypatch.setenv("QS_POISON_MAX", "3")
    days = ["202609%02d" % d for d in range(1, 11)]
    _seed_calendar(w, days + ["20260911"])
    _seed_health(w, days)

    ms = _day_ms("20260911")
    for i in range(3):
        _arm_own_failure("b_h%d" % i, ms)
        _mock_conflict_write(monkeypatch, w, _conflict_exc("510300, %d, daily" % ms))
        with pytest.raises(RuntimeError):
            w.write(_df(ms, n_codes=3), "etf_daily", "b_h%d" % i)
    assert list(w._wm_poison_load().values())[0]["poisoned_at"], "先达阈"

    # 数据补齐 = 离线回填形态（直连 SQL：poison 门拦 write() 路径是设计内——
    # 门的存在就是防 daemon 无限重拉同窗；人工路径 = 直连回填或删库旁 json 键）
    conn = w._conn()
    try:
        conn.executemany(
            "INSERT INTO etf_daily (code, time, open, high, low, close) "
            "VALUES (?, ?, 1.0, 1.0, 1.0, 1.0)",
            [["5103%02d" % i, ms] for i in range(50)])
    finally:
        conn.close()
    # 再写：门重校验过 ⇒ 自愈清除放行 ⇒ 冲突耗尽 ⇒ own+校验过 ⇒ 解耦返回（重试后成功）
    _arm_own_failure("b_h3", ms)
    _mock_conflict_write(monkeypatch, w, _conflict_exc("510300, %d, daily" % ms))
    res = w.write(_df(ms), "etf_daily", "b_h3")
    assert int(res) == 50, "补齐后放行且解耦成功（V1「重试后成功」分支）"
    st = W.watermark_decouple_stats()
    assert st["poison_cleared"] >= 1 and st["own_decoupled"] == 1
    assert w._wm_poison_load() == {}, "poison 条目已清除"


def test_v3_poison_count_readable_for_s4(tmp_path, monkeypatch):
    """S4 读数接口：watermark_decouple_stats() 含 poison 口径字段；文件键含 poison_count。"""
    w = _mk_writer(tmp_path, monkeypatch)
    days = ["202609%02d" % d for d in range(1, 11)]
    _seed_calendar(w, days + ["20260911"])
    _seed_health(w, days)
    ms = _day_ms("20260911")
    _arm_own_failure("b_s4", ms)
    _mock_conflict_write(monkeypatch, w, _conflict_exc("510300, %d, daily" % ms))
    with pytest.raises(RuntimeError):
        w.write(_df(ms, n_codes=3), "etf_daily", "b_s4")
    st = W.watermark_decouple_stats()
    for k in ("own_decoupled", "own_validation_blocked", "foreign", "unknown",
              "poison_rejected", "poison_cleared"):
        assert k in st, "S4 依赖字段 %s 必须可读" % k
    state = w._wm_poison_load()
    ent = list(state.values())[0]
    assert ent["poison_count"] == 1 and "poison_count" in json.dumps(state)


# ── 回退面：QS_WATERMARK_DECOUPLE=0 逐位回退 ───────────────────────────────

def test_switch_off_bitwise_rollback(tmp_path, monkeypatch):
    """开关=0：同 V1 场景（数据完整）⇒ 冲突耗尽照旧抛出；无统计、无 poison 文件。"""
    monkeypatch.setenv("QS_WATERMARK_DECOUPLE", "0")
    w = _mk_writer(tmp_path, monkeypatch)
    days = ["202609%02d" % d for d in range(1, 11)]
    _seed_calendar(w, days + ["20260911"])
    _seed_health(w, days)
    _seed_health(w, ["20260911"])

    ms = _day_ms("20260911")
    exc = _conflict_exc("510300, %d, daily" % ms)
    _arm_own_failure("b_off", ms)
    _mock_conflict_write(monkeypatch, w, exc)

    with pytest.raises(RuntimeError, match="write-write conflict"):
        w.write(_df(ms), "etf_daily", "b_off")
    assert W.watermark_decouple_stats() == {k: 0 for k in W._WM_DECOUPLE_STATS}, \
        "开关关 ⇒ S1 统计零变化"
    assert not w._wm_poison_path().exists(), "开关关 ⇒ 不产生 poison 文件"


def test_switch_off_blocked_case_unchanged(tmp_path, monkeypatch):
    """开关=0：校验不过场景同样照旧抛出（不推进逻辑恢复原样，毒窗不拦）。"""
    monkeypatch.setenv("QS_WATERMARK_DECOUPLE", "0")
    w = _mk_writer(tmp_path, monkeypatch)
    days = ["202609%02d" % d for d in range(1, 11)]
    _seed_health(w, days)
    ms = _day_ms("20260911")
    _arm_own_failure("b_off2", ms)
    _mock_conflict_write(monkeypatch, w, _conflict_exc("510300, %d, daily" % ms))
    with pytest.raises(RuntimeError, match="write-write conflict"):
        w.write(_df(ms, n_codes=3), "etf_daily", "b_off2")
    assert W.watermark_decouple_stats()["own_validation_blocked"] == 0


# ── A′（2026-11-05）：S1 健康基准近期窗口中位 —— docs/design-s1-baseline- ──
#    recent-median-20261105.md（V1 增长型可过 / V2 负向三样本 / V3 稳定码数
#    逐位等价 / V6 QS_WM_BASELINE_DAYS=0 逐位回退阀）。

def _date_range(start_yyyymmdd: str, n: int):
    """start 起连续 n 个自然日（数据日不依赖日历——开市日由 trade_calendar 决定）。"""
    from datetime import timedelta
    d0 = datetime.strptime(start_yyyymmdd, "%Y%m%d")
    return [(d0 + timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def _seed_days_sql(w, days, n_codes=100, prefix="5100", table="etf_daily"):
    """直连批量种历史（离线回填形态，与 test_v3_poison_self_heals_after_backfill
    同通道；A′ 用例需百日级样本，走 write 路径慢一个量级且绕开被测面）。"""
    conn = w._conn()
    try:
        rows = []
        for d in days:
            ms = _day_ms(d)
            rows += [["%s%02d" % (prefix, i), ms, 1.0, 1.0, 1.0, 1.0]
                     for i in range(n_codes)]
        conn.executemany(
            "INSERT INTO %s (code, time, open, high, low, close) "
            "VALUES (?, ?, ?, ?, ?, ?)" % table, rows)
    finally:
        conn.close()


def _seed_minutes_sql(w, days, n_codes=50):
    """stock_minutes 种子（PK 含 freq，列集与 _seed_days_sql 适配差异仅此）。"""
    conn = w._conn()
    try:
        rows = []
        for d in days:
            ms = _day_ms(d)
            rows += [["%s%02d" % ("6000", i), ms, "1m", 1.0, 1.0, 1.0, 1.0]
                     for i in range(n_codes)]
        conn.executemany(
            "INSERT INTO stock_minutes (code, time, freq, open, high, low, close) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    finally:
        conn.close()


def _growth_table(w):
    """码数增长型样本（ETF 家族缺陷靶型）：70 个低码日×100 + 68 个近期高码日×200。
    全表中位=100.0（138 日）；近期 60 日中位=200.0；近期 30 日中位=200.0。"""
    old_days = _date_range("20260601", 70)
    rec_days = _date_range("20260810", 68)     # 末日至 2026-10-16（含 ≥60 近期日）
    _seed_days_sql(w, old_days, n_codes=100, prefix="5100")
    _seed_days_sql(w, rec_days, n_codes=200, prefix="5101")
    return old_days, rec_days


def test_a1_growth_table_recent_baseline_passes(tmp_path, monkeypatch):
    """A′ V1：2026 窗口（2026-09-07~10-16）近期基准下 ok=True；同数据同窗口在
    全表旧行为（env=0）下必拒——正是生产缺陷（全表 1,060 vs 窗口 2,137）的靶型。"""
    monkeypatch.delenv("QS_WM_BASELINE_DAYS", raising=False)
    w = _mk_writer(tmp_path, monkeypatch)
    old_days, rec_days = _growth_table(w)
    _seed_calendar(w, rec_days)                # 近期日全开市（含窗口日）
    t_lo, t_hi = _day_ms("20260907"), _day_ms("20261016")
    ok, detail = w._wm_decouple_validate("etf_daily", t_lo, t_hi)
    assert ok and detail["reason"] == "ok"
    assert detail["median"] == 200.0, "近期 60 日中位（A′ 基准）"
    # 对照：回退阀=0（全表旧行为）同窗口必拒（缺陷态复现，非估算——同库实测）
    monkeypatch.setenv("QS_WM_BASELINE_DAYS", "0")
    ok0, detail0 = w._wm_decouple_validate("etf_daily", t_lo, t_hi)
    assert not ok0 and detail0["reason"] == "code_count_out_of_tolerance"
    assert detail0["median"] == 100.0, "全表中位（旧口径）恒低于当前窗口 ⇒ 恒拒"


def test_a2_negative_full_day_missing_blocks(tmp_path, monkeypatch):
    """A′ V2-①：缺整日（日历可得）⇒ ok=False reason=missing_open_days
    （码数达标也拦——近期基准放宽后负向硬门仍兜底）。"""
    monkeypatch.delenv("QS_WM_BASELINE_DAYS", raising=False)
    w = _mk_writer(tmp_path, monkeypatch)
    days = _date_range("20260901", 12)         # 12 个健康日（≥8 近期样本）
    miss = days[8]
    _seed_days_sql(w, [d for d in days if d != miss], n_codes=100)
    _seed_calendar(w, days)
    ok, detail = w._wm_decouple_validate(
        "etf_daily", _day_ms(days[0]), _day_ms(days[-1]))
    assert not ok and detail["reason"] == "missing_open_days"
    assert detail["missing_days"], "缺失日须列出"


def test_a2_negative_half_code_count_blocks_with_log(tmp_path, monkeypatch, caplog):
    """A′ V2-②：某日码数仅 50%（100→50 超容差）⇒ ok=False reason=
    code_count_out_of_tolerance；V5 四要素日志行必须可见（median/tolerance_band/
    bad_days/bad_days_total/window）。"""
    monkeypatch.delenv("QS_WM_BASELINE_DAYS", raising=False)
    w = _mk_writer(tmp_path, monkeypatch)
    days = _date_range("20260901", 12)
    _seed_days_sql(w, days[:-1], n_codes=100)
    _seed_days_sql(w, days[-1:], n_codes=50)   # 窗口末日仅 50 码
    _seed_calendar(w, days)
    with caplog.at_level(logging.WARNING, logger="quantstudio.pipeline.writers"):
        ok, detail = w._wm_decouple_validate(
            "etf_daily", _day_ms(days[0]), _day_ms(days[-1]))
    assert not ok and detail["reason"] == "code_count_out_of_tolerance"
    assert detail["median"] == 100.0 and len(detail["bad_days"]) == 1
    joined = "\n".join(r.getMessage() for r in caplog.records)
    for key in ("reason=code_count_out_of_tolerance", "median=", "tolerance_band=",
                "bad_days=", "bad_days_total=", "window="):
        assert key in joined, "V5 可观测性四要素缺 %s" % key


def test_a2_negative_window_end_missing_degraded_blocks(tmp_path, monkeypatch):
    """A′ V2-③：日历不可得（降级口径）+ 窗口末端缺日 ⇒ ok=False reason=
    boundary_day_missing_degraded（边界缺失）。"""
    monkeypatch.delenv("QS_WM_BASELINE_DAYS", raising=False)
    w = _mk_writer(tmp_path, monkeypatch)
    days = _date_range("20260901", 16)         # 不种 calendar ⇒ 日历不可得
    _seed_days_sql(w, days[:12], n_codes=100)   # 前 12 日有数据
    ok, detail = w._wm_decouple_validate(
        "etf_daily", _day_ms(days[0]), _day_ms(days[15]))   # 末端 4 日无数据
    assert not ok and detail["reason"] == "boundary_day_missing_degraded"


def test_a3_stock_minutes_stable_counts_bitwise_equivalent(tmp_path, monkeypatch):
    """A′ V3：stock_minutes（码数稳定）——默认近期 60 日 vs 回退阀全表旧行为：
    (ok, reason, median) 及 detail 全量逐位等价（80>60 日 ⇒ 近期窗口确实截断过）。"""
    monkeypatch.delenv("QS_WM_BASELINE_DAYS", raising=False)
    w = _mk_writer(tmp_path, monkeypatch)
    days = _date_range("20260701", 80)         # 80 日稳定 50 码
    _seed_minutes_sql(w, days, n_codes=50)
    _seed_calendar(w, days[-3:])
    t_lo, t_hi = _day_ms(days[-3]), _day_ms(days[-1])
    ok_new, d_new = w._wm_decouple_validate("stock_minutes", t_lo, t_hi)
    monkeypatch.setenv("QS_WM_BASELINE_DAYS", "0")
    ok_old, d_old = w._wm_decouple_validate("stock_minutes", t_lo, t_hi)
    assert ok_new and ok_old
    assert (ok_new, d_new["reason"], d_new["median"]) == \
           (ok_old, d_old["reason"], d_old["median"]) == (True, "ok", 50.0)
    assert d_new == d_old, "detail 全量逐位等价（窗口截断不改变稳定码数中位）"


def test_a6_baseline_days_env_valve_and_overrides(tmp_path, monkeypatch):
    """A′ V6：QS_WM_BASELINE_DAYS=0 ⇒ _wm_health_median 逐位回退全表旧行为
    （(median, sample_days)=(全表中位, 全表日数)）；60/30 覆盖各自生效（§2.1 备选）。"""
    w = _mk_writer(tmp_path, monkeypatch)
    old_days, rec_days = _growth_table(w)
    total = len(old_days) + len(rec_days)      # 138
    monkeypatch.setenv("QS_WM_BASELINE_DAYS", "0")
    assert w._wm_health_median("etf_daily") == (100.0, total), \
        "回退阀=0 ⇒ 全表旧行为逐位（旧函数即返回 (全表中位, len(全表日集))）"
    monkeypatch.setenv("QS_WM_BASELINE_DAYS", "60")
    assert w._wm_health_median("etf_daily") == (200.0, 60)
    monkeypatch.setenv("QS_WM_BASELINE_DAYS", "30")
    assert w._wm_health_median("etf_daily") == (200.0, 30)
    monkeypatch.delenv("QS_WM_BASELINE_DAYS")  # 缺省 ⇒ 默认 60
    assert w._wm_health_median("etf_daily") == (200.0, 60)
    monkeypatch.setenv("QS_WM_BASELINE_DAYS", "abc")   # 非法 ⇒ fail-safe 默认 60
    assert w._wm_health_median("etf_daily") == (200.0, 60)


def test_a_prime_insufficient_recent_history_fail_closed(tmp_path, monkeypatch):
    """A′ 补充：近期窗口样本 <8（env 覆盖 N=5 ⇒ 仅取最近 5 个有数据日）⇒ fail-closed
    禁回落全表中位——全表 43 日本有中位（env=0 可取），回落即重现缺陷。"""
    monkeypatch.delenv("QS_WM_BASELINE_DAYS", raising=False)
    w = _mk_writer(tmp_path, monkeypatch)
    old_days = _date_range("20260101", 40)     # 40 个老日
    rec_days = _date_range("20260901", 3)      # 3 个近期日
    _seed_days_sql(w, old_days, n_codes=100)
    _seed_days_sql(w, rec_days, n_codes=100)
    monkeypatch.setenv("QS_WM_BASELINE_DAYS", "0")
    m0, s0 = w._wm_health_median("etf_daily")  # 旧行为：全表 43 日 ⇒ 有中位
    assert m0 is not None and s0 == 43
    monkeypatch.delenv("QS_WM_BASELINE_DAYS")  # 默认 N=60 ≥ 43 ⇒ 全量样本可用
    assert w._wm_health_median("etf_daily") == (100.0, 43)
    monkeypatch.setenv("QS_WM_BASELINE_DAYS", "5")   # 近期窗口 5 日 <8 ⇒ 拒
    m1, s1 = w._wm_health_median("etf_daily")
    assert m1 is None and s1 == 5, "近期样本 <8 ⇒ fail-closed 不回落全表"
    ok, detail = w._wm_decouple_validate(
        "etf_daily", _day_ms(rec_days[0]), _day_ms(rec_days[-1]))
    assert not ok and detail["reason"] == "insufficient_history" \
        and detail["sample_days"] == 5
