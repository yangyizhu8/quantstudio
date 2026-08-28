# QuantStudio 策略生成 Skill 参数寻优体系（Phase 2）设计方案

- skill：quantstudio-strategy-compiler ｜ 目标版本：`1.0.0-r54-optimize`（baseline 不变；新增契约 `optimization_study_report 1.0`；**agent design schema 2.2 → 2.3**）
- 日期：2026-08-28 ｜ 状态：终审通过（ZCode；M1-M3 已并入）
- 上游基础：Phase 1（R5.5，`0.9.0-r55-robustness`，已推送 `9a9a774`）；Phase 1 裁定延续：寻优=可选、启用需 R2.5 显式授权搜索空间、寻优结果是提案须回 R2.5 重新确认
- 修订记录：2026-08-28 审计意见 M1-M3 并入（外层训练区公式与种子折语义 / param_overrides.json 生命周期不变量 / 规则编号现状核对）

## 1. 问题定义

Phase 1 的 R5.5 解决"给定参数的统计鲁棒性"，不解决"参数从何而来"。参数目前由 R2 设计期一次性确定，无数据驱动的寻优依据。Phase 2 引入**参数寻优研究阶段 R5.4**（可选，位于 R5 与 R5.5 之间）：Optuna TPE / 网格在**授权搜索空间**内寻优，**WF 嵌套防过拟合**（外层折做真 OOS 验证，杜绝全程调参再回测），产出**调参提案**；提案必须经客户 verbatim 再确认并重走 R3→R4→R5→R5.5 才可发布。

非目标：Jinja/legacy 渲染路径的寻优支持（非默认路径）、多目标寻优、全文搜索空间自动发现（只寻优 design 显式声明参数）、策略逻辑结构搜索。

## 2. 改动范围

**新增 4 文件**：
1. `scripts/run_optimization_study.py` — R5.4 编排器（嵌套 WF 网格/Optuna 搜索、预算熔断、多数票聚合、提案产出；**Optuna study 持久化落盘：中断后已完成试验可审计可续查（sqlite storage，随报告同目录）**）；
2. `schemas/optimization_study_report.schema.json` — v1.0；
3. `references/parameter-optimization.md` — 方法/成本公式/聚合规则/防过拟合防线存档；
4. `scripts/optimization_selftest.py` — 编排自检（stub 引擎，覆盖预算熔断/未声明键拒绝/聚合规则含 SKIP 折/提案 diff）。

**修改 6 文件**：
5. `schemas/agent_strategy_design.schema.json` — 2.2→2.3：新增可选 `parameter_optimization_contract`（缺省=禁用=Phase 1 行为）；
6. `scripts/create_agent_workspace.py` — 脚手架注入参数覆写钩子（`P(name, default)` 访问器 + 策略同目录 `param_overrides.json` 读取；文件缺失/为空 → 设计默认值，行为逐位不变）+ 落盘授权搜索空间副本；
7. `scripts/validate_agent_strategy.py` — 新增 lint（**仅当 contract.enabled=true 触发；仅对契约声明键检查**，不扫描其他参数）：搜索空间参数必须经 P() 读取，违者 BLOCK（`OPTIMIZATION-PARAM-NOT-VIA-HOOK`）；
8. `scripts/publish_agent_strategy.py` — 三项门检：①授权搜索空间但台账无研究记录且无 verbatim 拒绝 → 拒绝发布；②提案已接受但发布参数与提案不一致 → 拒绝；③发布目录存在 `param_overrides.json` → 拒绝发布（M2 生命周期不变量的末端执法）；
9. `SKILL.md` — R5.4 阶段章节、新增绝对规则（**M3：编号在实施时与 SKILL.md 现状逐一核对后顺序编号**——Phase 1 末条为 35，预期 36-39，若他线已占用则顺延并全文一致化）、Prohibited 补条、Commands 补条、版本行；
10. `schemas/run_card.schema.json` — stage 枚举 + R5.4（M4 式兼容声明）。

**实施期文档同步**：README、docs/strategy_toolbox.md（R5.4 表述）。

**明确不改动**：templates/（legacy Jinja 路径不支持寻优）、引擎、转换管线、PyQt、validate_runtime_shapes.py、Phase 1 的 run_robustness_suite.py 与 robustness 契约。

