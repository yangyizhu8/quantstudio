# R5 回测证据 — ou_reversal_csi300_10（沪深300均值回归超跌反弹）

> 阶段：R5（Backtest execution and evidence review）— **agent-managed 模式**
> 执行：2026-09-23；执行方：策略线（agent）
> 设计契约：`output/generated_strategies/ou_reversal_csi300_10/agent_strategy_design.json`（design 2.3，R2.5 已关闭）
> 策略源码：`agent_workspace/ou_reversal_csi300_10/strategy.py`
> 执行器：`agent_workspace/ou_reversal_csi300_10/r5_run.py`

## 0. 结论摘要

| 项 | 结果 |
| --- | --- |
| 证据完整性 | **PASS**（三件套 SHA-256 + 库路径 + 两次独立进程逐位一致） |
| 运行完整性 | **PASS**（287 交易日，0 ERROR / 0 Traceback / 0 数据失败） |
| `r5_deployment_invariants` | **PASS**（91 个调仓日逐日核账，0 FAIL） |
| 引擎口径一致性 | **PASS**（profile / match mode / 语义版本 / 初始资金 全部匹配） |
| 策略表现 | 总收益 **−29.29%** vs 基准 **+17.16%**（超额 **−46.45 pp**），最大回撤 **−30.75%** |
| 待办 | F2-A 对账缺口（框架层，另有立项）；R5.5 稳健性门控 |

## 1. 数据源（外部库覆盖，客户已批准）

| 项 | 值 |
| --- | --- |
| 项目本地库（设计声明） | `data/quantstudio.db` |
| **R5 实际使用库** | `D:\miniQMT策略实盘\QuantStudio\data\quantstudio.old_20260920.db` |
| `external_db_override_confirmed` | **true** |
| 客户原话（2026-09-23） | 「用 data/quantstudio.old_20260920.db 跑 R5」 |
| 覆盖理由 | 项目本地库存在但被生产守护进程（PID 1564，`--mode forever`）持有 RW 锁，无法并发读 |
| 等价性证据 | 表数 101；窗口内开市日 287 / 指数日 286（含同一 2026-08-03 缺口）/ complete 成分快照 15；前复权锚点抽检 600519@2025-01-02 = close 1488.0 / close_front 1401.7967454286804（与主库 R1 实测逐位一致） |
| 被否决的备选 | `data/quantstudio_backup_20260912.db`（= `agent_workspace/backtest_readonly/quantstudio.db`）：000300 指数日线止于 2026-07-31，窗口内仅 265/287 指数日 → 22 个交易日择时门会 fail-closed |

## 2. 运行契约

| 项 | 值 |
| --- | --- |
| 窗口 | 2025-07-01 .. 2026-09-01（**287 交易日**） |
| 初始资金 | 100,000.00 |
| `engine_profile` | `daily-bar-v1` |
| `match_price_mode` | `open`（撮合于 D 日开盘价） |
| `engine_semantics_version` | `0.1.0-legacy`（config.csv 实测） |
| 成本 | `commission_rate=0.00324` / `min_commission=5.0`（印花税 0.001、过户费 1e-05 引擎内置） |
| E1 合规 | 信号取数全部 `include=False`；零 `data[code]` 依赖；指数行消费至 D-1 |

## 3. 产物与哈希（run1）

| 文件 | SHA-256（前 16 位） | 字节 |
| --- | --- | --- |
| `strategy.py`（canonical source） | `9cfb26e568d8be11` | — |
| `config.csv` | `0e02576f6fa1e34a` | 322 |
| `daily_stats.csv` | `6da36f30d6df60ed` | 23,449 |
| `trades.csv` | `4ee8e68ded8cced2` | 51,716 |

产物目录：`output/backtest_results/20260923_101807_strategy/`
Provenance：`agent_workspace/ou_reversal_csi300_10/r5_provenance_run1.json`

## 4. 绩效（run1，287 交易日）

| 指标 | 值 |
| --- | --- |
| 期末净值 | 70,712.26 |
| 总收益率 | **−29.29%** |
| 基准（沪深300） | **+17.16%** |
| 超额收益 | **−46.45 pp** |
| 最大回撤 | **−30.75%** |
| 成交笔数 | 695 |

### 4.1 持有/空仓分解

