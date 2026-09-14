# -*- coding: utf-8 -*-
"""股息防守小市值五日轮动 —— QuantStudio 本地策略（R3 生成，2026-09-12）

设计契约：agent_workspace/dividend_defense_smallcap_5d/agent_strategy_design.json（design 2.3，schema VALID，R2.5 已关闭）
目标平台：quantstudio（本地单目标）
PTrade 转换：不在本策略范围（由 PyQt "转 PTrade" tab / qs-compile import 承接）

策略规则（严格来自客户提示词，零改动、零新增因子）：
  前置过滤池：非ST ∧ 上市满1年 ∧ 未停牌 ∧ 流通市值>5亿 ∧ 净利润为正 ∧ 股息率>0 ∧ 近三年净利润复合增速>15%
              ∧ 剔除科创板(688/689)与北交所(43/83/87/920)；创业板保留
  分时段选股：调仓日所在自然月 ∈ {1,4} → 防守模式（股息率降序 Top10）；其余月份 → 进攻模式（流通市值升序 Top10）
  二次筛选：Top10 按近 20 个交易日换手率标准差升序保留最小 5 只
  持仓调仓：5 只等权（runtime_total_value，单只 = 运行时总资产 × (1-0.03) ÷ N）；每 5 个交易日调仓一次
  基准：沪深300（000300.SS）

实现纪律（R2.5 放行的 8 条）：
  1) _ensure_runtime_state() 幂等守卫为每个回调首语句
  2) 信号/过滤/排序全 T-1 PIT；T 日仅执行；get_history 字面 fq='pre' + include=False；后缀 .SS/.SZ
  3) 持仓读取六字段白名单 + 按 code 索引对齐；禁 .value、禁按位置取值、禁一切依赖哈希迭代序的容器写法
  4) 股息聚合游标增量 + 事件列表缓存（不跨运行持久化、不依赖哈希序）
  5) 参数冻结零寻优
  6) 执行层三项仅执行层（涨停不买不递补 / 跌停顺延 / 换出遇停牌保留卖出尝试）
  7) 每期调仓输出 QS_REBALANCE_AUDIT + QS_PORTFOLIO_AUDIT；候选 <5 时 note 标 A-9 合法原因
  8) R5 预备：窗口与基准按设计；墙钟预声明见 R5 运行说明

实现选择（设计 implementation_notes）：
  * 价格读取不使用 get_history(is_dict=True)——实测 multi-code is_dict=False 无 code 列且 index 恒为 [-1]，
    故改为逐标的 is_dict=False（返回普通 DataFrame），从源头免除 rule 17 的 _extract_history_field 义务。
  * 「股息率>0」过滤不需要价格（Σ bonus_ps > 0 ⟺ 股息率 > 0，分母 close 恒正）；
    过滤条件为合取，故把最贵的股息扫描放在全部谓词之后（结果池不变，见设计 design_choice_standardization）。
  * 涨跌停由引擎原生阻断（is_price_limit_blocked → limit_up_blocked / limit_down_blocked）；
    回测源禁止调用 check_limit（注册表 PTRADE-PLATFORM-FALLBACK-BAN 语义），策略只提交订单并列账。
"""
import datetime

import numpy as np

# ---------------------------------------------------------------- 冻结参数
CAGR_MIN = 0.15                 # 近三年归母净利润复合增速下限（客户提示词）
FLOAT_VALUE_MIN = 50000.0       # 流通市值下限，单位万元（= 5 亿；float_value 单位实测为万元）
TOPN_MODE = 10                  # 分时段选股 Top10（客户提示词）
TOPN_FINAL = 5                  # 二次筛选保留 5 只（客户提示词）
CADENCE = 5                     # 每 5 个交易日调仓（客户提示词）
BUFFER_PCT = 0.03               # 换仓缓冲带（R0 07 客户裁定）
TURNOVER_WINDOW = 20            # 近 20 个交易日换手率（客户提示词）
DIVIDEND_LOOKBACK_DAYS = 365    # 股息率近 12 个月（R0 乙项口径）
LISTING_MIN_DAYS = 365          # 上市满 1 年（客户提示词）
COMMISSION = 0.0003             # 双边万三（R0 09 客户采纳）
SLIPPAGE = 0.001                # 比例滑点（R0 09 客户采纳）
BENCHMARK = "000300.SS"         # 沪深300（客户提示词）
DEFENSIVE_MONTHS = (1, 4)       # 防守模式月份（客户提示词）
EXCLUDED_PREFIXES = ("688", "689", "43", "83", "87", "920")   # 科创板 + 北交所（客户提示词）

