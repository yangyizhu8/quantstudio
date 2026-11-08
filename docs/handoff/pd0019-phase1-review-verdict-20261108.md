# PD-0019 期 1 ②审裁决与我的复核（2026-11-08）

> **审核方式**：`zcode_code`（mode=plan，只读分析；session `sess_384ae9a8…`，91,640 tokens）
> **zcode 总裁决**：**有条件可实施**（不整体阻断），最小前置清单 7 项，最可能真机失败单点＝「下单链路双读分叉」。
> **本件性质**：按既有纪律「zcode 无 Bash 权限，其返回 ≠ 已验证」，由我**自行复核**后成文——
> 复核结论与 zcode **有一处实质性分歧**（见 §二 R-1／R-2），并因此**提高**了期 1 的准入门槛。
> **六步②结论（我方）**：**有条件可实施，但条件严于 zcode 版**（新增 P0 前置，见 §四）。

---

## 一、zcode 意见采信对照（逐条）

| # | zcode 结论 | 我方复核 | 采信 |
|---|---|---|---|
| 1 | 代理形态「需实证」；最可能失败＝平台下单 API 内部重取账户态按真实账户折算（≈100 倍） | **机制需修正**（见 §二 R-1）：我方产物 `order_target_value` 是**自实现**，买入侧已走显式股数；但持仓读数走 `get_position` 不吃代理 | **采信其洞察，不同意其机制** |
| 2 | 拒单须回滚预扣＋累计告警，计数入台账 epoch 键、收盘核对行含拒单合计 | 完全成立。补充：`qs_cash_reserved` 已在 `g.state`，epoch 归零机制已存在（PD-0018 F3） | ✅ 采信 |
| 3 | 沪 `60/68/51/56/58/50/11`、深 `00/30/15/12/16`；未知段 fail-closed；我原写「深 1」过宽（11x 是沪转债） | **更正正确**：11x＝沪转债，15x/16x＝深基金 | ✅ 采信（我方 brief 有错，已更正） |
| 4 | 跨日重置取**昨收（不复权）**；缺失回退最近可得收盘＋告警，**禁回退 0**（否则首日超买） | 与 §11.3 意图一致，理由更完整（今开在重置时点尚不存在） | ✅ 采信 |
| 5 | AST 扫描六条：只认 `Name(context)→portfolio→attr` 三级链、禁 regex、白名单三元组不写 attr 通配、Load/Store 分列、**豁免注入块自身的挂载行**、report-only 先行、报错精确 file:line:attr | 「豁免注入块自身」是**真问题**——挂载行 `context.portfolio = _qs_iso_portfolio(...)` 天然是裸写点，不豁免则门禁上线即自杀 | ✅ 采信（六条全采） |
| 6 | `__class__` 需透传（property 与 `__getattr__` 不冲突）；但 **`type()` 走 C 层不可伪造** ⇒ N9 原判据不可达，须改为「isinstance 达标＋type 差异登记豁免」 | 技术上正确：`isinstance()` consult 实例 `__class__`，`type()` 返回 `ob_type` 不走属性查找 | ✅ 采信（N9 判据须改） |
| 7 | 验收不穷尽；同意补「重启恢复」；另补 N11 部分成交/废单、N12 费用溢出、N13 卖出回收时点、N14 `enabled=false` 哈希逐位一致、N15 停牌、N16 分红拆合股 | 期 1 最小集＝N11／N12／N14＋重启，合理 | ✅ 采信 |

---

## 二、我的复核：三处修正（其中两处改变风险方向）

### R-1（关键）双读分叉的**真实落点**不是 `order_target_value` 内部，而是**持仓读数 `get_position`**

实测产物（`output/ptrade_export/四象限ETF轮动策略/…_ptrade.py`）：

| 行 | 事实 |
|---|---|
| L1105 | `def order_target_value(security, value, ...)` —— **我方自实现**，非平台原生 |
| L1133 | `_amt = _qs_pos_amount(security)` ⇒ **持仓股数** |
| L740 | `_qs_pos_amount` 实现＝ `get_position(_code).amount` —— **平台持仓 API，不经 `context.portfolio`** ⇒ **不吃 isolation 代理** |
| L1135 | `_delta = value − _amt * _px` |
| L1149 | `_cash = _qs_cash_budget(security)` ⇒ 内部读 `_qs_cash_avail()`（`context.portfolio.cash`）⇒ **吃代理** |
| L1150 | `_qs_split_order(security, _delta, _px_exec, cash_avail=_cash)` ⇒ **按 delta 折股数、逐笔显式 `order(security, amount)`** ⇒ 买入侧**已隔离** |

⇒ **双读分叉确实存在，但落在「现金吃代理、持仓不吃代理」这条裂缝上**，不是 zcode 说的「平台在 order_target_value 内部重取账户」。

