"""DataProvider 适配层 — 数据源可插拔

Phase 1：DuckDBDataAccess 把所有 SQL 查询集中到单一类。
后续 Phase 2/3 在此基础上定义抽象 Provider 接口与可插拔实现。
"""

from .base import (CalendarProvider, DataProviderRegistry,
                   FundamentalDataProvider, MarketDataProvider,
                   ReferenceDataProvider)

__all__ = ['MarketDataProvider', 'FundamentalDataProvider', 'ReferenceDataProvider',
           'CalendarProvider', 'DataProviderRegistry']
