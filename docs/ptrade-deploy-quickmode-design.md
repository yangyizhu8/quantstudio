# PD-0008 方案：GUI 快捷模式 + 统一输出目录（六步①方案）

> 立项依据：用户 2026-10-19 完成首次真实部署转换（tech_etf_mvo，七阶段全 PASS）后提出体验需求——
> 「转换非常麻烦，应该像回测一样选本地存量策略直接转，产物统一存到指定目录（如
> Demo_account_Real_account），其下按策略名建子目录」。
> 状态：①方案（待②审计）——**未实施、未提交**。
> 附带：本方案同时界定 P5 未知 API 误报修复（独立验收门）与「两段自动转换」延伸评估（单列）。

## 1. 问题定义

**现状（实测取证 2026-10-19）**：

| 项 | 现状 | 证据 |
|---|---|---|
| 输入 | 只能手写/选 `deploy.yaml`（七段配置） | `gui/tabs/deploy_tab.py` L84-92：仅 `LineEdit` + 「浏览...」 |
| 产物位置 | 固定 `output/deploy/<strategy_id>/`，按 ASCII id 分目录 | `deliver.py` L24 `OUTPUT_ROOT = Path("output")/"deploy"`；L225 `out_dir = (out_root or OUTPUT_ROOT)/cfg.strategy_id` |
| strategy_id 约束 | 仅 `[A-Za-z0-9_-]+`（不可含中文） | `config.py` L27 `_STRATEGY_ID_RE = ^[A-Za-z0-9_\-]+$`；L114 校验 |
| out_root 参数 | `deliver()` 已有 `out_root` 形参，但 runner 从不传（恒默认根） | `runner.py` L243 调用未传 out_root |

**用户痛点**：①手写 yaml 门槛高（strategy_id/source/execution/persistence 七段）；②策略产物与
回测策略不在同一处，需手动拼路径；③产物散落 `output/deploy/<ascii id>/`，看目录名无法对应中文策略名。

## 2. 需求点（用户口径）

1. **GUI 快捷模式**：deploy tab 增本地存量策略选择器（扫描 `output/ptrade_export/*/` 下 `*_ptrade.py`）；
   选中后自动生成部署配置（strategy_id 由产物名派生、执行档位三选一下拉默认 `daily_eod_1450`、
   persistence/guards 走默认值），免手写 yaml 一键转换；**现有 yaml 路径模式保留为高级入口（不删不改）**。
2. **统一输出目录**：默认输出根改为 `Demo_account_Real_account/`（仓库根，尊重用户原话），
   其下按**策略名称**建子目录。两个必须显式设计点：
   - ① `strategy_id`（ASCII）与**目录名**（可中文）分离；
   - ② 同策略重转的覆盖/版本语义（防新旧产物混存）。
3. **契约变更点**：`output/deploy` 默认值的去留，须显著标注并由用户裁定。

## 3. 设计决策

### D1 目录结构（统一输出根）

```
<repo>/Demo_account_Real_account/            ← 默认输出根（可由 output.root / --out-root 覆盖）
  <策略目录名>/                               ← 中文原名（文件系统安全过滤后）
      <strategy_id>_trade.py                  ← 部署产物（ASCII 文件名，平台侧要求）
      report.md / runbook.md
      _archive/<YYYYMMDD-HHMMSS>/             ← 上一版三件（R-1 重转归档）
```

- **仓库根**放 `Demo_account_Real_account/`（尊重用户原话；不埋在 `output/` 下），
  同步在 `.gitignore` 登记该目录（产物不入库）——**同步内容必含此项**。
- 目录名过滤：`<>:"/\|?*` 与控制字符 → `_`；去首尾空格与点；长度 ≤ 80；重名冲突加 `-2`。

### D2 strategy_id 与目录名分离（核心设计点①）

| 量 | 口径 | 例 |
|---|---|---|
| **目录名**（人看） | 上游导出目录名原文（可中文） | `CANSLIM突破成长选股策略` |
| **strategy_id**（机用） | 名称 ASCII 片段归一：保留 `[A-Za-z0-9_-]`，其余折叠为 `_`，压缩重复下划线，`strip("_-")`，≤40 字符；**若 ASCII 片段 < 3 字符** → 追加 `_<名称 sha1 前 8 位>`（稳定：名称不变则 id 不变） | `CANSLIM`、`tech_etf_mvo_rotation`、`strat_3f2a9c1b` |
| **state_file 默认** | `<strategy_id>_state.pkl`（沿用 `DEFAULT_STATE_FILE`） | — |

**为什么不能拿内容哈希当 id**：`strategy_id` 参与默认 `state_file` 命名，内容一变 id 就变会导致
部署端状态文件对不上（状态丢失）。故 id 只由**名称**派生，稳定性优先。

### D3 输出根契约变更（显著标注，需用户裁定）

- 新增配置项 **`output.root`**（字符串；缺省 `Demo_account_Real_account`，相对仓库根解析，亦接受绝对路径）；
  CLI 增 **`--out-root`** 顶层覆盖（优先于 yaml）。
