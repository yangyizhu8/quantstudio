# 六策略 api_portability 基线（2026-11-10 · 甲方案改动前置）

> 目的：甲方案（`order` 包装内按虚拟额度折算）**改动前**必须先证明六策略 `api_portability` 基线全 PASS，
> 否则改动后无法归因。**R-20261110-04 裁②照准项。**
> 调用方式：`validate_ptrade_portability(code: str, ir=None, spec=None) -> (ok, violations, warnings)`
> —— **参数是源码文本，不是路径**（首次误传路径致 4 个产物全部报 `SyntaxError`，属调用错误）。

## 一、基线读数（现有产物）

| 策略 | 产物 | ok | BLOCK | 判定 |
|---|---|---|---|---|
| CANSLIM突破成长选股策略 | 157,604 B | **True** | 0 | ✅ PASS |
| tech_etf_mvo_rotation | 67,390 B | **True** | 0 | ✅ PASS |
| vol_regime_mom_rev | 92,769 B | **True** | 0 | ✅ PASS |
| **四象限ETF轮动策略-ISO-P1**（今日实跑） | 102,619 B | **False** | **3** | ❌ **BLOCK** |

## 二、验收面不完整（3/6 缺产物）

| 策略 | 源文件 | 产物 | 说明 |
|---|---|---|---|
| fall_reversal_quantstudio | **有** | **无** | 需重转后才能入基线 |
| weekly_smallcap_growth | **无** | **无** | 源缺失 |
| 周频小市值成长动量三层止损 | **无** | **无** | 源缺失 |
| tech_etf_mvo_rotation | 无（仅产物） | 有 | 基线可用产物 |
| vol_regime_mom_rev | 无（仅产物） | 有 | 基线可用产物 |

⇒ **六策略验收面当前只有 3 个可用产物** ⇒ 甲方案实施后仅能对 3 个做「前后对照」；
其余 3 个需先补源/重转，否则**验收门槛（六策略全 PASS）无法在本批达成**。

## 三、⚠️ 四象限 ISO 产物的 3 个 BLOCK（**既有问题，非本次改动引入**）

| 规则 | 位置 | 内容 |
|---|---|---|
| `PORTABILITY-LOCAL-API` | line **221** | `is_trade()` — 本地专属 API（`portability_rules` DENY_REMOVE/DENY_BLOCK），PTrade 公有 Profile 禁止 |
| `PORTABILITY-FILE-DB-ACCESS` | line **352** | `get_research_path()` — 本地文件/DB/持仓访问（`ptrade-profile-contract.md` §2 禁止） |
| `PORTABILITY-LOCAL-API` | line **1824** | `is_trade()`（第二处） |
| 警告 | — | header 未声明 `ptrade-default` profile |

### 3.1 性质判定（铁律 18 四型）

**新增检测型**：verdict 由「未检出」变为「检出 BLOCK」，是**校验能力开始报告真相**，不等于行为漂移。
⇒ 须单列，不得与本次改动混办。

### 3.2 关键矛盾（须澄清）

**今日该产物在真实平台跑通了**（`QS_ISO_BANNER`／`QS_ISO_FILL`／`QS_ISO_RECONCILE` 四键全中、
3 笔委托全部成交）⇒ 与「禁止 API ⇒ BLOCK」冲突。三种可能：

| 可能 | 判据 | 处置 |
|---|---|---|
| **A. 平台实际允许** `is_trade()`/`get_research_path()` | 今日实跑成功 | 校验器规则过严 ⇒ 应放宽（另一条目） |
| **B. 调用位于不执行的分支**（`try/except` 兜底、未命中的 `if`） | 需读产物 line 221/352/1824 上下文 | 死代码 ⇒ 可清理（不影响行为） |
| **C. 调用真有执行**且平台容忍 | 需平台侧确认 | 风险项，登记 |

⇒ **未澄清前，甲方案验收不得把这三个 BLOCK 计入"改动引入"**，也不得因此判甲方案失败。

## 四、结论与下一步