STATE_FLAG = "_dividend_defense_state_ready"


# ---------------------------------------------------------------- 基础工具
def _ensure_runtime_state():
    """幂等运行状态守卫（每个回调首语句调用；逐属性守卫，重复调用不重置）。"""
    if not hasattr(g, "day_index"):
        g.day_index = 0          # 交易日序号（首个交易日 = 1，即第 0 个调仓日）
    if not hasattr(g, "rebalance_seq"):
        g.rebalance_seq = 0
    if not hasattr(g, "div_cursor"):
        g.div_cursor = {}        # bare -> 已扫描到的 api_date（含），增量游标
    if not hasattr(g, "div_events"):
        g.div_events = {}        # bare -> [(ex_date_api_str, bonus_ps), ...] 事件列表缓存
    if not hasattr(g, "pending_exits"):
        g.pending_exits = []     # 待卖出（跌停/停牌顺延）的裸码，确定性列表
    if not hasattr(g, "cal_cache"):
        g.cal_cache = {}         # 微轮B：交易日历 memoize（键 = (start_date, end_date) 参数元组原值）
    if not hasattr(g, "listed_cache"):
        g.listed_cache = {}      # 批1-① 上市日会话级缓存：listed_date 不随时间变化，仅需取一次


def _date_text(value):
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])
    return s[:10]


def _api_date(value):
    return _date_text(value).replace("-", "")


def _date_obj(value):
    return datetime.datetime.strptime(_date_text(value), "%Y-%m-%d")


def _bare(code):
    return str(code).split(".")[0]


def _suffix_by_prefix(bare):
    if bare[:3] in ("688", "689") or bare[:1] in ("5", "6", "9"):
        return ".SS"
    if bare[:1] in ("0", "1", "2", "3"):
        return ".SZ"
    if bare[:1] in ("4", "8"):
        return ".BJ"
    return ".SS"


def _portable(code):
    """归一为 .SS/.SZ/.BJ（键精确匹配语义）。"""
    s = str(code).strip().upper()
    if "." in s:
        bare, suf = s.split(".", 1)
        if suf in ("SH", "SS", "XSHG"):
            return bare + ".SS"
        if suf in ("SZ", "XSHE"):
            return bare + ".SZ"
        if suf in ("BJ", "XBJ", "XBSE"):
            return bare + ".BJ"
        return bare + _suffix_by_prefix(bare)
    return s + _suffix_by_prefix(s)


def _bj_ms(value):
    """毫秒时间戳 → 北京时区 naive datetime（数据层按北京 00:00 存储）。"""
    try:
        return datetime.datetime.utcfromtimestamp(int(value) / 1000.0) + datetime.timedelta(hours=8)
    except Exception:
        return None


def _finite(value, default=None):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if np.isfinite(f) else default


def _mapping_true(mapping, code):
    """键精确匹配读取（.SS/.SZ 归一），不做 alias 感知。"""
    if not isinstance(mapping, dict):
        return False
    for key in (code, _bare(code)):
        if key in mapping:
            return bool(mapping[key])
    return False


# ---------------------------------------------------------------- 持仓（六字段白名单）
def _position_amount(position):
    """持仓数量：白名单字段 amount（禁用 volume / .value）。"""
    return int(_finite(getattr(position, "amount", 0), 0.0) or 0)


def _position_enable(position):
    """可卖量：白名单字段 enable_amount（含 T+1 语义）。"""
    return int(_finite(getattr(position, "enable_amount", 0), 0.0) or 0)