## 3. R5.4 阶段定义（可选）

**位置**：R5 PASS 之后、R5.5 之前。**启用条件（三缺一不可）**：design 2.3 `parameter_optimization_contract.enabled=true` + 搜索空间非空 + R2.5 verbatim 确认（新增 confirmation_evidence 键 `parameter_optimization_contract`，含搜索空间、成本公式估算、目标函数声明）。**禁用（默认）**：契约缺失/`enabled=false` → R5.4 标 `NOT_APPLICABLE`，管线与 Phase 1 逐位一致（M4 兼容：旧 ledger 无 optimization 字段 = 未进入，不报错）。

### 3.1 搜索空间契约（design 2.3 新增，钉死）

```json
"parameter_optimization_contract": {
  "enabled": true,
  "engine": "grid",                      // "grid" | "optuna"
  "n_trials": 30,                         // 仅 optuna；≤50 fail-closed
  "timeout_seconds": 1800,                // 熔断；触发 → INCOMPLETE_TIMEOUT（诚实状态）
  "inner_folds": 2,                       // ∈ {1,2,3}
  "search_space": {
    "target_holdings": {"type": "int", "low": 10, "high": 30, "step": 5},
    "stoploss_pct": {"type": "float", "low": -0.12, "high": -0.05}
  }
}
```

约束（fail-closed）：可调参数 ≤6；网格组合数 = 各参数取值积 ≤ **50**（超出 → BLOCK 并提示收缩，不截断）；类型 ∈ {int, float, categorical}；low<high、choices 非空。**成本公式必须在 R2.5 确认包中展示**：引擎运行数 = Σ_k(有效外层折) × 每折试验数(网格=组合数 / optuna=n_trials) × inner_folds；网格缺省组合数即上限。

### 3.2 WF 嵌套方案（M1 修订钉死）

- **训练区公式**：外层折 k 的训练区 = **窗口起点 至 折 k 起点**（前闭后开；对折 1 该区间为空 → 折 1 无内层搜索、不作 OOS 点，即**种子折**——"种子折"仅指非 OOS 点这一身份，不参与多数票）；
- **外层 OOS 验证 = 折 2..5**（4 个真 OOS 点）：内层最优参数在折 k 上单次引擎运行的真实 OOS 超额日收益均值（基准口径同 Phase 1：daily_stats `benchmark` 列）；
- **内层**：训练区内切 inner_folds 个时序子折；每个候选参数组 = inner_folds 次引擎运行（状态独立空仓起），内层目标 = 内层验证切片的超额日收益均值；
- **短训练区规则（M1）**：折 k 训练区交易日数 < inner_folds×20（每内层折最少 20 日）→ 该外层折标 `SKIP`，**不计入多数票分母**、不出现在 OOS 统计（与 Phase 1 NO_TRADE 折同构的防污染设计）；
- **聚合规则（提案产出，钉死）**：逐参数取**非 SKIP 外层折**内层最优值的多数票；无多数（全异）或非 SKIP 折 <2 → 该参数**保持设计默认值**并在报告标 `UNRESOLVED`；禁止"全窗重调"（在样本内寻优提案）；
- 每次试验的 config/param_overrides.json/三件套 SHA-256 全部绑定进报告；PYTHONHASHSEED 同主运行规约。

### 3.3 Optuna 依赖策略与中断可审计（实施附带①）

`import optuna` 受保护：缺失时按 contract.engine 分派——`grid` 不受影响；`optuna` → BLOCK 并提示安装或改用 grid（**不新增硬依赖**）。Optuna TPE 采样器以确定性 seed（工件哈希派生，同 Phase 1 纪律）初始化，MedianPruner 启用，n_trials/timeout 双熔断。**study 持久化**：Optuna 使用 sqlite storage（`<workspace>/robustness/optuna_study.db`）——**中断后已完成试验全部落盘可审计可续查**，报告记录 storage 路径与已完成试验数；timeout 触发 → 状态 INCOMPLETE_TIMEOUT + 已完成试验结果保留。

### 3.4 提案 → 再确认 → 重走管线（确认纪律闭环）+ 覆写文件生命周期（M2）

