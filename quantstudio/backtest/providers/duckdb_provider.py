from __future__ import annotations

import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .base import (CalendarProvider, FundamentalDataProvider,
                   MarketDataProvider, ReferenceDataProvider)
from .duckdb_data_access import DuckDBDataAccess
from ..libs.security_code_rules import exchange as security_exchange


def _start_ms(date: str) -> int:
    return int(pd.Timestamp(date, tz='Asia/Shanghai').timestamp() * 1000)


def _end_ms(date: str) -> int:
    return _start_ms(date) + 86_399_999


def _fields(df: pd.DataFrame, fields: Optional[List[str]]) -> pd.DataFrame:
    if not fields or df.empty:
        return df
    columns = [field for field in fields if field in df.columns]
    if 'code' in df.columns and 'code' not in columns:
        columns.insert(0, 'code')
    return df[columns] if columns else df


class DuckDBMarketDataProvider(MarketDataProvider):
    def __init__(self, db_path: Path, calendar_provider=None):
        self._data = DuckDBDataAccess(db_path)
        # PR3: 分钟查询需 calendar 枚举交易日（生成时段窗口）。可后置注入。
        self._calendar = calendar_provider
    def set_calendar(self, calendar_provider):
        """PR3: 后置注入 calendar（registry 组装时 market 先建，calendar 后建）。"""
        self._calendar = calendar_provider
    def preload(self, start_date, end_date): self._data.preload_daily_bars(end_date)
    def get_daily_snapshot(self, date, fields=None):
        return _fields(self._data.query_daily_snapshot(_start_ms(date)), fields)
    def get_bars(self, codes, start_date, end_date, fields=None, fq=None, frequency="1d",
                 bar_cutoff_ms=None):
        # PR3: frequency="1d"（默认）走原日线路径，字节级不变。
        if frequency == "1d":
            result = {}
            for code in codes:
                df = self._data.query_bars_by_range(code, _start_ms(start_date), _end_ms(end_date))
                if not df.empty: result[code] = _fields(df, fields)
            return result
        # 分钟路径：绝不进入日线 fallback 链
        from .frequency_labels import api_to_storage, FrequencyCapabilityError
        storage_freq = api_to_storage(frequency)
        result = {}
        for code in codes:
            df = self._data.query_minute_bars_by_range(
                code, start_date, end_date, storage_freq, fq, self._calendar,
                bar_cutoff_ms=bar_cutoff_ms)   # PR4 缺口 1
            if not df.empty: result[code] = _fields(df, fields)
        return result
    def get_bars_by_count(self, codes, count, end_date, fields=None, fq=None, frequency="1d",
                          bar_cutoff_ms=None):
        # PR3: frequency="1d"（默认）走原日线路径，字节级不变。
        if frequency == "1d":
            result = {}
            for code in codes:
                df = self._data.query_bars_by_count_multi_table(
                    code, count, _end_ms(end_date), use_qfq=str(fq).lower() in ('pre', 'dypre'))
                if not df.empty: result[code] = _fields(df, fields)
            return result
        # 分钟路径
        from .frequency_labels import api_to_storage
        storage_freq = api_to_storage(frequency)
        result = {}
        for code in codes:
            df = self._data.query_minute_bars_by_count(
                code, count, end_date, storage_freq, fq, self._calendar,
                bar_cutoff_ms=bar_cutoff_ms)   # PR4 缺口 1
            if not df.empty: result[code] = _fields(df, fields)
        return result
    def get_snapshot(self, date_or_dt, frequency="1d", fields=None):
        """PR3 补齐 5：日线快照（frequency="1d"）。分钟快照留待 PR4 引擎提供 _current_minute_data。"""
        from .frequency_labels import FrequencyCapabilityError, ERR_TABLE_EMPTY
        if frequency != "1d":
            raise FrequencyCapabilityError(
                ERR_TABLE_EMPTY, api_freq=frequency,
                detail="minute snapshot requires PR4 engine (current_bar 数据)")
        date_str = str(date_or_dt)[:10]
        return _fields(self._data.query_daily_snapshot(_start_ms(date_str)), fields)
    def get_benchmark(self, code, start_date, end_date):
        df = self._data.query_benchmark(code, _start_ms(start_date), _end_ms(end_date))
        if df.empty: return {}
        dates = pd.to_datetime(df['time'], unit='ms', utc=True).dt.tz_convert(
            'Asia/Shanghai').dt.strftime('%Y-%m-%d')
        return dict(zip(dates, df['close']))


