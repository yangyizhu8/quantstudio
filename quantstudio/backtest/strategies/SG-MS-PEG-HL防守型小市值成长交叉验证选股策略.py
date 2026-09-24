"""
SG-MS-PEG-HL防守型小市值成长交叉验证选股策略（sg_ms_peg_hl_defensive_smallcap_growth）- agent-authored QuantStudio-only strategy.

Chinese published filename: SG-MS-PEG-HL防守型小市值成长交叉验证选股策略.py (quantstudio/backtest/strategies/<strategy_name>.py).

策略语义（客户确认 design 2.2，R0 13 项裁决 + R2.5 四项裁决）：
- 三大独立成长维度交叉验证选股（无机器学习、无财报单一看法）：
  维度SG  营收增长：营收 CAGR 降序 Top50（自适应年限：base=最早可得年报且最多回看4年，R2.5 f1）
  维度MS  综合增长评分：净利CAGR/利润总额CAGR/最新年报净利润额/年报ROE均值，
          四因子横截面百分位秩(0-100)等权25%加权 → Top50
  维度PEG 估值性价比：PE_TTM>0 且 净利CAGR>0 → PEG=PE_TTM/(CAGR×100) 升序 Top50；
          维度内先剔除换手率异常股（20日CV>1.2 或 20日日均换手率>20%，R2.5 a4 仅作用于维度3）
- 交叉：三维度并集中被 >=2 个维度同时选中 → 高置信度成长候选池 → 流通市值升序 Top10
- 交易风控：
  ①持仓当日涨停不卖（方案A日线近似：收盘封住=维持→顺延；未封住=已打开→当日收盘卖出，R0 q9）
  ②过去20个交易日曾收盘涨停的标的禁止新建买入（分板块幅度，R0 q8）
  ③调仓日收盘涨停的目标股跳过买入（涨停买不进，R2.5 f3）
- 组合：等权10只，runtime_total_value 动态推导；月频（每月第一个交易日）调仓，先卖后买
- 费率：佣金万3双边（min_commission=0）+ 卖出印花税千1（引擎默认）+ 过户万0.1（R2.5 f2）
- 基准：沪深300（000300.SS）；目标平台：quantstudio 本地（PTrade 转换 out of scope）

PIT 契约：全部信号仅用 T-1 及以前已完成数据（行情 get_history_batch 字面 fq='pre'+include=False；
财务 get_fundamentals ann_date PIT；估值 get_fundamentals(_batch) valuation PIT）；执行/估值/涨停判定用 raw 价基。
"""

import numpy as np
import pandas as pd

STRATEGY_ID = 'sg_ms_peg_hl_defensive_smallcap_growth'
STRATEGY_NAME = 'SG-MS-PEG-HL防守型小市值成长交叉验证选股策略'
DESIGN_VERSION = '2.2'

# ---- 确认参数（客户 R0/R2.5 裁决，不得运行时改动）----
_TARGET_HOLDINGS = 10              # R0 q1：持仓10只·等权
_PER_POSITION_WEIGHT = 0.098       # gross 0.98 / 10 只（2% 现金缓冲覆盖费用与整手取整）
_TOP_N_PER_DIM = 50                # R0 q2：每维度 Top50
_TOP_N_MS = 50
_MIN_LISTED_DAYS = 365             # R0 q12：上市不满1年剔除
_EXCLUDE_BSE = True                # R0 q12：剔北交所
_MS_WEIGHTS = (0.25, 0.25, 0.25, 0.25)  # R0 q3：等权（CAGR净利/CAGR利润总额/净利额/ROE均值）
_PEG_TOP_N = 50
_TURNOVER_LOOKBACK = 20            # R0 q6：20日
_TURNOVER_CV_MAX = 1.2             # R0 q6：CV>1.2 剔除
_TURNOVER_DAILY_AVG_MAX_PCT = 20.0 # R0 q6：日均换手>20% 剔除
_LIMIT_LOOKBACK = 20               # 风控2：过去20个交易日曾涨停禁入
_LIMIT_TOL = 0.001                 # 涨停判定容差（元）
_LOOKBACK_BARS = 21                # include=False 下取21根= T-21..T-1
_BATCH = 800                       # 全市场批查询分批上限
_MAX_CAGR_LOOKBACK_YEARS = 4       # R2.5 f1：base 最多回看4年
_ROE_MEAN_MAX_REPORTS = 5          # ROE均值取最近≤5个年报
_BUY_VALUE_FLOOR = 1000.0          # 买入金额下限（资金不足时放弃，防尘单）


