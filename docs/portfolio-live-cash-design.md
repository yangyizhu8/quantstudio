# 框架修复方案 v2（终稿）：引擎 Portfolio 活属性（单一真源委托 engine.account）

- 作者：dsh（D4-S7 探针定谳 + ZCode ① 钳制存活补证后续，2026-08-28）
- 状态：方案 v2（六步流水线第 1 步，ZCode 5 项细化 D1-D5 已并入）→ 待审计通过后实施
- 关联：D4-S7（#5 钳制接线）、issue_registry、docs/evidence/ptrade-pctchg-synth-evidence.md（§10 钳制存活补证）
- 用户裁定：陈旧现金必须修复，不接受 known-difference 关闭

## 0. ZCode 细化修入记录（2026-08-28）

| 项 | 审计要求 | 方案 v2 落实 |
|---|---|---|
| D1 | 写面审计：property 无 setter，全库扫描赋值点清零或 AttributeError 佐证 | §2a：**字段级写点 = 0**（框架+测试广扫实证）；整体替换点 2 处（Context.portfolio 初始化 + refresh_portfolio 重建）——重建在活属性下冗余但无害，**引擎零改动保留原样**；property 无 setter → 若有外来写点将显式 AttributeError（可防性证明） |
| D2 | positions 返回只读快照（copy），禁止内部可变状态外泄 | §2b：`positions` property 返回 `dict()` 深拷贝（或只读 mapping），与 PTrade get_positions 快照语义对齐 |
| D3 | 市值价格基准显式化 | §2c：`market_value/total_value` 委托 `account.market_value_at_price(prices)`，prices = `_api._prices`（成交后引擎已更新）；缺失回退 `refresh_portfolio` 更新的 account 快照；穷举写进设计 |
| D4 | 验收增补：钳制存活单测 + 活属性后三方复跑（终判）+ 不读 portfolio 策略零漂移 | §4b/4c/4d 新增 |
| D5 | 引擎 Account 零改动 ✓ 策略零改动 ✓ | 维持（仅 Portfolio 类字段改活属性） |

## 1. 问题定义

**本地引擎 `context.portfolio.cash` 陈旧**：`Portfolio.__init__(cash)`（ptrade_api.py:110）初始化后不再随成交更新——引擎成交直接改 `_engine.account.cash`（backtest_engine.py:2261），`Portfolio.cash` 恒=初始资金。平台 `context.portfolio.cash` 实时（探针 07-16/07-28 实证）。

**钳制存活补证（§10 证据文档）**：本地原生 07-15/07-31 钳制放行合法（1200/7200 非巧合）；但**活属性落地后本地 smoke 现金实时，三方一致须复跑复验**——当前一致不作终态。

**同族字段**（D3 关联）：cash / market_value / total_value / portfolio_value / positions 五字段全陈旧，一并修复。

## 2. 方案（单一真源，禁止同步刷新补丁）

### 2a. Portfolio 字段改活属性（property 委托 `${'_api' if False else '_api'}` 引擎 account）

```python
class Portfolio:
    def __init__(self, cash, positions):
        self._init_cash = cash
        self._init_positions = dict(positions or {})
        self._snapshot = {"cash": cash, "market_value": 0.0}

    def _engine_account(self):
        try:
            from quantstudio.backtest.ptrade_api import _api
            eng = getattr(_api, "_engine", None)
            if eng is not None and getattr(eng, "account", None) is not None:
                return eng.account
        except Exception:
            pass
        return None

    @property
    def cash(self):
        acc = self._engine_account()
        return acc.cash if acc is not None else self._snapshot["cash"]

    @property
    def market_value(self):      # D3：市值基准显式化
        acc = self._engine_account()
        if acc is not None:
            try:
                prices = getattr(_api, "_prices", None) or {}
                return acc.market_value_at_price(prices)
            except Exception:
                pass
        return self._snapshot["market_value"]

    @property
    def total_value(self):
        return self.cash + self.market_value

    @property
    def portfolio_value(self):
        return self.total_value

    @property
    def positions(self):        # D2：只读快照（copy），防内部状态外泄
        acc = self._engine_account()
        if acc is not None:
            return dict(acc.positions)
        return dict(self._init_positions)
```

