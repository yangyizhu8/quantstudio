# source_import PTrade 行情翻译缺口修复 — 方案设计

> 文档类型：框架层改动 · 方案（六步流水线 Step 1）
> 日期：2026-08-19
> 状态：**实施完成，待审计验收**（Step 3 实施于 2026-08-19；Step 4 验收证据见 §7）

## 1. 问题定义

### 1.1 现象（真实平台证据，2026-08-19 02:29 客户运行）
将 `vol_regime_mom_rev_quantstudio.py` 经 `qs-compile import`（source_import）转换为
PTrade 版本后在真实 PTrade 平台运行，每个交易日重复出现：

```
15:00:00 - WARNING - get_history(market) failed: %s
15:00:00 - WARNING - monthly %s: regime unavailable, retry next trading day
```

即 `_compute_regime` 中的 `get_history(market)` 抛错 → regime 永远不可用 → 策略**从不建仓**。
（同一日志亦暴露 `log.X("fmt %s", arg)` 在 PTrade 上不格式化，输出裸 `%s`。）

### 1.2 根因
规范的本地策略源码使用了**本地专用构造**，source_import 转换器未等价翻译：

| # | 本地构造 | 现象 | 层次 |
|---|---|---|---|
| A | `get_history(field=['close','trade_date'], ...)` 中的 `trade_date`（本地 provider 合成伪列） | `_QS_FIELD_TO_PTRADE` 不识别 → 原样传给 PTrade → **invalid field → get_history 抛错** | 转换器缺口（框架层） |
| B | `get_history(..., is_dict=True)`（本地扩展返回 {code: df}） | wrapper 原样透传 → 真 PTrade `get_history` 无 `is_dict` 参数 → TypeError | 转换器缺口（框架层） |
| C | `log.warning("... %s", exc)` 等 Python-logging 风格 | PTrade LogEngine 不做 C-style 参数格式化 → 输出裸 `%s`，异常信息被吞 | 规范源码写法（策略层） |

### 1.3 为什么不能靠本地改策略绕过
候选（全A约4000只）横截面排序依赖每股「按交易日历锚点取收盘价 + 逐股拆分」，
若去掉 `trade_date`/`is_dict` 而改走非 is_dict 多标的返回，则依赖 **PTrade 多标的
get_history 返回契约（每行是否含证券码列、日期形态）**，该契约当前未在转换器内定义/验证，
属于转换层应负责的边界。

### 1.4 审计证据（2026-08-19，全量比对 `ptrade/` 目录 15 个已转换文件）
> 用于回答「为什么既往转换不出问题、本次出问题」。

| 特征 | 既往已转策略（`ptrade/*.py`） | 本次 `vol_regime_mom_rev` |
|---|---|---|
| 使用 `trade_date` | **0 个**（全部无） | **是（唯一一个）** |
| 使用 `is_dict` | 5+ 个（bbi_etf_rotation / sw_industry_etf_rotation_8f / ashare_manual_pool_2d_momentum_top2 / first_cover_event_daily / smallcap_overnight_scalp_7）——**有先例** | 是 |

比对结论：
- 本策略是**首个把本地伪列 `trade_date` 送进转换器**的对象。既往策略全是**固定窗长/位置序**
  （20/60/252 根K线累计或均值），或像 `fall_reversal` 用 `context.current_dt` 字符串判月
  （`today[:7]`）、跌幅用 252 根收盘的**位置序** —— 均不需要「每根K线所属日历月」，因此从未
  请求日期列 → `_QS_FIELD_TO_PTRADE`（仅收录 `amount`/`preClose`）这一字段映射盲区**从未被触发**。
- 真实平台抛错的**首因** = `field=['close','trade_date']` 中 `trade_date` 为非法字段
  （`get_history` 直接 raise → `regime unavailable` → 不建仓），而非转换器整体失效。
- `is_dict` 在既往转换中有先例 → 其对真平台是否兼容**仍待确认**（见 §6/C）；对本次失败而言**并非首因**。

## 2. 改动范围（仅 source_import 转换输出侧，最小增量）

改动文件：`quantstudio/strategy_compiler/source_import.py` 的 `_QS_HISTORY_WRAPPER`（150-206 行）
与对应单元测试 `tests/test_source_import.py` / `tests/test_ptrade_contract_compliance.py`。