def _ensure_runtime_state():
    """幂等创建全部 g 状态字段（hasattr 守卫，绝不重置既有状态）。"""
    if not hasattr(g, 'target_list'):
        g.target_list = []
    if not hasattr(g, 'pending_exit'):
        g.pending_exit = []
    if not hasattr(g, 'last_rebalance_ym'):
        g.last_rebalance_ym = None
    if not hasattr(g, 'rebalance_seq'):
        g.rebalance_seq = 0
    if not hasattr(g, 'last_audit_rebalance_id'):
        g.last_audit_rebalance_id = None
    if not hasattr(g, 'status_pool'):
        g.status_pool = []
    if not hasattr(g, 'status_pool_date'):
        g.status_pool_date = ''


def _extract_history_field(history_item, field, dtype=float):
    """get_history(is_dict=True) 字段提取归一（skill 绝对规则 17 硬闸）。

    item 可能是 DataFrame / 结构化数组 / recarray；字段可能是 Series 或 ndarray。
    统一 np.asarray 归一后返回；任何异常/缺失形状 fail-soft 返回空 ndarray。
    """
    try:
        if history_item is None or field is None:
            return np.asarray([], dtype=dtype)
        values = history_item[field]
        if values is None:
            return np.asarray([], dtype=dtype)
        if hasattr(values, 'values'):
            values = values.values
        return np.asarray(values, dtype=dtype)
    except Exception:
        return np.asarray([], dtype=dtype)


def _bare(code):
    return str(code).split('.')[0]


def _limit_ratio_pct(code):
    """板块涨停幅度（%）：创业板30XXXX/科创板688XXX=20，其余主板=10（北交所已在池剔除）。"""
    bare = _bare(code)
    if bare.startswith(('300', '301', '688', '689')):
        return 20.0
    return 10.0


def _chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _iso_year_month(dt):
    return (int(dt.year), int(dt.month))


def initialize(context):
    """配置参数、成本与基准（本地扩展 set_backtest 不使用：资金由回测配置注入）。"""
    _ensure_runtime_state()
    set_benchmark('000300.SS')
    set_commission(commission_ratio=0.0003, min_commission=0)


def before_trading_start(context, data):
    """盘前：当日状态过滤池（filter_stock_by_status 的注册合法宿主），按日缓存。

    query_date=None → 引擎上一交易日快照（与 T-1 信号截止一致）。
    引擎以 (context, data) 两参调用本回调。
    """
    _ensure_runtime_state()
    today = context.current_dt.strftime('%Y-%m-%d')
    if g.status_pool_date == today and g.status_pool:
        return
    codes = get_Ashares(exclude_bse=_EXCLUDE_BSE)
    if not codes:
        g.status_pool = []
        g.status_pool_date = today
        return
    pool = filter_stock_by_status(codes, filter_type=['ST', 'HALT', 'DELISTING'],
                                  query_date=None)
    g.status_pool = list(pool) if pool else []
    g.status_pool_date = today


def _fetch_statement_rows(codes, table, fields):
    """批量取三大报表 PIT 年报行（ann_date PIT 由 API 内建），返回原始 DataFrame。"""
    frames = []
    for batch in _chunk(codes, _BATCH):
        try:
            df = get_fundamentals(batch, table, fields=fields, report_types='4')
        except Exception:
            df = None
        if df is not None and len(df) > 0:
            frames.append(df)
    if not frames:
        return None
    return pd.concat(frames, axis=0, ignore_index=False)


