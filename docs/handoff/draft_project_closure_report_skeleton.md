# 【草案】QuantStudio 全项目收官报告框架（2026-08-21 起草，WP8 前定稿）

> 定位：跨五任务最终验证总纲。逐任务核对 功能/产出/状态机/冻结项/待办，串联各 memory/handoff/*_closure.md。
> 状态：⏳ 框架草案，占位符〔待填〕，周一终验后填充，WP8 前落盘正式版。

## 1. 任务清单与收口状态总览
| 任务 | 负责智能体 | closure 文档 | 状态 | 终验结论 |
|---|---|---|---|---|
| QuantStudio 稳定化 | ZCode | 〔待产出〕 | 六步流水线推进中（快照→基线→推送） | 〔周一基线后〕 |
| QuestDB ETL/云端同步 | Trae | 〔待产出〕 | guard 开发+周末冻结首战 | 〔〕 |
| 分钟 qfq 复权修复 | Trae | 〔待产出〕 | P2 22 只执行中 | 〔〕 |
| 统一水位自愈 | Trae | 〔已 FINAL PASS〕 | FINAL | PASS（2026-08-20） |
| 本地云端数据管线终极解决（SEGMENT-2） | Trae+ZCode | 〔待产出〕 | 周日 12:00 开跑 | 〔周一 09:30 终验〕 |

## 2. 冻结项核销核对表（对接 F1-F5 批复，全部已批 2026-08-21）
| 冻结项 | 批复内容 | 批复日期/证据 | 核销状态 |
|---|---|---|---|
| F1 消费白名单退役 | 终验 PASS（含 P1-D 四方验收）后按计划退役 | 2026-08-21 / baseline_20260821_0040_dispatch.md | 〔终验后〕 |
| F2 560650.SH 占位 K 线 | 待重锚任务实测通过后清理 | 同上 | 〔重锚实测后〕 |
| F3 消费解冻总闸 | **批 B——不接线、随终验退役归档**；前提=终验黄金门覆盖 is_qfq=false 残留=0 校验（WP6.2 职责承接），模块+测试保留并在 docstring 标注决议 | 同上 | 〔终验黄金门确认后〕 |
| F4 周末代码冻结 | 范围=各任务代码分支；解冻=周一 01:05 随 SEGMENT-2（01:00 硬截止后） | 同上 | 〔周一 01:05〕 |
| F5 技术债 5 项 | 已批（2026-08-21），推送后逐项走流程排期 | 同上 | 〔推送后排期〕 |

## 3. 数据与基线证据链
- 快照：SNAP_001/002（pinned）→ SNAP_003（周六）→ verify/protect（周日）
- 修复批 1-3 证据 JSON 清单：batch1_reanchor_apply / batch2_sync_apply / batch3_apply
- 双端对齐：docs/evidence/dual-end-alignment-20260818.md；探针 v1-v5.1
- 黄金基线：〔周一+ 建立，产物路径待填〕

## 4. 登记表终态
- 全关闭清单〔待：周一复检后统计〕；known-noise 清单；外部核对清单（S1-2/3/4）

## 5. 六步流水线收官段（推送）
- 方案/审计/实施/验收证据索引 → 用户确认记录 → 双仓库推送 SHA + 两远程一致性核对〔待〕
- 同步完整性：README + docs/strategy_toolbox.md + docs/prompt_engineering.md〔核对清单〕
- **PTrade 平台对齐（P-D 系，2026-08-22 闭环）**：
  - A 组订单拆单接线：commit `6f263c3`（18 文件，+1749/-80；本地 order API 拆单包装 + 模板 5-API + 107→107 全绿 + 低价股拆单分支实证 buy_filled=10）
  - P-D9 池过滤语义一致化：commit `b000d9e`（7 文件，+517/-2；_QS_FILTER_STATUS_EXT 注入 + 10 测试用例，118 全绿）
  - 双端冒烟对账（2026-08-22 12:56/12:58）：选股重合 **1/5 → 4/5**（仙股 4 只+临界带 2 只全剔除）、重合标的股数逐只一致（统一链 px 同值强实证）、fail-open 失败 0 次；唯一席位差 600158/603272 = D3-X2 拥挤带（第 4-7 名 ret 1.2% 窄带）排序抖动
  - 证据链：docs/evidence/a1-wiring-20260822.md / pd9-filter-status-20260822.md / p-d9-pool-semantics-20260822.md；登记表 v1.37（P-D9 closed）
  - 防回潮（C 组）：validator `PTRADE-PLATFORM-FALLBACK-BAN` 覆盖 _normalize_date_str / _current_raw_price / **_is_delisting_risk**（P-D9 增量）/ g.last_close 模式

## 6. 遗留与技术债移交（推送后排期）
- 5 项技术债 + D2-F3 索证 + WP3/WP6/WP8 观察周→解禁周计划

## 7. 变更记录
- 2026-08-21 框架起草（总调度）
