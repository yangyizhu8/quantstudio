# 回测两端对齐可诊断性修复 — 设计方案

- 状态：**审计有条件通过（2026-08-17），修订完成，可进入实施**（未触碰任何代码）
- 作者：CodeBuddy 会话（2026-08-17）
- 关联证据：`output/debug_align/rootcause_probe.py` / `rootcause_probe.txt`（四项根因取证，只读复算）
- 流水线：本方案为六步流水线第 1 步产物；审计通过后方可进入实施（第 3 步）

### 审计结论与修订记录（2026-08-17）

审计结论：**有条件通过**——方向正确、边界清晰；决策点 1 确认走"引擎层新增 QS_FILL_AUDIT"路线（不动 QS_REBALANCE_AUDIT/renderer）；决策点 2 确认单笔 DEBUG + 日末汇总（no_price 单笔至少补 DEBUG）。以下 3 处修订已并入 2.1/2.2 节：

- **修订①（方向丢失）**：`_immediate_execute` 中 no_price 分支在方向判定之前返回 `direction="unknown"`（现 L633-636）。方向推断逻辑（target_value/shares 符号，现 L642-650）上移到价格检查之前，no_price 拒单的方向据此归类（sell/buy；无指令才 unknown），Order.direction 与拒单采集共用。
- **修订②（采集不完整→集中采集）**：原"每分支手工埋点"易漏。改为 `_immediate_execute` **单出口**，出口前统一检查 `order.status=="rejected" and order.reason != "below_rebalance_threshold"` 写入 `_day_rejections`，一处覆盖全部拒单路径：no_price、limit_up/down_blocked、halted（分钟 Profile，现 L666-668 零日志）、insufficient_cash_or_rounding 兜底（含整手取整不足 100 股与资金不足，现 L695-706）。next_open 模式 `_drain_pending_orders` 出口同规则采集（本次运行 close 模式，drain 采集为语义一致性扩展）。
- **修订③（字段语义）**：`positions_filled` 改名 **`positions_total`**（当日收盘实际持仓总数，volume>0 计数），并在文档明确：与 QS_REBALANCE_AUDIT 的 `positions`（目标仓数）**口径不同，不可直接相减**（清仓单、未变动持仓均造成口径差）。

次要意见全部采纳：跨日重置场景进单测；rejected_detail 截断输出省略计数（`...(+N more)`）；巡检脚本 ±1.5% 阈值报告边界样本。

---

## 1. 问题定义

本地回测与 PTrade 回测（同一策略 `etf_theme_rotation_quantstudio.py`、同一区间 2026-07、同一初始资金）运行结果分叉，经四项根因深度取证（数据实证），结论如下：

| 根因 | 取证结论 | 性质 | 本地框架可修？ |
|---|---|---|---|
| A 股票池 | 静态池 1271 = 本地 PIT 池 1271（差集 0）；层1过滤复算 23/23 天与审计一致；两端 valid 差 8 只（1128 vs 1136）源于**两端行情数据差异**（PTrade 侧无法本地复核） | 数据源差异，非框架缺陷 | 否（文档化） |
| B 本地拒单 | ① 停牌日无当日 bar → `no_price` 拒单（588710/560780 @07-01、159739 @07-29），**该分支零日志**；② 跌停阻断（159558 @07-02，pctChg=-9.96%），仅 DEBUG 日志；③ 资金不足（159327 @07-02）有 WARNING | **框架可诊断性缺陷** | **是** |
| C 价格/复权 | 本地策略信号=close_front（前复权，=raw×0.5 恒定）；589020 本地评分第 510 名 vs PTrade 第 1 名——PTrade 端序列在 07-01 出现 ~+100% 假跳变（本地 07-01 停牌+合并×2 无 bar，PTrade 有 bar）或 get_history include 语义含当日 | 数据/API 语义差异，需 PTrade 平台端核对 | 部分（本地数据巡检） |
| D 审计语义 | 审计行=**计划层**（策略打印 `buy_submitted=5`），与成交不符（本地 07-01 实际成交 3）；`BUY/TARGET` 行两端均不区分买卖方向与是否成交 | **框架可诊断性缺陷** | **是** |

