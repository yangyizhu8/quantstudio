"""数据源适配器（Layer 1 模块①）

每个数据源一个 Adapter，统一接口只负责拉取，不做格式转换（格式转换归 FieldAligner）。
"""
from .base import BaseSourceAdapter
from .tushare_adapter import TushareAdapter
from .baostock_adapter import BaostockAdapter
from .akshare_adapter import AkshareAdapter
from .xtquant_adapter import XtquantAdapter
from .astockdata_adapter import AStockDataAdapter

__all__ = ["BaseSourceAdapter", "TushareAdapter", "BaostockAdapter", "AkshareAdapter",
           "XtquantAdapter", "AStockDataAdapter", "create_adapter"]


def create_adapter(source: str, config: dict) -> BaseSourceAdapter:
    """工厂方法：按 source 名创建 Adapter"""
    registry = {
        "tushare": TushareAdapter,
        "baostock": BaostockAdapter,
        "akshare": AkshareAdapter,
        "xtquant": XtquantAdapter,
        "a_stock_data": AStockDataAdapter,
    }
    if source not in registry:
        raise ValueError(f"未知数据源: {source}（已注册: {list(registry)})")
    return registry[source](config)
