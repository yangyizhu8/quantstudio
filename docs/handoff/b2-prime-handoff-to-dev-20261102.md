---
title: B2′ 交接补齐件（两项）——致 dev（QS_ORDER_RESULT 排产输入）
date: 2026-11-02
from: 策略研发专属会话（节点⑫）
purpose: 解锁 B2′（类型② 3 日 + gap13 未确证笔 合并审计）；dev 已申报「补齐即排产（一日内）」
---

# 一、数据指针：模拟盘真实意图/成交数据（路径与格式，实测）

| 项 | 值 |
| --- | --- |
| 意图落盘 | `D:\miniQMT策略实盘\QuantStudio-trading\trading-runtime\tdx-paper\var\orders\intents_YYYYMMDD.ndjson` |
| 现存意图件 | intents_20261028（5,876B）/ intents_20261029（5,898B）/ intents_20261102（562B） |
| ⚠ 缺口 | **intents_20261026 已不在盘**（10-26 G3 Day 1 四证在 `docs/handoff/dispatch-graph.md:151` 在册：生产件装载/真实取价/tick#353 信号/**intents_20261026 首行 159925.SZ buy**）；若需 10-26 原始行，向⑩/⑨调取备份 |
| 成交/对账回执 | 同目录 `reconcile_YYYYMMDD.json`（现存 10-16/10-19/10-20/10-21/10-26/10-28/10-29，其中 10-26=285B、10-28=3,382B、10-29=3,402B） |
| 另 | `probe_record_20261029.md`（817B） |
| NDJSON 格式（实读样例） | `{"client_order_id":"paper:paper-main:20261102:159925.SZ:buy:1","method":"order_stock","env":"paper","strategy":"paper-main","trade_date":"20261102","symbol":"159925.SZ",…}` —— 一行一意图；client_order_id 结构=`paper:<策略>:<日期>:<代码>:<side>:<序号>` |

# 二、重跑规格：gap13「命中窗口」

- **策略**：首板高开接力1进2（`output/generated_strategies/first_board_high_open_1to2/strategy.py`，`--profile minute-bar-v1`）
- **重跑主体**：本线（节点⑫）回测；副本根=`data/backup_import_20261101_040935`（覆盖闸 PASS）
- **命中 13 笔所在日（12 个交易日）**：2026-03-02 / 03-03 / 03-16 / 04-01 / 04-07 / 04-08（2 笔）/ 04-09 / 04-10 / 05-19 / 06-09 / 07-21（2 笔）
- **窗格划分建议（供 QS_ORDER_RESULT 读数对齐）**：
  - gap13 未确证（满现金/中等现金仍未成交）：03-02、03-03、03-16、04-01、05-19、06-09
  - A3 类型②（cash=1.0 有 selected 零买入）：03-06、03-16、07-14 —— **与上行仅 03-16 重合，其余互不重叠** ⇒ 合并审计正是为判「同根因 or 两族」
  - 已确证（资金不足/一字板，无需重跑）：04-07、04-08×2、04-09、04-10、07-21×2

# 三、ETA（B2′ 是否赶上 11-04 呈批）

两项已于本件补齐 ⇒ 按 dev「补齐即排产（一日内）」⇒ **QS_ORDER_RESULT 11-03 可出** ⇒ B2′ 审计结果 **11-03 晚~11-04 早**可并入呈批面 ⇒ **赶得上 11-04**（若 dev 排产延迟，呈批面以「B2′ 已排产+ETA」状态行呈现，不空等）。

# 四、边界

- intents 现存最早 **10-28**；模拟盘 shadow 自 10-26 起、但 10-26/10-27 文件不在我可见盘面 ⇒ 涉这两日须向⑩/⑨调取。
- reconcile 为**模拟盘对账**，与回测内 `order()` 拒单码是两个数据面；QS_ORDER_RESULT 需以**回测引擎侧审计**为准，reconcile 仅作交叉参照。