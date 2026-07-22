# smallcap_guard 基线漂移归因分析（PR6a 验收期间发现）

> Date: 2026-07-22
> Trigger: PR6a fidelity gate 回归发现 smallcap_guard FAIL（verdict=CLOSE，但 local_result 校验超容差）
> Status: 归因完成，等待用户决定是否 re-baseline
> Protocol: 黄金基线变更协议（strategy-fidelity-regression-gate.md §"Do not update golden values merely to make a changed implementation pass"）

## 1. 现象

```
etf_momentum:   [PASS] verdict=PASS  (L1=1.0, L3=1.0, final_asset=87752.56, trade_count=3)
smallcap_guard: [FAIL] verdict=CLOSE (L2.sharpe=0.0496 > 0.03,
                                 L3=0.9425 < 0.95,
                                 final_asset=118160.53 vs 118551.21 ± 10,
                                 trade_count=59 vs expected 57)
```

etf_momentum 硬门禁 PASS（同一份 xtquant 数据），smallcap_guard 软门禁 FAIL。差异规模：多 2 笔成交，final_asset 偏 390.68 元。

## 2. 排除项：PR6a 是否引入此回归

**结论：否。PR6a 零运行时影响。**

证据链：
1. `git status` 证明 PR6a 未修改任何运行时文件：`quantstudio/backtest/strategies/小市值策略ptrade.py`（黄金策略）、`backtest_engine.py`、`strategy_runner.py`、`ptrade_api.py`、`ptrade_import.py` 全部未动。
2. PR6a 交付物全是 `quantstudio/strategy_compiler/` 下的新文件 + tests + docs + skill templates，无一被回测运行时 import。
3. 因此 smallcap_guard 回测结果在 PR6a 前后完全相同。

## 3. pipeline 4 文件 + 2 脚本 diff 交代（补归因漏洞）

PR6a 工作区有 4 个 pipeline 文件未提交修改（基线提交 96314a5 之后），它们在数据写入路径上。逐一交代：

| 文件 | 改动 | 影响 stock_daily 数据内容？ |
|---|---|---|
| `aligner.py` (+76/-59) | threading.local 连接池（5 处 PIT JOIN 复用线程连接）+ valuation_df 按 code 过滤 | **否**。纯性能优化：SQL 语义不变，JOIN 结果集相同；aligner 是采集时对齐层，回测不经过 |
| `xtquant_adapter.py` (+2/-1) | isST 补全条件 `"code"` → `"stock_code"`（列名 bug 修复） | **否（对小市值选股而言）**。修复后 isST 粗标按 ST 板块补，但 miniQMT 模拟端 ST 板块返回空，isST 仍恒 0（实测 8926581 行 isST=1 count=0）。小市值策略用 `is_st_reliable`（namechange PIT JOIN，136777 行 =TRUE），不用 isST 粗标 |
| `validator.py` (+10/-4) | AdjustmentFactorConsistency 阈值源感知（xtquant back 5%，其他 2%）+ 向量化索引修复 | **间接，但已验证不传导到选股**。5% 阈值让 xtquant back 复权数据更多行通过校验入库。但这是 xtquant 算法固有特性（逐 tick 累积复权，同根 K 线 OHLC 因子 2-4% 微差），非数据错误——validator 5% 阈值文档化了这点。微差传导到 float_value 因子排序，仅在排序边缘（接近阈值的票）偶尔改变入选/出选 |
| `writers.py` (+15/-13) | write 前后 COUNT(*) 改为主键 IN 查询 | **否**。纯性能优化：写入/upsert 语义不变，只改 new/updated 行数的审计统计方式 |

**结论**：4 个 pipeline 文件的修改中，只有 validator 阈值放宽（2%→5%）间接影响 stock_daily 数据内容（让 xtquant back 复权微差行入库），但这是 xtquant 算法特性的正确适配（非错误数据通过校验），且影响仅在因子排序边缘。

## 4. 根因：xtquant vs tushare 复权数据微差

