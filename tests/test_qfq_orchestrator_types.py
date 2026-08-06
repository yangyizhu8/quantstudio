"""B-2 测试：trigger_id v1/v2 + alert_id_v2 + QFQOrchestratorConfig 世代字段（v2.4 设计 §3.2.2）。

覆盖：
- trigger_id_v1 与历史 trigger_id_of 完全等价（向后兼容黄金值）；
- trigger_id_v2 跨世代（不同 price_source/source_generation）生成不同 ID；
- 同世代内 v2 幂等（相同输入同一 ID）；
- v1 与 v2 对相同业务输入产生不同 ID（防跨世代冲突）；
- alert_id_v2 跨世代不冲突；
- TriggerRecord.trigger_id_version 默认 1，可显式设 2；
- QFQOrchestratorConfig.source_generation/cutover_id 默认值保守（不自动进 mcp-gen1）；
- 旧配置文件（不含新字段）仍可解析；
- MCP 世代需显式传 source_generation。
"""
from __future__ import annotations

import pytest

from quantstudio.pipeline.qfq_orchestrator_types import (
    QFQConfigError, QFQOrchestratorConfig, TriggerRecord,
    trigger_id_of, trigger_id_v1, trigger_id_v2, alert_id_v2,
    TRIGGER_ID_VERSION,
)


class TestTriggerIdVersions:
    def test_v1_equals_legacy_of(self):
        """v1 与历史 trigger_id_of 完全等价（黄金值）。"""
        for at, code, eff, ds, ph in [
            ("STOCK", "600000", 20260108, "stock_dividend", "abc"),
            ("ETF", "510050", 20260108, "tushare_fund_adj", "def"),
        ]:
            assert trigger_id_v1(at, code, eff, ds, ph) == trigger_id_of(at, code, eff, ds, ph)

    # ---- 固定黄金常量（防两函数同时被改仍通过，v2.4 B-2.1 阻断 1）----
    def test_v1_fixed_golden_stock(self):
        assert trigger_id_v1(
            "STOCK", "600000", 20260108, "stock_dividend", "abc"
        ) == "e9c69c65871b1ee306fdd491510e3a7c04ced44a"

    def test_v1_fixed_golden_etf(self):
        assert trigger_id_v1(
            "ETF", "510050", 20260108, "tushare_fund_adj", "def"
        ) == "6135f1daf38440d477d2c07d4335e7a11e28ad3f"

    def test_v2_fixed_golden(self):
        assert trigger_id_v2(
            "STOCK", "600000", 20260108, "stock_dividend", "abc", "mcp", "mcp-gen1"
        ) == "67b6c96665c06aaa4ff06a8c29bfc064059ec172"

    def test_v2_idempotent_within_generation(self):
        """同世代内 v2 幂等：相同输入同一 ID。"""
        a = trigger_id_v2("STOCK", "600000", 20260108, "stock_dividend", "abc", "mcp", "mcp-gen1")
        b = trigger_id_v2("STOCK", "600000", 20260108, "stock_dividend", "abc", "mcp", "mcp-gen1")
        assert a == b

    def test_v2_differs_across_generations(self):
        """同一分红事件跨世代生成不同 ID（核心：防 INSERT OR IGNORE 跨世代跳过）。"""
        xt = trigger_id_v2("STOCK", "600000", 20260108, "stock_dividend", "abc",
                           "xtquant", "xtquant-legacy")
        mcp = trigger_id_v2("STOCK", "600000", 20260108, "stock_dividend", "abc",
                            "mcp", "mcp-gen1")
        assert xt != mcp

    def test_v2_differs_across_price_source(self):
        """同 generation 不同 price_source 也不同（price_source 进 ID）。"""
        a = trigger_id_v2("STOCK", "600000", 20260108, "stock_dividend", "abc",
                          "xtquant", "gen1")
        b = trigger_id_v2("STOCK", "600000", 20260108, "stock_dividend", "abc",
                          "mcp", "gen1")
        assert a != b

    def test_v1_differs_from_v2_same_business(self):
        """相同业务输入，v1 与 v2 产生不同 ID（v2 有 'v2|' 前缀 + 世代后缀）。"""
        v1 = trigger_id_v1("STOCK", "600000", 20260108, "stock_dividend", "abc")
        v2 = trigger_id_v2("STOCK", "600000", 20260108, "stock_dividend", "abc",
                           "xtquant", "xtquant-legacy")
        assert v1 != v2

    def test_v2_payload_change_changes_id(self):
        """payload 变化（业务修订）→ ID 变化（发现新事件）。"""
        a = trigger_id_v2("STOCK", "600000", 20260108, "stock_dividend", "hash_a",
                          "mcp", "mcp-gen1")
        b = trigger_id_v2("STOCK", "600000", 20260108, "stock_dividend", "hash_b",
                          "mcp", "mcp-gen1")
        assert a != b

    def test_trigger_id_version_constant(self):
        assert TRIGGER_ID_VERSION == 2