def _cagr_factors_from_income(df):
    """从利润表年报行计算每码（营收CAGR, 净利CAGR, 利润总额CAGR, 最新年报净利额）。

    自适应年限（R2.5 f1）：L=最新年报 end_date；base=最早可得年报且 end_date>=L-4年
    （若无窗口内行→取最早可得行）；n=两期间年数（>=1）；CAGR=(V_L/V_base)^(1/n)-1。
    需 >=2 个不同年报期且基期值>0（负/零基期 CAGR 无定义→该因子缺失）。
    """
    out = {}
    if df is None or len(df) == 0:
        return out
    try:
        end_ms = np.asarray(df['end_date'], dtype=float)
        rev = np.asarray(df['operating_revenue'], dtype=float)
        npf = np.asarray(df['net_profit'], dtype=float)
        tpf = np.asarray(df['total_profit'], dtype=float)
        code_arr = [str(c) for c in df.index]
    except Exception:
        return out
    acc = {}
    for i in range(len(code_arr)):
        ed = end_ms[i]
        if not np.isfinite(ed):
            continue
        acc.setdefault(code_arr[i], []).append(
            (ed, rev[i], npf[i], tpf[i]))
    for code, rows in acc.items():
        rows.sort(key=lambda r: r[0])
        # 同报告期多次披露取最新披露行（列表稳定排序后保留末行）
        dedup = {}
        for r in rows:
            dedup[r[0]] = r
        seq = [dedup[k] for k in sorted(dedup.keys())]
        if len(seq) < 2:
            continue
        latest = seq[-1]
        l_ms = latest[0]
        # base：end_date >= L-4年 的最早行；窗口内无（L 为首份年报）→ 最早行（n=1）
        window_min = l_ms - _MAX_CAGR_LOOKBACK_YEARS * 365.25 * 86400000.0
        base = None
        for r in seq[:-1]:
            if r[0] >= window_min:
                base = r
                break
        if base is None:
            base = seq[0]
        n_years = max(1.0, (l_ms - base[0]) / (365.25 * 86400000.0))
        vals = []
        for k in (1, 2, 3):
            lv, bv = latest[k], base[k]
            if np.isfinite(lv) and np.isfinite(bv) and bv > 0 and lv > 0:
                vals.append((lv / bv) ** (1.0 / n_years) - 1.0)
            else:
                vals.append(np.nan)
        out[code] = {'rev_cagr': vals[0], 'np_cagr': vals[1],
                     'tp_cagr': vals[2], 'np_latest': latest[2],
                     'cagr_n': n_years}
    return out


def _roe_mean_from_fin_indicator(df):
    """每码年报 ROE 均值（最近<=5个年报行；ann_date PIT 由 API 内建）。"""
    out = {}
    if df is None or len(df) == 0:
        return out
    try:
        roe = np.asarray(df['roe'], dtype=float)
        end_ms = np.asarray(df['end_date'], dtype=float)
        code_arr = [str(c) for c in df.index]
    except Exception:
        return out
    acc = {}
    for i in range(len(code_arr)):
        if np.isfinite(roe[i]):
            acc.setdefault(code_arr[i], []).append((end_ms[i], roe[i]))
    for code, pairs in acc.items():
        pairs.sort(key=lambda r: r[0])
        recent = [v for _, v in pairs[-_ROE_MEAN_MAX_REPORTS:]]
        arr = np.asarray(recent, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr):
            out[code] = float(np.nanmean(arr))
    return out


def _pct_rank(series):
    """横截面百分位秩 0-100（ties=average，确定性）。空输入返回空映射。"""
    s = pd.Series(series, dtype=float).dropna()
    if len(s) == 0:
        return {}
    ranked = s.rank(pct=True) * 100.0
    return dict(zip(ranked.index.tolist(), ranked.values.tolist()))


def _top_n(score_map, n):
    """按分数降序取前 n（分数相同的按代码字典序稳定排序，保证跨进程确定性）。"""
    items = [(k, v) for k, v in score_map.items() if np.isfinite(v)]
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    return [k for k, _ in items[:n]]


