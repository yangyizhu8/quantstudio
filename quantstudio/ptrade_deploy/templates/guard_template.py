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
GUARD_HELPER_TEMPLATE = '''\
def _deploy_guard_pass(context, security, side):
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
GUARD_SNAPSHOT_BLOCK = '''\
    if is_trade():
        try:
            _snap = get_snapshot(security)
            _px = _snap.get('last_px') if isinstance(_snap, dict) else _snap
            log.info('护栏参考: %s 快照最新价=%s（F8：下单请显式限价，默认最新价报单）' % (security, _px))
        except Exception:
            pass
'''

#: 订单幂等护栏（state 内当日记单标记，防 6 秒同步重复下单，F8；guards.order_idempotency）
GUARD_IDEMPOTENCY_BLOCK = '''\
    try:
        _book = getattr(g, 'state', None)
        if isinstance(_book, dict):
            _key = [str(security), str(side)]
            _done = _book.get('orders_done', [])
            if _key in _done:
                log.warning('护栏拦截: %s %s 当日重复下单，幂等拦截' % (security, side))
                return False
            _done.append(_key)
            _book['orders_done'] = _done
    except Exception:
        pass
'''
