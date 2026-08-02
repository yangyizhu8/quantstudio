# tests/test_qfq_authoritative_rebase.py
# 阶段2(R1)单元测试：fresh_authoritative_rebase 模型 + precheck(A-D) + capture 不可变契约
#
# 设计依据：docs/superpowers/specs/2026-07-29-fresh-authoritative-rebase-design.md
# 铁律：禁止改变 ratio / fresh_staged 既有行为（逐位不变）；apply_reanchor_for_security
#       既有参数签名不变；四价格表 raw/*_back/volume/amount/行数/主键不受 rebase 影响；
#       失败路径绝不推进 anchor；禁止 commit/push。
"""fresh_authoritative_rebase 模型 + capture 不可变契约 阶段2(R1)测试。"""

import sys
import json
import types
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
import pandas as pd

from quantstudio.pipeline import qfq_reanchor_schema as SCHEMA
from quantstudio.pipeline import qfq_reanchor_engine as ENG
from quantstudio.pipeline.qfq_calendar import CalendarService
from quantstudio.pipeline.writers import DDL_DUCKDB as PRICE_DDL
from quantstudio.pipeline.qfq_reanchor_engine import (
    FRONT_COLS, ReanchorTolerances, apply_reanchor_for_security,
    ReanchorBlocked, PostcheckFailed,
)
from quantstudio.pipeline import qfq_fresh_capture as CAP
from quantstudio.pipeline.qfq_orchestrator_types import FreshCaptureRecord
from quantstudio.pipeline.qfq_fresh_capture import CaptureContentConflict

# 复用 batch2 既有 hermetic helper（避免重复构造，保证与 fresh_staged 同基准）
from tests.test_qfq_reanchor_batch2 import (
    _seed_security, _fresh_daily, _fresh_minutes_syn, _AUDIT_KW,
    _scales_exd5, F_600875, DAY_MS, OPEN_DAYS, D1, D2, D3, D5, BAR_CLOCKS,
    _snap, _minute_front, _assert_nonfront_unchanged, _make_env,
    RAW_CLOSE, _day_str,
)

REBASE = "fresh_authoritative_rebase"
REBASE_REASON = ("阶段2(R1)：fresh xtquant 为权威 oracle，rebase 仅 UPDATE 四 front 列，"
                 "precheck 以 raw 逐 bar 对齐替代乘法/加法比例校验（§3.3 删除比例假设）")


@pytest.fixture
def env(tmp_path):
    return _make_env(tmp_path, open_days=OPEN_DAYS)


# ===========================================================================
# 1. precheck 正常提交：rebase 仅 UPDATE 四 front 列，其余逐值不变
# ===========================================================================
class TestRebasePrecheckHappyPath:
    def test_rebase_committed_only_four_front_cols(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_security(conn, "000001")            # 对照证券：全程不许动
        _seed_anchor(conn, "600875", version=0)
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")

        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model=REBASE, model_reason=REBASE_REASON, fresh_minutes=fm,
            **_AUDIT_KW)

        # —— 提交 + 仅 UPDATE front 列 ——
        assert res.status == "committed"
        assert res.model == REBASE
        assert res.daily_rows_updated == 5
        assert res.plans == {}

        post_d, post_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        _assert_nonfront_unchanged(pre_d, post_d)
        _assert_nonfront_unchanged(pre_m, post_m)
        assert len(pre_d) == len(post_d) and len(pre_m) == len(post_m)

        # 分钟 front 逐值 = staged fresh（含 09:30 集合竞价 bar）
        fmap = _minute_front(conn, "stock_minutes", "600875")
        assert len(fmap) == 5 * len(BAR_CLOCKS)
        for r in fm.itertuples():
            assert fmap[int(r.time)] == pytest.approx(
                (r.open_front, r.high_front, r.low_front, r.close_front),
                rel=1e-12)
        # 对照证券完全未动
        pd.testing.assert_frame_equal(
            pre_m[pre_m["code"] == "000001"].reset_index(drop=True),
            post_m[post_m["code"] == "000001"].reset_index(drop=True))

        # anchor 同事务推进（成功路径推进至 1）
        assert _anchor_version(conn, "600875") == 1

    # —— §3.3 关键验收：rebase 的 postcheck 必须跳过乘法/加法比例校验 ——
    def test_rebase_postcheck_skips_multiplication_addition(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model=REBASE, model_reason=REBASE_REASON, fresh_minutes=fm,
            **_AUDIT_KW)
        pc = res.postchecks
        # §3.3 删除项：scale_consistency / front_chain_return 以 status=skipped 显式存在
        assert pc["scale_consistency"]["status"] == "skipped"
        assert "§3.3" in pc["scale_consistency"]["reason"]
        assert pc["front_chain_return"]["status"] == "skipped"
        # §3.4 R2：rebase 复用 fresh_staged 三项写后逐 bar 一致（不跑 ≤1 tick 理想校验）
        assert "minute_staged_match" in pc
        assert pc["minute_staged_match"]["1min"]["mismatch"] == 0
        assert "minute_raw_match" in pc
        assert "minute_coverage" in pc
        # rebase 删除 ≤1 tick 假设（§3.3）→ 仍不跑 minute_tick_error
        assert "minute_tick_error" not in pc
        # 保留项：kline_relation / row_conservation / cross_table_overlap
        assert set(pc) >= {"kline_relation", "row_conservation",
                           "cross_table_overlap"}
        # rebase 仍产出 minute_coverage 计划字段（B-1 覆盖统计，非 postcheck）
        assert isinstance(res.minute_coverage, dict) and len(res.minute_coverage) == 1


