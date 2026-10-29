# PD-0012 方案：`is_trade()` 两级语义冲突修复（回测专用分支被固化为恒真）

> 立项依据：`全球资产轮动` 部署转换 **P5 FAIL**（2026-10-28 复现、2026-10-30 仍复现）：
> `contract_full: ['行 942: set_limit_mode 交易环境不可用且未门控（契约表 trading:false）']`
> 前置已完成：PD-0011 闭环管线出仓（`869a351`，pushed=True）→ 按裁定本件启动。
> 状态：①方案（待②审计）——**未实施、未提交**。

## 1. 问题定义（三段证据链）

**现象**：产物 P3 警告 + P5 拦截：
```
[P3] initialize: 回测专用 API set_limit_mode 位于嵌套复合语句内，M3 仅门控函数体首层语句（P5 将按未门控拦截）
[P5] FAIL — contract_full: ['行 942: set_limit_mode 交易环境不可用且未门控（契约表 trading:false）']
```

**根因链（三级子系统语义相反）**：

| 级 | 事实 | 位置 |
|---|---|---|
| 策略源 | PTrade 惯用法 `if not is_trade(): set_limit_mode("UNLIMITED")` —— 回测才解除量限制 | `全球资产轮动_ptrade.py:1418-1419` |
| `qs-compile import` | 把 `is_trade` **固化为字面量 `"False"`**（使 `if not is_trade():` 恒真，令回测配置生效） | `source_import.py:3087` 映射表；`:3771` 注释 |
| deploy transformer | 门控时**期望 `is_trade()` 是运行时函数**（包裹成 `if not is_trade():`），且**只处理函数体首层语句** | `transformer.py:296-316` |

⇒ 源产物携带 `if not False:`（**恒真**）进入部署；M3 只扫首层，嵌套里的 `set_limit_mode` 逃逸门控；
P5 按契约 `trading:false` 正确拦截（**保护生效，非误报**）。

**真实风险（不止转换失败）**：若该产物被手工放行到交易环境，`set_limit_mode` 会被平台拒绝/报错——
因为 `if not False:` 恒真，本该在交易时跳过的调用一定会执行。

**影响面（实测）**：182 个 PTrade 产物中 **3** 个含 `if not False:`（`全球资产轮动` / `小市值策略ptrade` / `ETF动量`）；
其中 `全球资产轮动` 因此被阻断。

## 2. 目标

让「回测专用分支」在部署产物中恢复为**运行时可判门控**（回测执行、交易跳过），使：
- P5 `contract_full` 通过（无未门控的回测专用 API）；
- **P4 等价性不回退**（回测语境行为逐位不变——因为回测时 `is_trade()=False`，`not is_trade()` 与原 `not False` 同为真）。

## 3. 两条候选路线

### 路线 A（推荐）：deploy 侧「恒真分支还原」

**做法**：transformer 在 M3 门控阶段，除现有首层处理外，增加一步**嵌套还原**：

识别满足以下**全部**条件的 `ast.If`（或 `while`）节点：
1. 条件为**静态恒真**：`not False` / `True` / `not 0` 等字面量可判定形态；
2. 分支体内**全部**语句均为：契约表 `gate_apis`（`backtest:true, trading:false, action∈{strip_or_gate,gate}`）的
   调用语句，或 `pass` / 纯注释（`Expr(Constant(str))`）；
3. 该节点位于任意嵌套深度。

**改法**：把条件替换为 `not is_trade()`（等价门控），**分支体原样保留**。
即 `if not False: set_limit_mode(X)` → `if not is_trade(): set_limit_mode(X)`。

**为什么正确**：
- 回测语境：`is_trade()` 返回 False ⇒ `not is_trade()` = True ⇒ 执行，**与改前 `not False`=True 完全一致**（P4 不变）；
- 交易语境：`is_trade()` True ⇒ 条件 False ⇒ 跳过，正是原意图；
- 不删除任何代码：无需担心"删了回测行为"。

**为何不选「直删分支」（原设计给的另一选项）**：删除会改变**回测**行为（限价模式变回默认），
P4 等价性必然差；而还原法在两侧都正确。

### 路线 B（不推荐）：source_import 侧不再固化