class TestAlertIdV2:
    def test_v2_idempotent_within_generation(self):
        a = alert_id_v2("STOCK", "600000", 20260108, 1, "mcp-gen1")
        b = alert_id_v2("STOCK", "600000", 20260108, 1, "mcp-gen1")
        assert a == b

    def test_v2_differs_across_generations(self):
        """跨世代 alert_id 不冲突（防 MCP 伪 revision 与旧 xtquant alert 撞 ID）。"""
        xt = alert_id_v2("STOCK", "600000", 20260108, 1, "xtquant-legacy")
        mcp = alert_id_v2("STOCK", "600000", 20260108, 1, "mcp-gen1")
        assert xt != mcp

    def test_v2_differs_across_revision_no(self):
        """同世代不同 revision_no → 不同 ID。"""
        a = alert_id_v2("STOCK", "600000", 20260108, 1, "mcp-gen1")
        b = alert_id_v2("STOCK", "600000", 20260108, 2, "mcp-gen1")
        assert a != b

    def test_v2_fixed_golden(self):
        """固定黄金常量（v2.4 B-2.1 阻断 1）。"""
        assert alert_id_v2(
            "STOCK", "600000", 20260108, 1, "mcp-gen1"
        ) == "57e2bb50d65bc712a168dad33e6da54544fa44ff"


class TestTriggerRecordVersion:
    def test_default_version_is_1(self):
        """TriggerRecord.trigger_id_version 默认 1（向后兼容历史行）。"""
        r = TriggerRecord(trigger_id="x", asset_type="STOCK", code="600000",
                          trigger_type="stock_dividend", detection_source="stock_dividend")
        assert r.trigger_id_version == 1

    def test_can_set_version_2(self):
        """MCP 生产路径生成 TriggerRecord 时显式传 version=2。"""
        r = TriggerRecord(trigger_id="x", asset_type="STOCK", code="600000",
                          trigger_type="stock_dividend", detection_source="stock_dividend",
                          trigger_id_version=2)
        assert r.trigger_id_version == 2

    def test_version_in_cols_b3a(self):
        """trigger_id_version 在 B-3a 阶段已进 COLS/as_insert_params（schema 列已就绪）。

        v2.4 B-3a 持久化契约（修订 B-2.1）：新库 2.1 契约启用后，trigger_id_version
        列已存在（INTEGER NOT NULL），故 legacy/pre-cutover trigger 显式持久化 version=1；
        B-5 MCP v2 路径显式写 version=2。COLS/as_insert_params 现包含该列。
        """
        assert "trigger_id_version" in TriggerRecord.COLS
        r = TriggerRecord(trigger_id="x", asset_type="STOCK", code="600000",
                          trigger_type="stock_dividend", detection_source="stock_dividend",
                          trigger_id_version=2)
        params = r.as_insert_params()
        assert 2 in params  # version 现在在插入参数里


