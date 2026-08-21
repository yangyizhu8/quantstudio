"""
vol_regime_mom_rev.py - agent-authored QuantStudio-only strategy.

波动率区制动量反转（全A·5只等权·长多1x·月度）：
  Step1 σ_m(T) = 沪深300(000300.SS, 引擎 INDEX_ETF_MAP→510300 前复权) 当月日收益样本标准差 × √252
  Step2 Q_t = σ_m(T) 在滚动60个月(含当月)月度 σ 中的分位（升序 ≤ 计数占比）
  Step3 Q_t > 75% → 反转月（持最近1月跌幅最大5只）；否则 动量月（持跳过1月的11个月涨幅最大5只）
  全A剔除 ST/退市/停牌/次新(<12月)/科创板688(.SS)/北交所(.BJ) 后横截面极值排序。
  每月首个交易日 15:00 close 撮合，等权目标 5×20%，恒定1x不融资，基准沪深300。

Implementation notes (verified engine semantics):
- 持仓自维护 g.current_holdings（list，确定性容器，不用哈希容器）。
- 卖出=对非目标持仓 order_target_value(0)；买入仅对新目标，按卖出后可用现金 / 新目标数
  per_new 下单（现金可负担 ⇒ 零 insufficient_cash 拒单）+ 整手预筛（1手>per_new 则跳过）。
- 新目标与已持仓重叠时保留（不重复买入），目标权重≈20%。
- 空目标/regime 不可用时当月不锁定 g.last_month ⇒ 下一交易日重试（规避首日数据预热）。

This file is intentionally a lifecycle/API scaffold, not a strategy template.
QuantStudio APIs, registered local extensions, numpy/pandas, g and log are injected locally.
The validated file is published only to the QuantStudio PyQt strategy directory.
"""
import numpy as np

STRATEGY_ID = 'vol_regime_mom_rev'
DESIGN_VERSION = '2.2'

# ---------------- 策略常量（语义契约，勿改语义） ----------------
# 市场波动率基准标的选择（R5 数据驱动修订，2026-08-18，已披露）：
# 原设计用 get_history('000300.SS')，但 index_daily(000300) 仅覆盖 2025-04 起，
# 2025-05 后引擎 count 路径返回浅层 index_daily 而非 510300 fallback → σ 60月窗口被截断、
# regime 失效、月度调仓停摆。改为直接用 510300.SS（CSI300 跟踪 ETF，etf_daily 2018-01 起全量，
# 前复权）= 引擎对 000300 的 INDEX_ETF_MAP fallback 同款标的，日收益波动率≈沪深300。
_MARKET_CODE = '510300.SS'          # 仅用于 σ_m 计算的行情标的（CSI300 ETF，日收益波动率≈沪深300）
_BENCHMARK_CODE = '000300.SS'       # 基准对标 = 沪深300 指数（独立于 σ 计算标的；set_benchmark 用此）
_VOL_WINDOW_MONTHS = 60             # 滚动60个月月度波动率（含当月）
_REVERSAL_QUANTILE = 0.75           # Q_t > 0.75 → 反转月（50-80 稳健）
_SKIP_MONTHS = 1                    # 动量跳过最近1个月
_MOMENTUM_MONTHS = 11               # 动量窗口 11 个月（跳1月）
_TOP_N = 5                          # 极值前/后 5 只
_MAX_NEW_LISTING_MONTHS = 12        # 次新：上市不足 12 个月剔除
_MIN_VALID_Q_MONTHS = 50            # σ 有效月数下限（不足则本季跳过调仓）
_HIST_COUNT = 300                   # ~14个月日线（个股动量/反转锚点窗口）
_MARKET_HIST_COUNT = 1300           # ~62个月日线（σ 60月窗口）
_SQRT252 = float(np.sqrt(252.0))
_LOT = 100                          # A股整手 = 100 股
_PX_BUFFER = 0.95                   # 整手可负担预筛缓冲（执行价 ≤5% 上浮仍可建仓）


