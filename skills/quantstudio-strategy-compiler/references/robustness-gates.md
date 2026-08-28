# Robustness Gates (R5.5) — 阈值与方法规范

> 归属：quantstudio-strategy-compiler ｜ 契约版本：robustness_report 1.0 ｜ 生效：2026-08-28
> 权威设计文档：`docs/strategy-compiler/robustness-gates-design.md`（终审通过版）
> 本文件为 R5.5 阶段的方法与阈值权威参考。阈值 Phase 1 **固定**，不可按 design 覆盖；
> 唯一豁免通道 = `design.validation_contract.robustness_gates.enabled=false` + R2.5 verbatim 确认。

## 阈值表（照 simple-quant-factory 原版）

| 门 | 阈值 | 维度 | 数据源 | INSUFFICIENT_SAMPLES 条件 |
|---|---|---|---|---|
| G1 | 年化 > 0%，(nav_end/nav_start)^(252/n) − 1 | absolute | 主运行 daily_stats.csv `total_asset` | 交易日 n < 60 |
| G2 | 最大回撤 < 25%（NAV 峰谷） | absolute | 同上 | n < 60 |
| G3 | 夏普 > 0.5（**ETF 池策略 > 0.25**）= mean(r)/std(r)×√252，rf=0 | absolute | 同上 `daily_return` 或 NAV 差分 | n < 60 |
| G4 | 胜率 > 40% | trade_level | 主运行 round_trips.csv / trades.csv FIFO 配对 | round trips < 10 |
| G5 | WF 正窗口 ≥ 60%（有效折超额 > 0 占比） | fold_level | 5 折运行报告 | N<250 / 基准缺失 / 有效折 <3 |
| G6 | MC p < 0.01 | excess | 主运行 NAV + 基准 | n<120 / 基准缺失 |

三态判定：`PASS` / `FAIL` / `INSUFFICIENT_SAMPLES`。INSUFFICIENT 不阻断（不构成 FAIL），但必须在报告与汇报中显著呈现并注明原因。

ETF 判定：design `universe_contract`（`get_etf_list_local` / 显式 ETF 池）→ G3 阈值 0.25。仅对 ETF 池策略生效。

## 维度声明（M2）

- **G1/G2/G3 = 绝对收益维度**；**G6 = 超额收益维度**（对基准的超额日收益均值的统计显著性）；G4 = 交易级；G5 = 折级。
- 两门独立评估：策略绝对收益为负但跑赢基准时，**G1 FAIL 而 G6 可能 PASS——属设计语义，不是矛盾**。报告按门独立呈现。

## WF 5 折切分规则

1. 折轴 = 主运行 daily_stats.csv 交易日序列（权威日期轴），N = 总交易日数；
2. 时序连续、不重叠、禁止随机打乱；base = N//5，余数 r = N%5，最早 r 折各多 1 日；
3. N < 250 → 跳过折运行，G5 = INSUFFICIENT_SAMPLES；
4. 折配置从**哈希验证后的 config.csv 程序化派生，仅替换 start_time/end_time**（费率/滑点/close 模式/engine_profile/fidelity/资金/ETF T+0 语义全继承，杜绝折间漂移）；
5. 每折空仓起、资金同主运行（WF 固有状态独立性，报告强制声明）；
6. 折起点前 provider 历史照常可用于指标预热（规则 24），不构成前视。

### 折内零交易规则（M1）

折内 round trips = 0 或全程空仓（折收益无法计算）→ 该折标记 `NO_TRADE`，**不计入 G5 正窗口分母**（防止零交易折因"超额=0 不>0"被系统性计为负窗口）；**有效折（非 NO_TRADE）< 3 → G5 = INSUFFICIENT_SAMPLES**。

### 每折报告四字段（M3，schema 强制）

`fold_window(start/end)` / `trade_days` / `round_trips` / `no_trade_flag` + 每折 config/daily_stats/trades/round_trips 四工件 SHA-256。

## OOS 正窗口定义

折超额收益 = 折策略区间收益 − 折基准区间收益。基准优先级：主运行 daily_stats.csv 内嵌 `benchmark` 列（同轴、同工件，首选）→ design 声明的 set_benchmark 指数（provider）→ 缺省 000300.SS；基准不可得 → G5 = INSUFFICIENT_SAMPLES。正窗口 = 有效折中超额 > 0；G5 PASS ⟺ 正窗口数/有效折数 ≥ 60%。

## G4 round-trip 配对规范

- 优先数据源：引擎产出的 `round_trips.csv`（`buy_date,sell_date,code,pnl,hold_days`，引擎侧 FIFO 配对、pnl 已含费）；
- 交叉验证/selftest 用途的独立配对器：同标的同方向开平配对，每笔卖出按 FIFO 抵扣最早未平买入批量（一笔卖出可拆多对），期末仍持仓的买入不计；pnl = 卖出净额（amount − commission − tax）− 买入净成本（amount + commission），按 trades.csv 逐笔费字段计算；
- 胜 = pnl > 0；胜率 = 胜对数/总对数；总对数 < 10 → G4 = INSUFFICIENT_SAMPLES（保护周频/事件驱动小样本策略）。

## MC n=1000 方法规范（解析法，0 次引擎重跑）

- 输入：主运行 NAV → 日收益 r_t；超额日收益 e_t = r_t − b_t（b_t = daily_stats `benchmark` 列日收益）；
- 平稳块自助法：块长固定 **21 交易日**（月频策略约一个月相关长度），n=1000 次环形重采样；统计量 = 重采样超额日收益均值；H0 = 均值 ≤ 0；**p = (1 + #{重采样均值 ≤ 0}) / (1 + 1000)**（加一校正）；
- G6 PASS ⟺ p < 0.01；NAV 日数 < 120 → INSUFFICIENT_SAMPLES。

### G6 阈值收紧理由存档（p<0.05 → p<0.01，源自 simple-quant-factory v2.12，2026-07-04）

多重检验防御：按年研发 20 个策略计，p0.05 → 期望 1 个纯运气策略通过门控；p0.01 → 期望 0.2 个。G6 是防"运气当 alpha"的最后防线，收紧的统计代价（偶杀真策略）由 R4.5 迭代通道（最多 2 轮）与人工豁免通道兜底。

## 随机种子纪律

MC seed = SHA-256(trades.csv 字节 + config.csv 字节) 前 8 hex，numpy Generator(PCG64)；seed、观测统计量、null 分布分位数（q05/q50/q95）全部写入报告。PYTHONHASHSEED 与主运行同规约。折边界纯日期确定性，无随机数。

## 哈希预验（fail-closed）

suite 启动先重算 config/daily_stats/trades 三件套 SHA-256 并与 R5 证据绑定哈希逐一比对；任一不匹配 → `EVIDENCE_INCOMPLETE`，R5.5 不启动、无报告产出、台账留痕。suite 对 R5 工件只读。

## 迭代语义

初评 = 第 1 次评估；FAIL → 回 R3 修复（修复使 R4/R5 哈希失效，重走 R4→R5→R5.5）→ iteration_count+1；**第 3 次评估仍 FAIL → stage=ROBUSTNESS_FAILED，formal_publish_allowed=false，终止**（封堵"迭代至过验"的自适应过拟合）。全部历史评估入台账。
