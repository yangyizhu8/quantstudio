# -*- coding: utf-8 -*-
"""PD-0019 期 1 · 多策略同账户隔离注入模板（第二跳 deploy 注入）。

裁定甲（2026-11-08）：**隔离力量在下单入口 + 虚拟台账层**，不替换 ``context.portfolio``
（代理降级为可选：仅当策略源码直接读 ``portfolio.<attr>`` 做信号判断时才需要）。

本模板只含字符串常量，``exec`` 加载无副作用（与 persistence/guard 模板同约定）。

注入形态（两段，位置敏感）：
- ``_QS_ISO_HEAD``：插在模块头（``_after_header_index``）——常量 + 虚拟台账 + 读数/拒单函数。
- ``_QS_ISO_TAIL``：**追加到模块尾**——``_qs_pos_amount`` 与 ``_QSOrderRefState.target_orig``
  的覆盖必须在两者都已定义（第一跳产物里）之后执行，否则 NameError。

占位符（由 transformer 用 ``.replace`` 填）：
``__CAPITAL__`` / ``__POLICY__`` / ``__MARKET_SPLIT__`` / ``__RESERVE_BUFFER__``。

判据来源（PD-0019 §11.6）：
- P0-1 ``_qs_pos_amount`` 代理化：虚拟持仓，杜绝 ``get_position`` 真实持仓混入 delta。
- P0-2 ``target_orig`` 兜底全禁：虚拟额度 < 真实持仓 ⇒ delta 恒负 ⇒ 原实现走平台原生
  按**真实**持仓折算 ⇒ 把真实持仓卖掉（不可逆）。strict ⇒ 拒单 + 计数；soft ⇒ 告警放行。
- §11.3 跨日重置：``当日虚拟现金基准 = capital − 隔夜持仓市值``，<0 取 0 并告警；
  隔夜市值取**不复权昨收**，缺失回退最近可得收盘 + 告警，**禁回退 0**（0 会致额度虚增=首日超买）。
"""

QS_ISO_MARK = "ISO"