1. **基线（3/6）成立**：CANSLIM／tech_etf_mvo／vol_regime_mom_rev **全 PASS** ⇒ 可用作甲方案前后对照；
2. **验收面不完整**：3 个策略缺产物/源 ⇒ 六策略门槛本批无法达成，需先补（或由你裁定本批降为 3 策略对照 + 后续补齐）；
3. **四象限既有 3 BLOCK** 待澄清（A/B/C 三择）后决定是否为条目；
4. 甲方案②审（极短版 3 问）**下一轮送**——先澄清 BLOCK 性质，避免②审基于不完整基线。

---

## 五、BLOCK 定性（裁①「B 先行」执行 · 2026-11-10 22:4x）

### 5.1 三处上下文取证

| # | 位置 | 所在函数 | 原文上下文 | 是否执行 |
|---|---|---|---|---|
| 1 | L**221** `if is_trade():` | `_deploy_guard_pass` | 前接 `check_limit()` 涨跌停护栏（L209-219）；`is_trade()` 为真时用 `get_snapshot(security)` 取快照价打「护栏参考」日志 | **真执行**（交易时段必进入） |
| 2 | L**352** `_PD_NOTEBOOK = get_research_path()` | **`<module>`（模块级）** | PERSIST 注入块：`try: _PD_NOTEBOOK = get_research_path() except: _PD_NOTEBOOK = ''` | **真执行**（**模块每次加载必执行**） |
| 3 | L**1824** `if not is_trade():` | `initialize` | GATE_RESTORE 块：`if not is_trade(): set_commission(commission_ratio=5e-05, min_commission=5.0, type='ETF')` | **真执行**（`initialize` 必执行；今日 01:24 启动属非交易时段 ⇒ **条件成立**） |

### 5.2 判定：**B（死代码）证伪**

⇒ 三处**均非死代码**，全部落在**会执行**的路径上。⇒ 按裁①进入 **A（平台实际允许 → 放宽规则，另立条目走②审）** 或 **C（风险项登记）** 分支。

### 5.3 A/C 判定所需依据（**尚未完成，不得预判**）

按**铁律 16（平台代码先查文档）**，判 A/C 前必须以 Context7 查证这三个 API 是否在 PTrade 公有 Profile：

| API | 待查证问题 | 现有旁证 |
|---|---|---|
| `is_trade()` | 是否为 PTrade 公有 API（判断交易时段） | 今日实跑**未报错**、且 L1824 分支语义成立（01:24 非交易时段 ⇒ 执行了 `set_commission`） |
| `get_research_path()` | 公有 API 是否提供（取研究目录路径，**仅取路径不读写文件**） | 模块级 try/except 兜底 ⇒ 若不存在则 `''`；今日未观察到相关告警 |
| `set_commission(...)` | 公有 API 签名是否含 `type='ETF'` | 未报错 |

⇒ **强旁证**：今日该产物**在真实平台运行成功**（四键全中、3 笔委托成交、无异常）⇒ 三个调用**未导致失败**。
⇒ 但「未失败」≠「规则应改」——**规则过严**与**平台容忍**是两种不同结论，须文档依据。

### 5.4 待办（下一轮）

1. **Context7 查证** `is_trade` / `get_research_path` / `set_commission`（库 ID `/kay-ou/ptradeapi`，单主题、≤3 次）；
2. 依结果定 **A**（另立条目放宽 `portability_rules`，走②审）或 **C**（风险项登记，不动规则）；
3. A/C 定后再送 **§12 极短版②审**（裁③：随澄清结果送）。

---

## 六、A／C 定性（Context7 查证完成 · 2026-11-10 23:3x）

> 依据：Context7 `/kay-ou/ptradeapi`（`api-reference-en/utilities.md`、`api-reference-en/settings.md`、
> `docs/original/Ptrade国盛.md`、`docs/original/Ptrade东莞.md`）。铁律 16 要求标注依据，故逐条引用。

### 6.1 三 API 查证结果