def _held_codes(context):
    """持仓裸码列表（确定性：仅用 list + sorted，禁 set/frozenset 哈希序）。"""
    positions = getattr(context.portfolio, "positions", None) or {}
    held = []
    try:
        items = list(positions.items())
    except AttributeError:
        return held
    for code, position in items:
        bare = _bare(code)
        if _position_amount(position) > 0 and bare not in held:
            held.append(bare)
    return sorted(held)


def _portfolio_total_value(context):
    """运行时组合总资产（sizing_mode=runtime_total_value 的唯一取数入口）。"""
    for field in ("portfolio_value", "total_value", "total_asset"):
        value = _finite(getattr(context.portfolio, field, None), None)
        if value is not None and value > 0:
            return float(value)
    cash = _finite(getattr(context.portfolio, "cash", 0.0), 0.0) or 0.0
    market = _finite(getattr(context.portfolio, "market_value", 0.0), 0.0) or 0.0
    return float(cash + market)


def _position_market_value(position):
    direct = _finite(getattr(position, "market_value", None), None)
    if direct is not None:
        return direct
    amount = _finite(getattr(position, "amount", 0), 0.0) or 0.0
    price = _finite(getattr(position, "last_sale_price", 0), 0.0) or 0.0
    return amount * price


# ---------------------------------------------------------------- T-1 价格（逐标的 is_dict=False）
def _t1_prev_close(code):
    """T-1 前复权收盘价（include=False 锚定上一交易日）。返回 None 表示取数失败。"""
    try:
        frame = get_history(_portable(code), count=1, unit="1d", fields=["close"],
                            fq="pre", include=False, is_dict=False)
    except Exception as exc:
        log.info("T1 close failed %s: %s" % (code, exc))
        return None
    if frame is None or len(frame) == 0:
        return None
    try:
        values = [float(v) for v in list(frame["close"])]
    except Exception:
        return None
    for v in reversed(values):
        if np.isfinite(v) and v > 0:
            return v
    return None


# ---------------------------------------------------------------- 股息聚合（游标增量 + 事件缓存）
def _scan_dividends(bare, start_api, end_api):
    """增量扫描 [start_api, end_api] 内的除权除息事件，追加进事件列表缓存。

    契约（设计 A-7）：游标增量结果必须与全窗口逐日扫描逐值相等；
    缓存不跨回测运行持久化；迭代一律 list + sorted，绝不依赖哈希迭代序。
    """
    cursor = g.div_cursor.get(bare)
    if cursor is not None and cursor >= end_api:
        return
    days = []
    try:
        # 微轮B memoize：键 = (start_date, end_date) 参数元组原值，不做任何日期归一化简化。
        # 前提：结果随参数恒等（回测内交易日历静态）；即便未来出现含未来日期的区间，按键原值缓存同样安全。
        _cal_key = (start_api, end_api)
        if _cal_key in g.cal_cache:
            raw_days = g.cal_cache[_cal_key]
        else:
            raw_days = get_trade_days(start_date=start_api, end_date=end_api)
            g.cal_cache[_cal_key] = raw_days
        days = [_api_date(d) for d in raw_days]
    except Exception as exc:
        log.info("trade days failed for dividend scan %s: %s" % (bare, exc))
        return
    if not days:
        return
    events = g.div_events.setdefault(bare, [])
    for day in days:
        if cursor is not None and day <= cursor:
            continue
        try:
            frame = get_stock_exrights(_portable(bare), day)
        except Exception:
            frame = None
        if frame is not None and len(frame) > 0:
            for _, row in frame.iterrows():
                bonus = _finite(row.get("bonus_ps"), None)
                if bonus is not None and bonus > 0:
                    events.append((day, float(bonus)))
    g.div_cursor[bare] = end_api
    events.sort(key=lambda item: (item[0], item[1]))


def _dividend_sum_12m(bare, asof_api):
    """Σ bonus_ps（ex_date ∈ (asof-365天, asof]）。"""
    start_dt = _date_obj(asof_api) - datetime.timedelta(days=DIVIDEND_LOOKBACK_DAYS)
    start_api = start_dt.strftime("%Y%m%d")
    _scan_dividends(bare, start_api, asof_api)
    total = 0.0
    for ex_api, bonus in g.div_events.get(bare, []):
        if start_api < ex_api <= asof_api:
            total += bonus
    return total