def _ensure_runtime_state():
    """Idempotently create every g field used by any callback.

    Real PTrade may continue later lifecycle calls after initialize raises, so
    state construction must never reset existing fields.
    """
    if not hasattr(g, 'universe'):
        g.universe = []
    if not hasattr(g, 'last_month'):
        g.last_month = 0
    if not hasattr(g, 'last_rebalance_ymd'):
        g.last_rebalance_ymd = 0
    if not hasattr(g, 'last_q'):
        g.last_q = 0.0
    if not hasattr(g, 'regime'):
        g.regime = 'momentum'
    if not hasattr(g, 'targets'):
        g.targets = []
    if not hasattr(g, 'current_holdings'):
        g.current_holdings = []
    if not hasattr(g, 'listing_date'):
        g.listing_date = {}
    if not hasattr(g, 'rebalance_seq'):
        g.rebalance_seq = 0
    if not hasattr(g, 'last_close'):
        g.last_close = {}


def _ymd(value):
    """Normalize a date-like value to int YYYYMMDD."""
    if value is None:
        return 0
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        return int(digits) if digits else 0
    if hasattr(value, 'year'):
        return value.year * 10000 + value.month * 100 + value.day
    text = str(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else 0


def _ym(value):
    """Normalize a date-like value to int YYYYMM."""
    v = _ymd(value)
    return v // 100 if v >= 1000000 else 0


def _month_key(day_str):
    """'YYYY-MM-DD' -> 'YYYY-MM'"""
    if not day_str or len(day_str) < 7:
        return str(day_str)
    return str(day_str)[:7]


def _month_offset(ym_str, offset):
    """'YYYY-MM' + <int months> -> 'YYYY-MM'"""
    if not ym_str or len(ym_str) < 7:
        return ym_str
    y = int(ym_str[:4])
    m = int(ym_str[5:7])
    total = y * 12 + (m - 1) + offset
    return '%04d-%02d' % (total // 12, total % 12 + 1)


def _normalize_date_str(value):
    """把 PTrade/QuantStudio 各种日期返回（str、date、datetime、YYYYMMDD）统一成 'YYYY-MM-DD'。"""
    if value is None:
        return ""
    # date/datetime 对象
    if hasattr(value, 'year') and hasattr(value, 'month') and hasattr(value, 'day'):
        return "%04d-%02d-%02d" % (value.year, value.month, value.day)
    text = str(value)
    # 已是 YYYY-MM-DD
    if len(text) == 10 and text[4] == '-' and text[7] == '-':
        return text
    # YYYYMMDD 数字或字符串
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 8:
        return "%s-%s-%s" % (digits[:4], digits[4:6], digits[6:8])
    # 兜底：尝试 pandas 解析
    try:
        return str(pd.to_datetime(text).date())
    except Exception:
        return text


def _extract_history_field(item, field, dtype=float):
    """Rule 17: normalize an extracted get_history item field via np.asarray.

    item is a DataFrame / structured array / recarray per security; guard with
    a hasattr('values') check before numeric use. Fail-soft: None / missing
    field / unusable item returns an empty ndarray (no exception).
    """
    if item is None:
        return np.asarray([], dtype=dtype)
    try:
        values = item[field]
    except (KeyError, TypeError, IndexError):
        return np.asarray([], dtype=dtype)
    if hasattr(values, 'values'):
        try:
            values = values.values
        except Exception:
            return np.asarray([], dtype=dtype)
    try:
        return np.asarray(values, dtype=dtype)
    except Exception:
        return np.asarray([], dtype=dtype)


def _month_std(daily_rets):
    """样本标准差 × √252（当月日收益标准差）。"""
    arr = np.asarray(daily_rets, dtype=float)
    if arr.size < 2:
        return None
    return float(np.std(arr, ddof=1)) * _SQRT252


def _portfolio_total_value(context):
    v = getattr(context.portfolio, 'total_value', None)
    if v is None:
        v = getattr(context.portfolio, 'portfolio_value', None)
    if v is None:
        v = getattr(context.portfolio, 'cash', 0.0)
    try:
        return float(v)
    except Exception:
        return 0.0


def _position_volume(pos):
    """ptrade 持仓对象数量字段：.amount 为正式，.volume 兜底。"""
    try:
        v = getattr(pos, 'amount', None)
        if v is None:
            v = getattr(pos, 'volume', None)
        return float(v or 0.0)
    except Exception:
        return 0.0


def _current_positions(context):
    """当前持仓 {code(带后缀): volume>0}，来自 context.portfolio.positions（与参考策略同源）。"""
    positions = {}
    try:
        src = getattr(context.portfolio, 'positions', None) or {}
    except Exception as exc:
        log.warning("portfolio.positions failed: %s" % (exc,))
        return positions
    for code, pos in src.items():
        try:
            if _position_volume(pos) > 0:
                positions[str(code)] = pos
        except Exception:
            continue
    return positions


def initialize(context):
    """Configure benchmark, schedule and strategy constants."""
    _ensure_runtime_state()
    set_benchmark(_BENCHMARK_CODE)
    # 无显式 set_commission/set_slippage → 引擎默认成本（R5 从 config.csv 取证）
    run_daily(context, monthly_rebalance, time='15:00')


def before_trading_start(context, data):
    """Build the PIT base universe without same-day future data.

    filter_stock_by_status 仅在 before_trading_start 调用；
    run_daily 回调内用 get_stock_status 复核。次新/历史门槛在决策日评估。
    """
    _ensure_runtime_state()
    universe = []
    try:
        all_codes = get_Ashares()
    except Exception as exc:
        log.warning("get_Ashares failed: %s" % (exc,))
        all_codes = []
    for code in all_codes or []:
        if code.startswith("688"):
            continue
        if code.endswith(".BJ") or code.endswith(".XBJ"):
            continue
        universe.append(code)
    try:
        clean = filter_stock_by_status(
            stocks=universe, filter_type=["ST", "HALT", "DELISTING"])
    except Exception as exc:
        log.warning("filter_stock_by_status failed: %s" % (exc,))
        clean = universe
    g.universe = list(clean) if clean else list(universe)


def handle_data(context, data):
    """Bar-level hook（daily-bar-v1 close 模式；决策在 run_daily 15:00）。"""
    _ensure_runtime_state()
    log.debug("handle_data %s" % (context.current_dt.date(),))


def after_trading_end(context, data):
    """Reconcile diagnostics."""
    _ensure_runtime_state()
    log.info("after_trading_end %s regime=%s targets=%d last_q=%.4f"
             % (context.current_dt.date(), g.regime, len(g.targets), g.last_q))


# ---------------- 波动率区制 ----------------

def _compute_regime(context):
    """计算当月 σ_m(T) 与 Q_t，返回 (regime, q, sigma_cur)。数据截至上一交易日（=上月末）。"""
    try:
        hist = get_history(
            count=_MARKET_HIST_COUNT, frequency="1d", field=["close", "trade_date"],
            security_list=[_MARKET_CODE], fq="pre", include=False)
    except Exception as exc:
        log.warning("get_history(market) failed: %s" % (exc,))
        return None, None, None
    if hist is None or len(hist) < 300:
        log.warning("regime skip: market history too short")
        return None, None, None
    closes = _extract_history_field(hist, "close")
    dates = _extract_history_field(hist, "trade_date", dtype=str)
    month_rets = {}
    prev_close = None
    cur_mon = None
    rets = []
    for i in range(len(closes)):
        dstr = str(dates[i])
        m = _month_key(dstr)
        try:
            c = float(closes[i])
        except Exception:
            c = 0.0
        if cur_mon is None or m != cur_mon:
            if cur_mon is not None and len(rets) >= 2:
                s = _month_std(rets)
                if s is not None:
                    month_rets[cur_mon] = s
            cur_mon = m
            rets = []
        if prev_close is not None and prev_close > 0 and c > 0:
            rets.append(c / prev_close - 1.0)
        prev_close = c
    if cur_mon is not None and len(rets) >= 2:
        s = _month_std(rets)
        if s is not None:
            month_rets[cur_mon] = s
    if not month_rets:
        log.warning("regime skip: no valid monthly sigma")
        return None, None, None
    ordered = sorted(month_rets.items())
    sigma_cur = ordered[-1][1]
    window = ordered[-_VOL_WINDOW_MONTHS:]
    sigma_vals = np.array([v for _, v in window], dtype=float)
    if sigma_vals.size < _MIN_VALID_Q_MONTHS:
        log.warning("regime skip: need >=%d valid monthly sigma, got %d"
                    % (_MIN_VALID_Q_MONTHS, sigma_vals.size))
        return None, None, None
    q = float(np.count_nonzero(sigma_vals <= sigma_cur) / float(sigma_vals.size))
    regime = "reversal" if q > _REVERSAL_QUANTILE else "momentum"
    return regime, q, sigma_cur


# ---------------- 候选与选股 ----------------

def _ensure_listing_dates(codes):
    """一次性缓存 listed_date（上市日），后续按月比对次新门槛。"""
    if g.listing_date:
        return
    try:
        infos = get_stock_info(codes, field=["listed_date"]) or {}
    except Exception as exc:
        log.warning("get_stock_info failed: %s" % (exc,))
        infos = {}
    for code in codes:
        rec = infos.get(code) or {}
        g.listing_date[code] = _normalize_date_str(rec.get("listed_date"))


def _selected_targets(context, regime):
    """返回 (target_codes, anchor_info)。target_codes 为已排序极值 top5/bottom5。"""
    today = context.current_dt
    base = g.universe
    if not base:
        log.warning("selection skip: empty universe")
        return [], None

    _ensure_listing_dates(base)
    cutoff_ym = _month_offset("%04d-%02d" % (today.year, today.month), -_MAX_NEW_LISTING_MONTHS)
    cutoff_date = cutoff_ym + "-01"

    eligible = []
    for code in base:
        listed = g.listing_date.get(code)
        if listed is None:
            eligible.append(code)
        elif listed >= cutoff_date:
            continue
        else:
            eligible.append(code)
    if not eligible:
        log.warning("selection skip: all candidates are 次新/无日期")
        return [], None

    trade_days = None
    today_str = "%04d-%02d-%02d" % (today.year, today.month, today.day)
    try:
        # 先尝试带 end_date 的调用（部分 PTrade 支持）；失败则退化为无参调用。
        try:
            raw = get_trade_days(end_date=today.strftime("%Y%m%d"))
        except Exception:
            raw = get_trade_days()
        trade_days = [_normalize_date_str(x) for x in raw] if raw is not None else []
        # 关键防御：无论平台是否支持 end_date，都在策略内过滤掉晚于当前回测日的日期。
        # 真实 PTrade 无 end_date 时返回全量交易日历（含未来），会导致月份锚点指向
        # 未来日期、所有候选算不出收益；本地引擎语义本就是「截至 current_date」，
        # 过滤对本地是空操作（行为不变）。
        trade_days = [d for d in trade_days if d and d <= today_str]
    except Exception as exc:
        log.warning("get_trade_days failed: %s" % (exc,))
    if not trade_days:
        log.warning("selection skip: empty trade calendar")
        return [], None
    anchors = {}
    for dd in trade_days:
        anchors[_month_key(dd)] = dd

    prev_day = trade_days[-2] if len(trade_days) >= 2 else trade_days[-1]
    cur_ym = _month_key(prev_day)
    end_T = anchors.get(cur_ym)
    end_Tm1 = anchors.get(_month_offset(cur_ym, -(_SKIP_MONTHS + 0)))
    end_Tm12 = anchors.get(_month_offset(cur_ym, -(_SKIP_MONTHS + _MOMENTUM_MONTHS)))
    if not end_T or not end_Tm1 or not end_Tm12:
        log.warning("selection skip: missing month anchors (T=%s T-1=%s T-12=%s)"
                    % (end_T, end_Tm1, end_Tm12))
        return [], (cur_ym, end_T, end_Tm1, end_Tm12)

    codes = list(eligible)
    # 分批取数（历史复现性修复，2026-08-19）：单次对 ~4000 只全历史缓存装载的瞬态内存过高，
    # 在首个决策日存在内存/时序竞态（某次运行会整月 get_history 返回空 → 空转数周）。
    # 拆批后单批瞬态 ≤ 500 只，单批失败仅影响该批（该月仍可从其余批次建仓）；
    # 并集与一次大取数逐码一致（本地语义零变化）。批次大小保持为 .SS/.SZ 有序分块，确定性稳定。
    _HIST_CHUNK = 500
    hist = {}
    for _i in range(0, len(codes), _HIST_CHUNK):
        _grp = codes[_i:_i + _HIST_CHUNK]
        try:
            _h = get_history(
                count=_HIST_COUNT, frequency="1d", field=["close", "trade_date"],
                security_list=_grp, fq="pre", include=False, is_dict=True) or {}
        except Exception as exc:
            log.warning("get_history(candidates) chunk failed: %s" % (exc,))
            _h = {}
        hist.update(_h or {})
    if not hist:
        log.warning("selection skip: empty history (%d codes)" % (len(codes),))

    # 诊断日志（首次运行）：输出首个候选 DataFrame 格式，用于排查 PTrade 返回形态
    if hist:
        _sample_code = next(iter(hist))
        _sample_df = hist[_sample_code]
        log.debug("selection sample %s shape=%s cols=%s index=%s index_dtype=%s" % (
            _sample_code,
            getattr(_sample_df, 'shape', 'N/A'),
            list(getattr(_sample_df, 'columns', [])),
            list(getattr(_sample_df, 'index', []))[:3],
            getattr(getattr(_sample_df, 'index', None), 'dtype', 'N/A'),
        ))

    g.last_close = {}
    picked = []
    for code, df in hist.items():
        try:
            if df is None or len(df) < 1:
                continue
            closes = _extract_history_field(df, "close")
            dates = _extract_history_field(df, "trade_date", dtype=str)
            if closes.size < 1 or dates.size < 1:
                continue
            emap = {}
            for j in range(len(dates)):
                dk = str(dates[j])
                if dk and dk != "nan":
                    try:
                        emap[dk] = float(closes[j])
                    except Exception:
                        pass
            first_date = str(dates[0])
            if regime == "momentum":
                c_1 = emap.get(end_Tm1)
                c_12 = emap.get(end_Tm12)
                if c_1 is None or c_12 is None or c_1 <= 0 or c_12 <= 0:
                    continue
                if first_date > end_Tm12:
                    continue
                ret = c_1 / c_12 - 1.0
            else:  # reversal
                c_T = emap.get(end_T)
                c_1 = emap.get(end_Tm1)
                if c_T is None or c_1 is None or c_T <= 0 or c_1 <= 0:
                    continue
                if first_date > end_Tm1:
                    continue
                ret = c_T / c_1 - 1.0
            picked.append((float(ret), code))
            # 记录最近收盘价（PTrade current_price 不可用时，买卖预筛用现成日线收盘）
            if closes.size > 0:
                try:
                    g.last_close[code] = float(closes[-1])
                except Exception:
                    pass
        except Exception:
            continue

    if not picked:
        log.warning("selection skip: no ranked candidates (%d codes checked) regime=%s end_T=%s end_Tm1=%s end_Tm12=%s sample_dates=%s" % (
            len(codes), regime, end_T, end_Tm1, end_Tm12,
            list(dates[:3]) if 'dates' in locals() and dates.size > 0 else "N/A"))
        return [], (cur_ym, end_T, end_Tm1, end_Tm12)

    if regime == "momentum":
        picked.sort(key=lambda t: (-t[0], t[1]))
    else:
        picked.sort(key=lambda t: (t[0], t[1]))
    targets = [c for _, c in picked[:_TOP_N]]
    return targets, (cur_ym, end_T, end_Tm1, end_Tm12)


# ---------------- 调仓 ----------------

def _current_raw_price(code):
    """执行口径现价（原始快照价）。失败返回 0（调用方据此跳过）。

    优先级：① 选股时记录的最近日线收盘（g.last_close，PTrade 通用）
          ② current_price API（本地可用；真实 PTrade 可能返回 0）
          ③ get_history 最近一根日线收盘（兜底）
    """
    try:
        lc = getattr(g, 'last_close', {}) or {}
        p = lc.get(code, 0.0)
        if p and p > 0:
            return float(p)
    except Exception:
        pass
    try:
        p = current_price(code)
        if p is not None and p > 0:
            return float(p)
    except Exception:
        pass
    try:
        h = get_history(count=1, frequency="1d", field=["close"],
                        security_list=[code], fq="pre", include=False)
        c = _extract_history_field(h, "close")
        if c.size > 0:
            v = float(c[-1])
            if v > 0:
                return v
    except Exception:
        pass
    return 0.0


def monthly_rebalance(context):
    """Scheduled: 波动率区制判定 + 动量/反转选5 + 等权清旧买新 + QA 审计。

    现金模型（close 即时撮合，参考已发布策略范式）：
    - 非目标持仓 order_target_value(0) 先卖（释放现金）
    - 新目标（未持有者）按 卖出后可用现金 / 新目标数 per_new 调仓（现金可负担，零 insufficient_cash）
    - 已持有且在目标中的保留（目标权重≈20%），不重复买入。
    """
    _ensure_runtime_state()
    today = context.current_dt
    today_ymd = _ymd(today)
    ym = _ym(today)
    if g.last_month == ym:
        return
    if today_ymd == g.last_rebalance_ymd:
        return

    regime, q, sigma_cur = _compute_regime(context)
    if regime is None:
        log.warning("monthly %s: regime unavailable, retry next trading day" % (today.date(),))
        return                      # 不锁月 → 次日重试

    targets, anchor_info = _selected_targets(context, regime)
    if not targets:
        log.warning("monthly %s regime=%s: no targets, retry next trading day"
                    % (today.date(), regime))
        return                      # 不锁月 → 次日重试

    total = _portfolio_total_value(context)
    if total <= 0:
        log.warning("monthly skip: invalid portfolio total_value=%.2f" % (total,))
        return

    # 状态复核（HALT/DELISTING）→ 可交易目标
    tradable = []
    for code in targets:
        try:
            store = get_stock_status(stocks=[code], query_type="HALT", query_date=None) or {}
            delist = get_stock_status(stocks=[code], query_type="DELISTING", query_date=None) or {}
        except Exception as exc:
            log.warning("get_stock_status failed: %s" % (exc,))
            store, delist = {}, {}
        if not (store.get(code) or delist.get(code)):
            tradable.append(code)
        else:
            log.info("skip status-blocked %s" % (code,))

    holdings = _current_positions(context)
    target_set_list = list(tradable)
    held_targets = [c for c in target_set_list if c in holdings]
    new_targets = [c for c in target_set_list if c not in holdings]

    g.rebalance_seq += 1
    rebalance_id = "R%s-%d" % (today.strftime("%Y%m%d"), g.rebalance_seq)

    # 1) 先卖非目标持仓
    sells = 0
    for code in list(holdings.keys()):
        if code not in target_set_list:
            order_target_value(security=code, value=0.0)
            sells += 1

    # 2) 买新目标：预算重分配（客户确认，2026-08-19）。
    #    目标5格等权（20%），但小资金×整手下不可负担的格子预算重新分配到可建仓格子，
    #    保持 ~1x 满仓（gross≈1、现金≈0）；迭代收敛 per_slot（有限 5 轮）。
    buys = 0
    per_new = 0.0
    if new_targets:
        cash_avail = float(getattr(context.portfolio, 'cash', 0.0) or 0.0)
        candidates = list(new_targets)
        buildable_new = sorted(candidates)
        per = cash_avail / float(max(1, len(candidates)))
        for _ in range(5):
            keep = []
            for code in buildable_new:
                px = _current_raw_price(code)
                if px > 0 and px * _LOT <= per * _PX_BUFFER:
                    keep.append(code)
                else:
                    log.info("skip redistributed %s (px=%.2f lot=%.0f per_slot=%.2f)"
                             % (code, px, px * _LOT, per))
            buildable_new = sorted(keep)
            if not buildable_new:
                break
            new_per = cash_avail / float(len(buildable_new))
            if abs(new_per - per) < 0.01:
                per = new_per
                break
            per = new_per
        per_new = per if buildable_new else 0.0
        for code in buildable_new:
            order_target_value(security=code, value=per_new)
            buys += 1

    log.info("QS_REBALANCE_AUDIT rebalance_id=%s date=%s regime=%s q=%.4f "
             "sigma=%.6f selected=%d tradable=%d sell_submitted=%d buy_submitted=%d"
             % (rebalance_id, today.strftime("%Y-%m-%d"), regime, q, sigma_cur,
                len(targets), len(tradable), sells, buys))

    # 组合审计（实际持仓数 / 现金 / 总仓位）
    after_pos = _current_positions(context)
    pos_count = len(after_pos)
    total_after = _portfolio_total_value(context)
    cash_after = float(getattr(context.portfolio, "cash", 0.0) or 0.0)
    gross = 0.0 if total_after <= 0 else (total_after - cash_after) / total_after
    cash_ratio = 0.0 if total_after <= 0 else cash_after / total_after
    log.info("QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d "
             "cash_ratio=%.6f gross_exposure=%.6f"
             % (rebalance_id, today.strftime("%Y-%m-%d"), pos_count, cash_ratio, gross))

    g.last_month = ym
    g.last_rebalance_ymd = today_ymd
    g.regime = regime
    g.last_q = q
    g.targets = list(target_set_list)
    g.current_holdings = sorted(after_pos.keys())
    log.info("monthly %s regime=%s q=%.4f -> traded targets=%d/%d held=%d new=%d per_new=%.2f"
             % (today.date(), regime, q, len(tradable), len(targets),
                len(held_targets), buys, per_new))
