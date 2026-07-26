"""
tech_etf_mvo_rotation.py - agent-authored canonical QuantStudio/PTrade strategy.

科技ETF四维评分MVO轮动策略（design_version 2.0, strategy_id tech_etf_mvo_rotation）

语义（客户 R0/R2.5 已确认）：
- 固定池：8 只科技细分赛道 ETF 评分，2 只避险 ETF（黄金/国债）仅参与 MVO 对冲；
- 四维截面评分（252 日，fq='pre'，include=False，信号截止 T-1 收盘）：
  年化收益率(1x) + 夏普比率(2x) + 胜率(1x) + 最大回撤反向(1x)，百分位归一，Top3 入选；
- 权重：120 日协方差 MVO（做多-only、满仓、期望收益不低于入选均值），失败回退 Top3 等权；
- 风控：MACD 死叉标的权重减半；任一入选标的 21 日年化波动率 252 日百分位 > 80% → 总仓位 ×0.5；
- 周频：run_daily 15:00 调度，ISO 周边界去重，仅每周第一个交易日调仓；
- 暖机期（历史不足 252 日）持币空仓；缺数据标的 fail-soft 跳过。

平台纪律：
- 显式 import numpy/pandas；g/log 与公共 API 由平台提供；不使用 MyTT/get_current_data/check_limit/data[code]；
- get_history 使用 PTrade count-first 便携签名；订单返回值按不透明 ID/None 处理；
- 所有回调首行调用幂等 _ensure_runtime_state()。
"""

import numpy as np
import pandas as pd


STRATEGY_ID = 'tech_etf_mvo_rotation'
DESIGN_VERSION = '2.0'


def _ensure_runtime_state():
    """Idempotently create every g field used by any callback."""
    if not hasattr(g, 'tech_pool'):
        # PTrade 便携后缀 .SS/.SZ（本地适配层按裸码归一化）
        g.tech_pool = [
            '515000.SS',  # 科技ETF
            '512760.SS',  # 芯片ETF
            '515880.SS',  # 通信ETF
            '512720.SS',  # 计算机ETF
            '159995.SZ',  # 芯片ETF(深)
            '588000.SS',  # 科创50ETF
            '515050.SS',  # 5GETF
            '512480.SS',  # 半导体ETF
        ]
    if not hasattr(g, 'hedge_pool'):
        g.hedge_pool = [
            '518880.SS',  # 黄金ETF
            '511260.SS',  # 十年国债ETF
        ]
    if not hasattr(g, 'lookback'):
        g.lookback = 252            # 四维指标回看窗口（交易日）
    if not hasattr(g, 'mvo_lookback'):
        g.mvo_lookback = 120        # MVO 协方差窗口
    if not hasattr(g, 'top_n'):
        g.top_n = 3                 # 入选数量
    if not hasattr(g, 'w_ann'):
        g.w_ann = 1.0               # 年化收益率权重
    if not hasattr(g, 'w_sharpe'):
        g.w_sharpe = 2.0            # 夏普比率权重（最重）
    if not hasattr(g, 'w_win'):
        g.w_win = 1.0               # 胜率权重
    if not hasattr(g, 'w_mdd'):
        g.w_mdd = 1.0               # 最大回撤（反向）权重
    if not hasattr(g, 'vol_window'):
        g.vol_window = 21           # 波动率窗口
    if not hasattr(g, 'vol_pct_threshold'):
        g.vol_pct_threshold = 0.80  # 波动率历史百分位降仓阈值
    if not hasattr(g, 'vol_scale'):
        g.vol_scale = 0.5           # 触发后总仓位缩放
    if not hasattr(g, 'macd_scale'):
        g.macd_scale = 0.5          # 死叉标的权重缩放
    if not hasattr(g, 'last_rebalance_week'):
        g.last_rebalance_week = None  # (iso_year, iso_week) 周频去重


# ---------------------------------------------------------------------------
# 便携工具函数（NumPy/pandas 显式导入，PTrade 不依赖本地注入）
# ---------------------------------------------------------------------------

def _bare(code):
    """提取裸 6 位代码，跨 .SS/.SZ/.XSHG/.SH 等后缀比较。"""
    s = str(code)
    digits = ''
    for ch in s:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return digits[:6]


