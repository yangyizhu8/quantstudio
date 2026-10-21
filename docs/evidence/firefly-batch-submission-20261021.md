# ⑤ 呈批件：firefly「水位线」案修复批（FP-1/2/3/4/6/7）· 待用户终审

- 日期：2026-10-21（12:30 终核呈报）
- 呈批路径：客户运维会话 → 总调度亲审 → **用户终审** → 双仓库推送
- 关联：N0–N4 全链记录（本会话）；设计文档 2 份；证据文档 2 份

## 0. 知识反哺检索留痕（依 2026-10-10 铁律）

```
知识反哺：[命中] 案例卷宗-CASE-006交付质量案（12-客户运维/案例卷宗/案例卷宗-CASE-006交付质量案.md，2026-10-18）→ FIXTEST 项已修不重复归因；qfq 窗口守卫族框架层修复方法学
知识反哺：[无命中] 12-客户运维/已知错误（水位线/门禁判定域/周期生命周期主题）, 12-客户运维/案例卷宗，2026-10-18
```

## 1. 问题与根因（一段话）

客户报「GUI 水位线与库内数据不符」。实证：数据无缺失（日线已到 10-16/10-20），水位线是**增量游标**而非数据边界；
四张 QFQ 价格表游标自 09-03/09-04 冻结，根因四层——①展示层只有一列（游标≠边界不可辨）；②周期状态机不闭合
（进程死→`started` 悬挂，生产 10 条）；③门禁判定域为全表全历史（历史残渣永久锁死提交）；④applying 无配额
（4,818 单元≈18 天，单窗不可 finalize）。深层数据面根因（上游因子漂移致 rebase 自锁）另立专项 B（已出骨架）。

## 2. 批次清单与判型

| FP | 内容 | 判型 | 状态 |
|---|---|---|---|
| FP-1 | GUI「数据止点」列（max(time)，单连接 UNION ALL + 60s TTL + 降级不缓存） | 新增能力型 | ✅ 验收绿 |
| FP-2 | GUI「水位状态」徽标（正常/延迟提交/门控保持+原因；降级显「未知」不伪装） | 新增检测型 | ✅ 验收绿 |
| FP-3 | 悬挂周期终态回收（双条件门：4h + 早于本进程启动；世代解析先于回收——10-21 缺陷修复含 B1/B2 守卫） | 纯恢复型 | ✅ 验收绿（生产 10-19 实证回收 10 条） |
| FP-4 | 停止路径 DB 终态补齐（qfq_abort_cycle） | 纯恢复型 | ✅ 验收绿 |
| FP-6 | 门禁判定域收敛（cycle 域 + health 并行留痕 + env 双轨回退） | **行为修正型（数据正确性）→ 需用户终审** | ✅ 验收绿 |
| FP-7 | applying 配额（首值 60，实测反推；顺延保持 pending） | 节奏型 | ✅ 验收绿（生产 06:40 实证 60/4613） |

**FP-5（last_run_id）不在本批**：执行中触发 schema 契约事故已回滚定谳，按 2.2 契约件另行实施（②审已过）。

## 3. 改动清单（本批待推送）

代码 5：`gui/db_helper.py`、`gui/tabs/task_tab.py`、`pipeline/qfq_resident_orchestrator.py`、`pipeline/daemon.py`、`pipeline/daemon_lifecycle.py`
配置 0（`max_units_per_cycle` 走默认值，不强制改 collector_tasks.json）
测试 5 新（`test_gui_watermark_display` 15、`test_qfq_cycle_reaper` 10、`test_qfq_cycle_abort` 7、`test_qfq_gate_scope` 9、`test_qfq_reap_identity_wiring` 3）＝ **44 例**
脚本 1：`scripts/migrate_source_watermark_last_run_id.py`（**带硬门**，默认拒执行——为 FP-5 备用，本批不运行）
文档 4：2 设计（gate-scope、factor-equivalence）+ 2 证据（spectrum、本件）

## 4. 验收证据汇总

| 项 | 证据 |
|---|---|
| 单测 | 本批 **44 passed**（含 A20-N/A32 类负向对照 7 项） |
| 回归 | qfq+daemon+GUI 套件 **137 passed**，1 deselected＝HEAD 基线同红预存在项（`test_require_bootstrap_fail_closed`，另案） |
| 生产实证 | ①FP-3：10-19 10:18:22 回收 10 条（error=reaped）；②FP-7：10-21 06:40:04「配额生效 60/顺延 4553」；③FP-2 数据面：候选水位 candidate=2026-10-20 已入 intent；④cyc_0802003f5128 完整走完 applying→gate→终态（**finalized_held，0 committed**——因数据面因子漂移，见专项 B；FP-6/7 机制本身按设计工作） |
| 判据修订（已申报） | A4 冷启 1.28s/热 0.00s（原 ≤1s）；FP-7 首值 60（原建议 500–1000，实测 5.4min/单元否定） |
| 事故（已定谳） | FP-5 加列→partial_or_mixed→daemon 拒启：即改即回滚（DROP 后 complete_2_1 实测），脚本加硬门，2.2 契约件重立 |

## 5. 待用户终审事项（🔴 必签）

1. **FP-6 数据正确性边界**：水位将可在「历史 dead_letter 未清零」时按「本周期窗口内一致」推进。
   缓解在位：单单元判定零放宽（A20-N 前后均 False）；health 并行留痕（首周观察窗，异常即回退 global）。
2. **FP-7 配额 60**：每轮最多 60 单元重锚，全量收敛需多轮（~4,613 单元 ÷ 60/轮）。
3. 推送范围 = §3 清单（不含 FP-5 任何库操作）。

## 6. 观察期与回退

- 首周每轮留痕 `health.in_progress/dead_letter` 与水位推进量（总调度②条件②）；
- FP-6 回退：`QS_QFQ_GATE_SCOPE=global`（逐位还原旧判定）；FP-7 回退：`max_units_per_cycle=0`（不限）；
- 复发监测：同类「水位冻结」再现 → 回 N2b 复评（先查知识库是否缺条目）。

## 7. 后续排程（已裁定）

FP-5（2.2 契约）→ 本批出仓后实施；专项 B（因子等价）②审后实施；C 与 FP-5 窗口协同评估；A 终态登记。