**核心问题**：本地引擎存在影响成交结果、但 INFO 级日志完全不可见的拒单路径（no_price 拒单连 DEBUG 都没有），且审计行（计划）与实际成交（`trades.csv`/`daily_stats`）不一致——导致两端日志无法机械对齐，任何一次拒单都会静默改变仓位与净值轨迹（本次 07-01 停牌拒单直接造成本地 56% 仓位 vs PTrade 95%，净值差峰值 +8.8%）。

**修复目标**：**可诊断性对齐**——让本地每次回测的"计划 vs 实际"在日志中显式、可机器解析、可与 PTrade 日志逐日对照；**不改变任何撮合/资金/订单行为**（停牌拒单、跌停阻断、T+1 资金均为 A 股正确语义，保持现状）。

## 2. 改动范围

### 2.1 改动点 1：拒单采集与可见性（`quantstudio/backtest/backtest_engine.py`，引擎层）

- **方向判定上移**：将 `target_value/shares` 的方向推断逻辑（现 L642-650）移到价格检查（现 L633）之前，`no_price` 分支与其余分支共用；`no_price` 拒单的 `Order.direction` 因此为 buy/sell（仅当无任何指令时为 unknown）。实施时核对既有测试是否断言 `direction="unknown"`；若存在依赖，则 Order 保持原样、方向仅用于采集（二选一，以测试为准，记录于验收证据）。
- **no_price 单笔补一条 DEBUG 日志**（与其他拒单分支对齐，消除"零日志"盲区；单笔不升 WARNING，避免刷屏）。
- **集中采集（单出口）**：`_immediate_execute` 重构为单出口（result Order 变量 + 末尾统一 return），出口前统一检查 `order.status == "rejected" and order.reason != "below_rebalance_threshold"`，追加到 `self._day_rejections`（元素 `(code, direction, reason)`）。覆盖：no_price / limit_up_blocked / limit_down_blocked / halted（分钟 Profile）/ insufficient_cash_or_rounding（含整手取整不足 100 股与资金不足兜底）。
- **next_open 模式**：`_drain_pending_orders` 出口按同一规则采集（本次目标运行 close 模式；drain 采集为语义一致性扩展，防止模式切换后出现同类盲区）。
- **每日生命周期**：run() 主循环每日起点重置 `_day_rejections = []`；在 `_run_ptrade_strategy` 之后、日末，统计当日 `trade_records`（date==day_str）成交笔数与 `_day_rejections`，打印 `QS_FILL_AUDIT` 行（无拒单 INFO、有拒单 WARNING），随后清空当日记录。
- 涨跌停阻断单笔保持 DEBUG、资金不足单笔保持 WARNING（现状不变，仅纳入采集）。

### 2.2 改动点 2：新增 `QS_FILL_AUDIT` 实际成交审计行（引擎层）

格式（与策略层 `QS_REBALANCE_AUDIT` 风格一致）：

```
QS_FILL_AUDIT date=2026-07-01 sell_filled=0 buy_filled=3 sell_rejected=0 buy_rejected=2 positions_total=3 rejected_detail=[588710:no_price,560780:no_price]
```

- 由**引擎**打印（策略/渲染模板零改动，R5 契约不变，`QS_REBALANCE_AUDIT`/`QS_PORTFOLIO_AUDIT` 保持计划层原样）。
- `sell_filled/buy_filled`：当日 `result.trade_records` 计数；`sell_rejected/buy_rejected`：`_day_rejections` 按方向计数；`positions_total`：当日收盘实际持仓数（volume>0 计数）——**注意与 QS_REBALANCE_AUDIT 的 `positions`（目标仓数）口径不同，不可直接相减**（清仓单、未变动持仓均造成口径差），仅作为"实际持仓"的独立观测。
- `rejected_detail`：`code:reason` 明细，最多 10 条，超出输出 `...(+N more)`（N 为省略条数），不静默丢失。
- 对齐规则：本地 `QS_FILL_AUDIT` 的 filled/rejected 与 `QS_REBALANCE_AUDIT` 的 submitted 对照即得"计划 vs 实际"；PTrade 端无此行（其订单除 0 股取消外全部成交，WARNING 已明示），两端对齐时以"PTrade 订单全成交 ⇔ 本地 submitted==filled"为判据。