def _get_close(code, count):
    """便携取前复权收盘价序列（np.array，升序）；失败/空返回 None。"""
    try:
        hist = get_history(count, frequency='1d', field=['close'],
                           security_list=code, fq='pre', include=False)
    except Exception as e:
        log.warning('get_history 失败 %s: %s' % (code, e))
        return None
    try:
        if hist is None:
            return None
        if isinstance(hist, dict):
            # is_dict 形式兜底（部分券商返回映射）
            obj = None
            for k in hist:
                obj = hist[k]
                break
            if obj is None:
                return None
            hist = obj
        if hasattr(hist, 'columns'):
            if 'close' not in list(hist.columns):
                return None
            arr = hist['close'].values
        else:
            arr = hist
        arr = np.asarray(arr, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) == 0:
            return None
        return arr
    except Exception as e:
        log.warning('历史解析失败 %s: %s' % (code, e))
        return None


def _daily_returns(close):
    """日收益序列。"""
    prev = close[:-1]
    nxt = close[1:]
    mask = prev > 0
    if mask.sum() < 2:
        return None
    return (nxt[mask] - prev[mask]) / prev[mask]


def _pct_rank(values, v):
    """v 在 values 中的百分位（0~1），截面相对打分用。"""
    n = len(values)
    if n <= 1:
        return 1.0
    less = 0
    for x in values:
        if x < v:
            less += 1
    return float(less) / float(n - 1)


def _four_factor_metrics(close):
    """四维指标：年化收益、夏普、胜率、最大回撤绝对值。数据不足返回 None。"""
    if close is None or len(close) < g.lookback:
        return None
    window = close[-(g.lookback + 1):]
    rets = _daily_returns(window)
    if rets is None or len(rets) < g.lookback - 2:
        return None
    n = len(rets)
    start_px = window[0]
    end_px = window[-1]
    if start_px <= 0:
        return None
    ann_ret = (end_px / start_px) ** (252.0 / n) - 1.0
    mu = float(np.mean(rets))
    sigma = float(np.std(rets))
    sharpe = mu / sigma * float(np.sqrt(252.0)) if sigma > 1e-12 else 0.0
    win_rate = float((rets > 0).sum()) / float(n)
    nav = np.cumprod(1.0 + rets)
    peak = np.maximum.accumulate(nav)
    dd = nav / peak - 1.0
    mdd = abs(float(np.min(dd)))
    return {'ann': ann_ret, 'sharpe': sharpe, 'win': win_rate, 'mdd': mdd}


def _ema(arr, n):
    """便携 EMA（pandas ewm, adjust=False），返回 np.array。"""
    return pd.Series(arr).ewm(span=n, adjust=False).mean().values


def _macd_dead_cross(close):
    """MACD(12,26,9) 死叉判定：DIF 上穿后下穿 DEA（最新两根）。"""
    if close is None or len(close) < 35:
        return False
    try:
        dif = _ema(close, 12) - _ema(close, 26)
        dea = _ema(dif, 9)
        return bool(dif[-2] >= dea[-2] and dif[-1] < dea[-1])
    except Exception:
        return False


def _vol_percentile(close):
    """最新 21 日年化波动率在其 252 日历史序列中的百分位（0~1）；不足返回 None。"""
    if close is None or len(close) < g.lookback:
        return None
    rets = _daily_returns(close[-(g.lookback + 1):])
    if rets is None or len(rets) < g.vol_window + 20:
        return None
    try:
        s = pd.Series(rets)
        vol = s.rolling(g.vol_window).std() * float(np.sqrt(252.0))
        vol = vol.values
        vol = vol[np.isfinite(vol)]
        if len(vol) < 20:
            return None
        latest = vol[-1]
        hist = vol[:-1]
        return float((hist < latest).sum()) / float(len(hist))
    except Exception:
        return None


