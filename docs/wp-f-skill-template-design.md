# WP-F 设计：策略生成 skill 模板升级（F1 六项 + B4 归并，2026-08-27）

- **流水线状态**：Step 1 方案（本文件）→ 待 ZCode 审计
- **纪律**：版本化（SKILL release bump）+ **存量零触碰**（只影响新生成策略；已发布策略文件不动、不重渲）
- **同步义务**（AGENTS.md 铁律）：README + `docs/strategy_toolbox.md` + `docs/prompt_engineering.md`
- **依赖**：B1/B2（P-D12 已交付）——F1⑥ delta 语义可依赖（T9 同构保证：接线层/模板/引擎三侧 delta 语义逐位一致，见 pd12 implementation-acceptance T13/T9）

---

## 1. 现状盘点（避免重复——skill 0.7.1 已含部分项）

| F1 项 | 现状（0.7.1） | WP-F 处置 |
|---|---|---|
| ① 止损基准 `cost_basis` 优先 | 止损模式雏形（L454 触发即锁+重试二审）**未提 cost_basis** | **强化**：止损基准 = `get_position().cost_basis` 优先，无持仓/无 cost_basis 时退避参考价 |
| ② halt 冻结防御性卖出通道 | 未显式 | **新增**：冻结期间保留防御性卖出通道 |
| ③ 审计三件套强制埋点 | ✅ **已固化**（R21 QS_REBALANCE/PORTFOLIO + 引擎 QS_FILL_AUDIT） | 保持（验收现 may 复核嵌入） |
| ④ 资金常量从 context 派生 | ✅ R18 runtime_total_value 已约束 hardcoded capital | 补强：`order_target_value` 目标值一律从 context 现值派生 |
| ⑤ 换仓缓冲带 | 未显式 | **新增**（R19 现金缓冲不足于 full-turnover；缓冲带规范） |
| ⑥ 补差语义默认 | 未含（旧模板可能全额） | **默认依赖** B1/B2 delta（T9 同构保证）；**B4 审计计数归入** |

## 2. 改动范围（全部在新生成策略模板/IR/skill 规则；存量零触碰）

### 2.1 止损基准（F1①）——skill 规则文本 + 生成模板

```
止损基准价格（优先级）：
  1. get_position(code).cost_basis（持仓成本，双端可移植——P-POS F3 实证平台 Position 有 cost_basis）
  2. 兜底：最近 get_history(fq='pre').close 末值（无持仓/无 cost_basis 时）
止损触发：当下价 <= 基准 × (1 - stop_loss_pct)
```

### 2.2 halt 冻结防御性卖出通道（F1②）

```
冻结（suspendFlag==1 或行情缺失）期间：
  - 买入冻结（不建立新仓）
  - 卖出保留防御性通道：若 stop 已触发且 sellable，仍尝试卖出（P-D13b 保真开关外，
    策略侧防御——平台 halted 语义下卖出不被拒）
```

### 2.3 换仓缓冲带（F1⑤）

```
调仓时对净换仓金额留缓冲带：target_position_value × (1 - buffer_pct)
  buffer_pct 默认 0.03（3%，覆盖费用/整手取整/一级估值差）
  ——不得宣称覆盖 full-turnover 融资（R19 纪律：full turnaround 用 basket/两段；close 模式同日资金可用）
```

### 2.4 补差语义默认（F1⑥）+ B4 审计计数

```
调仓用 order_target_value(code, target_value)（默认补差语义——B1/B2 delta 修复已交付，
接线层/模板/引擎三侧 T9 同构保证 target 语义一致）；
B4 审计计数（归并本项）：每期调仓记账 order_stats（下单笔数/金额/拒绝数）→
  并入 QS_REBALANCE_AUDIT witness 字段（不新增独立行，防日志膨胀）
```

### 2.5 资金常量派生补强（F1④）

```
禁止 hardcoded g.capital/固定 order_target_value 正数（R18 已有）；
order_target_value 目标一律 = context.portfolio.total_value（或现值）× 目标权重（runtime_total_value 模式）
```

