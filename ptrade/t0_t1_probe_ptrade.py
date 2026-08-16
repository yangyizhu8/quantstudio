# -*- coding: utf-8 -*-
"""
PTrade T+0/T+1 交易制度实证探针 v2（平台验证专用，非可部署策略）
================================================================
v2 修复（首轮 2026-08-15 实测发现的 bug）：
  1. 第二轮不再重买 → 每轮独立清空 bought，买入/卖出/判定全部按轮次隔离；
  2. 14:50 把"没持仓"误标 FILLED → 新增 ever_held 跟踪（10:30 实际持仓>0 才算持有），
     未持有 → NO_POS → 汇总判 UNKNOWN；
  3. 10:05 补买：09:35 买入未成交（如 513100 首轮 bar 数据量为 0）自动补一次；
  4. 总结日判定用 == 而非 >=（首轮 Day5/Day6 重复打印）。

首轮已证实的平台行为（2026-08-15 实测）：
  - equity 5 只：T+1（当日卖拒单，次日可卖）✅
  - qdii 6 只（513500/513520/513030/159920/513050/513180）+ gold + commodity + bond + money：T+0 ✅
  - 520830 沙特（qdii）与 3 只 LOF（161226/501018/162411）：平台按 T+1 ⚠️ 平台偏差，真实规则为 T+0
  - 513100：PTrade 回测 09:35 bar 数据量为 0，买入未成交 ⚠️（v2 已加 10:05 补买）

回测设置（PTrade 平台 GUI 里配置，代码不含 set_backtest）：
  - 初始资金：300000
  - 频率：【1 分钟】（必须，否则时间点不命中）
  - 回测区间：至少 5 个连续交易日（建议 6 天）

协议（两轮独立验证 + 总结日）：
  Day1/3：09:35 买入 → 10:05 未成交补买 → 10:30 核对持仓+可卖字段 → 14:30 当日卖出尝试 → 14:50 核对
  Day2/4：10:00 清理卖出剩余持仓（验证次日可卖）→ 10:30 核对
  Day5 收盘：逐标的汇总判定（优先用第一轮证据）

回报方式：把回测日志中所有 [T0PROBE] 行发回即可。同一文件也可贴入【模拟盘】验证（首日即 Day1）。
本文件为一次性平台探针，跑完即弃，严禁当作策略部署。
================================================================
"""

# ---------------------------------------------------------------
# 探针标的清单（模块级纯常量；fund_type 来源：本地 etf_basic 8/13 库实测）
# ---------------------------------------------------------------
TEST_PLAN = [
    # ---------------- equity：期望 T+1 ----------------
    ('510300.SS', 'equity', False, '华泰柏瑞沪深300ETF'),
    ('510500.SS', 'equity', False, '南方中证500ETF'),
    ('159915.SZ', 'equity', False, '易方达创业板ETF'),
    ('512480.SS', 'equity', False, '国联安半导体ETF'),
    ('159995.SZ', 'equity', False, '华夏芯片ETF'),
    # ---------------- qdii：期望 T+0 ----------------
    ('513100.SS', 'qdii', True, '国泰纳指100ETF'),
    ('513500.SS', 'qdii', True, '博时标普500ETF'),
    ('513520.SS', 'qdii', True, '华夏野村日经225ETF'),
    ('513030.SS', 'qdii', True, '华安德国DAX ETF'),
    ('159920.SZ', 'qdii', True, '华夏恒生ETF'),
    ('513050.SS', 'qdii', True, '易方达中概互联ETF'),
    ('513180.SS', 'qdii', True, '华夏恒生科技ETF'),
    ('520830.SS', 'qdii', True, '华泰柏瑞南方东英沙特ETF'),
    # ---------------- gold：期望 T+0 ----------------
    ('518880.SS', 'gold', True, '华安黄金ETF'),
    ('518800.SS', 'gold', True, '国泰黄金ETF'),
    # ---------------- commodity：期望 T+0 ----------------
    ('159985.SZ', 'commodity', True, '华夏饲料豆粕期货ETF'),
    ('159980.SZ', 'commodity', True, '大成有色金属期货ETF'),
    # ---------------- bond：期望 T+0 ----------------
    ('511010.SS', 'bond', True, '国泰上证5年期国债ETF'),
    ('511260.SS', 'bond', True, '国泰上证10年期国债ETF'),
    # ---------------- money：期望 T+0 ----------------
    ('511990.SS', 'money', True, '华宝添益货币ETF'),
    ('511880.SS', 'money', True, '银华日利货币ETF'),
    # ---------------- LOF 边界探针：真实规则期望 T+0（首轮实测平台按 T+1） ----------------
    ('161226.SZ', 'lof', True, '国投瑞银白银期货LOF'),
    ('501018.SS', 'lof', True, '南方原油LOF'),
    ('162411.SZ', 'lof', True, '华宝油气LOF'),
]

