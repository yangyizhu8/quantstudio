from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


class ReferenceDataCapabilityError(RuntimeError):
    """Raised when a requested reference-data contract is not available locally."""


class MarketDataProvider(ABC):
    @abstractmethod
    def preload(self, start_date: str, end_date: str) -> None: ...

    @abstractmethod
    def get_daily_snapshot(self, date: str,
                           fields: Optional[List[str]] = None) -> pd.DataFrame: ...

    @abstractmethod
    def get_bars(self, codes: List[str], start_date: str, end_date: str,
                 fields: Optional[List[str]] = None,
                 fq: Optional[str] = "pre",
                 frequency: str = "1d",
                 bar_cutoff_ms: Optional[int] = None) -> Dict[str, pd.DataFrame]: ...
        # PR3: frequency 默认 "1d" 走日线（向后兼容）；"1m"/"5m"/... 走分钟表。
        # PR4: bar_cutoff_ms 分钟 Profile 的当前 bar 截断（修正缺口 1，防未来 bar 泄漏）；
        #      None 时走 PR3 原逻辑（日级全天窗口）。末位默认值保证日线路径零触达。

    @abstractmethod
    def get_bars_by_count(self, codes: List[str], count: int, end_date: str,
                          fields: Optional[List[str]] = None,
                          fq: Optional[str] = "pre",
                          frequency: str = "1d",
                          bar_cutoff_ms: Optional[int] = None) -> Dict[str, pd.DataFrame]: ...

    @abstractmethod
    def get_snapshot(self, date_or_dt, frequency: str = "1d",
                     fields: Optional[List[str]] = None) -> pd.DataFrame: ...
        # PR3 补齐 5：抽象层补 get_snapshot 声明（主计划 7.17）。
        # frequency="1d" 返回日线快照；分钟快照留待 PR4 引擎提供 _current_minute_data。

    @abstractmethod
    def get_benchmark(self, code: str, start_date: str,
                      end_date: str) -> Dict[str, float]: ...


class FundamentalDataProvider(ABC):
    FUND_TABLES: Dict[str, List[str]] = {
        "valuation": ["code", "float_value", "a_floats", "total_value", "total_share",
                      "market_cap", "circulating_market_cap", "pe_ratio", "pe_ratio_lyr",
                      "pb_ratio", "ps_ratio", "pcf_ratio", "turnover_ratio"],
        "balance_statement": ["code", "end_date", "publ_date", "total_assets", "total_liability",
                              "total_equity", "total_parent_equity", "minority_interest",
                              "total_current_assets", "total_non_current_assets",
                              "total_current_liability", "total_non_current_liability",
                              "cash_equivalents", "account_receivable", "account_payable",
                              "inventory", "notes_payable", "advance_payment", "fixed_asset",
                              "intangible_asset", "goodwill"],
        "income_statement": ["code", "end_date", "publ_date", "operating_revenue", "operating_cost",
                             "operating_profit", "total_profit", "net_profit",
                             "np_parent_company_owners", "minority_profit", "total_operating_revenue",
                             "total_operating_cost", "operating_tax_surcharges", "sale_expense",
                             "manage_expense", "finance_expense", "rd_expense", "invest_income",
                             "non_operating_income", "non_operating_expense", "income_tax",
                             "basic_eps", "diluted_eps"],
        "cashflow_statement": ["code", "end_date", "publ_date", "net_operate_cash_flow",
                               "net_invest_cash_flow", "net_finance_cash_flow", "cash_add_balance",
                               "end_cash_and_equiv", "sale_services", "buy_services",
                               "goods_sale_and_services", "goods_buy_and_services", "invest_long_asset",
                               "invest_other", "fixed_asset_depreciation", "intangible_asset_amortization",
                               "debt_to_assets", "debt_paying_cash", "dividend_interest_payment"],
        "eps": ["code", "end_date", "publ_date", "eps", "bps", "diluted_eps",
                "total_asset_share", "deducted_eps", "operating_eps"],
        "profit_ability": ["code", "end_date", "publ_date", "roe", "roa", "roic",
                           "net_profit_margin", "gross_profit_margin", "operating_profit_margin",
                           "total_profit_net_profit", "expense_to_revenue", "operate_profit_to_profit",
                           "net_profit_to_balance", "roe_avg", "roa_avg"],
        "growth_ability": ["code", "end_date", "publ_date", "np_yoy", "or_yoy", "equity_yoy",
                           "netasset_yoy", "net_profit_5y_cagr", "operating_revenue_5y_cagr",
                           "total_assets_yoy", "deducted_np_yoy"],
        "operating_ability": ["code", "end_date", "publ_date", "accounts_receivable_turnover",
                              "inventory_turnover", "accounts_payable_turnover", "total_asset_turnover",
                              "current_asset_turnover", "fixed_asset_turnover", "equity_turnover",
                              "operating_cycle", "asset_turnover_days"],
        "debt_paying_ability": ["code", "end_date", "publ_date", "current_ratio", "quick_ratio",
                                "cash_ratio", "debt_to_assets", "asset_to_liability", "equity_multiplier",
                                "long_debt_to_assets", "long_debt_to_equity", "interest_protection_ratio",
                                "operating_cashflow_to_liability", "operating_cashflow_to_debt"],
    }

    @abstractmethod
    def preload(self, date: str) -> None: ...

    @abstractmethod
    def get_valuation(self, codes: List[str], date: str,
                      fields: Optional[List[str]] = None) -> pd.DataFrame: ...

    @abstractmethod
    def get_valuation_query(self, filters: List[dict], order_by: List[dict],
                            limit: Optional[int], date: str,
                            fields: List[str]) -> pd.DataFrame: ...

    @abstractmethod
    def get_financial(self, codes: List[str], table: str, date: str,
                      fields: Optional[List[str]] = None,
                      start_year: Optional[int] = None,
                      end_year: Optional[int] = None,
                      report_types: Optional[str] = None) -> pd.DataFrame: ...