去掉 `is_trade → "False"` 映射，保留运行时 `is_trade()`。
- 缺点①：`source_import.py:3771` 注释明确该固化是**为了让回测配置生效**（`if not is_trade(): set_backtest()`）；
  去掉后 PTrade 回测环境若无 `is_trade()` 即崩——波及所有产物与回测语境；
- 缺点②：需重转全部产物（182 件），影响面远超本缺陷；
- 缺点③：已产出的历史产物仍是 `if not False:`，deploy 侧问题依旧要在 deploy 修。

⇒ **采纳 A**；B 仅作为长期方向记录（若将来统一部署/回测语境，另行立项）。

## 4. 改动范围

| 文件 | 改动 |
|---|---|
| `quantstudio/ptrade_deploy/transformer.py` | M3 门控阶段新增 `_restore_always_true_gate()`：按 §3-A 三条件识别并还原条件；保留既有首层逻辑（首层仍走原路径，避免行为变化） |
| `tests/test_deploy_mixedform.py` 或新增 `tests/test_deploy_nested_gate.py` | 用例：①`if not False:` + 单一 gate API → 条件还原为 `not is_trade()`；②分支体含**非** gate 语句 → 不动（保持 P5 拦截，防误伤）；③首层行为不变（回归）；④P4 等价性用例 |
| `docs/ptrade-deploy-pipeline-design.md` | §P3 补「嵌套恒真分支还原」口径 |
| 条目 | `PD-2026-0012`（kb_draft 起草 + kb_hook 校验） |

**不动**：`source_import.py`、`contract_table.yaml`、gates 判据（P5 仍 fail-closed）。

## 5. 验收标准（6 项）

1. `全球资产轮动` 实跑 `run_deploy` → **P5 PASS**（`contract_full` 无 set_limit_mode 未门控）；
2. **P4 等价性 PASS**（回测语境行为逐位不变；若等价器报告差异即失败回退）；
3. 负向：构造 `if not False: <非gate语句>` → **不得**被还原（仍由 P5 拦截），防误伤；
4. deploy 全套件绿（现 84 + 新增用例）；契约矩阵哈希一致；
5. 3 个含 `if not False:` 的产物逐个重转验证（不只修好一个）；
6. 族机检 `kb_hook --strict` 全绿（新增 PD-2026-0012）。

## 6. 影响面 / 风险 / 回退

| 项 | 评估 |
|---|---|
| 转换行为 | **零变化**于首层路径；仅对「恒真分支只含回测专用 API」这一窄形态生效 |
| P4 等价 | 必须 PASS；不等价即回退（红线） |
| 风险 R1 | 误识别恒真条件（如 `if True:` 但体内混有业务逻辑）→ 三条件全满足才动，且体内非 gate 语句即跳过 |
| 风险 R2 | `is_trade()` 在部署产物中是否可用？——**需在实施前核实**（deploy 产物已由 transformer 注入 `is_trade` 门控，见 `transformer.py:298`，故该函数在产物语境可用） |
| 回退 | 单批 revert（改动集中一个函数 + 测试） |

## 7. 三型判定

**纯恢复型**：把被固化的门控**还原为运行时可判门控**，回测行为不变（P4 逐位一致为证），交易侧从"必执行"回到"跳过"。

## 8. R1 知识反哺检索留痕

```
知识反哺：[命中] PD-2026-0004（PD/D2/PD-2026-0004.md，2026-10-30）——嵌套 AST 处理先例（ast.walk 全量口径）
知识反哺：[命中] PD-2026-0007（PD/D2/PD-2026-0007.md，2026-10-30）——AST 形态判定须黑名单优先+允许集
知识反哺：[无命中] PD 族检索（is_trade / 恒真分支 / 嵌套门控还原），2026-10-30
```

采纳要点：0004 示范「AST 处理必须覆盖嵌套节点（`ast.walk` 而非仅顶层）」；0007 示范「形态判定用
黑名单优先 + 允许集，防放宽过度」——本方案 §3-A 的三条件即「允许集」式收紧。

## 9. 排期

①方案（本件）→②审计→③实施→④验收（6 项，含 3 产物重转）→⑤确认→⑥随批统推。
