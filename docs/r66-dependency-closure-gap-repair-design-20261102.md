# R66 修复方案 · 依赖闭包未入库导致远端断裂（2026-11-02 · 六步①方案）

> 状态：**待②审**（用户裁定 R66 裁 A·六步轻量·今日出方案）｜呈批人：用户（我域）｜执行：本会话（节点①）
> 关联：`docs/evidence/delivery-acceptance-20261031.md` §1.1（远端断裂归因）｜`docs/qmt-pipeline-convergence.md`（M5.2 谱系）｜`docs/handoff/push-batch-execution-card.md`（4b/4c/4d/7b）

---

## 一、问题定义

**现象**：主仓推送后远端「本地绿 ≠ 仓库绿」——已提交文件在远端 `import` 未入库模块 ⇒ 远端 CI/导入链断裂。

**两个已证实例**（同型）：

| 实例 | 已提交方（远端有） | 缺失方（远端无） | 后果 |
|---|---|---|---|
| 簇 A（QMT 部署） | `quantstudio/strategy_compiler/orchestrator.py`（M 态形参 `deploy_qmt`） | **`quantstudio/strategy_compiler/qmt_deploy.py`**（未跟踪 7,145 B） | QMT 部署内核远端缺失 |
| 簇 B（delivery 族） | `tests/test_delivery_*.py` 共 15 个（import `tests.fixtures.delivery`） | **`tests/fixtures/delivery/` 整目录**（4 个 .py 未跟踪） | **远端 CI delivery 族 15 测试必挂** |

**规模**：未跟踪 `.py` 共 **728** 个；同型扫描（已跟踪 .py 的 import 解析 vs 未跟踪集）命中 **15 处 / 2 簇**。

**性质判定（铁律 18 三型）**：**纯恢复型**——把「本地已绿、远端必挂」的断裂恢复到「仓库含全部依赖」的原态；diff 应为**纯新增**（新增未跟踪文件）＋**已跟踪文件的 M 态收敛**（把工作区已绿的实现入库），**不得引入任何新行为**。

---

## 二、归因与流程缺口定位（如实，不预设责任）

### 2.1 五件套自 2026-10-12 未入库的成因（谱系事实）

- `docs/qmt-pipeline-convergence.md`：**M5.2（闭环基建）立项 2026-10-12**（总调度批准立项，件3），其内容为「矩阵门/执行器/台账/**部署器**/PyQt/双口径」；总立项文件 `docs/qmt-m5.2-closure-design.md`（件3）。
- 谱系显示 M5.2 为**基建批**（本身不产生 D 编号缺陷），「部署器」即件4 载体。
- **git 史核对实测**：五件套（含实态版）**全部「从未入库」**——`git log --diff-filter=A` 无首入库记录；`git log -- <path>` 无任何触达记录。
- **886f294（10-31 修复笔）核对**：其裁定池**即两件**（`qmt_export_tab.py`＋`test_qmt_export_parity.py`，用户令原文「提交…两文件」）⇒ **「只带壳」表述作废**；该笔与五件套未入库**无因果关系**。

### 2.2 流程缺口定位（机制层，不指向个人）

**缺口 G1 · 依赖闭包未随「载体批」闭包**：M5.2 作为基建批的七件候⑤统推谱系中，**「部署器」件的依赖闭包（内核模块 + 配置 + 测试 + 两个调用点 M 态）未被纳入同一入库单元**——入库单元按「件」切分，而非按「依赖闭包」切分。

**缺口 G2 · 「本地绿」被当作「仓库绿」的验收口径**：现有门禁（`prepush-gate`）校验的是**提交声明完整性**（每笔 commit 都被声明）与**四查预算**，**不校验「被推文件集在 HEAD 树上是否自洽（import 闭包完整）」** ⇒ 依赖闭包缺口可静默过闸。

**缺口 G3 · 未跟踪资产无盘点闸**：工作区 728 个未跟踪 `.py` 无清单化扫描 ⇒ 任何「已提交文件 import 未跟踪模块」的断裂只能在**远端 CI 挂掉后**才被发现（本次两簇皆如此）。

⇒ **三类缺口共同构成本次事故的成因链**：G1（切分单位错）→ G2（闸门口径漏）→ G3（无盘点）→ 远端断裂显现。

