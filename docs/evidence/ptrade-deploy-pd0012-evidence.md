# PD-0012 验收证据：`is_trade()` 恒真分支还原（2026-10-30）

> 方案：`docs/ptrade-deploy-istrade-gate-design.md`（②审通过 + 四裁，含「还原须在转换审计行留痕」）
> ③实施：`transformer.py`（还原逻辑 + 审计行）、`equiv.py`（`GATE_RESTORE` 剥离处理器）、
> `gates.py`（门控块种类判定）、`tests/test_deploy_nested_gate.py`（9 用例）
> 本轮沉淀：PD-2026-0012

## 1. 根因回顾（三级语义冲突）

`if not is_trade(): set_limit_mode(...)`（PTrade 惯用法，回测才解除量限制）
→ `qs-compile import` 把 `is_trade` 固化为字面量 `False`（`source_import.py:3087`）⇒ `if not False:`（**恒真**）
→ deploy M3 只门控函数体首层 ⇒ 嵌套调用逃逸 ⇒ P5 `contract_full` 拦截（保护生效）。

## 2. 修复

| 环节 | 实现 |
|---|---|
| 识别面 | 仅 `not False` 字面量形态（`_is_not_false_literal`）——conservative，`if True:` 等不认 |
| 允许集 | 分支体**只含**回测专用 API 调用 / `pass` / 纯注释常量（沿用 PD-2026-0007 允许集收紧） |
| 动作 | 条件改为 `not is_trade()`，**分支体原样保留**；节点打 `GATE_RESTORE` 标记 |
| 审计行 | `edits.append({"kind": "GATE_RESTORE", "detail": "恒真分支还原@<行号>: …"}）`（②审新增要求） |
| P4 归一 | `equiv._handle_gate_restore`：把 `if not is_trade(): <体>` 重建为 `if not False:` + 原缩进体 ⇒ 与源码逐节点相等 |
| P5 判定 | `gates._GATE_BLOCK_KINDS = ("GATE", "GATE_RESTORE")`（原为硬编码 `"GATE"`） |

## 3. 验收六项（方案 §5）

| # | 标准 | 结果 |
|---|---|---|
| 1 | `全球资产轮动` 实跑 P5 PASS | ✅ **status=PASS**（P0–P6 全 PASS；`set_limit_mode` 不再出现在 P5 reason） |
| 2 | P4 等价性 PASS（红线） | ✅ **P4=PASS**（2 个产物均 PASS） |
| 3 | 负向：非 gate 语句不得还原 | ✅ `test_no_restore_when_branch_has_business_stmt`（仍告警 + 不还原）；`if True:` 亦不还原 |
| 4 | deploy 全套件 + 契约矩阵 | ✅ **110/110**；`check_fund_matrix --check` = `OK` |
| 5 | 3 个含 `if not False:` 产物逐个重转 | ✅ 2 个 **PASS**（`全球资产轮动`、`小市值策略ptrade`）；第 3 个 `ETF动量` 卡 **P0 `危险 import: sklearn`**——F10 正确 fail-closed，**与 PD-0012 无关**（逐项归因，未含混带过） |
| 6 | 族机检 + 条目 | ✅ PD 族 `--strict` 全绿（含新增 PD-2026-0012） |

## 4. 实施中被自测/实跑抓出的三处缺陷（如实登记）

| # | 缺陷 | 发现方式 | 修正 |
|---|---|---|---|
| 1 | 还原后 `_scan_ungated_calls` 仍对已还原分支报「嵌套未门控」 | 自测 `test_restore_always_true_branch_and_audit` | 扫描入口先判自身 `id(node) in restored` |
| 2 | 我最初的测试断言用 `ast.dump` 字符串匹配 `not False`（dump 文本无此串） | 自测第二条失败 | 改用结构化判定 `_is_not_false_literal` |
| 3 | **P4 红**：还原未打标记、形态不匹配等价器 `_handle_gate`（要求单语句） | 实跑 3 产物 | 新增 `GATE_RESTORE` 标记 + equiv 专属还原处理器（重建 `not False` + 原体） |
| 4 | **P5 仍拦**：`gates._gated_linenos` 把种类**硬编码** `"GATE"`，`GATE_RESTORE` 块被忽略 | 实跑复验 | 提为 `_GATE_BLOCK_KINDS` 统一判定 |

> 教训固化：**门控「已门控」判定与 P4 归一必须共用同一套标记种类**——两处各自硬编码字符串，
> 新增种类时必然漏一处。已在 `gates.py` 注释中写明该案例。

## 5. 零回归与判型

- **判型：纯恢复型**——回测语境 `is_trade()` 为 False ⇒ `not is_trade()` 与原 `not False` 同为真，
  执行行为不变（P4 逐位 PASS 为证）；交易侧从「必执行不可用 API」恢复为「跳过」；
- 首层门控路径**未改**（`test_first_level_gate_unchanged`）；
- `source_import.py` **零改动**、策略源码**零改动**、产物手工修改**零**。

## 6. 六步进度

①方案 ✅ → ②审计 ✅（四裁）→ ③实施 ✅ → ④验收 ✅（本件）→ **⑤用户确认（待）** → ⑥随批统推。
PD-0013（契约表补 `get_order`）按裁定**待本件 ④ 过后启动**——现已具备启动条件。
