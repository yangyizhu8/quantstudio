# 件 3 方案 v1.2 —— 平台原生涨跌停判定能力（PTrade `check_limit` 对齐）

- 版本：**v1.2**（v1.1 四条件 folded + **实施钉死项 folded**：数据源单一路径）｜日期：2026-10-15
- ②审结论：条件通过（四条件）⇒ 本版逐条 folded，**实施可启**
- 性质：**框架层新增能力**（六步流水线：①方案 → ②审 → ③实施 → ④验收 → ⑤用户确认 → ⑥双仓库推送）

**知识反哺：[命中] 机器学习多因子选股策略（01-策略/机器学习多因子选股策略.md，2026-10-15）**

| 摘录 | 采纳要点 |
| --- | --- |
| L113（事件驱动回测器核心架构）`DataProvider … └── check_limit_up/down(code, date)` | **涨跌停查询入口归属数据层**（DataProvider）——与本方案落点（provider）**一致** ✓ 既有设计先例 |
| L26（数据层）「QuestDB(stock_daily/…/**stk_limit**/…) 统一数据源，**含涨跌停价(1135万条, 2018-2026)**」 | 涨跌停价属**数据层既有资产**；若库内已有 `stk_limit` 类表，provider **优先取表**、无表则按 §三 规则自算 ✓ |
| L29 / L60（真实成交模型）「涨跌停/滑点/佣金」「防护3: 实盘失真」 | 涨跌停判定是**执行层**关注点 ⇒ 与 E1「raw 快照限执行层」一致 ✓ |

> 主库规范路径：`D:\miniQMT策略实盘\knowleage_base`（后续检索直接用绝对路径）

## 一、要件 a｜平台文档前置查询留痕（v1 已具，此处保留）

**依据 Context7 `/kay-ou/ptradeapi` → `docs/api-reference-en/utilities.md`**（原文）：

```
check_limit(security, query_date=None)
- security (str or list[str]) - Required
- query_date (str, optional) - Query date, format YYYYMMDD. Defaults to current date.
- Return: dict — key = security code, value = status code:
    1: Limit up   |  -1: Limit down  |  2: Touch limit up  |  -2: Touch limit down  |  0: No limit
```

**版本注记**（同库 `docs/original/Ptrade东莞.md`）：PBOXQT1.0V202202.01.038 ——「`check_limit()` **新增支持在回测、研究场景中使用**」✓

## 二、要件 b｜豁免窄口径（**校验器主体不改**，v1 结论保留）

**取证**（`<skill>/scripts/validate_agent_strategy.py:157-188`）：`SIGNAL_PRICE_APIS = {"get_history","get_history_batch","get_price"}`；非该集合的 API **不检查** ⇒ `check_limit` 为**新具名 API** ⇒ 集合与 `fq='pre'` 规则**原文不动** ✓

**负向测试（验收必含）**：`get_history_batch(fq=None)` / `fq='dypre'` / 省略 fq ⇒ **均 BLOCK** ✓；`check_limit(codes, query_date='20260105')` ⇒ PASS ✓

> **v1.1 补充**：新增**具名守卫规则**（见 §五）——属**为新 API 新增守卫**，不改既有 `SIGNAL-PRICE-ADJUSTMENT` 规则 ✓

## 三、要件 c + ②审条件 2｜±2 语义完备性与边界行为

### 3.1 状态判定规则（provider 实现口径）

| 返回码 | PTrade 语义 | 本地判定（`stock_daily`，`time = query_date` 行） |
| --- | --- | --- |
| **1** | 涨停 | `close >= up_limit − tol` |
| **-1** | 跌停 | `close <= down_limit + tol` |
| **2** | **触涨停** | 非 1 且 `high >= up_limit − tol` |
| **-2** | **触跌停** | 非 -1 且 `low <= down_limit + tol` |
| **0** | 无涨跌停 | 其余 |

⇒ **须补 `high` / `low` 列**（②审条件 2 ✓）：`stock_daily` 已含 `high`/`low` ✓（已核列清单）⇒ provider 直接取用，**不退化** ✓

### 3.2 涨停价取整规则（写入契约）