---

## 三、改动范围（五件，逐件含 git 史核对与纯新增声明）

### (a) M5.2 件4 内核五件套补提交清单（实态确认版）

| # | 路径 | 实态 | 大小 | diff 性质声明 |
|---|---|---|---|---|
| a1 | `quantstudio/strategy_compiler/qmt_deploy.py` | 未跟踪（`??`） | 7,145 B | **纯新增**（从未入库；无历史基线） |
| a2 | `quantstudio/strategy_compiler/orchestrator.py` | **M 态**（已跟踪） | diff 36 行 | **纯新增语义**：新增形参 `deploy_qmt: bool = False`（M5.2 件4 层2：QMT 产物落盘后自动部署到 config 部署目录）＋注释行；**无既有行为改动**（默认 `False` ⇒ 旧调用路径零影响） |
| a3 | `quantstudio/strategy_compiler/cli.py` | **M 态**（已跟踪） | diff 22 行 | **纯新增语义**：新增 `--deploy-qmt` 透传 `deploy_qmt=getattr(args, 'deploy_qmt', False)`；**默认 False ⇒ 旧 CLI 行为零影响** |
| a4 | `tests/test_qmt_deploy.py` | 未跟踪（`??`） | 4,772 B | **纯新增**（从未入库） |
| a5 | `config/qmt_deploy.json` | 未跟踪（`??`） | 255 B | **纯新增**（从未入库） |

**提交纪律**：精确清单 `git add -- <五路径>`（**禁 `git add -A`／`git add .`**，铁律 10）；uuid 唯一 message 文件；写后核首行；**`.pyc`／`__pycache__` 一律不入库**。

**入库单元**：五件**同 commit**（依赖闭包完整才可入库——这是对缺口 G1 的直接修正）。

### (b) prepush 依赖闭包门设计（补 G2）

**目标**：推前拦截「被推文件集在 HEAD 树上 import 闭包不完整」。

**机制**（拟 `scripts/check_dependency_closure.py`，挂 `.githooks/pre-push`，在 budget 门之后、既有 gate 之前或之后按序）：

1. **取待推文件集**：`git diff --name-only <remote_sha>..<local_sha>`（与既有 gate 同一窗口口径，**按 refspec 上界**，见执行卡 4e 判据）。
2. **HEAD 树解析**：对每个待推 `.py`，用 `ast` 解析 `import`/`from ... import`，把模块名解析为**仓库内路径候选**（同目录 + 包内相对 + 顶层包）。
3. **闭包校验**：候选路径在 **HEAD 树**（`git cat-file -e HEAD:<path>`）中不存在 ⇒ **判定「待推文件集引用了未入库模块」⇒ 拦**（fail-closed，与既有 `exit 9` 同纪律）。
4. **豁免白名单**：标准库 / 三方库 / 可选依赖（try-import 包裹）——以**显式白名单文件**声明，不做启发式猜测；白名单本身入仓可审。
5. **输出**：缺口清单（已提交文件 → 缺失模块路径），与 `--pool` 同风格可读。

**验收判据**：构造「已提交文件 import 未跟踪模块」的临时样例 ⇒ 门禁 **exit≠0 且打印缺口**；正常批 ⇒ **exit 0**。

**成本评估**：AST 解析范围仅限**待推文件集**（通常 ≤ 数十文件）⇒ 单次耗时可控；不扫全仓。

### (c) Tab 懒加载降级（**可选项**，需②审确认是否纳入）

**问题**：QMT 导出 Tab（`quantstudio/gui/tabs/qmt_export_tab.py`，已入库 `886f294`）在内核 `qmt_deploy` 缺失时**裸异常**。

**方案**：Tab 采用**懒加载**——首次真正使用（点按钮）时才 import 内核；import 失败 ⇒ 弹**可读提示**（「QMT 部署内核未安装/未入库，请先完成部署器安装」＋ 指引路径），**不裸异常、不阻断 GUI 启动**。

**影响面**：`quantstudio/gui/tabs/qmt_export_tab.py` ＋ GUI 启动路径；属**新增能力型**（错误路径改善），非纯恢复 ⇒ **须与 (a)(b)(e) 分型申报**，可**独立成笔**。