### A. 返回侧合成 `trade_date`（合成字段机制）
- 在 `_qs_to_dataframe`（或 wrapper 收口）中：当返回体含 `datetime`/`time` 列时，
  派生 **`trade_date`（'YYYY-MM-DD' 字符串）** 列并加入返回 DataFrame。
- 效果：策略代码 `_extract_history_field(hist, 'trade_date', dtype=str)` 在 PTrade 侧
  也可读到与本地一致的日期字符串 → σ 分月/动量锚点逻辑无需改动。

### B. 请求侧剔除 `trade_date`
- `_QS_FIELD_TO_PTRADE` 增加一项语义：`trade_date` 为**合成字段**——请求前从 field 列表
  **剔除**（不传给 PTrade；PTrade 只收真实字段如 `close`），由 A 在返回侧补齐。
- 若字段列表剔除后为空 → 按错误处理（BLOCK，避免空 field 传给 PTrade）。

### C. `is_dict=True` 处理 — 契约无关的逐码路径（审计 R1）
- wrapper 先 `is_dict = kwargs.pop('is_dict', False)`。
- 当 `is_dict=True` 时，**不依赖任何「多标的返回含证券码列」的未验证契约**：按
  `security_list` 对每一证券**逐码调用原 `get_history`（单证券请求，返回形态风险最小）**，
  每块经 `_qs_to_dataframe` 处理（含 A 的 trade_date）后拼成 `{code: df}`。
- 语义与本地 `is_dict=True` 严格等价；代价为 N 次 API 调用（月度策略可接受，计入 known_limitation）。
- **「返回单块 + WARNING」回退删除**（下游按 dict 迭代会以另一种方式失败）。

### G. 门控（两 wrapper 变体，审计 R2 落地）
- `source_import` 检测源源码是否**显式使用 `trade_date`**（get_history 的 `field`/`fields` 含
  `trade_date`，或源码文本含 `'trade_date'` 字面量）：
  - **用** → 注入「新 wrapper」（含 A/B/C/G）；
  - **不用** → 注入**与现状逐字节一致**的旧 wrapper。
- 由此：不使用 `trade_date` 的存量策略**转换输出文本逐字节不变**（纯增益）；仅
  `vol_regime_mom_rev` 这类显式依赖 `trade_date` 的策略获得新 wrapper。

### D. （策略层，非框架改动）日志 `%` 预格式化
- 规范源码 `vol_regime_mom_rev` 的 `log.X("... %s", a)` 全部改为 `log.X("... %s" % (a,))`
  （PTrade LogEngine 单字符串语义），与 skill 规范已发布策略风格一致。
- 该改动只影响日志文本，不影响交易/净值（本地 R5 行为逐字节不变的部分），
  但仍按「源哈希变化 → 重跑 R5」处理。

## 3. 影响面

- **仅影响** source_import 生成的 PTrade 转换输出（`get_history` wrapper 代码块）。
- **本地引擎 / 数据适配 / 校验器 / 策略规范源码**（除 D 的日志文本）不受影响。
- **存量已转换策略**（`bbi_etf_rotation_ptrade.py` 等）：见 **3.1 纯增益（不污染）保证**。
- 风险点：PTrade 多标的 `get_history` 返回是否含证券码列、`datetime`/`time` 的精确实列名
  与 dtype —— 需以真实平台实证/既有证据与夹具固化；实证不足即标 `PTRADE_RUNTIME_UNVERIFIED`，
  **不宣称已验证可上线**。

### 3.1 纯增益（不污染存量转换）保证
- **门控双变体（见 §2 G）**：不用 `trade_date` 的策略注入旧 wrapper（文本逐字节不变）；
  只有显式使用 `trade_date` 的策略注入新 wrapper。因此对存量策略是**纯增益**。
- C（is_dict）按 R1 走逐码路径，仅在**新 wrapper**（=`trade_date` 使用者）内启用；
  存量 use is_dict（无 trade_date）策略（bbi_etf_rotation 等）继续用旧 wrapper，
  其 is_dict 返回契约与改造前**完全一致** → 不污染。

## 4. 验收标准

