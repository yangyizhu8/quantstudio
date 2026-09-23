# R2.5 客户确认包 — ou_reversal_csi300_10

> 阶段：R2.5（Explicit customer hard confirmation）
> 设计契约：`output/generated_strategies/ou_reversal_csi300_10/agent_strategy_design.json`（design_version 2.3，R2 修订稿 v2 已送审通过）
> 中文策略名：**沪深300均值回归超跌反弹** → `quantstudio/backtest/strategies/沪深300均值回归超跌反弹.py`
> 随卷凭证：`R2_E1_EVIDENCE_APPENDIX.md`
> 生成时间：2026-09-22

## 0. 一句话策略摘要

在沪深300 成分股里，剔除次新/科创/北交/停牌/涨跌停/近3日大跌/高波动/低价/低成交/弱趋势的股票，
按「距 60 日均线的相对偏离度」降序取前 10 只等权持有；同时用大盘 ADX 与布林中轨双条件择时，
不满足就清仓。**昨天收盘算信号，今天开盘照单买卖。**

## 1. 执行链（E1 定稿后）

```text
D-1 收盘  → 数据截止（之后不再变化）
D 日 handle_data（唯一决策回调）
   get_history(..., fq='pre', include=False)  → 读到 D-1
   get_index_day_bar('000300', count=need+1)  → 丢弃含 D 的行，只用至 D-1
   计算：择时门 → 五层过滤 → OU 因子 Top10
   下单：先卖后买，撮合价 = D 日开盘价（match_price_mode='open'）
```

## 2. 十三项近似（人话 + 影响）

| 编号 | 近似（人话） | 影响 |
| --- | --- | --- |
| A-1 | 今天开盘买什么，是用昨天收盘的数据算出来的；成交价 = 今天的开盘价 | 即「隔夜决策、开盘执行」；若改当天收盘价成交，收益特征会明显不同 |
| A-2 | 涨跌停用前复权价算涨跌幅，不用原始价字段 | 原始价字段有约 0.43% 行级噪声，反而可能误判 |
| A-3 | ST 用官方改名记录推导的字段（库里老 isST 字段全空，不可用） | ST 剔除可靠、不漏剔 |
| A-4 | 框架只能设一个佣金率，做不到买千三/卖千四分开 | 取往返等效 0.00324：实际买 0.325% / 卖 0.375%，往返正好 0.700%；单边偏差 ±0.025% |
| A-5 | 沪深300 指数 2026-08-03 缺一天数据（窗口 287 开市日中 286 天有数据，缺口仅此 1 天） | ① 基准曲线该日回落到 100（尖点），不影响买卖；② 受影响的是**执行日 2026-08-04**（信号日 08-03 缺失）→ 择时门消费末行为 2026-07-31（旧 1 个交易日，无穿越）；其余执行日不受影响 |
| A-6 | 沪深300 成分名单 2021-04~2025-06 缺失 | 原起点会让 2025 上半年 117 个交易日用 2021 年 3 月旧名单（差 4 年）→ 窗口改为 2025-07-01 起（287 交易日） |
| A-7 | 候选不足 10 只时按实际数量等权满仓 | 单只权重可能超过 1/10（C3-B 裁定） |
| A-8 | 「上市不足 100 天」用交易所真实上市日 | 2018 年后上市 1988 只中仅 2 只库内首日不同，最大差 8 天 |
| A-9 | 涨跌停幅度按板块档位（主板 ±10%、创业板 ±20%） | ST ±5% 档不影响（已在前面剔除） |
| A-10 | 信号用前复权价，成交/记账用原始价 | 框架标准口径，价格空间分离 |
| A-11 | 清仓后次日立即重建，无冷却期 | 换手可能较高（C4-A 裁定） |
| A-12 | 拒单（涨跌停/停牌/资金不足）框架直接拦，不强平不补单 | 实际持仓与目标有偏差，回测如实反映 |
| A-13 | 指数接口在日线模式下返回当天数据，必须丢弃当天行 | 防未来函数关键，R4 硬断言 A4-1 锁定 |

## 3. 七项确认键（需逐条原话回复）

| # | 确认键 | 建议回复文本 |
| --- | --- | --- |
| 1 | generation_target | generation_target：仅本地 QuantStudio 策略，R5 由 agent 执行 |
| 2 | strategy_semantics | strategy_semantics：沪深300 成分池 + 五层过滤 + OU 因子取前 10 等权 + 大盘 ADX/布林中轨择时；昨天收盘算、今天开盘买 |
| 3 | portfolio_contract | portfolio_contract：等权 10 只、敞口 97%、缓冲 3%、候选不足按实际数量等权、无杠杆 |
| 4 | rebalance_funding_contract | rebalance_funding_contract：先卖后买，同批卖出资金可用于买入 |
| 5 | r5_deployment_invariants | r5_deployment_invariants：建仓日至少 8/10 成交、敞口≥0.8、现金≤0.2、资金不足拒单≤2；无成交日只记轻量信号行 |
| 6 | execution_approximations | execution_approximations：A-1 ~ A-13 总体确认 |
| 7 | component_plan | component_plan：三钩子 + 11 个 API + E1 合规四项 + 参数冻结 |

## 4. 组件计划要点

- 生命周期：`initialize` / `handle_data` / `after_trading_end`（不使用 `run_daily`；daily-bar-v1 下 09:31 会被校验器 BLOCK）
- 11 个 API：set_benchmark · set_commission · get_index_stocks · get_stock_info · get_stock_status · get_history · get_index_day_bar · get_positions · get_position · order_target_value · log.info
- E1 合规四项：include=False 零例外 / match_price_mode='open' 显式 / 零 data[code] 依赖 / 指数行限定 D-1
- 参数冻结：上市 100 自然日 · 3 日跌幅 8% · 波动率 30% · √250(ddof=1) · 价格 2 元 · 均额 1000 万 · 个股 ADX 20 · 市场 ADX 25 · 布林 20 · MA60 · 持仓 10 · 成本 0.00324 —— 全部来自你的提示词或裁定，未做任何寻优

## 5. 输出路径与验证计划

- 设计契约（工作区）：`output/generated_strategies/ou_reversal_csi300_10/`
- 正式发布路径：`quantstudio/backtest/strategies/沪深300均值回归超跌反弹.py`（**R6 才产生**；R5 阶段不向该目录写任何文件）
- R4：`validate_agent_strategy.py --target-profile quantstudio` 0 BLOCK + 4 条断言（A4-1/2/3/4）
- R5：agent-managed，窗口 2025-07-01..2026-09-01，资金 100,000；证据含三件套 SHA-256 + 第二次独立进程重跑
- R5.5：287 交易日 5 折 ≈ 57 交易日/折；`INSUFFICIENT_SAMPLES` 如实呈现、不豁免

## 6. 随卷凭证

- `R2_E1_EVIDENCE_APPENDIX.md`：E1 双落位 SHA 表 + 用户批复 verbatim + 四格实测 + 治理链闭环记录
- `R2_AGENT_COMPONENT_PLAN.md`：组件计划 v2（含 v1→v2 变更对照表）
- `agent_workspace/ou_reversal/`：R0 台账 / R1 证据 / 21 个探针脚本