- **契约变更点**：`output/deploy` 从「唯一默认」降为「显式可选值」。影响面：
  - 未显式配置 `output.root` 的**既有 yaml**：产物位置从 `output/deploy/...` 迁到 `Demo_account_Real_account/...`；
  - **既有固件 yaml**（`tests/deploy_fixtures/**`）：实施时**显式写入** `output.root: output/deploy`
    以保持既有 49 用例的路径确定性（零回归），新默认只作用于「未配置」的新调用。
- 三路 parity 不受影响（同配置 → 同路径 → 同 SHA256）。

### D4 同策略重转语义（核心设计点②；②审条件①定稿）

- **默认：归档上一版 + 覆盖主位**——目标目录已存在 `<strategy_id>_trade.py` 时，先把三件
  （product/report/runbook）移动到 `_archive/vNNN/`（**序号不用时间戳**：保证同输入多次转换的
  目录结构可复跑、不污染产物/报告文本的确定性——§7.3 parity 基础），再写新件；
- **`output.keep_versions` 口径（定稿，三档互斥、无隐含默认）**：
  `null`/缺省 = 归档**不限份数**；`0` = **关闭归档**（直接覆盖）；`N>0` = 仅保留最近 N 份归档；
- 重名判据用 `strategy_id`（同目录名 → 同 id → 同目录），不靠 mtime 猜测；
- 目录名长度上限 **≤ 40 字符**（②审条件②：防 Windows MAX_PATH 260；`safe_dir_name()` 强制截断）。

### D5 GUI 快捷模式（薄壳纪律不变）

- **UI 分区**：`tabs` 两页——「快捷模式（选本地策略）」默认页 / 「高级模式（deploy.yaml）」；
  高级页 = 现有控件原样搬迁（**不删不改行为**）。
- **内核侧新增** `quantstudio/ptrade_deploy/quickmode.py`（纯函数，可单测）：
  - `discover_strategies(scan_root) -> list[{name, product_path, mtime, strategy_id}]`
    （**递归**扫描 `output/ptrade_export/` 的 `*_ptrade.py`——实测 45 个产物中 **22 个位于两层**
    〔如 `fscore_rsrs/<策略名>/`、`broken_board_reversal/strategy/`〕，**仅一层 glob 会漏近半**；
    策略名取产物所在目录名；同目录多产物取 mtime 最新；默认过滤 `_verify*` / `_probe*` 前缀目录
    〔实测 5 个验证副本〕，可配置纳入）；
  - `derive_strategy_id(name) -> str`（D2 规则）；
  - `build_quick_config(name, product_path, profile, output_root) -> str`（生成等价 yaml 文本）。
- **GUI 壳**：只做「选策略 → 选档位 → 点按钮 → 调 `run_deploy_kernel(生成的 yaml)`」的接线；
  生成文件落 `output/.qs-deploy-quick/<strategy_id>.yaml`（暂存，便于追溯与复跑）。
- **对等判据**：快捷模式生成的 yaml 与手写 yaml **同内核同输入**（`run_deploy_kernel` 唯一调用点不变）→
  三路 parity 哈希门禁照旧。

### D6 P5 未知 API 误报修复（附带项①，独立验收门）

**根因（已确证）**：`preflight.call_name` 对 `X.method()` 返回**根对象名 X**（为兼容 `log.info` 口径）；
而 `gates._local_names` 只收集 `FunctionDef/ClassDef/Assign/Import`，**未收集函数参数、for 目标、
with as、except 名、walrus 目标、推导式目标、解包赋值**——于是 `history.keys()`（history 为参数）、
`ch.isdigit()`（ch 为 for 目标）被判「契约表外 API」。

**修复（纯恢复型，双判据）**：

1. `_local_names` 补全绑定面（7 类：`ast.arg` / `For.target` / `withitem.optional_vars` /
   `ExceptHandler.name` / `NamedExpr.target` / `comprehension.target` / 解包目标）；
2. 属性根名分类：`call_name` 返根名时——根名属**局部绑定** → **不报**（数据对象方法调用，非平台 API）；
   根名属模块豁免/契约表 → 现行逻辑；根名既不属局部也不属豁免/契约表 → **仍报**（保留正例捕获）；
3. 引擎注入全局 `g` 纳入豁免（`g.state.save()` 类形态不报）。

**验收（正负双测）**：正例 `unknown_api_xyz()` 仍报；`unknownmod.do_stuff()`（根名未知且非局部）仍报；
负例 `history.keys()` / `ch.isdigit()` / `g.state.save()` / `for ch in s:` 内方法调用 不报。

### D7 附带项归属

- **P5 修复并入本批**（同方案、**独立 commit + 独立验收门**）：两者同属 qs-deploy 框架层且互不依赖，
  分开提交便于单独回退。
- **「本地回测策略 → 两段自动转换」单列呈报，不纳入本批**。工作量评估：需（a）import 段参数化
  （转 PTrade 的 ETF 池固化起始日、目标参数）、（b）导出目录命名映射、（c）失败与半成品回滚；
  估 **约 1 个工作周期**，风险中（参数语义需用户确认）。**建议在 PD-0008 落地并稳定使用后单独立项。**