1. **（R3）trade_date 合成格式断言（以本地实测为基准，勿用文档假设）**：
   夹具输入 structured array（含 `datetime` 列，dtype 对齐 PTrade 实测），断言 `_qs_to_dataframe`
   合成出的 `trade_date` 与**本地 provider 同日期输出的 `trade_date` 逐字符相等**
   （本地实测：`object` 类型、`'YYYY-MM-DD'` 字符串，由 Asia/Shanghai `strftime('%Y-%m-%d')`
   推导一致）；同时断言合成时机在 `_QS_COL_TO_LOCAL`（datetime→time）改名**之后**，
   「先改名、再合成」，两列互不冲突。
2. 转换 round-trip：重新转换 `vol_regime_mom_rev` → 输出注入**新 wrapper**、`get_history`
   请求字段剔除 `trade_date`（仅合法字段）、`is_dict=True` 走逐码路径，`py_compile` 通过。
3. **（R2）存量回归改为行为等价**：门控保证「不使用 trade_date」的策略输出与之旧 wrapper
   文本**逐字节不变**（属纯增益的直接证据）；对受影响策略（vol_regime_mom_rev）：
   转换输出 diff **逐行审查**（预期 diff 仅限 wrapper 块新增的 A/B/C/G 逻辑，策略主体代码零变化），
   diff 审查记录写入验收证据；`tests/test_source_import.py`、`tests/test_ptrade_contract_compliance.py`
   全绿；存量 is_dict（无 trade_date）策略返回契约与改造前一致。
4. 本地/模拟回测：转换后 PTrade 模拟运行，交易/净值与规范 R5 一致（或按确认口径逐项归因）。
5. 真平台冒烟：**需客户在真实 PTrade 平台冒烟**（`PTRADE_RUNTIME_UNVERIFIED` 保持置位，
   仅在客户回传平台证据后由 `retire/runtime` 流程升级）。真平台失败 → 回到本条 1-4 归因修复。

## 5. 回退条件

- 任一验收项失败：
  - 若 D 之前已提交源码：先 `git stash create -u -m "baseline-<ts>"` 建零副作用快照，
    再 `git reset --hard <hash>`（留 hash 凭证）。
  - source_import 改动：回退 wrapper 变更，转换输出回到当前旧 wrapper（门控保证
    不用 trade_date 的存量转换始终不受影响，回退面最小；受影响的仅显式用 trade_date 的策略）。
- 已按 R1 消掉「回退 C 自动拆分」分支（逐码路径无多标的契约依赖）。

## 6. 待确认项（审计/用户）
- [x] 改动范围仅限 source_import 转换输出（A/B/C/G 门控）+ 规范源码日志（D），
      不碰本地引擎/验证器/数据层。 —— 审计已确认
- [x] 验收第 4/5 条口径（本地模拟由我方出证据；真平台冒烟由客户执行，
      `PTRADE_RUNTIME_UNVERIFIED` 保持置位）。 —— 审计已确认
- [x] 回退预案（先建零副作用 git 快照再 reset；R1 后简化 C 分支）。 —— 审计已确认

## 7. 实施记录与验收证据（2026-08-19）

### 7.1 实施内容

- 修改文件：
  - `quantstudio/strategy_compiler/source_import.py`：
    - `_QS_HISTORY_TRADE_DATE_EXT` 新增非 dict 模式下 `security_list`/`security` 透传逻辑；
    - `_qs_to_dataframe` 对已是 DataFrame 的返回也调用 `_qs_synthesize_trade_date`；
    - `_qs_synthesize_trade_date` 增加从 DataFrame index 合成 `trade_date` 的能力（PTrade 日线常以日期为 index，无 time/datetime 列）。
  - `quantstudio/backtest/strategies/vol_regime_mom_rev_quantstudio.py`：
    - line 469 `log.warning(..., total)` 改为 `%` 预格式化（PTrade LogEngine 兼容）；
    - 新增 `_normalize_date_str` 统一 `get_trade_days()` / `get_stock_info(..., 'listed_date')` 返回的日期格式（兼容 `YYYY-MM-DD`、`YYYYMMDD`、`datetime.date` 等），并增加选股无结果时的诊断日志。
  - `output/ptrade_export/vol_regime_mom_rev/vol_regime_mom_rev_ptrade.py`：使用修复后的模板重新生成。
  - `tests/test_ptrade_contract_compliance.py`：新增回归用例：
    - `test_trade_date_ext_non_dict_passes_security_list`
    - `test_trade_date_ext_synthesizes_from_index`