ROUND_DAYS = [1, 3]      # 买入日（两轮）
CLEANUP_DAYS = [2, 4]    # 清理日
SUMMARY_DAY = 5          # 总结日


def initialize(context):
    g.test_plan = list(TEST_PLAN)
    g.round_days = list(ROUND_DAYS)
    g.cleanup_days = list(CLEANUP_DAYS)
    g.summary_day = SUMMARY_DAY
    g.day_count = 0
    g.round_no = 0
    g.bought = {}                # code -> round_no（当轮有效）
    g.round_bought = {}          # round_no -> set(code)（该轮下过买单的标的）
    g.ever_held = {}             # round_no -> set(code)（该轮 10:30 实际持仓>0）
    g.sold_today = set()
    g.checked_1030 = set()
    g.rebought_1005 = set()
    g.same_day_result = {1: {}, 2: {}}   # round_no -> {code: FILLED/REJECTED/PENDING}
    g.cleanup_result = {1: {}, 2: {}}    # round_no -> {code: FILLED/NO_POS/REJECTED}
    log.info('[T0PROBE] === 初始化：探针标的 %d 只（v2 修复版） ===' % len(g.test_plan))
    for code, ftype, expect, note in g.test_plan:
        log.info('[T0PROBE] plan code=%s fund_type=%s expect_t0=%s %s' % (code, ftype, expect, note))


def before_trading_start(context, data):
    g.day_count += 1
    g.sold_today = set()
    g.checked_1030 = set()
    g.rebought_1005 = set()
    if g.day_count in g.round_days:
        g.round_no = g.round_days.index(g.day_count) + 1
        g.bought = {}            # 每轮独立：清空上轮持仓记录
        g.round_bought[g.round_no] = set()
        g.ever_held[g.round_no] = set()
    log.info('=' * 70)
    log.info('[T0PROBE] === Day %d (round=%s) ===' % (g.day_count, g.round_no or '-'))