# ===========================================================================
# 2. precheck A/B/C/D 失败路径：apply 捕获 ReanchorBlocked → status=blocked，不推进 anchor
# ===========================================================================
def _rebase_call(conn, cal, *, fresh_daily, fresh_minutes=None, freqs=("1min",)):
    if fresh_minutes is None:
        fresh_minutes = _fresh_minutes_syn("600875", _scales_exd5(F_600875))
    return apply_reanchor_for_security(
        conn, asset_type="STOCK", code="600875",
        fresh_daily=fresh_daily, calendar=cal, freqs=freqs,
        ex_dates_ms=(D5,), list_date_ms=D1,
        model=REBASE, model_reason=REBASE_REASON, fresh_minutes=fresh_minutes,
        **_AUDIT_KW)


def _seed_anchor(conn, code="600875", version=0):
    # apply_reanchor_for_security 默认 price_source="xtquant"（与引擎一致）
    conn.execute(
        "INSERT INTO qfq_anchor_state (asset_type, code, price_source, "
        "anchor_version, status, updated_at) VALUES ('STOCK', ?, "
        "'xtquant', ?, 'ok', ?) "
        "ON CONFLICT (asset_type, code, price_source) DO UPDATE SET "
        "anchor_version=excluded.anchor_version",
        [code, version, datetime.now()])


def _anchor_version(conn, code="600875"):
    row = conn.execute(
        "SELECT anchor_version FROM qfq_anchor_state "
        "WHERE asset_type='STOCK' AND code=? AND price_source='xtquant'",
        [code]).fetchone()
    return None if row is None else int(row[0])