def _prev_20d_limit_hit_map(context, codes):
    """每码过去20个交易日（T-21..T-1）是否出现收盘涨停（引擎合成 pctChg，raw 基列）。

    返回 {code: bool}；数据缺失视为 False（fail-soft，该股按未涨停处理）。
    """
    result = {c: False for c in codes}
    for batch in _chunk(codes, _BATCH):
        try:
            hist = get_history_batch(batch, _LOOKBACK_BARS, '1d',
                                     fields=['pctChg'], fq='pre', include=False)
        except Exception:
            continue
        for code in batch:
            item = hist.get(code) if hist is not None else None
            arr = _extract_history_field(item, 'pctChg', dtype=float)
            if len(arr) == 0:
                continue
            tail = arr[-_LIMIT_LOOKBACK:]
            tail = tail[np.isfinite(tail)]
            if len(tail) == 0:
                continue
            thr = _limit_ratio_pct(code) - 0.1  # 断板反包 E1 同款 0.1pct 容差
            if float(np.max(tail)) >= thr:
                result[code] = True
    return result


def _turnover_stats_map(context, codes, anchor_map):
    """每码（20日换手率CV, 20日日均换手率%）。

    CV = std(volume[-20:])/mean(volume[-20:])（换手率与成交量成正比，量纲消去）。
    日均换手率% = turnover_ratio[T-1] × mean(volume)/volume[-1]（平台锚换算）；
    锚缺失/为0时日均换手率返回 NaN（该股仅做CV判定）。
    """
    stats = {c: (np.nan, np.nan) for c in codes}
    for batch in _chunk(codes, _BATCH):
        try:
            hist = get_history_batch(batch, _LOOKBACK_BARS, '1d',
                                     fields=['volume'], fq='pre', include=False)
        except Exception:
            continue
        for code in batch:
            item = hist.get(code) if hist is not None else None
            vol = _extract_history_field(item, 'volume', dtype=float)
            if len(vol) == 0:
                continue
            tail = vol[-_TURNOVER_LOOKBACK:]
            tail = tail[np.isfinite(tail) & (tail > 0)]
            if len(tail) < 5:
                continue
            mean_v = float(np.mean(tail))
            std_v = float(np.std(tail))
            cv = std_v / mean_v if mean_v > 0 else np.nan
            tr = anchor_map.get(code, np.nan)
            last_v = float(tail[-1])
            daily_avg_pct = (tr * mean_v / last_v
                             if np.isfinite(tr) and tr > 0 and last_v > 0 else np.nan)
            stats[code] = (cv, daily_avg_pct)
    return stats


