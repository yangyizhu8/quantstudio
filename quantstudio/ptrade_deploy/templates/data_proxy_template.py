# -*- coding: utf-8 -*-
"""部署态 `data` 惰性代理模板（PD-0017 A 子项）。

## 为什么需要

PTrade 平台契约（Context7 `/kay-ou/ptradeapi`）：`run_daily(context, func, time)` 的
func 签名为 `Callable[[Context], None]` —— **只传 context，不传 data**；官方示例一律
`def get_finance(context):`。

而 qs-deploy 把源 `handle_data(context, data)` 包装成 run_daily 回调
（`transformer.py` MOUNT），回调体内对 `data` 的任何访问都会拿到 `None`：

    if security in data:                    # TypeError: NoneType is not iterable
        price = data[security]['close']

策略侧的 `except` 吞掉该异常后回落到 `position.last_sale_price`；首日无持仓即
返回 `0.0` → 「价格无效，无法计算目标数量」→ **整轮调仓中止**
（2026-11-02 四象限ETF轮动策略 512890.SS 实测复现）。

## 做法

注入一个**无状态**惰性代理作为挂载包装的 `data` 默认值，使 `data` 语义在交易态可用：

    security in data          -> 能否解析出正价格
    data[security]['close']   -> 解析到的价格（取不到为 0.0）
    data[security].close      -> 同上（属性形态）
    data.get(security)        -> 同上
    data.keys() / len(data)   -> 空集合（平台回调不提供 bar 全集）

## 两条硬约束

1. **取价与下单换算价同源**：走 `_qs_px_exec`（当日 close 优先 → 前收回退），
   与 `order_target_value` 的股数换算价同链，保证「目标数量」与「下单金额」不自相矛盾。
2. **必须无状态**：Python 的默认参数在 `def` 时求值一次并被所有调用共享 ——
   代理若缓存价格，隔日/隔 tick 就会读到陈价。故每次 `__getitem__` 实时解析。

## fail-safe

取不到价 ⇒ `close = 0.0` ⇒ 策略的 `if price > 0` 判定不通过 ⇒ 自然回落到持仓价分支
（与改造前「data=None → 抛异常 → except → 同一回落路径」**同构**），不产生行为分叉。
"""

#: 代理类 + 工厂函数（3 个模块级语句，逐条带 DATA_PROXY 标记，P4 整体剔除）
DATA_PROXY_TEMPLATE = '''\
class _QSDataItem(dict):
    """单标的行情项：同时支持 item['close'] 与 item.close 两种访问形态。"""

    def __getattr__(self, _name):
        try:
            return self[_name]
        except KeyError:
            raise AttributeError(_name)


class _QSDataProxy(object):
    """交易态 data 惰性代理（PTrade run_daily 不传 data 时的替身）。

    无状态：每次访问实时解析价格，不做缓存。
    取价链与 _qs_px_exec 同源（当日 close 优先 → 前收缓存 → get_history → 0.0）。
    """

    def _resolve(self, security):
        try:
            _px = _qs_px_exec(security)
        except Exception:
            _px = 0.0
        try:
            _px = float(_px)
        except Exception:
            return 0.0
        return _px if _px > 0 else 0.0

    def __contains__(self, security):
        try:
            return self._resolve(security) > 0
        except Exception:
            return False

    def __getitem__(self, security):
        _px = self._resolve(security)
        return _QSDataItem(close=_px, last_px=_px, price=_px, preclose_px=_px)

    def get(self, security, default=None):
        _px = self._resolve(security)
        if _px <= 0 and default is not None:
            return default
        return _QSDataItem(close=_px, last_px=_px, price=_px, preclose_px=_px)

    def __len__(self):
        return 0

    def keys(self):
        return []

    def __iter__(self):
        return iter([])


def _qs_data_proxy():
    """构造全新代理（无状态，可安全作为默认参数被共享）。"""
    return _QSDataProxy()
'''