### 2.3 改动点 3：数据巡检脚本（新增，不改框架代码）

`scripts/audit_etf_corporate_actions.py`（只读巡检，输出报告）：
- 统计 2026-07 全月 ETF 停牌日（当日无 bar 的 equity ETF 及名单）、公司行为日（preClose 相对前一收盘跳变 >±1.5%，区分拆分/合并）；
- **阈值边界样本**：±1.5% 阈值附近的样本（如 1.2%~1.8% 区间）单独列出，避免阈值选择倒推结论；
- 输出 `output/debug_align/etf_ca_audit_202607.md`，用于确认"07-01 停牌/公司行为群"是模拟数据源特征还是同步缺口（根因 B/C 的定性依据），并作为证据文档附件。

### 2.4 文档同步（铁律要求）

- `README.md`：策略工具箱章节补充"回测审计行（QS_REBALANCE_AUDIT / QS_FILL_AUDIT）计划-实际对照"说明。
- `docs/strategy_toolbox.md`、`docs/prompt_engineering.md`：R5 审计日志契约章节同步新增 `QS_FILL_AUDIT` 行格式与对齐用法。
- `docs/evidence/`：新增本次取证与验收证据文档（引用 `output/debug_align/rootcause_probe.txt` 等）。

## 3. 不纳入本次改动的范围（明确边界）

| 事项 | 原因 |
|---|---|
| 撮合/资金/订单行为（停牌拒单、跌停阻断、T+1 资金、close 撮合价） | A 股正确语义，修改即改变回测行为（违反性能/行为铁律）；本次只增强可见性 |
| 静态池固化逻辑（FREEZE） | 已实证与 PIT 池完全一致（1271=1271），无缺陷 |
| 本地层1过滤逻辑（60bar/1e6 成交额） | 复算 23/23 与审计一致，无缺陷 |
| 策略生成模板/renderer（R5 审计行打印） | 审计行保持计划层契约不变，避免重渲染风险面 |
| PTrade 端 589020 序列 / include 语义 / 1136 差集 | 无本地数据可复核，列为外部核对清单（第 6 节），不阻塞本地实施 |
| 两端"结果对齐"（净值/持仓逐日一致） | 根因 A/C 主因是数据源差异，本地框架无法修复 PTrade 数据；本方案只保证"差异可解释、可机械对照" |

## 4. 影响面分析

- **代码文件**：`quantstudio/backtest/backtest_engine.py`（方向判定上移 + 单出口集中采集 + 日末审计行 + `_drain_pending_orders` 同规则采集，约 40-60 行）；新增 `scripts/audit_etf_corporate_actions.py`；新增测试 `tests/test_fill_audit.py`。
- **行为影响**：零。改动仅为日志/内存统计增量，不触碰 `_execute_buy/_execute_sell/_immediate_execute` 的成交、资金、仓位逻辑，不改变任何 API 签名/返回结构（`Order` 对象不变）。
- **风险点**：① 日末统计需以 `trade_records` 的 date 字段为准，与撮合模式（close/open/next_open）的记账日期语义一致（本次运行是 close 模式，同日成交同日记账）；② `_day_rejections` 生命周期需在每日循环起点重置，防止跨日残留；③ 日志行长度（rejected_detail 可能很长）需截断（如最多列 10 条，超长省略）。
- **性能**：每次拒单一次内存 append，日末一次格式化，可忽略。

## 5. 验收标准

