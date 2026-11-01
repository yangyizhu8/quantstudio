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
