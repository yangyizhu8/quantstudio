# 方案：接线层换算价按撮合模式分流（px_exec basis fix）

> 六步流水线**第 1 步（方案）** ｜ 提出方：策略线会话 ｜ 日期：2026-10-03 ｜ 方向：**甲**（总调度裁定）
> 类型：**行为正确性修复**（影响面红线：diff 只归因于「换算价来源」单一变量）
> 触发：`csi300_slow_kd_reversal` 线 ZCode 独立复核发现 FIX-1 疑点 → 本线实测归因 → 总调度立项

## 1. 问题定义

`order_target_value` 接线层用 `px_exec = _QSPriceState.orig(security)`（= `_api.current_price`）作为**定股数换算价**。
但在 `match_price_mode='open'` 下，引擎按**当日开盘价**撮合，而 `_api.current_price` **恒返回当日收盘价**
（`ptrade_api.py:1563-1579` `_get_current_price` 读 `_current_day_data[...]['close']`，与撮合模式无关）。

**后果**：定股数价与成交基准分叉 → 单只成交金额围绕目标值**双向系统性偏离**
（`open < close` 的上涨日 → 股数按高价定 → 成交额欠额、余资闲置；`open > close` 的下跌日 → 超额）。

## 2. 证据链（反推 + 直采，双证闭合）

### 2.1 反推证据（全窗口）

对 R5 修复版 139 笔买入逐笔反解 `px_exec` 的可行区间（由 `volume = floor(100000/px_exec/100)*100`）：

**全窗口全量（139 笔，时区修正后）**：

| 候选价 | 落在该价区间的笔数 |
| --- | --- |
| 当日 **收盘** close | **139 / 139（100%）** |
| 当日开盘 open | 74（与 close 同区间的巧合——日振幅小时两价落同一手宽区间） |
| 前收 prev_close | 70（同上） |
| 三者皆不落 | **0** |

脚本：`agent_workspace/csi300_slow_kd_reversal/probe_px_basis_infer.py`（用法：传入 R5 产物目录或 trades.csv 路径）。

> 说明：区间归属非互斥（open/close 常落同一手宽区间），故**判定的决定性来自直采的精确等值**（§2.2），区间统计用于证明「无例外」。

### 2.2 直采证据（1 个月窗，插桩直采，**不改框架文件**）

方法：运行时替换 `ptrade_api._QSPriceState.orig` 为记录器（`staticmethod` 包装），逐次采集
`(交易日, 标的, px_exec, 当时现金)`；窗口 2025-07-01..2025-07-31（23 交易日 / 13 个订单日 / 13 笔买入）。
脚本：`agent_workspace/csi300_slow_kd_reversal/probe_px_basis.py`；原始记录：`probe_px_basis_records.json`。

**决定性单点**（其余 12 条同构，见原始记录）：

| 项 | 值 | 来源 |
| --- | --- | --- |
| `px_exec`（直采） | **15.01** | `probe_px_basis_records.json`（600886.SS @2025-07-01） |
| 当日收盘 close | **15.01** | `stock_daily`（600886 @2025-07-01，raw time 1751299200000） |
| 当日开盘 open | 14.77 | 同上 |
| 实际成交价 | **14.77** | `trades.csv`（该笔买入） |
| 当时现金 | 902,358.69 | 直采记录 |

→ **`px_exec` 逐位等于当日收盘价，实际成交价逐位等于当日开盘价。反推结论升级为直采确证。**

> 取证附注：本库 `stock_daily.time` 为**北京时间零点**（raw 1751299200000 = 2025-07-01 00:00 CST）；
> 首次按 UTC 零点（1751328000000）精确匹配查不到行，已修正（±8h 时区差）。反推脚本原用 `time <= t` 取最近行，
> 恰好落在正确的 2025-07-01 行，故反推结论未受影响。

## 3. 改动范围（方向甲 · 最小侵入）

| 项 | 内容 |
| --- | --- |
| 落点 | `quantstudio/backtest/ptrade_api.py` 接线层换算价取值（`:2819` 与 `:2872` 两处 `px_exec = _QSPriceState.orig(security)`） |
| 做法 | 依 `match_price_mode` 分流：`open` → 当日**开盘价**（与引擎成交基准一致）；`close` → 当日**收盘价**（保持现行为不变） |
| 价源 | 复用引擎已构建的撮合价字典（`_build_match_prices` 的产物），或等价的开盘价取数口——**不新增数据访问路径** |
| 明确不改 | 引擎接口与撮合逻辑（`_build_match_prices`）、`_api.current_price` 的对外语义、记账/估值价（仍用收盘）、`_qs_split_order` 的现金钳制逻辑、任何策略源码 |
| 通用性 | 改动位于 P-D12 通用接线层，**对所有策略生效**，非本策略专用 |

**为什么不是乙案（引擎暴露本次撮合价）**：面大且当前无第二受益方（`next_open` 已废弃）；甲案已能消除错配。
**为什么不是丙案（策略层自行定量）**：违反「策略生成与转换全链路修复：仅限框架层」，禁止把框架问题下推策略层。