# ---------------------------------------------------------------- 前置过滤池（T-1 PIT）
def _build_filter_pool(prev_api):
    """按客户提示词的合取条件构建过滤池；返回 {bare: 基础信息}。

    过滤顺序：剔除板块 → 非ST → 未停牌 → 上市满1年 → 流通市值>5亿 → 净利润为正 → 三年CAGR>15%
    （合取条件，顺序不影响结果池；把最贵的股息扫描留到排序阶段）。
    """
    stage = []
    try:
        all_codes = list(get_Ashares(prev_api) or [])
    except Exception as exc:
        log.info("get_Ashares failed: %s" % exc)
        return {}
    for code in all_codes:
        port = _portable(code)
        if port[:3] in EXCLUDED_PREFIXES or _bare(port)[:3] in EXCLUDED_PREFIXES:
            continue
        stage.append(port)
    if not stage:
        return {}

    for status_type in ("ST", "HALT"):
        try:
            result = get_stock_status(stage, query_type=status_type, query_date=prev_api)
        except Exception as exc:
            log.info("get_stock_status %s failed: %s" % (status_type, exc))
            result = {}
        stage = [c for c in stage if not _mapping_true(result, c)]
    if not stage:
        return {}

    # 批1-① 上市日会话级缓存：只对未缓存标的发起查询（listed_date 不随时间变化）
    listed = g.listed_cache
    missing_listed = [c for c in stage if _bare(c) not in listed]
    if missing_listed:
        try:
            info = get_stock_info(missing_listed, field=["listed_date"]) or {}
            for code, record in info.items():
                listed[_bare(code)] = (record or {}).get("listed_date")
        except Exception as exc:
            log.info("get_stock_info failed: %s" % exc)
    prev_dt = _date_obj(prev_api)
    aged = []
    for code in stage:
        ld = listed.get(_bare(code))
        if not ld:
            continue
        try:
            if (prev_dt - _date_obj(ld)).days >= LISTING_MIN_DAYS:
                aged.append(code)
        except Exception:
            continue
    stage = aged
    if not stage:
        return {}

    float_value = {}
    try:
        val = get_fundamentals(stage, "valuation", fields=["float_value"], date=prev_api)
        if val is not None and len(val) > 0:
            for code, row in val.iterrows():
                fv = _finite(row.get("float_value"), None)
                if fv is not None and fv > FLOAT_VALUE_MIN:
                    float_value[_bare(code)] = float(fv)
    except Exception as exc:
        log.info("valuation failed: %s" % exc)
    stage = [c for c in stage if _bare(c) in float_value]
    if not stage:
        return {}

    np_map = _load_annual_np(stage, prev_api)
    prev_year = prev_dt.year
    passing = []
    for code in stage:
        series = np_map.get(_bare(code)) or {}
        latest = max(series) if series else None
        if latest is None:
            continue
        np_t = series.get(latest)
        if np_t is None or np_t <= 0:
            continue
        np_t3 = series.get(latest - 3)
        if np_t3 is None or np_t3 <= 0:
            continue
        cagr = (np_t / np_t3) ** (1.0 / 3.0) - 1.0
        if cagr > CAGR_MIN:
            passing.append(code)
    stage = passing
    if not stage:
        return {}

    pool = {}
    for code in stage:
        pool[_bare(code)] = {
            "code": code,
            "float_value": float_value[_bare(code)],
        }
    return pool


