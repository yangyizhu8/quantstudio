# 双端对齐闭环 · 循环编排 SOP（件 D，2026-10-08）

> **定位分工**：本 SOP = **操作流程**（怎么跑）；[`knowledge/alignment-loop-guide.md`](../knowledge/alignment-loop-guide.md)
> = 闭环总指导（为什么这样设计）+ [registry 台账](../knowledge/registry.md)。
> **本 SOP 引用上述文档，不复制其内容**（`knowledge/README.md` §导览的「指针不复制」纪律）。
> 母计划：[`docs/alignment-loop-architecture-plan.md`](alignment-loop-architecture-plan.md)（七件 A-G）。

## 1. 策略生命周期挂载（每新策略）

策略状态机六态（契约详表见 [`docs/strategy-compiler/alignment-lifecycle.md`](strategy-compiler/alignment-lifecycle.md)）：

```
generated → converted → local-passed → platform-run → aligned → graduated
```

| 态 | 进入判据（可执行） | 证据指针 |
|---|---|---|
| `generated` | skill 全流程产出设计+实现（R0→R6） | `output/generated_strategies/<id>/agent_strategy_design.json` |
| `converted` | 转换产物生成（`qs-compile import` 或 `package`） | `output/ptrade_export/<id>/` 或 `qmt/` |
| `local-passed` | 本地回测+校验器全绿（api_portability PASS） | 回测三件套+`run_card.json` |
| `platform-run` | 平台首跑完成（日志导出） | 平台日志+解析产物 |
| `aligned` | S1 对齐报告判定「已对齐」或「残差达标」（<0.5%） | `output/backtest_results/<产物>/` 对齐报告 |
| `graduated` | **ALIGNMENT-GATE 通过**+R5.5 通过+发布 | 发布产物+R5.5 报告 |

**挂载方式**：每新策略在看板建任务卡，卡片描述含当前态+下一态动作；态迁移时更新卡片并附证据指针。
**存量豁免**：既有策略不回溯（登记制，ALIGNMENT-GATE 对 legacy 出 WARN 不 BLOCK）。

## 2. 案件流转（每案一件）

每案走框架层六步件：**①方案 → ②审计 → ③实施 → ④验收 → ⑤用户确认 → ⑥双仓库推送**。

最小闭环动作清单（对齐差异案件）：

1. **发现**：平台日志/回测产物差异 → 登记 `knowledge/registry.md`（案号+现象+首偏日）；
2. **S1 定位**：本地跑 `scripts/align_diff_report.py`（`--local <回测产物> --platform <日志>`）产出差异报告；
3. **S3 路由**：报告自动输出候选案型（[`scripts/align_triage_rules.py`](../scripts/align_triage_rules.py)）；
   已对齐直通／命中候选附匹配规则 J／无命中立新案；
4. **归因取证**：证据链定根因（禁止未证实归因动手）——平台实证优先；
5. **修复**：框架层六步件（策略源码零改动，重转生效）；
6. **S4 回写**：台账追加一行（[`knowledge/alignment-convergence.md`](../knowledge/alignment-convergence.md)）；
7. **回灌**：新判别特征→S3 规则库；新平台语义→`knowledge/contracts/` 契约档案。

## 3. 常态节奏

**S4 台账更新的三条触发并存（互补，非替代）**：

| 触发 | 来源 | 性质 |
|---|---|---|
| 每策略每次 S1 跑（`--as-json`） | 既有机制 | **自动追加** |
| A 件 aligned 态落盘 | 既有机制（A 件联动） | **自动带出** |
| 每案闭环／每策略毕业 | 本 SOP | **强制核对点**（人工确认台账已含本案行，防漏记） |

**平台期告警**：台账自动统计「连续 M=3 轮残差不动 ⇒ 存在未发现系统性漏洞」——触发时开新案归因
（不因"残差达标"而停止排查）。

**周期复核**：收敛复核挂 **FW6/晨检时段**——注意该节奏为**总调度侧内部节奏**；
客户自持侧见 §4 的等价动作（节奏自定，动作同构）。

**维度宣布（件 G）联动**：连续 N=3 新策略首跑「已对齐」⇒ 该 API 维度转维护态（台账 §3 自动统计触发提示）。

## 4. 客户自持版（任意智能体可执行）

**适用前提**：整仓 clone（`knowledge/`、`docs/`、`scripts/` 均在仓库内）。

客户侧（或无总调度机制的独立部署）执行步骤：

1. 读 [`AGENTS.md`](../AGENTS.md)（项目铁律）→ [`knowledge/alignment-loop-guide.md`](../knowledge/alignment-loop-guide.md)
   （闭环总指导）→ 本 SOP；
2. 按 §1 挂载策略状态机（可用任意任务工具，不依赖本项目看板实现）；
3. 按 §2 执行案件流转（S1/S3 脚本随仓库提供，命令同主仓）；
4. 按 §3 维护台账（三条触发中「每案闭环强制核对点」为人工动作，其余自动）；
5. 收敛复核节奏**自定**（如月度/季度），动作与主仓 FW6/晨检同构。

**不含**的内容（总调度私有机制，客户侧不需要）：三件套排程、线调度日历与回执、用户域闸门、
跨会话派单/审核流程——这些是主仓治理机制，不影响闭环本体运行。

## 5. 回退与治理

- **回退**：每件独立 revert；A 件 ALIGNMENT-GATE 可单点关停（校验清单项摘除即回退）；
- **零常驻**：全链无守护进程——循环由「策略事件驱动」（新策略/新日志到达才转），零额外算力；
- **唯一权威源**：闭环资产=仓库版本（双推同步，无第二分发通道）；
- **缝隙分类处置**：
  - **SOP 文本缝隙**（本文件表述不清/与既有机制不一致）→ **当场修**；
  - **框架机制缝隙**（SOP 演练暴露的真实框架缺陷）→ 登记后**同周期启动修复**（对齐
    「框架问题立即解决」铁律，禁止以登记挂账替代修复）；
- **知识库膨胀治理**：`knowledge/contracts/` 季度 sweep+supersedes 语义（母计划 §4-4）。

## 6. 演练校验（只读对照，2026-10-08 落盘时执行）

以四象限全链（DAT-16→CORP-01→CORP-02）逐节对照既有证据件，验证本 SOP 每节动作可对应真实案例：

| SOP 节 | 四象限链对应动作 | 证据件 |
|---|---|---|
| §2 发现/登记 | DAT-16 差异登记（平台 01-29 锚） | `knowledge/registry.md`、`docs/evidence/corp-action-etf-merge-2025-09-22.md` |
| §2 S1 定位 | 残差 3.88pp→2.23%→0.013% 序列 | `knowledge/alignment-convergence.md` §种子数据 |
| §2 修复 | CORP-01 合并带规则退役+反推 ratio | `docs/evidence/corp-action-etf-merge-fix-acceptance-20261007.md` |
| §2 联动案 | CORP-02 零股全清（合并→零股→全清链） | `docs/evidence/corp02-odd-lot-sell-acceptance-20261007.md` |
| §2 回灌 | S3 规则库四种子+ALIGNMENT-GATE 门 | `docs/evidence/loop-ab-gate-triage-acceptance-20261007.md` |
| §3 台账/告警 | S4 台账收敛矩阵+M=3 平台期 | `knowledge/alignment-convergence.md` |

**演练结论**：六节动作全部可映射既有真实案例证据件，无悬空动作（详见
`docs/evidence/loop-cd-acceptance-20261008.md`）。
