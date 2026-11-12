# -*- coding: utf-8 -*-
"""QFQ settle（历史冻结）测试 —— 乙件②。

设计要点（2026-11-10）
----------------------
本文件分**两层**，以适配「判据尚未实施」的当前状态（总调度排期：G6 后/明晚受控窗）：

**Layer A（立即可测，不 skip）**
  · `data/qfq_frozen_bounds.json` 的结构与数据质量
  · 三个不变量：只增不改 / 边界来自实测 / 缺失即 fail-closed（数据侧可见部分）
  这些在「只有数据文件、没有判据代码」时也应绿——它们防的是**数据本身出错**。

**Layer B（TDD 前置，接口未实施时整组 skip）**
  · V1-V7：`_resolve_frozen_before` / `_load_frozen_bounds` 的契约与判据行为
  · 一旦 `qfq_reanchor_engine` 落地 settle 判据，本组**自动启用**，无需改测试
  这样做的理由：现在写「必红」的测试违反「验收须全绿」纪律；
  而 skip 不是免检——它是有明确解除条件的挂起（解除条件 = 接口存在）。

对应方案：docs/qfq-settle-design-20261110.md §六 验收 V1-V10（V8-V9 属实施轮）
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

CST = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
BOUNDS = ROOT / "data" / "qfq_frozen_bounds.json"
KNOWN_VIOLATIONS = ROOT / "data" / "qfq_known_violations.json"

JOBS = [
    ("STOCK", "stock_minutes"),
    ("STOCK", "stock_daily"),
    ("ETF", "etf_minutes"),
    ("ETF", "etf_daily"),
]


# ============================================================ Layer A：数据质量


@pytest.fixture(scope="module")
def bounds_doc() -> dict:
    if not BOUNDS.exists():
        pytest.skip(f"{BOUNDS.name} 尚未生成（运行 scripts/gen_frozen_bounds.py）")
    return json.loads(BOUNDS.read_text(encoding="utf-8"))


class TestBoundsDataQuality:
    """A1-A6：冻结边界文件的结构与数据质量。"""

    def test_a1_meta_required_fields(self, bounds_doc):
        """A1：_meta 必含 purpose / routing_ruling / source / counts / invariants。"""
        meta = bounds_doc.get("_meta", {})
        for k in ("purpose", "routing_ruling", "source", "counts", "invariants"):
            assert k in meta, f"_meta 缺 {k}"
        assert isinstance(meta["invariants"], list) and len(meta["invariants"]) >= 3

    def test_a2_all_four_tables_present(self, bounds_doc):
        """A2：四个表都必须有边界集合（STOCK/ETF × minutes/daily）。"""
        b = bounds_doc.get("bounds", {})
        for asset, table in JOBS:
            assert asset in b, f"缺资产 {asset}"
            assert table in b[asset], f"缺 {asset}/{table}"
            assert isinstance(b[asset][table], dict)

    def test_a3_code_counts_match_meta(self, bounds_doc):
        """A3：实际 code 数与 _meta.counts 一致（防文件被截断）。"""
        b, counts = bounds_doc["bounds"], bounds_doc["_meta"]["counts"]
        for asset, table in JOBS:
            key = f"{asset}/{table}"
            actual = len(b[asset][table])
            assert key in counts, f"_meta.counts 缺 {key}"
            assert actual == counts[key]["codes"], (
                f"{key} 实际 {actual} != 记录 {counts[key]['codes']}")

    def test_a4_bounds_are_epoch_ms(self, bounds_doc):
        """A4：所有边界值必须是**正整数毫秒**（不是秒、不是字符串、不是 None）。"""
        b = bounds_doc["bounds"]
        checked = 0
        for asset, table in JOBS:
            for code, v in b[asset][table].items():
                assert isinstance(v, int), f"{asset}/{table}/{code} 非 int: {type(v)}"
                assert v > 0, f"{asset}/{table}/{code} 非正: {v}"
                # 2000-01-01 ~ 2030-01-01 的毫秒区间（粗校验，防秒/微秒混入）
                assert 946_684_800_000 <= v <= 1_893_456_000_000, (
                    f"{asset}/{table}/{code} 超出合理毫秒区间: {v}")
                checked += 1
                if checked > 500:
                    return

    def test_a5_bounds_not_in_future(self, bounds_doc):
        """A5：边界不得晚于今天（晚于今天 = 生成逻辑错，会误豁免未来数据）。"""
        now_ms = int(datetime.now(CST).timestamp() * 1000)
        b = bounds_doc["bounds"]
        checked = 0
        for asset, table in JOBS:
            for code, v in b[asset][table].items():
                assert v <= now_ms, (
                    f"{asset}/{table}/{code} 边界在未来: "
                    f"{datetime.fromtimestamp(v/1000, CST):%Y-%m-%d}")
                checked += 1
                if checked > 500:
                    return

    def test_a6_code_format_is_6_digit(self, bounds_doc):
        """A6：code 必须是 6 位纯数字（不带交易所后缀）。"""
        b = bounds_doc["bounds"]
        bad = []
        for asset, table in JOBS:
            for code in list(b[asset][table])[:300]:
                if not (len(code) == 6 and code.isdigit()):
                    bad.append(f"{asset}/{table}/{code}")
        assert not bad, f"code 格式异常: {bad[:5]}"


# ============================================================ Layer B：判据契约（TDD 前置）

eng = pytest.importorskip("quantstudio.pipeline.qfq_reanchor_engine")

_HAS_RESOLVE = hasattr(eng, "_resolve_frozen_before")
_HAS_LOAD = hasattr(eng, "_load_frozen_bounds")

pytestmark_layer_b = pytest.mark.skipif(
    not (_HAS_RESOLVE and _HAS_LOAD),
    reason=(
        "settle 判据尚未实施（P3/P4 闸门未满足，排期 G6 后/明晚受控窗）。"
        "本组为 TDD 前置：待 qfq_reanchor_engine 落地 _resolve_frozen_before / "
        "_load_frozen_bounds 后自动启用。"
    ),
)


@pytest.mark.skipif(
    not (_HAS_RESOLVE and _HAS_LOAD),
    reason="settle 判据尚未实施（TDD 前置，实施后自动启用）",
)
class TestSettleContract:
    """V3/V6：`_resolve_frozen_before` 的 fail-closed 契约。

    V1/V2/V4/V5/V7 需要判据内联改动才能断言（覆盖计算的豁免与不减），
    属实施轮（步骤 4-7）的验收，不在此处。
    """

    def test_v3_missing_code_returns_none(self, monkeypatch):
        """V3：code 不在边界文件内 ⇒ 返回 None（调用方走严格路径，不豁免）。"""
        monkeypatch.setenv("QS_QFQ_FROZEN_BEFORE", "1")
        # 用一个绝不可能存在的 code
        got = eng._resolve_frozen_before("999999", "STOCK")
        assert got is None, f"缺失 code 应返回 None，实际 {got}"

    def test_v6_env_disable_returns_none(self, monkeypatch, bounds_doc):
        """V6：QS_QFQ_FROZEN_BEFORE=0 ⇒ 即使 code 有边界也返回 None（一键退回严格）。"""
        # 取一个真实存在的 code
        sample = next(iter(bounds_doc["bounds"]["STOCK"]["stock_daily"]))
        monkeypatch.setenv("QS_QFQ_FROZEN_BEFORE", "0")
        got = eng._resolve_frozen_before(sample, "STOCK")
        assert got is None, f"环境变量置 0 时应返回 None，实际 {got}"

    def test_v6b_env_enable_returns_bound(self, monkeypatch, bounds_doc):
        """V6 反向对照：环境变量=1（默认）且 code 存在 ⇒ 返回其边界毫秒值。"""
        sample_code, expect = next(
            iter(bounds_doc["bounds"]["STOCK"]["stock_daily"].items()))
        monkeypatch.setenv("QS_QFQ_FROZEN_BEFORE", "1")
        got = eng._resolve_frozen_before(sample_code, "STOCK")
        assert got == expect, (
            f"应返回边界 {expect}，实际 {got}（反向对照失败 ⇒ 豁免永不生效）")

    def test_v5b_missing_bounds_file_fails_closed(self, monkeypatch, tmp_path):
        """V5 的边界侧对偶：边界文件不存在 ⇒ 全部返回 None（不豁免）。"""
        monkeypatch.setenv("QS_QFQ_FROZEN_BEFORE", "1")
        # 若实现缓存了模块级 bounds，这里先清缓存（接口约定应提供）
        if hasattr(eng, "_reset_frozen_bounds_cache"):
            eng._reset_frozen_bounds_cache()
        # 把路径指向不存在的位置（实现应可被 monkeypatch 覆盖）
        if hasattr(eng, "_FROZEN_BOUNDS_PATH"):
            monkeypatch.setattr(eng, "_FROZEN_BOUNDS_PATH", tmp_path / "nope.json")
            if hasattr(eng, "_reset_frozen_bounds_cache"):
                eng._reset_frozen_bounds_cache()
            got = eng._resolve_frozen_before("000001", "STOCK")
            assert got is None, f"文件缺失时应 fail-closed，实际 {got}"
        else:
            pytest.skip("实现未暴露 _FROZEN_BOUNDS_PATH，无法注入缺失路径")


# ============================================================ Layer C：V1/V2/V4/V7（双态对照）

# 判据辅助函数是否已落地（settle 丙方案 §3.3 的 4 个新函数）
_HAS_HELPERS = (hasattr(eng, "_count_target_after")
                and hasattr(eng, "_count_matched_after"))
_HAS_WHITELIST = hasattr(eng, "_count_in_whitelist")

# 测试用日基准（CST 00:00）。用整数偏移构造「边界前 / 边界 / 边界后」三档。
_D = 1793491200000                      # 2026-10-30 00:00 CST
_DAY = 86400000
_T_PRE2, _T_PRE1 = _D - 2 * _DAY, _D - _DAY
_T_D, _T_POST1 = _D, _D + _DAY
_R = 10.0                               # raw 值：库内与 fresh 用同一组，确保 raw 对齐不干扰覆盖判定


def _mk_minute_db(rows):
    """最小 stock_minutes（仅判据用到的列）——内存库，不触生产库。"""
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE stock_minutes(code VARCHAR, time BIGINT, freq VARCHAR, "
        "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE)")
    for t in rows:
        conn.execute(
            "INSERT INTO stock_minutes VALUES ('X', ?, '1min', ?, ?, ?, ?)",
            [t, _R, _R, _R, _R])
    return conn


def _mk_fresh(times):
    """fresh 分钟 df（列 = code/time/freq + RAW_COLS）。"""
    pd = pytest.importorskip("pandas")
    n = len(times)
    return pd.DataFrame({
        "code": ["X"] * n, "time": list(times), "freq": ["1min"] * n,
        "open": [_R] * n, "high": [_R] * n, "low": [_R] * n, "close": [_R] * n,
    })


def _run_minute_cov(conn, fresh):
    """直接调判据（allow_partial=False，与 rebase 默认一致）。"""
    return eng._check_minute_cov_raw(
        conn, "STOCK", "X", "1min", fresh, 1e-9, allow_partial=False)


@pytest.fixture
def frozen_at_d(monkeypatch):
    """把冻结边界打桩为 `_D`。

    必要性：测试用合成 code='X'，而真实 `data/qfq_frozen_bounds.json` 里并无 'X'
    ⇒ `_resolve_frozen_before` 会**正确地** fail-closed 返回 None ⇒ 全部走严格路径，
    导致 V1/V4/V7 永远测不到豁免语义（2026-11-12 首次运行即踩此坑）。
    打桩只替换「边界解析」这一个纯函数，判据本体与 SQL 全部走真实实现。
    """
    monkeypatch.setenv("QS_QFQ_FROZEN_BEFORE", "1")
    monkeypatch.setattr(eng, "_resolve_frozen_before",
                        lambda code, asset_type: _D, raising=True)
    return _D


# ---------- 前态（判据未实施时断言「现行严格口径会 BLOCK」）----------

@pytest.mark.skipif(
    _HAS_HELPERS,
    reason="settle 判据已实施 ⇒ 前态对照自动退役（见下方后态用例）",
)
class TestSettlePreStateBaseline:
    """**前态基线**：settle 未实施时，现行严格口径对「边界前缺行」也会 BLOCK。

    作用 = 双态对照的「前」半：证明下面的 V1 fixture **确实构成旧口径下的失败**，
    从而 V1 实施后转 PASS 才是有意义的（否则可能是 fixture 本身就不触发判据）。
    """

    def test_pre_v1_strict_blocks_on_pre_boundary_gap(self):
        """前态：库内有 [D-2, D-1, D, D+1]，fresh 只有 [D, D+1] ⇒ 现行 BLOCK。"""
        conn = _mk_minute_db([_T_PRE2, _T_PRE1, _T_D, _T_POST1])
        try:
            with pytest.raises(eng.ReanchorBlocked) as ei:
                _run_minute_cov(conn, _mk_fresh([_T_D, _T_POST1]))
            # ReanchorBlocked 的 args[0] 是「code: 详情」整串
            assert ei.value.args[0].startswith("minute_coverage_mismatch"), (
                f"应报 minute_coverage_mismatch，实际 {ei.value.args[0][:120]}")
            assert "缺 2" in ei.value.args[0], (
                f"旧口径应把边界前 2 行也计为缺，实际 {ei.value.args[0][:120]}")
        finally:
            conn.close()


# ---------- 后态（判据实施后断言 settle 语义）----------

@pytest.mark.skipif(
    not _HAS_HELPERS,
    reason=("settle 判据尚未实施（TDD 前置）：待 qfq_reanchor_engine 落地 "
            "_count_target_after/_count_matched_after 后自动启用"),
)
class TestSettleV1V2V4:
    """V1/V2/V4：冻结边界与白名单对 `missing_target` 的影响（判据级）。"""

    def test_v1_pre_boundary_gap_is_exempt(self, frozen_at_d):
        """V1：边界前有缺 ⇒ 豁免且 pass（missing_target 只统计边界后）。"""
        conn = _mk_minute_db([_T_PRE2, _T_PRE1, _T_D, _T_POST1])
        try:
            cov = _run_minute_cov(conn, _mk_fresh([_T_D, _T_POST1]))
            assert cov is not None, "边界前缺行应被豁免（返回 cov），实际 BLOCK"
            assert cov["missing_target"] == 0, f"missing_target 应为 0，实际 {cov}"
        finally:
            conn.close()

    def test_v2_post_boundary_gap_still_blocks(self, frozen_at_d):
        """V2（防回归核心）：边界**后**有缺 ⇒ 不豁免，仍 BLOCK。"""
        conn = _mk_minute_db([_T_PRE2, _T_D, _T_POST1])
        try:
            with pytest.raises(eng.ReanchorBlocked) as ei:
                _run_minute_cov(conn, _mk_fresh([_T_PRE2]))
            msg = ei.value.args[0]
            assert msg.startswith("minute_coverage_mismatch"), (
                f"应报 minute_coverage_mismatch，实际 {msg[:120]}")
            # 缺的必须是边界后那两行（D、D+1），不是边界前的
            assert "缺 2" in msg, f"应报缺 2 行（边界后），实际 {msg[:120]}"
        finally:
            conn.close()

    def test_v4_whitelist_reduces_missing(self, frozen_at_d, monkeypatch):
        """V4：白名单命中 ⇒ 从 missing_target 中减去 ⇒ 减后 0 ⇒ pass。"""
        # 白名单强制命中 (X, D) 与 (X, D+1) 两条
        if not _HAS_WHITELIST:
            pytest.skip("实现未暴露 _count_in_whitelist，无法构造白名单减免")
        monkeypatch.setattr(eng, "_count_in_whitelist", lambda *a, **kw: 2, raising=False)
        conn = _mk_minute_db([_T_PRE2, _T_D, _T_POST1])
        try:
            cov = _run_minute_cov(conn, _mk_fresh([_T_PRE2]))
            assert cov is not None, "白名单减后 missing_target=0，应 pass 而非 BLOCK"
            assert cov["missing_target"] == 0
        finally:
            conn.close()

    def test_v6_env_zero_disables_all_exemptions(self, monkeypatch):
        """V6 判据级对照：QS_QFQ_FROZEN_BEFORE=0 ⇒ V1 场景回到 BLOCK（一键退回严格）。"""
        monkeypatch.setenv("QS_QFQ_FROZEN_BEFORE", "0")
        conn = _mk_minute_db([_T_PRE2, _T_PRE1, _T_D, _T_POST1])
        try:
            with pytest.raises(eng.ReanchorBlocked):
                _run_minute_cov(conn, _mk_fresh([_T_D, _T_POST1]))
        finally:
            conn.close()


@pytest.mark.skipif(
    not (_HAS_HELPERS and hasattr(eng, "_resolve_frozen_before")),
    reason="settle 判据尚未实施（TDD 前置，实施后自动启用）",
)
class TestSettleV7Warning:
    """V7：豁免必须留 WARNING（日志含 history_exempt）。"""

    def test_v7_exempt_emits_history_exempt_warning(self, frozen_at_d, caplog):
        """V7：触发豁免时日志必须含 `history_exempt` 且带 frozen_before 值。"""
        conn = _mk_minute_db([_T_PRE2, _T_PRE1, _T_D, _T_POST1])
        try:
            with caplog.at_level("WARNING"):
                _run_minute_cov(conn, _mk_fresh([_T_D, _T_POST1]))
        finally:
            conn.close()
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "history_exempt" in text, (
            f"豁免必须留痕 history_exempt（便于审计放行了哪些），实际日志: {text[:400]}")
        # 实现用 _ms_to_date 把边界渲染成 'YYYY-MM-DD'（比裸 ms 更可读）⇒ 断言日期形态
        _expect_day = "2026-11-01"
        assert _expect_day in text, (
            f"日志应带 frozen_before 日期 {_expect_day} 便于追溯，实际日志: {text[:400]}")


# ============================================================ 附带：白名单数据质量


@pytest.fixture(scope="module")
def kv_doc() -> dict:
    if not KNOWN_VIOLATIONS.exists():
        pytest.skip(f"{KNOWN_VIOLATIONS.name} 尚未生成")
    return json.loads(KNOWN_VIOLATIONS.read_text(encoding="utf-8"))


class TestKnownViolationsQuality:
    """KV1-KV3：白名单数据质量（裁 (a) 产物）。"""

    def test_kv1_has_meta_and_entries(self, kv_doc):
        """KV1：含 _meta 与 entries，且 _meta 有 consumed_by / invariants。"""
        assert "entries" in kv_doc and isinstance(kv_doc["entries"], list)
        meta = kv_doc.get("_meta", {})
        assert "consumed_by" in meta
        assert isinstance(meta.get("invariants"), list)

    def test_kv2_entries_shape(self, kv_doc):
        """KV2：每条 entry 必含 asset/table/code/days/reason/evidence。"""
        for e in kv_doc["entries"][:50]:
            for k in ("asset", "table", "code", "days", "reason", "evidence"):
                assert k in e, f"entry 缺 {k}: {e}"

    def test_kv3_all_marked_source_absent(self, kv_doc):
        """KV3：所有条目的 reason 必须是 source_absent（本批只登记「源端也无」）。"""
        bad = [e for e in kv_doc["entries"] if e.get("reason") != "source_absent"]
        assert not bad, f"存在非 source_absent 条目: {bad[:3]}"