- `up_limit = round(preClose × (1 + pct), 2)`；`down_limit = round(preClose × (1 − pct), 2)` ✓
- `pct` 由既有 `get_price_limit_pct` 提供（**含 ST 5% 档** ✓，与 D-1b 修复一致）
- **舍入方式**：Python `round()` 为**银行家舍入**（0.5 → 偶）✗ ⇒ 与交易所四舍五入可能差 1 分 ⇒ **以分价容差吸收**：`tol = 0.001` ✓（与既有 `LIMIT_PRICE_TOLERANCE` 一致 ✓）；契约须写明此口径 ✓

### 3.3 首日 / 无 preclose 边界（**不得静默丢语义**）

| 情形 | 返回 | 诊断 |
| --- | --- | --- |
| `query_date` 行不存在（停牌/非交易日/未上市） | **0** | 记 `QS_DIAG`（kind=`LIMIT_CHECK_NO_ROW`，含 code+date）✓ |
| `preClose` 为空或 ≤0（首日/数据缺口） | **0** | 记 `QS_DIAG`（kind=`LIMIT_CHECK_NO_PRECLOSE`）✓ |
| `high`/`low` 为空（±2 不可判） | 退化为 ±1 判定 | 记 `QS_DIAG`（kind=`LIMIT_CHECK_NO_HILO`）✓ |


## 三·补｜实施钉死项（数据源单一路径）—— **已取证并 folded**

**取证（2026-10-15，副本库同 schema）**：主仓 DuckDB **表总数 101**；含 `limit` 的表 = `limit_cpt_list` / `limit_list_d` / `limit_list_ths` / `limit_step`；
**`stk_limit` 不存在** ✗ ⇒ v1.1 所写「优先取表（QuestDB `stk_limit`）、无表自算」中的**取表分支在主仓为死分支** ✗。

**裁定处置**：

1. **删除「优先取表」分支** ✓（v1.2 起 provider **无表读取路径**）
2. **单一数据源**：涨跌停状态一律由 `stock_daily`（`close`/`preClose`/`high`/`low`）+ `get_price_limit_pct` 派生 ✓ —— **单一口径，无双源** ✓
3. `limit_list_*` / `limit_step` 等表属**涨跌停列表/统计表**，**明确不作为价源**（防口径漂移 ✓）；契约须显式标注该边界 ✓
4. 知识库 L26 所述 `stk_limit`（1135 万条）属**知识库所记另一架构（QuestDB）的自有资产**，与主仓本地库无关 ✓ —— 该条知识仅作**设计先例**引用（L113 的入口归属），**不作数据源** ✓

## 四、要件 d + ②审条件 3｜PIT 边界成文

### 4.1 契约条文（写入 `knowledge/contracts/api-semantics.md`）

> **`check_limit(security, query_date)` 日期语义**：返回 `query_date` **当日**的涨跌停状态。
> `query_date = D`（当日）在回测语境意味着**消费 D 的收盘/最高/最低数据** ⇒ **仅在执行层上下文（收盘后/日终）合法**；
> 在**盘中或盘前**上下文中，一律须传 `query_date = T-1`（或更早），**禁止**依赖默认值（默认=当日）✗。
> 与 E1 铁律关系：E1 约束**信号价**（`get_history(..., fq='pre', include=False)`）；本 API 服务**执行层涨跌停判定**（E1 §4 明确允许 raw 快照用于执行层）⇒ 职责分离、互不替代 ✓。

### 4.2 静态用法守卫（②审条件 3：**决定加**，对齐 `:1113` 先例）

**先例依据**：`validate_agent_strategy.py:1113-1133`「`get_index_day_bar` 守卫」（引 `docs/get-index-day-bar-design.md`，2026-09-08）：具名 BLOCK 规则 + 设计文档注释 + 按上下文/profile 双条件拦截 ✓

**新增规则**（**为新 API 新增**，不改既有规则 ✓）：

| 规则名 | 触发 | 判定 |
| --- | --- | --- |
| **`LIMIT-CHECK-EXPLICIT-DATE`** | `check_limit(...)` **省略 `query_date`**（即依赖默认=当日 ✗） | **BLOCK**（可静态判定 ✓） |
| **`LIMIT-CHECK-SAMEDAY`** | `query_date` 为**字面量且等于当前回测日**（可静态判定时） | **BLOCK** |
| 动态 `query_date`（变量/表达式） | 静态不可判 | **PASS**（由契约 + 运行时保证；记入契约条文 ✓） |

**理由**：默认值与字面量当日是**唯一可静态判定的泄漏形态** ✓；动态值交由契约约束，避免误杀合法用法（如 `query_date=previous_date`）✓