| 项 | 值 |
| --- | --- |
| 持有天数 | 94 / 287（32.8%） |
| 空仓天数 | 193 / 287（67.2%，择时门关闭 → 全现金） |
| 持有段累计收益 | **−22.77%** |
| 空仓段累计收益 | 0.00% |
| 调仓日数（QS_REBALANCE_AUDIT） | 91 |
| 清仓事件数（QS_LIQUIDATION） | 9 |

**解读**：亏损全部发生在持有段；择时门在 2025-09-25..2026-01-16（74 日）、2026-01-26..2026-04-21（55 日）、2026-07-03..2026-09-01（43 日）等长区间关闭，策略空仓。这是客户 C6-A 与流程图「市场 ADX(14)>25 且 中轨向上且收盘站上中轨」的既定口径。

## 5. `r5_deployment_invariants` 逐调仓日核账

| 指标 | 阈值 | 实测 | 判定 |
| --- | --- | --- | --- |
| `holding_count_mode` | `strict_target_when_candidates_available` | selected=10 / tradable=10，**91/91 日** | PASS |
| `minimum_fill_ratio` = 0.8 | positions ≥ ceil(10×0.8)=8 | positions **恒为 10**（min=max=10） | PASS |
| `minimum_gross_exposure` = 0.8 | gross ≥ 0.8 | min **0.8006**，mean 0.9052，max 0.9658 | PASS |
| `maximum_cash_ratio_after_rebalance` = 0.2 | cash ≤ 0.2 | 全部满足 | PASS |
| `maximum_insufficient_cash_rejections` = 2 | 资金不足拒单 | 未超阈 | PASS |
| `require_at_least_one_rebalance` | ≥1 | 91 | PASS |

**FAILS = 0。**

## 6. 运行完整性

| 检查 | 结果 |
| --- | --- |
| 交易日数 | 287（与日历开市日一致） |
| ERROR / Traceback 行 | **0** |
| `QS_HISTORY_FAIL` | 0 |
| `QS_UNIVERSE_FAIL` | 0 |
| `QS_INDEX_BAR_FAIL` | 0 |
| 成分池 PIT | 15 个 complete 快照全覆盖（2025-07-01 .. 2026-08-31） |

## 7. 已知偏差与未闭合项

| # | 项 | 状态 |
| --- | --- | --- |
| 1 | **F2-A 对账缺口**：`delta_below_one_lot` 接线层 no-op 不进 QS_FILL_AUDIT 计数器 → 买入侧 submitted=574 / filled=364 / rejected=9，缺口 201（QS_ZERO_ORDER 行 762）。方案已落 `docs/qs-fill-audit-delta-below-one-lot-design.md`，待审核方确认前提修正后实施 | **未闭合（框架层，另有立项）** |
| 2 | A-5 基准 2026-08-03 单日缺口（D4-A 已接受） | 已知近似 |
| 3 | A-13 指数行含 D 必须丢弃（E1-4） | 已实现 + R4 断言 A4-1 锁定 |
| 4 | 策略表现为负（−29.29%） | 事实陈述；R5.5 稳健性门控评判 |

## 8. 复现性（G3.5）— **PASS**

第二次独立进程运行（run2，2026-09-23 10:18:07 至 12:11:56），同窗口/同资金/同配置：

| 文件 | run1 SHA-256 | run2 SHA-256 | 一致 |
| --- | --- | --- | --- |
| `config.csv` | `0e02576f6fa1e34a` | `0e02576f6fa1e34a` | **逐位一致** |
| `daily_stats.csv` | `6da36f30d6df60ed` | `6da36f30d6df60ed` | **逐位一致** |
| `trades.csv` | `4ee8e68ded8cced2` | `4ee8e68ded8cced2` | **逐位一致** |
| `strategy.py`（canonical） | `9cfb26e568d8be11` | 同 | **逐位一致** |
| 数据库文件 | `321291870c7b3da9` | 同 | **逐位一致** |

- run1 产物：`output/backtest_results/20260923_101807_strategy/`
- run2 产物：`output/backtest_results/20260923_121155_strategy/`
- 策略确定性：0 ERROR / 0 非确定性来源（无 set()/frozenset 决策依赖；排序全键确定；无随机数）

**G3.5 判定：PASS**（三件套 + canonical 源码 + 数据库 全部逐位一致）。