class ReferenceDataProvider(ABC):
    @abstractmethod
    def preload(self) -> None: ...

    @abstractmethod
    def get_index_constituents(self, index_code: str,
                               date: Optional[str] = None) -> List[str]:
        """指数成分 PIT 查询（F3 契约）。

        ``date`` 显式传入（YYYY-MM-DD）：严格 as-of，只返回不晚于该日的
        最近完整快照；无历史快照返回空列表，绝不向未来 fallback、绝不返回
        历史并集。``date=None`` 仅限非回测直接调用：保留"最新快照"兼容行为；
        回测期间由 ptrade_api 注入当前回测日期，绝不使用数据库全局最新日期。
        """
        ...

    @abstractmethod
    def get_all_stocks(self, date: Optional[str] = None) -> List[str]: ...

    @abstractmethod
    def get_security_info(self, code: str) -> Optional[dict]: ...

    @abstractmethod
    def get_industry(self, code: str,
                     date: Optional[str] = None) -> Optional[dict]:
        """行业归属 PIT 查询（F4 契约）。

        ``date``（YYYY-MM-DD）显式传入：严格 as-of，无有效历史归属返回 None，
        绝不使用最新行业填充过去日期。``date=None`` 仅限非回测直接调用：
        返回当前有效归属。正式表缺失时抛 ReferenceDataCapabilityError
        （fail-closed），绝不回退 legacy sw_industry 快照。
        外部 PTrade 签名（ptrade_api.get_industry(code)）不变，由 API 层
        自动注入当前回测日期。
        """
        ...

    @abstractmethod
    def get_stock_status(self, codes: List[str], date: str) -> pd.DataFrame: ...

    @abstractmethod
    def get_etf_list(self) -> List[str]: ...

    @abstractmethod
    def get_etf_list_local(self, query_date: str, etf_type: str = "equity",
                           active_only: bool = True) -> List[str]: ...

    @abstractmethod
    def get_etf_info(self, codes: List[str]) -> dict: ...

    @abstractmethod
    def get_cb_list(self) -> List[str]: ...

    @abstractmethod
    def get_market_detail(self, mic: str) -> pd.DataFrame: ...

    def get_exrights(self, code: str, date: Optional[str] = None) -> Optional[pd.DataFrame]: return None
    def get_corporate_actions(self, date: str) -> pd.DataFrame:
        return pd.DataFrame(columns=["code", "cash_div", "stk_div", "div_rat"])
    def get_blocks(self, code: str) -> Optional[dict]: return None
    def get_industry_stocks(self, industry_code: str) -> List[str]: return []
    def get_etf_stock_list(self, etf_code: str) -> List[str]: return []
    def get_etf_stock_info(self, etf_code: str, security: str) -> dict: return {}
    def get_reits_list(self, date: Optional[str] = None) -> List[str]: return []
    def get_cb_info(self) -> pd.DataFrame:
        return pd.DataFrame(columns=['bond_code', 'bond_name', 'stock_code', 'stock_name',
                                     'list_date', 'premium_rate', 'convert_date', 'maturity_date',
                                     'convert_rate', 'convert_price', 'convert_value'])
    def get_ipo_stocks(self) -> dict: return {}
    def get_strategy_events(self, event_type: str, effective_date: Optional[str] = None,
                            start_date: Optional[str] = None,
                            end_date: Optional[str] = None,
                            codes: Optional[List[str]] = None) -> pd.DataFrame:
        return pd.DataFrame(columns=[
            "event_type", "event_date", "effective_date", "code", "signal",
            "name", "category", "source", "source_row_id", "source_key", "payload", "imported_at",
        ])


class CalendarProvider(ABC):
    @abstractmethod
    def get_trade_days(self, start_date: str, end_date: str) -> list: ...

    @abstractmethod
    def get_trading_day(self, date: str, offset: int = 0) -> 'datetime.date': ...

    @abstractmethod
    def get_all_trade_days(self) -> List[str]: ...

    @abstractmethod
    def get_kline_count(self, date: str) -> int: ...

    def diagnose(self) -> Optional[dict]:
        """返回该 provider 的数据范围诊断信息，用于错误信息细化。

        非抽象、带默认实现：基类默认返回 None（表示该 provider 不支持诊断，
        例如测试 mock），所有现有子类/测试桩零改动兼容。
        DuckDBCalendarProvider 覆写为返回 stock_daily 覆盖范围。
        仅在"无交易日"失败分支被引擎调用，不参与任何成功路径。
        """
        return None


@dataclass
class DataProviderRegistry:
    market: MarketDataProvider
    fundamental: FundamentalDataProvider
    reference: ReferenceDataProvider
    calendar: CalendarProvider

    @classmethod
    def from_duckdb(cls, db_path: Path) -> "DataProviderRegistry":
        from .duckdb_provider import (DuckDBCalendarProvider,
                                     DuckDBFundamentalDataProvider,
                                     DuckDBMarketDataProvider,
                                     DuckDBReferenceDataProvider)
        # PR3: calendar 先建，再注入到 market provider（分钟查询需枚举交易日生成时段窗口）
        calendar = DuckDBCalendarProvider(db_path)
        market = DuckDBMarketDataProvider(db_path, calendar_provider=calendar)
        return cls(market, DuckDBFundamentalDataProvider(db_path),
                   DuckDBReferenceDataProvider(db_path), calendar)