class TestConfigGenerationFields:
    def test_defaults_are_conservative(self):
        """默认 source_generation/cutover_id 保守，不自动进 mcp-gen1。"""
        cfg = QFQOrchestratorConfig()
        assert cfg.source_generation == "xtquant-legacy"
        assert cfg.cutover_id == "legacy-xtquant-pre-cutover"

    def test_legacy_config_parses_without_new_fields(self):
        """旧配置文件（不含 source_generation/cutover_id）仍可解析，用默认值。"""
        cfg = QFQOrchestratorConfig.from_dict({
            "enabled": True, "price_source": "xtquant",
            # 无 source_generation / cutover_id
        })
        assert cfg.source_generation == "xtquant-legacy"
        assert cfg.cutover_id == "legacy-xtquant-pre-cutover"
        assert cfg.price_source == "xtquant"

    def test_mcp_generation_explicit_field_parses(self):
        """MCP 世代显式传 source_generation/cutover_id 可正常解析（预切换哨兵语义）。

        注：B-2 阶段解析器允许旧配置回退到 pre-cutover legacy 哨兵（price_source=mcp +
        source_generation=xtquant-legacy）。B-6 激活 MCP cutover 时才拒绝 legacy 哨兵，
        要求 generation/cutover 与 active cutover 一致。本测试只证明"显式值可解析"。
        """
        cfg = QFQOrchestratorConfig.from_dict({
            "enabled": True, "price_source": "mcp",
            "source_generation": "mcp-gen1", "cutover_id": "cut_abc",
        })
        assert cfg.source_generation == "mcp-gen1"
        assert cfg.cutover_id == "cut_abc"

    def test_mcp_legacy_sentinel_allowed_in_b2(self):
        """B-2 允许 price_source=mcp + legacy 哨兵（预切换状态，避免改变当前配置行为）。

        B-6 cutover 激活时才拒绝此组合。
        """
        cfg = QFQOrchestratorConfig.from_dict({"enabled": True, "price_source": "mcp"})
        assert cfg.price_source == "mcp"
        assert cfg.source_generation == "xtquant-legacy"
        assert cfg.cutover_id == "legacy-xtquant-pre-cutover"


class TestConfigIdentifierFailFast:
    """v2.4 B-2.1 阻断 2：generation/cutover 标识符 fail-fast 校验。"""

    @pytest.mark.parametrize("bad_value", ["", "   ", "None", "none", "NONE"])
    def test_empty_or_none_string_rejected(self, bad_value):
        """空串/空白/'None' 字面量被拒绝。"""
        with pytest.raises(QFQConfigError):
            QFQOrchestratorConfig.from_dict({"source_generation": bad_value})
        with pytest.raises(QFQConfigError):
            QFQOrchestratorConfig.from_dict({"cutover_id": bad_value})

    def test_none_value_rejected(self):
        """显式 None 被拒绝（不再被 str() 转成 'None'）。"""
        with pytest.raises(QFQConfigError):
            QFQOrchestratorConfig.from_dict({"source_generation": None})
        with pytest.raises(QFQConfigError):
            QFQOrchestratorConfig.from_dict({"cutover_id": None})

    @pytest.mark.parametrize("bad_type", [123, 1.5, [], {}, True])
    def test_non_string_rejected(self, bad_type):
        """非字符串类型被拒绝。"""
        with pytest.raises(QFQConfigError):
            QFQOrchestratorConfig.from_dict({"source_generation": bad_type})

    def test_legacy_sentinel_passes(self):
        """正常 legacy 哨兵通过。"""
        cfg = QFQOrchestratorConfig.from_dict({})
        assert cfg.source_generation == "xtquant-legacy"
        assert cfg.cutover_id == "legacy-xtquant-pre-cutover"

    def test_mcp_generation_passes(self):
        """正常 MCP generation/cutover 通过。"""
        cfg = QFQOrchestratorConfig.from_dict({
            "source_generation": "mcp-gen1", "cutover_id": "cut_abc123",
        })
        assert cfg.source_generation == "mcp-gen1"
        assert cfg.cutover_id == "cut_abc123"

    def test_whitespace_stripped(self):
        """带空白的合法值 strip 后通过。"""
        cfg = QFQOrchestratorConfig.from_dict({
            "source_generation": "  mcp-gen1  ", "cutover_id": "  cut_abc  ",
        })
        assert cfg.source_generation == "mcp-gen1"
        assert cfg.cutover_id == "cut_abc"

    def test_invalid_price_source_still_rejected(self):
        """price_source 非法仍 fail-fast（既有校验不回归）。"""
        with pytest.raises(QFQConfigError):
            QFQOrchestratorConfig.from_dict({"price_source": "bogus"})

    def test_can_coordinate_watermark_unchanged(self):
        """既有 can_coordinate_watermark 行为不受新字段影响。"""
        cfg = QFQOrchestratorConfig(enabled=True, source_generation="mcp-gen1")
        assert cfg.can_coordinate_watermark("etf_daily") is True
        assert cfg.can_coordinate_watermark("stock_basic") is False
