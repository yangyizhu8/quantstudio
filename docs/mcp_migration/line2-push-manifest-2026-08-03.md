# 线2 推送前逐文件改动清单（codex 闭环#1）

> 生成时间：2026-08-03
> 目的：codex 要求推送前出完整逐文件清单（归属+证据），因工作区混杂两条线改动
> **当前状态：⚠️ 禁止推送**——线1 is_qfq=False bug 未修 + 两条线共享文件未拆分

## 工作区总览

- 13 个已修改文件 + 14 个未跟踪文件
- 混杂：线2（本轮 codex 审计修复）+ 线1（is_qfq 还原，workbuddy 未完成）+ 临时脚本

## 逐文件归属清单

### 线2 专有文件（仅线2改动，可独立推送）

| 文件 | +/- | 归属 | 改动内容 | 验证证据 |
|---|---|---|---|---|
| config/profiles/mcp_only/alignment_rules.json | +296/-8 | **线2** | 14处映射修复：财务表补PIT映射/错配表停更/陈旧列名6处/fin_indicator列名/stock_basic配置级/industry_classification column_map+code_col | 审计A段8OK+抽测6表PASS |
| config/profiles/mcp_only/collector_tasks.json | +2395/-26 | **线2** | 错配表停更删task + sw_daily/sw_weight补passthrough + namechange补task | task=86(17映射+69passthrough) |
| docs/mcp_migration/full-table-config-task.md | 新增 | **线2** | 线2任务书 | - |
| docs/mcp_migration/full_table_inventory.json | 新增 | **线2** | 86表清单 | - |
| docs/mcp_migration/is_qfq_restore-raw-task.md | 新增 | **线1任务书**（线2会话起草） | is_qfq还原任务书 | - |
| docs/mcp_migration/review-reply-mcp-mappings-audit.md | 新增 | **线2** | codex审计回复文档 | - |
| scripts/_audit_mcp_mappings.py | 新增 | **线2**（codex审计脚本） | 映射审计回归 | A段8OK/B空/C清 |

### 线1 专有文件（仅线1改动，待线1完成）

| 文件 | +/- | 归属 | 改动内容 | 状态 |
|---|---|---|---|---|
| docs/evidence/mcp_qfq_restore_verify_2026-08-03.md | 新增 | **线1** | 还原验收报告 | ⚠️待is_qfq=False修复后更新 |
| scripts/verify_mcp_qfq_restore.py | 新增 | **线1** | 黄金数字断言 | ⚠️A6b断言待翻转 |
| docs/qfq-production-enablement-checklist.md | +9/-3 | **线1** | QFQ生产启用清单 | - |
| config/qfq_rebase_admissible_securities.json | +197/-4 | **线1** | QFQ准入证券 | - |

### ⚠️ 共享文件（两条线都改了，必须拆分）

| 文件 | +/- | 线2改动 | 线1改动 | 拆分难点 |
|---|---|---|---|---|
| quantstudio/pipeline/sources/mcp_adapter.py | +577/-0 | 字典(sw_daily/sw_weight/停更表) + industry_classification常量注入/L1过滤/去重 | _restore_to_raw/_get_adj_latest_global/_coldstart 全套还原逻辑 + is_passthrough/_fetch_passthrough | **最复杂**：线2的 industry_classification 后处理(L502-525) 与线1还原代码在同一文件，但逻辑独立 |
| quantstudio/pipeline/daemon.py | +68/-4 | 无直接改动 | MCP adj_factor分支补etf_minutes + batch_audit rows_fixed | 线1专有（但v3.5记线1混入了passthrough路由L554/L712，需核实） |
| quantstudio/pipeline/writers.py | +60/-2 | 无 | passthrough write支持 | 线2基础设施（被线1混入提交） |
| quantstudio/pipeline/qfq_resident_orchestrator.py | +22/-8 | 无 | supersede逻辑 | 线1专有 |
| quantstudio/pipeline/config_lint.py | +6/-1 | 待核实 | 待核实 | 需diff确认归属 |
| AGENTS.md | +32/-0 | 待核实 | 待核实 | 需diff确认归属 |
| README.md | +10/-0 | 待核实（线2可能涉及MCP章节） | 线1 is_qfq还原章节 | 需diff确认 |
| docs/mcp_migration/mcp_contract_v1_draft.md | +25/-0 | 待核实 | 线1 metadata契约 | 需diff确认 |
| docs/mcp_migration/mcp_protocol_probe.md | +27/-0 | 待核实 | 线1 §7.4还原方案 | 需diff确认 |