class DuckDBFundamentalDataProvider(FundamentalDataProvider):
    def __init__(self, db_path: Path): self._data = DuckDBDataAccess(db_path)
    def preload(self, date): self._data.preload_fundamentals_pit(_end_ms(date))
    def get_valuation(self, codes, date, fields=None):
        query_ms = _end_ms(date)
        df = self._data.get_fundamentals_from_preload(codes, fields)
        if df is not None and not df.empty:
            df = df.copy()
            df.index = [str(code).split('.')[0] for code in df.index]
            return df
        df = self._data.query_valuation_daily_pit(codes, query_ms)
        if df.empty: df = self._data.query_valuation_monthly_fallback(codes, query_ms)
        if df.empty: return pd.DataFrame(columns=self.FUND_TABLES['valuation']).set_index('code')
        shares = self._data.query_total_share(codes, query_ms)
        if not shares.empty: df = df.merge(shares[['code', 'total_share']], on='code', how='left')
        df['pe_ratio_lyr'] = df['pe_ratio']
        df = df.set_index('code')
        if fields:
            selected = [field for field in fields if field in df.columns]
            if selected: df = df[selected]
        return df
    def get_valuation_query(self, filters, order_by, limit, date, fields):
        df = self._data.query_valuation_orm(_end_ms(date))
        for condition in filters:
            field, operator, value = condition['field'], condition['op'], condition['value']
            if field not in df.columns: continue
            if operator == '>=': df = df[df[field] >= value]
            elif operator == '<=': df = df[df[field] <= value]
            elif operator == '>': df = df[df[field] > value]
            elif operator == '<': df = df[df[field] < value]
            elif operator == '==': df = df[df[field] == value]
        for ordering in order_by:
            if ordering['field'] in df.columns:
                df = df.sort_values(ordering['field'], ascending=ordering['direction'] == 'asc')
        if limit: df = df.head(limit)
        selected = [field for field in fields if field in df.columns]
        return df[selected].reset_index(drop=True) if selected else df.reset_index(drop=True)
    def get_financial(self, codes, table, date, fields=None, start_year=None,
                      end_year=None, report_types=None):
        if table not in ('eps', 'profit_ability', 'growth_ability'):
            return pd.DataFrame(columns=self.FUND_TABLES.get(table, []))
        df = self._data.query_fin_indicator(codes, _end_ms(date), start_year, end_year, report_types)
        if df.empty: return pd.DataFrame(columns=self.FUND_TABLES.get(table, []))
        df = df.set_index('code')
        if fields:
            selected = [field for field in fields if field in df.columns]
            if selected: df = df[selected]
        return df


