# 探针策略矩阵（Probe Matrix）· 规划 v1

> 主动踩面（coverage-driven）取代被动撞洞（demand-driven）。每探针 = 一个最小策略，系统性
> 消费一类对齐面，双端跑逐笔 diff。**新策略放行门禁：探针矩阵 diff=0 方可出管线。**

## 探针清单（P1 建 v1，P2 扩全量）

| 探针 | 覆盖面 | 消费模式 | 状态 |
|---|---|---|---|
| probe_cost_basis | POS-01/13、FEE-05 | 止损按成本判定 + 盈利/亏损卖出/分红/送股混合序列 + 清仓重建 | 规划（P1） |
| probe_enable_amount | POS-03/04、MCH-05/06 | T+1 冻结/当日回转/T+0 ETF 分类/部分卖出后可用量 | 规划 |
| probe_limit | MCH-03/04 | 涨跌停日委托/一字板/停牌复牌恢复交易 | 规划 |
| probe_data_boundary | DAT-05..15 | include 边界/除权除息日/首末交易日/fallback 源切换 | 规划 |
| probe_lifecycle | LC-01..08 | 生命周期时点/定时任务/上下文快照 | 规划 |
| probe_fees_slippage | FEE-02/04、MCH-02 | 显式滑点设置双端比/比例佣金/过户费 | 规划 |
| probe_indicator | IDX-01..10 | 平仓对胜率 vs 日胜率口径/年化/回撤算法 | 规划 |

## 执行规程

1. 探针入 `tests/strategy_references/probes/`（测试资产，非实盘策略目录）；
2. 每探针双端各跑一次（本地引擎 + 用户平台人工/半自动），日志归档 `probes/results/<日期>_<探针名>/`；
3. 逐笔 diff（diff-triage 总纲 §1-3 三态仲裁）；结果回写 registry 条目状态；
4. diff=0 的探针结果也是「已排查→已锚定」升级证据（配契约测试后）。

## 平台侧配合（人工 SOP）

- 探针策略生成 PTrade 版（经转换管线），用户在 PTrade 回测导出日志；
- 日志按 `probes/results/` 规范归档（复用 ptrade回测日志 目录约定）；
- 每次 10-15 分钟/探针——P1 五探针一轮 ≈ 1 小时人工配合。