"""数据源适配器（Layer 1 模块①）

每个数据源一个 Adapter，统一接口只负责拉取，不做格式转换（格式转换归 FieldAligner）。

数据源唯一化（2026-08-28 终态）：全项目唯一权威源 = MCP。
tushare/baostock/akshare/xtquant/a_stock_data 的 Adapter 文件**留盘停用**（可回溯），
但不再注册进工厂 registry——create_adapter 对非 mcp 源一律 raise。
"""
from .base import BaseSourceAdapter
from .mcp_adapter import MCPAdapter

__all__ = ["BaseSourceAdapter", "MCPAdapter", "create_adapter"]


def create_adapter(source: str, config: dict) -> BaseSourceAdapter:
    """工厂方法：按 source 名创建 Adapter。

    数据源唯一化：仅注册 mcp（唯一权威源）。若未来需要恢复某停用源，
    属显式运维决策——从 git 历史恢复本文件旧版 registry 并重新评估
    跨源复权基准一致性（2026-07-21 决策已被唯一化废止）。
    """
    registry = {
        "mcp": MCPAdapter,
    }
    if source not in registry:
        raise ValueError(
            f"未知数据源: {source}（数据源唯一化后仅支持: {list(registry)}；"
            "其他源已停用——如需恢复属显式运维决策，见本文件说明）")
    return registry[source](config)
