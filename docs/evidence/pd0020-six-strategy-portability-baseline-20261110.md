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