### R-2（关键）风险方向与 zcode 相反：期 1 首发动作可能是**真实清仓**，不是「100 倍超买」

代入真实读数：虚拟额度 `capital=100000`；四象限真实持仓 512890 为 1,215,000 股 × ≈1.237 ≈ **150 万元**。

- `_current = 1,215,000 × 1.237 ≈ 150 万`
- 策略按虚拟额度给的目标 `value ≈ 10 万级`
- ⇒ `_delta = value − _current ≈ −140 万` ⇒ **`_delta <= 0` 且有持仓 ⇒ L1138-1140 走 `target_orig`（平台原生）**
- ⇒ 平台按**真实**持仓折算 → **把 150 万真实持仓卖掉**

**这比超买更危险**：卖出不可逆、且四象限恰是 Q4 单边持仓策略，一次触发即清仓。
`target_orig(` 全文 **6 处**兜底入口，L1140 是期 1 **必然命中**的那一处。

### R-3 zcode 的 A-2「target 类折算显式数量」**部分已存在**

买入侧（`_delta > 0`）本就走 `_qs_split_order` 显式股数 ⇒ A-2 不需从零实现，**需扩展到 `_delta <= 0` 分支与 6 处 `target_orig` 兜底**（isolation 启用时不得回落平台原生）。

---

## 三、复核采信的其余更正

- **我在送审要旨第 3 点的代码段口径有错**：原写「深 `0/1/3`」，其中 `1` 误把 11x（沪转债）划给深 ⇒ 已按 zcode 前缀表更正。
- **N9 判据须改**：`type()` 不可伪造，原判据「`type(代理) is type(真实)` 成立」不可达。
- **AST 门禁须先 report-only**：与 IMPORT-FRESHNESS「收敛作用域防误伤 19 条固件」同族手法。

---

## 四、六步②结论（我方，严于 zcode）

**裁决：有条件可实施。期 1 实施前必须完成 P0 级三项（缺一不得进③实施）：**

| 级别 | 必办项 | 依据 |
|---|---|---|
| **P0-1** | **`_qs_pos_amount` 代理化**：isolation 启用时持仓读数须走代理虚拟持仓（或新增 `_qs_pos_amount_iso`），**杜绝 `get_position` 真实持仓混入 delta** | R-1／R-2 |
| **P0-2** | **`target_orig` 兜底收口**：`isolation.enabled=true` 时 **6 处 `target_orig` 全部禁用**（改为自算显式股数或 fail-closed 拒单），禁回落平台原生折算 | R-2／zcode A-2 |
| **P0-3** | **N9 判据修正**：改为「`isinstance` 成立 ＋ `type()` 差异登记豁免」，或先实证 Portfolio 可否继承（受 F10 import 白名单约束） | zcode 第 6 点 |
| P1-1 | 拒单回滚预扣 ＋ 拒单计数入台账 ＋ 收盘核对行含拒单合计 | zcode 第 2 点 |
| P1-2 | market_split 前缀表落方案（沪 60/68/51/56/58/50/11；深 00/30/15/12/16）＋未知 fail-closed ＋ capital 分池语义写明 | zcode 第 3 点 |
| P1-3 | §11.3 补价格口径（不复权昨收／缺失回退＋告警／禁回退 0） | zcode 第 4 点 |
| P1-4 | AST 扫描六条设计 ＋ 豁免注入块挂载行 ＋ report-only 先行 | zcode 第 5 点 |
| P1-5 | 验收负向补：重启恢复／N11 部分成交废单／N12 费用溢出／N14 `enabled=false` 哈希逐位一致 | zcode 第 7 点 |
| P1-6 | 实测形态两条入形态库：代理被交易模块接受性／平台是否 pickle 深拷贝 context | zcode A-7 |

**P0-1／P0-2 是我复核新增的，zcode 未识别**（它把风险定位在下单 API 内部，而真实裂缝在持仓读数与兜底分支）。

---

## 五、最可能让期 1 真机失败的单点（我方修复版）

**不是「100 倍超买」，而是「虚拟额度下 delta 恒负 ⇒ 走 `target_orig` 平台原生 ⇒ 把真实持仓按真实账户口径清掉」。**

机制链：虚拟 `capital=10 万` ＜ 真实持仓 ≈150 万 ⇒ `_delta = value − 真实持仓市值` 恒为负 ⇒ L1138-1140 ⇒ `_QSOrderRefState.target_orig(...)` ⇒ 平台按真实持仓算"目标 0 附近" ⇒ 卖出真实持仓。
根因：**代理只覆盖了 `context.portfolio` 这条路，没覆盖 `get_position` 与 `target_orig` 两条平台原生路**。
唯一防线：**P0-1 ＋ P0-2 同时落地**，缺任一则隔离在首日就是「反向操作器」。