def _mvo_weights(codes, closes):
    """均值方差优化（做多-only、满仓、期望收益不低于入选科技均值）。

    codes: 入选科技 + 可用避险资产；closes: {code: close_array}
    返回 {code: weight}；失败返回 None（调用方回退等权）。
    """
    try:
        m = g.mvo_lookback
        ret_cols = []
        valid = []
        for c in codes:
            cl = closes.get(c)
            if cl is None or len(cl) < m + 1:
                continue
            r = _daily_returns(cl[-(m + 1):])
            if r is None or len(r) < m - 2:
                continue
            ret_cols.append(r[-m:])
            valid.append(c)
        k = len(valid)
        if k == 0:
            return None
        min_len = min(len(r) for r in ret_cols)
        ret_cols = [r[-min_len:] for r in ret_cols]
        R = np.vstack(ret_cols)             # k x T
        mu = np.mean(R, axis=1)             # k
        cov = np.cov(R)                     # k x k
        cov = cov + np.eye(k) * 1e-8        # 正则化防奇异
        inv = np.linalg.inv(cov)
        ones = np.ones(k)
        # 全局最小方差解
        w_gmv = inv.dot(ones) / float(ones.dot(inv).dot(ones))
        tech_idx = [i for i, c in enumerate(valid) if c in list(g.tech_pool)]
        if len(tech_idx) == 0:
            return None
        mu_min = float(np.mean(mu[tech_idx]))
        w = w_gmv
        if float(mu.dot(w_gmv)) < mu_min:
            # 沿最高期望收益方向混合，直到满足收益下界
            best = int(np.argmax(mu))
            w_ret = np.zeros(k)
            w_ret[best] = 1.0
            chosen = None
            for step in range(1, 21):
                alpha = step / 20.0
                cand = (1.0 - alpha) * w_gmv + alpha * w_ret
                if float(mu.dot(cand)) >= mu_min:
                    chosen = cand
                    break
            if chosen is not None:
                w = chosen
        # 做多-only：负权重裁剪 + 满仓归一
        w = np.clip(w, 0.0, None)
        total = float(w.sum())
        if total <= 1e-12 or not np.isfinite(total):
            return None
        w = w / total
        return {valid[i]: float(w[i]) for i in range(k)}
    except Exception as e:
        log.warning('MVO 求解失败，回退等权: %s' % e)
        return None


# ---------------------------------------------------------------------------
# 生命周期与调度
# ---------------------------------------------------------------------------

def initialize(context):
    """设基准、初始化状态、注册周频调仓调度。"""
    _ensure_runtime_state()
    set_benchmark('000300.SS')
    # daily-bar-v1 Profile：调度时刻须为 15:00（A8）；close 模式即时撮合，
    # 信号截止 T-1 收盘、T 日收盘成交，与 R0#9 语义一致。
    run_daily(context, weekly_rebalance, time='15:00')
    log.info('[%s] 初始化完成: 科技池%d只 避险池%d只 top%d 回看%d日' % (
        STRATEGY_ID, len(g.tech_pool), len(g.hedge_pool), g.top_n, g.lookback))


