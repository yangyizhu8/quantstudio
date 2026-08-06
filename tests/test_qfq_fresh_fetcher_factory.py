"""B-1 测试：fresh fetcher 共享工厂（v2.4 MCP cutover 设计 §3.1 P0-1）。

覆盖：
- MCP 模式构造 McpFreshFetcher、不 import XtquantFreshFetcher、不触发 QMT 连接；
- xtquant 模式仍返回 XtquantFreshFetcher；
- 非法 price_source 抛 QFQConfigError；
- sources.mcp 与裸 mcp 两种结构都能解析；
- sources_dir=None 兼容（返回 {}，维持旧 CLI 行为）；
- load_sources_cfg 路径不存在/JSON 损坏语义。
"""
from __future__ import annotations

import json
import sys

import pytest

from quantstudio.pipeline.qfq_fresh_fetcher_factory import (
    build_qfq_fresh_fetcher, load_sources_cfg, _resolve_mcp_cfg,
)
from quantstudio.pipeline.qfq_orchestrator_types import QFQConfigError, QFQOrchestratorConfig
from quantstudio.pipeline.qfq_fresh_capture import (
    McpFreshFetcher, XtquantFreshFetcher, FreshFetcher,
)


def _cfg(price_source: str) -> QFQOrchestratorConfig:
    return QFQOrchestratorConfig(price_source=price_source)


class TestFetcherRouting:
    def test_mcp_returns_mcp_fetcher(self):
        f = build_qfq_fresh_fetcher(_cfg("mcp"), {"sources": {"mcp": {"base_url": "x"}}})
        assert isinstance(f, McpFreshFetcher)
        assert isinstance(f, FreshFetcher)

    def test_mcp_does_not_import_xtquant_module(self):
        # import xtquant 是惰性的（首次取数才发生），构造 McpFreshFetcher 不应触发
        before = set(sys.modules)
        f = build_qfq_fresh_fetcher(_cfg("mcp"), {"sources": {"mcp": {"base_url": "x"}}})
        after = set(sys.modules)
        # 构造后不应新增 xtquant 相关模块
        xt_mods = {m for m in (after - before) if "xtquant" in m.lower()}
        assert not xt_mods, f"构造 McpFreshFetcher 不应 import xtquant，新增了: {xt_mods}"

    def test_mcp_does_not_connect_qmt(self):
        # McpFreshFetcher 构造惰性（_connected=False），不建任何连接
        f = build_qfq_fresh_fetcher(_cfg("mcp"), {"sources": {"mcp": {"base_url": "x"}}})
        assert getattr(f, "_connected", None) is False

    def test_xtquant_returns_xtquant_fetcher(self):
        f = build_qfq_fresh_fetcher(_cfg("xtquant"), {})
        assert isinstance(f, XtquantFreshFetcher)

    def test_xtquant_fetcher_lazy_no_connect(self):
        f = build_qfq_fresh_fetcher(_cfg("xtquant"), {})
        assert getattr(f, "_connected", None) is False

    def test_invalid_price_source_raises(self):
        with pytest.raises(QFQConfigError):
            build_qfq_fresh_fetcher(_cfg("invalid"), {})


class TestMcpCfgResolution:
    def test_sources_dot_mcp_structure(self):
        cfg_block = {"sources": {"mcp": {"base_url": "http://x", "enabled": True}}}
        assert _resolve_mcp_cfg(cfg_block) == {"base_url": "http://x", "enabled": True}

    def test_bare_mcp_structure(self):
        cfg_block = {"mcp": {"base_url": "http://y"}}
        assert _resolve_mcp_cfg(cfg_block) == {"base_url": "http://y"}

    def test_missing_mcp_block_returns_empty(self):
        assert _resolve_mcp_cfg({}) == {}
        assert _resolve_mcp_cfg({"sources": {}}) == {}

    def test_mcp_fetcher_uses_resolved_cfg(self):
        mcp_block = {"base_url": "http://z", "name": "mcp"}
        f = build_qfq_fresh_fetcher(_cfg("mcp"), {"sources": {"mcp": mcp_block}})
        assert f._mcp_cfg == mcp_block


class TestLoadSourcesCfg:
    def test_none_returns_empty(self, tmp_path):
        # CLI 未传 --config-dir/--sources-dir 时 sources_dir=None，兼容返回 {}
        assert load_sources_cfg(None) == {}

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_sources_cfg(tmp_path) == {}

    def test_reads_sources_config(self, tmp_path):
        (tmp_path / "sources_config.json").write_text(
            json.dumps({"sources": {"mcp": {"base_url": "http://x"}}}), encoding="utf-8")
        cfg = load_sources_cfg(tmp_path)
        assert cfg == {"sources": {"mcp": {"base_url": "http://x"}}}

    def test_corrupt_json_raises(self, tmp_path):
        (tmp_path / "sources_config.json").write_text("{not valid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_sources_cfg(tmp_path)
