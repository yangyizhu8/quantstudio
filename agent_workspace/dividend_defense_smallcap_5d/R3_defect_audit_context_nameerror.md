# R3 缺陷发现：_audit() 引用了未定义的 context（rule 21 双审计行只出一半）

- 发现时刻：2026-09-13（插桩测量跑期间，插桩本身反而成了探针）
- 发现路径：插桩 `QS_PERF` 始终不输出 → 追查 `_audit()` → 引擎日志暴露 `NameError`

## 一、缺陷

`strategy.py:544` 定义：

```python
def _audit(rid, date_str, selected, tradable, sell_submitted, buy_submitted, note):
    log.info("QS_REBALANCE_AUDIT ...")      # ← 成功输出
    ...
    positions = getattr(context.portfolio, "positions", None) or {}   # ← context 未定义 → NameError
    ...
    log.info("QS_PORTFOLIO_AUDIT ...")      # ← 永远到不了
```

**`_audit` 没有 `context` 形参**，函数体却使用模块级 `context`；本框架下 `context` 在回调作用域内可用、
但在普通函数体内不构成注入名 → 每次调仓抛 `NameError: name 'context' is not defined`，
被引擎逐日 `except` 捕获（`backtest_engine: [Ptrade] handle_data 错误: ...`）后静默继续。

调用点：`strategy.py:516`（无候选路径）与 `:539`（正常路径）——两处均未传 `context`。

## 二、证据（本次插桩跑 + 历史三跑全部复核）

| 运行 | QS_REBALANCE_AUDIT | **QS_PORTFOLIO_AUDIT** | NameError |
|---|---|---|---|
| run2（中断） | 18 | **0** | — |
| run2b（终止） | 30 | **0** | — |
| 优化前有界基线 | 24 | **0** | — |
| 插桩跑（本次） | 15 | **0** | **15** |

引擎日志原话：`ERROR quantstudio.backtest.backtest_engine: [Ptrade] handle_data 错误: name 'context' is not defined`（15 次）

## 三、影响评估

| 面 | 影响 |
|---|---|
| **rule 21 双审计行** | **只出一半** —— `QS_PORTFOLIO_AUDIT`（positions / cash_ratio / gross_exposure）**全部缺失** |
| `r5_deployment_invariants` 机器核账 | 口径缺一半（只有 selected/tradable/sell/buy，没有组合面） |
| 选股 / 成交 / 持仓 / 净值 | **无影响** —— 异常发生在下单**之后** → 这解释了为什么**四哈希仍逐位一致**（等价性 PASS 成立） |
| 插桩 | `QS_PERF` 挂在同一函数尾部 → 完全失效（已终止该跑，日志归档留证） |
| R4 静态校验 | **未能拦截**（NameError 属运行时缺陷，静态 AST 校验不覆盖） |

## 四、修复方案（待审计）

- 最小改动：`_audit(context, rid, ...)` 增加 `context` 首参；两处调用点（516 / 539）传入 `context`；
- 纯缺陷修复（非性能优化），**不触碰** 选股逻辑 / 漏斗 / A-9 / 执行层三项 / 参数冻结；
- 修复后**四哈希必须仍然逐位一致**（预期一致——异常在下单之后，不影响订单序列）；
- 与批 2 同轮完成（按裁定「移除插桩 + 实施缓存同一改动轮」）；
- 需 **R4 复跑**（哈希卫生）。

## 五、案例登记（第五条）

**静态校验通过 ≠ 运行时行为正确** —— rule 21 双审计行缺失在 R4 PASS 下静默通过四轮运行，
最终由「插桩无输出」这一异常反向暴露。教训：审计行必须做**输出存在性校验**（不只是静态声明检查），
R5 验收清单应含「`QS_PORTFOLIO_AUDIT` 条数 == `QS_REBALANCE_AUDIT` 条数」一条。
