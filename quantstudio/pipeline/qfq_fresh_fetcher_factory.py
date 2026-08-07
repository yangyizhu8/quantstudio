"""QFQ fresh fetcher 共享工厂（daemon 与 CLI 共用，防漂移）。

v2.4 MCP cutover 设计 §3.1（P0-1）：消除 daemon（``daemon.py:227-233``）与 CLI
（``qfq_orchestrator_cli.py:194-195`` 硬编码 ``XtquantFreshFetcher``）的 fetcher 分支漂移。
两边都调本工厂，按 ``cfg.price_source`` 决定构造 ``McpFreshFetcher`` 还是
``XtquantFreshFetcher``。

MCP-only fail-fast 保证（v2.4 设计 §3.1）：``price_source == "mcp"`` 时**只**构造
``McpFreshFetcher``，不 import xtquant、不连接 QMT；capture/event/intent 的 source
字段必须与实际 fetcher 一致（"实际取 xtquant、元数据标 mcp" 禁止）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from quantstudio.pipeline.qfq_fresh_capture import FreshFetcher
from quantstudio.pipeline.qfq_orchestrator_types import QFQConfigError, QFQOrchestratorConfig

__all__ = ["build_qfq_fresh_fetcher", "load_sources_cfg"]


def load_sources_cfg(sources_dir) -> Dict:
    """从 ``sources_dir``（Path 或 str）读 ``sources_config.json``，兼容缺失返回 {}。

    daemon 已持有 sources_cfg dict 直接传入；CLI 从 ``--sources-dir`` 读文件。

    ``sources_dir=None``（CLI 未传 --config-dir/--sources-dir）时返回 {}，维持旧 CLI
    惰性 fetcher 行为（price_source=xtquant 无需 sources 文件）。price_source=mcp 时若
    MCP 配置缺失必要参数，由 ``build_qfq_fresh_fetcher``/adapter 初始化处 fail-fast
    给出明确配置错误，而非在此处静默。
    """
    import json
    from pathlib import Path
    if sources_dir is None:
        return {}
    p = Path(sources_dir) / "sources_config.json"
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_mcp_cfg(sources_cfg: Dict) -> Dict:
    """从 sources_cfg 取 mcp 块（兼容 ``sources.mcp`` 与裸 ``mcp`` 两种结构）。"""
    return (sources_cfg.get("sources", {}).get("mcp")
            or sources_cfg.get("mcp", {}))


def build_qfq_fresh_fetcher(cfg: QFQOrchestratorConfig,
                            sources_cfg: Optional[Dict],
                            main_db: Optional[str] = None) -> FreshFetcher:
    """按 ``cfg.price_source`` 构造 fresh fetcher（daemon 与 CLI 共用）。

    - ``price_source == "mcp"`` → ``McpFreshFetcher(mcp_cfg=...)``（惰性，首次取数才建 MCP 连接）
    - ``price_source == "xtquant"`` → ``XtquantFreshFetcher()``（惰性，首次取数才 import/连 xtquant）
    - 其它 → ``QFQConfigError``（fail-fast）

    MCP 模式不 import xtquant、不连接 QMT（``McpFreshFetcher`` 构造函数本身不触发任何连接）。
    """
    sources_cfg = sources_cfg or {}
    ps = cfg.price_source
    if ps == "mcp":
        from quantstudio.pipeline.qfq_fresh_capture import McpFreshFetcher
        mcp_cfg = _resolve_mcp_cfg(sources_cfg)
        return McpFreshFetcher(mcp_cfg=mcp_cfg, main_db=main_db)
    if ps == "xtquant":
        from quantstudio.pipeline.qfq_fresh_capture import XtquantFreshFetcher
        return XtquantFreshFetcher()
    raise QFQConfigError(f"不支持的 price_source: {ps!r}（合法值: xtquant | mcp）")