## 4. 三型判定（分开声明）

| 项 | 判定 | 说明 |
|---|---|---|
| GUI 快捷模式 | **新增能力型** | 新增入口；既有 yaml 模式与 CLI 行为零变化；不改变任何既有 verdict |
| 默认输出根变更 | **契约变更**（非纯增益，需用户裁定） | 未显式配置的调用产物位置迁移；既有固件显式钉住旧根 → 用例零回归 |
| P5 误报修复 | **纯恢复型** | `unknown_apis` 警告集合收窄（噪声消除），PASS/FAIL 判定不变；正负双测锁定 |

## 5. 影响面与纯增益约束

- 硬约束：**既有 yaml 模式与 CLI 行为零变化**；三路 parity（内核/CLI/PyQt）SHA256 不回退；
  既有 49 用例全绿。
- 风险 R1：`output.root` 新默认导致既有脚本按旧路径找不到产物 → 缓解：report/runbook 与 GUI
  报告区均打印**绝对产物路径**；CLI 增 `--out-root` 显式回退一档。
- 风险 R2：中文目录名在 Windows/PTrade 平台侧路径兼容 → 缓解：目录名仅本地归档用，
  平台侧上传文件名恒为 ASCII `<strategy_id>_trade.py`；目录名过滤非法字符。
- 风险 R3：快捷模式扫描大目录树（`output/ptrade_export/` 实测 32 个顶层目录 / 45 个产物文件）
  导致 UI 卡顿 → 缓解：扫描在 worker 线程 + 结果缓存；仅含 `*_ptrade.py` 的目录入列；`_verify*`
  副本默认过滤。

## 6. 验收标准

1. **快捷模式端到端**：GUI 选 `tech_etf_mvo_rotation` → 一键转换 → 七阶段全 PASS →
   产物落 `Demo_account_Real_account/tech_etf_mvo_rotation/tech_etf_mvo_rotation_trade.py`；
2. **三路 parity 不回退**：同一 yaml 经 内核/CLI/GUI 产物 SHA256 逐位一致（既有 + 新增用例如实跑）；
3. **中文策略名**：`CANSLIM突破成长选股策略` → 目录名保留中文、`strategy_id` 为 ASCII 且稳定
   （连续两次派生同值）；
4. **重转归档**：同策略二次转换 → `_archive/<ts>/` 内含上一版三件，主位为新件；
5. **P5 双测**：正例 2（`unknown_api_xyz()`、`unknownmod.do_stuff()`）+ 负例 4（`history.keys()`、
   `ch.isdigit()`、`g.state.save()`、for 目标方法调用）全绿；
6. **零回归**：既有 deploy 套件 49 用例全绿 + `check_fund_matrix.py --check` 哈希一致；
7. **快捷配置对拍（②审条件③）**：`output/.qs-deploy-quick/<id>.yaml` 与**等价手写 yaml**
   经同一内核 → 产物 SHA256 逐位一致（`test_quick_equals_manual_yaml_sha`）。

## 7. 回退条件

- 任一 parity 红、既有用例红、产物路径错位或 P5 正例漏报 → **立即回退**本项 commit；
- 若用户裁定「保留 `output/deploy` 为默认」→ D3 退化为「仅新增 `output.root` 可选配置 + GUI 默认指向
  `Demo_account_Real_account`」，不动全局默认（一条配置改动即可切换，代码无需重写）。

## 8. 知识反哺检索留痕

```
知识反哺：[命中] 系统性思维总纲（11-方法论与工程体系，2026-10-19）
知识反哺：[命中] 实战范例-双端对齐闭环全案（11-方法论与工程体系，2026-10-19）
知识反哺：[命中] 审计方法论-先证可达性再证敏感性与无先例断言定谳（11-方法论与工程体系，2026-10-19）
知识反哺：[命中] 工程化落地手册（11-方法论与工程体系，2026-10-19）
```

采纳要点：
- **系统性思维总纲**（L53「一切重复劳动 → 脚本；对账从两天人工到一键」）→ 支撑快捷模式
  把「手写七段 yaml」降为「选策略 + 选档位 + 一键」；
- **双端对齐闭环全案**（L67「一键产出」）→ 支撑快捷模式仍复用同一内核调用点，一键不等于另起实现；
- **审计方法论-先证可达性**（L14-15「误报源于只核敏感性未证可达性」）→ 直接支撑 P5 修复的
  **先证绑定可达性**判据（根名是否为局部绑定/参数，再判是否平台 API）；
- **工程化落地手册**（L48「测试套件一键跑 + 红名单对照」）→ 支撑验收第 6 项（既有套件 + 矩阵门）。

## 9. 排期与六步

①方案（本文件，2026-10-19 出件）→ **②审计（待）** → ③实施（写前快照 + 精确清单；P5 与快捷模式
分两个 commit）→ ④验收（本文件 §6 六项）→ ⑤用户确认 → ⑥随批统推 + README/`docs/strategy_toolbox.md`/
`docs/prompt_engineering.md` 同步 + `.gitignore` 登记 `Demo_account_Real_account/`（**实测当前未含，须新增**）。
