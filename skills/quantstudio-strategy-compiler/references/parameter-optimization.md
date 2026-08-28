# Parameter Optimization (R5.4) — 方法与预算规范

> 归属：quantstudio-strategy-compiler ｜ 契约版本：optimization_study_report 1.0 ｜ 生效：2026-08-28
> 权威设计文档：`docs/strategy-compiler/parameter-optimization-design.md`（终审通过，M1-M3 已并入）
> Phase 1 基础：R5.5 套件与 G1-G6 门控（`references/robustness-gates.md`）不变；R5.4 是其**前置可选研究阶段**。

## 定位与纪律

R5.4 = 可选参数寻优研究（R5 之后、R5.5 之前）。**三重启用条件缺一不可**：design 2.3 `parameter_optimization_contract.enabled=true` + 搜索空间非空 + R2.5 verbatim 确认（confirmation_evidence 键 `parameter_optimization_contract`：搜索空间 + 成本公式 + 目标函数展示）。缺省/禁用 → `NOT_APPLICABLE`，管线与 Phase 1 逐位一致。

**研究完成 ≠ 发布许可**：提案必须经客户 verbatim 再确认（接受 → design 默认值更新 → 重走 R3→R4→R5→R5.5；拒绝 → 原参数直接进 R5.5）。

## 搜索空间契约（design 2.3）

```json
"parameter_optimization_contract": {
  "enabled": true,
  "engine": "grid" | "optuna",
  "n_trials": 30,            // 仅 optuna；≤50 fail-closed
  "timeout_seconds": 1800,   // 双熔断之一；触发 → INCOMPLETE_TIMEOUT
  "inner_folds": 2,          // ∈ {1,2,3}
  "objective": "mean_daily_excess_return",   // Phase 2 唯一目标，钉死
  "search_space": { "param_name": {"type": "int|float|categorical", ...} }
}
```

fail-closed 约束：可调参数 ≤6；网格组合数 ≤50（超出 BLOCK，不截断）；low<high / choices≥2；n_trials≤50；engine=optuna 必须带 timeout_seconds。

## WF 嵌套（M1 训练区公式）

- 外层 = Phase 1 五折切分器（时序连续不重叠、余数归最早折）；
- **折 k 训练区 = 窗口起点 至 折 k 起点**（前闭后开）；折 1 区间为空 → 种子折（无内层搜索、非 OOS 点）；
- **外层 OOS = 折 2..5**：内层最优参数在折 k 单次运行的真实 OOS 超额日收益均值；
- **内层** = 训练区内切 inner_folds 个时序子折；每候选参数组跑 inner_folds 次引擎（空仓起、状态独立），目标 = 内层验证切片超额日收益均值（基准 = daily_stats `benchmark` 列）；
- **短训练区 SKIP**：折 k 训练区 < inner_folds×20 交易日 → 该折 SKIP，不计多数票分母、不入 OOS 统计；
- 折间状态独立空仓起；折起点前 provider 历史可作指标预热（规则 24），不构成前视。

## 成本公式（R2.5 确认包必展示）

引擎运行数 = Σ(有效外层折=非 SKIP，理论 ≤4) × 每折试验数（grid=组合数 / optuna=n_trials）× inner_folds；外层终评另加 ≤4 次。最坏界：4×50×3+4=604 次（grid 由组合数≤50 封顶在 4×50+4）；timeout_seconds 独立兜底。

## 聚合规则（提案产出，钉死）

逐参数取**非 SKIP 外层折**的内层最优值多数票；无多数（全异）或非 SKIP 折 <2 → 该参数保持设计默认并在报告标 `UNRESOLVED`。禁止全窗重调（在样本内寻优提案）。

## Optuna 规范与中断可审计

- `import optuna` 受保护：engine=optuna 且缺失 → BLOCK（提示安装或改 grid）；**零新增硬依赖**；
- TPE + MedianPruner；sampler seed = SHA-256(trades+config)[:8] 派生（PCG64 同源纪律）；
- **sqlite 持久化**：`<workspace>/robustness/optuna_study.db`——中断后已完成试验落盘可审计可续查；报告记录 storage 路径 + 已完成试验数；
- timeout 触发 → 状态 `INCOMPLETE_TIMEOUT`，已完成试验结果保留；
- 每次试验的 param_overrides.json / config.csv / 三件套 SHA-256 绑定进报告。

## param_overrides.json 生命周期不变量（M2）

仅存在于 R5.4 研究运行窗口内；**R5.4 收尾（接受/拒绝两路）一律删除**；**任何状态不得跨越 R6**（publish 门检③末端执法）；生命周期事件（创建/每次覆写/删除）记入研究报告与台账。钩子语义：策略同目录读取该文件；文件缺失/为空 → 设计默认值（行为逐位不变）；**含搜索空间外键 → 运行期 fail-closed 报错**。

## 防过拟合与防滥用五道防线

1. WF 嵌套（内搜外验）——禁止全程调参再回测；
2. 多数票聚合 + SKIP 折不分母；
3. R5.5 门控在调参后策略上独立复跑 + 迭代上限 2 轮不变；
4. 每管线周期研究 ≤1 次；门控 FAIL 后的新研究须全新 R2.5 授权；
5. 预算双熔断 + 网格组合上限。

## Lint 纪律（validate_agent_strategy.py 新增检查）

仅当 contract.enabled=true 触发；**仅对契约声明键检查**（不扫描其他参数）：声明键必须经 P(name, default) 钩子读取，违者 `OPTIMIZATION-PARAM-NOT-VIA-HOOK` BLOCK。非声明键、禁用契约——零影响。