def _select_targets(context):
    """三维度独立筛选 → 交叉验证 → 小市值 Top10（全部信号 T-1 截止）。

    返回 (target_list, counts_dict)。
    """
    counts = {}
    today = context.current_dt.date()

    # ---- P0：状态过滤池（盘前缓存）+ 上市不满1年剔除 ----
    codes = list(g.status_pool)
    counts['P0_status_pool'] = len(codes)
    try:
        info = get_stock_info(codes, field=['listed_date'])
    except Exception:
        info = {}
    stage1 = []
    for code in codes:
        listed = (info.get(code) or {}).get('listed_date')
        if not listed:
            continue  # 无上市日期数据 → fail-soft 剔除（宁缺勿假）
        try:
            listed_date = pd.Timestamp(listed).date()
        except Exception:
            continue
        if (today - listed_date).days < _MIN_LISTED_DAYS:
            continue
        stage1.append(code)
    counts['P1_listed'] = len(stage1)
    if len(stage1) == 0:
        return [], counts

    # ---- P2：利润表年报行（PIT ann_date）→ SG/MS 增长因子 ----
    inc_df = _fetch_statement_rows(stage1, 'income_statement',
                                   fields=['operating_revenue', 'net_profit',
                                           'total_profit', 'end_date', 'publ_date'])
    fac = _cagr_factors_from_income(inc_df)
    counts['P2_income_codes'] = len(fac)
    stage2 = [c for c in stage1 if c in fac]
    if len(stage2) == 0:
        return [], counts

    # ---- P3：年报 ROE 均值 ----
    roe_df = _fetch_statement_rows(stage2, 'profit_ability',
                                   fields=['roe', 'end_date'])
    roe_map = _roe_mean_from_fin_indicator(roe_df)
    counts['P3_roe_codes'] = len(roe_map)

    # ---- P4：估值（T-1 PIT）PE_TTM / 流通市值 / 换手锚 ----
    pe_map = {}
    fv_map = {}
    tr_map = {}
    for batch in _chunk(stage2, _BATCH):
        try:
            vdf = get_fundamentals_batch(batch, 'valuation',
                                         fields=['pe_ratio', 'float_value',
                                                 'turnover_ratio'])
        except Exception:
            continue
        if vdf is None or len(vdf) == 0:
            continue
        idx = [str(c) for c in vdf.index]
        for col, target_map in (('pe_ratio', pe_map), ('float_value', fv_map),
                                ('turnover_ratio', tr_map)):
            if col in vdf.columns:
                vals = np.asarray(vdf[col], dtype=float)
                for k, c in enumerate(idx):
                    if np.isfinite(vals[k]):
                        target_map[c] = float(vals[k])
    counts['P4_valuation_codes'] = len(fv_map)
    stage3 = [c for c in stage2 if c in fv_map and np.isfinite(fv_map[c])
              and fv_map[c] > 0]
    if len(stage3) == 0:
        return [], counts

    # ---- 维度SG：营收CAGR 降序 Top50 ----
    rev_series = {c: fac[c]['rev_cagr'] for c in stage3
                  if np.isfinite(fac[c]['rev_cagr'])}
    sg_top = _top_n(rev_series, _TOP_N_PER_DIM)
    counts['D1_sg'] = len(sg_top)

    # ---- 维度MS：四因子百分位秩等权加权 → Top50 ----
    r_np = _pct_rank({c: fac[c]['np_cagr'] for c in stage3
                      if np.isfinite(fac[c]['np_cagr'])})
    r_tp = _pct_rank({c: fac[c]['tp_cagr'] for c in stage3
                      if np.isfinite(fac[c]['tp_cagr'])})
    r_amt = _pct_rank({c: fac[c]['np_latest'] for c in stage3
                       if np.isfinite(fac[c]['np_latest'])})
    r_roe = _pct_rank({c: roe_map[c] for c in stage3 if c in roe_map})
    ms_score = {}
    for c in stage3:
        parts = []
        for rank_map, w in zip((r_np, r_tp, r_amt, r_roe), _MS_WEIGHTS):
            v = rank_map.get(c)
            parts.append(w * v if v is not None else np.nan)
        arr = np.asarray([p for p in parts if np.isfinite(p)], dtype=float)
        if len(arr) >= 2:  # 至少2个因子有值才可评分（fail-soft，防止缺数股得高分）
            ms_score[c] = float(np.nansum(arr) / float(len(arr)))
    ms_top = _top_n(ms_score, _TOP_N_MS)
    counts['D2_ms'] = len(ms_top)

    # ---- 维度PEG：换手异常剔除 → PE>0 且 净利CAGR>0 → 低PEG Top50 ----
    turn_stats = _turnover_stats_map(context, stage3, tr_map)
    stage4 = []
    for c in stage3:
        cv, daily_avg = turn_stats.get(c, (np.nan, np.nan))
        if np.isfinite(cv) and cv > _TURNOVER_CV_MAX:
            continue  # 忽高忽低
        if np.isfinite(daily_avg) and daily_avg > _TURNOVER_DAILY_AVG_MAX_PCT:
            continue  # 换手水平过高
        stage4.append(c)
    counts['D3a_turnover_ok'] = len(stage4)
    peg_series = {}
    for c in stage4:
        pe = pe_map.get(c, np.nan)
        cagr = fac[c]['np_cagr'] if np.isfinite(fac[c]['np_cagr']) else np.nan
        if np.isfinite(pe) and pe > 0 and np.isfinite(cagr) and cagr > 0:
            peg_series[c] = pe / (cagr * 100.0)
    # PEG 语义要求"数值偏低"取头部 → 用 1/PEG 作为降序分数等价实现（_top_n 降序）
    inv_peg = {c: (1.0 / v) for c, v in peg_series.items() if v > 0}
    peg_top = _top_n(inv_peg, _PEG_TOP_N)
    counts['D3_peg'] = len(peg_top)

    # ---- 交叉验证：>=2 维度同时选中（list 成员判定 + sorted 保序，禁 set 决策依赖）----
    all_c = sorted(dict.fromkeys(sg_top + ms_top + peg_top).keys())
    pool = []
    for c in all_c:
        votes = (1 if c in sg_top else 0) + (1 if c in ms_top else 0) \
            + (1 if c in peg_top else 0)
        if votes >= 2:
            pool.append(c)
    counts['X_pool'] = len(pool)
    if len(pool) == 0:
        return [], counts

    # ---- 小市值：流通市值升序 Top10 ----
    pool.sort(key=lambda c: (fv_map.get(c, float('inf')), c))
    target = pool[:_TARGET_HOLDINGS]
    counts['T_target'] = len(target)

    # ---- 买入禁入：过去20交易日曾收盘涨停 → 不新建买入 ----
    limit_hit = _prev_20d_limit_hit_map(context, target)
    target = [c for c in target if not limit_hit.get(c, False)]
    counts['T_after_limit_ban'] = len(target)
    return target, counts


