"""BaseSourceAdapter — 数据源适配器基类

统一接口只负责拉取，返回 (原始DataFrame, metadata)。格式转换由 FieldAligner 负责。
"""
from __future__ import annotations

import abc
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class RateLimiter:
    """简单限流器：calls_per_min + wait_on_429"""
    calls_per_min: int = 200
    wait_on_429: bool = True
    _timestamps: List[float] = field(default_factory=list)

    def acquire(self):
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < 60]
        if len(self._timestamps) >= self.calls_per_min:
            sleep_sec = 60 - (now - self._timestamps[0]) + 0.1
            logger.debug(f"[RateLimiter] sleep {sleep_sec:.1f}s (limit={self.calls_per_min}/min)")
            # 分段 sleep + 心跳：超过 30s 的等待每 30s 打一条 INFO，避免误判为卡死
            slept = 0
            while slept < sleep_sec:
                chunk = min(30, sleep_sec - slept)
                time.sleep(chunk)
                slept += chunk
                remaining = sleep_sec - slept
                if remaining > 0:
                    logger.info(f"[RateLimiter] 限流等待 {slept:.0f}/{sleep_sec:.0f}s，剩余 {remaining:.0f}s "
                                f"(limit={self.calls_per_min}/min)")
        # 每次 acquire 只记一个时间戳（修复：原两次 append 导致实际限流减半）
        self._timestamps.append(time.time())

    def on_429(self):
        if self.wait_on_429:
            logger.warning("[RateLimiter] 429 限流，等待 60s 重试")
            time.sleep(60)