### (d) 同型专项扫描固化（补 G3）

**方案**：把本轮的一次性扫描脚本化（`scripts/scan_untracked_dependency_gaps.py`），**纳入例行**（可挂 pre-push 只读告警，或 CI 周期任务）：

- 扫全仓已跟踪 `.py` 的 import vs 未跟踪集 ⇒ 输出缺口清单 ＋ 存量计数（基线 **15 处 / 2 簇**）。
- **首轮必须清零**（本轮 (a)(e) 修复后复测）⇒ 后续任何新增缺口在入库前即被看见。
- 与 (b) 互补：(b) 是**推前闸**（阻断型），(d) 是**盘点**（可见型）。

### (e) 簇 B 补提交（用户 11-02 三裁并入）

| # | 路径 | 大小 | 性质 |
|---|---|---|---|
| e1 | `tests/fixtures/delivery/__init__.py` | 795 B | **纯新增**（HEAD 不含 ⇒ 远端 15 个 delivery 测试 import 失败） |
| e2 | `tests/fixtures/delivery/env.py` | 10,844 B | 纯新增 |
| e3 | `tests/fixtures/delivery/main_db.py` | 5,925 B | 纯新增 |
| e4 | `tests/fixtures/delivery/crash_runner.py` | 1,619 B | 纯新增 |
| — | `__pycache__/*.pyc`（3 个） | — | **不入库**（构建产物） |

**验收判据**：**远端 CI（delivery 族 15 测试）绿**（用户指定判据）；本地侧同族测试全绿。

---

## 四、影响面

| 触及面 | 说明 |
|---|---|
| 共享层（入库即触） | `quantstudio/strategy_compiler/`（3 件）＋`tests/`（5 件）＋`config/`（1 件）⇒ **本轮必过同步门 ⑩**（副本同步，零豁免） |
| 门禁 | `.githooks/pre-push` ＋ 新脚本（(b)(d)）⇒ 推前行为变更，须过门禁自测 |
| GUI | 仅 (c) 纳入时触及 `quantstudio/gui/tabs/` |
| 转换管线 | `orchestrate_source` 新增形参（默认 False）⇒ 旧调用路径零影响；`--deploy-qmt` 为 opt-in |
| 远端 CI | (e) 修复 delivery 族；五件套补齐后 QMT 部署链远端可运行 |

---

## 五、纯增益三审计（铁律 18 前置）

| 审计项 | 结论 | 依据 |
|---|---|---|
| **是否影响项目其他功能** | **否** | a2/a3 新增项默认 `False`／opt-in ⇒ 旧调用与旧 CLI 行为零变化；(b)(d) 为门禁/盘点，只增不减；（c）若纳入仅改错误路径 |
| **是否影响回测性能** | **否** | 改动为导入期/调用期分支，不在回测热路径；(b) 门禁仅在 push 时执行 |
| **是否影响回测精度** | **否** | 无数据口径、无取数边界、无复权口径改动；(b)(d) 不触运行时 |

⇒ **纯恢复型**成立（(c) 若纳入则单列为**新增能力型**，不与纯恢复混办——铁律 18）。

---

## 六、验收判据（可复核）

| # | 判据 | 命令/方式 |
|---|---|---|
| V1 | 五件套 + 簇 B 四件**入库且同一/相邻 commit** | `git show --stat <sha>` 逐笔核对清单 |
| V2 | 工作区该 9 件无残留未跟踪/M 态 | `git status --porcelain -- <9 路径>` 空 |
| V3 | **依赖闭包门**拦截有效 | 构造样例（已提交文件 import 未跟踪模块）⇒ 门禁 exit≠0 且打印缺口 |
| V4 | 依赖闭包门**不误伤** | 对 #19/#18 正常批跑门禁 ⇒ exit 0 |
| V5 | **同型扫描清零** | `scripts/scan_untracked_dependency_gaps.py` ⇒ 缺口 15→**0**（修后复测） |
| V6 | **delivery 族远端 CI 绿** | 远端 CI：delivery 族 15 测试 PASS（用户指定判据） |
| V7 | 转换管线回归 | `tests/test_qmt_deploy.py` 全绿 ＋ 既有 api_portability 六策略重转 PASS（铁律 07） |
| V8 | 门禁自测 | `sh -n .githooks/pre-push` ＋ 非 main 分支命中预算门 exit 0（沿用既有自测口径） |
| V9 | 同步门 ⑩ | 主仓 push 后副本 `fetch+merge` → check-drift 九项 PASS ＋ ci-smoke 共享层段 PASS（零豁免） |

