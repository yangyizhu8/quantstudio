# QuantStudio 策略生成 Skill 统计鲁棒性验证体系（Phase 1）设计方案

- skill：quantstudio-strategy-compiler ｜ 目标版本：`0.9.0-r55-robustness`（compiler/package baseline `0.3.2-mvp` 不变；新增契约 `robustness_report 1.0`）
- 日期：2026-08-28 ｜ 状态：终审通过（ZCode；M1-M4 已并入）
- 上游裁定（已批准）：R4.5→R5.5 映射；两期实施（Phase 1 先行）；G1-G6 默认强制 + verbatim 豁免；阈值照 A 原版（含 G6 p<0.01 收紧理由存档、ETF 路由 G3 放宽 0.25）
- 修订记录：2026-08-28 审计意见 M1-M4 并入（折内零交易规则 / G6-G1G3 维度声明 / 每折报告四字段 / run_card 升位兼容）

## 1. 问题定义

quantstudio-strategy-compiler 现有验证体系覆盖工程契约域（R4 静态校验、R5 运行时证据——哈希绑定 + G3.5 复现门禁 + 逐期部署不变量、R6 门控发布），缺失统计鲁棒性域：策略收益的统计显著性（MC）、样本外稳定性（WF 时序折）、绝对质量门（G1-G4）。本设计将 simple-quant-factory 的验证体系以**纯增益**方式植入：新增 **R5.5 阶段**（R5 与 R6 之间）。Phase 1 范围 = WF 5 折 + MC n=1000 + G1-G6 + 迭代上限 2 轮；Optuna/网格/WF 嵌套寻优为 **Phase 2**（另行方案、另行审计）。

非目标（Phase 1 明确不做）：参数寻优、模板（templates/）改动、引擎/转换管线（source_import）/PyQt/既有校验器（validate_agent_strategy.py 等）改动、R0-RE 回撤保护层、evidence 2.1 契约变更。

## 2. 改动范围

**新增 4 文件**（全部位于 skills/quantstudio-strategy-compiler/）：
1. `scripts/run_robustness_suite.py` — R5.5 编排器：哈希预验 → WF 折运行 → MC 解析 → 门控判定 → robustness_report.json；CLI fail-closed；
2. `schemas/robustness_report.schema.json` — v1.0（**每折记录必须含四字段：fold_window(start/end)、trade_days、round_trips、no_trade_flag**——支撑验收 1 人工复算）；
3. `references/robustness-gates.md` — 阈值表 / MC 方法 / 折切分规则（含零交易折规则）/ round-trip 配对规范 / G6 收紧理由存档；
4. `scripts/robustness_selftest.py` — 机器可跑自检（合成夹具覆盖验收 2/3/4 项；`--all` 一键）。

**修改 4 文件**：
5. `SKILL.md` — 新增 R5.5 阶段章节（入口/出口门）、绝对规则 33-35、Prohibited 补条、Commands 补条、R6 发布门更新、skill 版本行与契约版本行更新；
6. `schemas/run_card.schema.json` — stage 枚举增加 R5.5 与终态（小版本升位）。**升位兼容声明（M4）：旧 workspace（ledger 无 robustness 字段）读取兼容——缺字段视为未进入 R5.5，不报错**（与豁免路径语义一致，防历史 workspace 被新代码误判）；
7. `scripts/create_agent_workspace.py` — ledger（workspace_state.json）增加 robustness 字段（stage/iteration_count/history[]）；
8. `scripts/publish_agent_strategy.py` — R6 发布门：ledger 无 R5.5 PASS / EXEMPTED 证据 → 拒绝发布（fail-closed）。

**实施期文档同步（铁律义务）**：README 策略工具箱章节、docs/strategy_toolbox.md 中流水线阶段表述（R0→R6 变 R0→R6 + R5.5）。

**明确不改动**：回测引擎、转换管线、PyQt、validate_agent_strategy.py、validate_runtime_shapes.py、templates/、既有 R5 证据契约（user_backtest_evidence 2.1）。

## 3. R5.5 阶段定义

**位置与触发**：R5 PASS 之后、R6 之前。**豁免**：`design.validation_contract.robustness_gates.enabled=false` 且 R2.5 verbatim 确认（customer_text + 时区感知时间戳 + source=customer_reply）→ 台账 EXEMPTED，R6 放行（豁免路径下管线行为与现状一致）；默认 `enabled=true`。豁免不产报告，台账记录豁免证据指针。

**入口校验（fail-closed，先于一切分析）**：
- 重算 config.csv / daily_stats.csv / trades.csv 的 SHA-256，与 R5 证据绑定哈希逐一比对；任一不匹配 → `EVIDENCE_INCOMPLETE`，R5.5 不启动、无报告产出、台账留痕；
- config.csv 声明窗口与证据声明窗口一致性核对。