### 2b. D2 只读快照语义

- `positions` 返回 `dict(acc.positions)`（浅拷贝）——策略 `portfolio.positions.clear()` 等操作**不影响 engine 持仓**（与平台 get_positions 快照语义一致）。
- `cash`/`market_value` 返回 float（不可变）→ 无写面。

### 2c. D3 市值价格基准（显式）

- `market_value` = `account.market_value_at_price(_api._prices)`：`_prices` 为成交后引擎更新价（refresh_portfolio 同源）；
- `_prices` 缺失/异常 → 回退 `_snapshot["market_value"]`（构造期 0.0）；
- 兜底一致性：`total_value = cash + market_value` 纯派生，无独立陈旧源。
- **与 refresh_portfolio 关系**：L2095 每成交重建 Portfolio 用 `self.account.cash` + `_get_ptrade_positions(prices)`——活属性下重建冗余但无害（新对象同样委托 account，字段一致）；**为最小改动保留重建原样**（引擎零改动），仅 Portfolio 类自身字段活属性化。

### 2d. D1 写面审计（实证）

- 字段级写点（`portfolio.cash=` 等）= **0**（框架 ptrade_api/backtest_engine/tests 广扫）；
- 整体替换点：Context.portfolio 初始化（ptrade_api:162）+ refresh_portfolio 重建（L2095）——均为**新对象赋值**，非字段写面；
- property 无 setter：任何意外字段写点触发 `AttributeError: can't set attribute`（显式防性，非静默漂移）。

## 3. 改动范围

- `quantstudio/backtest/ptrade_api.py`：`Portfolio` 类（仅此类，7 个字段/方法改 property；`__init__` 兼容保留）
- **引擎零改动**（Account/refresh_portfolio/成交路径不动；L2095 重建保留原样）——D5
- **策略零改动**（context.portfolio.* 读取点不变，仅值实时化）

## 4. 验收标准

- **4a 行为等价边界**：不读 portfolio 字段策略零漂移——149 套件全绿 + G3.5 逐位一致
- **4b 钳制存活单测（D4 新增）**：构造引擎+持仓 → 模拟现金竞对日（target=50,000、cash=48,000）→ order_target_value → 断言订单被裁（结果 48,000 内整手），钳制未旁路
- **4c 活属性实时性单测（D4 新增）**：Portfolio 构造 → attach 引擎 → 改 account.cash/positions → portfolio.cash/market_value/total_value/positions 实时反映；positions 改返回 dict 不影响 engine
- **4d 活属性后三方复跑（D4 终判）**：本地引擎跑 v2 产物（钳制用实时现金）→ 本地 smoke = 本地原生 = 平台 = 1200/1200/7200（**复跑验证**，非沿用现状）
- **4e 同族验证**：portfolio.positions 反映 account.positions 且只读（改返回 dict 引擎不变）

## 5. 回退条件

- 任一验收失败/行为漂移 → git 回退 Portfolio 类（提交前基线）；平台产物不受影响（仅本地引擎侧）

## 6. 实施后登记

- issue_registry：#5 关联（Portfolio 陈旧）→ closed（修复+三方对齐复跑证据）
- 证据文档 §11 补活属性修复证据

## 7. 已核验事实（含 ZCode ① 补证）

- Portfolio.__init__ L110 cash 一次性赋值无后续更新 ✅；engine.account.cash 成交实时（L2261）✅
- refresh_portfolio L2089-2095 每成交重建 Portfolio（快照化）✅
- Context.portfolio 初始化 L162 + 引擎重建 L2095 ✅
- _api._engine attach_day 设置（self._engine = engine）✅ → Portfolio 可经 _api 访问引擎
- **字段写面 = 0**（D1 广扫实证）✅
- 钳制存活：07-15 下单前 cash=100,000 ≥49,920（1200 合法）；07-31 下单前 cash=95,841.54 ≥47,920.77（7200 合法）——钳制未旁路（§10 证据）✅
- 平台 portfolio 实时（探针 07-16/07-28）→ 本次仅本地侧修复，平台无回归 ✅