---

## 七、回退条件与手段

| 触发 | 回退 |
|---|---|
| V1/V2 不符（清单缺件/范围越界） | `git reset --soft HEAD~1` ＋ `git reset` 撤暂存（**不删工作区文件**）；该笔**不推送** |
| V3/V4 任一失败 | 回退门禁改动（`git checkout -- .githooks/pre-push scripts/check_dependency_closure.py`），保留 (a)(e) 独立入库 |
| V5 未清零 | 逐条按扫描缺口补齐（**逐笔精确 add**），**禁 `git add -A`**；未清零不推送 |
| V6/V7 回归红 | 该笔**不推送**；按 `git stash show --stat <hash>` 核对后按路径单文件 `git show <hash>:<path>` 回退（铁律 03） |
| 远端 CI 红 | 立即 `git revert <sha>`（已推送）＋ 双远程核一致 ＋ 台账登记事故 |

**写前快照四步**（批量/破坏性操作前）：`git add -A -- <本会话涉及路径>` → `git stash create -m "baseline-<ts>"` → **随即 `git stash store <hash>`** → `git reset`；hash 记入会话记录。

---

## 八、提交与出仓纪律

- **入库单元**：五件套（a1–a5）**同 commit**；簇 B（e1–e4）**同 commit**（可与 (a) 分两笔，但各自内部必须完整）；(b)(d) 门禁/脚本独立笔；(c) 若纳入独立笔。
- **精确 add**：`git add -- <精确路径>`，**禁 `git add -A`／`git add .`**（铁律 10）；`.pyc`/`__pycache__`/`*.bak` **一律不入库**。
- **uuid message**：message 文件名含 `uuid4().hex[:8]`；写后核 `git log -1 --format='%s'` 首行（铁律 共享工作区纪律）。
- **出仓**：⑤ 用户确认 → 随 #18/#19 批出仓（执行卡四查：`--pool` 池外 0 → 7b refspec 断言 → `"${anchor}:main"` → 三方核 vs 锁存锚）＋ 留痕 ＋ 共享层触面**过同步门 ⑩**。

---

## 九、知识反哺检索留痕（铁律 21/22 R1）

知识反哺：[命中] `qmt-pipeline-convergence`（族=项目收敛台账 / 域=QMT 转换管线 / 文件名=docs/qmt-pipeline-convergence.md，2026-11-02）——M5.2 基建批立项与件号谱系，据此定位「部署器」= 件4 载体。
知识反哺：[命中] `fundamentals-contract-matrix`（族=QMT wrapper 契约 / 域=M5.2 件1 矩阵门 / 文件名=docs/evidence/fundamentals-contract-matrix.md，2026-11-02）——M5.2 批次内各件落位关系，用于确认件4 的上游依赖。
知识反哺：[无命中] `knowledge/**` 结构化条目库中「未跟踪依赖闭包／push 前依赖闭包闸」条目（按族+domain 检索，2026-11-02）——**本方案为该类缺口的首个显式立项**，建议本轮后新增条目（族前缀待项目侧确认）。

**本轮沉淀**：待实施轮按 R4 落定（`kb_draft.py` 过 `kb_hook.validate_record`）。

---

## 十、待②审裁定的开放项

1. **(c) Tab 懒加载降级是否纳入本轮**（新增能力型，须与纯恢复分型；若纳入建议独立笔）。
2. **(b) 闭包门的阻断强度**：默认 **fail-closed 阻断**（推荐）vs 先期告警模式（一轮观察期）。
3. **(d) 扫描固化的执行位置**：pre-push 只读告警 vs CI 周期任务 vs 两者。
4. **入库单元切分**：五件套同 commit ＋ 簇 B 同 commit（两笔）——是否合并为一笔（用户已裁「簇 B 并入本轮」，本项是 commit 粒度确认）。