_QS_ISO_HEAD = '''
# PTRADE-DEPLOY:ISO BEGIN
# PD-0019 期1：多策略同账户隔离——裁定甲「隔离落在下单入口 + 虚拟台账层」
# 不替换 context.portfolio（代理降级为可选）；enabled=False 时本块不注入（纯增益）。
_QS_ISO_ENABLED = True
_QS_ISO_CAPITAL = __CAPITAL__
_QS_ISO_POLICY = __POLICY__
_QS_ISO_MARKET_SPLIT = __MARKET_SPLIT__
_QS_ISO_RESERVE_BUFFER = __RESERVE_BUFFER__


def _qs_iso_today():
    """当日键（本地日期 yyyymmdd）——跨日重置的比较基准。"""
    try:
        import datetime as _d
        return _d.datetime.now().strftime('%Y%m%d')
    except Exception:
        return 'unknown'


def _qs_iso_overnight_value(iso):
    """隔夜持仓市值（不复权昨收；缺失 → 告警并跳过，禁回退 0）。"""
    total = 0.0
    try:
        _pos = iso.get('pos') or {}
    except Exception:
        return 0.0
    for _code, _amt in _pos.items():
        try:
            _amt = float(_amt or 0)
        except Exception:
            continue
        if _amt <= 0:
            continue
        _px = 0.0
        try:
            _px = float(_qs_last_close_lookup(_code) or 0.0)
        except Exception:
            _px = 0.0
        if _px <= 0:
            try:
                log.warning('QS_ISO_PX_MISSING code=%s（隔夜市值缺口；回退 0 会使额度虚增）' % _code)
            except Exception:
                pass
            continue
        total += _amt * _px
    return total


def _qs_iso_state():
    """虚拟台账（惰性初始化 + 跨日重置 §11.3）。失败返回 None（调用方须容错）。"""
    try:
        _st = g.state
    except Exception:
        return None
    if not isinstance(_st, dict):
        return None
    iso = _st.get('qs_iso')
    if not isinstance(iso, dict):
        iso = {}
        _st['qs_iso'] = iso
    if not isinstance(iso.get('pos'), dict):
        iso['pos'] = {}
    if 'reserved' not in iso:
        iso['reserved'] = 0.0
    if 'rejects' not in iso:
        iso['rejects'] = 0
    _ep = _qs_iso_today()
    if iso.get('epoch') != _ep:
        _overnight = 0.0
        try:
            _overnight = _qs_iso_overnight_value(iso)
        except Exception:
            _overnight = 0.0
        try:
            _base = float(_QS_ISO_CAPITAL) - float(_overnight)
        except Exception:
            _base = 0.0
        if _base < 0:
            _base = 0.0
            try:
                log.warning('QS_ISO_CAPITAL_NEGATIVE overnight=%.2f capital=%.2f'
                            '（当日虚拟现金基准取 0）' % (_overnight, float(_QS_ISO_CAPITAL)))
            except Exception:
                pass
        iso['cash_base'] = _base
        iso['reserved'] = 0.0
        iso['rejects'] = 0
        iso['epoch'] = _ep
        try:
            log.info('QS_ISO_RESET epoch=%s capital=%.2f overnight=%.2f cash_base=%.2f'
                     % (_ep, float(_QS_ISO_CAPITAL), _overnight, _base))
        except Exception:
            pass
    if 'cash_base' not in iso:
        iso['cash_base'] = float(_QS_ISO_CAPITAL)
    return iso


def _qs_iso_pos_amount(security):
    """虚拟持仓股数（P0-1）。"""
    iso = _qs_iso_state()
    if iso is None:
        return 0.0
    try:
        return float((iso.get('pos') or {}).get(str(security), 0) or 0)
    except Exception:
        return 0.0


def _qs_iso_cash_avail():
    """虚拟可用现金 = cash_base − reserved（跨标的累计预扣）。"""
    iso = _qs_iso_state()
    if iso is None:
        return 0.0
    try:
        _c = float(iso.get('cash_base', _QS_ISO_CAPITAL)) - float(iso.get('reserved', 0.0))
        return _c if _c > 0 else 0.0
    except Exception:
        return 0.0


def _qs_iso_target_orig_blocked(security, value, *args, **kwargs):
    """P0-2：isolation 启用时 target_orig 兜底全禁（strict 拒单 / soft 告警放行）。"""
    try:
        iso = _qs_iso_state()
        if iso is not None:
            iso['rejects'] = int(iso.get('rejects', 0)) + 1
    except Exception:
        pass
    if _QS_ISO_POLICY == 'soft':
        try:
            log.warning('QS_ISO_TARGET_ORIG_SOFT api=order_target_value code=%s value=%s'
                        % (security, value))
        except Exception:
            pass
        return None
    try:
        log.error('QS_ISO_TARGET_ORIG_BLOCKED api=order_target_value code=%s value=%s'
                  '（policy=strict：隔离启用时禁用平台原生态折算，防按真实持仓超卖）'
                  % (security, value))
    except Exception:
        pass
    return None
# PTRADE-DEPLOY:ISO END
'''

_QS_ISO_TAIL = '''
# PTRADE-DEPLOY:ISO BEGIN
# PD-0019 P0-1/P0-2：模块尾部覆盖（此时 _qs_pos_amount 与 _QSOrderRefState 均已定义）
if _QS_ISO_ENABLED:
    _qs_pos_amount_legacy = _qs_pos_amount

    def _qs_pos_amount(security):
        """isolation 启用：持仓读数改走虚拟台账（P0-1，杜绝真实持仓混入 delta）。"""
        return _qs_iso_pos_amount(security)

    _QSOrderRefState.target_orig = _qs_iso_target_orig_blocked
# PTRADE-DEPLOY:ISO END
'''