### 7.2 根因补充（实施中发现）

1. **非 dict 模式丢失 `security_list`**：原设计 §2 C 只显式规定了 `is_dict=True` 的逐码路径；非 dict 单市场调用（`_compute_regime` 中 `get_history(security_list=[_MARKET_CODE], ...)`）在实现时被遗漏：wrapper 把 `security_list` 从 kwargs 弹出后未在 else 分支回传，导致真实 PTrade 收到空 `security_list`，触发「未订阅股票池时，security_list不能为空」。
2. **DataFrame 返回未合成 `trade_date`**：PTrade 真实平台返回日线 DataFrame 时，日期在 index（列只有 `code`/`close`），而 wrapper 仅在 structured array → DataFrame 转换路径中合成 `trade_date`，对原生 DataFrame 直接返回，导致 `_extract_history_field(hist, 'trade_date')` 拿到空数组 → `IndexError`。
3. **index 转 Series 索引错位**：修复 2 的过程中发现，若直接把 `df.index` 转成 Series 而不保留原 index，后续 `strftime` 结果索引为默认整数，赋值给原 DataFrame 会因索引对齐失败而得到 NaN；需 `pd.Series(index, index=index)` 保留索引。
4. **`get_trade_days()` 日期格式差异**：真实 PTrade `get_trade_days()` 返回格式可能与本地 QuantStudio 不同（如 `YYYYMMDD` 字符串或 `datetime.date`）。策略原代码直接用 `str(x)` 生成月份锚点，与 `trade_date` 的 `'YYYY-MM-DD'` 无法匹配，导致 4331 只候选全部无法计算出有效收益 → `no ranked candidates`。
5. **`get_trade_days()` 无 end_date 时返回全量日历**（真实平台冒烟实证）：本地 QuantStudio 的 `get_trade_days()` 语义为「截至当前回测日」；真实 PTrade 不带 `end_date` 调用返回**全量交易日历（含未来）**。日志实证 `end_T=2026-12-31`（回测起始 2026-07-01），`trade_days[-2]` 取到 2026-12-30 → 月份锚点全部指向未来 → 候选 history 不含这些日期 → `no ranked candidates (4331 codes checked)`。

### 7.2b 修复处置（2026-08-19，真实平台冒烟第 4 轮）

- `source_import.py`（wrapper 侧）：见 §7.1 第 1/2/3 项。
- `vol_regime_mom_rev_quantstudio.py`（策略侧）：
  - 新增 `_normalize_date_str`：统一 `get_trade_days()` / `get_stock_info(..., 'listed_date')` 返回日期格式（兼容 `YYYY-MM-DD`、`YYYYMMDD`、`datetime.date`）。
  - **本地过滤未来日期（决定性修复，第 4 轮冒烟修正）**：先尝试 `get_trade_days(end_date=YYYYMMDD)`，失败则无参调用；随后**在策略内过滤掉 `> 当前回测日` 的日期**。因为真实 PTrade 不带 `end_date`（或忽略该参数）时返回**全量日历含未来**（第 5 轮冒烟实证 `end_T` 仍为 `2026-12-31`），依赖平台的 `end_date` 不可靠；本地过滤与平台行为无关，对本地 QuantStudio 是空操作（本地本就只返回到当日）。
  - **`current_price` 不可用 → 用选股时已拉取的最近日线收盘**（第 6 轮冒烟实证）：真实 PTrade `current_price('000004.SZ')` 返回 0，导致整手可负担预筛 `px>0` 全部失败 → `sell_submitted=0 buy_submitted=0`。修复：`_selected_targets` 把每只候选的最近收盘价记入 `g.last_close`；`_current_raw_price` 按 `g.last_close → current_price → get_history(count=1)` 优先级取值，保证预筛有价可用。
  - 增加选股样本/锚点诊断日志（`selection sample ...` / `selection skip ... end_T=...`），供真实平台冒烟定位。

本次修复补齐上述分支，不改变 §2 设计语义。

### 7.3 验收结果