## 4. 影响面（行为正确性修复必须显式声明）

| 维度 | 声明 |
| --- | --- |
| 触发条件 | **任何** `match_price_mode='open'` 的策略（E1-2 要求「开盘执行必须显式声明 open」→ 触发面会持续扩大） |
| 结果变化 | open 模式下每笔 `order_target_value` 的**股数与成交金额**会变；`close` 模式**逐位不变** |
| 波及 | 所有 open 模式策略的 R5/R5.5 证据须**全部重跑**（成交金额变 → 三件套哈希变） |
| 不受影响 | `close` 模式策略（默认多数）、记账/估值、撮合价格本身、信号计算 |

## 5. 验收标准

| # | 判据 |
| --- | --- |
| V1 | **反向复现**：修复前，插桩直采证明 `px_exec == 当日收盘` 且成交价 `== 当日开盘`（已完成，见 §2.2） |
| V2 | **修复后直采**：同窗口同插桩，`px_exec == 当日开盘`（逐笔 13/13 命中），成交价不变 |
| V3 | **金额收敛**：open 模式策略单只成交金额围绕 10 万的离散度显著收敛（目标值 ±1 手价）；修复前 31/139 笔低于整手上限，修复后应趋近 0 |
| V4 | **close 模式零回归**：任一 close 模式策略同窗口重跑，三件套 SHA-256 **逐位不变**（不变量） |
| V5 | **回归套件**：`run_contract_gate.py --strategies` PASS + 相关单测全绿 + 6 策略 api_portability 无衰减 |
| V6 | **差异归因**：open 模式策略的 diff 只能归因于「换算价来源」；出现任何其他 diff 即验收失败 |

## 6. 回退条件

- V2/V4/V5 任一失败 → **单点回退**换算价取值逻辑（恢复 `px_exec = _QSPriceState.orig`）；
- 回退后须重新登记该缺陷为「未修」，不得静默带过；
- 本策略（`csi300_slow_kd_reversal`）在修复落地后必须重跑 R4/R5/R5.5 并重新发布（其当前发布版基于错配价定量）。

## 7. 附：配套件清单

| 件 | 路径 |
| --- | --- |
| 本方案 | `docs/px-exec-basis-mismatch-design.md` |
| 直采脚本（插桩，不改框架） | `agent_workspace/csi300_slow_kd_reversal/probe_px_basis.py` |
| 直采原始记录 | `agent_workspace/csi300_slow_kd_reversal/probe_px_basis_records.json` |
| 反推脚本（全窗口逐笔反解） | `agent_workspace/csi300_slow_kd_reversal/probe_px_basis_infer.py` |
| 归因报告（ZCode 复核 + DSH 归因） | `agent_workspace/csi300_slow_kd_reversal/ZCODE_REVIEW_REPORT.md` |
---

# 3A. ③放行前补入：两实施条件（审计方新发现 + 本线亲验精化）

> 来源：总调度②审计意见（2026-10-03，PASS + 两条件）｜状态：**已补入，③可放行**
> 本线纪律：审计方给出的代码链与代码草稿**已逐行亲验**，并对其草稿作了**一处必要更正**（见 3A.1.3）。

## 3A.1 条件一：价源精确化（审计新发现——方案大幅简化）

### 3A.1.1 审计发现的调用链（本线亲验通过）

| 环节 | 位置 | 亲验结果 |
| --- | --- | --- |
| 主循环传参 | `backtest_engine.py:602` | `self._run_ptrade_strategy(day, prev_day, **match_prices**, ...)` ✓ |
| ptrade 路径注入 | `backtest_engine.py:2169` | `_api.attach(self, curr_data, prev_data, day_str, prev_day_str, **prices**)` ✓ |
| 落到 `_prices` | `ptrade_api.py:584` | `self._prices = prices or {}` ✓ |
| open 模式下 `match_prices` 语义 | `backtest_engine.py:1313-1314` | `match_price_mode == 'open'` → `col = 'open'` → **当日开盘价** ✓ |
| close 模式下 | `backtest_engine.py:1318-1319` | `col = 'close'` → 当日收盘价 ✓ |

**结论：`_api._prices` 即 `match_prices`，在 open/close 两种模式下**都等于引擎的实际成交基准价** → 精化方案**零新增取数路径**，且两条模式自动语义正确。

> 亲验附注（防误读）：同文件另有 `:2266` / `:2425` 两处 `attach_day(..., prev_close_prices)`——那是**其他策略类型路径**（非 `ptrade` 即时执行路径），与本件无关；若照抄那两处的实参会得到**前收**而非成交基准，属高危误读，已在审计意见基础上显式标注。

### 3A.1.2 精化后的实现（定稿）