**根因**：本次 stock_daily 全量重拉用的是 xtquant 源（凌晨清空重拉执行，8926581 行 100% xtquant），而 smallcap_guard 基线（final_asset 118551.21 / 57 笔）冻结时用的是 tushare 源。两源的复权算法不同：
- **tushare**：日级单一复权因子，OHLC 共用同一因子。
- **xtquant back**：逐 tick 累积复权，同根 K 线 OHLC 因子有 2-4% 微差（算法固有，validator 阈值 5% 文档化）。

小市值策略按 `float_value`（流通市值）排序选股。复权微差传导到价格→市值计算→排序，在排序边缘（float_value 接近第 N 名阈值的票）偶尔改变入选/出选，导致本次 59 笔 vs 基线 57 笔（差 2 笔），final_asset 偏 390.68 元。

**为何 etf_momentum 不受影响**：ETF 动量按 `np.polyfit` 动量评分选 1 只 ETF，候选池仅几只 ETF，排序边缘极窄，复权微差不足以改变排名。小市值候选池是指数成分股（数百只），排序边缘密集，复权微差易传导。

## 5. 本次实际成交 vs 基线期望（gate config）

| 指标 | 基线（gate config） | 本次（20260722_225239） | 偏离 | 容差 | 状态 |
|---|---|---|---|---|---|
| trade_count | 57 | 59 | +2 | 精确匹配 | FAIL |
| final_asset | 118551.21 | 118160.53 | -390.68 | ±10 | FAIL |
| L1（信号重合） | — | — | — | ≥0.72 | PASS（软门禁） |
| L2.sharpe | — | 0.0496 | — | ≤0.03 | FAIL |
| L3（持仓重合） | — | 0.9425 | — | ≥0.95 | FAIL |

gate config 的 `expected_trade_sequence` 为空，无法做逐笔对比。差异归因只能到"排序边缘 2 笔换入/换出"粒度。

## 6. 处理建议（等待用户决定）

按黄金基线变更协议，禁止在归因完成前更新 envelope。归因现已完成：

- **选项 A（re-baseline）**：接受 xtquant 作为新权威源，更新 smallcap_guard 基线为本次值（final_asset 118160.53 ± 10 / 59 笔）。理由：xtquant 单源锁已定（2026-07-21 决策），tushare 基线已过时；etf_momentum 硬门禁在同一份数据上 PASS 证明引擎完好。
- **选项 B（保持基线，调查）**：先验证 xtquant 复权数据在 float_value 计算上是否与 tushare 有系统性偏差（不只是排序边缘），确认无数据质量问题后再 re-baseline。
- **选项 C（扩大容差）**：smallcap_guard 本就是软门禁（accepted_verdicts 含 CLOSE），可放宽 final_asset 容差（±10 → ±500）和 trade_count（精确 → ±3），承认数据源切换带来的合理漂移。

**我的建议**：选项 A + 文档化。xtquant 单源锁是已批准的架构决策，复权微差是算法特性（非错误），etf_momentum 硬门禁 PASS 证明引擎完好。把 smallcap_guard 基线更新为 xtquant 数据下的新值，并在 gate config 注明"2026-07-22 re-baseline: tushare→xtquant 数据源切换"。

## 7. 不影响 PR6a

无论 smallcap_guard 走 A/B/C 哪个选项，都不影响 PR6a 的交付与 commit。PR6a 是 strategy_compiler 层的新增代码，与数据层/回测层解耦。smallcap_guard 漂移是数据运维事项，归因已交代清楚。

## 8. 附：pipeline 4 文件的后续 commit 归属

这 4 个 pipeline 文件 + 2 个切源脚本（`clear_etf_daily_for_xtquant.py` / `run_valuation_full.py`）**不进 PR6a commit**。它们是数据切源 + 性能修复的成果，等本归因文档审完后作为**独立的数据运维 commit** 提交（message 说明：aligner 连接池化 + validator 阈值源感知 + writer COUNT 优化 + xtquant_adapter isST 修复 + 切源脚本）。