| API | 是否公有 | 文档逐字依据 | 判定 |
|---|---|---|---|
| `is_trade()` | ✅ **公有** | 「Determine whether the current strategy is running in **trading** environment or **backtest** environment. Returns `True` in trading environment, `False` in backtest environment.」（`api-reference-en/utilities.md`） | **A**（规则误判） |
| `get_research_path()` | ✅ **公有** | 「获取研究根目录路径。该函数可在**回测、交易模块**使用。返回路径为'/home/fly/notebook/'。」（`docs/original/Ptrade国盛.md`） | 公有 ✓，但配套 open 见 §6.2 |
| `set_commission(...)` | ✅ **公有** | `set_commission(commission_ratio=0.0003, min_commission=5.0, type="STOCK")`；`type` 支持 `"STOCK", "ETF", "LOF"`；「**该函数仅在回测模块可用**」（`api-reference-en/settings.md` + `Ptrade东莞.md`） | **A**（规则误判） |

### 6.2 **`is_trade()` 语义纠正（我方第 6 次自纠）**

⚠️ `is_trade()` 判的是「**交易环境 vs 回测环境**」，**不是**「是否交易时段」。
⇒ 我方 §5.1 表格中「交易时段必进入」的表述**错误**，应为「**交易环境下**必进入」。

**但产物用法反而因此更正确**：
- L1824 `if not is_trade(): set_commission(...)` ＝「**回测环境**才设佣金」——而文档明确「该函数**仅在回测模块可用**」⇒ **完全正确** ✓
- L221 `if is_trade():` ＝「**交易环境**才取 `get_snapshot` 快照价打护栏参考日志」⇒ 合理 ✓

### 6.3 **`_PD_NOTEBOOK` 的真用途（推翻"仅取路径"假设）**

| 位置 | 原文 | 性质 |
|---|---|---|
| L352 | `_PD_NOTEBOOK = get_research_path()` | 取路径（公有 API） |
| **L1808** | `with open(_PD_NOTEBOOK + 'ETF_f926a7_state.pkl', 'rb') as _pf:` | **读本地文件** |
| **L1973** | `with open(_PD_NOTEBOOK + 'ETF_f926a7_state.pkl', 'wb') as _pf:` | **写本地文件** |

⇒ `get_research_path()` 的返回值**被实际用于 `open()` 读写文件**（PERSIST 注入块，框架层 `_apply_persist`）。

### 6.4 **校验器检测点错位（新发现）**

`PORTABILITY-FILE-DB-ACCESS` 报在 **L352（`get_research_path()` 调用处）**，而**真实风险在 L1808/L1973 的 `open()`**。
⇒ 该 API 本身是官方文档明示「可在交易模块使用」的取路径函数；**被禁的是"访问本地文件"**（`ptrade-profile-contract.md` §2），
应报在 `open()` 处。**这是校验器的检测点错位，不是 API 不能用。**

### 6.5 最终定性（分项，不混办）

| 项 | 定性 | 处置 |
|---|---|---|
| `is_trade()` 被列 `PORTABILITY-LOCAL-API` DENY | **A — 规则误判**（公有 API） | **另立条目放宽** `portability_rules`，走②审 |
| `set_commission()` 同上（若也被列） | **A — 规则误判**（公有 API，`type='ETF'` 合法） | 同上 |
| `get_research_path()` 被列 `PORTABILITY-FILE-DB-ACCESS` | **A（API 本身）+ C（配套 open 读写文件）** 复合 | ①规则从「禁用该 API」改为「检测 `open()` 等真实文件访问」；②`open()` 读写 `/home/fly/notebook/` 是否被公有 Profile 允许，**文档未见明确许可** ⇒ **C 风险登记**，待平台实证或用户裁定 |

⚠️ **`open()` 是否真成功今日无法证明**：L1808 在 `try/except: _saved_state=None` 内 ⇒ 即使失败也静默 ⇒
「今日未报错」**不能**作为「文件读写成功」的证据。

### 6.6 结论

- **A 项确凿**：`is_trade` / `set_commission` / `get_research_path` **均为公有 API** ⇒ 校验器把它们当"本地专属"是**误判**，
  应另立条目放宽（**走②审**）；
- **C 项待定**：`open()` 读写 `get_research_path()` 下的 `.pkl` 是否属公有 Profile 允许的文件访问 ⇒ **文档无明文**，
  登记风险；**检测点应从 api 名改为 `open()` 位置**（这是校验器改进项）；
- **甲方案验收**：这 3 个 BLOCK 仍是「既有问题」，**不计入甲方案改动引入**；
- **§12 ②审**：A/C 定性已完成 ⇒ 按裁③**可送**（下一轮）。
