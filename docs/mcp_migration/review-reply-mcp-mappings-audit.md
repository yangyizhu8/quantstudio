# MCP 映射配置审核处理意见（回复 ZCode 中期汇报 + 全量映射审计）

> **日期**：2026-08-03
> **审核方**：Codex（本地 QuantStudio 实测核实）
> **核实方式**：本地 QuestDB（127.0.0.1:9000）逐表查列结构与行数 + DuckDB 对照 +
> 三方对照审计（canonical schema ↔ alignment_rules mcp column_map ↔ QuestDB 实际列），
> 审计脚本：scripts/_audit_mcp_mappings.py（修复后可重跑回归）

---

## 一、ZCode 报告的两个问题：决策意见

### 问题1：industry_classification ← sw_classify —— 批准修复，补一个对齐要求

实测确认诊断准确：云端 sw_classify 8 列（index_code/industry_name/industry_code/
parent_code/level/is_pub/src/ingest_time），canonical 期望 9 列。映射方案可行：

- classification_version ← src（实测全为 "SW2021"，与 tushare 路径 index_classify(src="SW2021") 同口径 ✓）
- classification_system ← 常量 "SW"
- industry_level ← level、parent_industry_code ← parent_code
- effective_from ← 0、effective_to ← NULL、update_time ← ingest_time

**补充要求（ZCode 未提）**：tushare 路径只拉 L1（index_classify(level="L1")，且有
SW2021_L1_EXPECTED_COUNT=31 门控），而云端 sw_classify 有 511 行
（L1 31 + L2 134 + L3 346）。MCP 映射应先**过滤 level='L1'** 与传统模式对齐
（回测消费方按传统模式内容设计）；是否纳入 L2/L3 作为增强项另行决策。

### 问题2：industry_membership ← sw_weight —— 反对选项 1/3，采纳修订版选项 2

语义错配诊断完全正确。对三个选项的裁决：

- **反对选项 1**（passthrough 建 industry_membership 同名表）：违反 passthrough 同名原则
  （QuestDB 表名是 sw_weight），且造出与 canonical schema 完全不兼容的同名表——回测
  get_industry 查询 classification_system/industry_code/effective_from 会因列不存在直接报错，
  比没有表更糟（fail-closed 检查认表存在即走查询路径）。名字污染，最差选项。
- **反对选项 3**（映射但标注语义不同）：canonical schema 注释明写"禁止用当前快照回填历史"，
  权重快照写进 PIT 表即数据投毒，文档标注救不回来。
- **采纳选项 2（停更）+ 两条补充**：
  1. **sw_weight 以本名 passthrough 入库**——实测有独立价值：31 个 L1 行业指数、
     日频快照 2024-01-02→2026-07-31、每日权重和=100 完整成分。注意它目前**不在**
     _PASSTHROUGH_TABLES 中，只被错误映射消费，停映射后必须补进 passthrough 清单。
  2. **停更影响写明**：统一库下传统模式已写入的 industry_membership 历史仍在，
     get_industry 不会 fail-closed；MCP 模式下新上市/新归属调整停更，记为已知限制。

**补充实测结论：sw_weight 不能用于推导成员历史**，三条硬伤：
(a) 仅 31 个 L1 指数，无 L2/L3；(b) 快照自 2024-01 起，tushare index_member 历史可溯至 90 年代；
(c) 数据质量问题——成员数隔天 449↔611 跳变（801030.SI 实测），非真实调整，
推导 in/out 区间会产生海量伪区间。
**正确解法：生产端补建成员历史表**（tushare index_member 全量拉取 → 本地 QuestDB → 同步上云），
建议立为后续任务项（系统 A 侧，不阻塞本轮）。

---

## 二、全量映射审计新发现（17 张映射表中 11 张有问题）

ZCode 的 2 张之外，审计又发现 9 张问题表，分三类。

### 类别1：语义错配（新发现 2 张，与 industry_membership 同类）

**③ sw_industry ← sw_daily**：sw_daily 是申万行业指数日线行情
（open/high/low/close/pe/pb/total_mv，ts_code 为 801xxx.SI 指数代码），不是股票-行业分类。
映射引用的 industry_code/industry_name/industry_level/update_time 在 sw_daily 中一列都不存在。
处理：mcp 的 sw_industry 任务停更（canonical sw_industry 本为 legacy audit-only）；
sw_daily 改走本名 passthrough（行业指数行情，有独立价值）。

**④ stock_suspend_d ← stock_suspend（双重错误）**：
(a) 源表名错误——QuestDB 无 stock_suspend，实际表名即 stock_suspend_d（110,515 行）；
(b) 列模型不同——实际列为 ts_code/trade_date/suspend_timing/suspend_type（每日停牌状态表），
映射期望 suspend_date/resume_date/suspend_reason（停牌区间表）。
处理：重写映射为状态模型，或停更该任务。

### 类别2：映射整体缺失（新发现 3 张，P0，最优先）