| 验收项 | 结果 | 证据 |
|---|---|---|
| 单元测试全绿 | ✅ | `tests/test_ptrade_contract_compliance.py` + `tests/test_source_import.py` 共 **74** 项 PASS |
| 新增回归用例 | ✅ | `test_trade_date_ext_non_dict_passes_security_list` 捕获非 dict security_list 透传；`test_trade_date_ext_synthesizes_from_index` 捕获 index 日期合成 |
| 转换输出可编译 | ✅ | `python -m py_compile output/ptrade_export/vol_regime_mom_rev/vol_regime_mom_rev_ptrade.py` 通过 |
| 本地源码可编译 | ✅ | `python -m py_compile quantstudio/backtest/strategies/vol_regime_mom_rev_quantstudio.py` 通过 |
| 模板与输出一致 | ✅ | 用修复后 `convert_source` 重新生成 `vol_regime_mom_rev_ptrade.py`，与手动修复版 `git diff --no-index` 零差异 |
| 门控纯增益 | ✅ | `test_trade_date_ext_not_injected_when_used` 验证不使用 `trade_date` 的策略仍注入旧 wrapper |

### 7.4 基线快照

- 修改前零副作用回退点：`git stash create -u -m "baseline-vol-regime-ptrade-fix-20260819-..."` → `adf150cc3bf582aaa19c7fad46c96752fcf37d55`

### 7.5 真实平台冒烟终局（2026-08-19 22:46，第 7 轮）— **PASS**

经 6 轮迭代修复后，`vol_regime_mom_rev` 在真实 PTrade（策略名「测试456」，2026-07-01~07-31，10 万资金）**完整跑通全月**，本地同区间回测对照通过。

#### 双端关键指标对照

| 指标 | 真实 PTrade | 本地 QuantStudio | 判定 |
|---|---|---|---|
| regime / sigma | reversal / **0.251286** | reversal / **0.251286** | ✅ 完全一致 |
| q | 0.9000 | 0.9333 | ⚠️ 窗口深度差（σ 有效月数不同），同判 reversal |
| selected/tradable | 5 / 5 | 5 / 5 | ✅ |
| buy_submitted | 5 | 5 | ✅ |
| 建仓后 positions | 4（1 笔被 5 万股上限撤销） | 5 | ⚠️ 平台语义差（见下） |
| 全月运行 | 23 交易日无崩溃，汇总完成 | 23 交易日无崩溃 | ✅ |

#### 已识别的双端语义差（非转换 bug，待登记）

1. **市价单 5 万股上限**（PTrade-only）：反转月选出超跌低价股（如 002808 约 0.23 元 → 86900 股），单笔超交易所市价单上限被撤单 → 该格空仓、现金留存 20%。本地引擎未建模此约束。
2. **退市强平 `is expired`**（PTrade-only）：000004/002808（07-13）、002898（07-17）持仓中被平台按退市强制平仓。本地引擎无此事件（持仓按月末价估值）。
3. **q 分位粒度**：本地 σ 有效 60 月（2018 起）vs PTrade 窗口略浅 → 0.9333 vs 0.9000，同一 regime 判定。
4. **横截面选股差异**：两端各选 5 只深跌股（仅 300854 重合），根因是双端日线数据源差异 + 极值排序对数据边缘高度敏感（与既往登记 D3-X1/D3-X2 同类）。

#### 策略层风险发现（与转换无关，建议策略侧处置）

反转月（q>0.75）持「最近 1 月跌幅最大 5 只」会自然买入**面值退市边缘股**（000004 收盘价 <1 元、002808 约 0.23 元），持有期内三只相继退市、全月 -22.92%（本地口径）。`filter_stock_by_status(DELISTING)` 只能剔除**决策日已确认退市风险**者，无法排除持有期内新退市。建议策略侧评估：候选池增加「收盘价 < 1.5 元剔除」或「is_delisting_risk 剔除」类护栏。

### 7.6 六步流水线状态

- Step 1 方案：本文档 ✅
- Step 2 审计：§6 三项待确认已由审计方确认 ✅（实施期间发现的 5 项根因补充已回写 §7.2/7.2b，属实现偏差修正，不改变设计语义）
- Step 3 实施：✅（§7.1）
- Step 4 验收：单元测试 74 PASS + 真实平台冒烟 PASS（§7.5）✅
- Step 5 用户确认：**待用户确认**
- Step 6 双仓库推送：待 Step 5 后执行