def handle_data(context, data):
    """执行层：月初调仓（先卖后买）+ 每日 pending-exit 涨停维持检查。"""
    _ensure_runtime_state()
    today = context.current_dt.date()
    ym = _iso_year_month(today)
    is_rebalance_day = (g.last_rebalance_ym is None) or (ym != g.last_rebalance_ym)

    if is_rebalance_day:
        g.last_rebalance_ym = ym
        g.rebalance_seq += 1
        rebalance_id = 'rb_%04d_%s' % (g.rebalance_seq,
                                       context.current_dt.strftime('%Y%m%d'))
        g.last_audit_rebalance_id = rebalance_id
        _run_rebalance(context, data, rebalance_id)

    _process_pending_exits(context, data)


def _run_rebalance(context, data, rebalance_id):
    """月频调仓：筛选 → 目标清单 → 先卖后买（close 模式同批撮合，卖出资金当期可用）。"""
    date_str = context.current_dt.strftime('%Y-%m-%d')
    target, counts = _select_targets(context)
    log.info('QS_FUNNEL_AUDIT rebalance_id=%s date=%s %s'
             % (rebalance_id, date_str,
                ' '.join('%s=%d' % (k, v) for k, v in sorted(counts.items()))))

    # ---- 真实持仓快照（过滤引擎累积的零持仓陈旧条目；只对 amount>0 的持仓操作）----
    positions_before = []
    for code in sorted(context.portfolio.positions.keys()):
        if _pos_amount(context.portfolio.positions[code]) > 0:
            positions_before.append(code)
    g.target_list = list(target)
    g.pending_exit = []

    # ---- 卖出：不在目标清单的持仓（涨停/跌停锁定 → pending-exit 顺延）----
    sell_submitted = 0
    deferred = 0
    for code in sorted(positions_before):
        if code in target:
            continue
        bar = data[code]
        if bar.close <= 0 or bar.volume <= 0:
            g.pending_exit.append(code)  # 停牌/无 bar → 顺延
            deferred += 1
            continue
        if bar.low_limit > 0 and bar.close <= bar.low_limit + _LIMIT_TOL:
            g.pending_exit.append(code)  # 跌停卖不出（现实模拟）→ 顺延
            deferred += 1
            continue
        if bar.high_limit > 0 and bar.close >= bar.high_limit - _LIMIT_TOL:
            g.pending_exit.append(code)  # 涨停维持 → 当日不卖（风控1）
            deferred += 1
            continue
        order_target_value(code, 0)
        sell_submitted += 1

    # ---- 买入：目标清单中未持仓的（涨停买入跳过 → F-3）----
    buy_submitted = 0
    buys = [c for c in target if c not in positions_before]
    remaining = len(buys)
    for code in buys:
        remaining -= 1
        bar = data[code]
        if bar.close <= 0 or bar.volume <= 0:
            continue  # 停牌/无 bar 不下单
        if bar.high_limit > 0 and bar.close >= bar.high_limit - _LIMIT_TOL:
            continue  # F-3：收盘涨停跳过买入（涨停买不进）
        tv = context.portfolio.total_value
        cash = context.portfolio.cash
        target_val = min(tv * _PER_POSITION_WEIGHT, cash / max(remaining, 1))
        if target_val < _BUY_VALUE_FLOOR:
            continue
        if bar.close > 0 and bar.close * 100 > target_val:
            continue  # 一手金额超过目标金额（高价小市值股买不起一手）→ fail-soft 跳过
        order_target_value(code, target_val)
        buy_submitted += 1

    log.info('QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d '
             'sell_submitted=%d buy_submitted=%d'
             % (rebalance_id, date_str, len(target), len(target),
                sell_submitted, buy_submitted))


