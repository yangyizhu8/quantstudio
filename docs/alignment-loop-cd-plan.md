# 闭环计划 P2-β · 件 C + 件 D 合并方案（①方案轮，2026-10-08 呈②审）

> 母授权：`docs/alignment-loop-architecture-plan.md` v1.0（总调度批准；「本方案批准=各件①的
> 母授权，逐件②仍单独审」）。本件=件 C（知识库注入生成端）+件 D（循环编排 SOP）合并①方案。
> 当前批次：P2-β（依赖件 A 生效——A 已落 `df9948b`，依赖满足）。

## 🔹 勘察事实（本轮实测）

| 事实 | 证据 |
|---|---|
| 件 C 落点=R2 段（写 design 契约+交叉校验+中文命名契约+R2 exit gate） | `skills/quantstudio-strategy-compiler/SKILL.md:205-279`（581 行；R2 exit gate 在 L279） |
| `knowledge/contracts/` 契约档案 5 件在役 | api-semantics.md(1784B)/data-caliber.md(1455B)/etf-share-merge.md(2593B)/matching-rules.md(1161B)/position-fields.md(2961B) |
| 契约档案形态=规则表+退役规则+精度边界+关联指针+策略层范本 | `knowledge/contracts/etf-share-merge.md`（CORP-01+CORP-02 链：合并→零股→全清；策略层范本=四象限 `submit_target_amount` L400-408） |
| 知识库导览机制在役（contracts/playbooks/registry/archive 四层） | `knowledge/README.md:1-20` |
| **件 C 现状：SKILL.md 零契约面引用**（无「契约面」节、无 knowledge/contracts/ 加载） | grep 实测 |
| **件 D 现状：`docs/alignment-loop-sop.md` 不存在** | Test-Path False |
| 件 D 可参照形态：A 件六态契约+knowledge 闭环总指导 | `docs/strategy-compiler/alignment-lifecycle.md`、`knowledge/alignment-loop-guide.md` |

## 🔹 件 C · 知识库注入生成端（第⑥环建制化）

**落点**：`skills/quantstudio-strategy-compiler/SKILL.md` R2 段，新增小节
「Contract surfaces（契约面）」——置于 L275（portfolio 契约交叉校验段）之后、L279（R2 exit gate）之前。

**内容（四要素）**：
1. **强制加载**：R2 阶段读 `knowledge/contracts/` **全部**契约档案（当前 5 件；新增档案自动纳入，
   不硬编码文件名清单——以目录列举为权威），作为设计约束输入；
2. **平台语义边界清单**（从契约档案提炼，写入 skill 以使生成端直接可用）：零股卖出（`order(-N)`
   数量式=委托量即成交量，买入整手强制）/ 摊薄与合并口径（`0.50≤ratio<0.99` 任意比反推+按份取整）/
   E1 取数（日线信号 `include=False` D-1）/ ETF 动态池（本地 `get_etf_list_local`，转 PTrade 固化）/
   撮合与现金先序（`sell_then_buy_immediate`）；
3. **契约内调用模式产出**：R3 实现阶段生成的调仓代码须落契约内形态——**范本=四象限
   `submit_target_amount`**（非清仓分支自整手 L406-408／清仓分支含零股全清 L400-402）；
4. **R2 exit gate 增强**：设计契约须与 `knowledge/contracts/` 无冲突（冲突即 BLOCK，以档案为权威）；
   R5 部署不变量沿用既有 `r5_deployment_invariants` 面。

**改动面**：SKILL.md 新增 1 小节（约 25-35 行）+（可选）`references/` 增 1 份清单指针文档。
**生成器逻辑零改动**（约束注入属提示层，不改任何脚本/校验器/引擎）。

**三型判定**：**纯增益**——新生成策略从源头落在已对齐子空间（契约内调用模式）；存量策略零影响
（不重生成即不变）；无既有行为改变（提示层文本增量）。

**验收**：
- ①SKILL.md 契约面小节落位，含四要素与 5 档案目录级引用（非硬编码名）；
- ②**契约模式抽查**：以本 skill 走一次最小生成（或对既有 design 做契约面核对 dry-run），确认产出
  设计/实现含契约内调用模式 ≥1 处（清仓含零股全清分支为佳）；
- ③六策略零重转、`git status` 策略文件零改动；既有测试套件全绿。

## 🔹 件 D · 循环编排 SOP（常态运转+客户自持版）

**产出**：`docs/alignment-loop-sop.md`（新建）。

**内容（五节，按母计划 §件 D 展开）**：
1. **策略生命周期挂载**：每新策略=看板任务挂六态状态机（`generated → converted → local-passed →
   platform-run → aligned → graduated`，契约见 `docs/strategy-compiler/alignment-lifecycle.md`）；
   每态进入/退出的判定命令与证据指针；
2. **案件流转**：每案=六步件（方案→②审→实施→验收→确认→推送），含 S1 报告→S3 路由→修复→
   S4 台账回写的最小闭环动作清单；