def handle_data(context, data):
    t = context.current_dt
    d = g.day_count
    hhmm = '%02d:%02d' % (t.hour, t.minute)
    r = g.round_no

    # ---------- 买入日 09:35：买入本轮回合未持有标的各 100 股 ----------
    if d in g.round_days and hhmm == '09:35':
        for code, ftype, expect, note in g.test_plan:
            if code in g.bought:
                continue
            try:
                oid = order(code, 100)
            except Exception as e:
                oid = None
                log.info('[T0PROBE] buy_exc code=%s err=%s' % (code, str(e)))
            if _oid_ok(oid):
                g.bought[code] = r
                g.round_bought[r].add(code)
                log.info('[T0PROBE] buy_ok code=%s round=%d expect_t0=%s oid=%s %s' % (code, r, expect, oid, note))
            else:
                log.info('[T0PROBE] buy_reject code=%s round=%d oid=%s %s（买入未受理）' % (code, r, oid, note))

    # ---------- 买入日 10:05：09:35 买入未成交的补买一次（如 513100 数据缺口） ----------
    if d in g.round_days and hhmm == '10:05':
        for code, ftype, expect, note in g.test_plan:
            if code not in g.bought or code in g.rebought_1005:
                continue
            pos = _get_pos(code, context)
            amt = _attr_number(pos, ('amount', 'total_amount'), 0)
            if amt > 0:
                continue
            g.rebought_1005.add(code)
            try:
                oid = order(code, 100)
            except Exception as e:
                oid = None
                log.info('[T0PROBE] rebuy_exc code=%s err=%s' % (code, str(e)))
            log.info('[T0PROBE] rebuy_try code=%s round=%d oid=%s（09:35未成交补买）' % (code, r, oid))

    # ---------- 买入日 10:30：核对持仓 + 平台可卖字段 ----------
    if d in g.round_days and hhmm == '10:30':
        for code, ftype, expect, note in g.test_plan:
            if code not in g.bought or code in g.checked_1030:
                continue
            g.checked_1030.add(code)
            pos = _get_pos(code, context)
            amt = _attr_number(pos, ('amount', 'total_amount'), 0)
            if amt > 0:
                g.ever_held[r].add(code)
            sellable = _attr_number(pos, ('enable_amount', 'closeable_amount', 'can_sell'), None)
            log.info('[T0PROBE] hold_check code=%s amount=%s platform_sellable=%s expect_t0=%s %s'
                     % (code, amt, sellable, expect, note))

    # ---------- 买入日 14:30：尝试当日卖出（仅当轮实际持仓>0 的标的） ----------
    if d in g.round_days and hhmm == '14:30':
        for code, ftype, expect, note in g.test_plan:
            if code not in g.ever_held.get(r, set()) or code in g.sold_today:
                continue
            g.sold_today.add(code)
            try:
                oid = order(code, -100)
            except Exception as e:
                oid = None
                log.info('[T0PROBE] sell_exc code=%s err=%s' % (code, str(e)))
            if _oid_ok(oid):
                log.info('[T0PROBE] same_day_sell_accepted code=%s round=%d expect_t0=%s oid=%s（当日卖出被受理）' % (code, r, expect, oid))
            else:
                g.same_day_result[r][code] = 'REJECTED'
                log.info('[T0PROBE] same_day_sell_rejected code=%s round=%d expect_t0=%s oid=%s（order返回None/-1=拒单，当日卖不出）' % (code, r, expect, oid))
            try:
                op = get_open_orders(security=code)
                log.info('[T0PROBE] open_orders_after_sell code=%s open=%s' % (code, str(op)))
            except Exception as e:
                log.info('[T0PROBE] get_open_orders_exc code=%s err=%s' % (code, str(e)))

    # ---------- 买入日 14:50：核对当日卖出最终结果（仅当轮实际持仓>0 的标的） ----------
    if d in g.round_days and hhmm == '14:50':
        for code, ftype, expect, note in g.test_plan:
            if code not in g.ever_held.get(r, set()):
                if code in g.bought:
                    g.same_day_result[r].setdefault(code, 'NO_POS')
                continue
            pos = _get_pos(code, context)
            amt = _attr_number(pos, ('amount', 'total_amount'), 0)
            if amt <= 0:
                g.same_day_result[r][code] = 'FILLED'
                log.info('[T0PROBE] same_day_sell_filled code=%s round=%d expect_t0=%s（当日卖出成交，持仓清零）' % (code, r, expect))
            elif code not in g.same_day_result[r]:
                g.same_day_result[r][code] = 'PENDING'
                log.info('[T0PROBE] same_day_sell_pending code=%s round=%d expect_t0=%s amount=%s（受理但当日未成交/未卖出）' % (code, r, expect, amt))

    # ---------- 清理日 10:00：卖出全部剩余持仓（验证次日可卖） ----------
    if d in g.cleanup_days and hhmm == '10:00':
        for code, ftype, expect, note in g.test_plan:
            if code not in g.bought or code in g.sold_today:
                continue
            pos = _get_pos(code, context)
            amt = _attr_number(pos, ('amount', 'total_amount'), 0)
            if amt <= 0:
                g.cleanup_result[r].setdefault(code, 'NO_POS')
                continue
            g.sold_today.add(code)
            try:
                oid = order(code, -amt)
            except Exception as e:
                oid = None
                log.info('[T0PROBE] cleanup_exc code=%s err=%s' % (code, str(e)))
            if _oid_ok(oid):
                log.info('[T0PROBE] cleanup_sell_accepted code=%s round=%d amt=%s oid=%s（次日卖出被受理）' % (code, r, amt, oid))
            else:
                g.cleanup_result[r][code] = 'REJECTED'
                log.info('[T0PROBE] cleanup_sell_rejected code=%s round=%d amt=%s oid=%s（次日仍卖不出！）' % (code, r, amt, oid))

    # ---------- 清理日 10:30：核对清理结果 ----------
    if d in g.cleanup_days and hhmm == '10:30':
        for code, ftype, expect, note in g.test_plan:
            if code not in g.bought or code in g.cleanup_result[r]:
                continue
            pos = _get_pos(code, context)
            amt = _attr_number(pos, ('amount', 'total_amount'), 0)
            if amt <= 0:
                g.cleanup_result[r][code] = 'FILLED'
                log.info('[T0PROBE] cleanup_filled code=%s round=%d（次日卖出成交，持仓清零）' % (code, r))