### 临时文件（需清理，不推送）

| 文件 | 处理 |
|---|---|
| scripts/_c_*.py（9个：_c_check/_c_daily_gap_backfill/_c_fixrun/_c_recon_baseline/_c_recon_final/_c_recon_status/_c_verify/_c_wait_recon） | 删除（线1调试脚本） |
| nul | 删除（Windows保留名误创建，0字节） |

## 推送阻塞项（codex 闭环要求）

1. **线1 is_qfq=False bug 未修**（v3.5 记录）——mcp_adapter.py:843 qfq_mask 只还原 is_qfq=True，需改走还原公式
2. **共享文件未拆分**——mcp_adapter.py/daemon.py/writers.py 混两条线，必须拆3独立提交（线1还原/线2映射/passthrough基础设施）
3. **classification_version/classification_system 疑点已解决**（本轮修复：column_map补src→classification_version + code_col=industry_code，4必填列全PASS）

## 建议的推送顺序（待两条线都完成）

1. 线2映射配置（alignment_rules.json + collector_tasks.json + 任务书/审计文档）——独立，无依赖
2. passthrough基础设施（mcp_adapter字典部分 + writers + daemon路由）——线2依赖
3. **etf_basic 专项（第4组）**：etf_basic_standardizer.py（build_payload_mcp）+ aligner.py L288 放宽 + alignment_rules.json etf_basic identity —— 独立组，不碰线1文件
4. 线1还原（mcp_adapter还原代码 + daemon adj_factor + orchestrator + config_lint + 文档）——待is_qfq=False修复

## etf_basic 专项改动（第4组提交，2026-08-03）

| 文件 | 改动 | 验证 |
|---|---|---|
| quantstudio/pipeline/etf_basic_standardizer.py | 新增 build_payload_mcp（MCP输入分支）：形状适配(list_status→status/index_code→tracking_index) + drop云端etf_type防concat冲突 + fund_type用classify etf_type填充 + tracking_index缺失用name兜底 + 复用classify_etf/daily_bounds/quality gate | 1622取数→1606行(PASS)，12必填列全有值 |
| quantstudio/pipeline/aligner.py L286-294 | 触发条件 source=="tushare" → source in ("tushare","mcp")，按source分发build_payload/build_payload_mcp | 实测align 1606行 |
| config/profiles/mcp_only/alignment_rules.json | etf_basic: column_map → identity（standardizer已完成列转换） | 必填列全PASS |

**逐列比对结论**：build_payload_mcp 输出 == BASELINE_COLUMNS（与 tushare 同构）；classify_etf 单一真相源保证 etf_type 分类两路径一致；data_source=mcp_questdb_etf_basic 区分来源。

**codex 审计 P2 决策落地（equity 分类兜底）**：classify_etf 的 'equity' 只在 fund_type/type 含"股票"时返回，云端无此列 → MCP 路径股票型 ETF 全落 'other'（回测按 etf_type='equity' 筛选会拿到空集，双模式不等价）。采纳 codex 选项2：build_payload_mcp 内 classify 后补兜底启发式——etf_type=='other' 且 tracking_index 为真实指数代码 → 'equity'，classification_method 标注 'mcp_heuristic:tracking_index_present' 可追溯。实测：equity 从 0→1300（510050/510300/159007 等典型股票 ETF 正确归类），不动 classify_etf 本体。