def weekly_rebalance(context):
    """每日 15:00 被调度；ISO 周变化时执行评分+MVO+风控+调仓。"""
    _ensure_runtime_state()

    # --- 周频去重：仅每周第一个交易日执行 ---
    dt = context.current_dt
    try:
        if hasattr(dt, 'isocalendar'):
            iso = dt.isocalendar()
            week_key = (int(iso[0]), int(iso[1]))
        else:
            week_key = (int(pd.Timestamp(str(dt)[:10]).isocalendar()[0]),
                        int(pd.Timestamp(str(dt)[:10]).isocalendar()[1]))
    except Exception:
        week_key = str(dt)[:10]
    if g.last_rebalance_week == week_key:
        return
    g.last_rebalance_week = week_key

    # --- 取数（信号截止 T-1 收盘，fq='pre' 前复权）---
    closes = {}
    for code in list(g.tech_pool) + list(g.hedge_pool):
        cl = _get_close(code, g.lookback + 1)
        if cl is not None and len(cl) >= g.lookback:
            closes[code] = cl
        else:
            log.info('历史不足/缺数据，跳过: %s' % code)

    # 数据整体不可用（暖机期/数据故障）：持币不动
    tech_available = [c for c in g.tech_pool if c in closes]
    if len(tech_available) == 0:
        log.info('[%s] %s 暖机期或数据不可用，本期持币不交易' % (STRATEGY_ID, str(dt)[:10]))
        return

    # --- 四维评分（截面百分位） ---
    metrics = {}
    for code in tech_available:
        m = _four_factor_metrics(closes[code])
        if m is not None:
            metrics[code] = m
    if len(metrics) == 0:
        log.info('[%s] %s 指标计算为空，本期持币不交易' % (STRATEGY_ID, str(dt)[:10]))
        return

    anns = [m['ann'] for m in metrics.values()]
    sharpes = [m['sharpe'] for m in metrics.values()]
    wins = [m['win'] for m in metrics.values()]
    mdds = [m['mdd'] for m in metrics.values()]

    scored = []
    for code, m in metrics.items():
        score = (g.w_ann * _pct_rank(anns, m['ann'])
                 + g.w_sharpe * _pct_rank(sharpes, m['sharpe'])
                 + g.w_win * _pct_rank(wins, m['win'])
                 + g.w_mdd * (1.0 - _pct_rank(mdds, m['mdd'])))
        scored.append((code, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    selected = [c for c, s in scored[:g.top_n]]
    log.info('[%s] %s 评分排名: %s' % (
        STRATEGY_ID, str(dt)[:10],
        ', '.join('%s=%.3f' % (c, s) for c, s in scored)))

    # --- MVO 权重（入选科技 + 避险池） ---
    mvo_codes = list(selected)
    for hc in g.hedge_pool:
        if hc in closes:
            mvo_codes.append(hc)
    weights = _mvo_weights(mvo_codes, closes)
    if weights is None:
        # 回退：Top3 等权（基础仓位）
        weights = {c: 1.0 / len(selected) for c in selected}
        log.info('[%s] MVO 回退等权: %s' % (STRATEGY_ID, str(selected)))

    # --- 风控一：MACD 死叉降仓（仅科技入选标的） ---
    for code in selected:
        if weights.get(code, 0.0) > 0.0 and _macd_dead_cross(closes[code]):
            weights[code] = weights[code] * g.macd_scale
            log.info('[%s] %s MACD死叉，权重减半 -> %.3f' % (STRATEGY_ID, code, weights[code]))

    # --- 风控二：波动率百分位降仓（组合级） ---
    vol_trigger = False
    for code in selected:
        pct = _vol_percentile(closes[code])
        if pct is not None and pct > g.vol_pct_threshold:
            vol_trigger = True
            log.info('[%s] %s 21日波动率百分位 %.2f > %.2f，触发降仓' % (
                STRATEGY_ID, code, pct, g.vol_pct_threshold))
    if vol_trigger:
        for code in list(weights.keys()):
            weights[code] = weights[code] * g.vol_scale
        log.info('[%s] 组合总仓位收紧至 %.0f%%' % (STRATEGY_ID, g.vol_scale * 100))

    # 过滤微小权重
    weights = {c: w for c, w in weights.items() if w > 1e-4}
    target_bares = set(_bare(c) for c in weights.keys())

    # --- 执行：先卖后买（T+1/整手由引擎处理，订单返回值按不透明 ID/None 处理） ---
    total_value = float(context.portfolio.portfolio_value)
    try:
        positions = context.portfolio.positions
        held_keys = list(positions.keys())
    except Exception:
        held_keys = []
    for key in held_keys:
        if _bare(key) not in target_bares:
            try:
                amount = float(getattr(positions[key], 'amount', 0))
            except Exception:
                amount = 0.0
            if amount > 0:
                oid = order_target_value(security=key, value=0.0)
                if oid is None:
                    log.warning('[%s] 清仓委托失败: %s' % (STRATEGY_ID, key))
                else:
                    log.info('[%s] 清仓: %s' % (STRATEGY_ID, key))
    for code, w in weights.items():
        target_value = total_value * w
        if target_value < 1000.0:
            continue
        oid = order_target_value(security=code, value=target_value)
        if oid is None:
            log.warning('[%s] 委托失败: %s -> %.0f' % (STRATEGY_ID, code, target_value))
        else:
            log.info('[%s] 调仓: %s 目标权重 %.3f 市值 %.0f' % (
                STRATEGY_ID, code, w, target_value))