3. **常态节奏**：季度收敛复核挂 FW6/晨检节奏；S4 台账更新触发条件（**每案闭环/每策略毕业时强制**）
   +平台期告警（连续 M=3 轮残差不动）响应动作；
4. **客户自持版**：本章节=任意智能体（Claude Code/Cursor/ZCode/dsh 等）可执行步骤，**不含总调度
   私有机制**（三件套排程/线调度回执/用户域闸门等），仅含仓库内可验证动作；客户侧执行指引=读
   `AGENTS.md`+`knowledge/alignment-loop-guide.md`+本 SOP；
5. **回退与治理**：每件独立 revert（A 门禁单点关停=清单项摘除）；零守护进程（策略事件驱动）。

**改动面**：新增 1 文档；**代码零改动**。
**三型判定**：**纯增益**（文档件，零行为改动）。

**验收**：按文**纸面演练**走一遍四象限案（DAT-16→CORP-01→CORP-02 全链），逐节动作可对应既有
证据件（`docs/evidence/corp-action-etf-merge-fix-acceptance-20261007.md`、
`corp02-odd-lot-sell-acceptance-20261007.md`、`loop-ab-gate-triage-acceptance-20261007.md`）
——演练结论写入④验收证据。

## 🔹 合并方案八项（Plan-Mode）

**① 阶段**：闭环计划 P2-β（A+B 已落 `df9948b`；E 已落 `5fa1bc3`；C+D 为本批）；六步：本件①→②审→③实施→④验收→⑤确认→随批⑥。

**② 目标**：件 C=生成端接入契约面（第⑥环建制化）；件 D=循环常态运转 SOP 落盘（含客户自持版）。

**③ 分工**：主导=本会话；②审=ZCode；③实施=**本会话直接执行**（文档/提示层文本件，非实现型编码
——若②审要求，可改委派）→ 编码委派纪律声明见下；终审=总调度/用户。

**③·委派声明**：件 C/D 均为 **Markdown 文本件**（skill 提示词节+文档），无程序代码改动、无算法/
接口实现——**不触发实现型编码委派条件**（委派纪律适用「写或改代码」）；本会话直接执行，此声明
即当轮显式不委派说明。

**④ 事项清单**：
1. 件 C：SKILL.md R2 段插入「Contract surfaces」小节（四要素）；核 skill 校验脚本仍 PASS；
2. 件 D：`docs/alignment-loop-sop.md` 五节落盘；
3. 双向指针：SKILL.md 契约面小节 ←→ `knowledge/contracts/`；SOP ←→ alignment-lifecycle.md /
   alignment-loop-guide.md / knowledge/README.md 导览；
4. ④验收证据：`docs/evidence/loop-cd-acceptance-20261008.md`（含纸面演练结论）。

**⑤ 待确认**：本合并方案批复（②审通过后③实施，④验收后呈⑤）。

**⑥ 风险与卡点**：
- skill SKILL.md 为双推即达客户组件——改动须同步 README「策略工具箱」/`docs/prompt_engineering.md`
  相关表述（六步⑥推送前置义务，本批预置）；
- 契约档案新增时 skill 表述不失效（采用「目录列举为权威」而非硬编码文件名）；
- 件 D SOP 与 alignment-loop-guide.md 的分工须写清避免双权威（SOP=操作流程；guide=闭环总指导；
  SOP 引用 guide 不复制内容）；
- 纸面演练可能暴露 SOP 与既有机制的缝隙——发现即登记（不夹带修复）。

**⑦ 合规约束**：六步流水线；纯增益三型判定（C/D 均纯增益）；写前快照；精确 add（多会话共享
工作区——skills/ 有他线在途改动时须核对）；推送前置文档同步义务（README+docs 引用文档）。

**⑧ 验收判据+回退**：
- **判据①**：SKILL.md 契约面小节落位（四要素齐+目录级引用+范本指针）；skill 自身的校验/冒烟
  不退化（如存在 `validate_*` 或 skill lint）；
- **判据②**：契约模式抽查——产出含契约内调用模式 ≥1 处（清仓零股全清分支优先）；
- **判据③**：SOP 五节齐+纸面演练走通四象限全链（逐节对应既有证据件）；
- **判据④**：既有测试套件全绿（`scripts/run_contract_gate.py --strategies` PASS 沿用）+策略源码零改动；
- **判据⑤**：双链指针有效（SKILL.md/README/prompt_engineering/alignment-loop-guide 互指不死链）。
- **回退**：单件 revert（SKILL.md 小节摘除／SOP 文件删除即回退，无行为残留）。
- **失败判定**：契约面表述与契约档案冲突、SOP 与既有机制双权威冲突、skill 校验退化。

⑧·**判型声明**：**纯增益 ×2**（C=提示层约束注入；D=文档件）——既有产物/行为零改变以判据④实证；
修复前置三问：①影响其他功能=无（提示层/文档）；②影响性能=无；③影响精度=无（引擎/校验/渲染零触碰）。

---

**暂停语义**：本合并方案呈②审——通过后③实施（本会话直执）→④验收→⑤确认→随批⑥。
