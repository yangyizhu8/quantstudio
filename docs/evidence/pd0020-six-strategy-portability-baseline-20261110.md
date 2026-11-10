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

---

## 七、A 项修正方案 ＋ C 项探针（2026-11-11 00:1x · R-20261110-06 裁①②）

### 7.1 A 项：条目与方案

**条目**：`PD-2026-0022`（族 pd／层 gate／域 D3）——已 `kb_draft` 机检通过。

**改动（`quantstudio/strategy_compiler/portability_rules.py` ＋ 校验器）**：

| # | 改动 | 说明 |
|---|---|---|
| A-1 | `is_trade()`、`set_commission()` **从 DENY 白名单移除** | 均为 PTrade 公有 API（附 Context7 逐字依据于规则注释） |
| A-2 | `get_research_path()` **从 FILE-DB-ACCESS 名单移除** | 它只返回路径字符串，不访问文件 |
| **A-3** | `PORTABILITY-FILE-DB-ACCESS` **检测点迁移**：从「按 api 名匹配」改为「**按真实文件访问调用点匹配**」（`open(`／`os.path.*`／`os.makedirs`／`json.dump`／`pd.read_*`／`.to_csv`／`.to_parquet`） | **同批实施**（裁①条件②） |
| A-4 | **负向不放松**：非 Profile 的本地专属 API **仍拦**（`DENY_REMOVE`/`DENY_BLOCK` 保留） | 裁①条件③ |

**②审三条件（裁①）**：

1. **仅三 API 白名单**，且**附 Context7 逐字依据**（`utilities.md`／`Ptrade国盛.md`／`settings.md`）；
2. **FILE-DB-ACCESS 检测点迁 `open()` 调用点**——**同批**实施，不得只放宽不移点（否则真实文件访问从此无人拦）；
3. **负向非 Profile 仍拦**——白名单化不得成为"全面放宽"。

### 7.2 C 项：待实证探针（裁②）

**目标**：确认 `open(get_research_path() + 'xxx.pkl', 'wb')` 在 PTrade **公有 Profile 交易环境**下是否真成功。

**探针设计**（并入下次实跑产物，**只读不写业务数据**）：

```python
# PERSIST 块内，写后立即读回并打日志（自证成功性）
try:
    _p = _PD_NOTEBOOK + 'QS_PERSIST_PROBE.txt'
    with open(_p, 'w') as _pf:
        _pf.write('probe-ok')
    with open(_p, 'r') as _pf:
        _back = _pf.read()
    log.info('QS_PERSIST_PROBE write_read_back=%s path_len=%d' % (_back == 'probe-ok', len(_PD_NOTEBOOK)))
except Exception as _e:
    log.warning('QS_PERSIST_PROBE failed=%s: %s' % (type(_e).__name__, _e))
```

| 读数 | 结论 | 后续 |
|---|---|---|
| `write_read_back=True` | `open()` 在 `/home/fly/notebook/` **可用** | ⇒ `C` 转为「平台支持」⇒ PERSIST **无需重构**；规则按 A-3 只迁检测点 |
| `failed=<异常>` | `open()` **不可用**（只读或禁写） | ⇒ PERSIST **须重构**（改 `g.state` 或其它机制），另立条目 |
| 无该日志行 | PERSIST 块未执行 | ⇒ 需先确认注入是否生效（回到产物核查） |

**边界**：探针**只写一个 ~10 字节的 txt**，不触碰 `ETF_f926a7_state.pkl`，**不改变任何业务行为**。

### 7.3 与 §12 ②审的关系

- **A／C 两项均不改 §12 的甲方案设计**（甲方案在 `order` 包装内折算，与 portability 规则正交）；
- §12 极短版②审**下一轮送**（裁③），A 项放宽另行走②审（条目 `PD-2026-0022`）。

---

## 八、②审（§12 甲方案 ＋ A 项放宽 合并极短版 4 问）结论

> **送审形态**：极短版（内容嵌入、不读文件、4 问、限 700 字）⇒ **33,987 tokens**（in 27,739／out 6,248），
> session `sess_23df5826-0f5c-4e5a-92e3-0e11abf398db`。
> **裁定：有条件通过**（4 项均附条件，**无一票否决**）。

### 8.1 a（order 包装折算）——**可过**，两点均不足以否决

