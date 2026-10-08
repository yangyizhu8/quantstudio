# 闭环计划 P2-β · 件 C + 件 D 合并方案（rev2，2026-10-08 呈②审 delta 追认）

> 母授权：`docs/alignment-loop-architecture-plan.md` v1.0。本件=件 C+件 D 合并①方案。
> **rev2 说明**：②审（commit eab090e 审）判定「修订后通过」（必改 A-G／应改 H/I/J/L／可选 K）
> 全落本版。版本链：eab090e → 本版 rev2。
> **②审最大抓漏（必改 B）**：本机运行副本 `.agents/skills/` 与项目副本双副本制——改项目
> SKILL.md 后**必须重跑 `scripts/install_skill.py`** 刷新运行副本，否则件 C 在会话生态内**空转**。

## 🔹 勘察事实（rev2 补 ②审实测）

| 事实 | 证据 |
|---|---|
| 件 C 落点=R2 段 | `SKILL.md:205-279`（`:279`=R2 exit gate；插入位=L275 后） |
| **R2 段名勘误（应改 H）**：R2=Design contract and component plan；母计划所写「R2 能力勘察」混称 | `SKILL.md:205`（R2）vs `:169`（R1=Program-specific capability inspection）——实施按 R2，勿落 R1 |
| contracts 契约档案 5 件，覆盖 **4/5** 主题（必改 C） | 零股卖出 ✓`etf-share-merge.md:25-37`／摊薄 ✓`position-fields.md §1`／合并 ✓`etf-share-merge.md:6-23`／E1 ✓`api-semantics.md:26`+`data-caliber.md:10-14`；**ETF 动态池仅「待枚举」级**（`api-semantics.md:33`/`data-caliber.md:25`），实际规则在 `docs/strategy_toolbox.md:530`+`SKILL.md:199/224-227` |
| 范本具名（必改 D） | `quantstudio/backtest/strategies/四象限ETF轮动策略.py`：L400-402=清仓含零股全清／L406-408=非清仓自整手（`etf-share-merge.md:37` 仅为指针）；**范本=只读指针，策略源码零改动** |
| **双副本机制（必改 B）** | 项目 `skills/quantstudio-strategy-compiler/SKILL.md` ↔ 运行副本 `C:\Users\Administrator\.agents\skills\quantstudio-strategy-compiler\SKILL.md`（当前 sha256 逐位一致 `228c530f…`）；刷新=`scripts/install_skill.py`（copytree→quick_validate→失败回滚）；E1 铁律 skill 双副本同步先例 |
| SKILL.md 校验面（必改 B） | `skills/quantstudio-strategy-compiler/scripts/quick_validate.py`（`SKILL.md:579` 自登记）+`scripts/install_skill.py` 链式校验+**四测试**：`tests/test_agent_first_strategy_skill.py:260-270`（**SKILL.md 文案逐句断言——R2 段改动高危触点**）／`test_g4_release.py:256-258`／`test_pr6b1_install_skill.py:44-98`／`test_delivery_flow.py:272` |
| SKILL.md 版本标签（应改 I） | `SKILL.md:8`=`1.0.0-r54-optimize`——历次 skill 改动随版递增（beb3ec8/3e9b5cc/9a9a774 先例） |
| 件 D 参照 | `docs/strategy-compiler/alignment-lifecycle.md:9`（六态逐字）／`knowledge/alignment-loop-guide.md`（总指导，**不含**六态/S4/FW6/客户自持版——双权威结构性不存在）／`knowledge/README.md:33`（指针不复制纪律） |
| S4 触发既有口径（必改 F-1） | `knowledge/alignment-convergence.md:4`：每策略每次 S1 跑（`--as-json` 追加）+A 件 aligned 态落盘自动带出 |
| 同步义务清单（必改 E） | AGENTS.md「同步内容必须完整」：README+`docs/strategy_toolbox.md`（`:530` ETF 动态池／`:536` r5_deployment_invariants，直接被件 C 波及）+`docs/prompt_engineering.md`；母计划 `:61` 有指向 alignment-loop-sop.md 的前向指针（件 D 落地即通） |

## 🔹 件 C · 知识库注入生成端（rev2 修订）

**落点**：`SKILL.md` **R2 段**（`:205` Design contract and component plan），插入位于 `:275` 后、`:279`（exit gate）前。

**四要素（rev2 修订后）**：
1. **强制加载**：R2 读 `knowledge/contracts/` **目录列举**（非硬编码文件名——件 G 新增档案自动纳入），
   作为设计约束输入；**适用前提**：整仓 clone（可选 K 注明）；