def after_trading_end(context, data):
    log.info('[T0PROBE] === Day %d 收盘持仓 ===' % g.day_count)
    try:
        for sec_key in context.portfolio.positions:
            pos = context.portfolio.positions[sec_key]
            amt = _attr_number(pos, ('amount', 'total_amount'), 0)
            sellable = _attr_number(pos, ('enable_amount', 'closeable_amount', 'can_sell'), None)
            log.info('[T0PROBE] eod_pos code=%s amount=%s platform_sellable=%s' % (sec_key, amt, sellable))
    except Exception as e:
        log.info('[T0PROBE] eod_pos_exc %s' % str(e))

    # ---------- 总结日：汇总判定 ----------
    if g.day_count == g.summary_day:
        _summary()


def _oid_ok(oid):
    """order() 返回值：非 None 且非 -1/'-1' 视为受理成功"""
    if oid is None:
        return False
    if oid == -1 or oid == '-1':
        return False
    return True


def _get_pos(code, context):
    """优先 get_position，失败回退 context.portfolio.positions"""
    try:
        return get_position(code)
    except Exception:
        pass
    try:
        return context.portfolio.positions.get(code)
    except Exception:
        return None


def _attr_number(pos, names, default):
    """防御式读取持仓数值字段（PTrade 回测/实盘字段名有差异）"""
    if pos is None:
        return default
    for n in names:
        try:
            v = getattr(pos, n, None)
        except Exception:
            v = None
        if v is not None:
            try:
                return float(v)
            except Exception:
                return default
    return default


def _round_evidence(r, code):
    """取某轮该标的的当日卖出结果；未产生观察返回 None"""
    return g.same_day_result.get(r, {}).get(code)


def _summary():
    log.info('*' * 70)
    log.info('[T0PROBE] === 汇总判定（每标的优先第一轮证据，第一轮无结论用第二轮） ===')
    n_pass, n_fail, n_dev, n_unknown = 0, 0, 0, 0
    for code, ftype, expect, note in g.test_plan:
        same1 = _round_evidence(1, code)
        same2 = _round_evidence(2, code)
        same = same1 if same1 not in (None, 'NO_POS') else same2
        r_used = 1 if same1 not in (None, 'NO_POS') else 2
        clean1 = g.cleanup_result.get(1, {}).get(code)
        clean2 = g.cleanup_result.get(2, {}).get(code)
        clean = clean1 if clean1 in ('FILLED', 'REJECTED') else clean2
        if same is None or same == 'NO_POS':
            verdict = 'UNKNOWN（买入未成交/无有效观察）'
        elif expect and same == 'FILLED':
            verdict = 'PASS（T+0：当日买当日卖成交）'
        elif not expect and same in ('REJECTED', 'PENDING'):
            verdict = 'PASS（T+1：当日买当日卖被拒/未成交）'
        elif expect and same in ('REJECTED', 'PENDING'):
            verdict = 'PLATFORM_DEV（期望T+0，但平台当日卖失败=平台按T+1处理）'
        elif not expect and same == 'FILLED':
            verdict = 'FAIL（期望T+1，但平台放行当日卖=平台未执行T+1限制）'
        else:
            verdict = 'UNKNOWN（%s）' % same
        if verdict.startswith('PASS'):
            n_pass += 1
        elif verdict.startswith('FAIL'):
            n_fail += 1
        elif verdict.startswith('PLATFORM_DEV'):
            n_dev += 1
        else:
            n_unknown += 1
        log.info('[T0PROBE] code=%s fund_type=%s expect_t0=%s same_day(r%d)=%s same_day(r%d)=%s cleanup=%s verdict=%s %s'
                 % (code, ftype, expect, 1, same1, 2, same2, clean, verdict, note))
    log.info('[T0PROBE] === 汇总：PASS=%d FAIL=%d PLATFORM_DEV=%d UNKNOWN=%d / 共%d只 ==='
             % (n_pass, n_fail, n_dev, n_unknown, len(g.test_plan)))
    log.info('[T0PROBE] 判定说明：FILLED当日卖成交 | REJECTED=order返回None/-1拒单 | PENDING=受理但当日未成交 | NO_POS=买入未成交')
    log.info('[T0PROBE] PLATFORM_DEV=平台行为与真实交易规则不一致（真实规则以交易所为准，建议模拟盘复核）')
    log.info('[T0PROBE] 请同时导出 PTrade【成交明细】CSV 与平台日志交叉核对')
    log.info('*' * 70)
