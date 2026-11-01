# PD-0016 方案：护栏首参错填 `context` + PERSIST 挤掉函数 docstring（应修尽修，两处同批）

> 立项依据：2026-11-01 用户要求「任何修复应该做到应修尽修，不要留尾巴」——PD-0015 对照分析中发现的
> 两处框架层瑕疵，一并立项修复。
> 状态：①方案（待②审计）——**未实施、未提交**。

## 1. 问题定义（两处，同源：注入位置的「上下文假设」错误）

### D1（主）护栏 helper 首参被填成「被包裹函数的首参」

**现象**（部署产物实测）：

```python
# 期望
if _deploy_guard_pass(context, security, 'auto'):
# 实际（产物 L1147）
if _deploy_guard_pass(security, security, 'auto'):
```

**根因**：`transformer.py:735`

```python
ctx_name = fn.args.args[0].arg if fn.args.args else "context"
```

把「被包裹函数的首参名」当作 `context` 传给护栏。对 `submit_target_value_order(security, ...)` 这类
**首参不是 context** 的函数即传错（实际传入 `security`）。

**为何必须修**（当前虽无功能影响）：

| 维度 | 说明 |
|---|---|
| 语义正确性 | helper 签名为 `(context, security, side)`，调用点却给 `(security, security, side)`——**名实不符** |
| 脆弱性 | 目前 helper 体不用 `context`（见下），**一旦将来 helper 需要 context**（查持仓/查计数器/查交易日），就会拿到 `security` 字符串当作 context 使用 → 直接 AttributeError 或静默错判 |
| 可读性/审阅 | 每个读代码的人都会先怀疑这是 bug——已实际发生（本轮对照分析即因此触发立项） |
| 静态检查 | 参数语义无法被任何类型检查/契约表覆盖 |

### D2（附）PERSIST 恢复块挤掉 `initialize` 的 docstring

**现象**（部署产物实测）：`initialize` 原 docstring 由 `"""..."""` 变成普通字符串表达式（`Expr(Constant)`），
且被推到第 2 条语句 ⇒ Python 不再认为它是 docstring，`initialize.__doc__` 变 `None`。

**根因**：`transformer.py:465`

```python
init.body[0:0] = restore_stmts   # 头部插入，未避开 docstring
```

**为何要修**：纯保真度问题——`__doc__` 丢失对运行行为无影响，但
① 与「P4 等价性」精神不符（结构被无谓扰动）；
② 用户在 PTrade 平台查看策略时看不到 initialize 说明；
③ 属「注入不得改变原有语义结构」纪律的边界情形。

## 2. 目标

1. 护栏调用点实参与 helper 签名**完全一致**，且不再伪造 context；
2. `initialize.__doc__` 保住（注入块插在 docstring 之后）；
3. P4 等价性红线不破（equiv 对 GUARD 块按**函数名**识别，不校验实参个数——已取证，去参不破 P4）；
4. 零运行行为变化（两项均不改变任何策略逻辑与产品行为）。

## 3. 设计

### D1：删除护栏 helper 的冗余 `context` 形参（而非"修好填参"）

**取证决定取舍**：`guard_template.py` 中 `context` 仅出现 **1 次**（就签名本身），
helper 体（停牌/涨跌停/限价快照/幂等四段）**完全不使用** `context`。

⇒ 正确修法不是"想方设法把真 context 传进去"，而是**把这个无人使用的形参删掉**——
既消除错误语义，又不引入「如何正确解析 context」的脆弱逻辑（被包裹函数可能嵌套多层、
可能是模块级辅助函数、可能根本没有 context 变量）。

改动：

```python
# guard_template.py
def _deploy_guard_pass(security, side):      # 原 (context, security, side)

# transformer._make_guard_if
args=[sec_expr, ast.Constant(value=side)]     # 原 [Name(ctx_name), sec_expr, Constant(side)]

# transformer._apply_guards
_guard_body(fn.body, fn.name, edits)          # 原传 ctx_name
```

**同步核验**（已取证，无需改）：`equiv._is_guard_call` 只比对函数名 `_deploy_guard_pass`，
不校验实参；`_handle_guard` 剥离 GUARD 块时取 `ifn.body[0]` ⇒ 实参个数变化**不影响 P4**。

### D2：注入避开 docstring

```python
# transformer._apply_persistence
_pos = 1 if (init.body and isinstance(init.body[0], ast.Expr)
             and isinstance(init.body[0].value, ast.Constant)
             and isinstance(init.body[0].value.value, str)) else 0
init.body[_pos:_pos] = restore_stmts
```

同时核验 `_ensure_notebook_path` / `_ensure_pickle_import` / `_apply_mount`：
前两者走 `_after_header_index`（模块级，已保模块 docstring）、后者 `body.append`（尾部）——**均不挤 docstring**，
无需改（本次一并核验并写入验收）。

