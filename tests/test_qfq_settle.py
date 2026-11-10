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
