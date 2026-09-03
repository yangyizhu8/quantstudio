# QSPROBE 探针归档：平台分钟 include 语义实证（known-limitation 证据附件）

- 日期：2026-09-03
- 关联：`docs/evidence/pctchg-portability-20260901.md` §6.13 / §8（known-limitation 四要素）
- 实证对象：PTrade IQEngine 回测 `get_history` 分钟频率 include 参数语义
- 结论：**include=False/True 双模式均不可安全表达"上一已完成 bar"**（平台回测数据面固有限制）

## 1. 探针实现（source_import.py `_QS_HISTORY_WRAPPER` 段，v9 起随产物注入）

分钟路径首次调用时（仅一次、纯观测、不参与策略逻辑），以同参数分别发起
`include=False` 与 `include=True` 调用，打印返回 bars 的时间戳/列名/close 值：

```python
# v9 QSPROBE：分钟 include 语义双形态探针（仅首调一次、纯观测、不参与策略逻辑）
_kf = _freq if 'frequency' in kwargs or 'unit' in kwargs else '1m'
_ksecs = _k0
_probe_log = []
for _inc in (False, True):
    _pr = _QSHistoryState.orig(kwargs.get('count', 3), frequency=_kf,
                               field=['close'], security_list=_ksecs,
                               fq=kwargs.get('fq', 'pre'), include=_inc)
    _pr = _qs_to_dataframe(_pr)
    if isinstance(_pr, dict):
        _pr = _pr.get(_ksecs)
    # 打印：有 time 列 → 时间戳数组；无 time 列 → rows + closes 全值
    ...
log.info("QSPROBE %s %s" % (_ksecs, " | ".join(_probe_log)))
```

（产物内为 format 转义后的完整实现；本归档为逻辑摘要。）

## 2. 对照数据（本地 stock_minutes 真实行情，000017.SZ）

| 时点 | 本地真实 close | 说明 |
|---|---|---|
| 06-30 14:55 / 14:56 / 14:57 | **6.12 / 6.12 / 6.12** | 昨日尾盘（06-30 收盘=6.12，涨停封板；preClose=5.56 → +10.07% 封板） |
| 07-01 09:30 / 09:31 | **6.00 / 6.05** | 今日早盘（当日 preClose=6.12，涨停价=6.73） |
| 07-01 09:32 / 09:33 / 09:34 / 09:35 | 6.15 / 6.12 / 6.10 / 6.08 | 今日盘中（09:31 判定时点之后=未来） |

本地库：`data/quantstudio.db` stock_minutes（code 裸 6 位，epoch ms 时间戳，
覆盖 2026-06-15~08-13，000017 共 9,378 行）。

## 3. 平台回执（第十五轮，2026-09-03 10:59 短窗 07-01~07-10，v9 slim 产物）

```
09:31:00 QS_MINUTE_DIAG v=20260902-v7.1 code=000017.SZ keys=4
         cols=['close'] rows=3 last_t=None last_close=6.12 closes=[6.12, 6.12, 6.12]
09:31:00 QSPROBE 000017.SZ
         False:notime rows=3 closes=[6.12, 6.12, 6.12]
         True :notime rows=3 closes=[6.12, 6.12, 6.08]
```

## 4. 逐价格定谳

- **include=False**：`closes=[6.12, 6.12, 6.12]` ≡ 本地 **06-30 尾盘 14:55-14:57**
  → 返回的是**昨日** bar（今日 09:30=6.00 / 09:31=6.05 未出现）。
- **include=True**：`closes=[6.12, 6.12, 6.08]` —— 09:31 判定时点之后才发生的
  **09:33=6.12、09:35=6.08** 出现在返回中 → **包含当前时点之后的 bar（未来函数）**。
  （前值 6.12 与 09:33 一致、末值 6.08 与 09:35 一致；中值受复权/精度影响存在
  ±0.02 级偏差，不影响"含未来 bar"的定性。）

## 5. 结论

| include 形态 | 平台回测返回 | 打板判定后果 |
|---|---|---|
| False | 昨日最后 N 根 bar | last_close=昨日封板价、昨收回退同值 → 涨跌幅恒 0 → 永不买入 |
| True | 含当前时点之后 bar | 未来函数 → 回测失真，不可用 |

→ 平台回测分钟通道无法安全表达"上一已完成 bar"；**实盘/模拟盘不受影响**
（实盘 include=False = 最近已完成 bar = 今日盘中 bar，语义天然正确）。

## 6. 探针运行轮次索引（第十二~十五轮）

- 第十二轮（09-02 22:47，v7.2）：`last_t=None last_close=6.12`——末值疑点首次暴露
- 第十三轮（09-03 09:08，v7.3）：`code=000017.SZ closes=[6.12,6.12,6.12]`——归属疑点锁定
- 第十五轮（09-03 10:59，v9 slim）：QSPROBE 双形态 `closes` 对照——**定谳**
（第十轮 09-02 17:19，v7.1：`cols=['close'] rows=3`——分钟数据可达首次证明）