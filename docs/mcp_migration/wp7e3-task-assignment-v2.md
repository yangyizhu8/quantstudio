# WP7-E3 任务分工总览 v2（2026-08-09）

> 项目经理：reasonix（审核/决策呈报/进度报告）
> 执行方 A：Trae —— trading-battle-back（同步管线侧，**本地↔云端一致性唯一责任方**）
> 执行方 B：ZCode —— QuantStudio（客户端/回测引擎侧）
> 架构事实（责任边界）：数据同步 = **本机主动推送**（trading-battle-back → 云端 QuestDB 镜像）；
> 云端仅镜像 + MCP 读服务，不独立产生/修正数据 → 本地↔云端一致性由 Trae 确保。

---

## 并行线 1：C 序列（bootstrap → watermark release → G3）—— ZCode 主导

| 步骤 | 内容 | 状态/要求 |
|---|---|---|
| C-4 | 全量 bootstrap-run（5385 证券，PID 37460） | 🔄 后台运行中（10-20h）；每 2-4h 回报 completed/failed/内存；新增 failed 记录后继续（不中断） |
| C-4 后 | failed 分布统计 | 完成时：全部 failed 清单 + 原因分类（预期 Tushare 固有差异类 >3） |
| **A+ 机制** | dead_letter 批准机制（**待用户确认后实施**）：qfq_bootstrap_item 加 approved 标记；bootstrap_completed 只阻塞未批准的 dead_letter；CLI `bootstrap-approve --run-id --codes --reason`；测试 | 前置：ZCode 查 C-6 release 实现是否硬卡 bootstrap_completed（回报后定路径） |
| failed 重跑 | 数据修复（如需要）+ 对 failed 证券重跑 bootstrap | 依赖 A+ 或数据修复完成 |
| C-5 | bootstrap-audit：completed / blocked=0 / failed=0（含已批准 dead_letter 审计） | 证据回报 |
| C-6 | watermark release | **暂停点**：C-1~C-5 证据 + 用户最终确认（最不可逆） |
| 观察期 | P2-3 的 2+2 实证（两次 post-close 采集 + 两次增量回放，Run Card） | G3 前到期 |
| P2-1 | 双 aux 退役结论 + 用户接受 | G3 前到期 |
| G3 | 最终闭环审核（P0=0/P1=0/P2 全解决或用户接受） | 大跨度 C 完成后 |

## 并行线 2：数据一致性机制（A4 + 第一层修复）—— Trae 主导，ZCode 客户端接入

> 背景：① QuantStudio 每周对账过渡方案与 ② 云端 updated_at 变更检测**合并为 A4**——
> QuantStudio 作为第一个用户接入验证（不做两套、技术债取消）。

| 子任务 | 责任方 | 内容 |
|---|---|---|
| 第一层修复 | Trae | `qfq_maintenance.py` 更新已有行后写 `local_repair_log`（每日 05:00 repair 同步自动推送）；**排查所有会更新历史行的本地脚本**（凡更新即写 repair log） |
| A4 云端侧 | Trae | `cloud_updated_log` 表（(table, day, updated_at)，分钟按日聚合）+ `sync_incremental.py` 推送时 upsert（INSERT 与 UPDATE 统一记）+ repair 推送同样记 log + 查询接口 + 测试 |
| A4 客户端侧 | ZCode | daemon 增量拉取前查询 cloud_updated_log（本地水位后有更新的窗口 → **先局部全量重拉再增量**）；接入 fetch_table 增量分支（不动 bootstrap/export_cache 路径）；测试 + staging 验证 |
| 联调 | Trae+ZCode | 真实增量采集验证：云端更新历史窗口 → 客户端检测 → 重拉 → 一致 |
| 契约文档 | Trae | ops-runbook 补"更新传播契约"节：三条通道（因子事件驱动 + repair log 显式推送 + updated_at 变更检测）+ **责任边界（本机推送负责一致性）** + 写行脚本必须写 log 的约定 |

## 并行线 3：代码收尾与同步（C-4 结束后，ZCode 主导 + 用户确认）

| 项 | 状态 |
|---|---|
| 6 个流式 bug 修复 + 4 新回归测试（27 全过） | ✅ 已完成，待同步 |
| 注入优化（连接复用 + 按日去重 240x，C-2 已用） | ✅ 已实施，待同步 |
| A+ 机制（若用户确认） | 待实施 |
| **GitHub 同步** | C-4 结束后统一执行（运行期间代码稳定原则），**用户确认后**：QuantStudio 追加 commit + README/docs 同步 |

## 决策点清单（用户拍板）

1. **A+ dead_letter 批准机制**（框架行为变更，铁律红线）——ZCode 回报 release 实现后给出最终建议，用户确认后实施；
2. **C-4 结束后 failed 处理**（A+ 批准 vs 数据修复）——按 failed 清单与根因定；
3. **C-6 watermark release**（最不可逆）——C-1~C-5 证据齐后最终确认；
4. **GitHub 同步**（QuantStudio 追加改动）——C-4 结束后确认；
5. **P2-1 双 aux 退役结论**——观察期后用户接受。

## 技术债（不阻塞，登记在案）

- read_increment 行数级告警（靠 7 天扫描兜底）；
- get_tushare_data.py 15:00 采集防御入库（随该文件清理）；
- push_repair_window.py 凭证改造（Q040，扩 config schema）；
- 分钟线重锚并行化（T-B2：读并行 + 写串行，bootstrap 10-20h 优化——C 完成后立项）。

## 进度报告

- v6.7.46：任务分工总览 v2 + 责任边界确认 + A4 合并（待更新）。