def _load_annual_np(stage, prev_api):
    """PIT 可见的年度归母净利润：{bare: {year: np}}。"""
    out = {}
    end_year = _date_obj(prev_api).year
    try:
        frame = get_fundamentals(stage, "income_statement",
                                 fields=["end_date", "np_parent_company_owners"],
                                 date=prev_api, start_year=end_year - 6, end_year=end_year)
    except Exception as exc:
        log.info("income_statement failed: %s" % exc)
        return out
    if frame is None or len(frame) == 0:
        return out
    for code, row in frame.iterrows():
        end_dt = _bj_ms(row.get("end_date"))
        if end_dt is None or end_dt.month != 12:
            continue
        value = _finite(row.get("np_parent_company_owners"), None)
        if value is None:
            continue
        out.setdefault(_bare(code), {})[end_dt.year] = value
    return out


# ---------------------------------------------------------------- 分时段排序 + 二次筛选
def _rank_pool(pool, month, prev_api):
    """返回 Top10（按模式排序）。防守模式需要股息率，故此处才做股息扫描与取价。"""
    rows = []
    if month in DEFENSIVE_MONTHS:
        for bare, info in pool.items():
            total_bonus = _dividend_sum_12m(bare, prev_api)
            if total_bonus <= 0.0:          # 「股息率 > 0」过滤（分母恒正，等价于 Σbonus > 0）
                continue
            close = _t1_prev_close(bare)
            if close is None or close <= 0:
                continue
            rows.append((total_bonus / close, info["float_value"], bare))
        rows.sort(key=lambda item: (-item[0], item[2]))
    else:
        for bare, info in pool.items():
            if _dividend_sum_12m(bare, prev_api) <= 0.0:   # 过滤阶段仍需 股息率>0
                continue
            rows.append((info["float_value"], bare))
        rows.sort(key=lambda item: (item[0], item[1]))
    mode = "defensive" if month in DEFENSIVE_MONTHS else "attack"
    return mode, [item[-1] for item in rows[:TOPN_MODE]]


def _secondary_screen(candidates, prev_api):
    """近 20 个交易日换手率标准差升序取最小 5 只；不足则按实际数量返回。"""
    days = []
    try:
        raw = get_trade_days(end_date=prev_api, count=TURNOVER_WINDOW + 1)
        days = [_api_date(d) for d in list(raw) if _api_date(d) < prev_api]
    except Exception as exc:
        log.info("turnover窗口取交易日失败: %s" % exc)
        return []
    if len(days) < TURNOVER_WINDOW:
        return []
    days = days[-TURNOVER_WINDOW:]
    series = {}
    for day in days:
        try:
            frame = get_fundamentals(candidates, "valuation", fields=["turnover_ratio"], date=day)
        except Exception as exc:
            log.info("turnover date=%s failed: %s" % (day, exc))
            frame = None
        if frame is None or len(frame) == 0:
            continue
        for code, row in frame.iterrows():
            value = _finite(row.get("turnover_ratio"), None)
            if value is None:
                continue
            series.setdefault(_bare(code), []).append(float(value))
    scored = []
    for bare in candidates:
        values = series.get(bare) or []
        if len(values) < TURNOVER_WINDOW:
            continue
        scored.append((float(np.std(np.asarray(values, dtype=float))), bare))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [bare for _, bare in scored[:TOPN_FINAL]]


# ---------------------------------------------------------------- 执行层
def _retry_pending_exits(context):
    """跌停/停牌顺延：每个交易日重试未完成的卖出（仅执行层）。"""
    pending = []
    for bare in g.pending_exits:
        code = _portable(bare)
        position = get_position(code)
        if _position_amount(position) <= 0:
            continue
        if _position_enable(position) <= 0:
            pending.append(bare)
            continue
        order_target_value(code, 0)
        pending.append(bare)
    g.pending_exits = sorted(dict.fromkeys(pending))


