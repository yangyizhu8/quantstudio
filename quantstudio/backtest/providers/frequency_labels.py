"""PR3: API 层频率标签 ↔ 存储层 freq 列值的双向映射 + 结构化能力错误。

API 层用 "1d/1m/5m/15m/30m/60m/tick"（Ptrade/get_price/get_history 对外约定）。
存储层 stock_minutes/etf_minutes 的 freq 列用 "1min/5min/15min/30min/60min"
（采集管线约定，见 source_capabilities.KNOWN_TABLE_FREQS 和 writers.py DDL）。

单一映射源，禁止在多处发明第三种写法。

主计划 7.19 "严禁频率缺失时回退到日线；数据缺失返回结构化能力错误"——
本模块定义结构化能力错误，用 code 属性支持程序化判断（不依赖中文字符串匹配）。
"""
from __future__ import annotations

from typing import Iterable, Optional


# API 标签 → 存储 freq 列值
API_TO_STORAGE = {
    "1d": "daily",
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "60m": "60min",
    "tick": "tick",
}

# 存储 freq 列值 → API 标签
STORAGE_TO_API = {v: k for k, v in API_TO_STORAGE.items()}

VALID_API_FREQS = tuple(API_TO_STORAGE.keys())
MINUTE_API_FREQS = ("1m", "5m", "15m", "30m", "60m")  # 走 minutes 表的频率

# 能力错误 code 常量（补齐 4：用 code 而非字符串匹配）
ERR_INVALID_FREQUENCY = "INVALID_FREQUENCY"     # API 标签本身不被识别（如 "1s"）
ERR_TABLE_MISSING = "TABLE_MISSING"             # 该证券类型无对应分钟表（如指数查分钟）
ERR_TABLE_EMPTY = "TABLE_EMPTY"                 # 表存在但无数据（当前 stock/etf_minutes 实际状态）
ERR_FREQ_NOT_IN_TABLE = "FREQ_NOT_IN_TABLE"     # 表有数据但缺该原生 freq（列出可用 freq）


# 变体归一化表（接受 "daily"/"1min"/"D"/"day" 等历史写法，归一化到标准 API 标签）
_VARIANT_TO_API = {
    **{v: k for k, v in API_TO_STORAGE.items()},  # 反向：1min→1m 等
    "d": "1d", "day": "1d", "daily": "1d",
    "1min": "1m", "5min": "5m", "15min": "15m", "30min": "30m", "60min": "60m",
}


class FrequencyCapabilityError(Exception):
    """PR3: 频率查询能力错误。严禁静默回退日线。

    code 属性用于程序化判断（补齐 4），不依赖中文字符串匹配。
    except 分支应判断 getattr(e, 'code', None)，而非匹配异常消息文本。

    Attributes:
        code: 错误码常量（ERR_* 之一）
        api_freq: 触发错误的 API 频率标签
        storage_freq: 对应的存储 freq 列值（如适用）
        table: 涉及的表名（如适用）
        available_freqs: 表中实际可用的 freq 列表（FREQ_NOT_IN_TABLE 时填充）
        detail: 额外说明
    """

    def __init__(self, code: str, api_freq: Optional[str] = None,
                 storage_freq: Optional[str] = None, table: Optional[str] = None,
                 available_freqs: Optional[Iterable[str]] = None, detail: str = ""):
        self.code = code
        self.api_freq = api_freq
        self.storage_freq = storage_freq
        self.table = table
        self.available_freqs = list(available_freqs) if available_freqs else []
        self.detail = detail
        super().__init__(self._msg())

    def _msg(self) -> str:
        parts = [f"[{self.code}]"]
        if self.api_freq:
            parts.append(f"api_freq={self.api_freq}")
        if self.storage_freq:
            parts.append(f"storage_freq={self.storage_freq}")
        if self.table:
            parts.append(f"table={self.table}")
        if self.available_freqs:
            parts.append(f"available_freqs={self.available_freqs}")
        if self.detail:
            parts.append(self.detail)
        return " ".join(parts)


def normalize_api_frequency(freq: str) -> str:
    """归一化频率变体到标准 API 标签。

    接受 "1d"/"daily"/"day"/"1min" 等变体，返回 "1d"/"1m" 等标准标签。
    不识别的值原样返回（由调用方决定是否报错）。
    """
    if freq is None:
        return "1d"
    key = str(freq).strip().lower()
    return _VARIANT_TO_API.get(key, str(freq))


def api_to_storage(freq: str) -> str:
    """API 标签 → 存储 freq 列值。

    未知频率（不在 API_TO_STORAGE 中）raise FrequencyCapabilityError(ERR_INVALID_FREQUENCY)。
    """
    if freq is None:
        return "daily"
    key = str(freq).strip().lower()
    if key in API_TO_STORAGE:
        return API_TO_STORAGE[key]
    raise FrequencyCapabilityError(
        ERR_INVALID_FREQUENCY, api_freq=freq,
        detail=f"未知频率标签 {freq!r}；合法值: {VALID_API_FREQS}")


def is_minute_frequency(freq: str) -> bool:
    """是否走 minutes 表（True）还是日线表（False）。

    tick 不走 minutes 表（tick 表独立），返回 False。
    """
    if freq is None:
        return False
    return str(freq).strip().lower() in MINUTE_API_FREQS