**出口门**：全部可评估门 PASS（INSUFFICIENT_SAMPLES 不阻断但强制显著呈现）→ R5.5 PASS；任一 FAIL → R5.5 FAILED（进入 3.5 迭代语义）。报告与全部工件哈希留痕。

### 3.1 WF 5 折规则（钉死）

- 折轴 = 主运行 daily_stats.csv 交易日序列（权威日期轴），N = 总交易日数；
- **时序连续、不重叠、禁止随机打乱**；base = N//5，余数 r = N%5，**最早 r 折各多 1 日**（确定性余数归最早折）；
- **窗口下限**：N < 250 交易日 → 跳过折运行（省算力），G5 = INSUFFICIENT_SAMPLES 注明原因；G1-G4/G6 照常评估；
- **折配置派生**：每折 = 一次引擎运行，配置**从哈希验证后的 config.csv 程序化派生、仅替换起止日期**——费率/滑点/close 模式/engine_profile/fidelity 关闭/资金/ETF T+0 语义全部继承，单一事实源，结构上杜绝折间漂移；
- 折内策略状态独立（每折空仓起、资金同主运行）——与主运行对应切片存在状态差异，属 WF 固有特性，报告强制声明；
- 折起点前的 provider 历史照常可用于指标预热（规则 24 语义），不构成前视；
- 折边界纯日期确定性，无随机数；PYTHONHASHSEED 与主运行同规约；
- **折内零交易规则（M1，钉死）**：折内 round trips = 0 或全程空仓（折收益无法计算）→ 该折标记 `NO_TRADE`，**不计入 G5 正窗口分母**——防止零交易折因"超额 = 0 不 > 0"被系统性计为负窗口而压低 G5（WF 实现最常见隐性 bug 源）；**G5 有效折数（非 NO_TRADE 折）< 3 → G5 = INSUFFICIENT_SAMPLES**；
- **OOS 正窗口定义（裁定）**：折超额收益 = 折策略区间收益 − 折基准区间收益（同折交易日轴；基准 = design 的 set_benchmark 指数经 provider 取收盘序列；未声明 → 缺省 000300.SS；基准数据不可得 → G5=INSUFFICIENT_SAMPLES 注明）；正窗口 = 有效折中超额 > 0 的折；G5 PASS ⟺ 正窗口数/有效折数 ≥ 60%；
- **每折报告四字段（M3，schema 强制）**：`fold_window(start/end)`、`trade_days`、`round_trips`、`no_trade_flag`——验收 1（week10 回放）的人工复算以这四字段为基准；每折 config/daily_stats/trades 的 SHA-256 同步绑定进报告。

### 3.2 MC n=1000 规则（钉死）