## 4. 改动范围

| 文件 | 改动 |
|---|---|
| `quantstudio/ptrade_deploy/templates/guard_template.py` | `GUARD_HELPER_TEMPLATE` 签名去掉 `context` |
| `quantstudio/ptrade_deploy/transformer.py` | `_make_guard_if` 实参减一；`_apply_guards` 不再推导 ctx_name；`_apply_persistence` 插入避开 docstring |
| `tests/test_deploy_guard_context.py`（新建） | 见 §5 |
| 既有测试 | 若有断言 `_deploy_guard_pass(` 三参形态者同步（实施时以全绿为准） |

**不动**：equiv（按名识别，无需改）、gates、contract_table、策略源码、产物手工件。

## 5. 验收标准（6 项）

1. **D1 机检**：重转产物中所有 `_deploy_guard_pass(` 调用实参个数 **= 2**，且 helper 签名 = `(security, side)`；
   构造一个「首参非 context」的函数内含下单 → 断言调用实参仍为 2 个（防回退到 ctx_name 推导）；
2. **D2 机检**：重转产物 `initialize` 首语句**仍是 docstring**（`ast.Expr(Constant(str))` 且位于 body[0]），
   且 `ast.get_docstring(init)` 非 None；
3. **P4 红线**：`P4_equiv = PASS`（GUARD 块剥离后与源逐节点相等）；
4. **零行为变化**：`全球资产轮动` 重转 P0–P6 全 PASS；P5 `platform_path_policy` 仍 PASS（PD-0015 不回退）；
5. **回归**：deploy 全套件绿（现 129 + 新增）；契约矩阵 `OK`；族机检含 `PD-2026-0016`；
6. **护栏功能未削弱**：GUARD 四段（停牌/涨跌停/限价快照/幂等）日志与拦截分支仍在（静态断言 + 抽读产物）。

## 6. 影响面 / 风险 / 回退

| 项 | 评估 |
|---|---|
| 产物代码 | 每处 GUARD 调用点实参少一个；`initialize` 首语句恢复 docstring。**策略逻辑零变化** |
| 运行行为 | 零变化（被删形参原本无人使用；docstring 不参与执行） |
| P4 | 不受影响（equiv 按名识别 GUARD；docstring 属源侧原有语句，注入让位后更贴近源） |
| 风险 R1 | 若某处 helper 体内 `context` 被子段模板隐式引用 → 已取证「仅签名出现 1 次」，且验收第 6 项守门 |
| 风险 R2 | 既有测试若硬断言三参形态 → 作为「数据修正型」的预期改动同步（全绿为准） |
| 回退 | 单批 revert（2 文件 + 测试） |

## 7. 三型判定

- **D1、D2 均属「数据修正型」**：产物代码按正确口径变化（去掉伪造实参 / 恢复 docstring 位置），
  显式声明影响面 = GUARD 调用点 + initialize 首语句；**准确度只升不降**；运行行为不变；
  出现任何其他 diff 即验收失败回退。

## 8. 本次扫到的其他观察项（**不混办**，仅登记）

| # | 观察 | 处置 |
|---|---|---|
| 1 | `_apply_guards` 只遍历 `tree.body` 顶层函数 ⇒ **嵌套函数/类方法内的下单不进护栏** | 属**功能覆盖增强**（新增能力型），须单独立项评估（改判型 vs 改覆盖范围），本批不动 |
| 2 | GUARD 的 `side` 参数在部分调用点由 `_order_side()` 推断，推断面可能窄于实际方向语义 | 同上，须单独立项 + 真实成交方向证据，本批不动 |
| 3 | `check_strategy` 段为 `SKIPPED`（官方预检未接线） | 既有设计状态，非缺陷，不属本批 |

> 说明：以上 3 项**不是**「本次修复留下的尾巴」，而是**性质不同**的独立议题（前者改能力边界、后者需证据）。
> 按三型判定纪律**分列**，避免「以修尾巴之名夹带功能变更」。如需推进，请另立条目。

## 9. 知识反哺检索留痕（R1）

```
知识反哺：[命中] PD-2026-0012（PD/D2/PD-2026-0012.md，2026-10-30）——注入块「打标记 + 等价器可剥离」范式（本批沿用以保证 P4）
知识反哺：[命中] PD-2026-0015（PD/D3/PD-2026-0015.md，2026-10-30）——本轮对照分析发现来源；注入不得改变原有语义结构
知识反哺：[无命中] PD 族检索（guard context / docstring 保位 / 注入位置），2026-11-01
```

## 10. 排期

①方案（本件）→②审计→③实施（2 文件 + 测试，约 40 分钟）→④验收（6 项）→⑤确认→⑥随批统推。