**⑤ income_statement / cashflow_statement / fin_indicator**：
三表均在 _MCP_SUPPORTED 且任务 enabled，但 source_mappings.mcp **无映射条目**。
已核实 aligner._get_mapping（aligner.py:457）查不到映射直接 raise KeyError——
**enabled 状态下 daemon 每轮必崩**。修法：照 balance_statement 模板写 PIT 映射
（云端 stock_income/stock_cashflow/stock_fina_indicator 均为 tushare 风格列名，
ts_code→code + ann_date/f_ann_date/end_date/report_type/comp_type/end_type）。

### 类别3：column_map 引用不存在的列（陈旧映射，6 处）

| 表 | 问题 | 修法 |
|---|---|---|
| ⑥ index_constituents | 引用 stock_code，实际为 ts_code | 改 ts_code→code |
| ⑦ index_daily | 引用 volume，实际为 vol；pct_chg→pct_chg 目标名应为 pctChg；pre_close 存在未映射 | 改 vol→volume、pct_chg→pctChg、补 pre_close→preClose |
| ⑧ balance_statement | 引用 update_time，stock_balancesheet 无此列 | 删除该项；建议顺手把 canonical 14 个可选科目列（total_assets 等，QuestDB 同名存在）扩进映射 |
| ⑨ stock_dividend | 引用 update_time，ws_exdiv 无此列 | 删除该项；其余可选列云端确实没有（ws_exdiv 仅现金分红），_note 已声明，可接受 |
| ⑩ etf_basic | identity 声明不实（与 industry_classification 同病）：QuestDB 实际列 ts_code/name/extname/index_code/index_name/etf_type/list_date/list_status 等，canonical 要求 classification_method/classification_version/code/fund_type/is_cross_border/tracking_index | 写真实映射：tracking_index←index_code、fund_type←etf_type、code←ts_code(code_format)；classification_* 与 is_cross_border 参照 tushare 路径派生逻辑补常量 |
| ⑪ stock_minutes / etf_minutes | freq 不在 column_map（QuestDB 有 freq 列）；P3-4 能跑通说明运行时有别处补齐，非故障 | 建议补 "freq": "freq" 保持映射自洽 |

### 类别4：内容差异（可接受，写明即可，不改）

- stock_daily 的 peTTM/psTTM/pcfNcfTTM/pbMRQ 在 MCP 模式为 NULL（数据在 stock_daily_valuation 中），消费方改用估值表。
- stock_dividend 仅现金分红（ws_exdiv 无送股/配股列），_note 已声明。

---

## 三、配置卫生问题（3 项）

1. **stock_namechange 有映射无 task（dead config）**。该表为新建表（7451 行，1994 年至今，
   前一日 109 表快照中尚无），建议补 task。**同时必须确认 sync_to_cloud 配置已覆盖**——
   sync 表清单是静态的（realtime 55 + slow 49 + full_only 5 = 109），stock_namechange 是第 110 张，
   未加 sync 配置则云端无此表，MCP 拉取 404。生产端以后每建新表都要同步 sync 配置，建议写入运维清单。
2. **passthrough 清单中的 factor_value 和 daily_info 疑似生产系统内部状态表**
   （与已排除的 decision_log/qfq_checkpoint 同类），请确认是否有意同步给客户。
3. sw_weight 停映射后补入 _PASSTHROUGH_TABLES（见问题2 补充 1）。

---

## 四、审计通过项（无需改动）

- passthrough 67 张表全部在 QuestDB 存在 ✓
- 任务与支持矩阵一致（所有 task 的表均在支持范围内；14 张大表 disabled 与汇报一致）✓
- 完全干净的映射表：stock_daily_valuation、trade_calendar、etf_daily、stock_namechange ✓

---

## 五、前序 P0 待回应项（请在下一轮汇报逐条回应）

1. **server 侧 per-key scope 白名单**：67 张 passthrough 是否都在当前 key 的
   validate_access/list_datasets scope 内？未实测前不得认为可拉。
2. **passthrough 写库语义**：upsert/全量覆盖、主键、水位推进、大表 export 日期过滤列，需定稿。
3. **67 张 passthrough 排除清单**：测试表/内部状态表/空表的排除明细需落清单逐项核对
   （76→67 收敛了 9 张，与应排除量约 24 张对不上）。

---

## 六、修复优先级

| 优先级 | 内容 |
|---|---|
| P0 | 类别2 三张财务表补映射（KeyError 必崩）；类别1 两张错配表停更/改 passthrough |
| P1 | 类别3 六处陈旧列名修正（含 etf_basic 真实映射）；sw_weight 补入 passthrough |
| P2 | 类别4 差异写明进已知限制；stock_namechange 补 task + sync 覆盖确认；factor_value/daily_info 确认 |

审计脚本 scripts/_audit_mcp_mappings.py 保留，修复完成后重跑应全部转 OK。
