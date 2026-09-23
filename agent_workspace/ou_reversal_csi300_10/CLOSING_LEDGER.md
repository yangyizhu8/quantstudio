# 收官台账 — ou_reversal 全链路（ou_reversal_csi300_10 / 沪深300均值回归超跌反弹）

> 策略线收官记录｜2026-09-23｜策略线会话（agent-managed）
> 策略中文名：**沪深300均值回归超跌反弹**；机器标识：`ou_reversal_csi300_10`

## 1. 全链路状态

| 阶段 | 状态 | 关键产物 / 证据 |
| --- | --- | --- |
| R0 | COMPLETED | `agent_workspace/ou_reversal_r0/flowchart.html`（17 节点）；C1-C12 裁定 verbatim 入 `agent_workspace/ou_reversal/workspace_state.json` |
| R1 | CLOSED | `R1_capability_evidence.md` + `capability_report.r1.json`；D1-D4 裁定 |
| R2 / R2.5 | COMPLETED | `agent_strategy_design.json`（design 2.3，14 条近似全 confirmed）；`R2_5_CONFIRMATION_PACKAGE.md` |
| R3 / R4 | COMPLETED | `strategy.py`（503 行）+ `local_validation_report.json`（PASS / 0 BLOCK / 1 WARN） |
| R5 | **PASS** | `R5_BACKTEST_EVIDENCE.md`：287 交易日；G3.5 三件套 + canonical + DB 逐位一致；部署不变量 0 FAIL；0 ERROR |
| R5.5 | CUSTOMER_WAIVED | 客户风险接受型发布（verbatim 入 `confirmation_evidence.robustness_exemption`） |
| R6 | **PUBLISHED** | `quantstudio/backtest/strategies/沪深300均值回归超跌反弹.py`（sha256 `9cfb26e5…`） |
| 转换管线 | **PASS** | `output/ptrade_export/沪深300均值回归超跌反弹/`：`source_import=PASS`、`api_portability=PASS`、`get_index_day_bar` DENY-SHIM 生效、0 BLOCK |

## 2. E1 治理链（由一次 R2 时序契约错误沉淀为框架层铁律）

| 环节 | 内容 |
| --- | --- |
| 阻断发现 | R2 送审暴露「15:00 含 T」声明与引擎源码矛盾（三段源码证据） |
| 四格实测 | `probe_include_semantics2.py`：`include=False` 锚定 `prev_date`；`include=True` 在 09:31 语境含当日全日 bar |
| 双向纠偏 | 审核方先例引用核频率域（first_cover 为 1m 分钟豁免域，非日线先例）；我方「信号后移一交易日」为伪代价（标签换位错觉） |
| 原则定稿 | E1 七条（含专门 API 消费边界）；A′（proxy 15:00 日线 include=True 豁免）**未进入实施**，登记为治理转向 |
| 双落位 | `SKILL.md` + `references/no-lookahead-rules.md`（项目内 + 用户级，SHA 逐位一致）+ `AGENTS.md` 铁律节 |

## 3. 框架修复（本批）

| # | 缺陷 | 根因 | 修复 | 验收 |
| --- | --- | --- | --- | --- |
| F2-A | 接线层 no-op（`delta_below_one_lot`）不进 QS_FILL_AUDIT → 对账缺口 | `_qs_noop_target` 绕过引擎 `_finalize_immediate` | `ptrade_api.py` 新增 `_qs_report_noop` + 调用点 | `docs/evidence/f2a-…-20260923.md`；A/B：改动前 287 行审计含该明细 0 行 → 改动后 44 行含 39 行；4 条常设单测 |
| F3-A | E1 合规的 `open` 设计被 `design_metadata` 判 INVALID_PROFILE → GUI 转换 BLOCK | `_profile_combo_valid` 硬编码 `mp == "close"`（早于 E1-2） | `design_metadata.py` 单处改为 `mp in ("close", "open")` | `docs/evidence/f3a-…-20260923.md`；`INVALID_PROFILE` → `RESOLVED`；不传 `--engine-profile` 亦通过；2 条新单测；全框架同类硬编码复扫归零 |

## 4. 案例库新增（本周期沉淀）

| # | 教训 | 来源 |
| --- | --- | --- |
| C-1 | **四格实测纠偏**：接口语义断言必须附实测证据，不得由源码阅读直接写成契约 | R2 阻断 |
| C-2 | **「信号后移」伪代价**：跨链比较时须先对齐日历标签（S/D 记法），否则把同构链误判为有损 | E1 纠偏 |
| C-3 | **先例引用须核频率域/语境域**：`include=True` 先例属分钟域，不能证明日线域已豁免 | 双向纠偏 |
| C-4 | **路径①不复制引擎规则**：能用引擎真值判定的，不要在策略侧重算（避免规则漂移） | F1-B 实施 |
| C-5 | **R1 最小可交易性检查（S-1）**：能力核验阶段即须比对「单只预算 vs 池内一手价格分布」，避免 R3→实测→回退整轮返工 | F1 发现 |
| C-6 | **原则级变更须触发全框架硬编码假设扫描**：E1-2 放宽 `open` 后，框架内仍以 `mp == "close"` 写死旧假设，单测全绿也未能暴露 | F3-A 发现 |

## 5. 遗留项（已登记，非挂账）

| # | 项 | 处置 |
| --- | --- | --- |
| S-1 | skill R1 检查清单增加「最小可交易性检查」 | 随下个 skill 维护批走流程（C-5） |
| S-2 | `QS_REBALANCE_AUDIT.submitted` 计数口径明确为「所有提交次数」 | 落在 skill 标准层（生成模板/审计契约），**不得改单个策略源码**；随下个 skill 维护批 |
| S-3 | `inspect_capabilities.py` 成分覆盖只校验首尾、漏中段空档 | 最小改进方案（complete 快照序列空档检测）随下个框架维护批 |

## 6. 数据源说明（R5 外部库覆盖）

| 项 | 值 |
| --- | --- |
| 设计声明库 | `data/quantstudio.db` |
| R5 实际库 | `data/quantstudio.old_20260920.db`（`external_db_override_confirmed=true`，客户批准） |
| 等价性 | 表数 101；窗口内 287 开市日 / 286 指数日（含同一 2026-08-03 缺口）/ 15 个 complete 快照；前复权锚点抽检逐位一致 |
| 被否决备选 | `data/quantstudio_backup_20260912.db`（指数止于 2026-07-31，窗口内仅 265/287 指数日） |

## 7. 策略绩效（事实陈述，客户已裁定盈亏非目标）

| 指标 | 值 |
| --- | --- |
| 总收益率 | **−29.29%** |
| 基准（沪深300） | +17.16% |
| 超额 | **−46.45 pp** |
| 最大回撤 | −30.75% |
| 持有/空仓天数 | 94 / 193（空仓 67.2%，择时门关闭） |
| 成交笔数 | 695 |

> 客户原话：「发布，我的意图旨在测试回测功能和转换ptrade管线的双端对齐功能，策略本身盈亏不重要。」
