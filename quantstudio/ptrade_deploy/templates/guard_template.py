# -*- coding: utf-8 -*-
"""稳定性护栏注入模板（对应设计文档 docs/ptrade-deploy-pipeline-design.md §6.3）。

护栏形态：模块级 _deploy_guard_pass 函数（整块带 GUARD 标记、可整体删除），
transformer 在每个下单调用前插入 `if _deploy_guard_pass(...): <原下单语句> else: log.warning`
检查块——策略原语句零改动（§5.2 P3 硬约束）。

平台事实依据（设计文档 §1.2）：
- F6：check_limit 真实平台返回 {1,-1,2,-2,0}（1/2 涨停、-1/-2 跌停）；
  本地镜像 ptrade_api.py 返回 dict —— 模板做双形态容忍；
- get_stock_status 签名与本地镜像对齐（query_type：ST/HALT/DELISTING，dict 返回）；
- F8：限价单不传 limit_price 默认最新价报单——显式限价以 is_trade() 分支内的
  get_snapshot 快照参考价形式提醒（不改下单语句本身）。

分段占位：__SUSPENSION__ / __LIMIT_BLOCK__ / __SNAPSHOT__ / __IDEMPOTENCY__
按 deploy.yaml guards 开关由 transformer 拼装（确定性：同配置同输出）。
"""

#: 护栏函数骨架（分段按 guards 配置拼装后整体注入）
#:
#: PD-0016：签名**不含 context** —— 四段护栏体（停牌/涨跌停/限价快照/幂等）只用
#: `security` / `side` / `g` / `log`，从未使用 context。原实现把「被包裹函数的首参名」
#: 当 context 传入（对 `submit_target_value_order(security, ...)` 会传成 security），
#: 属名实不符；删掉该冗余形参即根治（而非引入脆弱的「向上解析 context」逻辑）。
GUARD_HELPER_TEMPLATE = '''\
def _deploy_guard_pass(security, side):
    """PTRADE-DEPLOY 注入护栏（停牌/涨跌停/限价参考/订单幂等，设计文档 §6.3）。

    由 qs-deploy 转换器自动注入，整块带 GUARD 标记、可整体删除；
    全部检查失败仅拦截下单并告警，绝不抛异常中断策略。
    """
__SEGMENTS__
    return True
'''

#: 停牌护栏（get_stock_status query_type='HALT'，guards.suspension_block）
GUARD_SUSPENSION_BLOCK = '''\
    try:
        if get_stock_status(security, query_type='HALT').get(security, False):
            log.warning('护栏拦截: %s 停牌/HALT，跳过下单' % security)
            return False
    except Exception:
        pass
'''

#: 涨跌停护栏（check_limit：F6 双形态容忍；guards.limit_up_down_block）
GUARD_LIMIT_BLOCK = '''\
    try:
        _flag = check_limit(security)
        if isinstance(_flag, dict):
            _flag = _flag.get(security, 0)
        if side == 'buy' and _flag in (1, 2):
            log.warning('护栏拦截: %s 涨停(check_limit=%s)，拒买' % (security, _flag))
            return False
        if side == 'sell' and _flag in (-1, -2):
            log.warning('护栏拦截: %s 跌停(check_limit=%s)，拒卖' % (security, _flag))
            return False
    except Exception:
        pass
'''

#: 显式限价参考（仅交易分支，get_snapshot 为交易专用 API，F8；guards.limit_price_explicit）
#:
#: PD-0017 B：PTrade `get_snapshot(security)` 返回**二层嵌套** `{code: {last_px: ...}}`
#:（官方示例 `snapshot[stock]['last_px']`，Context7 /kay-ou/ptradeapi）。
#: 原实现 `_snap.get('last_px')` 在外层找字段名 ⇒ 恒 None。改为**一层/二层双形态兼容**
#:（兼容而非替换：若平台版本返回一层，行为不退化）。
GUARD_SNAPSHOT_BLOCK = '''\
    if is_trade():
        try:
            _snap = get_snapshot(security)
            _px = None
            if isinstance(_snap, dict):
                _inner = _snap.get(security)
                if isinstance(_inner, dict):
                    _px = _inner.get('last_px')
                if _px is None:
                    _px = _snap.get('last_px')
                if _px is None and isinstance(_inner, (int, float)):
                    _px = _inner
            elif isinstance(_snap, (int, float)):
                _px = _snap
            log.info('护栏参考: %s 快照最新价=%s（F8：下单请显式限价，默认最新价报单）' % (security, _px))
        except Exception:
            pass
'''