def _process_pending_exits(context, data):
    """每日：pending-exit 持仓的涨停维持检查（方案A日线近似）。

    收盘封住涨停 → 维持 → 顺延持有；未封住（含平开低走）→ 视为已打开 → 卖出；
    停牌/无成交 → 顺延。跌停无法卖出 → 顺延。
    """
    if not g.pending_exit:
        return
    still_pending = []
    for code in list(g.pending_exit):
        if code not in context.portfolio.positions:
            continue  # 已不在持仓（已成交）→ 移除
        if _pos_amount(context.portfolio.positions[code]) <= 0:
            continue  # 零持仓 → 移除
        bar = data[code]
        if bar.close <= 0 or bar.volume <= 0:
            still_pending.append(code)  # 停牌 → 顺延
            continue
        if bar.high_limit > 0 and bar.close >= bar.high_limit - _LIMIT_TOL:
            still_pending.append(code)  # 涨停维持 → 顺延持有（风控1）
            continue
        if bar.low_limit > 0 and bar.close <= bar.low_limit + _LIMIT_TOL:
            still_pending.append(code)  # 跌停 → 卖不出 → 顺延
            continue
        order_target_value(code, 0)  # 涨停已打开 → 卖出
    g.pending_exit = still_pending


def _pos_amount(pos):
    """持仓数量兼容读取（PTrade 包装 Position.amount / 引擎原生 Position.volume）。"""
    if pos is None:
        return 0
    return int(getattr(pos, 'amount', None) or getattr(pos, 'volume', 0) or 0)


def after_trading_end(context, data):
    """盘后：记录持仓/现金/敞口（与当日调仓的 rebalance_id 一对一审计）。

    市值直接读 context.portfolio.market_value（框架 Portfolio 活属性，单一真源），
    持仓计数兼容 PTrade 包装(.amount)与引擎原生(.volume)两种形态。
    """
    _ensure_runtime_state()
    if g.last_audit_rebalance_id is None:
        return
    date_str = context.current_dt.strftime('%Y-%m-%d')
    positions = context.portfolio.positions
    tv = context.portfolio.total_value
    cash = context.portfolio.cash
    n_pos = 0
    for code in positions.keys():
        if _pos_amount(positions[code]) > 0:
            n_pos += 1
    market_value = context.portfolio.market_value
    cash_ratio = (cash / tv) if tv and tv > 0 else 1.0
    gross = (market_value / tv) if tv and tv > 0 else 0.0
    log.info('QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d '
             'cash_ratio=%.4f gross_exposure=%.4f'
             % (g.last_audit_rebalance_id, date_str, n_pos, cash_ratio, gross))
    g.last_audit_rebalance_id = None