### 2.6 涉及文件（全部 skill 资产，非引擎）

| 文件 | 改动 |
|---|---|
| `skills/quantstudio-strategy-compiler/SKILL.md` | release bump（0.7.1 → 0.8.0-f1 六项）；规则 R21-24 补充（cost_basis 止损/halt 卖出通道/缓冲带/补差默认）；B4 计数入 R21 |
| `skills/quantstudio-strategy-compiler/**/agent_strategy_design.json`（设计 IR 模板） | 新字段：stop_loss_basis=cost_basis 优先 / defensive_sell_during_halt=true / rebalance_buffer_pct=0.03 / order_semantics=delta |
| renderer/Jinja 模板（若存在策略生成模板） | 止损基准三选一逻辑 + halt 卖出通道 + 缓冲带 + 补差注释 |
| `README.md` / `docs/strategy_toolbox.md` / `docs/prompt_engineering.md` | 同步 F1 六项（铁律义务） |

## 3. 关键设计决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | 存量零触碰：只影响新生成策略（skill 规则+模板），已发布 6 策略文件不动 | master-plan WP-F 定义（P2 只影响新生成）；版本化 release bump |
| D2 | 止损基准 cost_basis 优先 + 兜底 close | 双端可移植（P-POS F3/F5 实证）；无持仓/无成本时兜底不崩 |
| D3 | halt 防御性卖出 = 策略侧通道（非引擎开关） | P-D13b 引擎保真开关另路；策略侧防御 hook 在新策略模板内 |
| D4 | 缓冲带 3% 默认（不宣称覆盖 full-turnover）| R19 纪律；close 模式资金同日可用 |
| D5 | F1⑥ 用 delta 默认（B1/B2 交付）| T9 同构保证（接线/模板/引擎三侧一致）——不再全额买入 |
| D6 | B4 审计计数并入 QS_REBALANCE_AUDIT witness（不新增行）| 防日志膨胀；审计三件套契约不变 |

## 4. 影响面

- **新生成策略**：止损基准/halt 通道/缓冲带/补差语义/资金派生全部默认生效；
- **存量 6 策略**：零触碰（不重渲、不改文件）；
- **skill release**：0.7.1 → 0.8.0（版本化标记，回退=回退 skill 目录）；
- **文档**：README/toolbox/prompt 同步（铁律）。

## 5. 测试/验收矩阵

| 用例 | 场景 | 断言 |
|---|---|---|
| T1 | 生成含 F1① 止损基准 | 模板含 cost_basis 优先 + close 兜底分支 |
| T2 | halt 防御卖出通道 | 冻结期间卖出不拒 |
| T3 | 缓冲带 3% | 调仓 target 乘 (1-0.03) |
| T4 | 补差语义默认 | 产物调仓 = order_target_value（非全额预折股） |
| T5 | B4 审计计数 | QS_REBALANCE_AUDIT 含 witness order_stats |
| T6 | 资金派生 | 无 hardcoded g.capital / 固定正 target |
| T7 | 存量零触碰 | 已发布 6 策略文件 hash 不变 |
| T8 | 版本化 | SKILL release = 0.8.0，回退=git checkout skill 目录 |

## 6. 验收标准

1. T1~T8 全绿；
2. 用 skill 生成**一个新策略**（设计 2.2 全流程 R0-R6）+ API 校验 + 本地回测 PASS——验证新模板端到端可用；
3. 存量 6 策略 hash 核对（零触碰铁证）；
4. 文档同步完成（README/toolbox/prompt）。

## 7. 回退

- 版本化回退：git checkout skill 目录（0.7.1 版本原地保留）；
- 存量零触碰 → 无引擎/策略副作用；
- 同步文档回滚同 commit。

## 8. 明确不做

- 不改引擎/转换管线（F1⑥ 的接线层已由 P-D12 交付）；
- 不重渲存量 6 策略；
- 不做 halt 引擎保真开关（P-D13b 另路）；
- 不改 R18/R21 既有契约（只增补）。