class BaseSourceAdapter(abc.ABC):
    """数据源适配器基类

    子类必须实现 fetch_table / get_last_date / supports_freq
    """

    def __init__(self, config: Dict):
        self.source_name: str = config.get("name", self.__class__.__name__.lower())
        self.api_config: Dict = config.get("api", {})
        token = config.get("token")
        if token:
            self.api_config["token"] = token
        rate_cfg = config.get("rate_limit", {})
        self._base_rate_limit = {
            "calls_per_min": int(rate_cfg.get("calls_per_min", 200)),
            "wait_on_429": bool(rate_cfg.get("wait_on_429", True)),
        }
        self.rate_limiter = RateLimiter(**self._base_rate_limit)
        retry_cfg = config.get("retry", {})
        self._base_retry_max = int(retry_cfg.get("max", 5))
        self._base_retry_backoff_sec = tuple(retry_cfg.get(
            "backoff_sec", (60, 120, 240, 480, 960)))
        self._base_call_timeout = float(config.get("call_timeout", 90))
        self.retry_max = self._base_retry_max
        self.retry_backoff_sec = self._base_retry_backoff_sec
        self.call_timeout = self._base_call_timeout
        self._client = None

    def configure_execution(self, task: Dict) -> None:
        """Apply per-task rate-limit, retry and timeout settings.

        Adapter instances are cached by the collector. Applying settings at
        the task boundary makes collector_tasks.json authoritative while all
        source calls continue to share one retry implementation.
        """
        # Reset first: a cached adapter must not inherit execution settings
        # from the previously executed task.
        self.rate_limiter.calls_per_min = self._base_rate_limit["calls_per_min"]
        self.rate_limiter.wait_on_429 = self._base_rate_limit["wait_on_429"]
        self.retry_max = self._base_retry_max
        self.retry_backoff_sec = self._base_retry_backoff_sec
        self.call_timeout = self._base_call_timeout

        rate_cfg = task.get("rate_limit", {}) or {}
        if "calls_per_min" in rate_cfg:
            self.rate_limiter.calls_per_min = max(1, int(rate_cfg["calls_per_min"]))
        if "wait_on_429" in rate_cfg:
            self.rate_limiter.wait_on_429 = bool(rate_cfg["wait_on_429"])

        retry_cfg = task.get("retry", {}) or {}
        if "max" in retry_cfg:
            self.retry_max = max(1, int(retry_cfg["max"]))
        if retry_cfg.get("backoff_sec"):
            self.retry_backoff_sec = tuple(int(x) for x in retry_cfg["backoff_sec"])
        if "call_timeout" in task:
            self.call_timeout = max(0.0, float(task["call_timeout"]))

    @abc.abstractmethod
    def fetch_table(self, table: str, start: str, end: str,
                    freq: str = "daily",
                    codes: Optional[List[str]] = None) -> Tuple[pd.DataFrame, Dict]:
        """拉取指定表数据，返回 (原始DataFrame, metadata)

        Args:
            table: stock_daily / stock_minutes / fin_indicator / index_daily
            start/end: 日期字符串 YYYY-MM-DD 或 YYYYMMDD
            freq: daily / 1min / 5min / 15min / 30min / 60min
            codes: 股票代码列表（None=全部）

        Returns:
            (raw_df, metadata) —— metadata 含 source/freq/columns_origin/code_format/date_format/units
        """
        ...

    @abc.abstractmethod
    def get_last_date(self, table: str, freq: str = "daily") -> Optional[str]:
        """获取该表已有的最新日期（用于增量拉取）。子类可选实现（无 DB 时返回 None）"""
        ...

    @abc.abstractmethod
    def supports_freq(self, freq: str) -> bool:
        """声明该数据源支持的频率"""
        ...

    def get_st_codes(self) -> set:
        """获取当前 ST 股票代码集合（裸码格式）。每个 adapter 用自己的数据源方式实现。
        返回 {'600001', '000002'} 格式。无 ST 数据时返回空 set。
        基类提供默认空实现，子类应覆盖。"""
        return set()

    def fill_isst(self, df, code_col="code"):
        """为 DataFrame 填充 isST 字段（0=正常, 1=ST）。
        每个 adapter 调用自己的 get_st_codes() 判断。
        如果 isST 列已存在且有值，不覆盖。"""
        if "isST" in df.columns and df["isST"].notna().any():
            return df  # 已有值，不覆盖
        st_codes = self.get_st_codes()
        if code_col in df.columns:
            df["isST"] = df[code_col].apply(lambda c: 1 if str(c) in st_codes else 0)
        return df

    def supports_task(self, table: str, freq: str) -> tuple:
        """检查该数据源是否支持指定的表+频率组合。
        Returns: (支持/不支持, 原因说明)"""
        if not self.supports_freq(freq):
            return (False, f"{self.source_name} 不支持频率 '{freq}'")
        if table == "fin_indicator" and self.source_name not in ("tushare",):
            return (False, f"{self.source_name} 不支持财务指标（仅 tushare 支持）")
        return (True, "")

    def supports_qfq(self) -> bool:
        """是否支持复权价格。子类覆盖。
        baostock/tushare/xtquant 返回 True，a_stock_data 返回 False。"""
        return True

    def _retry_with_backoff(self, fn, *args, max_retries: Optional[int] = None,
                            backoff_sec=None,
                            call_timeout: Optional[float] = None, **kwargs):
        """指数退避重试（含心跳日志，便于区分"正常等待"与"真卡死"）。

        call_timeout: 单次调用的「硬超时」（秒）。tushare/akshare 等数据源在 token 被
        并发限流、或服务端慢滴响应时，可能长时间挂起而不抛任何异常，导致调用线程永久
        阻塞、整轮任务假死（无报错、不写库、进程却活着）。这里用线程级超时把"挂起"转成
        可捕获的 TimeoutError，触发退避重试；多次失败后抛出 RuntimeError（由调用方决定
        跳过当天还是终止）。call_timeout 应略大于数据源自身的网络超时（tushare 默认 30s），
        专门兜住"连上了但慢滴不发数据"的极端情况。
        """
        max_retries = self.retry_max if max_retries is None else max(1, int(max_retries))
        backoff_sec = self.retry_backoff_sec if backoff_sec is None else tuple(backoff_sec)
        call_timeout = self.call_timeout if call_timeout is None else call_timeout
        if not backoff_sec:
            backoff_sec = (0,)
        last_err = None
        for attempt in range(max_retries):
            try:
                self.rate_limiter.acquire()
                if call_timeout and call_timeout > 0:
                    _result = [None]
                    _err = [None]

                    def _run():
                        try:
                            _result[0] = fn(*args, **kwargs)
                        except Exception as e:  # noqa: BLE001
                            _err[0] = e

                    _th = threading.Thread(target=_run, daemon=True)
                    _th.start()
                    _th.join(call_timeout)
                    if _th.is_alive():
                        # 调用在 call_timeout 内未返回 → 视为挂起（极可能限流慢滴）。
                        # 放弃该守护线程，当作失败触发退避重试。
                        raise TimeoutError(
                            f"{self.source_name} 单次调用超过 {call_timeout}s 未返回（疑似挂起/限流慢滴）")
                    if _err[0] is not None:
                        raise _err[0]
                    return _result[0]
                return fn(*args, **kwargs)
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                if "429" in msg or "rate" in msg or "limit" in msg:
                    self.rate_limiter.on_429()
                wait = backoff_sec[min(attempt, len(backoff_sec) - 1)]
                logger.warning(f"[Retry] {self.source_name} attempt {attempt+1}/{max_retries} "
                               f"failed: {type(e).__name__}: {e}, sleep {wait}s")
                # 分段 sleep + 心跳，让长等待可见（避免误判为卡死）
                self._sleep_with_heartbeat(wait, tag=f"[Retry] {self.source_name} backoff")
        raise RuntimeError(f"{self.source_name} 重试 {max_retries} 次仍失败: {last_err}") from last_err

    @staticmethod
    def _sleep_with_heartbeat(total_sec: int, tag: str = "", interval: int = 30):
        """分段 sleep，每 interval 秒打一条 DEBUG 心跳。
        total_sec<=interval 时直接 sleep 不打日志（避免噪音）。"""
        if total_sec <= interval:
            time.sleep(total_sec)
            return
        slept = 0
        while slept < total_sec:
            chunk = min(interval, total_sec - slept)
            time.sleep(chunk)
            slept += chunk
            remaining = total_sec - slept
            if remaining > 0:
                logger.debug(f"{tag} 等待中 {slept}/{total_sec}s，剩余 {remaining}s")

    def close(self):
        """释放资源（子类可覆盖）"""
        self._client = None