class TestRebasePrecheckBlocks:
    def test_A_wrong_security_blocks(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        fd = _fresh_daily("600875", _scales_exd5(F_600875)).copy()
        fd["code"] = "600876"          # 错证券
        res = _rebase_call(conn, cal, fresh_daily=fd)
        assert res.status == "blocked"
        assert res.block_reason == "fresh_daily_code_mismatch"
        assert _anchor_version(conn, "600875") == 0

    def test_A_bad_value_blocks(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        fd = _fresh_daily("600875", _scales_exd5(F_600875)).copy()
        fd.loc[fd.index[0], "close"] = -1.0   # close 非正数
        res = _rebase_call(conn, cal, fresh_daily=fd)
        assert res.status == "blocked"
        assert res.block_reason == "fresh_daily_bad_close"
        assert _anchor_version(conn, "600875") == 0

    def test_B_missing_row_blocks(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        fd = _fresh_daily("600875", _scales_exd5(F_600875)).copy()
        fd = fd[fd["time"] != D3].reset_index(drop=True)   # 缺 D3
        res = _rebase_call(conn, cal, fresh_daily=fd)
        assert res.status == "blocked"
        assert res.block_reason == "daily_coverage_mismatch"
        assert _anchor_version(conn, "600875") == 0

    def test_B_extra_row_blocks(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        fd = _fresh_daily("600875", _scales_exd5(F_600875)).copy()
        extra = fd.iloc[[0]].copy()
        extra["time"] = D5 + 86_400_000          # 多一日
        fd = pd.concat([fd, extra], ignore_index=True)
        res = _rebase_call(conn, cal, fresh_daily=fd)
        assert res.status == "blocked"
        assert res.block_reason == "daily_coverage_mismatch"
        assert _anchor_version(conn, "600875") == 0

    def test_A_duplicate_key_blocks(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        fd = _fresh_daily("600875", _scales_exd5(F_600875)).copy()
        dup = fd.iloc[[0]].copy()                # 复制 D1 重复键
        fd = pd.concat([fd, dup], ignore_index=True)
        res = _rebase_call(conn, cal, fresh_daily=fd)
        assert res.status == "blocked"
        assert res.block_reason == "fresh_daily_dup_key"
        assert _anchor_version(conn, "600875") == 0

    def test_C_raw_mismatch_blocks(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        fd = _fresh_daily("600875", _scales_exd5(F_600875)).copy()
        # raw 与库内逐 bar 错位（open / open_front 同时 +0.5，仅测 raw 对齐）
        mask = fd["time"] == D2
        fd.loc[mask, "open"] = fd.loc[mask, "open"] + 0.5
        fd.loc[mask, "open_front"] = fd.loc[mask, "open_front"] + 0.5
        res = _rebase_call(conn, cal, fresh_daily=fd)
        assert res.status == "blocked"
        assert res.block_reason == "daily_raw_mismatch"
        assert _anchor_version(conn, "600875") == 0

    def test_minute_wrong_frequency_blocks(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        fd = _fresh_daily("600875", _scales_exd5(F_600875))
        # fresh 分钟是 5min，但 apply 声明 freqs=("1min",)
        fm5 = _fresh_minutes_syn("600875", _scales_exd5(F_600875), freqs=("5min",))
        res = _rebase_call(conn, cal, fresh_daily=fd, fresh_minutes=fm5,
                           freqs=("1min",))
        assert res.status == "blocked"
        assert res.block_reason == "minute_freq_mismatch"
        assert _anchor_version(conn, "600875") == 0

    def test_minute_coverage_missing_blocks(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        fd = _fresh_daily("600875", _scales_exd5(F_600875))
        fm = _fresh_minutes_syn("600875", _scales_exd5(F_600875)).copy()
        # 删掉 D3 当天的所有分钟 bar → 覆盖缺口
        fm = fm[fm["time"] < D3].reset_index(drop=True)
        res = _rebase_call(conn, cal, fresh_daily=fd, fresh_minutes=fm,
                           freqs=("1min",))
        assert res.status == "blocked"
        assert res.block_reason == "minute_coverage_mismatch"
        assert _anchor_version(conn, "600875") == 0


# ===========================================================================
# 2b. D 方案：allow_partial_minute —— 存量证券分钟历史缺失(fresh ⊃ target)→
#     committed(partial)，只 UPDATE 共有区间 front，不 INSERT 新行，日线不变
# ===========================================================================
class TestRebasePartialMinuteDeferred:
    def test_partial_minute_committed_not_blocked(self, env):
        """seed 日线完整(D1-D5)，但 seed 分钟只 D2-D5（D1 历史缺失）；
        fresh_minutes 完整(D1-D5)。allow_partial_minute=True → 应 committed。
        """
        conn, cal = env.conn, env.calendar
        # 完整 seed（日线 D1-D5 + 分钟 D1-D5），再 DELETE 分钟 D1 模拟库内历史缺失
        _seed_security(conn, "600875", days=DAY_MS)
        conn.execute(
            "DELETE FROM stock_minutes WHERE code=? AND time >= ? AND time < ?",
            ["600875", DAY_MS[0], DAY_MS[1]])
        _seed_anchor(conn, "600875", version=0)
        scales = _scales_exd5(F_600875)
        fm_full = _fresh_minutes_syn("600875", scales)  # 完整 D1-D5
        pre_m = _snap(conn, "stock_minutes")
        pre_m_count = len(pre_m)

        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model=REBASE, model_reason=REBASE_REASON, fresh_minutes=fm_full,
            allow_partial_minute=True, **_AUDIT_KW)

        # —— committed（非 blocked）——
        assert res.status == "committed"
        assert res.minute_coverage["1min"].get("partial") is True
        # 日线正常 UPDATE（5 天）
        assert res.daily_rows_updated == 5
        # 分钟只 UPDATE 共有区间：行数不变（未 INSERT D1）
        post_m = _snap(conn, "stock_minutes")
        assert len(post_m) == pre_m_count
        # 共有区间(D2-D5) front 已更新为 staged fresh：逐 bar 比对 fm_full 的
        # close_front（rebase front 值含 D5 除息调整链，不应自行重算）
        fmap = _minute_front(conn, "stock_minutes", "600875")
        fm_close_front = dict(
            zip(fm_full["time"].tolist(), fm_full["close_front"].tolist()))
        seen_days = set()
        for t, front in fmap.items():
            assert front[3] == pytest.approx(fm_close_front[t], rel=1e-9)
            day = next(d for d in DAY_MS[1:] if _day_str(d) == _day_str(t))
            seen_days.add(day)
        # D1 不在库内（未 INSERT 历史缺失行）
        assert DAY_MS[0] not in seen_days
        # 缺失历史 D1 仍无行
        assert all(_day_str(t) != _day_str(DAY_MS[0]) for t in fmap)
        # anchor 推进
        assert _anchor_version(conn, "600875") == 1

    def test_partial_minute_default_false_still_blocks(self, env):
        """默认 allow_partial_minute=False：相同历史缺失 → 仍 BLOCK（回归保护）。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875", days=DAY_MS)
        conn.execute(
            "DELETE FROM stock_minutes WHERE code=? AND time >= ? AND time < ?",
            ["600875", DAY_MS[0], DAY_MS[1]])
        _seed_anchor(conn, "600875", version=0)
        scales = _scales_exd5(F_600875)
        fm_full = _fresh_minutes_syn("600875", scales)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model=REBASE, model_reason=REBASE_REASON, fresh_minutes=fm_full,
            **_AUDIT_KW)
        assert res.status == "blocked"
        assert res.block_reason == "minute_coverage_mismatch"
        assert _anchor_version(conn, "600875") == 0


# ===========================================================================
# 3. §3.3 删除乘法/加法校验的实证：同样输入 fresh_staged BLOCK，rebase 提交
# ===========================================================================
class TestRebaseDeletesMultiplicationAssumption:
    def _make_proportion_violation(self):
        """fresh_daily 满足 raw 逐 bar 对齐 + 完整覆盖，但 high_front 破坏乘法比例
        （high_front = high_raw × 2，而 close_front = close_raw × s）。"""
        scales = _scales_exd5(F_600875)
        fd = _fresh_daily("600875", scales).copy()
        fd["high_front"] = fd["high"] * 2.0     # 破坏 (X_front/X) 与 (close_front/close) 一致性
        return fd, scales

    def test_fresh_staged_blocks_on_proportion_violation(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        fd, scales = self._make_proportion_violation()
        fm = _fresh_minutes_syn("600875", scales)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=fd, calendar=cal, freqs=("1min",),
            ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason="x", fresh_minutes=fm,
            **_AUDIT_KW)
        assert res.status == "blocked"
        assert res.block_reason == "fresh_daily_scale_inconsistent"
        assert _anchor_version(conn, "600875") == 0

    def test_rebase_commits_on_proportion_violation(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        fd, scales = self._make_proportion_violation()
        fm = _fresh_minutes_syn("600875", scales)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=fd, calendar=cal, freqs=("1min",),
            ex_dates_ms=(D5,), list_date_ms=D1,
            model=REBASE, model_reason=REBASE_REASON, fresh_minutes=fm,
            **_AUDIT_KW)
        assert res.status == "committed"
        # rebase 忠实写入了破坏比例假设的 high_front（证明未做乘法/加法比例拦截）
        fhm = dict(zip(fd["time"].astype("int64"), fd["high_front"]))
        stored = conn.execute(
            "SELECT time, high_front FROM stock_daily WHERE code='600875' "
            "ORDER BY time").df()
        stored["time"] = stored["time"].astype("int64")
        for _, r in stored.iterrows():
            assert r["high_front"] == pytest.approx(fhm[r["time"]], rel=1e-9)
        assert _anchor_version(conn, "600875") == 1   # 成功路径推进


# ===========================================================================
# 4. 模型注册 / 审计三元组 fail-fast（事务外 ValueError）
# ===========================================================================
class TestRebaseAuditFailFast:
    def _base(self, conn, cal):
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        return dict(
            fresh_daily=_fresh_daily("600875", scales),
            fresh_minutes=_fresh_minutes_syn("600875", scales),
            calendar=cal, freqs=("1min",), ex_dates_ms=(D5,),
            list_date_ms=D1, model=REBASE, model_reason=REBASE_REASON)

    def test_missing_fresh_minutes_valueerror(self, env):
        conn, cal = env.conn, env.calendar
        kw = self._base(conn, cal)
        kw.pop("fresh_minutes")
        with pytest.raises(ValueError):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="600875", **kw, **_AUDIT_KW)

    def test_missing_model_reason_valueerror(self, env):
        conn, cal = env.conn, env.calendar
        kw = self._base(conn, cal)
        kw.pop("model_reason")
        with pytest.raises(ValueError):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="600875", **kw, **_AUDIT_KW)

    def test_missing_capture_id_valueerror(self, env):
        conn, cal = env.conn, env.calendar
        kw = self._base(conn, cal)
        bad_audit = {k: v for k, v in _AUDIT_KW.items()
                     if k != "fresh_capture_id"}
        with pytest.raises(ValueError):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="600875", **kw, **bad_audit)

    def test_unknown_model_valueerror(self, env):
        conn, cal = env.conn, env.calendar
        kw = self._base(conn, cal)
        kw["model"] = "bogus"
        with pytest.raises(ValueError):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="600875", **kw, **_AUDIT_KW)


# ===========================================================================
# 5. capture 不可变契约（§3.5 五条）
# ===========================================================================
def _cap_rec(capture_id, *, code="600875", status="captured",
             daily_sha="aa" * 32, minute_sha="bb" * 32, meta_sha="ab" * 32):
    now = datetime.now()
    return types.SimpleNamespace(
        capture_id=capture_id, asset_type="STOCK", code=code,
        source="xtdata.get_market_data_ex",
        daily_range_start=D1, daily_range_end=D5,
        minute_range_start=D1, minute_range_end=D5,
        daily_sha256=daily_sha, minute_sha256=minute_sha,
        metadata_sha256=meta_sha, status=status,
        daily_row_count=0, minute_row_count=0,
        created_at=now, updated_at=now)


def _insert_event_committed(conn, capture_id, code="600875"):
    plan = json.dumps({"model_audit": {
        "fresh_capture_id": capture_id,
        "fresh_source": "xtdata.get_market_data_ex",
        "fresh_metadata_sha256": "ab" * 32}})
    now = datetime.now()
    conn.execute(
        "INSERT INTO qfq_reanchor_event (event_id, event_type, asset_type, code, "
        "price_source, daily_method, minute_ratio_plan, status, created_at, "
        "first_seen_at, last_seen_at, occurrence_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [f"ev-{capture_id}", "reanchor", "STOCK", code, "xtquant_front",
         "staged_fresh_update", plan, "committed",
         now, now, now, 1])


def _resolve_call(conn, capture_id, *, daily_sha="aa" * 32, minute_sha="bb" * 32):
    return CAP.resolve_fresh_capture(
        conn, capture_id=capture_id, asset_type="STOCK", code="600875",
        source="xtdata.get_market_data_ex", daily_range_start=D1,
        daily_range_end=D5, minute_range_start=D1, minute_range_end=D5,
        daily_sha256=daily_sha, minute_sha256=minute_sha,
        metadata_sha256="ab" * 32)


class TestFreshCaptureContract:
    def test_new_capture_writes(self, env):
        conn, _ = env.conn, env.calendar
        cid = "cap-new-001"
        action = _resolve_call(conn, cid)
        assert action == CAP.CAPTURE_ACTION_NEW
        CAP.write_fresh_capture(conn, _cap_rec(cid))
        row = conn.execute(
            "SELECT status FROM qfq_fresh_capture WHERE capture_id=?",
            [cid]).fetchone()
        assert row is not None and row[0] == "captured"

    def test_already_committed_no_rewrite(self, env):
        conn, _ = env.conn, env.calendar
        cid = "cap-committed-001"
        CAP.write_fresh_capture(conn, _cap_rec(cid, status="committed"))
        _insert_event_committed(conn, cid)
        action = _resolve_call(conn, cid)
        assert action == CAP.CAPTURE_ACTION_ALREADY_COMMITTED
        # 不可变契约：动作不是 new，调用方不应再次写价；committed event 仍在
        ev = conn.execute(
            "SELECT status FROM qfq_reanchor_event WHERE "
            "json_extract_string(minute_ratio_plan,'$.model_audit.fresh_capture_id')=?",
            [cid]).fetchone()
        assert ev is not None and ev[0] == "committed"

    def test_uncommitted_sha_match_continue(self, env):
        conn, _ = env.conn, env.calendar
        cid = "cap-uncommitted-001"
        CAP.write_fresh_capture(conn, _cap_rec(cid, status="captured"))
        action = _resolve_call(conn, cid)
        assert action == CAP.CAPTURE_ACTION_RECOLLECT_OK

    def test_sha_conflict_blocks(self, env):
        conn, _ = env.conn, env.calendar
        cid = "cap-conflict-001"
        # 库内 daily_sha256 = "aa"*32，本次请求 "bb"*32 → 内容冲突
        CAP.write_fresh_capture(conn, _cap_rec(cid, status="captured",
                                               daily_sha="aa" * 32))
        with pytest.raises(CAP.CaptureContentConflict) as ei:
            _resolve_call(conn, cid, daily_sha="bb" * 32)
        assert "内容冲突" in str(ei.value)

    def test_applied_no_event_recovers(self, env):
        conn, _ = env.conn, env.calendar
        cid = "cap-applied-001"
        CAP.write_fresh_capture(conn, _cap_rec(cid, status="applied"))
        action = _resolve_call(conn, cid)
        assert action == CAP.CAPTURE_ACTION_RECOVER_APPLIED_NO_EVENT


# ===========================================================================
# 6. 回归：ratio / fresh_staged 既有行为逐位不变
# ===========================================================================
class TestRegressionRatioFreshStaged:
    def test_fresh_staged_still_commits_four_front_cols(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason="x", fresh_minutes=fm,
            **_AUDIT_KW)
        assert res.status == "committed"
        assert res.model == "fresh_staged"
        # fresh_staged 仍运行 scale_consistency / front_chain_return（未被删除）
        assert "scale_consistency" in res.postchecks
        assert "front_chain_return" in res.postchecks
        assert res.postchecks["scale_consistency"]["daily_max_dev"] <= 1e-9
        post_d, post_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        _assert_nonfront_unchanged(pre_d, post_d)
        _assert_nonfront_unchanged(pre_m, post_m)
        assert len(pre_d) == len(post_d) and len(pre_m) == len(post_m)


# 崩溃恢复场景构造：插入与 engine 同口径计算的匹配内容 hash 的 capture 行
def _insert_matching_capture(conn, *, capture_id, code="600875", fresh_daily,
                             fresh_minutes, status="captured",
                             metadata_sha256="ab" * 32, daily_sha256=None,
                             minute_sha256=None):
    mdf = fresh_minutes
    if "freq" not in mdf.columns:
        mdf = mdf.copy()
        mdf["freq"] = "1min"
    d_start = int(fresh_daily["time"].min()); d_end = int(fresh_daily["time"].max())
    m_start = int(mdf["time"].min()); m_end = int(mdf["time"].max())
    daily_sha, minute_sha = ENG._fresh_content_hashes(fresh_daily, mdf)
    rec = FreshCaptureRecord(
        capture_id=capture_id, asset_type="STOCK", code=code,
        source="xtdata.get_market_data_ex",
        daily_range_start=d_start, daily_range_end=d_end,
        minute_range_start=m_start, minute_range_end=m_end,
        daily_sha256=daily_sha256 or daily_sha,
        minute_sha256=minute_sha256 or minute_sha,
        metadata_sha256=metadata_sha256, daily_row_count=len(fresh_daily),
        minute_row_count=len(fresh_minutes), status=status,
        created_at=datetime.now(), updated_at=datetime.now())
    CAP.write_fresh_capture(conn, rec)


# ===========================================================================
# 7. 阶段3(R2)：崩溃恢复幂等 + 事务原子性 + 写后逐 bar 一致（apply 级闭环）
# ===========================================================================
class TestRebaseR2CrashRecovery:
    def _apply(self, conn, cal, *, fresh_daily, fresh_minutes, capture_id,
               metadata_sha256="ab" * 32, **kw):
        return apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=fresh_daily, calendar=cal, freqs=("1min",),
            ex_dates_ms=(D5,), list_date_ms=D1,
            model=REBASE, model_reason=REBASE_REASON, fresh_minutes=fresh_minutes,
            fresh_capture_id=capture_id, fresh_metadata_sha256=metadata_sha256,
            fresh_source="xtdata.get_market_data_ex", **kw)

    def test_same_capture_repeat_apply_idempotent(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        scales = _scales_exd5(F_600875)
        fd = _fresh_daily("600875", scales)
        fm = _fresh_minutes_syn("600875", scales)
        cid = "cap-r2-idem-001"
        r1 = self._apply(conn, cal, fresh_daily=fd, fresh_minutes=fm, capture_id=cid)
        assert r1.status == "committed"
        assert _anchor_version(conn, "600875") == 1
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        # 重放同一 capture（崩溃恢复场景）：幂等，不重复写价、不推进 anchor
        r2 = self._apply(conn, cal, fresh_daily=fd, fresh_minutes=fm, capture_id=cid)
        assert r2.status == "committed"
        assert r2.block_reason == "already_committed"
        post_d, post_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        pd.testing.assert_frame_equal(pre_d, post_d)
        pd.testing.assert_frame_equal(pre_m, post_m)
        assert _anchor_version(conn, "600875") == 1
        st = conn.execute("SELECT status FROM qfq_fresh_capture WHERE capture_id=?",
                          [cid]).fetchone()[0]
        assert st == "applied"

    def test_event_committed_capture_not_applied_repaired(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=1)
        scales = _scales_exd5(F_600875)
        fd = _fresh_daily("600875", scales)
        fm = _fresh_minutes_syn("600875", scales)
        cid = "cap-r2-repair-001"
        # 崩溃窗口：event 已 committed 但 capture 未 applied
        _insert_matching_capture(conn, capture_id=cid, fresh_daily=fd,
                                 fresh_minutes=fm, status="captured")
        _insert_event_committed(conn, cid)
        r = self._apply(conn, cal, fresh_daily=fd, fresh_minutes=fm, capture_id=cid)
        assert r.status == "committed"
        assert r.block_reason == "already_committed"
        # capture 状态被修复为 applied（以 committed event 为成功事实）
        st = conn.execute("SELECT status FROM qfq_fresh_capture WHERE capture_id=?",
                          [cid]).fetchone()[0]
        assert st == "applied"

    def test_capture_applied_no_event_recover(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        scales = _scales_exd5(F_600875)
        fd = _fresh_daily("600875", scales)
        fm = _fresh_minutes_syn("600875", scales)
        cid = "cap-r2-applied-noevt-001"
        # 崩溃窗口：capture 已 applied 但无 committed event（价格已写但事件丢失）
        _insert_matching_capture(conn, capture_id=cid, fresh_daily=fd,
                                 fresh_minutes=fm, status="applied")
        with pytest.raises(ReanchorBlocked) as ei:
            self._apply(conn, cal, fresh_daily=fd, fresh_minutes=fm, capture_id=cid)
        assert ei.value.reason == "capture_recover_applied_no_event"
        # 进入异常恢复：不静默跳过，未写价、未推进 anchor
        assert _anchor_version(conn, "600875") == 0

    def test_capture_content_conflict_blocks(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        scales = _scales_exd5(F_600875)
        fd = _fresh_daily("600875", scales)
        fm = _fresh_minutes_syn("600875", scales)
        cid = "cap-r2-conflict-001"
        # 库内 daily_sha256 故意不一致 → 内容冲突 BLOCK
        _insert_matching_capture(conn, capture_id=cid, fresh_daily=fd,
                                 fresh_minutes=fm, status="captured",
                                 daily_sha256="00" * 32)
        with pytest.raises(CaptureContentConflict):
            self._apply(conn, cal, fresh_daily=fd, fresh_minutes=fm, capture_id=cid)
        assert _anchor_version(conn, "600875") == 0


class TestRebaseR2Atomicity:
    def _apply(self, conn, cal, *, fresh_daily, fresh_minutes,
               capture_id="cap-r2-atomic", **kw):
        return apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=fresh_daily, calendar=cal, freqs=("1min",),
            ex_dates_ms=(D5,), list_date_ms=D1,
            model=REBASE, model_reason=REBASE_REASON, fresh_minutes=fresh_minutes,
            fresh_capture_id=capture_id, fresh_metadata_sha256="ab" * 32,
            fresh_source="xtdata.get_market_data_ex", **kw)

    def test_postcheck_failure_rolls_back(self, env, monkeypatch):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        scales = _scales_exd5(F_600875)
        fd = _fresh_daily("600875", scales)
        fm = _fresh_minutes_syn("600875", scales)
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        # 注入 postcheck 异常 → 整券 ROLLBACK
        def _boom(*a, **k):
            raise PostcheckFailed("kline_relation", "注入 postcheck 失败")
        monkeypatch.setattr(ENG, "run_postchecks", _boom)
        res = self._apply(conn, cal, fresh_daily=fd, fresh_minutes=fm)
        assert res.status == "rolled_back"
        assert res.block_reason == "kline_relation"
        post_d, post_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        pd.testing.assert_frame_equal(pre_d, post_d)
        pd.testing.assert_frame_equal(pre_m, post_m)
        assert _anchor_version(conn, "600875") == 0

    def test_blocked_event_records_but_anchor_not_advanced(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        scales = _scales_exd5(F_600875)
        fd = _fresh_daily("600875", scales)
        fm = _fresh_minutes_syn("600875", scales).iloc[1:]  # 缺 1 根 → coverage block
        res = self._apply(conn, cal, fresh_daily=fd, fresh_minutes=fm,
                          capture_id="cap-r2-block-001")
        assert res.status == "blocked"
        assert _anchor_version(conn, "600875") == 0
        # failed/blocked event 已记录（独立短事务），锚点不推进
        n = conn.execute(
            "SELECT COUNT(*) FROM qfq_reanchor_event WHERE code='600875' "
            "AND status='blocked'").fetchone()[0]
        assert n == 1


class TestRebaseR2WriteAfterConsistency:
    def test_write_after_minute_staged_and_raw_consistent(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_anchor(conn, "600875", version=0)
        scales = _scales_exd5(F_600875)
        fd = _fresh_daily("600875", scales)
        fm = _fresh_minutes_syn("600875", scales)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=fd, calendar=cal, freqs=("1min",),
            ex_dates_ms=(D5,), list_date_ms=D1,
            model=REBASE, model_reason=REBASE_REASON, fresh_minutes=fm,
            **_AUDIT_KW)
        assert res.status == "committed"
        pc = res.postchecks
        # daily 四 front == staged 精确一致（mismatch=0）
        assert pc["daily_staged_match"]["mismatch"] == 0
        # minute 四 front == staged 精确一致
        assert pc["minute_staged_match"]["1min"]["mismatch"] == 0
        # minute raw 未被触碰（raw_match）
        assert pc["minute_raw_match"]["1min"]["raw_invalid"] == 0
        assert pc["minute_raw_match"]["1min"]["raw_mismatch"] == 0
        # minute 覆盖完整
        cov = pc["minute_coverage"]["1min"]
        assert cov["staged_count"] == cov["matched_count"] == cov["target_count"]
        assert cov["missing_target"] == 0
        assert cov["missing_staged"] == 0