| 疑虑 | 二审结论 | 条件 |
|---|---|---|
| 截断 vs 比例缩量 | **逐单包装层无篮子上下文，比例缩量在此层级不可实现** ⇒ 截断是**唯一可行语义**；权重失真属隔离层**已知代价** | ①钳制日志须含**原量/可买/剩余额度三数**（可审计）；②文档登记该边界 |
| px 缺失 fail-open | **真弱点但非否决项** | ①**独立日志码**（区别于正常钳制）；②**RECONCILE 汇总「未武装」事件数**，使保护层失效可被发现；③下版建议改 **fail-closed 拒买**（呈用户裁定，可后置） |

⇒ **我方接受**：「比例缩量不可实现」是对的——我原想的「按 budget/需求 比例缩量」在 `order()` 单笔包装层确实拿不到其它标的的需求，无法计算比例。这是**二审纠正了我方的一个不可行设想**。

### 8.2 b（卖出不钳）——**方向正确但不充分**，须补两条

| # | 必补 | 理由 |
|---|---|---|
| ① | **卖出成交后回写账本**（释放 `reserved`／增 `cash_base`） | 否则额度**只耗不补**，换手后额度枯竭失真 |
| ② | **账本非负不变量**：任何字段 `<0` 记异常日志交 RECONCILE，**禁静默钳零** | 超卖防护依赖券商/平台拒单（**需实证**），对账兜底 |

### 8.3 c（F2 简化）——**可接受**

条件：①隔离模式下把 `order_target_value` 等**金额版调用面列入检测**（出现即拦/告警）——pos 不精确的风险由**调用面检测**兜底，而非靠精确维护；②F2 降级写入设计文档**边界节**。

### 8.4 d（A 项放宽 ＋ 检测点迁移）——**A-1/A-2/A-4 可过；A-3 须补三约束**

| 项 | 结论 |
|---|---|
| A-1／A-2／A-4 | **可过**（逐字文档依据已附）。注：`set_commission` 实盘调用行为＝平台定义，**需实证**，非可移植性问题 |
| **A-3** | **绕过风险确实存在**（别名赋值、`getattr` 非字面量、未列 IO 面）⇒ **AST 名匹配对前两者天然漏检** |

**A-3 三约束（必补）**：

1. **真实 IO 调用面扩列**：`pathlib`／`csv`／`pickle`／`shutil`／`subprocess`／`os.remove` 等（现清单不全）；
2. **动态取函数**（`getattr` 非字面量、别名赋值）判**可疑**（fail-closed 或 WARN）；
3. **负向自测**增加别名／`getattr`／`pathlib` 用例，且**必须仍被拦**。

### 8.5 必补项汇总（进实施清单）

| 编号 | 必补 | 归属 |
|---|---|---|
| B1 | 钳制日志含**原量/可买/剩余额度三数** ＋ 文档登记截断边界 | §12 a |
| B2 | px 缺失：**独立日志码** ＋ **RECONCILE 汇总未武装事件数** | §12 a |
| **B3** | **卖出成交后回写账本**（释放 reserved／增 cash_base） | §12 b（新增） |
| **B4** | **账本非负不变量**（`<0` 记异常，禁静默钳零） | §12 b（新增） |
| B5 | 隔离模式下**检测金额版调用面**（`order_target_value` 出现即拦/告警） | §12 c |
| B6 | A-3 IO 面扩列 ＋ 动态取函数判可疑 ＋ 负向自测三用例 | A 项（PD-2026-0022） |
| **B7（呈裁）** | px 缺失是否改 **fail-closed 拒买**（二审建议**可后置**） | 呈用户裁定 |
| **B8（需实证）** | `set_commission` 实盘调用行为、超卖防护依赖券商拒单 | 需实证，不作先验假设 |

### 8.6 效力

- **②审已过（有条件通过）** ⇒ §12 甲方案与 A 项放宽**均可进③实施**，但须**先落 B1~B6**；
- **B7／B8 不阻断**（B7 可后置、B8 标需实证）；
- 实施批范围更新：a-2 ＋ b ＋ 甲-order（含 B1/B2/B3/B4）＋ F2 简化（含 B5）＋ BUILD 唯一化 ＋ **A 项放宽（含 B6）**。