1. **单元测试**（`tests/test_fill_audit.py`）：
   - 构造四种拒单场景（no_price、涨跌停阻断、halted（分钟 Profile）、资金不足/整手取整不足 100 股兜底），断言 `QS_FILL_AUDIT` 行存在、方向计数正确（含 **no_price 的 buy/sell 方向归类**）、明细正确；
   - 断言 `below_rebalance_threshold` 不计入 rejected（集中采集排除规则）；
   - **跨日重置**：第一天有拒单、第二天无拒单，断言第二天 `rejected_detail=[]`、计数归零；
   - 截断场景：>10 条拒单时输出 `...(+N more)` 且 N 正确；
   - 正常日 `sell_filled+buy_filled == submitted`、`positions_total == daily_stats.positions`；
   - 断言 `QS_REBALANCE_AUDIT`/`QS_PORTFOLIO_AUDIT` 行输出与修复前逐字节一致（R5 契约不变）。
2. **黄金结果回归**：修复前后重跑代表策略（含 etf_theme_rotation 2026-07 区间），`trades.csv`、`daily_stats.csv`、净值序列**逐字节一致**（纯日志/采集改动的行为等价性证明）。
3. **Order 行为检查**：修复后 `no_price` 拒单的 `Order.direction` 变化（unknown → buy/sell，或按既有测试依赖保留）记录于验收证据，确认无策略可观察行为意外变化。
4. **两端对齐演示**（验收证据文档）：本地重跑 2026-07 回测，产出日志中 `QS_REBALANCE_AUDIT`（计划）+ `QS_FILL_AUDIT`（实际）逐日对照 PTrade 日志，能机械解释全部差异：拒单（停牌/跌停/资金）、数据差异导致的选股差异（589020 等）、池差异（1128 vs 1136）——差异项全部有据可查。
5. **全量 pytest 通过**（不引入回归）。

## 6. 外部核对清单（不阻塞本地实施，需 PTrade 平台端/数据源侧）

- [ ] PTrade 端 589020 日线序列（07-01 前后），验证"合并×2 被当作正常交易日 → ~+100% 假跳变"推断；
- [ ] PTrade `get_history(include=False)` 实际返回窗口（是否含当日 bar）；
- [ ] PTrade 端 1136 valid 与本地 1128 的差集代码（8 只）及原因；
- [ ] 本地数据源侧确认 2026-07-01 停牌/公司行为群（ETF 合并×2、拆分等）为模拟数据生成特征（改动点 3 巡检报告支撑）。

## 7. 实施顺序（审计通过后）

1. 改动点 1+2（引擎日志/审计行）→ 2. 新增单测 → 3. 黄金回归对比 → 4. 改动点 3 巡检脚本 → 5. 文档同步 → 6. 验收证据文档 → 7. 用户确认 → 8. 双仓库推送。

## 8. 回退条件

- 所有改动为增量日志/统计：删除引擎两处新增代码 + 测试 + 巡检脚本 + 文档回滚即可完全回退；
- 不涉及撮合/资金/数据变更，回退无行为残留风险；
- 若黄金回归出现任何 `trades.csv`/净值差异（哪怕 1 分钱），视为验收失败，立即停止并回退。

## 9. 审计已确认的决策（存档）

- **审计行实现位置**：引擎层新增 `QS_FILL_AUDIT`（策略/renderer/模板零改动，R5 契约不变）；不走修改 `QS_REBALANCE_AUDIT` 的路线。✅ 审计确认
- **日志级别**：单笔拒单 DEBUG（no_price 由零日志补 DEBUG）、日末有拒单时 WARNING 汇总。✅ 审计确认
- **修订项落实**：① no_price 方向推断上移；② `_immediate_execute` 单出口集中采集（含 halted 与兜底拒单）+ next_open drain 同规则；③ `positions_filled` → `positions_total` 并明确口径。✅ 已并入 2.1/2.2/5 节
- 次要意见（跨日重置单测、截断省略计数、巡检阈值边界样本）✅ 已并入 2.3/5 节
