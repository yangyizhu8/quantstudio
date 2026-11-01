# PD-0016 验收证据：护栏首参去冗余 + PERSIST 保位 docstring（2026-11-01）

> 方案：`docs/ptrade-deploy-guard-context-design.md`（②审通过：D1 删形参照准 / D2 docstring 照准 / §8 三项不混办照准）
> ③实施：`templates/guard_template.py` + `transformer.py`（`_make_guard_if` / `_guard_body` / `_apply_guards` / `_apply_persistence`）
> ＋ 测试健壮性 D3（仅测试层）＋ `tests/test_deploy_guard_context.py`（6 用例）
> 本轮沉淀：PD-2026-0016

## 1. 问题（两处，同源）

| 项 | 现象（改前） | 根因 |
|---|---|---|
| **D1** | `if _deploy_guard_pass(security, security, 'auto'):` — 首参本应 `context` | `transformer._apply_guards` 用 `fn.args.args[0].arg` 推导 ctx_name；对 `submit_target_value_order(security, ...)` 必然错填 |
| **D2** | `initialize` 的 docstring 退化为普通字符串表达式（`__doc__` = None） | `_apply_persistence` 用 `init.body[0:0]` 头部插入，未避开 docstring |

## 2. 实施内容

| 项 | 改动 |
|---|---|
| D1 | `guard_template.GUARD_HELPER_TEMPLATE`：`def _deploy_guard_pass(security, side)`（删 `context`）；`_make_guard_if(sec_expr, side, original)` 实参减为 2 个且 `copy.deepcopy(sec_expr)`；`_guard_body` / `_apply_guards` 不再推导/传递 `ctx_name` |
| D2 | `_apply_persistence`：`_ins = 1 if body[0] 是字符串常量 docstring else 0`，`init.body[_ins:_ins] = restore_stmts` |
| D3（仅测试层） | `test_deploy_mixedform`：`next(..., None)` + 模块级 `skipif`；`test_deploy_quickmode`：`_needs_tech` 装饰器覆盖 6 个依赖真实产物的用例 |

**取证支撑（决定 D1 修法）**：`guard_template.py` 全文 `context` 命中 **1 处**（就是签名本身）；四段护栏体（停牌 `get_stock_status` / 涨跌停 `check_limit` / 限价快照 `get_snapshot` / 幂等 `orders_done`）只用 `security`/`side`/`g`/`log`。

## 3. 验收六项（方案 §5）

| # | 标准 | 实测 |
|---|---|---|
| 1 | D1：签名 = `(security, side)`，所有调用实参个数 = 2，且负向防回退 | ✅ 重转产物签名 `['security','side']`；调用点实参个数集合 `{2}`（`L1153: _deploy_guard_pass(security, 'auto')`）；`test_guard_not_fed_with_function_first_param` 守门 |
| 2 | D2：`initialize` 首语句仍是 docstring | ✅ `首语句=Expr`、`get_docstring(init) is not None`；`test_module_docstring_still_first` 另证模块 docstring 仍首位 |
| 3 | P4 红线 | ✅ **P4_equiv = PASS** |
| 4 | 零行为变化 + PD-0015 不回退 | ✅ 重转 **P0–P6 全 PASS**；P5 `platform_path_policy` 仍 **PASS** |
| 5 | 套件 + 矩阵 + 族机检 | ✅ deploy **115 passed / 20 skipped**（skip 全为产物缺失，见 §4）；契约矩阵 `OK`；PD 族 **16/16** |
| 6 | 护栏未削弱 | ✅ `get_stock_status` / `check_limit` / `get_snapshot` / `orders_done` 全在；「护栏拦截」「护栏参考」日志仍在 |

## 4. 环境变动与覆盖降级（如实登记）

**事实**：`output/ptrade_export` 目录在本次工作期间被清理，由 **24 个子目录 → 5 个**
（`fall_reversal` 目录 mtime = 2026-11-01 14:16，为**他线会话**动作）。`output/` 在 `.gitignore` 内，
git **无法恢复**。

**影响**：`test_deploy_mixedform`（收集期 `StopIteration` 崩）与 `test_deploy_quickmode`（3 例断言失败）
依赖已删产物 ⇒ 修测试健壮性（D3）后转为 **skip**，并写明重建原因。

**覆盖降级登记**：20 个 skip 用例依赖 `CANSLIM突破成长选股策略` / `vol_regime_mom_rev` / `tech_etf_mvo_rotation`
三个真实产物。**恢复条件**：用 `qs-compile import` 重建这三个策略的 PTrade 产物后自动恢复（测试无需再改）。

## 5. 判型

**数据修正型**（D1/D2）：产物代码按正确口径变化（去掉伪造实参 / 恢复 docstring 位置），
影响面显式声明为「GUARD 调用点 + initialize 首语句」，**运行行为不变**，准确度只升不降。
**D3 属测试层健壮性修正**（不改产品代码，零行为影响）。

## 6. 六步进度

①方案 ✅ → ②审计 ✅ → ③实施 ✅ → ④验收 ✅（本件）→ **⑤用户确认（待）** → ⑥随批统推。