#: 订单幂等护栏（state 内当日记单标记，防 6 秒同步重复下单，F8；guards.order_idempotency）
#:
#: PD-0018 F2：本段改为**只读判定、不再登记**——原「调用即登记」在拆单循环内
#: 逐笔调守卫时，第 1 笔登记后第 2 笔即被自己拦截（2026-11-06 平台实证：513500
#: 拆 4 笔、循环第 2 笔即被拦）。登记统一由订单 shim 的 `_qs_idempotent_admit()`
#: 在逻辑下单入口一次完成（键同构：[str(security), str(side)]）；本段仅消费台账。
GUARD_IDEMPOTENCY_BLOCK = '''\
    try:
        _book = getattr(g, 'state', None)
        if isinstance(_book, dict):
            _key = [str(security), str(side)]
            _done = _book.get('orders_done', [])
            if _key in _done:
                log.warning('护栏拦截: %s %s 当日重复下单，幂等拦截' % (security, side))
                return False
    except Exception:
        pass
'''

#: 陈旧挂单巡检（PD-0017 D；guards.stale_order_check，默认开启）
#:
#: 语义：上一交易日遗留、至今非终态的委托 = 尾盘限价单未成交。2026-11-02 实测 15 笔
#: 「已报」成交 0（513500/159941/513030 三只零持仓），跨日存在误成交 + 策略重新下单
#: 的**双买**风险。
#:
#: 判据**不依赖「今天日期」**（run_daily 回调无统一日期入口，context 日期字段各券商不一）：
#: 框架在 `after_trading_end` 落盘时把「本次执行提交的委托号」滚动进 `qs_prev_orders`
#:（见 persistence_template），本段在**下一次执行的首次下单前**消费该集合、消费后立即
#: 清空 ⇒ 天然「每日一次」，且「每日执行」= 每个交易日，语义精确。
#:
#: 默认**只告警不撤单**；`__CANCEL_STALE__` 由 transformer 按
#: `guards.cancel_stale_orders`（默认 false）替换为 True/False 字面量 ——
#: 撤单是不可逆外部动作，须用户显式开启。
GUARD_STALE_ORDER_BLOCK = '''\
    try:
        _pq = getattr(g, 'state', None)
        if isinstance(_pq, dict):
            _stale_ids = _pq.get('qs_prev_orders', [])
            if _stale_ids:
                _pq['qs_prev_orders'] = []
                _stale_open = []
                for _sid in list(_stale_ids):
                    try:
                        _so = get_order(_sid)
                        _sob = None
                        if isinstance(_so, (list, tuple)):
                            if len(_so) > 0:
                                _sob = _so[0]
                        elif _so is not None:
                            _sob = _so
                        _sst = ''
                        if _sob is not None:
                            try:
                                if isinstance(_sob, dict):
                                    _sst = str(_sob.get('status', ''))
                                else:
                                    _sst = str(getattr(_sob, 'status', ''))
                            except Exception:
                                _sst = ''
                        if _sst in ('5', '6', '8', '9'):
                            continue
                        _stale_open.append(_sid)
                    except Exception:
                        continue
                if _stale_open:
                    log.warning('PTRADE-DEPLOY 陈旧挂单巡检: 上一交易日遗留未终态委托 %d 笔 [%s]——策略本次将重新下单，存在双买风险，请人工确认是否需撤单' % (len(_stale_open), ','.join(_stale_open[:10])))
                    if __CANCEL_STALE__:
                        for _sid in _stale_open:
                            try:
                                cancel_order(_sid)
                                log.warning('PTRADE-DEPLOY 陈旧挂单已撤: %s' % _sid)
                            except Exception:
                                pass
    except Exception:
        pass
'''