```python
# 接线层（模块级函数 _qs_wire_order_target_value 内，替换原 :2819）
# 换算价优先取「引擎本日撮合价」（open→开盘 / close→收盘，语义自动正确）；
# 取不到（_prices 为空 / 该标的缺价）时回退原语义 _QSPriceState.orig。
px_exec = 0.0
try:
    _qmt = _api._bare_to_qmt(bare_code(security))
    _raw = _api._prices.get(_qmt, 0.0) if getattr(_api, '_prices', None) else 0.0
    px_exec = float(_raw or 0.0)
except Exception:
    px_exec = 0.0
if not (px_exec > 0):
    px_exec = _QSPriceState.orig(security)          # 原语义兜底：_prices 为空 dict 或该标的无价
if px_exec <= 0:
    return _QSOrderWiringState.target_orig(security, value, *args, **kwargs)
```

同样替换 `:2872` 处的 `_QSPriceState.orig(security)`（同一接线逻辑的另一分支）。

### 3A.1.3 对审计方代码草稿的一处必要更正（**请审核方复核**）

审计意见给出的草稿为：

```python
px_exec = self._prices.get(_bare_to_qmt(security), 0) or _QSPriceState.orig(security)
```

**其中 `self` 在此处不可用**——接线层 `_qs_wire_order_target_value` 是**模块级函数**，无 `self` 参数。本线亲验确认：

| 因素 | 事实 | 依据 |
| --- | --- | --- |
| 接线层函数形态 | 模块级（使用 `_QSPriceState` / `_QSOrderWiringState` 等模块单例，非实例方法） | `ptrade_api.py:2806-2826` |
| `_bare_to_qmt` 的归属 | **`PtradeAPI` 的 `@staticmethod`**，非模块级函数（其内部委托模块级 `normalize_to_qmt`） | `ptrade_api.py:1605-1607` |
| 可达的 API 单例 | 模块级全局 `_api = PtradeAPI()` | `ptrade_api.py:2695` |

→ 故定稿改用 `_api._prices` / `_api._bare_to_qmt(...)`，并补 `bare_code(security)` 归一（与 `_get_current_price` 的 `:1547-1548` 同款），
使 `'600886.SS'` / `'600886'` 两种入参形态都能命中 `_prices` 的 QMT 键。

**该更动属实现细节修正，不改变审计结论与方向；请审核方在③放行时一并确认。**

## 3A.2 条件一附：两个 fallback 边界（审计要求，均已处理）

| # | 边界 | 事实（亲验） | 处理 |
| --- | --- | --- | --- |
| ① | **分钟 profile** | `attach_bar` 的 `bar_prices` 由**当根分钟 bar 的 close** 构造（`backtest_engine.py:2299` 与 `:2467-2468`）；分钟路径**不构建 `match_prices`** | 精化式在分钟面将取到「分钟 bar close」。**须显式声明其语义**（见 3A.3），并按「取不到则回退」保证不劣化 |
| ② | **`_prices` 为空 dict** | `initialize` 前的初始 attach 传 `{}`（`backtest_engine.py:488`）→ `_prices = {}` | 精化式取不到 → **回退 `_QSPriceState.orig`**（原语义）；定稿代码已含此回退 |

## 3A.3 条件二：分钟 profile 行为的**显式声明**（审计要求：不可沉默遗漏）

**声明如下（事实基础均为亲验行号）**：

1. **本件修复范围＝日线 profile 的接线层换算价**；`daily-bar-v1` 下 `_prices ≡ match_prices`（3A.1.1 全链实证），精化后换算价与成交基准**必然一致**。
2. **分钟 profile 下 `_prices` 的语义不同**：其值来自 `attach_bar` 的 `bar_prices`＝**当根分钟 bar 的 close**（`:2299` / `:2467-2468`），而非 `match_prices`（分钟路径不产 `match_prices`）。
3. 因此 **分钟 profile 下精化式取到的是「当根 bar close」**。若分钟撮合基准确为该 bar close，则语义同样正确；**若不一致，则分钟面的换算价与成交基准仍可能分叉**——**本件不宣称已覆盖分钟面**。
4. **不劣化保证**：分钟面走的是同一条「取不到即回退」的代码路径，且精化式仅在 `_prices` 有值时生效——**close 模式与无 `_prices` 场景行为逐位不变**（见验收 V4）。
5. **后续处置（不在本件内）**：分钟面是否需要对齐，须由引擎侧确认其撮合基准价后**另立方案件**（候选：由引擎同步把 `match_prices` 传至 bar 层）；本件**只做声明，不做分钟面改动**，避免扩大影响面。

> 本策略（`csi300_slow_kd_reversal`）系日线策略，不受分钟面声明影响；该声明为框架通用件的完整性义务。

## 3A.4 两条件补入后的放行结论

| 条件 | 状态 |
| --- | --- |
| 一（价源精确化 + 两 fallback 边界） | **已补入**（3A.1 / 3A.2），含对审计草稿的一处更正 |
| 二（分钟 profile 显式声明） | **已补入**（3A.3），5 条声明，含「不宣称覆盖 + 不劣化保证 + 后续另立件」 |
| ③实施放行 | 审计意见：**补入两条件后即刻实施**，验收序 **V2 → V3 → V4 → V5 → V6** |