2. **平台语义边界清单（4 档案 + 1 沿用，必改 C）**：零股卖出／摊薄与合并口径／E1 取数——源自
   contracts 档案（带档案指针）；**ETF 动态池**——contracts **无锚定档案**，沿用具名既有规则
   （`docs/strategy_toolbox.md:530`+`SKILL.md:199/224-227` 的 `universe_contract`），并在小节内
   显式标注「来源=既有规则，非契约档案」（**不得写成 5/5 契约覆盖**）；
3. **契约内调用模式产出**：R3 生成调仓代码须落契约形态——**范本=只读指针**
   `quantstudio/backtest/strategies/四象限ETF轮动策略.py` L400-402（清仓含零股全清）／
   L406-408（非清仓自整手）；**策略源码零改动**（仅作形态参考）；
4. **R2 exit gate 增强 + 如实标注（应改 L）**：设计契约与 contracts 无冲突为出口条件；
   **该门属会话层执行**（`validate_agent_strategy.py` 无 knowledge 感知、不改即无机检）——
   实施文档与验收**不得声称「机器强制」**，只以会话层核对+⑧判据②抽查为证。

**改动面**：SKILL.md 新增小节约 30-40 行+`:8` 版本标签递增（应改 I）；生成器逻辑零改动。
**三型判定**：**纯增益**。
**实施附带动作（必改 B，②审最大抓漏）**：改项目 SKILL.md 后**重跑 `scripts/install_skill.py`**
刷新 `.agents` 运行副本→**双副本 sha256 一致**入判据；SKILL.md 每次 edit 后即时 `git diff` 自检
（共享核心文件纪律第 5 条同款）。

**验收（rev2）**：
- ①小节落位（四要素+目录级引用+范本具名+来源标注）+**点名校验面全绿**：`quick_validate.py`+
  `install_skill.py`+四测试（重点是 `test_agent_first_strategy_skill.py:260-270` 文案断言）+`:8`
  版本已递增+**双副本 sha 一致**；
- ②契约模式抽查（**定主径，必改 G**）：走**最小生成**（R0/R2.5 停止点需用户配合），产物**限
  `output/generated_strategies/` 沙箱、不得写入 `quantstudio/backtest/strategies/`**；以 grep 契约
  模式标记（如清仓零股全清分支）≥1 处为证；dry-run 仅作降级补充并声明证明力限度；
- ③既有测试套件全绿；相对**开工写前快照**策略源码零新增改动（**必改 A**：基线化，不用
  `git status` 字面——他线在途 M 致字面不可执行）。

## 🔹 件 D · 循环编排 SOP（rev2 修订）

**产出**：`docs/alignment-loop-sop.md`（五节）。

1. **策略生命周期挂载**：六态（`generated → converted → local-passed → platform-run → aligned →
   graduated`，契约=`alignment-lifecycle.md`）+每态判定命令与证据指针；
2. **案件流转**：六步件+S1 报告→S3 路由→修复→S4 回写最小闭环；
3. **常态节奏（rev2 修订，必改 F）**：
   - **S4 台账触发三条并存声明**：自动追加（每次 S1 跑 `--as-json`）／A 件 aligned 态落盘自动带出
     （`alignment-convergence.md:4` 既有两条）**+** 每案闭环/每策略毕业**强制核对点**（第三条，
     与前两条互补非替代——SOP 须写明并存关系）；
   - **FW6/晨检标注为总调度侧内部节奏**，并给**客户自持版可自排的等价动作**（客户侧收敛复核节奏自定）；
   - 平台期告警（连续 M=3 轮残差不动）响应动作（出处=件 B 台账 `:22`）；
4. **客户自持版**：任意智能体可执行步骤，**不含总调度私有机制**（三件套排程/线调度回执/用户域闸门）；
   客户侧=读 `AGENTS.md`+`knowledge/alignment-loop-guide.md`+本 SOP；
5. **回退与治理**：单件独立 revert；零守护进程（事件驱动）；**缝隙分类处置（应改 J）**——SOP 文本
   缝隙当场修；暴露的**框架机制缝隙登记后同周期启动修复**（对齐「框架问题立即解决」铁律，不挂账）。

**改动面**：新增 1 文档；代码零改动。**三型判定**：**纯增益**。
**验收**：纸面演练走四象限全链（DAT-16→CORP-01→CORP-02），逐节映射既有证据件
（`corp-action-etf-merge-fix-acceptance-20261007.md`／`corp02-odd-lot-sell-acceptance-20261007.md`／
`loop-ab-gate-triage-acceptance-20261007.md`；DAT-16 登记在 `registry.md` 与
`corp-action-etf-merge-2025-09-22.md:3`）——演练结论入④证据。

## 🔹 合并方案八项（rev2）

**① 阶段**：P2-β（A+B/E 已落）→本件 rev2 呈 delta 追认→③实施。
**② 目标**：件 C=生成端接入契约面（第⑥环建制化）；件 D=循环 SOP 落盘（含客户自持版）。

