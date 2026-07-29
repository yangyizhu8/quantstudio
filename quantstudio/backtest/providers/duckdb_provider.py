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
    def preload(self, start_date, end_date):
        """回测前批量预加载数据（纯性能优化钩子）。

        由引擎在日线循环前以全期 [start_date, end_date] 调用一次，或由 ptrade_api 的
        每日 attach_day(prev_date, prev_date) 触发。实现幂等：全期预取完成后后续调用
        直接跳过，避免重复全表扫描。语义不变：query_daily_snapshot 命中内存后返回与
        单日查询字节级一致的结果。
        """
        # 归一化日期参数：兼容 'YYYY-MM-DD' 字符串、naive/tz-aware Timestamp，
        # 避免 _start_ms 对 tz-aware Timestamp 直接传 tz 报错导致预取被静默跳过。
        self._data.preload_daily_snapshots(
            _start_ms(str(start_date)[:10]),
            _end_ms(str(end_date)[:10]),
        )
    def get_daily_snapshot(self, date, fields=None):
        return _fields(self._data.query_daily_snapshot(_start_ms(date)), fields)
    def get_bars(self, codes, start_date, end_date, fields=None, fq='pre', frequency="1d",
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
    def get_bars_by_count(self, codes, count, end_date, fields=None, fq='pre', frequency="1d",
                          bar_cutoff_ms=None):
        # PR3: frequency="1d"（默认）走原日线路径，字节级不变。
        if frequency == "1d":
            # 阶段1 批量化：单条/少量批量 SQL 取代 N 次单码 SQL，与逐只调用字节级等价
            use_qfq = str(fq).lower() in ('pre', 'dypre')
            batch = self._data.query_bars_by_count_batch(codes, count, _end_ms(end_date), use_qfq)
            return {c: _fields(df, fields) for c, df in batch.items()}
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
    def __init__(self, db_path: Path):
        self._data = DuckDBDataAccess(db_path)
        # 惰性全量证券元数据缓存（性能优化，语义等价）。
        # get_security_info 原对每个 code 调 query_security_metadata(codes=[bare])，
        # 全市场 N+1（get_stock_info 循环逐码）。改为首次全量载入一次，
        # 后续内存过滤。query_security_metadata 本身不动（仍支持批量/无参）。
        self._security_meta_df = None
        # 阶段 2.5：{bare: 子DataFrame} 字典（首次填充时构建），消除 get_security_info 内
        # 逐只 df[df['code']==bare] 的 O(N) 布尔扫描（get_stock_info 全市场万次调用→新 N+1）。
        self._security_meta_by_code = None
        # 阶段 B(b)：{ms: datetime} memoization 缓存。pd.Timestamp(ms, unit='ms',
        # tz='Asia/Shanghai') 的时区转换每次 ~1ms，get_security_info 万次调用累计 ~10s。
        # 同一 listing_ms 在全市场多只跨天重复（同一股票 2 天各调一次）→ O(1) 查表。
        # 纯 memoization：同 ms 同输出，返回类型/值/时区逐值不变。
        self._ms_dt_cache: dict = {}
    def preload(self):
        if self._data._preload_listing is None:
            self._data._preload_listing = self._data.query_listing_dates()
            # 阶段 2.5：_preload_listing 重新载入时同步失效其 O(1) 索引字典。
            self._data._preload_listing_by_code = None
    def get_index_constituents(self, index_code, date=None):
        """指数成分 PIT 查询（F3）。

        - ``date`` 显式传入：严格 as-of（不晚于该日的最近完整快照，无则空）；
        - ``date=None``（Provider 直接调用）：保留"最新快照"兼容行为（文档化）。
          回测路径由 ptrade_api 注入当前回测日期，绝不落入此分支。
        """
        bare = str(index_code).strip().upper().split('.')[0]
        as_of_ms = _end_ms(str(date)[:10]) if date else None
        df = self._data.query_index_constituents(bare, as_of_ms)
        return df['code'].astype(str).tolist() if not df.empty else []
    def get_all_stocks(self, date=None):
        df = self._data.query_all_stocks(_end_ms(date) if date else 2**63 - 1)
        return df['code'].astype(str).tolist() if not df.empty else []
    def get_security_info(self, code):
        """证券元数据（F2 修订版）：股票行为与修复前一致，仅扩展 ETF。

        - ETF（etf_basic 元数据成员）：返回真实名称、'etf' 类型、etf_basic
          上市/退市日；list_date 缺失时按 etf_basic 管线契约用首个 etf_daily
          交易日补齐并在 data_source 标记 'etf_daily_min_fallback'；
        - 股票及其他代码：**修复前行为** —— start_date=stock_daily 首根K线、
          display_name=入参代码；新增键（end_date/security_type/exchange/
          data_source）为附加键，不影响既有消费方；
        - 均无记录返回 None（既有兼容行为）。
        """
        self.preload()
        bare = str(code).split('.')[0]
        # 惰性全量载入证券元数据（首次），后续内存过滤。等价于原
        # query_security_metadata(codes=[bare])：单码 ORDER BY 无排序影响，
        # 列/行/类型字节级一致（SECURITY_METADATA_COLUMNS 固定列顺序）。
        if self._security_meta_df is None:
            self._security_meta_df = self._data.query_security_metadata()
            # 阶段 C：每个 code 在 stock_basic∪etf_basic 中唯一 1 行（实测 7650 个 code 全单行，
            # 无跨 stock/etf 表重复）。故字典直接存 g.iloc[0].to_dict() 单行字典，消除后续
            # get_security_info 内 meta[meta['security_type']=='etf'] 等的逐次单行子表布尔索引
            # （pandas 在 10087 次调用上累计 ~10s）。g.iloc[0] 即该唯一行，与原 df[df['code']==bare]
            # 取到的子表 .iloc[0] 字节级等价。groupby(sort=False) 仅保序，单行场景无影响。
            self._security_meta_by_code = {
                c: g.iloc[0].to_dict() for c, g in self._security_meta_df.groupby('code', sort=False)
            }
        # 阶段 C：字典直接存单行字典 → O(1) 取行，按 security_type 决定 ETF/股票分支，
        # 不再对单行子表做 meta[meta['security_type']=='etf'] 布尔索引（等价 etf_rows.iloc[0]）。
        row = self._security_meta_by_code.get(bare)
        etf_row = row if (row is not None and row.get('security_type') == 'etf') else None
        if etf_row is not None:
            listing_ms = self._data.query_security_info_from_preload(bare)
            # 阶段 2.5：复用 _preload_listing_by_code 字典（query_security_info_from_preload
            # 首次调用时已惰性构建），消除原 preload[preload['code']==bare] 的逐只 O(N) 扫描。
            # 等价原 `len(rows)>0 and rows.iloc[0]['listing_time']` 判定后才取 listing_source。
            listing_source = None
            entry = (self._data._preload_listing_by_code.get(bare)
                     if self._data._preload_listing_by_code is not None else None)
            if entry is not None and entry[0]:
                listing_source = entry[1]
            delist = etf_row.get('delist_date')
            info = {
                'code': code,
                'start_date': self._ms_to_pydatetime(listing_ms),
                'display_name': (etf_row['name']
                                 if etf_row.get('name') is not None and pd.notna(etf_row.get('name'))
                                 else code),
                'security_type': 'etf',
                'exchange': etf_row.get('exchange'),
                'end_date': (self._ms_to_pydatetime(delist)
                             if delist is not None and pd.notna(delist) else None),
                'data_source': etf_row.get('data_source'),
            }
            # fallback 来源必须显式标记，覆盖正式 data_source 声明
            if listing_source and str(listing_source).endswith('_fallback'):
                info['data_source'] = listing_source
            return info
        # 股票/其他：修复前行为（start_date=stock_daily 首根K线，display_name=入参代码）
        listing_ms = self._data.query_security_info_from_preload(bare)
        if not listing_ms:
            return None
        return {'code': code,
                'start_date': self._ms_to_pydatetime(listing_ms),
                'display_name': code,
                'security_type': 'stock',
                'exchange': None,
                'end_date': None,
                'data_source': None}
    def get_industry(self, code, date=None):
        """正式行业归属 PIT 查询（F4）。

        - ``date`` 显式传入：严格 as-of（effective_from <= d AND
          (effective_to IS NULL OR effective_to >= d)），无有效历史归属返回 None；
        - ``date=None``（Provider 直接调用）：返回当前有效归属（文档化兼容）；
        - 正式表缺失抛 ReferenceDataCapabilityError（fail-closed），
          绝不回退 legacy sw_industry 快照。
        """
        bare = str(code).split('.')[0]
        # 日期列均为 Asia/Shanghai 当日 00:00 ms，PIT 区间比较按日起点口径
        as_of_ms = _start_ms(str(date)[:10]) if date else None
        row = self._data.query_industry_membership(bare, as_of_ms)
        if row is None:
            return None
        return {'sw_l1': {
            'industry_code': row['industry_code'],
            'industry_name': row['industry_name'],
            'classification_system': row['classification_system'],
            'classification_version': row['classification_version'],
        }}
    def get_corporate_actions(self, date):
        return self._data.query_corporate_actions(_start_ms(str(date)[:10]))
    def get_exrights(self, code, date=None):
        """Return ex-rights info for a single code on a given date.

        Args:
            code: Security code (bare or PTrade format).
            date: Date string; if None the table is checked but no data is
                  returned (the underlying query requires a concrete date).

        Returns:
            DataFrame with PTrade-compatible columns (allotted_ps, bonus_ps,
            etc.) indexed by date, or None if no data.
        """
        if date is None:
            return None
        date_ms = _start_ms(str(date)[:10])
        bare = str(code).split(".")[0]
        return self._data.query_stock_exrights(bare, date_ms)
    def _ms_to_pydatetime(self, ms):
        # 阶段 B(b)：memoize。pd.Timestamp(ms, unit='ms', tz='Asia/Shanghai') 的时区转换
        # 每次 ~1ms，get_security_info 万次调用累计 ~10s。同一 listing_ms 在全市场多只
        # 跨天重复（同一股票 2 天各调一次）→ O(1) 查表。纯 memoization：同 ms 同输出，
        # 返回类型/值/时区逐值不变（下游依赖 pd.Timestamp/.date()）。
        if ms is None:
            return None
        cache = self._ms_dt_cache
        v = cache.get(ms)
        if v is None:
            v = pd.Timestamp(ms, unit='ms', tz='Asia/Shanghai').to_pydatetime()
            cache[ms] = v
        return v
    def get_stock_status(self, codes, date):
        source = self._data.query_daily_for_status(_start_ms(date))
        # 阶段 B(a)：一次性将全市场 source 预构建为 {code: 字段字典}，循环内 O(1) 取行，
        # 消除原 source[source['code']==code] 的 O(N²) 逐只布尔扫描（get_stock_status
        # 全市场调用时 cumtime ~14.7s）。每个 code 在当日快照中唯一（日线一行一码），
        # 故无需 iloc[0] 取首行语义；'code' 缺失回退空字典（等价原空匹配）。row.get(...)
        # 对 dict / Series 等价。
        if 'code' in source.columns:
            source_by_code = source.set_index('code').to_dict('index')
        else:
            source_by_code = {}
        rows = []
        for code in codes:
            row = source_by_code.get(code)
            if row is None:
                rows.append({'code': code, 'is_st': False, 'is_halt': False,
                             'is_delisting_risk': False, 'is_delisted': True,
                             'preClose': None, 'close': None})
                continue
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
    def get_etf_list_local(self, query_date, etf_type="equity", active_only=True):
        if query_date is None or not str(query_date).strip():
            raise ValueError("query_date is required at the provider layer")
        date_text = pd.Timestamp(query_date).strftime("%Y-%m-%d")
        df = self._data.query_etf_universe_pit(
            _start_ms(date_text), _end_ms(date_text),
            etf_type=etf_type, active_only=bool(active_only),
        )
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
