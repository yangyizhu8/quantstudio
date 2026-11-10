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