**③ 分工**：主导=本会话；②审=ZCode（delta 追认）；③实施=本会话直执。
**③·委派声明（应②审 PASS 保留）**：件 C/D 均为 Markdown 文本件（skill 提示词节+文档），无程序
代码/算法/接口实现——不触发实现型编码委派条件（`docs/AGENT-PRESET-dev-optimizer.md §9` 触发清单=
实现型编码）；本会话直接执行，此声明即不委派说明。

**④ 事项清单**：
1. 件 C：SKILL.md R2 段插入小节（四要素）+`:8` 版本递增；
2. **件 C 附带：重跑 `scripts/install_skill.py` 刷新运行副本+双副本 sha 核验**（必改 B）；
3. 件 D：`docs/alignment-loop-sop.md` 五节（含 F 两条口径调和+J 缝隙分类）；
4. 双链指针：SKILL.md↔`knowledge/contracts/`；SOP↔`alignment-lifecycle.md`/`alignment-loop-guide.md`/
   `knowledge/README.md`；**母计划 `:61` 前向指针落地即通核验**（必改 E）；
5. **推送前置文档同步（必改 E）**：`README.md`+`docs/strategy_toolbox.md`（`:530`/`:536` 波及面）
   +`docs/prompt_engineering.md`；客户侧注明 **pull 后生效**（可选 K）；
6. ④验收证据：`docs/evidence/loop-cd-acceptance-20261008.md`。

**⑤ 待确认**：本 rev2 delta 追认→③实施→④验收→⑤确认→随批⑥。

**⑥ 风险（rev2 修订）**：
- **双副本分叉（必改 B）**：勿只改项目副本（件 C 空转）；install_skill.py 刷新+sha 核验为硬动作；
- **SKILL.md 文案断言测试**（四测试，`test_agent_first_strategy_skill.py:260-270` 逐句断言）——
  改动须与断言兼容，必要时同步断言（属文案同步，非放宽校验）；
- 文档同步义务（必改 E）：`strategy_toolbox.md` 在 AGENTS.md 同步清单内且直接承载被件 C 引用的
  表述（`:530`/`:536`）；
- 契约档案新增不失效（目录列举）✓；双权威分工（SOP 引用 guide 不复制）✓；
- 缝隙处置分类（应改 J）；多会话叠加（精确 add+edit 后 diff 自检）；客户旧副本=能力差异非故障
  （pull 后生效，无需专项通知）。

**⑦ 合规约束**：六步流水线；纯增益×2；写前快照；精确 add；SKILL.md 属双推即达客户组件
（edit 后即时 git diff 自检）；推送前置文档同步义务。

**⑧ 验收判据（rev2）**：
- **①**：件 C 小节落位（四要素含来源标注+范本具名）＋校验面全绿（`quick_validate.py`+
  `install_skill.py`+四测试）+版本递增+**双副本 sha 一致**；
- **②**：契约模式抽查（最小生成主径+output/ 沙箱+grep 标记≥1 处；dry-run 仅降级补充）；
- **③**：SOP 五节齐+纸面演练走通四象限全链（逐节映射证据件）；
- **④**：既有套件全绿（`run_contract_gate.py --strategies`）+**相对开工写前快照**策略源码零新增改动；
- **⑤**：双链指针无死链（含 `strategy_toolbox.md` 同步+母计划 `:61` 前向指针通）+客户侧 pull 后生效注明。
- **回退**：单件 revert（SKILL.md 小节摘除+install_skill.py 重跑回滚／SOP 删除）。
- **失败判定**：双副本 sha 不一致；四测试任一红；契约面表述与档案冲突；SOP 双权威冲突。

⑧·**判型声明**：**纯增益 ×2**（C=提示层约束注入；D=文档件）。修复前置三问：①影响其他功能=无
（提示层/文档）；②影响性能=无；③影响精度=无（引擎/校验/渲染零触碰）——经②审核实准确。

---

**rev2 自检（②审 A-L）**：A✓（判据④基线化）；B✓（双副本同步入事项2/判据①/⑥+校验面点名）；C✓
（4 档案+1 沿用，标注来源）；D✓（范本具名+只读指针声明）；E✓（toolbox 同步入事项5/判据⑤）；F✓
（S4 三触发并存+FW6 标注+客户等价动作）；G✓（判据②定主径+沙箱+不入 strategies/）；H✓（R2 段名勘误
入勘察表）；I✓（版本递增+diff 自检）；J✓（SOP 节5 缝隙分类）；L✓（R2 契约面=会话层执行，禁称机器
强制）；K✓（整仓 clone 前提+客户 pull 后生效）。

**暂停语义**：rev2 呈 ZCode delta 追认（M2b-rev2-fix 先例）→③实施（本会话直执）→④验收→⑤确认→随批⑥。