class DuckDBReferenceDataProvider(ReferenceDataProvider):
    def __init__(self, db_path: Path): self._data = DuckDBDataAccess(db_path)
    def preload(self):
        if self._data._preload_listing is None:
            self._data._preload_listing = self._data.query_listing_dates()
    def get_index_constituents(self, index_code, date=None):
        df = self._data.query_index_constituents(index_code)
        return df['code'].astype(str).tolist() if not df.empty else []
    def get_all_stocks(self, date=None):
        df = self._data.query_all_stocks(_end_ms(date) if date else 2**63 - 1)
        return df['code'].astype(str).tolist() if not df.empty else []
    def get_security_info(self, code):
        self.preload()
        listing_ms = self._data.query_security_info_from_preload(code)
        if not listing_ms: return None
        return {'code': code,
                'start_date': pd.Timestamp(listing_ms, unit='ms', tz='Asia/Shanghai').to_pydatetime(),
                'display_name': code}
    def get_industry(self, code):
        row = self._data.query_sw_industry(code)
        return ({'sw_l1': {'industry_code': row[0], 'industry_name': row[1]}} if row else None)
    def get_stock_status(self, codes, date):
        source = self._data.query_daily_for_status(_start_ms(date))
        rows = []
        for code in codes:
            matches = source[source['code'] == code] if 'code' in source.columns else pd.DataFrame()
            if matches.empty:
                rows.append({'code': code, 'is_st': False, 'is_halt': False,
                             'is_delisting_risk': False, 'is_delisted': True,
                             'preClose': None, 'close': None})
                continue
            row = matches.iloc[0]
            rows.append({'code': code, 'is_st': bool(row.get('is_st_reliable', False)),
                         'is_st_reliable_source': row.get('is_st_reliable_source', 'none'),
                         'is_halt': bool(row.get('suspendFlag', 0) == 1 or row.get('volume', 0) == 0),
                         'is_delisting_risk': bool(row.get('is_delisting_risk', False)),
                         'is_delisting_risk_source': row.get('is_delisting_risk_source', 'none'),
                         'is_delisted': False, 'preClose': row.get('preClose'),
                         'close': row.get('close')})
        return pd.DataFrame(rows)
    def get_etf_list(self):
        df = self._data.query_etf_list_active()
        return df['code'].astype(str).tolist() if not df.empty else []
    def get_etf_info(self, codes):
        return {code: {'etf_redemption_code': code, 'publish': 0, 'report_unit': 1000000,
                       'cash_balance': 0.0, 'max_cash_ratio': 0.0, 'pre_cash_component': 0.0,
                       'nav_percu': 0.0, 'nav_pre': 0.0, 'allot_max': 0.0, 'redeem_max': 0.0}
                for code in codes}
    def get_cb_list(self):
        df = self._data.query_cb_list_active()
        return df['code'].astype(str).tolist() if not df.empty else []
    def get_strategy_events(self, event_type, effective_date=None, start_date=None,
                            end_date=None, codes=None):
        return self._data.query_strategy_events(
            event_type, effective_date=effective_date, start_date=start_date,
            end_date=end_date, codes=codes)

    def get_market_detail(self, mic):
        mic = str(mic).upper()
        aliases = {
            'XSHG': 'SH', 'SS': 'SH', 'SH': 'SH',
            'XSHE': 'SZ', 'SZ': 'SZ',
            'XBJ': 'BJ', 'BJ': 'BJ', 'XBSE': 'BJ',
        }
        if mic == 'CSI':
            df = self._data.query_market_detail('index_daily', '1=1')
        elif mic in aliases:
            df = self._data.query_market_detail('stock_daily', '1=1')
            if 'code' in df.columns:
                target = aliases[mic]
                df = df[df['code'].astype(str).map(security_exchange) == target]
        else:
            return pd.DataFrame(columns=[
                'hq_type_code', 'prod_code', 'prod_name', 'trade_time_rule'])
        codes = df['code'] if 'code' in df.columns else []
        return pd.DataFrame({'hq_type_code': 'MRI', 'prod_code': codes,
                             'prod_name': codes, 'trade_time_rule': 0})


class DuckDBCalendarProvider(CalendarProvider):
    def __init__(self, db_path: Path): self._data = DuckDBDataAccess(db_path)
    def get_trade_days(self, start_date, end_date):
        # 严格按结束日闭区间取交易日，避免把下一交易日（如 2026-07-14）误纳入 2026-07-13 的回测区间。
        df = self._data.query_trade_days_range(_start_ms(start_date), _end_ms(end_date))
        return [pd.Timestamp(value, unit='ms', tz='Asia/Shanghai').to_pydatetime()
                for value in df.get('time', [])]
    def get_trading_day(self, date, offset=0):
        df = self._data.query_trade_day_offset(_start_ms(date), offset)
        if df.empty: return None
        value = df.iloc[-1, 0] if offset > 0 else df.iloc[0, 0]
        return pd.Timestamp(value, unit='ms', tz='Asia/Shanghai').date()
    def get_all_trade_days(self):
        df = self._data.query_trade_days_range(0, 2**63 - 1)
        if df.empty: return []
        return pd.to_datetime(df['time'], unit='ms', utc=True).dt.tz_convert(
            'Asia/Shanghai').dt.strftime('%Y-%m-%d').tolist()
    def get_kline_count(self, date): return self._data.query_kline_count(_end_ms(date))