## 五、改动范围（v1.1：**补第 6 项**）

| # | 落点 | 内容 |
| --- | --- | --- |
| 1 | `quantstudio/backtest/ptrade_api.py` | 新增 `check_limit(security, query_date=None)`；签名/枚举逐字对齐 PTrade ✓ |
| 2 | 数据访问层 provider | 按 §3.1 判定（补 `high`/`low`）；`pct` 复用 `get_price_limit_pct`；边界按 §3.3 记 `QS_DIAG` ✓ |
| 3 | 转换管线 | **直通**（PTrade 原生，不转换/不归一）✓ |
| 4 | 契约文档 | `knowledge/contracts/api-semantics.md`：新 API 语义 + §4.1 日期条文 + §3.2 取整口径 + §3.3 边界 ✓ |
| 5 | **校验器**（skill） | **主体规则不改** ✗；**新增两条具名守卫**（§4.2）✓ |
| **6** | **文档与 skill 双副本同步**（铁律「同步内容必须完整」） | **`README.md`**（策略工具箱/注入 API 章节）+ **`docs/strategy_toolbox.md`** + **`docs/prompt_engineering.md`** + **skill 双副本**（主仓 + 副本 AGENTS.md 跟随制）✓ |

## 六、要件 e｜回退条件（纯新增可整体停用）

1. 移除 `check_limit` 注入 API + provider 方法 + 两条守卫规则 ⇒ 回到本件前状态 ✓
2. 撤回契约文档条目与 §五-6 的文档同步 ✓
3. **零残留**：无数据写入、无持久化状态、无既有 API 行为变化 ✓
4. 触发回退：任一回归红 / 双端对等哈希不一致 / 枚举与 PTrade 不符 ✓

## 七、验收标准（v1.1）

| # | 项 | 判据 |
| --- | --- | --- |
| 1 | 负向测试 | `fq=None`/`fq='dypre'`/省略 fq ⇒ BLOCK；`check_limit` ⇒ PASS ✓ |
| 2 | **新守卫测试** | 省略 `query_date` ⇒ BLOCK；字面量当日 ⇒ BLOCK；`query_date=previous_date` ⇒ PASS ✓ |
| 3 | 枚举一致性 | 1/-1/2/-2/0 与 PTrade 文档逐项一致 ✓；**±2 用例实测**（构造触涨停/触跌停样本）✓ |
| 4 | 边界行为 | 无行/无 preclose/high-low 缺失 ⇒ 按 §3.3 返回 0 并记 `QS_DIAG` ✓ |
| 5 | PIT 抽验 | 指定 `query_date` 的返回只依赖 ≤该日数据 ✓（改当日收盘不改变 `query_date=T-1` 的结果 ✓） |
| 6 | 纯新增 | 既有测试全绿 + **6 策略 `api_portability` 全 PASS** + 既有功能零衰减 ✓ |
| 7 | 双端 | 转换产物含同名 `check_limit` 且 `api_portability` PASS ✓ |
| 8 | 文档同步 | §五-6 全部落位且可 grep 检索 ✓ |

## 八、E7 编码委派申报

③实施阶段（框架层编码：API + provider + 两条守卫 + 文档同步）**委派 `zcode_code`**（配额已恢复，②审照准）✓；本会话负责方案、验收与证据；若报 `[zcode-fallback:*]` 按纪律处理（quota/auth ⇒ 回退本会话并写明；ratelimit/timeout ⇒ 拆小重试一次）✓

## 九、执行序（folded 后）

③实施（委派）→ ④验收（§七 八项）→ ⑤呈用户确认 → ⑥双仓库推送 → **件 1 解锁**（策略再生成改用 `check_limit(query_date=T-1)`）✓

## 十、实施事实回填（2026-10-17）

- **⚠ 守卫生效前提（已知限制，明示入档）**：`LIMIT-CHECK-SAMEDAY` 的静态判据是「`query_date` 为字面量且命中**设计声明的回测窗口端点**」；而 design 2.3 的 `backtest_window_contract` **不含窗口日期字段**（窗口由客户确认，见 `actual_window_selected_by`）⇒ 该规则在**当前设计契约下为 inert**（不触发），实际防护由 `LIMIT-CHECK-EXPLICIT-DATE` + 本条日期条文 + 运行时 `QS_DIAG` 共同承担。将来若 design 增补 `backtest_window_contract.start_date/end_date`，该规则**自动生效**，无需改代码。