- 输入：主运行 NAV（哈希已验）→ 日收益 r_t = NAV_t/NAV_{t-1} − 1；超额日收益 e_t = r_t − b_t（b_t 来源同 3.1；基准缺失 → G6=INSUFFICIENT_SAMPLES）；
- 方法：**平稳块自助法**——块长固定 21 交易日（月频策略约一个月相关长度），n=1000 次环形重采样；统计量 = 重采样超额日收益均值；H0 = 均值 ≤ 0；**p = (1 + #{重采样均值 ≤ 0}) / (1 + 1000)**（加一校正）；G6 PASS ⟺ p < 0.01；
- **维度显式声明（M2）**：G6 检验的是**超额维度的统计显著性**（对基准的超额日收益均值）；G1-G3 为**绝对收益维度**指标。两门独立评估：策略绝对收益为负但跑赢基准时，G1 FAIL 而 G6 可能 PASS——**属设计语义而非矛盾**，报告按门独立呈现，week10 回放验收时按此解读；
- **收紧理由存档**（写入 references/robustness-gates.md，源自 A v2.12，2026-07-04）：多重检验防御——按年研发 20 策略计，p0.05 = 1 个期望运气通过，p0.01 = 0.2 个；
- 样本下限：NAV 日数 < 120 → G6 = INSUFFICIENT_SAMPLES；
- **随机种子（全记录）**：seed = 主运行 trades.csv+config.csv SHA-256 前 8 hex 派生（确定性可复现），numpy Generator(PCG64)；seed 与观测统计量、重采样分位数全部入报告；
- **0 次引擎重跑**（解析法）。

### 3.3 G1-G6 门控表（阈值照 A 原版，Phase 1 固定、不可按设计覆盖）

| 门 | 阈值 | 数据源 | INSUFFICIENT_SAMPLES 条件 |
|---|---|---|---|
| G1 | 年化 > 0%（NAV 比值法 (nav_end/nav_start)^(252/n)−1，绝对维度） | daily_stats | n<60 日 |
| G2 | 最大回撤 < 25%（NAV 峰谷，绝对维度） | daily_stats | n<60 日 |
| G3 | 夏普 > 0.5（ETF 策略 > 0.25）= mean(r)/std(r)×√252，rf=0，绝对维度 | daily_stats | n<60 日 |
| G4 | 胜率 > 40%（round-trip 净收益口径，见 3.4） | trades.csv | round trips < 10 |
| G5 | WF 正窗口 ≥ 60%（**有效折**=非 NO_TRADE 折中超额 > 0 占比） | 5 折报告 | N<250 / 基准缺失 / **有效折 <3** |
| G6 | MC p < 0.01（块自助法，超额维度） | daily_stats + 基准 | <120 日或基准缺失 |

ETF 判定：design universe_contract（get_etf_list_local / 显式 ETF 池）→ G3 阈值 0.25（仅对 ETF 池策略生效）。判定结果三态：PASS / FAIL / INSUFFICIENT_SAMPLES（不阻断、强制显著呈现并注明原因）。

### 3.4 G4 胜率公式（裁定钉死）

- **Round trip 定义**：同标的、同方向（本框架仅多头）的开平配对；**FIFO 配对**——每笔卖出按 FIFO 抵扣最早未平买入批量，一笔卖出可拆多对；期末仍持有的买入不计；
- **pnl** = 卖出净额 − 配对买入净成本（trades.csv 含费率字段则含费；否则价格差口径并在报告标注口径版本——实现期核对实际列后钉死，合同层两态均定义）；
- 胜 = pnl > 0；胜率 = 胜对数 / 总对数；**总对数 < 10 → G4 = INSUFFICIENT_SAMPLES（不 FAIL——保护周频/事件驱动小样本策略，如断板反包类）**。

### 3.5 迭代语义（最多 2 轮）

- 初评 = 第 1 次评估；FAILED → 回 R3 修复（既有纪律：任何修复使 R4/R5 哈希失效 → 重走 R4→R5→R5.5）→ iteration_count+1；
- iteration_count ≤ 2 允许迭代；**第 3 次评估仍 FAIL → 不再迭代，stage=ROBUSTNESS_FAILED，formal_publish_allowed=false，终止**（同时封堵"迭代至过验"的自适应过拟合）；
- 台账：workspace_state.json.robustness = {stage, iteration_count, history:[{date, overall, gates, report_hash}]}，全部历史评估留痕。

## 4. 影响面

- **管线行为**：仅新增 R5.5；R0-R5 主契约零变化；R6 仅新增发布门检；豁免路径与旧 ledger 兼容路径下与现状逐位一致；
- **性能**：新增 5 次折引擎运行 + 0 次 MC 重跑（日频单折秒~分钟级）；Optuna 不在本期，无寻优算力面；
- **兼容**：既有策略与历史证据不受影响；旧 workspace ledger 缺 robustness 字段读取不报错（M4）；user-PyQt 模式用户零额外负担（折跑 agent 侧执行）；
- **依赖**：numpy/pandas（脚本区既有），**零新增硬依赖**（Optuna 属 Phase 2）。

## 5. 验收标准（七项，含裁定四项）

1. **week10 既有证据回放**：对既有 R5 三件套运行 suite → 产出 robustness_report.json（schema 校验 PASS），G 表数值以每折四字段（fold_window/trade_days/round_trips/no_trade_flag）为基准可人工复算核对；G1 FAIL + G6 PASS 组合按 M2 维度语义解读；
2. **哈希篡改注入**：副本翻转 trades.csv 一字节 → EVIDENCE_INCOMPLETE、非零退出、台账留痕、无报告产出；
3. **MC p 值方向对照**：合成序列三组——正均值→p<0.01、零均值→p 高位、负均值→p→1——方向全部正确（selftest 内建）；
4. **迭代上限生效**：iteration_count=2 且输入 FAIL → 拒绝第 3 轮 → ROBUSTNESS_FAILED 终止 + formal_publish_allowed=false；
5. `robustness_selftest.py --all` 全绿；`quick_validate.py` PASS（PYTHONDONTWRITEBYTECODE=1）；
6. **R6 门检注入**：无 R5.5 PASS/EXEMPTED 证据 → publish 拒绝；
7. 验收结论写入 `docs/evidence/robustness-gates-<date>.md`（含命令与输出摘录）。

## 6. 回退条件

- 任一验收不过 → 全部改动文件 git checkout（实施前记录 HEAD 干净点），零残留；
- suite 运行期内部失败 → 台账停 R5.5 FAILED + 原因；suite 对 R5 工件**只读**，主运行证据与报告目录零污染；
- 豁免路径始终可用（客户主权），不依赖 suite 可用性。

## 7. 实施序（终审通过后）

1. schemas + references（契约先行）→ 2. run_robustness_suite.py + robustness_selftest.py → 3. SKILL.md（R5.5/规则 33-35/Prohibited/Commands/R6 门/版本行）→ 4. run_card schema（含 M4 兼容声明）+ ledger + publish 门 → 5. 七项验收 → 6. 证据文档 + 汇报 → 用户确认 → 双仓库推送。文档同步（README/strategy_toolbox.md）随第 3 步履行。