def _execute_rebalance(context, today, mode, target_bares, candidates_before_screen):
    g.rebalance_seq += 1
    rid = "%s-%03d" % (today.replace("-", ""), g.rebalance_seq)
    held = _held_codes(context)
    target_codes = [_portable(b) for b in target_bares]

    sell_submitted = 0
    for bare in sorted(held):
        if bare not in target_bares:
            order_target_value(_portable(bare), 0)
            sell_submitted += 1
            if bare not in g.pending_exits:
                g.pending_exits.append(bare)

    n = len(target_codes)
    if n <= 0:
        _audit(context, rid, today, 0, 0, sell_submitted, 0,
               "no_candidate_after_screen")
        _retry_pending_exits(context)
        return

    total_value = _portfolio_total_value(context)
    per_target = total_value * (1.0 - BUFFER_PCT) / n
    # 执行层边界（X6）：回测源禁止调用 check_limit（trading-context API）；
    # 涨停不买 / 跌停不卖由引擎订单层按 is_price_limit_blocked 原生阻断
    # （reason=limit_up_blocked / limit_down_blocked），策略只提交订单并列账，
    # 被阻断者自然保留现金（不递补），卖出被阻断者进入 pending_exits 逐日顺延重试。
    buy_submitted = 0
    for code in target_codes:
        order_target_value(code, per_target)
        buy_submitted += 1

    notes = []
    if candidates_before_screen < TOPN_FINAL:
        notes.append("candidates_below_5_legal(A-9):%d" % candidates_before_screen)
    if n < TOPN_FINAL:
        notes.append("target_below_5_legal(A-9):%d" % n)
    if len(g.pending_exits) > 0:
        notes.append("exit_deferred:%d" % len(g.pending_exits))
    _audit(context, rid, today, n, len(target_codes), sell_submitted, buy_submitted,
           ",".join(notes) if notes else "mode_%s" % mode)
    _retry_pending_exits(context)


def _audit(context, rid, date_str, selected, tradable, sell_submitted, buy_submitted, note):
    log.info("QS_REBALANCE_AUDIT rebalance_id=%s date=%s selected=%d tradable=%d "
             "sell_submitted=%d buy_submitted=%d note=%s"
             % (rid, date_str, selected, tradable, sell_submitted, buy_submitted, note))
    try:
        positions = get_positions() or {}
    except Exception:
        positions = getattr(context.portfolio, "positions", None) or {}
    held = _held_codes(context)
    total_value = _portfolio_total_value(context)
    cash = _finite(getattr(context.portfolio, "cash", 0.0), 0.0) or 0.0
    market_value = 0.0
    try:
        for _, position in list(positions.items()):
            market_value += _position_market_value(position)
    except AttributeError:
        market_value = 0.0
    cash_ratio = (cash / total_value) if total_value > 0 else 0.0
    gross = (market_value / total_value) if total_value > 0 else 0.0
    log.info("QS_PORTFOLIO_AUDIT rebalance_id=%s date=%s positions=%d cash_ratio=%.4f "
             "gross_exposure=%.4f" % (rid, date_str, len(held), cash_ratio, gross))


# ---------------------------------------------------------------- 生命周期
def initialize(context):
    _ensure_runtime_state()
    set_benchmark(BENCHMARK)
    set_commission(commission_ratio=COMMISSION, min_commission=5.0)
    set_slippage(SLIPPAGE)
    log.info("股息防守小市值五日轮动: init | 参数冻结 CAGR=%.2f 市值下限=%.0f万 Top10/Top5 调仓=%d日 buffer=%.2f"
             % (CAGR_MIN, FLOAT_VALUE_MIN, CADENCE, BUFFER_PCT))


def handle_data(context, data):
    _ensure_runtime_state()
    g.day_index += 1
    if g.day_index > 1 and (g.day_index - 1) % CADENCE != 0:
        _retry_pending_exits(context)
        return
    today = _date_text(context.current_dt)
    prev_api = _api_date(context.previous_date)
    month = _date_obj(today).month

    pool = _build_filter_pool(prev_api)
    mode, top10 = _rank_pool(pool, month, prev_api)
    final = _secondary_screen(top10, prev_api) if top10 else []
    _execute_rebalance(context, today, mode, final, len(top10))


def after_trading_end(context, data):
    _ensure_runtime_state()
    held = _held_codes(context)
    kept = [b for b in held if b in g.pending_exits]
    g.pending_exits = sorted(dict.fromkeys(kept))
    log.info("Post-close %s: held=%s pending_exits=%s div_cache=%d"
             % (_date_text(context.current_dt), held, g.pending_exits, len(g.div_events)))
