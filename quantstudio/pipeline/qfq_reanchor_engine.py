"""QFQ 重锚子系统 —— 第二批：单证券修正引擎（staged fresh daily + 分钟方法 B/A + 事务 + postcheck）

职责（设计 v5.1 第二批 + 用户批准的强制实现边界，2026-07-27）：

1. **日线 staged fresh update**：staged fresh 日线按 ``(code,time)`` 对齐，事务内
   **只能 UPDATE 四个 front 列**（open_front/high_front/low_front/close_front）。
   禁止修改 open/high/low/close、preClose、pctChg、volume/amount/turn、``*_back``、
   ST/估值/退市风险、update_time/data_source。不得删除整只证券后重写，也不得在
   fresh 覆盖后再乘 R。

2. **分钟 R 方法 B**（对每个重叠交易日、每个 freq 独立计算）::

       target_scale(d)        = fresh_daily_close_front(d) / fresh_daily_close(d)
       stored_minute_scale(b) = stored_minute_close_front(b) / stored_minute_close(b)
       R(b)                   = target_scale(d) / stored_minute_scale(b)

   - OHLC 四列交叉验证；NULL 不伪造；分母必须 finite 且 >0；
   - 区间内形成单一稳定比率簇；bootstrap 分段（allow_multi_segment=True）时才允许
     多个可解释簇；
   - **不使用 Tushare A/B 直接乘价**（Tushare 事件仅用于解释变点与黄金抽验采样）。

3. **方法 A 黄金抽验**：每个拟修正区间、每个 freq 至少 3 交易日 × 5 根代表 bar，
   覆盖区间首部/尾部/除权日前一交易日/分段边界两侧；09:30 集合竞价 bar 仅作附加
   样本，连续竞价样本不得缺失。**有效连续竞价 = session-aware 双时段
   (09:30,11:30] ∪ (13:00,15:00] 且 end-labeled cadence 合法**（1min →
   09:31..11:30 ∪ 13:01..15:00；5min → 09:35..11:30 ∪ 13:05..15:00）；午间
   11:31–12:59 为休市，任何落在午间的 bar 不是有效样本。``R_golden =
   fresh_xtquant_minute_front / stored_minute_front``（golden_minutes 必须来自
   **独立采集的 fresh xtquant 前复权输出**，禁止由 stored_raw × daily_scale 合成）；
   与方法 B 的 R 超容差不一致 → **整个证券 BLOCK**，不得取平均后继续。

4. **分段计划**：主键/边界 = (code, freq, t_start, t_end, ratio)；时间区间左闭右开
   ``time >= t_start AND time < t_end``；UPDATE 边界取 R 时间序列确认的**精确变点**，
   不取 Tushare 事件日 ±1 的模糊边界。

5. **单证券事务**：每只证券使用**同一个 DuckDB connection**::

       BEGIN
         创建/注册 staged fresh daily
         分频率、分区间 UPDATE minute *_front
         UPDATE daily 4 个 *_front
         执行全部 COMMIT 前 postcheck
         写 committed event
         推进 anchor
       COMMIT

   任一检查失败 → ROLLBACK → 用**独立短事务**记录 failed / rolled_back / blocked，
   **绝不推进 anchor**。

6. **COMMIT 前硬门禁**：front-chain 收益一致性（CalendarService 确认真实相邻交易日，
   缺失日不得静默当停牌；首日缺上一交易日行时**唯一豁免凭据 = security master
   ``list_date_ms``**，本地 MIN(time) 不构成上市首日证据）、缩放一致性、日线
   staged 一致、K 线关系、行数守恒、跨表重叠日（按 freq 分组 + 校准容差 +
   session-aware 有效连续竞价；daily/minute 共存日无有效连续竞价 bar → 回滚）。

7. **B-1 fresh_staged 模型（用户批准边界，2026-07-27）**：调用方**显式**传入
   ``model="fresh_staged"`` 时，分钟修正不再计算 R，而是把**独立采集的 fresh
   xtquant 分钟前复权 OHLC** 按 ``(code,freq,time)`` staged 后逐值 UPDATE 四个
   front 列（禁触 raw OHLC/volume/amount/preClose/*_back/update_time/data_source，
   禁 DELETE/INSERT 重建）。写入前逐 bar 验证 stored raw OHLC 与 fresh
   ``dividend_type=none`` raw OHLC 一致；key/coverage/NULL/session/cadence 任一异常
   整券 BLOCK；覆盖必须完整（staged_count==target_count==matched_count，
   missing_target/missing_staged/duplicates/raw_mismatch 全 0）。COMMIT 前新增
   minute_staged_match / minute_raw_match / minute_coverage / minute_tick_error 四项
   postcheck，post-write 四个 front 列 vs fresh 逐 bar ≤1 tick（bars_over_1_tick
   必须为 0）。ratio 方法 B/A 路径完整保留；ratio BLOCK 后**绝不静默切换**到
   fresh_staged——模型只能由调用方显式选择，选择原因写入事件审计。

   模型语义说明：xtquant 现金分红为**减法复权**（front = raw − 每股现金分红），
   与乘法单比率模型不等价；因此 fresh_staged 模式下"缩放一致性 / front-chain"
   两项门禁按**模型感知形式**执行——每行"乘法偏离 ≤ tol **或** 加法偏离 ≤ 1
   tick"二者必须满足其一（加法偏离：OHLC 列 |(X−X_front)−(close−close_front)|；
   front-chain |(close_t−cf_t)−(preClose_t−cf_prev)|）。ratio 模式行为逐位不变。

安全边界：本模块**不默认打开任何数据库**——所有写路径 API 只接受调用方显式传入的
DuckDB 连接；测试一律使用临时库/合成 fixture，禁止在正式 data/quantstudio.db 上执行。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from quantstudio.pipeline.qfq_reanchor_schema import (
    ASSET_TABLE_MAP,
    _normalize_asset_type,
    _normalize_code,
    _validate_epoch_ms,
)
from quantstudio.pipeline.qfq_calendar import (
    CalendarService, TZ, _day_midnight_ms, _norm_freq,
)
from quantstudio.pipeline.qfq_fresh_capture import (
    resolve_fresh_capture, write_fresh_capture, FreshCapture,
    CaptureContentConflict,
    CAPTURE_ACTION_NEW, CAPTURE_ACTION_ALREADY_COMMITTED,
    CAPTURE_ACTION_RECOLLECT_OK, CAPTURE_ACTION_RECOVER_APPLIED_NO_EVENT,
)
from quantstudio.pipeline.qfq_orchestrator_types import FreshCaptureRecord

logger = logging.getLogger(__name__)

# 允许更新的日线/分钟 front 列（唯一白名单；除此四列外任何列都禁止 UPDATE）
FRONT_COLS: Tuple[str, ...] = ("open_front", "high_front", "low_front", "close_front")
RAW_COLS: Tuple[str, ...] = ("open", "high", "low", "close")

# 分钟修正模型（B-1 批准边界，2026-07-27/28）：
# - "ratio"（默认）：方法 B 乘法单比率簇 + 方法 A 黄金抽验（原行为，完整保留）；
# - "fresh_staged"：fresh xtquant 分钟前复权**逐值写入**（staged minute → 仅
#   UPDATE 四个 front 列）。模型由调用方**显式**选择并给出书面原因写入事件审计；
#   引擎内部**不存在任何 ratio BLOCK 后静默切换到 fresh_staged 的回退逻辑**。
MODELS: Tuple[str, ...] = ("ratio", "fresh_staged", "fresh_authoritative_rebase")

# stored raw vs fresh(dividend_type=none) raw 一致性硬阈（浮点存取噪声级，
# 实测 fixture 最大差 <4e-15；任何真实数据差异都远大于此阈）
_RAW_MATCH_EPS = 1e-9

# —— tick_size 资产路由（第六轮阻断 4：tick 不得统一写死 0.01）——
# A 股股票最小变动 0.01 元；场内 ETF 最小变动 0.001 元。
# ReanchorTolerances.tick_size=None 时按此表路由；显式设置则覆盖。
_TICK_SIZE_BY_ASSET: Dict[str, float] = {"STOCK": 0.01, "ETF": 0.001}

# —— raw 逐 bar 对齐容差路由（批次2：ETF 分钟 tick 噪声，2026-07-30）——
# STOCK/日线保持严格 _RAW_MATCH_EPS=1e-9（真实数据差异远大于此阈）；
# ETF 分钟放宽到 1 个 tick（0.001）——已全市场预检确认 277 只 BLOCK 全是
# ETF 1min tick 级舍入噪声（max diff ≤ 0.008，零只 > 0.01），非真实数据源不一致。
# 放宽仅作用于分钟 raw 对齐（precheck + B-1 precheck + 写后 postcheck 三处一致），
# 不影响 daily（日线差异为 0）与 STOCK（保持严格）。
_RAW_MATCH_EPS_ETF_MINUTE = 1e-3


def _raw_match_eps_minute(asset_type: str) -> float:
    """分钟 raw 逐 bar 对齐容差：ETF 分钟放宽到 1 个 tick（0.001），其余严格。"""
    if _normalize_asset_type(asset_type) == "ETF":
        return _RAW_MATCH_EPS_ETF_MINUTE
    return _RAW_MATCH_EPS


def resolve_tick_size(asset_type: str, tol: "Optional[ReanchorTolerances]" = None) -> float:
    """解析实际 tick_size：显式 tol.tick_size 优先，否则按资产类型路由。"""
    if tol is not None and tol.tick_size is not None:
        return float(tol.tick_size)
    return _TICK_SIZE_BY_ASSET[_normalize_asset_type(asset_type)]

# --- A 股连续竞价合法窗口（end-labeled，Asia/Shanghai）---------------------
# 上午 (09:30, 11:30]、下午 (13:00, 15:00]；11:31–12:59 为午间休市，任何落在
# 午间的 bar **不是**有效连续竞价样本；09:30 为集合竞价附加样本。
# 第三轮对抗审核修复：废除 [571,900] 单一区间（它把午间休市计为有效连续竞价）。
_AUCTION_CLOCK = 9 * 60 + 30
_AM_OPEN_MIN, _AM_CLOSE_MIN = 9 * 60 + 30, 11 * 60 + 30
_PM_OPEN_MIN, _PM_CLOSE_MIN = 13 * 60, 15 * 60


@lru_cache(maxsize=32)
def _cont_clock_set(freq_c: str) -> frozenset:
    """freq 的合法 end-labeled 连续竞价 clock 分钟集合。

    N 分钟 bar 的合法收盘时刻 = 会话开盘 + N, +2N, ... ≤ 会话收盘：
    1min → 09:31..11:30 ∪ 13:01..15:00（240 个）；
    5min → 09:35..11:30 ∪ 13:05..15:00（48 个）。
    午间（11:31–12:59）/ 集合竞价（09:30）/ 偏离 cadence 的时刻均不合法。
    """
    n = int(freq_c[:-3])
    out: List[int] = []
    for open_m, close_m in ((_AM_OPEN_MIN, _AM_CLOSE_MIN),
                            (_PM_OPEN_MIN, _PM_CLOSE_MIN)):
        t = open_m + n
        while t <= close_m:
            out.append(t)
            t += n
    return frozenset(out)


def _cont_mask(clock_min: pd.Series, freq_c: str) -> pd.Series:
    """session-aware 连续竞价掩码：黄金抽样与 cross_table_overlap 共用此判断。"""
    return clock_min.isin(_cont_clock_set(freq_c))


# ---------------------------------------------------------------------------
# 容差 / 数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReanchorTolerances:
    """第二批全部硬门禁容差（校准值；调用方可按数据源精度覆盖）。"""
    ratio_rel_tol: float = 5e-4    # 方法 B 簇内稳定 / R≈1 判定（相对）
    golden_rel_tol: float = 1e-3   # 方法 A R_golden vs 方法 B R（相对）
    tol_return: float = 1e-3       # front-chain 收益 vs 参考收益（绝对差）
    tol_scale: float = 1e-3        # OHLC 四列缩放一致性（相对）
    tol_cross: float = 1e-3        # 跨表重叠日 daily vs 分钟收盘 front（相对）
    min_golden_days: int = 3       # 每拟修正区间黄金抽验最少交易日数
    min_golden_bars_per_day: int = 5  # 每抽验日最少代表 bar 数
    # 价格最小变动单位（B-1 逐 bar 误差硬门禁 + 减法复权加法豁免上界）。
    # 第六轮阻断 4 修复：默认 None = 按资产类型路由（STOCK 0.01 / ETF 0.001，
    # 见 resolve_tick_size）；调用方显式设置则覆盖路由（并写入事件审计）。
    tick_size: Optional[float] = None


@dataclass
class RatioSegment:
    """分钟修正分段计划项。区间左闭右开：time >= t_start AND time < t_end。"""
    code: str
    freq: str
    t_start: int
    t_end: int
    ratio: float
    bar_count: int
    dispersion: float          # 段内 max|R/ratio - 1|
    needs_update: bool         # |ratio-1| > ratio_rel_tol 时才 UPDATE

    def to_dict(self) -> Dict:
        return {
            "code": self.code, "freq": self.freq,
            "t_start": int(self.t_start), "t_end": int(self.t_end),
            "ratio": float(self.ratio), "bar_count": int(self.bar_count),
            "dispersion": float(self.dispersion),
            "needs_update": bool(self.needs_update),
        }


@dataclass
class ReanchorResult:
    status: str                       # committed / blocked / rolled_back / failed
    event_id: str
    asset_type: str
    code: str
    plans: Dict[str, List[RatioSegment]] = field(default_factory=dict)
    golden_report: Dict = field(default_factory=dict)
    postchecks: Dict = field(default_factory=dict)
    rows: Dict = field(default_factory=dict)
    daily_rows_updated: int = 0
    block_reason: Optional[str] = None
    error: Optional[str] = None
    model: str = "ratio"              # 本次实际使用的分钟修正模型（显式选择）
    minute_coverage: Dict = field(default_factory=dict)  # B-1 覆盖统计（按 freq）


class ReanchorBlocked(Exception):
    """方法 B/A 或数据契约失败 → 整个证券 BLOCK（回滚 + blocked 事件，不推进 anchor）。

    第七轮审计阻断 2：携带 precheck 已计算的覆盖统计（``coverage``）与阶段
    （``phase``），使 blocked 事件能记录"执行到哪一步、各统计值是多少"。
    """

    def __init__(self, reason: str, detail: str = "", *,
                 coverage: Optional[dict] = None, phase: Optional[str] = None,
                 freq: Optional[str] = None):
        self.reason = reason
        self.detail = detail
        self.coverage = coverage
        self.phase = phase
        self.freq = freq
        super().__init__(f"{reason}: {detail}" if detail else reason)


def _is_valid_sha256(value: Optional[str]) -> bool:
    """合法 64 位十六进制 SHA-256（第七轮审计阻断 1：fresh_metadata_sha256 强校验）。"""
    import re
    if not value or not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", value.strip()))


class PostcheckFailed(Exception):
    """COMMIT 前硬门禁失败 → ROLLBACK + rolled_back 事件，不推进 anchor。"""

    def __init__(self, check: str, detail: str = ""):
        self.check = check
        self.detail = detail
        super().__init__(f"{check}: {detail}" if detail else check)


# ---------------------------------------------------------------------------
# 时刻向量化工具（Asia/Shanghai）
# ---------------------------------------------------------------------------

def _ts_local(times: pd.Series) -> pd.Series:
    return pd.to_datetime(times.astype("int64"), unit="ms", utc=True).dt.tz_convert(TZ)


def _day_ms_of(times: pd.Series) -> pd.Series:
    """bar time → 所在自然日 00:00（Asia/Shanghai）epoch-ms。"""
    ts = _ts_local(times)
    return (ts.dt.normalize().astype("int64") // 10 ** 6).astype("int64")


def _clock_min_of(times: pd.Series) -> pd.Series:
    """bar time → 当日 clock 分钟数（HH*60+MM）。"""
    ts = _ts_local(times)
    return (ts.dt.hour * 60 + ts.dt.minute).astype("int64")


def _is_finite_pos(x) -> bool:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0.0


def _canon_minute_freq(freq: str) -> str:
    """分钟 freq → 存储 canonical 字面量（"1min"/"5min"…）；日线/未知抛 ValueError。"""
    kind, n = _norm_freq(freq)
    if kind != "minute" or n <= 0:
        raise ValueError(f"非分钟 freq: {freq!r}")
    return f"{n}min"


def _tables_of(asset_type: str) -> Tuple[str, str]:
    """asset_type → (daily_table, minute_table)。"""
    tabs = ASSET_TABLE_MAP[asset_type]
    daily = next(t for t in tabs if t.endswith("_daily"))
    minute = next(t for t in tabs if t.endswith("_minutes"))
    return daily, minute


# ---------------------------------------------------------------------------
# 1. 日线 staged fresh
# ---------------------------------------------------------------------------

_STAGED_REQUIRED = ("code", "time") + RAW_COLS + FRONT_COLS


def stage_fresh_daily(conn, asset_type: str, code: str,
                      fresh_daily: pd.DataFrame,
                      tol: Optional[ReanchorTolerances] = None,
                      model: str = "ratio") -> str:
    """把 fresh 日线注册为**当前连接**上的 staged 临时表并做契约校验。

    校验（任一失败抛 ReanchorBlocked，调用方回滚）：
    - 必需列齐全；code 全部等于目标证券；(code,time) 无重复；time 为合法 epoch-ms；
    - close / close_front 必须 finite 且 >0（target_scale 分母/分子）；
    - open/high/low 及其 front：非 NULL 时必须 finite 且 >0；
    - fresh 自身 OHLC 四列缩放交叉验证（**模型感知**）：
      * ``model="ratio"``（默认，原行为逐位不变）：乘法偏离 ≤ tol_scale；
      * ``model="fresh_staged"``：xtquant 现金分红为减法复权（front = raw −
        每股分红），乘法比例天然不恒定；每行要求 乘法偏离 ≤ tol_scale **或**
        加法偏离 |(X−X_front) − (close−close_front)| ≤ 1 tick，二者满足其一。

    返回 staged 临时表名。临时表仅存在于本连接，不落正式表。
    """
    tol = tol or ReanchorTolerances()
    if model not in MODELS:
        raise ValueError(f"未知模型 {model!r}，仅支持 {MODELS}")
    asset_type = _normalize_asset_type(asset_type)
    code = _normalize_code(code)
    if fresh_daily is None or len(fresh_daily) == 0:
        raise ReanchorBlocked("fresh_daily_empty", f"code={code} fresh 日线为空")
    missing = [c for c in _STAGED_REQUIRED if c not in fresh_daily.columns]
    if missing:
        raise ReanchorBlocked("fresh_daily_missing_cols", f"缺列 {missing}")
    df = fresh_daily.copy()
    bad_code = df["code"].astype(str).str.strip() != code
    if bool(bad_code.any()):
        raise ReanchorBlocked("fresh_daily_code_mismatch",
                              f"存在非 {code} 行: {df.loc[bad_code, 'code'].unique()[:5]}")
    for t in df["time"]:
        _validate_epoch_ms(t)
    df["time"] = df["time"].astype("int64")
    if df.duplicated(subset=["code", "time"]).any():
        raise ReanchorBlocked("fresh_daily_dup_key", "staged (code,time) 存在重复")
    for _, row in df.iterrows():
        if not _is_finite_pos(row["close"]) or not _is_finite_pos(row["close_front"]):
            raise ReanchorBlocked(
                "fresh_daily_bad_close",
                f"time={row['time']} close={row['close']!r} close_front={row['close_front']!r}"
                f"（分母必须 finite 且 >0）")
        for c in ("open", "high", "low", "open_front", "high_front", "low_front"):
            v = row[c]
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                if not _is_finite_pos(v):
                    raise ReanchorBlocked("fresh_daily_bad_value",
                                          f"time={row['time']} {c}={v!r}")
    # fresh 自身四列缩放交叉验证（模型感知：ratio=纯乘法；fresh_staged=乘法或加法）
    tick = resolve_tick_size(asset_type, tol)   # 第六轮阻断 4：按资产路由
    scale_close = df["close_front"].astype(float) / df["close"].astype(float)
    add_ref = df["close"].astype(float) - df["close_front"].astype(float)
    for x, xf in zip(("open", "high", "low"), ("open_front", "high_front", "low_front")):
        xv = df[x].astype(float)
        xfv = df[xf].astype(float)
        mask = xv.notna() & xfv.notna() & (xv > 0)
        if mask.any():
            dev = ((xfv[mask] / xv[mask]) / scale_close[mask] - 1.0).abs()
            bad = dev > tol.tol_scale
            if model == "fresh_staged":
                # 减法复权豁免：加法偏离 ≤1 tick 亦可（用户批准边界，2026-07-27）
                add_dev = ((xv[mask] - xfv[mask]) - add_ref[mask]).abs()
                bad = bad & (add_dev > tick)
            if bool(bad.any()):
                raise ReanchorBlocked(
                    "fresh_daily_scale_inconsistent",
                    f"{xf}/{x} 与 close_front/close 超容差（model={model}）"
                    f"max_mul_dev={float(dev.max()):.2e}")

    staged = f"qfq_staged_fresh_daily_{code}"
    view = f"_qfq_staged_view_{code}"
    conn.register(view, df[list(_STAGED_REQUIRED)])
    conn.execute(f"DROP TABLE IF EXISTS {staged}")
    conn.execute(f"CREATE TEMP TABLE {staged} AS SELECT * FROM {view}")
    conn.unregister(view)
    return staged


def update_daily_front_from_staged(conn, asset_type: str, code: str,
                                   staged_table: str) -> int:
    """按 (code,time) 对齐，把 staged fresh 的**四个 front 列**写入日线表。

    - **仅** UPDATE open_front/high_front/low_front/close_front；
    - 不 DELETE、不 INSERT、不触碰任何其它列（原始 OHLC/preClose/pctChg/volume/
      amount/turn/*_back/ST/估值/update_time/data_source 全部保持原值）；
    - 返回被更新（对齐命中）的行数。
    """
    asset_type = _normalize_asset_type(asset_type)
    code = _normalize_code(code)
    daily_table, _ = _tables_of(asset_type)
    matched = conn.execute(
        f"SELECT COUNT(*) FROM {daily_table} t JOIN {staged_table} s "
        f"ON t.code = s.code AND t.time = s.time WHERE t.code = ?",
        [code]).fetchone()[0]
    conn.execute(
        f"UPDATE {daily_table} AS t SET "
        f"open_front = s.open_front, high_front = s.high_front, "
        f"low_front = s.low_front, close_front = s.close_front "
        f"FROM {staged_table} AS s "
        f"WHERE t.code = s.code AND t.time = s.time AND t.code = ?",
        [code])
    return int(matched)


# ---------------------------------------------------------------------------
# 2. 分钟 R 方法 B
# ---------------------------------------------------------------------------

def compute_method_b_segments(conn, asset_type: str, code: str, freq: str,
                              staged_table: str,
                              tol: Optional[ReanchorTolerances] = None,
                              allow_multi_segment: bool = False,
                              ) -> Tuple[List[RatioSegment], pd.DataFrame]:
    """方法 B：按重叠交易日逐 bar 计算 R 并切分稳定比率簇（精确变点）。

    Returns:
        (segments, bars_df)
        - segments：升序 RatioSegment 列表（含 noop 段 needs_update=False）；
        - bars_df：参与计算的 bar 明细（time/day/clock_min/R/seg_idx/close_front/close），
          供黄金抽验采样复用。stored 分钟为空 → ([], 空df)。

    契约（违反抛 ReanchorBlocked）：
    - staged 重叠日 close/close_front（分子分母）已由 stage_fresh_daily 保证 finite>0；
    - stored bar 的 close/close_front 只要参与计算就必须 finite 且 >0（NULL bar 跳过
      计算不伪造，但仍归属所在时间段的 UPDATE 范围）；
    - OHLC 四列交叉验证：open/high/low 的 stored scale 与 close scale 超容差 → BLOCK；
    - stored 分钟在 staged 覆盖 span 内存在无 fresh 日线的交易日 → BLOCK（覆盖缺口，
      防止 [t_start,t_end) 误伤无法验证的日子）；
    - 非 noop 段 >1 且未显式 allow_multi_segment（bootstrap 分段）→ BLOCK。
    """
    tol = tol or ReanchorTolerances()
    asset_type = _normalize_asset_type(asset_type)
    code = _normalize_code(code)
    freq_c = _canon_minute_freq(freq)
    _, minute_table = _tables_of(asset_type)

    staged_df = conn.execute(
        f"SELECT time, close, close_front FROM {staged_table} ORDER BY time").df()
    target_scale = {
        int(r["time"]): float(r["close_front"]) / float(r["close"])
        for _, r in staged_df.iterrows()
    }
    stored = conn.execute(
        f"SELECT time, open, high, low, close, "
        f"open_front, high_front, low_front, close_front "
        f"FROM {minute_table} WHERE code = ? AND freq = ? ORDER BY time",
        [code, freq_c]).df()
    if len(stored) == 0:
        return [], stored

    stored["day"] = _day_ms_of(stored["time"])
    stored["clock_min"] = _clock_min_of(stored["time"])
    staged_days = set(target_scale)
    stored_days = sorted(set(int(d) for d in stored["day"]))
    overlap_days = [d for d in stored_days if d in staged_days]
    if not overlap_days:
        return [], stored.iloc[0:0]
    span_lo, span_hi = overlap_days[0], overlap_days[-1]
    gap_days = [d for d in stored_days if span_lo <= d <= span_hi and d not in staged_days]
    if gap_days:
        raise ReanchorBlocked(
            "fresh_daily_coverage_gap",
            f"freq={freq_c} stored 分钟存在无 fresh 日线覆盖的交易日 {gap_days[:5]}"
            f"（span 内共 {len(gap_days)} 日），[t_start,t_end) 更新将误伤，整券 BLOCK")

    bars = stored[stored["day"].isin(overlap_days)].reset_index(drop=True)

    # —— 分母契约 + 逐 bar stored scale（NULL 跳过不伪造；非 NULL 必须 finite>0）——
    close = bars["close"].astype(float)
    close_f = bars["close_front"].astype(float)
    valid = close.notna() & close_f.notna()
    bad = valid & (~np.isfinite(close) | (close <= 0) |
                   ~np.isfinite(close_f) | (close_f <= 0))
    if bool(bad.any()):
        t0 = int(bars.loc[bad, "time"].iloc[0])
        raise ReanchorBlocked(
            "minute_bad_denominator",
            f"freq={freq_c} close/close_front 非 finite>0（首例 time={t0}）")
    calc = bars[valid].copy()
    if len(calc) == 0:
        raise ReanchorBlocked(
            "minute_no_valid_bars", f"freq={freq_c} 重叠日无任何可计算 bar（全 NULL）")
    calc["stored_scale"] = calc["close_front"].astype(float) / calc["close"].astype(float)
    calc["target_scale"] = calc["day"].map(target_scale).astype(float)
    calc["R"] = calc["target_scale"] / calc["stored_scale"]

    # —— OHLC 四列交叉验证（open/high/low 的 stored scale 应与 close scale 一致）——
    for x, xf in zip(("open", "high", "low"), ("open_front", "high_front", "low_front")):
        xv = calc[x].astype(float)
        xfv = calc[xf].astype(float)
        m = xv.notna() & xfv.notna()
        if not m.any():
            continue
        if bool((m & ((xv <= 0) | ~np.isfinite(xv))).any()):
            raise ReanchorBlocked("minute_bad_denominator",
                                  f"freq={freq_c} {x} 非 finite>0")
        dev = ((xfv[m] / xv[m]) / calc.loc[m, "stored_scale"] - 1.0).abs()
        n_bad = int((dev > tol.tol_scale).sum())
        if n_bad:
            raise ReanchorBlocked(
                "minute_ohlc_scale_inconsistent",
                f"freq={freq_c} {xf}/{x} 与 close scale 超容差 bar 数={n_bad} "
                f"max_dev={float(dev.max()):.2e}")

    # —— 精确变点切段：按时间序，R 相对当前段基准超容差即开新段 ——
    calc = calc.sort_values("time").reset_index(drop=True)
    times = calc["time"].to_numpy(dtype="int64")
    rvals = calc["R"].to_numpy(dtype=float)
    seg_idx = np.zeros(len(calc), dtype=int)
    cur = 0
    ref = rvals[0]
    for i in range(1, len(rvals)):
        if abs(rvals[i] / ref - 1.0) > tol.ratio_rel_tol:
            cur += 1
            ref = rvals[i]
        seg_idx[i] = cur
    calc["seg_idx"] = seg_idx

    segments: List[RatioSegment] = []
    n_seg = int(seg_idx.max()) + 1
    for s in range(n_seg):
        m = seg_idx == s
        seg_times = times[m]
        seg_r = rvals[m]
        ratio = float(np.median(seg_r))
        dispersion = float(np.max(np.abs(seg_r / ratio - 1.0)))
        if dispersion > tol.ratio_rel_tol:
            raise ReanchorBlocked(
                "ratio_cluster_unstable",
                f"freq={freq_c} 段 {s} 内 R 离散 {dispersion:.2e} > {tol.ratio_rel_tol:.2e}"
                f"（区间内未形成单一稳定比率簇）")
        t_start = int(seg_times[0])
        t_end = int(times[np.argmax(seg_idx > s)]) if s < n_seg - 1 else int(seg_times[-1]) + 1
        needs = abs(ratio - 1.0) > tol.ratio_rel_tol
        segments.append(RatioSegment(
            code=code, freq=freq_c, t_start=t_start, t_end=t_end, ratio=ratio,
            bar_count=int(m.sum()), dispersion=dispersion, needs_update=needs))

    non_noop = [s for s in segments if s.needs_update]
    if len(non_noop) > 1 and not allow_multi_segment:
        raise ReanchorBlocked(
            "ratio_multi_cluster",
            f"freq={freq_c} 存在 {len(non_noop)} 个需修正比率簇 "
            f"{[round(s.ratio, 6) for s in non_noop]}；"
            f"仅 bootstrap 分段（allow_multi_segment=True）允许多个可解释簇")
    return segments, calc


def explain_changepoints(segments: Sequence[RatioSegment],
                         ex_dates_ms: Sequence[int]) -> List[Dict]:
    """bootstrap 多簇：逐变点核验解释证据（除权事件日）并生成审计记录。

    规则（用户强制边界，2026-07-27）：
    - 变点 = 相邻两段的边界（下一段首 bar 时刻，即 R 序列精确变点）；
    - 每个变点所在**交易日**必须能被某个 ``ex_dates_ms``（除权事件日 00:00
      epoch-ms）解释；解释缺失或只能解释部分变点 → 整券 BLOCK
      （changepoint_unexplained）；
    - 事件解释只用于变点归因审计；**实际 UPDATE 边界仍取 R 序列精确时刻**，
      不取事件日 ±1 模糊边界。

    返回逐变点审计列表 [{boundary_time, boundary_day, explained_by_ex_date}]。
    """
    boundaries: List[Dict] = []
    if len(segments) < 2:
        return boundaries
    ex_set = {int(x) for x in ex_dates_ms}
    unexplained: List[int] = []
    for k in range(1, len(segments)):
        b_time = int(segments[k].t_start)
        b_day = int(_day_ms_of(pd.Series([b_time])).iloc[0])
        ok = b_day in ex_set
        boundaries.append({
            "boundary_time": b_time, "boundary_day": b_day,
            "prev_ratio": float(segments[k - 1].ratio),
            "next_ratio": float(segments[k].ratio),
            "explained_by_ex_date": b_day if ok else None,
        })
        if not ok:
            unexplained.append(b_day)
    if unexplained:
        raise ReanchorBlocked(
            "changepoint_unexplained",
            f"{len(unexplained)}/{len(boundaries)} 个变点无除权事件解释证据 "
            f"（未解释变点日 {unexplained[:5]}，提供的 ex_dates={sorted(ex_set)[:5]}）；"
            f"bootstrap 多簇必须逐变点可解释，整券 BLOCK")
    return boundaries


def apply_minute_segments(conn, asset_type: str, code: str,
                          segments: Sequence[RatioSegment]) -> Dict[str, int]:
    """按分段计划对分钟表执行 UPDATE（仅四个 *_front 列，区间左闭右开）。

    NULL front 乘 R 仍为 NULL（SQL 语义，天然不伪造）。
    返回 {"updated_bars": 实际影响行数合计, "segments_applied": 应用段数}。
    """
    asset_type = _normalize_asset_type(asset_type)
    code = _normalize_code(code)
    _, minute_table = _tables_of(asset_type)
    total = 0
    applied = 0
    for seg in segments:
        if not seg.needs_update:
            continue
        n = conn.execute(
            f"SELECT COUNT(*) FROM {minute_table} "
            f"WHERE code = ? AND freq = ? AND time >= ? AND time < ?",
            [code, seg.freq, seg.t_start, seg.t_end]).fetchone()[0]
        conn.execute(
            f"UPDATE {minute_table} SET "
            f"open_front = open_front * ?, high_front = high_front * ?, "
            f"low_front = low_front * ?, close_front = close_front * ? "
            f"WHERE code = ? AND freq = ? AND time >= ? AND time < ?",
            [seg.ratio, seg.ratio, seg.ratio, seg.ratio,
             code, seg.freq, seg.t_start, seg.t_end])
        total += int(n)
        applied += 1
    return {"updated_bars": total, "segments_applied": applied}


# ---------------------------------------------------------------------------
# 2b. B-1 fresh_staged：fresh xtquant 分钟前复权逐值写入
# ---------------------------------------------------------------------------

def stage_fresh_minutes(conn, asset_type: str, code: str, freq: str,
                        fresh_minutes: pd.DataFrame,
                        tol: Optional[ReanchorTolerances] = None,
                        calendar: Optional[CalendarService] = None) -> str:
    """把 fresh xtquant 分钟（raw + front OHLC）注册为 staged 临时表并做契约校验。

    B-1 强制边界（用户批准，2026-07-27）：
    - 必需列 = code/time + open/high/low/close + 四个 *_front（raw 即
      ``dividend_type=none`` 原始价，front 即 fresh 前复权价）；
    - key：(code,time) 在本 freq 内无重复；time 合法 epoch-ms；
    - session/cadence：每根 bar 的 clock 必须是合法 end-labeled 连续竞价时刻
      或 09:30 集合竞价；午间休市/偏离 cadence 时刻 → 整券 BLOCK；
    - 交易日（第六轮阻断 2）：每根 bar 所在**自然日**必须经 CalendarService
      确认为真实开市日；周末/休市日 → ``fresh_minutes_non_trading_day``、
      trade_calendar 未知日 → ``fresh_minutes_unknown_day``，均整券 BLOCK
      （session/cadence 只约束"钟面时刻"，约束不了"哪一天"——周六 09:31
      的 bar 钟面合法但日期非法，必须由交易日历拦截）；
    - NULL：八个价格列全部 finite 且 >0，任何 NULL → 整券 BLOCK（fresh 采集
      输出不允许缺价，缺价说明采集异常，不得伪造/跳过）。

    ``calendar`` 为 None 时**拒绝执行**（抛 ValueError）——交易日校验是硬
    门禁，不允许静默跳过。

    返回 staged 临时表名（仅存在于本连接）。
    """
    if calendar is None:
        raise ValueError(
            "stage_fresh_minutes 必须提供 CalendarService（交易日校验为 B-1 "
            "硬门禁，不允许静默跳过）")
    tol = tol or ReanchorTolerances()
    asset_type = _normalize_asset_type(asset_type)
    code = _normalize_code(code)
    freq_c = _canon_minute_freq(freq)
    if fresh_minutes is None or len(fresh_minutes) == 0:
        raise ReanchorBlocked("fresh_minutes_empty",
                              f"code={code} freq={freq_c} fresh 分钟为空")
    missing = [c for c in _STAGED_REQUIRED if c not in fresh_minutes.columns]
    if missing:
        raise ReanchorBlocked("fresh_minutes_missing_cols",
                              f"freq={freq_c} 缺列 {missing}")
    df = fresh_minutes.copy()
    if "freq" in df.columns:
        bad_f = df["freq"].astype(str).map(
            lambda f: _canon_minute_freq(f) if f else "") != freq_c
        if bool(bad_f.any()):
            raise ReanchorBlocked(
                "fresh_minutes_freq_mismatch",
                f"存在非 {freq_c} 行: {df.loc[bad_f, 'freq'].unique()[:5]}")
    bad_code = df["code"].astype(str).str.strip() != code
    if bool(bad_code.any()):
        raise ReanchorBlocked(
            "fresh_minutes_code_mismatch",
            f"存在非 {code} 行: {df.loc[bad_code, 'code'].unique()[:5]}")
    for t in df["time"]:
        _validate_epoch_ms(t)
    df["time"] = df["time"].astype("int64")
    if df.duplicated(subset=["time"]).any():
        raise ReanchorBlocked("fresh_minutes_dup_key",
                              f"freq={freq_c} staged (code,freq,time) 存在重复")
    # session/cadence：合法连续竞价时刻 ∪ 09:30 集合竞价；其余整券 BLOCK
    clock = _clock_min_of(df["time"])
    legal = clock.isin(_cont_clock_set(freq_c)) | (clock == _AUCTION_CLOCK)
    if bool((~legal).any()):
        t_bad = int(df.loc[~legal, "time"].iloc[0])
        raise ReanchorBlocked(
            "fresh_minutes_bad_session",
            f"freq={freq_c} 存在非法 session/cadence bar（首例 time={t_bad}，"
            f"共 {int((~legal).sum())} 根；午间/偏离 cadence 时刻不合法）")
    # —— 交易日校验（第六轮阻断 2）：逐自然日经 CalendarService 确认开市 ——
    #    周末/休市 bar 钟面时刻可以完全合法，session/cadence 拦不住；
    #    未知日不得静默当开市或闭市（与 is_trading_day 阻断 1 语义一致）。
    for d in sorted({_day_midnight_ms(int(t)) for t in df["time"]}):
        try:
            is_open = calendar.is_trading_day(int(d))
        except LookupError as exc:
            raise ReanchorBlocked(
                "fresh_minutes_unknown_day",
                f"freq={freq_c} 自然日 {d} 不在 trade_calendar（未知日不得"
                f"静默放行）：{exc}")
        if not is_open:
            raise ReanchorBlocked(
                "fresh_minutes_non_trading_day",
                f"freq={freq_c} 存在非交易日 bar（自然日 {d}；周末/休市日的"
                f"分钟 bar 无论钟面时刻是否合法一律整券 BLOCK）")
    # NULL / finite：八个价格列全部 finite>0
    for c in RAW_COLS + FRONT_COLS:
        v = df[c].astype(float)
        bad = v.isna() | ~np.isfinite(v) | (v <= 0)
        if bool(bad.any()):
            t_bad = int(df.loc[bad, "time"].iloc[0])
            raise ReanchorBlocked(
                "fresh_minutes_null_or_bad",
                f"freq={freq_c} 列 {c} 存在 NULL/非 finite>0（首例 time={t_bad}，"
                f"共 {int(bad.sum())} 根）——fresh 采集不允许缺价")

    staged = f"qfq_staged_fresh_min_{code}_{freq_c}"
    view = f"_qfq_staged_min_view_{code}_{freq_c}"
    conn.register(view, df[list(_STAGED_REQUIRED)])
    conn.execute(f"DROP TABLE IF EXISTS {staged}")
    conn.execute(f"CREATE TEMP TABLE {staged} AS SELECT * FROM {view}")
    conn.unregister(view)
    return staged


# ---------------------------------------------------------------------------
# fresh_authoritative_rebase 预检（R1）
# ---------------------------------------------------------------------------
# 设计依据：docs/superpowers/specs/2026-07-29-fresh-authoritative-rebase-design.md §3
# 核心安全假设：xtquant front 为权威 oracle。rebase 的「对齐 + 传输 + 覆盖 + 守恒 +
# 原子写入 + 写后一致」由框架保证；不检测源端 fresh front 自身同步污染（信任边界）。
#
# 与 fresh_staged 的关键差异：
#   * 删除乘法（open/close ≈ k×raw）与加法（≤1 tick 偏移）比例/理想化校验（§3.3 D）。
#   * 日线要求「全历史严格覆盖」（缺/多/重复行一律 BLOCK），而非 tol 容忍缺失。
#   * 新增 fresh raw 与库内 raw 逐 bar 精确对齐（核心安全网，替代乘法/加法假设）。
# ---------------------------------------------------------------------------

def _check_daily_coverage_strict(conn, asset_type, code, df):
    """rebase 日线全历史覆盖：target == staged == matched，缺/多/重复行一律 BLOCK。"""
    daily_table, _ = _tables_of(asset_type)
    view = f"_qfq_cov_d_{code}"
    conn.register(view, df[["code", "time"]])
    try:
        target_count = int(conn.execute(
            f"SELECT COUNT(*) FROM {daily_table} WHERE code=?", [code]).fetchone()[0])
        staged_count = int(conn.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0])
        matched = int(conn.execute(
            f"SELECT COUNT(*) FROM {daily_table} t JOIN {view} s "
            f"ON t.code=s.code AND t.time=s.time WHERE t.code=?", [code]).fetchone()[0])
    finally:
        conn.unregister(view)
    if df.duplicated(subset=["code", "time"]).any():
        raise ReanchorBlocked("daily_coverage_duplicate",
            f"code={code} fresh 日线 (code,time) 存在重复行")
    missing_target = target_count - matched
    extra = staged_count - matched
    if missing_target != 0 or extra != 0:
        raise ReanchorBlocked("daily_coverage_mismatch",
            f"rebase 日线覆盖不全：缺 {missing_target}/多 {extra} 行"
            f"（要求全历史严格一致，禁止缺行/增行）")


def _check_daily_raw_align(conn, asset_type, code, df, tol):
    """rebase 日线 raw 逐 bar 对齐：fresh raw OHLC 与库内 raw 逐 bar 精确一致（|Δ|≤eps）。

    保障范围：证明 fresh 与库内 raw 来源对齐、未被传输错位/串码/截断（信任边界不覆盖
    fresh front 自身污染）。
    """
    daily_table, _ = _tables_of(asset_type)
    view = f"_qfq_raw_d_{code}"
    conn.register(view, df[["code", "time"] + list(RAW_COLS)])
    try:
        raw_pred = " OR ".join(
            f"ABS(t.{c} - s.{c}) > {_RAW_MATCH_EPS!r}" for c in RAW_COLS)
        n = int(conn.execute(
            f"SELECT COUNT(*) FROM {daily_table} t JOIN {view} s "
            f"ON t.code=s.code AND t.time=s.time WHERE t.code=? AND ({raw_pred})",
            [code]).fetchone()[0])
    finally:
        conn.unregister(view)
    if n:
        raise ReanchorBlocked("daily_raw_mismatch",
            f"日线 raw OHLC 共 {n} 行与库内不一致（|Δ|>{_RAW_MATCH_EPS}）；"
            f"fresh raw 与库内 raw 未对齐，无法安全 rebase")
    view2 = f"_qfq_raw_d2_{code}"
    conn.register(view2, df[["code", "time"] + list(RAW_COLS)])
    try:
        inv = " OR ".join(
            f"t.{c} IS NULL OR NOT isfinite(t.{c}) OR t.{c} <= 0 OR "
            f"s.{c} IS NULL OR NOT isfinite(s.{c}) OR s.{c} <= 0"
            for c in RAW_COLS)
        ninv = int(conn.execute(
            f"SELECT COUNT(*) FROM {daily_table} t JOIN {view2} s "
            f"ON t.code=s.code AND t.time=s.time WHERE t.code=? AND ({inv})",
            [code]).fetchone()[0])
    finally:
        conn.unregister(view2)
    if ninv:
        raise ReanchorBlocked("daily_raw_invalid",
            f"日线 raw 存在 NULL/NaN/Inf/<=0 共 {ninv} 根（raw 被污染）")


def _stage_fresh_daily_rebase(conn, asset_type, code, fresh_daily, tol, calendar):
    """rebase 日线预检 + 构建 STAGED_DAILY：A 基本 + B 全历史严格覆盖 + C raw 逐 bar 对齐。

    与 stage_fresh_daily 的差异：**不做乘法/加法比例校验**；覆盖要求全历史严格一致；
    新增 fresh raw 与库内 raw 逐 bar 精确对齐。
    """
    tol = tol or ReanchorTolerances()
    asset_type = _normalize_asset_type(asset_type)
    code = _normalize_code(code)
    if fresh_daily is None or len(fresh_daily) == 0:
        raise ReanchorBlocked("fresh_daily_empty", f"code={code} fresh 日线为空")
    missing = [c for c in _STAGED_REQUIRED if c not in fresh_daily.columns]
    if missing:
        raise ReanchorBlocked("fresh_daily_missing_cols", f"缺列 {missing}")
    df = fresh_daily.copy()
    bad_code = df["code"].astype(str).str.strip() != code
    if bool(bad_code.any()):
        raise ReanchorBlocked("fresh_daily_code_mismatch",
            f"存在非 {code} 行: {df.loc[bad_code, 'code'].unique()[:5]}")
    for t in df["time"]:
        _validate_epoch_ms(t)
    df["time"] = df["time"].astype("int64")
    if df.duplicated(subset=["code", "time"]).any():
        raise ReanchorBlocked("fresh_daily_dup_key", "staged (code,time) 存在重复")
    # A 基本：finite>0（删除乘法/加法比例校验；K 线关系由 postcheck kline_relation 负责）
    for _, row in df.iterrows():
        if not _is_finite_pos(row["close"]) or not _is_finite_pos(row["close_front"]):
            raise ReanchorBlocked("fresh_daily_bad_close",
                f"time={row['time']} close={row['close']!r} close_front={row['close_front']!r}")
        for c in ("open", "high", "low", "open_front", "high_front", "low_front"):
            v = row[c]
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                if not _is_finite_pos(v):
                    raise ReanchorBlocked("fresh_daily_bad_value",
                        f"time={row['time']} {c}={v!r}")
    # B 全历史严格覆盖（缺/多/重复行一律 BLOCK）
    _check_daily_coverage_strict(conn, asset_type, code, df)
    # C raw 逐 bar 对齐
    _check_daily_raw_align(conn, asset_type, code, df, tol)
    staged = f"qfq_staged_fresh_daily_{code}"
    view = f"_qfq_staged_view_{code}"
    conn.register(view, df[list(_STAGED_REQUIRED)])
    conn.execute(f"DROP TABLE IF EXISTS {staged}")
    conn.execute(f"CREATE TEMP TABLE {staged} AS SELECT * FROM {view}")
    conn.unregister(view)
    return staged


def _fresh_minutes_basic_light(fm, code, freq_c):
    """rebase 分钟 A 基本轻量初检（列/code/dup/time/finite>0）；详尽 session/交易日校验
    由 apply 内 stage_fresh_minutes 复核。"""
    missing = [c for c in _STAGED_REQUIRED if c not in fm.columns]
    if missing:
        raise ReanchorBlocked("fresh_minutes_missing_cols",
            f"freq={freq_c} 缺列 {missing}")
    bad_code = fm["code"].astype(str).str.strip() != code
    if bool(bad_code.any()):
        raise ReanchorBlocked("fresh_minutes_code_mismatch",
            f"存在非 {code} 行: {fm.loc[bad_code, 'code'].unique()[:5]}")
    for t in fm["time"]:
        _validate_epoch_ms(t)
    if fm.duplicated(subset=["code", "time", "freq"]).any():
        raise ReanchorBlocked("fresh_minutes_dup_key",
            f"freq={freq_c} (code,time,freq) 存在重复")
    for c in RAW_COLS + FRONT_COLS:
        v = fm[c].astype(float)
        bad = v.isna() | ~np.isfinite(v) | (v <= 0)
        if bool(bad.any()):
            t_bad = int(fm.loc[bad, "time"].iloc[0])
            raise ReanchorBlocked("fresh_minutes_null_or_bad",
                f"freq={freq_c} 列 {c} 存在 NULL/非 finite>0（首例 time={t_bad}）")


def _check_minute_cov_raw(conn, asset_type, code, freq_c, fm, tol):
    """rebase 分钟预检 B+C：全历史严格覆盖（缺/多/重复 0）+ raw 逐 bar 对齐。

    fresh_staged 的覆盖/raw 对齐在 apply_fresh_minute_staged（B 部分）与 postcheck
    minute_raw_match 中；rebase 跳过后检，故在此预检显式承担（§3.C 核心安全网）。
    """
    _, minute_table = _tables_of(asset_type)
    mfreq = _canon_minute_freq(freq_c)
    view = f"_qfq_cov_m_{code}_{mfreq}"
    conn.register(view, fm[["code", "time"] + list(RAW_COLS)])
    try:
        target_count = int(conn.execute(
            f"SELECT COUNT(*) FROM {minute_table} WHERE code=? AND freq=?",
            [code, mfreq]).fetchone()[0])
        staged_count = int(conn.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0])
        matched = int(conn.execute(
            f"SELECT COUNT(*) FROM {minute_table} t JOIN {view} s "
            f"ON t.code=s.code AND t.time=s.time AND t.freq=? "
            f"WHERE t.code=?", [mfreq, code]).fetchone()[0])
    finally:
        conn.unregister(view)
    if fm.duplicated(subset=["code", "time", "freq"]).any():
        raise ReanchorBlocked("minute_coverage_duplicate",
            f"rebase 分钟 freq={mfreq} (code,time,freq) 重复")
    missing_target = target_count - matched
    extra = staged_count - matched
    if missing_target != 0 or extra != 0:
        raise ReanchorBlocked("minute_coverage_mismatch",
            f"rebase 分钟 freq={mfreq} 覆盖不全：缺 {missing_target}/多 {extra} 行"
            f"（要求全历史严格一致）")
    # C raw 逐 bar 对齐（同 postcheck minute_raw_match 口径）
    # ETF 分钟放宽到 1 个 tick（0.001）；STOCK/日线严格。
    _eps = _raw_match_eps_minute(asset_type)
    view2 = f"_qfq_raw_m_{code}_{mfreq}"
    conn.register(view2, fm[["code", "time"] + list(RAW_COLS)])
    try:
        raw_invalid = " OR ".join(
            f"t.{c} IS NULL OR NOT isfinite(t.{c}) OR t.{c} <= 0 OR "
            f"s.{c} IS NULL OR NOT isfinite(s.{c}) OR s.{c} <= 0"
            for c in RAW_COLS)
        raw_pred = " OR ".join(
            f"ABS(t.{c} - s.{c}) > {_eps!r}" for c in RAW_COLS)
        n_invalid = int(conn.execute(
            f"SELECT COUNT(*) FROM {minute_table} t JOIN {view2} s "
            f"ON t.code=s.code AND t.time=s.time AND t.freq=? "
            f"WHERE t.code=? AND ({raw_invalid})", [mfreq, code]).fetchone()[0])
        n_raw = int(conn.execute(
            f"SELECT COUNT(*) FROM {minute_table} t JOIN {view2} s "
            f"ON t.code=s.code AND t.time=s.time AND t.freq=? "
            f"WHERE t.code=? AND NOT ({raw_invalid}) AND ({raw_pred})",
            [mfreq, code]).fetchone()[0])
    finally:
        conn.unregister(view2)
    if n_invalid:
        raise ReanchorBlocked("minute_raw_mismatch",
            f"rebase 分钟 freq={mfreq} raw 存在 {n_invalid} 根 NULL/NaN/Inf/<=0"
            f"（raw 被污染，无法安全 rebase）")
    if n_raw:
        raise ReanchorBlocked("minute_raw_mismatch",
            f"rebase 分钟 freq={mfreq} raw OHLC 共 {n_raw} 行与库内不一致"
            f"（|Δ|>{_eps!r}）；fresh raw 与库内 raw 未对齐")


def stage_fresh_authoritative(conn, asset_type, code, fresh_daily, fresh_minutes,
                               canon_freqs, tol, calendar):
    """rebase 模型预检（A-D）→ 构建 STAGED_DAILY（分钟构建在 apply 循环内完成）。

    - 日线：A 基本 + B 全历史严格覆盖 + C raw 逐 bar 对齐（**删除乘法/加法比例校验**）。
    - 分钟：freq 集合必须与 canon_freqs 完全一致；逐 freq 做 C raw 逐 bar 对齐 +
      B 全历史严格覆盖（A 基本由 apply 内 stage_fresh_minutes 复核）。
    返回 STAGED_DAILY 临时表名。
    """
    staged_daily = _stage_fresh_daily_rebase(
        conn, asset_type, code, fresh_daily, tol, calendar)
    actual_freqs = {str(f) for f in fresh_minutes["freq"].unique()}
    if actual_freqs != set(canon_freqs):
        raise ReanchorBlocked("minute_freq_mismatch",
            f"fresh_minutes freq 集合 {sorted(actual_freqs)} 与预期 "
            f"{sorted(canon_freqs)} 不一致（不得缺频/多频）")
    for freq_c in canon_freqs:
        fm = fresh_minutes
        if "freq" in fm.columns:
            fm = fm[fm["freq"].astype(str).map(
                lambda f: _canon_minute_freq(f) if f else "") == freq_c]
        elif len(canon_freqs) > 1:
            raise ReanchorBlocked("fresh_minutes_missing_cols",
                f"多 freq（{canon_freqs}）时 fresh_minutes 必须含 freq 列")
        _fresh_minutes_basic_light(fm, code, freq_c)
        _check_minute_cov_raw(conn, asset_type, code, freq_c, fm, tol)
    return staged_daily


def apply_fresh_minute_staged(conn, asset_type: str, code: str, freq: str,
                              staged_minute: str,
                              tol: Optional[ReanchorTolerances] = None) -> Dict:
    """B-1 precheck + 逐值 UPDATE：staged fresh 分钟 front → 存量分钟表四个 front 列。

    precheck（任一失败抛 ReanchorBlocked，整券回滚）：
    - **raw 逐 bar 一致**：stored open/high/low/close vs staged raw 逐 bar
      |Δ| ≤ _RAW_MATCH_EPS；stored raw 存在 NULL/非 finite → BLOCK（无法验证）；
    - **完整覆盖**：staged_count == target_count == matched_count，
      missing_target / missing_staged / duplicates / raw_mismatch 全 0。

    UPDATE 契约：按 (code,freq,time) 对齐，**仅** UPDATE 四个 front 列；不触碰
    raw OHLC、volume/amount/preClose/*_back/update_time/data_source；不 DELETE、
    不 INSERT、不重建。

    返回覆盖统计 dict（写入事件审计与 postcheck minute_coverage）。
    """
    tol = tol or ReanchorTolerances()
    asset_type = _normalize_asset_type(asset_type)
    code = _normalize_code(code)
    freq_c = _canon_minute_freq(freq)
    _, minute_table = _tables_of(asset_type)

    staged_count = int(conn.execute(
        f"SELECT COUNT(*) FROM {staged_minute}").fetchone()[0])
    target_count = int(conn.execute(
        f"SELECT COUNT(*) FROM {minute_table} WHERE code=? AND freq=?",
        [code, freq_c]).fetchone()[0])
    dup_target = int(conn.execute(
        f"SELECT COUNT(*) - COUNT(DISTINCT time) FROM {minute_table} "
        f"WHERE code=? AND freq=?", [code, freq_c]).fetchone()[0])
    matched_count = int(conn.execute(
        f"SELECT COUNT(*) FROM {minute_table} t JOIN {staged_minute} s "
        f"ON t.time = s.time WHERE t.code=? AND t.freq=?",
        [code, freq_c]).fetchone()[0])
    missing_target = int(conn.execute(
        f"SELECT COUNT(*) FROM {staged_minute} s LEFT JOIN "
        f"(SELECT time FROM {minute_table} WHERE code=? AND freq=?) t "
        f"ON t.time = s.time WHERE t.time IS NULL",
        [code, freq_c]).fetchone()[0])
    missing_staged = int(conn.execute(
        f"SELECT COUNT(*) FROM {minute_table} t LEFT JOIN {staged_minute} s "
        f"ON t.time = s.time WHERE t.code=? AND t.freq=? AND s.time IS NULL",
        [code, freq_c]).fetchone()[0])
    coverage = {
        "freq": freq_c, "staged_count": staged_count,
        "target_count": target_count, "matched_count": matched_count,
        "missing_target": missing_target, "missing_staged": missing_staged,
        "duplicates": dup_target,
    }
    if not (staged_count == target_count == matched_count
            and missing_target == 0 and missing_staged == 0 and dup_target == 0):
        raise ReanchorBlocked(
            "minute_coverage_incomplete",
            f"freq={freq_c} 覆盖不完整 {coverage}（B-1 要求 staged==target=="
            f"matched 且 missing/duplicates 全 0，整券 BLOCK）",
            coverage=coverage, phase="precheck", freq=freq_c)

    # —— raw 逐 bar 一致：stored raw NULL/非 finite → BLOCK；|Δ|>eps → BLOCK ——
    null_raw = int(conn.execute(
        f"SELECT COUNT(*) FROM {minute_table} WHERE code=? AND freq=? AND ("
        f"open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR "
        f"NOT (isfinite(open) AND isfinite(high) AND isfinite(low) AND "
        f"isfinite(close)) OR open<=0 OR high<=0 OR low<=0 OR close<=0)",
        [code, freq_c]).fetchone()[0])
    if null_raw:
        raise ReanchorBlocked(
            "minute_raw_null",
            f"freq={freq_c} stored raw OHLC 存在 NULL/非 finite>0 共 {null_raw} 根"
            f"——无法完成 raw 一致性验证，整券 BLOCK",
            coverage=coverage, phase="precheck", freq=freq_c)
    _eps = _raw_match_eps_minute(asset_type)
    raw_pred = " OR ".join(
        f"ABS(t.{c} - s.{c}) > {_eps!r}" for c in RAW_COLS)
    mism = conn.execute(
        f"SELECT COUNT(*), MIN(t.time) FROM {minute_table} t "
        f"JOIN {staged_minute} s ON t.time = s.time "
        f"WHERE t.code=? AND t.freq=? AND ({raw_pred})",
        [code, freq_c]).fetchone()
    raw_mismatch = int(mism[0])
    coverage["raw_mismatch"] = raw_mismatch
    if raw_mismatch:
        raise ReanchorBlocked(
            "minute_raw_mismatch",
            f"freq={freq_c} stored raw OHLC vs fresh(dividend_type=none) raw "
            f"逐 bar 不一致 {raw_mismatch} 根（首例 time={int(mism[1])}，"
            f"eps={_eps:.0e}）——同源前提不成立，整券 BLOCK",
            coverage=coverage, phase="precheck", freq=freq_c)

    # —— 逐值 UPDATE：仅四个 front 列 ——
    conn.execute(
        f"UPDATE {minute_table} AS t SET "
        f"open_front = s.open_front, high_front = s.high_front, "
        f"low_front = s.low_front, close_front = s.close_front "
        f"FROM {staged_minute} AS s "
        f"WHERE t.time = s.time AND t.code = ? AND t.freq = ?",
        [code, freq_c])
    coverage["updated_bars"] = matched_count
    return coverage


# ---------------------------------------------------------------------------
# 3. 方法 A 黄金抽验
# ---------------------------------------------------------------------------

def select_golden_samples(bars_df: pd.DataFrame,
                          segments: Sequence[RatioSegment],
                          calendar: CalendarService,
                          ex_dates_ms: Sequence[int] = (),
                          tol: Optional[ReanchorTolerances] = None,
                          ) -> List[Dict]:
    """为每个**拟修正段**选黄金样本 —— **严格硬下限**：

    - 任一 needs_update 区间少于 ``min_golden_days``（默认 3）个**真实交易日**（以
      CalendarService 核验）→ 整券 BLOCK（golden_insufficient）；
    - 任一抽验日少于 ``min_golden_bars_per_day``（默认 5）根**有效连续竞价** bar →
      整券 BLOCK；**09:30 集合竞价 bar 不得计入/补足连续竞价样本数**，仅作附加样本。

    覆盖：区间首部、尾部、除权日前一交易日、分段边界两侧。
    返回样本列表 [{time, seg_idx, ratio, kind}]（按 time 去重）。
    """
    tol = tol or ReanchorTolerances()
    samples: Dict[int, Dict] = {}

    def _add(t: int, seg_i: int, ratio: float, kind: str):
        t = int(t)
        if t not in samples:
            samples[t] = {"time": t, "seg_idx": seg_i, "ratio": ratio, "kind": kind}

    seg_needs = [(i, s) for i, s in enumerate(segments) if s.needs_update]
    for i, seg in seg_needs:
        sub = bars_df[bars_df["seg_idx"] == i]
        days = sorted(set(int(d) for d in sub["day"]))
        if not days:
            raise ReanchorBlocked("golden_no_bars", f"段 {i} 无可抽验 bar")
        # —— 严格下限 1：真实交易日数（CalendarService 核验，非"有多少抽多少"）——
        trading_days = [d for d in days if calendar.is_trading_day(d)]
        if len(trading_days) < tol.min_golden_days:
            raise ReanchorBlocked(
                "golden_insufficient",
                f"段 {i} 需修正区间仅 {len(trading_days)} 个真实交易日 < 硬下限 "
                f"{tol.min_golden_days}（不得放宽为有多少抽多少，整券 BLOCK）")
        days = trading_days
        # 抽验日：首日 + 末日 + 中位日 + 除权日前一交易日（若在段内）
        want_days = {days[0], days[-1], days[len(days) // 2]}
        for ex in ex_dates_ms:
            try:
                prev_td = calendar.prev_trading_day(int(ex))
            except LookupError:
                prev_td = None
            if prev_td is not None and prev_td in days:
                want_days.add(prev_td)
        # 天数下限：不足 min_golden_days 则从剩余日补足
        for d in days:
            if len(want_days) >= tol.min_golden_days:
                break
            want_days.add(d)
        for d in sorted(want_days):
            day_bars = sub[sub["day"] == d].sort_values("time")
            # session-aware：与 cross_table_overlap 共用 _cont_mask（合法双时段
            # + 合法 end-labeled cadence；午间/集合竞价/非法时刻不计入）
            cont = day_bars[_cont_mask(day_bars["clock_min"],
                                       _canon_minute_freq(seg.freq))]
            # —— 严格下限 2：每抽验日 ≥ min_golden_bars_per_day 根有效连续竞价 bar；
            #    09:30 集合竞价不得补足 ——
            if len(cont) < tol.min_golden_bars_per_day:
                raise ReanchorBlocked(
                    "golden_insufficient",
                    f"段 {i} 抽验日 {d} 有效连续竞价 bar 仅 {len(cont)} 根 < 硬下限 "
                    f"{tol.min_golden_bars_per_day}（09:30 集合竞价不得补足，整券 BLOCK）")
            n_bar = tol.min_golden_bars_per_day
            idxs = sorted({int(round(q * (len(cont) - 1)))
                           for q in np.linspace(0.0, 1.0, n_bar)})
            for j in idxs:
                _add(cont["time"].iloc[j], i, seg.ratio, "representative")
            auction = day_bars[day_bars["clock_min"] == _AUCTION_CLOCK]
            if len(auction):  # 09:30 仅附加样本
                _add(auction["time"].iloc[0], i, seg.ratio, "auction_extra")
        # 区间首/尾 bar
        _add(sub["time"].iloc[0], i, seg.ratio, "interval_head")
        _add(sub["time"].iloc[-1], i, seg.ratio, "interval_tail")

    # 分段边界两侧（含 noop 段一侧，其比对基准为该段自身 ratio）
    for b in range(1, len(segments)):
        prev_bars = bars_df[bars_df["seg_idx"] == b - 1]
        next_bars = bars_df[bars_df["seg_idx"] == b]
        if len(prev_bars):
            _add(prev_bars["time"].iloc[-1], b - 1, segments[b - 1].ratio, "boundary_left")
        if len(next_bars):
            _add(next_bars["time"].iloc[0], b, segments[b].ratio, "boundary_right")
    return sorted(samples.values(), key=lambda x: x["time"])


def golden_check_method_a(bars_df: pd.DataFrame,
                          samples: Sequence[Dict],
                          golden_minutes: Optional[pd.DataFrame],
                          freq: str,
                          tol: Optional[ReanchorTolerances] = None) -> Dict:
    """方法 A：R_golden = fresh_xtquant_minute_front / stored_minute_front，逐样本
    与方法 B 段 ratio 比对。任一样本超容差 → 整个证券 BLOCK（不得取平均后继续）。

    golden_minutes 列约定：time + close_front（fresh 前复权收盘）；可含 freq 列
    （含则按 canonical freq 过滤）。缺失必需样本 → BLOCK("golden_insufficient")。
    返回逐样本报告 {"freq", "samples": [...], "max_dev"}。
    """
    tol = tol or ReanchorTolerances()
    freq_c = _canon_minute_freq(freq)
    if not samples:
        return {"freq": freq_c, "samples": [], "max_dev": 0.0}
    if golden_minutes is None or len(golden_minutes) == 0:
        raise ReanchorBlocked("golden_data_missing",
                              f"freq={freq_c} 需要修正但未提供 fresh 黄金分钟数据")
    g = golden_minutes
    if "freq" in g.columns:
        g = g[g["freq"].astype(str).map(
            lambda f: _canon_minute_freq(f) if f else "") == freq_c]
    gmap = {int(t): float(v) for t, v in zip(g["time"], g["close_front"])
            if v is not None and not (isinstance(v, float) and math.isnan(v))}
    smap = {int(t): float(v) for t, v in zip(bars_df["time"], bars_df["close_front"])}

    report = []
    max_dev = 0.0
    for s in samples:
        t = int(s["time"])
        stored_front = smap.get(t)
        golden_front = gmap.get(t)
        if golden_front is None:
            raise ReanchorBlocked(
                "golden_insufficient",
                f"freq={freq_c} 样本 time={t}（{s['kind']}）缺 fresh 黄金数据")
        if stored_front is None or not _is_finite_pos(stored_front):
            raise ReanchorBlocked(
                "golden_bad_stored", f"freq={freq_c} time={t} stored front 非 finite>0")
        if not _is_finite_pos(golden_front):
            raise ReanchorBlocked(
                "golden_bad_fresh", f"freq={freq_c} time={t} fresh front 非 finite>0")
        r_golden = golden_front / stored_front
        dev = abs(r_golden / float(s["ratio"]) - 1.0)
        max_dev = max(max_dev, dev)
        report.append({"time": t, "kind": s["kind"], "seg_idx": s["seg_idx"],
                       "ratio_b": float(s["ratio"]), "r_golden": r_golden,
                       "dev": dev})
        if dev > tol.golden_rel_tol:
            raise ReanchorBlocked(
                "golden_mismatch",
                f"freq={freq_c} time={t}（{s['kind']}）R_golden={r_golden:.8f} vs "
                f"方法B R={s['ratio']:.8f} dev={dev:.2e} > {tol.golden_rel_tol:.2e}"
                f"——整个证券 BLOCK，不取平均")
    return {"freq": freq_c, "samples": report, "max_dev": max_dev}


# ---------------------------------------------------------------------------
# 4. COMMIT 前硬门禁 postcheck
# ---------------------------------------------------------------------------

def run_postchecks(conn, *, asset_type: str, code: str,
                   staged_table: str, freqs: Sequence[str],
                   calendar: CalendarService,
                   pre_counts: Dict,
                   tol: Optional[ReanchorTolerances] = None,
                   list_date_ms: Optional[int] = None,
                   model: str = "ratio",
                   staged_minutes: Optional[Dict[str, str]] = None) -> Dict:
    """全部 COMMIT 前硬门禁。任一失败抛 PostcheckFailed（调用方 ROLLBACK）。

    检查项（ratio 模式，原六项逐位不变）：daily_staged_match /
    front_chain_return / scale_consistency / kline_relation / row_conservation /
    cross_table_overlap。

    ``model="fresh_staged"``（B-1）额外差异：
    - front_chain_return / scale_consistency 按模型感知形式执行：每行
      乘法偏离 ≤ 原容差 **或** 加法偏离 ≤ 1 tick，二者满足其一（xtquant 现金
      分红为减法复权，乘法偏离天然不恒定）；
    - 新增四项：minute_staged_match（写入后四 front 列 vs staged 逐值一致）/
      minute_raw_match（写入后 raw OHLC vs staged raw 仍逐 bar 一致，证明
      UPDATE 未触碰 raw）/ minute_coverage（staged==target==matched 且
      missing/duplicates 全 0）/ minute_tick_error（写入后四 front 列 vs fresh
      逐 bar |Δ| ≤ 1 tick，bars_over_1_tick 必须为 0）。
      ``staged_minutes``：{canonical freq: staged 分钟表名}，fresh_staged 模式
      必须提供且覆盖全部 freqs。

    ``list_date_ms``：security master 的上市日（epoch-ms 00:00 +08）。它是
    front-chain 首日缺上一交易日行时的**唯一**豁免凭据；不传（None）则任何
    缺行都回滚。禁止用本地 MIN(time) 推断上市首日。
    """
    tol = tol or ReanchorTolerances()
    if model not in MODELS:
        raise ValueError(f"未知模型 {model!r}，仅支持 {MODELS}")
    asset_type = _normalize_asset_type(asset_type)
    code = _normalize_code(code)
    daily_table, minute_table = _tables_of(asset_type)
    tick = resolve_tick_size(asset_type, tol)  # 第六轮阻断 4：tick 按资产路由
    details: Dict = {}

    staged_df = conn.execute(
        f"SELECT time, close, close_front FROM {staged_table} ORDER BY time").df()
    staged_days = sorted(int(t) for t in staged_df["time"])
    span_lo, span_hi = staged_days[0], staged_days[-1]

    # ---- (1) 日线 staged 一致 + 全覆盖：staged 每一行都必须命中正式日线 ----
    staged_count = int(conn.execute(
        f"SELECT COUNT(*) FROM {staged_table}").fetchone()[0])
    mismatch = conn.execute(
        f"SELECT COUNT(*) FROM {daily_table} t JOIN {staged_table} s "
        f"ON t.code = s.code AND t.time = s.time WHERE t.code = ? AND ("
        f"t.open_front IS DISTINCT FROM s.open_front OR "
        f"t.high_front IS DISTINCT FROM s.high_front OR "
        f"t.low_front IS DISTINCT FROM s.low_front OR "
        f"t.close_front IS DISTINCT FROM s.close_front)",
        [code]).fetchone()[0]
    matched = conn.execute(
        f"SELECT COUNT(*) FROM {daily_table} t JOIN {staged_table} s "
        f"ON t.code = s.code AND t.time = s.time WHERE t.code = ?", [code]).fetchone()[0]
    missing_rows = conn.execute(
        f"SELECT s.time FROM {staged_table} s LEFT JOIN {daily_table} t "
        f"ON t.code = s.code AND t.time = s.time AND t.code = ? "
        f"WHERE t.time IS NULL ORDER BY s.time", [code]).fetchall()
    missing_target = len(missing_rows)
    details["daily_staged_match"] = {
        "staged_count": staged_count, "matched": int(matched),
        "missing_target": int(missing_target), "mismatch": int(mismatch)}
    if int(mismatch) != 0:
        raise PostcheckFailed("daily_staged_match",
                              f"{mismatch} 行日线四列与 staged 不一致")
    if missing_target != 0 or int(matched) != staged_count:
        raise PostcheckFailed(
            "daily_staged_match",
            f"staged 未全覆盖：staged_count={staged_count} matched={matched} "
            f"missing_target={missing_target}（未命中日 "
            f"{[int(r[0]) for r in missing_rows[:5]]}），staged 行不得被静默忽略")
    # staged 每个日期必须为真实交易日（CalendarService 核验；未知日不得静默通过）
    non_trading = [t for t in staged_days if not calendar.is_trading_day(t)]
    if non_trading:
        raise PostcheckFailed(
            "daily_staged_match",
            f"staged 存在非交易日日期 {non_trading[:5]}（共 {len(non_trading)} 个）")

    # ---- rebase 模型：跳过 (2) front-chain 收益 / (3) 缩放一致性 / (7)-(10) fresh_staged
    #      专属四项（§3.3 删除乘法/加法假设 + 理想化模型假设）。安全责任由「raw 逐 bar
    #      对齐预检」+「写后一致（UPDATE 只触碰 front 四列）」承担。仅跑模型无关检查
    #      (1)(4)(5)(6)。这是为了在真实除权场景下 rebase 不被乘法/加法假设 BLOCK。----
    if model == "fresh_authoritative_rebase":
        daily_full = conn.execute(
            f"SELECT time, open, high, low, close, open_front, high_front, low_front, "
            f"close_front FROM {daily_table} WHERE code=? AND time BETWEEN ? AND ? "
            f"ORDER BY time", [code, span_lo, span_hi]).df()
        details["front_chain_return"] = {
            "status": "skipped", "checked": 0,
            "reason": "fresh_authoritative_rebase 删除 front-chain 收益乘法/加法假设（§3.3）"}
        details["scale_consistency"] = {
            "status": "skipped", "daily_max_dev": 0.0, "minute_max_dev": {},
            "reason": "fresh_authoritative_rebase 删除缩放一致性（乘法/加法）假设（§3.3）"}
        # (4) K 线关系：low_front <= min(o,c) <= max(o,c) <= high_front（复用正常路径逻辑）
        kline_bad = {}
        for table, extra in ((daily_table, ""), *(
                (minute_table, f" AND freq='{_canon_minute_freq(f)}'") for f in freqs)):
            n_bad = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE code=?{extra} "
                f"AND open_front IS NOT NULL AND high_front IS NOT NULL "
                f"AND low_front IS NOT NULL AND close_front IS NOT NULL "
                f"AND NOT (low_front <= LEAST(open_front, close_front) "
                f"AND GREATEST(open_front, close_front) <= high_front)",
                [code]).fetchone()[0]
            key = table if not extra else f"{table}{extra.replace(' AND freq=', '@')}"
            kline_bad[key] = int(n_bad)
            if int(n_bad) != 0:
                raise PostcheckFailed("kline_relation", f"{key} 违反 K 线关系 {n_bad} 行")
        details["kline_relation"] = kline_bad
        # (5) 行数守恒
        post_counts = collect_row_counts(conn, asset_type, code, freqs)
        if post_counts != pre_counts:
            raise PostcheckFailed(
                "row_conservation", f"行数不守恒: pre={pre_counts} post={post_counts}")
        details["row_conservation"] = {"pre": pre_counts, "post": post_counts}
        # (6) 跨表重叠日
        cross = {}
        for freq in freqs:
            freq_c = _canon_minute_freq(freq)
            mdf = conn.execute(
                f"SELECT time, close_front FROM {minute_table} "
                f"WHERE code=? AND freq=? AND close_front IS NOT NULL ORDER BY time",
                [code, freq_c]).df()
            if len(mdf) == 0:
                continue
            mdf["day"] = _day_ms_of(mdf["time"])
            mdf["clock_min"] = _clock_min_of(mdf["time"])
            cont = mdf[_cont_mask(mdf["clock_min"], freq_c)]
            last_bar = cont.groupby("day").last()
            minute_days = {int(x) for x in mdf["day"].unique()}
            n_check, worst = 0, 0.0
            for _, drow in daily_full.iterrows():
                d = int(drow["time"])
                if d not in minute_days:
                    continue
                if d not in last_bar.index:
                    raise PostcheckFailed(
                        "cross_table_overlap",
                        f"freq={freq_c} day={d} 存在分钟行但无有效连续竞价 bar")
                dcf = drow["close_front"]
                mcf = float(last_bar.loc[d, "close_front"])
                if dcf is None or (isinstance(dcf, float) and math.isnan(dcf)):
                    continue
                dev = abs(mcf / float(dcf) - 1.0)
                worst = max(worst, dev)
                n_check += 1
                if dev > tol.tol_cross:
                    raise PostcheckFailed(
                        "cross_table_overlap",
                        f"freq={freq_c} day={d} 分钟末bar front={mcf} vs 日线 front="
                        f"{dcf} dev={dev:.2e} > {tol.tol_cross:.2e}")
            cross[freq_c] = {"checked": n_check, "max_dev": worst}
        details["cross_table_overlap"] = cross
        # §3.4 R2：rebase 复用 fresh_staged 三项写后逐 bar 一致（不跑 ≤1 tick 理想校验）
        if staged_minutes:
            d_sm, d_rm, d_cov, _ = _run_minute_staged_postchecks(
                conn, asset_type=asset_type, code=code, freqs=freqs,
                staged_minutes=staged_minutes, minute_table=minute_table,
                tick=tick, include_tick_error=False)
            details["minute_staged_match"] = d_sm
            details["minute_raw_match"] = d_rm
            details["minute_coverage"] = d_cov
        else:
            raise PostcheckFailed(
                "minute_staged_match",
                "fresh_authoritative_rebase 模式缺 staged 分钟表（必须提供）")
        details["model"] = model
        return details

    # ---- (2) front-chain 收益一致性（真实相邻交易日；缺失日不得静默当停牌）----
    drows = conn.execute(
        f"SELECT time, close, preClose, close_front FROM {daily_table} "
        f"WHERE code = ? AND time BETWEEN ? AND ? ORDER BY time",
        [code, span_lo, span_hi]).df()
    dmap = {int(r["time"]): r for _, r in drows.iterrows()}
    checked = 0
    max_ret_dev = 0.0
    chain_start: Optional[int] = None
    for t in sorted(dmap):
        try:
            prev_td = calendar.prev_trading_day(t)   # CalendarService 确认真实相邻交易日
        except LookupError as exc:
            # 日历无法证明 t 的真实上一交易日（provider 延伸耗尽/无 provider）。
            # fail-closed：除非有 security master list_date 明确证据证明 t 为上市
            # 首日，否则确定性回滚——绝不允许 crash 穿透或静默 committed。
            if list_date_ms is not None and int(t) == int(list_date_ms):
                chain_start = int(t)
                continue
            raise PostcheckFailed(
                "front_chain_missing_prev",
                f"无法确认 {t} 的真实上一交易日（{exc}）；缺日历证据不得静默"
                f"通过，上市首日豁免需 list_date 明确证据（当前 list_date_ms="
                f"{list_date_ms!r}）") from exc
        if prev_td not in dmap:
            # 首个 staged 交易日：上一真实交易日在修正范围之外 —— **必须**读取
            # 范围外真实行校验，不得跳过（否则修正范围首日的 front-chain 断裂
            # 将静默通过）。缺行 → front_chain_missing_prev 回滚。
            #
            # 第三轮对抗审核修复：废除基于 MIN(time)==t 的通用豁免——本地表第一行
            # 只能证明"本库没有更早的数据"，**不能**证明 t 是上市首日（截断数据
            # 会被误当上市首日而静默 committed）。唯一豁免凭据 = 调用方传入的
            # security master ``list_date_ms``：仅当 t 就是该证券上市日、上市日
            # 之前不存在任何交易数据时才允许 front 链自 t 起。其余一律回滚。
            ext = conn.execute(
                f"SELECT time, close, preClose, close_front FROM {daily_table} "
                f"WHERE code = ? AND time = ?", [code, prev_td]).df()
            if len(ext) == 0:
                if list_date_ms is not None and int(t) == int(list_date_ms):
                    chain_start = int(t)
                    continue
                raise PostcheckFailed(
                    "front_chain_missing_prev",
                    f"日线缺 {prev_td}（{t} 的真实上一交易日"
                    f"{'，位于修正范围之外' if prev_td < span_lo else ''}）"
                    f"——缺失日不能静默当停牌；上市首日豁免需 security master "
                    f"list_date 明确证据（当前 list_date_ms="
                    f"{list_date_ms!r}），本地 MIN(time) 不构成证据")
            dmap[prev_td] = ext.iloc[0]
        row, prow = dmap[t], dmap[prev_td]
        cf_t, cf_p = row["close_front"], prow["close_front"]
        c_t, pc_t = row["close"], row["preClose"]
        for name, v in (("close_front(t)", cf_t), ("close_front(prev)", cf_p),
                        ("close(t)", c_t), ("preClose(t)", pc_t)):
            if v is None or (isinstance(v, float) and math.isnan(v)) or not _is_finite_pos(v):
                raise PostcheckFailed(
                    "front_chain_bad_value", f"time={t} {name}={v!r} 非 finite>0")
        fc_ret = float(cf_t) / float(cf_p) - 1.0
        ref_ret = float(c_t) / float(pc_t) - 1.0
        dev = abs(fc_ret - ref_ret)
        max_ret_dev = max(max_ret_dev, dev)
        checked += 1
        if dev > tol.tol_return:
            if model == "fresh_staged":
                # 减法复权模型感知豁免：两日的减法偏移必须一致（≤1 tick）。
                # |(close_t − cf_t) − (preClose_t − cf_prev)| ≤ tick_size
                add_dev = abs((float(c_t) - float(cf_t))
                              - (float(pc_t) - float(cf_p)))
                if add_dev <= tick:
                    continue
                raise PostcheckFailed(
                    "front_chain_return",
                    f"time={t}（model=fresh_staged）乘法收益 dev={dev:.2e} > "
                    f"{tol.tol_return:.2e} 且加法偏移差 {add_dev:.4f} > "
                    f"1 tick={tick}")
            raise PostcheckFailed(
                "front_chain_return",
                f"time={t} front链收益 {fc_ret:.6f} vs 参考收益 {ref_ret:.6f} "
                f"dev={dev:.2e} > {tol.tol_return:.2e}")
    details["front_chain_return"] = {"checked": checked, "max_dev": max_ret_dev,
                                     "chain_start": chain_start}

    # ---- (3) 缩放一致性：X_front/X 四列同一比例（日线 span 内 + 分钟重叠日）----
    def _scale_check(df: pd.DataFrame, where: str):
        sc = df["close_front"].astype(float) / df["close"].astype(float)
        add_ref = df["close"].astype(float) - df["close_front"].astype(float)
        base = df["close"].astype(float).notna() & df["close_front"].astype(float).notna()
        worst = 0.0
        for x, xf in zip(("open", "high", "low"), ("open_front", "high_front", "low_front")):
            xv, xfv = df[x].astype(float), df[xf].astype(float)
            m = base & xv.notna() & xfv.notna() & (xv > 0)
            if not m.any():
                continue
            dev = ((xfv[m] / xv[m]) / sc[m] - 1.0).abs()
            worst = max(worst, float(dev.max()))
            bad = dev > tol.tol_scale
            if model == "fresh_staged":
                # 减法复权豁免：加法偏离 ≤1 tick 亦可（每行二者满足其一）
                add_dev = ((xv[m] - xfv[m]) - add_ref[m]).abs()
                bad = bad & (add_dev > tick)
            if bool(bad.any()):
                t_bad = int(df.loc[bad[bad].index[0], "time"])
                raise PostcheckFailed(
                    "scale_consistency",
                    f"{where} time={t_bad} {xf}/{x} 与 close 缩放超容差"
                    f"（model={model}）max_mul_dev={float(dev.max()):.2e}")
        return worst

    daily_full = conn.execute(
        f"SELECT time, open, high, low, close, open_front, high_front, low_front, "
        f"close_front FROM {daily_table} WHERE code=? AND time BETWEEN ? AND ? "
        f"ORDER BY time", [code, span_lo, span_hi]).df()
    worst_scale = _scale_check(daily_full, "daily")
    minute_worst = {}
    for freq in freqs:
        freq_c = _canon_minute_freq(freq)
        mdf = conn.execute(
            f"SELECT time, open, high, low, close, open_front, high_front, low_front, "
            f"close_front FROM {minute_table} WHERE code=? AND freq=? ORDER BY time",
            [code, freq_c]).df()
        if len(mdf) == 0:
            continue
        mdf["day"] = _day_ms_of(mdf["time"])
        mdf = mdf[mdf["day"].isin(set(staged_days))].reset_index(drop=True)
        if len(mdf):
            minute_worst[freq_c] = _scale_check(mdf, f"minute[{freq_c}]")
    details["scale_consistency"] = {"daily_max_dev": worst_scale,
                                    "minute_max_dev": minute_worst}

    # ---- (4) K 线关系：low_front <= min(o,c) <= max(o,c) <= high_front ----
    kline_bad = {}
    for table, extra in ((daily_table, ""), *(
            (minute_table, f" AND freq='{_canon_minute_freq(f)}'") for f in freqs)):
        n_bad = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE code=?{extra} "
            f"AND open_front IS NOT NULL AND high_front IS NOT NULL "
            f"AND low_front IS NOT NULL AND close_front IS NOT NULL "
            f"AND NOT (low_front <= LEAST(open_front, close_front) "
            f"AND GREATEST(open_front, close_front) <= high_front)",
            [code]).fetchone()[0]
        key = table if not extra else f"{table}{extra.replace(' AND freq=', '@')}"
        kline_bad[key] = int(n_bad)
        if int(n_bad) != 0:
            raise PostcheckFailed("kline_relation", f"{key} 违反 K 线关系 {n_bad} 行")
    details["kline_relation"] = kline_bad

    # ---- (5) 行数守恒：修正前后 (code,freq) 行数完全一致（含全表守恒）----
    post_counts = collect_row_counts(conn, asset_type, code, freqs)
    if post_counts != pre_counts:
        raise PostcheckFailed("row_conservation",
                              f"行数不守恒: pre={pre_counts} post={post_counts}")
    details["row_conservation"] = {"pre": pre_counts, "post": post_counts}

    # ---- (6) 跨表重叠日：当日最后一个有效连续竞价 bar vs 日线（按 freq 分组）----
    cross = {}
    for freq in freqs:
        freq_c = _canon_minute_freq(freq)
        mdf = conn.execute(
            f"SELECT time, close_front FROM {minute_table} "
            f"WHERE code=? AND freq=? AND close_front IS NOT NULL ORDER BY time",
            [code, freq_c]).df()
        if len(mdf) == 0:
            continue
        mdf["day"] = _day_ms_of(mdf["time"])
        mdf["clock_min"] = _clock_min_of(mdf["time"])
        # session-aware：与黄金抽样共用 _cont_mask（合法双时段 + 合法 cadence）
        cont = mdf[_cont_mask(mdf["clock_min"], freq_c)]
        last_bar = cont.groupby("day").last()  # 已按 time 升序 → last = 当日最后有效 bar
        minute_days = {int(x) for x in mdf["day"].unique()}
        n_check, worst = 0, 0.0
        for _, drow in daily_full.iterrows():
            d = int(drow["time"])
            if d not in minute_days:
                continue  # 该日无任何分钟行 → 无从比较（仅比 daily/minute 共存日）
            if d not in last_bar.index:
                # daily 与 minute 都存在，但没有任何**有效连续竞价** bar（例如仅
                # 09:30 集合竞价、或全部落在午间休市）——不得静默跳过当"checked=0
                # 但 committed"，必须回滚。
                raise PostcheckFailed(
                    "cross_table_overlap",
                    f"freq={freq_c} day={d} 存在分钟行但无有效连续竞价 bar"
                    f"（集合竞价/午间/非法时刻 bar 不得计入）——不得静默跳过")
            dcf = drow["close_front"]
            mcf = float(last_bar.loc[d, "close_front"])
            if dcf is None or (isinstance(dcf, float) and math.isnan(dcf)):
                continue
            dev = abs(mcf / float(dcf) - 1.0)
            worst = max(worst, dev)
            n_check += 1
            if dev > tol.tol_cross:
                raise PostcheckFailed(
                    "cross_table_overlap",
                    f"freq={freq_c} day={d} 分钟末bar front={mcf} vs 日线 front={dcf} "
                    f"dev={dev:.2e} > {tol.tol_cross:.2e}")
        cross[freq_c] = {"checked": n_check, "max_dev": worst}
    details["cross_table_overlap"] = cross

    # ---- (7)-(10) B-1 fresh_staged 专属四项（ratio 模式不出现，六项集合不变）----
    if model == "fresh_staged":
        staged_minutes = staged_minutes or {}
        d_sm, d_rm, d_cov, d_tk = _run_minute_staged_postchecks(
            conn, asset_type=asset_type, code=code, freqs=freqs,
            staged_minutes=staged_minutes, minute_table=minute_table,
            tick=tick, include_tick_error=True)
        details["minute_staged_match"] = d_sm
        details["minute_raw_match"] = d_rm
        details["minute_coverage"] = d_cov
        details["minute_tick_error"] = d_tk
    return details


def _run_minute_staged_postchecks(conn, *, asset_type: str, code: str,
                                  freqs: Sequence[str],
                                  staged_minutes: Dict[str, str],
                                  minute_table: str, tick: float,
                                  include_tick_error: bool):
    """B-1 写后逐 bar 一致校验（§3.4），供 fresh_staged 与 fresh_authoritative_rebase 共用。

    返回 (d_staged_match, d_raw_match, d_coverage, d_tick)。

    - ``include_tick_error=True``（fresh_staged）：额外做四 front vs fresh ≤1 tick 理想校验；
    - ``include_tick_error=False``（fresh_authoritative_rebase）：rebase 删除 ≤1 tick
      假设（§3.3），不跑该项（由 front_exact_match 直接替代）。

    三项确定性校验（minute_staged_match / minute_raw_match / minute_coverage）对两种模型
    都跑：证明 UPDATE 仅触 front 四列、raw 未被污染、覆盖完整。
    """
    d_staged_match: Dict[str, Dict] = {}
    d_raw_match: Dict[str, Dict] = {}
    d_coverage: Dict[str, Dict] = {}
    d_tick: Dict[str, Dict] = {}
    for freq in freqs:
        freq_c = _canon_minute_freq(freq)
        sm = staged_minutes.get(freq_c)
        if not sm:
            raise PostcheckFailed(
                "minute_staged_match",
                f"freq={freq_c} 缺 staged 分钟表（fresh staged/rebase 模式必须提供）")
        # (7) minute_staged_match：写入后四 front 列 vs staged 逐值一致
        n_mis = int(conn.execute(
            f"SELECT COUNT(*) FROM {minute_table} t JOIN {sm} s "
            f"ON t.time = s.time WHERE t.code=? AND t.freq=? AND ("
            f"t.open_front IS DISTINCT FROM s.open_front OR "
            f"t.high_front IS DISTINCT FROM s.high_front OR "
            f"t.low_front IS DISTINCT FROM s.low_front OR "
            f"t.close_front IS DISTINCT FROM s.close_front)",
            [code, freq_c]).fetchone()[0])
        n_match = int(conn.execute(
            f"SELECT COUNT(*) FROM {minute_table} t JOIN {sm} s "
            f"ON t.time = s.time WHERE t.code=? AND t.freq=?",
            [code, freq_c]).fetchone()[0])
        d_staged_match[freq_c] = {"matched": n_match, "mismatch": n_mis}
        if n_mis:
            raise PostcheckFailed(
                "minute_staged_match",
                f"freq={freq_c} 写入后 {n_mis} 根 bar 四 front 列与 staged 不一致")
        # (8) minute_raw_match：写入后 raw OHLC 仍与 staged raw 逐 bar 一致
        #     （证明 UPDATE 只触碰 front 四列，未污染 raw）。
        #     第六轮阻断 1 修复：NULL 参与 ABS 比较时 SQL 结果为 NULL，
        #     WHERE 按"非真"过滤 → raw 被改成 NULL 会静默漏检。必须先
        #     **显式**检查 NULL/NaN/Inf/<=0（两侧都查），再比较 abs 差。
        raw_invalid = " OR ".join(
            f"t.{c} IS NULL OR NOT isfinite(t.{c}) OR t.{c} <= 0 OR "
            f"s.{c} IS NULL OR NOT isfinite(s.{c}) OR s.{c} <= 0"
            for c in RAW_COLS)
        _eps = _raw_match_eps_minute(asset_type)
        raw_pred = " OR ".join(
            f"ABS(t.{c} - s.{c}) > {_eps!r}" for c in RAW_COLS)
        n_invalid = int(conn.execute(
            f"SELECT COUNT(*) FROM {minute_table} t JOIN {sm} s "
            f"ON t.time = s.time WHERE t.code=? AND t.freq=? AND ({raw_invalid})",
            [code, freq_c]).fetchone()[0])
        n_raw = int(conn.execute(
            f"SELECT COUNT(*) FROM {minute_table} t JOIN {sm} s "
            f"ON t.time = s.time WHERE t.code=? AND t.freq=? AND "
            f"NOT ({raw_invalid}) AND ({raw_pred})",
            [code, freq_c]).fetchone()[0])
        d_raw_match[freq_c] = {"raw_invalid": n_invalid, "raw_mismatch": n_raw}
        if n_invalid:
            raise PostcheckFailed(
                "minute_raw_match",
                f"freq={freq_c} 写入后 stored/staged raw OHLC 存在 "
                f"{n_invalid} 根 NULL/NaN/Inf/<=0（raw 被污染，NULL 不得"
                f"借 SQL 三值逻辑漏检）")
        if n_raw:
            raise PostcheckFailed(
                "minute_raw_match",
                f"freq={freq_c} 写入后 stored raw OHLC 与 staged raw 出现 "
                f"{n_raw} 根不一致（raw 被污染或同源前提破坏）")
        # (9) minute_coverage：staged==target==matched 且 missing 全 0
        staged_count = int(conn.execute(
            f"SELECT COUNT(*) FROM {sm}").fetchone()[0])
        target_count = int(conn.execute(
            f"SELECT COUNT(*) FROM {minute_table} WHERE code=? AND freq=?",
            [code, freq_c]).fetchone()[0])
        cov = {"staged_count": staged_count, "target_count": target_count,
               "matched_count": n_match,
               "missing_target": staged_count - n_match,
               "missing_staged": target_count - n_match}
        d_coverage[freq_c] = cov
        if not (staged_count == target_count == n_match):
            raise PostcheckFailed(
                "minute_coverage",
                f"freq={freq_c} 覆盖不完整 {cov}")
        # (10) minute_tick_error：写入后四 front 列 vs fresh 逐 bar ≤1 tick
        #      （NULL/NaN/Inf/<=0 显式计入超差——不借三值逻辑漏检；
        #      tick 按资产路由，见 resolve_tick_size）
        if include_tick_error:
            tick_pred = " OR ".join(
                f"t.{c} IS NULL OR NOT isfinite(t.{c}) OR t.{c} <= 0 OR "
                f"ABS(t.{c} - s.{c}) > {tick!r}" for c in FRONT_COLS)
            trow = conn.execute(
                f"SELECT COUNT(*), MIN(t.time) FROM {minute_table} t JOIN {sm} s "
                f"ON t.time = s.time WHERE t.code=? AND t.freq=? AND ({tick_pred})",
                [code, freq_c]).fetchone()
            max_err = conn.execute(
                f"SELECT MAX(GREATEST("
                + ", ".join(f"ABS(t.{c} - s.{c})" for c in FRONT_COLS)
                + f")) FROM {minute_table} t JOIN {sm} s ON t.time = s.time "
                f"WHERE t.code=? AND t.freq=?", [code, freq_c]).fetchone()[0]
            bars_over = int(trow[0])
            d_tick[freq_c] = {"bars_over_1_tick": bars_over,
                              "max_abs_err": float(max_err or 0.0),
                              "tick_size": tick}
            if bars_over:
                raise PostcheckFailed(
                    "minute_tick_error",
                    f"freq={freq_c} 写入后 {bars_over} 根 bar front vs fresh 超 "
                    f"1 tick（首例 time={int(trow[1])}，max_abs_err="
                    f"{float(max_err or 0.0):.4f}，tick={tick}）")
    return d_staged_match, d_raw_match, d_coverage, d_tick


def collect_row_counts(conn, asset_type: str, code: str,
                       freqs: Sequence[str]) -> Dict:
    """采集行数快照：目标证券 daily / 各 freq 分钟行数 + 两表全表行数（守恒证据）。"""
    daily_table, minute_table = _tables_of(_normalize_asset_type(asset_type))
    out = {
        "daily_code": int(conn.execute(
            f"SELECT COUNT(*) FROM {daily_table} WHERE code=?", [code]).fetchone()[0]),
        "daily_table_total": int(conn.execute(
            f"SELECT COUNT(*) FROM {daily_table}").fetchone()[0]),
        "minute_table_total": int(conn.execute(
            f"SELECT COUNT(*) FROM {minute_table}").fetchone()[0]),
    }
    for freq in freqs:
        freq_c = _canon_minute_freq(freq)
        out[f"minute_code@{freq_c}"] = int(conn.execute(
            f"SELECT COUNT(*) FROM {minute_table} WHERE code=? AND freq=?",
            [code, freq_c]).fetchone()[0])
    return out


# ---------------------------------------------------------------------------
# 5. 事件 / anchor
# ---------------------------------------------------------------------------

def _insert_event_on_conn(conn, *, event_id: str, event_type: str, asset_type: str,
                          code: str, price_source: str, status: str,
                          minute_ratio_plan: Optional[str] = None,
                          golden_check: Optional[str] = None,
                          postcheck_summary: Optional[str] = None,
                          rows_detail: Optional[str] = None,
                          block_reason: Optional[str] = None,
                          error: Optional[str] = None,
                          ratio_dispersion: Optional[float] = None,
                          ratio_cluster_count: Optional[int] = None,
                          trigger_surface: str = "batch2",
                          started_at: Optional[datetime] = None,
                          finished_at: Optional[datetime] = None) -> None:
    now = datetime.now()
    conn.execute(
        "INSERT INTO qfq_reanchor_event (event_id, event_type, asset_type, code, "
        "price_source, daily_method, minute_ratio_plan, ratio_dispersion, "
        "ratio_cluster_count, golden_check, status, block_reason, error, "
        "postcheck_summary, rows_detail, trigger_surface, started_at, finished_at, "
        "created_at, first_seen_at, last_seen_at, occurrence_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        [event_id, event_type, asset_type, code, price_source,
         "staged_fresh_update", minute_ratio_plan, ratio_dispersion,
         ratio_cluster_count, golden_check, status, block_reason, error,
         postcheck_summary, rows_detail, trigger_surface,
         started_at or now, finished_at or now, now, now, now])


def _advance_anchor_on_conn(conn, *, asset_type: str, code: str, price_source: str,
                            event_id: str,
                            last_ex_date: Optional[int] = None) -> int:
    """事务内推进 anchor：anchor_version = 现值+1（无记录从 1 起），status='ok'。"""
    row = conn.execute(
        "SELECT anchor_version FROM qfq_anchor_state "
        "WHERE asset_type=? AND code=? AND price_source=?",
        [asset_type, code, price_source]).fetchone()
    new_version = int(row[0] or 0) + 1 if row else 1
    now = datetime.now()
    conn.execute(
        "INSERT INTO qfq_anchor_state (asset_type, code, price_source, anchor_version, "
        "status, last_event_id, last_ex_date, updated_at) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT (asset_type, code, price_source) DO UPDATE SET "
        "anchor_version=EXCLUDED.anchor_version, status=EXCLUDED.status, "
        "last_event_id=EXCLUDED.last_event_id, last_ex_date=EXCLUDED.last_ex_date, "
        "updated_at=EXCLUDED.updated_at",
        [asset_type, code, price_source, new_version, "ok", event_id,
         last_ex_date, now])
    return new_version


def _anchor_version_of(conn, *, asset_type: str, code: str,
                       price_source: str = "xtquant") -> int:
    """读当前 anchor_version（无记录返回 0）。"""
    row = conn.execute(
        "SELECT anchor_version FROM qfq_anchor_state "
        "WHERE asset_type=? AND code=? AND price_source=?",
        [asset_type, code, price_source]).fetchone()
    return int(row[0] or 0) if row else 0


def _fresh_content_hashes(fresh_daily: pd.DataFrame,
                          fresh_minutes: pd.DataFrame):
    """计算 fresh 内容 hash（与 FreshCapture.capture 同口径），供 capture 不可变契约比对。

    daily：time/open/high/low/close + 四 front 列；minute：time/open/high/low/close/
    freq + 四 front 列。与 qfq_fresh_capture.FreshCapture.capture 保持完全一致，确保
    崩溃恢复幂等时「同份数据」比对一致。
    """
    daily_cols = ["time", "open", "high", "low", "close"] + list(FRONT_COLS)
    minute_cols = ["time", "open", "high", "low", "close", "freq"] + list(FRONT_COLS)
    daily_csv = fresh_daily[daily_cols].to_csv(index=False)
    minute_csv = fresh_minutes[minute_cols].to_csv(index=False)
    return (hashlib.sha256(daily_csv.encode("utf-8")).hexdigest(),
            hashlib.sha256(minute_csv.encode("utf-8")).hexdigest())


def _resolve_capture_contract(conn, *, asset_type: str, code: str, source: str,
                              capture_id: str, metadata_sha256: str,
                              fresh_daily: pd.DataFrame, fresh_minutes: pd.DataFrame,
                              freqs: Sequence[str] = ()):
    """§3.5 capture 不可变契约 + 崩溃恢复幂等（R2）。

    计算 fresh 内容 hash/区间（与 FreshCapture.capture 同口径），与已登记 capture 比对；
    按 resolve_fresh_capture 四动作返回 ``(record_or_None, action)``：

    - NEW → 写 capture（plain INSERT，不可变），返回 (rec, NEW)；
    - ALREADY_COMMITTED / RECOLLECT_OK / RECOVER_APPLIED_NO_EVENT → 返回 (None, action)
      （由调用方据 action 决定：幂等返回 / 继续 apply / 异常恢复）。
    """
    # 单 freq 且 fresh_minutes 缺 freq 列时，补一列 canonical freq，使内容 hash 与
    # 带 freq 列的采集批次一致（多 freq 缺列由 apply 提前 ReanchorBlocked）。
    minute_df = fresh_minutes
    if "freq" not in minute_df.columns and len(freqs) == 1:
        minute_df = minute_df.copy()
        minute_df["freq"] = _canon_minute_freq(list(freqs)[0])
    d_start = int(fresh_daily["time"].min()) if len(fresh_daily) else None
    d_end = int(fresh_daily["time"].max()) if len(fresh_daily) else None
    m_start = int(minute_df["time"].min()) if len(minute_df) else None
    m_end = int(minute_df["time"].max()) if len(minute_df) else None
    daily_sha, minute_sha = _fresh_content_hashes(fresh_daily, minute_df)
    action = resolve_fresh_capture(
        conn, capture_id=capture_id, asset_type=asset_type, code=code, source=source,
        daily_range_start=d_start, daily_range_end=d_end,
        minute_range_start=m_start, minute_range_end=m_end,
        daily_sha256=daily_sha, minute_sha256=minute_sha,
        metadata_sha256=metadata_sha256)
    rec = None
    if action == CAPTURE_ACTION_NEW:
        now = datetime.now()
        rec = FreshCaptureRecord(
            capture_id=capture_id, asset_type=asset_type, code=code, source=source,
            daily_range_start=d_start, daily_range_end=d_end,
            minute_range_start=m_start, minute_range_end=m_end,
            daily_sha256=daily_sha, minute_sha256=minute_sha,
            metadata_sha256=metadata_sha256,
            daily_row_count=len(fresh_daily), minute_row_count=len(fresh_minutes),
            status="captured", created_at=now, updated_at=now)
        write_fresh_capture(conn, rec)
    return rec, action


def _record_failure_event(conn, *, event_id: str, asset_type: str, code: str,
                          price_source: str, status: str,
                          block_reason: Optional[str], error: Optional[str],
                          started_at: datetime,
                          minute_ratio_plan: Optional[str] = None,
                          postcheck_summary: Optional[str] = None,
                          rows_detail: Optional[str] = None) -> None:
    """ROLLBACK 后用**独立短事务**记录 failed / rolled_back / blocked。绝不触碰 anchor。

    第六轮阻断 3：失败事件同样必须携带审计上下文（model / model_reason /
    fresh_source / fresh_capture_id / metadata_sha256 / freqs / coverage
    摘要，经 ``minute_ratio_plan`` JSON 传入），否则 BLOCK 后无法审计
    "是哪个模型、哪份 fresh 数据、覆盖到哪一步失败的"。
    """
    try:
        conn.execute("BEGIN")
        _insert_event_on_conn(
            conn, event_id=event_id, event_type="reanchor_apply",
            asset_type=asset_type, code=code, price_source=price_source,
            status=status, block_reason=block_reason, error=error,
            minute_ratio_plan=minute_ratio_plan,
            postcheck_summary=postcheck_summary, rows_detail=rows_detail,
            started_at=started_at, finished_at=datetime.now())
        conn.execute("COMMIT")
    except Exception:  # pragma: no cover - 记录失败不掩盖原始异常
        logger.exception("[qfq_engine] 失败事件记录异常（不掩盖原始错误）")
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 6. 单证券编排入口
# ---------------------------------------------------------------------------

def apply_reanchor_for_security(conn, *, asset_type: str, code: str,
                                fresh_daily: pd.DataFrame,
                                calendar: CalendarService,
                                freqs: Sequence[str] = ("1min",),
                                golden_minutes: Optional[pd.DataFrame] = None,
                                ex_dates_ms: Sequence[int] = (),
                                allow_multi_segment: bool = False,
                                tol: Optional[ReanchorTolerances] = None,
                                price_source: str = "xtquant",
                                trigger_surface: str = "batch2",
                                event_id: Optional[str] = None,
                                list_date_ms: Optional[int] = None,
                                model: str = "ratio",
                                model_reason: Optional[str] = None,
                                fresh_minutes: Optional[pd.DataFrame] = None,
                                fresh_source: Optional[str] = None,
                                fresh_capture_id: Optional[str] = None,
                                fresh_metadata_sha256: Optional[str] = None,
                                ) -> ReanchorResult:
    """单证券 QFQ 重锚修正（第二批编排入口，仅接受调用方显式连接）。

    分钟修正模型（``model``，B-1 批准边界 2026-07-27）：
    - "ratio"（默认）：方法 B + 方法 A 黄金抽验（原行为逐位不变）。此模式
      **禁止**传 fresh_minutes（防呆：杜绝任何"ratio BLOCK 后换数据重试"的
      模糊语义——切换模型必须由调用方显式改 model 并给出书面原因）。
    - "fresh_staged"：fresh xtquant 分钟前复权逐值写入。必须提供
      ``fresh_minutes``（列 = code/time/freq? + OHLC raw + 四 *_front；多 freq
      时必须含 freq 列）与非空 ``model_reason``（写入事件审计）。
      引擎内**不存在** ratio BLOCK → fresh_staged 的静默回退路径。

    成功：status="committed"，committed event + anchor 推进与价格修正同一事务。
    方法 B/A 或数据契约失败：status="blocked"（回滚 + 独立短事务 blocked 事件）。
    postcheck 失败：status="rolled_back"（回滚 + 独立短事务 rolled_back 事件）。
    其它异常：记录 failed 事件后**重新抛出**。三种失败路径都绝不推进 anchor。
    """
    tol = tol or ReanchorTolerances()
    if model not in MODELS:
        raise ValueError(f"未知模型 {model!r}，仅支持 {MODELS}")
    if model == "ratio" and fresh_minutes is not None:
        raise ValueError(
            "model='ratio' 不接受 fresh_minutes（切换 fresh_staged 必须显式传 "
            "model='fresh_staged' 并提供 model_reason，禁止模糊语义/静默切换）")
    if model in ("fresh_staged", "fresh_authoritative_rebase"):
        if not (model_reason and model_reason.strip()):
            raise ValueError(
                f"model={model!r} 必须提供非空 model_reason（模型选择原因，"
                "写入事件审计）")
        if fresh_minutes is None or len(fresh_minutes) == 0:
            raise ValueError(f"model={model!r} 必须提供非空 fresh_minutes")
        # 第七轮审计阻断 1：来源字段强制（事务外 ValueError，不写价格/事件/anchor）。
        # 此段位于 try/BEGIN 之前，缺任一项直接抛错，绝不进入事务、不落 event/anchor。
        if not (fresh_source and str(fresh_source).strip()):
            raise ValueError(
                f"model={model!r} 必须提供非空 fresh_source（fresh 数据来源，"
                "写入事件审计）")
        if not (fresh_capture_id and str(fresh_capture_id).strip()):
            raise ValueError(
                f"model={model!r} 必须提供非空 fresh_capture_id（采集批次 id，"
                "写入事件审计）")
        if not _is_valid_sha256(fresh_metadata_sha256):
            raise ValueError(
                f"model={model!r} 必须提供合法 64 位 hex 的 fresh_metadata_sha256"
                f"（收到 {fresh_metadata_sha256!r}）")
        if not freqs:
            raise ValueError(f"model={model!r} 必须提供非空 freqs")
        # freqs 必须 canonical（拒绝非分钟/别名混淆）；事件 freqs 使用 canonical 去重列表
        for _f in freqs:
            _canon_minute_freq(_f)   # 非分钟 freq → ValueError（事务外）
    asset_type = _normalize_asset_type(asset_type)
    code = _normalize_code(code)
    # —— 崩溃恢复幂等 + capture 不可变契约（R2 §3）——
    # 仅 fresh_staged / fresh_authoritative_rebase 启用 capture 契约。在 BEGIN 之前完成：
    # NEW 写 capture（plain INSERT，不可变）后继续；ALREADY_COMMITTED 幂等返回（不重复写价、
    # 不推进 anchor）；RECOVER_APPLIED_NO_EVENT 进入异常恢复（不静默跳过）；
    # RECOLLECT_OK 继续 apply。
    _capture_action = None
    if model in ("fresh_staged", "fresh_authoritative_rebase") and fresh_capture_id:
        _cap_rec, _capture_action = _resolve_capture_contract(
            conn, asset_type=asset_type, code=code, source=fresh_source,
            capture_id=fresh_capture_id, metadata_sha256=fresh_metadata_sha256,
            fresh_daily=fresh_daily, fresh_minutes=fresh_minutes, freqs=freqs)
        if _capture_action == CAPTURE_ACTION_ALREADY_COMMITTED:
            # 幂等返回：不重复写价、不推进 anchor，直接给出已 committed 结果
            _idem = ReanchorResult(
                status="committed", event_id="<idempotent>",
                asset_type=asset_type, code=code, model=model,
                block_reason="already_committed",
                postchecks={"status": "already_committed",
                            "capture_id": fresh_capture_id})
            _idem.anchor_version = _anchor_version_of(
                conn, asset_type=asset_type, code=code)
            return _idem
        if _capture_action == CAPTURE_ACTION_RECOVER_APPLIED_NO_EVENT:
            # 异常恢复路径：不静默跳过，标记需人工/下轮处置（整券 BLOCK）
            raise ReanchorBlocked(
                "capture_recover_applied_no_event",
                f"capture_id={fresh_capture_id} 已 applied 但无 committed event "
                f"（崩溃窗口：价格已写但事件丢失）→ 进入异常恢复，禁止静默重复写价")
    ev_id = event_id or uuid.uuid4().hex
    started_at = datetime.now()
    result = ReanchorResult(status="pending", event_id=ev_id,
                            asset_type=asset_type, code=code, model=model)
    staged = None
    staged_min_tables: Dict[str, str] = {}
    # —— 审计上下文（第六轮阻断 3）：committed/blocked/rolled_back/failed 四类
    #    事件一律携带；失败路径经 _audit_json() 序列化进 minute_ratio_plan。——
    audit_ctx: Dict = {
        "fresh_source": fresh_source, "fresh_capture_id": fresh_capture_id,
        "metadata_sha256": fresh_metadata_sha256,
        "tick_size": resolve_tick_size(asset_type, tol),
        "freqs": [str(f) for f in freqs],
    }

    def _audit_json(phase: Optional[str] = None) -> str:
        payload = {"model": model, "model_reason": model_reason,
                   "model_audit": dict(audit_ctx)}
        if result.minute_coverage:      # precheck/coverage 走到哪一步记到哪一步
            payload["minute_coverage"] = result.minute_coverage
        if phase:
            payload["precheck_phase"] = phase
        return json.dumps(payload, ensure_ascii=False, default=float)

    try:
        # —— canonical freq：事务前统一 canonicalize + 去重（"1m"/"1min" 等别名重复
        #    会导致第二轮重算为 noop 并覆盖真实 ratio plan，审计失真）。counts /
        #    方法 B/A / UPDATE / postcheck / event 全部共用这份 canonical 列表。——
        canon_freqs: List[str] = []
        for f in freqs:
            fc = _canon_minute_freq(f)   # 非分钟 freq 抛 ValueError（进 failed 路径）
            if fc not in canon_freqs:
                canon_freqs.append(fc)
        audit_ctx["freqs"] = canon_freqs
        conn.execute("BEGIN")
        if model == "fresh_authoritative_rebase":
            # rebase：日线严格覆盖 + raw 逐 bar 对齐（无乘法/加法比例校验）
            staged = stage_fresh_authoritative(
                conn, asset_type, code, fresh_daily, fresh_minutes, canon_freqs, tol, calendar)
        else:
            staged = stage_fresh_daily(conn, asset_type, code, fresh_daily, tol,
                                       model=model)
        pre_counts = collect_row_counts(conn, asset_type, code, canon_freqs)

        all_dispersion = 0.0
        cluster_count = 0
        golden_all: Dict[str, Dict] = {}
        explanations_all: Dict[str, List[Dict]] = {}
        if model == "fresh_staged":
            # —— B-1：staged fresh minute → precheck（raw 逐 bar 一致 + 完整
            #    覆盖）→ 逐值 UPDATE 四 front 列（同一事务；不算 R、不抽黄金）——
            for freq_c in canon_freqs:
                fm = fresh_minutes
                if "freq" in fm.columns:
                    fm = fm[fm["freq"].astype(str).map(
                        lambda f: _canon_minute_freq(f) if f else "") == freq_c]
                elif len(canon_freqs) > 1:
                    raise ReanchorBlocked(
                        "fresh_minutes_missing_cols",
                        f"多 freq（{canon_freqs}）时 fresh_minutes 必须含 freq 列")
                sm = stage_fresh_minutes(conn, asset_type, code, freq_c, fm, tol,
                                         calendar=calendar)
                staged_min_tables[freq_c] = sm
                result.minute_coverage[freq_c] = apply_fresh_minute_staged(
                    conn, asset_type, code, freq_c, sm, tol)
        elif model == "fresh_authoritative_rebase":
            # —— rebase：复用 stage_fresh_minutes（A 基本 + 构建 STAGED_MINUTE）
            #    + apply_fresh_minute_staged（B 覆盖 + C raw 对齐已在 stage_fresh_authoritative
            #    预检；此处复核并写 front 四列。写后一致由 UPDATE 只触碰 front 四列保证）——
            for freq_c in canon_freqs:
                fm = fresh_minutes
                if "freq" in fm.columns:
                    fm = fm[fm["freq"].astype(str).map(
                        lambda f: _canon_minute_freq(f) if f else "") == freq_c]
                elif len(canon_freqs) > 1:
                    raise ReanchorBlocked(
                        "fresh_minutes_missing_cols",
                        f"多 freq（{canon_freqs}）时 fresh_minutes 必须含 freq 列")
                sm = stage_fresh_minutes(conn, asset_type, code, freq_c, fm, tol,
                                         calendar=calendar)
                staged_min_tables[freq_c] = sm
                result.minute_coverage[freq_c] = apply_fresh_minute_staged(
                    conn, asset_type, code, freq_c, sm, tol)
        else:
            for freq_c in canon_freqs:
                segments, bars_df = compute_method_b_segments(
                    conn, asset_type, code, freq_c, staged, tol,
                    allow_multi_segment=allow_multi_segment)
                result.plans[freq_c] = segments
                if segments:
                    all_dispersion = max(all_dispersion,
                                         max(s.dispersion for s in segments))
                    cluster_count += sum(1 for s in segments if s.needs_update)
                # bootstrap 多簇：逐变点解释证据（缺失/部分解释 → 整券 BLOCK）
                non_noop = [s for s in segments if s.needs_update]
                if allow_multi_segment and len(non_noop) > 1:
                    explanations_all[freq_c] = explain_changepoints(
                        segments, ex_dates_ms)
                # 方法 A 黄金抽验（仅当存在拟修正段）
                if non_noop:
                    samples = select_golden_samples(
                        bars_df, segments, calendar, ex_dates_ms, tol)
                    golden_all[freq_c] = golden_check_method_a(
                        bars_df, samples, golden_minutes, freq_c, tol)
                # 分区间 UPDATE minute *_front（左闭右开）
                apply_minute_segments(conn, asset_type, code, segments)
        result.golden_report = golden_all

        # UPDATE daily 四个 *_front（staged fresh 逐值覆盖，不乘 R）
        result.daily_rows_updated = update_daily_front_from_staged(
            conn, asset_type, code, staged)

        # COMMIT 前全部硬门禁（fresh_staged 额外四项 minute_* postcheck）
        result.postchecks = run_postchecks(
            conn, asset_type=asset_type, code=code, staged_table=staged,
            freqs=canon_freqs, calendar=calendar, pre_counts=pre_counts, tol=tol,
            list_date_ms=list_date_ms, model=model,
            staged_minutes=staged_min_tables or None)
        result.rows = result.postchecks.get("row_conservation", {}).get("post", {})

        plan_payload: Dict = {f: [s.to_dict() for s in segs]
                              for f, segs in result.plans.items()}
        if explanations_all:   # 逐变点解释写入事件审计（仅归因，不改 UPDATE 边界）
            plan_payload["changepoint_explanations"] = explanations_all
        # 模型选择及原因写入事件审计（B-1 边界 1：两种模式一律显式记录；
        # 第六轮阻断 3：fresh_source/capture_id/metadata_sha256/tick/freqs
        # 统一嵌在 model_audit 键下——不污染 plan 顶层 freq 键空间）
        plan_payload["model"] = model
        plan_payload["model_reason"] = model_reason
        plan_payload["model_audit"] = dict(audit_ctx)
        if model in ("fresh_staged", "fresh_authoritative_rebase"):
            plan_payload["minute_coverage"] = result.minute_coverage
        plan_json = json.dumps(plan_payload, ensure_ascii=False)
        _insert_event_on_conn(
            conn, event_id=ev_id, event_type="reanchor_apply",
            asset_type=asset_type, code=code, price_source=price_source,
            status="committed", minute_ratio_plan=plan_json,
            golden_check=json.dumps(golden_all, ensure_ascii=False, default=float),
            postcheck_summary=json.dumps(result.postchecks, ensure_ascii=False,
                                         default=float),
            rows_detail=json.dumps(result.rows, ensure_ascii=False),
            ratio_dispersion=all_dispersion, ratio_cluster_count=cluster_count,
            trigger_surface=trigger_surface, started_at=started_at,
            finished_at=datetime.now())
        _advance_anchor_on_conn(
            conn, asset_type=asset_type, code=code, price_source=price_source,
            event_id=ev_id,
            last_ex_date=max((int(x) for x in ex_dates_ms), default=None))
        conn.execute("COMMIT")
        # —— capture 标记 applied（R2 §3：与价格修正同一成功事实）——
        if (model in ("fresh_staged", "fresh_authoritative_rebase")
                and fresh_capture_id):
            FreshCapture(cfg=None).mark_applied(conn, fresh_capture_id)
        result.status = "committed"
        return result
    except ReanchorBlocked as e:
        conn.execute("ROLLBACK")
        # 第七轮审计阻断 2：precheck 已计算的覆盖统计回填 result，使 blocked 事件
        # 记录"执行到哪一步、各统计值是多少"（staged_count/target_count/...）。
        if e.coverage:
            fkey = e.freq or (e.coverage.get("freq")
                              if isinstance(e.coverage, dict) else None)
            if fkey:
                result.minute_coverage[fkey] = e.coverage
        _record_failure_event(conn, event_id=ev_id, asset_type=asset_type,
                              code=code, price_source=price_source,
                              status="blocked", block_reason=e.reason,
                              error=e.detail or None, started_at=started_at,
                              minute_ratio_plan=_audit_json(phase=e.phase or e.reason))
        result.status = "blocked"
        result.block_reason = e.reason
        result.error = e.detail or None
        return result
    except PostcheckFailed as e:
        conn.execute("ROLLBACK")
        _record_failure_event(conn, event_id=ev_id, asset_type=asset_type,
                              code=code, price_source=price_source,
                              status="rolled_back", block_reason=e.check,
                              error=e.detail or None, started_at=started_at,
                              minute_ratio_plan=_audit_json(phase="postcheck"))
        result.status = "rolled_back"
        result.block_reason = e.check
        result.error = e.detail or None
        return result
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        _record_failure_event(conn, event_id=ev_id, asset_type=asset_type,
                              code=code, price_source=price_source,
                              status="failed", block_reason=None,
                              error=repr(e), started_at=started_at,
                              minute_ratio_plan=_audit_json())
        raise
    finally:
        if staged is not None:
            try:
                conn.execute(f"DROP TABLE IF EXISTS {staged}")
            except Exception:  # pragma: no cover
                pass
        for sm in staged_min_tables.values():
            try:
                conn.execute(f"DROP TABLE IF EXISTS {sm}")
            except Exception:  # pragma: no cover
                pass
