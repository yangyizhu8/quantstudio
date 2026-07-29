"""任务2：每轮 post-ingest 前主动刷新股票 adj_factor 与 ETF fund_adj。

历史问题：事件发现只读已落库的因子快照，Tushare 因子晚到/修订时，旧快照会被
错误解释成"今天没有事件"。本模块在事件发现之前主动拉取最新因子，分别写入
adj_factor（股票）与 fund_adj（ETF），保证后续观察基于最新数据。

网络调用在数据库事务外；成功后由 QFQMaintenance 内部短事务写 SQLite。

degraded 契约（冲突点1 修复后的准确表述）：
- 某资产类别**全部逐码请求失败**（fetch_adj_factor 抛 FactorRefreshError）→ degraded=True；
- 正常返回空数据（区间内无复权事件）→ 不降级（degraded=False）；
- 部分代码失败（类内部分码成功、部分码失败）→ degraded=False，仍保留成功结果落库，
  失败码仅记录 warning。
已知残余风险：部分代码请求失败目前不触发全局 degraded，失败代码可能继续使用旧快照；
是否升级为"任意单码失败即 degraded"另立后续正确性变更审核。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class RefreshResult:
    stock_refreshed: int = 0
    etf_refreshed: int = 0
    stock_failed: int = 0
    etf_failed: int = 0
    error: Optional[str] = None
    degraded: bool = False


def _lookback_yyyymmdd(lookback_days: int) -> str:
    """回看起点（自然日 YYYYMMDD）。"""
    d = datetime.now() - timedelta(days=lookback_days)
    return d.strftime("%Y%m%d")


class QFQFactorRefresher:
    """任务2：主动刷新股票/ETF 复权因子。

    Args:
        aux_db: qfq_aux.db 路径（因子落库位置）。
    """

    def __init__(self, aux_db):
        self.aux_db = aux_db

    def refresh(
        self,
        adapter,
        stock_universe: Set[str],
        etf_universe: Set[str],
        *,
        overlap_days: int = 5,
        lookback_days: int = 365,
        rate_limiter: Optional[object] = None,
    ) -> RefreshResult:
        """刷新股票 adj_factor 与 ETF fund_adj。

        - 股票：fetch_adj_factor(is_etf=False) → adj_factor 表
        - ETF：fetch_adj_factor(is_etf=True) → fund_adj 表
        - 网络调用在事务外；成功后短事务写 SQLite。
        - degraded 契约：某资产类别**全部逐码请求失败**（fetch_adj_factor 抛
          FactorRefreshError）→ degraded=True；正常空数据不降级；部分码失败不降级
          （保留成功结果，失败码仅 warning）。
        - C3：调用 Tushare adj_factor/fund_adj 前，在各资产类别**自己的 try 块内**
          用 resolve_ts_codes 把裸码解析为 Tushare ts_code（元数据优先，前缀 fallback）。
          股票转换异常不影响 ETF（跨资产类别隔离性）。

        rate_limiter：参数仅为兼容保留（本次不再主动调用）。限流统一由
        fetch_adj_factor 内部逐码 adapter.rate_limiter.acquire() 负责，每码一次。
        """
        from quantstudio.pipeline.qfq_maintenance import QFQMaintenance, resolve_ts_codes

        res = RefreshResult()
        start = _lookback_yyyymmdd(lookback_days)
        m = QFQMaintenance(db_path=self.aux_db)
        # C3：从 aux_db 同目录推导 quantstudio.db（元数据表所在库）。
        # 不依赖当前工作目录；不存在则 resolve_ts_codes 内部 warning 后全量 fallback。
        main_db = Path(self.aux_db).resolve().parent / "quantstudio.db"

        if stock_universe:
            raw_codes = sorted(stock_universe)
            try:
                # C3：裸码 → Tushare ts_code（元数据优先），放在股票 try 内
                codes = resolve_ts_codes(raw_codes, asset_type="STOCK", main_db=main_db)
                res.stock_refreshed = m.fetch_adj_factor(
                    adapter, codes, start, is_etf=False)
            except Exception as e:  # 转换异常 / 全失败 FactorRefreshError / 其他 → degraded
                res.stock_failed = len(raw_codes)  # 按原始 universe 数量统计
                res.degraded = True
                res.error = f"stock adj_factor refresh failed: {e}"
                logger.warning(f"[qfq_refresh] {res.error}")

        if etf_universe:
            raw_codes = sorted(etf_universe)
            try:
                codes = resolve_ts_codes(raw_codes, asset_type="ETF", main_db=main_db)
                res.etf_refreshed = m.fetch_adj_factor(
                    adapter, codes, start, is_etf=True)
            except Exception as e:
                res.etf_failed = len(raw_codes)
                res.degraded = True
                res.error = (res.error or "") + f"; etf fund_adj refresh failed: {e}"
                logger.warning(f"[qfq_refresh] etf fund_adj refresh failed: {e}")

        if res.degraded:
            logger.warning(
                f"[qfq_refresh] degraded=True（股票失败 {res.stock_failed} / "
                f"ETF 失败 {res.etf_failed}）；本轮检测器不可信，价格水位应 hold")
        else:
            logger.info(
                f"[qfq_refresh] 刷新完成：股票 {res.stock_refreshed} 行 / "
                f"ETF {res.etf_refreshed} 行")
        return res
