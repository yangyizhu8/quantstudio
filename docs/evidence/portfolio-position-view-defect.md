# 缺陷取证：context.portfolio.positions 元素类型违背 PTrade 契约

- 取证日期：2026-09-04
- 触发来源：B2 验收期间发现的 round_trips 空输出疑点（审计要求最小复现，不得搁置）
- 状态：**根因已运行时定谳（可复现）**；尚未修复 —— 待走六步流水线（方案 → 审计 → 实施 → 验收 → 用户确认 → 推送）
- 最小复现脚本：`agent_workspace/dividend_defense_smallcap_5d/probe_position_view.py`

## 一、现象链

在 `小市值隔夜 7 号（smallcap_overnight_scalp_7_quantstudio.py）` 2026-07-01 ~ 2026-07-31 的一次运行中：

| 观测面 | 实测值 |
|---|---|
| `trades.csv` | 20 行，action 分布 **buy 20 / sell 0**（只买不卖） |
| `round_trips.csv` | 5 字节空文件（空 DataFrame，无列） |
| `daily_stats.csv` 的 `positions` 列 | 07-01=0 / 07-02=0 / **07-03=2 / 07-06=7 / 07-07=14** / 其余 18~19 |
| 策略侧日志 `Post-close ... held=` | **23 个交易日全部 `held=[] batches={} pending_exits=[]`** |

即：**引擎侧有持仓，策略侧读到的持仓永远为空**。

## 二、根因（运行时定谳）

`quantstudio/backtest/ptrade_api.py` `Portfolio.positions`（D4-S7「Portfolio 活属性」，2026-08-28，
`docs/portfolio-live-cash-design.md`）在引擎在场时**直接返回引擎 account 的持仓字典**：

```python
@property
def positions(self) -> dict:
    acc = self._engine_account()
    if acc is not None:
        return dict(acc.positions)     # <- 元素是「引擎 Position」，不是「PTrade Position」
    return dict(self._init_positions)
```

两个类字段完全不同：

| 类 | 字段 |
|---|---|
| `backtest_engine.Position`（实际返回） | `code` / `volume` / `avg_cost` / `can_sell` / `pending_sell_shares` |
| `ptrade_api.Position`（契约要求） | `sid` / `amount` / `enable_amount` / `cost_basis` / `last_sale_price` / `avg_cost`（`_get_ptrade_positions` 的产物） |

**最小复现实测输出**：

```
portfolio.positions 键: ['159870.SZ']
元素类型: quantstudio.backtest.backtest_engine.Position
   amount           = '<MISSING>'
   enable_amount    = '<MISSING>'
   cost_basis       = '<MISSING>'
   last_sale_price  = '<MISSING>'
   volume           = 100
   can_sell         = 100
   sid              = '<MISSING>'

策略视角（PTrade 契约）:
   getattr(position,'amount',0)        = 0 -> _held_bare_codes 判定: 空仓(误判)
   getattr(position,'enable_amount',0) = 0 -> 可卖量判定: 不可卖(误判)
```

**结论**：`context.portfolio.positions` 的容器类型违背 PTrade 契约；
任何按 `position.amount` / `enable_amount` / `cost_basis` / `last_sale_price` / `sid` 读取的策略
都会拿到 0 / None 并**静默**走错分支（无异常、无告警）。

## 三、与既有红测试同源

`tests/test_strategy_alignment_regressions.py::test_portfolio_position_suffixes_match_ptrade_exact_container_semantics`
断言 `assert "159870.SZ" in portfolio.positions`，实测得到 `{}`（该测试属既有失败 9 项之一，
B2 修复前后逐项一致）。本案根因与该红测试同源 —— **B2 期间把这条长期未被解释的红灯定位到了具体根因**。

（该测试同时命中 `_api` 模块级单例跨测试残留：`_engine_account()` 回退到前一个测试挂上的引擎，
导致返回 `{}` 而非错误类型；此为**测试隔离**问题，与本案主因并列记录。）

## 四、影响面（初评，方案阶段须逐个核验）

- `quantstudio/backtest/strategies/` 中引用 `portfolio.positions` 的**18 个策略文件**；
- 其中**直接按 PTrade 契约字段读取**的 3 个：
  `smallcap_overnight_scalp_7_quantstudio.py`（5 处，**已实测失效**）、
  `小市值策略ptrade.py`（1 处）、`小市值策略2.py`（1 处）；
- 其余 15 个是否受影响取决于是否用 `get_position()` / `get_positions()` 兜底 —— 需逐个核验（不得默认安全）；
- `get_position()` / `get_positions()` 走 `_get_ptrade_positions` 路径，本条**是否同样受影响**须在方案中核实并取证。

## 五、定性

**框架层正确性缺陷，非性能问题。** 依项目铁律：
- 「框架问题立即解决，禁止登记挂账」→ 根因已证实，本工作周期内启动修复流程；
- 「策略生成与转换全链路修复仅限框架层」→ 修复必须落在框架层，**禁止修改任何具体策略源码**
  （smallcap_overnight 的策略代码保持零改动，修复经框架层生效）；
- 六步流水线：方案 → 审计 → 实施 → 验收 → 用户确认 → 双仓库推送；
- 与「性能优化不得改变引擎行为」界别一致：这是行为变更（正确性修复），不是性能优化。

## 六、下一步（本周期启动，不搁置）

1. 产出方案 `docs/portfolio-position-view-contract-design.md`：问题定义、契约钉死
   （`positions` 元素必须为 PTrade `Position`）、改动范围、影响面逐策略核验法、
   验收标准（含 18 策略受影响清单 + 黄金结果对比）、回退条件；
2. 送独立审计；通过后实施；
3. 验收须包含：smallcap_overnight 由「0 sell」恢复为正常回合（round_trips 非空）；
   既有红测试 `test_portfolio_position_suffixes_...` 转绿或显式重定义；
   6 策略转换产物与既有回归零衰减。