研究完成 ≠ 发布许可。流程：R5.4 报告产出提案（params diff vs 设计默认）→ **客户 verbatim 再确认**（接受提案 / 拒绝保持原参数）→ 接受则 design 默认值更新（design 2.3，confirmation_evidence 追加）→ **重走 R3 重生成 → R4 → R5 → R5.5 门控**（Phase 1 套件在调参后策略上原样运行）→ R6。拒绝则原参数直接进 R5.5。

**param_overrides.json 生命周期不变量（M2，钉死）**：该文件仅存在于 R5.4 研究运行窗口内；**提案接受或拒绝后（即 R5.4 收尾时）一律删除**；**任何状态下不得跨越 R6**（publish 门检③为其末端执法）；生命周期事件（创建/每次覆写/删除）记入研究报告与台账。

### 3.5 防过拟合与防滥用五道防线

1. WF 嵌套（内搜外验，禁止全程调参再回测）；2. 多数票聚合 + SKIP 折不分母（禁单点最优、防小样本折污染）；3. R5.5 门控在调参后策略上独立复跑 + 迭代上限 2 轮不变；4. **研究次数上限：每管线周期 ≤1 次研究**，门控 FAIL 后的新研究须全新 R2.5 授权（封堵"调到过验"）；5. 预算双熔断 + 网格组合上限。

## 4. 影响面

- 管线：仅新增可选 R5.4；禁用/缺省/旧 design 2.2/旧 ledger 路径与现状逐位一致；
- 性能：仅授权寻优时产生有界引擎运行（成本公式前置展示）；禁用时零开销；
- 兼容：design 2.2 工件合法（=禁用）；新策略脚手架钩子在无覆写文件时行为逐位不变；已存在 workspace 不受影响；
- 依赖：零新增硬依赖（optuna 可选）。

## 5. 验收标准（八项）

1. **真实小规模研究回放**：对既有策略 workspace（如 fscore_rsrs）临时授权微型搜索空间（网格 ≤6 组合、inner_folds=1）→ 报告 schema PASS、逐试验 config 哈希绑定、聚合提案合理、成本记录完整（跑完撤销临时授权，不改原 design）；
2. **覆写钩子等价性**：无 `param_overrides.json` 时钩子存在但行为与设计默认逐位一致（selftest 断言）；
3. **未声明键拒绝**：覆写文件含搜索空间外的键 → 运行期 fail-closed 报错（防静默语义变更）；
4. **预算熔断**：网格组合 >50 → BLOCK；timeout 触发 → INCOMPLETE_TIMEOUT 诚实状态；
5. **聚合规则（含 M1 SKIP）**：构造外层折含短训练区 SKIP + 内层最优分歧 → SKIP 不入分母、多数票/UNRESOLVED 判定正确（selftest 内建）；
6. **lint**：enabled=true 且契约声明键未经 P() → R4 BLOCK（`OPTIMIZATION-PARAM-NOT-VIA-HOOK`）；非声明键不检查；
7. **发布三门 + M2 生命周期**：授权未研究 → 拒绝；提案已接受但发布参数与提案不一致 → 拒绝；发布目录含 param_overrides.json → 拒绝；R5.4 收尾后覆写文件必删（任意结果路径断言）；
8. 回归：Phase 1 selftest --all + quick_validate 全绿（禁用路径零回归）。
证据写入 `docs/evidence/parameter-optimization-<date>.md`。

## 6. 回退条件

任一验收不过 → 全部改动 git checkout（实施前记录 HEAD 干净点），零残留；研究运行期失败 → 台账停 OPTIMIZATION_FAILED + 原因，主运行证据只读零污染；禁用路径始终可用。

## 7. 实施序（终审通过后）

1. design schema 2.3 + references → 2. run_optimization_study.py（含 sqlite 持久化）+ optimization_selftest.py → 3. 脚手架钩子 + lint → 4. SKILL.md（R5.4/新规则编号现状核对后顺序编号/Prohibited/Commands/版本行）+ run_card + publish 门 + README/strategy_toolbox 同步 → 5. 八项验收 → 6. 证据文档 + 汇报 → 用户确认 → 双仓库推送。
