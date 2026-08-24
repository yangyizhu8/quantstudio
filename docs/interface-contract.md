# 接口契约文档（策略作者必读）

> 版本: v1.0（Phase A）| 对应方案 v2.1 Phase A0-A4
> 适用对象: **策略作者**（写 initialize/handle_data 等生命周期函数的人）
> 目标: 让策略作者"只关注策略逻辑"，不踩底层契约的坑。

本文档定义 QuantStudio 回测框架与策略代码之间的**全部接口契约**。违反契约会导致回测结果不可信或策略行为异常。Phase A（A0-A4）已修复所有已知契约漏洞，本文档是这些修复的正式记录。

---

## 1. 撮合价契约（A2）⭐最关键

### 三种撮合价模式

引擎通过 `match_price_mode` 参数声明 order 成交价口径：

| 模式 | 撮合价 | 语义 | 适用场景 |
|---|---|---|---|
| `"close"`（默认） | 当日收盘价 | 策略当天决策、当天收盘价成交 | 兼容历史回测结果 |
| `"open"` | 当日开盘价 | 策略当天决策、当天开盘价成交 | 部分缓解未来函数 |
| `"next_open"` | 次日开盘价 | 策略 T 日决策、T+1 开盘成交 | [DEPRECATED 2026-08-14: PTrade 实证确认平台用 close 撮合（日线 5/5、分钟 6/6 精确匹配 raw close），next_open 非对齐模式] |

> **Ptrade 对齐口径（2026-08-14 实证）**：PTrade 平台撮合价 = raw close（不复权原始收盘价），日线 = T 日 close、分钟 = 下单 bar close；持仓估值 last_price = raw close。本地引擎撮合/估值链路同样使用 raw close（`execution_price_basis=raw_trade_price`）。信号计算（`get_history`/`get_price`）保持 `fq='pre'` 前复权口径，与撮合/估值分离；转换到 PTrade 时由 source_import 自动注入 `fq='pre'` 保证两端信号一致。

> **平台差异吸收契约（2026-08-22，A1-A3）**：以下平台差异由框架/转换管线吸收，策略层零平台知识——

| API | 平台差异 | 吸收机制 |
|---|---|---|
| `order_target_value` 等订单 API | 市价单单笔上限：创业板/科创板 50,000 股（51,000 拒）、沪深主板 ≥86,900；超限整单取消 | 转换管线自动拆单（>49,000 股拆多笔，`_qs_split_order`，阈值 `_QS_MAX_ORDER_SHARES=49000`） |
| `current_price(security)` | **非真实 PTrade API**（官方文档全文无此 API；真实平台模块加载期引用 NameError，2026-08-22 双证）；统一链 PTrade 侧 = ① 前收（框架 `_qs_last_close_lookup`）→ ③ get_history 兜底；本地侧另有 ② 原 API | QuantStudio 本地扩展；双端/PTrade 目标策略由 Validator `TARGET-LOCAL-EXTENSION-BAN` BLOCK（profile local_only_symbols 已登记） |
| `get_current_data()` | **非真实 PTrade API**（文档全文零出现；本地扩展） | QuantStudio 本地扩展；双端/PTrade 目标同理 BLOCK |
| 回测取当前价（平台官方方式） | `data[code].price`（当前周期最新价）/ `get_history` 最新 bar / `get_price` | 平台文档 L151-154/5913；`get_snapshot` 仅交易场景（回测不可用） |
| `get_trade_days()` | 无 end_date 返回全量日历（含未来）；YYYYMMDD/date 混用 | 转换侧 `_qs_norm_date_str` 归一 'YYYY-MM-DD' + `<= 当日` 过滤 |
| `get_stock_info(listed_date)` | 格式混用 | 转换侧归一 'YYYY-MM-DD' |
| `filter_stock_by_status('ST')` | **转换语义不一致（P-D9）**：本地 'ST' = is_st_reliable OR is_delisting_risk（close<1 OR circ_mv<5亿 退市风险兜底）；平台仅官方 ST 标记（仙股留池） | 转换侧 `_QS_FILTER_STATUS_EXT` 注入：原生过滤后补 `close<1` 剔除（price-only；circ_mv 平台不可得 KeyError 实证 + 本地零触发降级）；fail-open 与本地一致 |
| `get_fundamentals` / `get_fundamentals_batch` | **返回形状不一致 + 字段名不一致（P-D10）**：平台 `growth_ability` 无 `or_yoy` 而使用 `operating_revenue_grow_rate`；`end_date/publ_date` 为字符串对象；批量返回 dict[code→DataFrame] | 转换侧 `_QS_FUNDAMENTALS_EXT` 注入：统一返回 `DataFrame(index=code, columns=fields)`；本地→平台字段名自动映射；日期归一数值；缺失字段 `QS_SHIM_FIELD_MISSING` 显性警报 |
| 退市强平（D4-S3） | T 日按当日 last 全额强平、T+1 现金落账 | 引擎层（B 组，数据前提已 PASS，独立流水线） |
| 资金不足三态（D4-S4） | 超限取消/可负担降量/买不起一手失败 | 登记为语义差，不建模（对齐判据配对） |

队策略代码**禁止手写平台兜底**（`def _normalize_date_str`/`def _current_raw_price`/`g.last_close` 自维护 → Validator `PTRADE-PLATFORM-FALLBACK-BAN` BLOCK）。费用：平台最低佣金 ≈5 元/笔，与本地 `min_commission=5.0` 同构（拆单双端对称）。

```bash
# CLI 切换模式
python -m quantstudio.backtest.run_ptrade_strategy 策略.py 2026-01-01 2026-07-13 --match-price close
```

### ⚠️ 未来函数警告（必读）

**`match_price_mode="close"`（默认）存在未来函数风险**：
- `data[stock].close` 在 `before_trading_start` 里返回的是**当日收盘价**
- 若策略据此下单，成交价也是当日收盘价 = 用当天收盘价成交当天决策 = 未来函数
- 这会让回测收益虚高（"上帝视角"）

**如何避免**：
1. **对照 Ptrade 平台时**：用 `--match-price close`（PTrade 实证确认 = close 撮合，2026-08-14）
2. **若坚持用 close 模式**：策略的信号应基于**前一日数据**（`context.previous_date`、`get_history` 不含当日），不要在 `before_trading_start` 里读 `data[stock].close` 用于下单决策

### 记账价 vs 撮合价（分离设计）

- **撮合价**（`match_prices`）：order 成交用，按 `match_price_mode` 取 close/open/next_open
- **记账价**（`prices`）：净值/持仓估值用，**始终用当日收盘价**（标准做法，与模式无关）

这意味着即使 `match_price_mode="next_open"`，`nav_history` 的净值仍按当日收盘估值。两套价格分离，互不污染。

---

## 2. 生命周期时序契约

每个交易日的执行顺序（`backtest_engine.py` 主循环）：

```
T+1解锁（前日买入的 now 可卖）
    ↓
before_trading_start(context, data)   ← 盘前选股/构建股票池
    ↓
handle_data(context, data)            ← 盘中交易（order 即时成交）
    ↓
after_trading_end(context, data)      ← 盘后收尾（仅 ptrade 模式）
    ↓
记录净值（按当日收盘价估值）
```

### portfolio 刷新时机（重要）

- `context.portfolio` 在**每次 order 调用后**原地刷新（`refresh_portfolio`）
- **不是**在生命周期节点统一刷新
- 含义：`before_trading_start` 里下第一单后，紧接着读 `context.portfolio.cash` 已反映新状态

### T+1 锁定语义

- 每日开盘前：`pos.can_sell = pos.volume`（前日持仓全部解锁）
- 买入时：`can_sell` **不增加**（当日新买的不可卖）
- 卖出时：只能卖 `can_sell` 部分
- `after_trading_end` 后：`can_sell` 保持开盘值（不锁定，次日开盘再解锁）

---

## 3. context.portfolio 字段契约

| 字段 | 类型 | 单位 | 保证存在 | 说明 |
|---|---|---|---|---|
| `cash` | float | 元 | ✅ | 可用现金 |
| `positions` | dict[code → Position] | - | ✅ | 持仓字典，key 与 PTrade 策略/CSV 一致为 `.SS`/`.SZ`；原生 dict membership 不跨后缀 |
| `market_value` | float | 元 | ✅ | 持仓市值（按最新价） |
| `total_value` | float | 元 | ✅ | 现金 + 市值 |

### Position 字段

| 字段 | 单位 | 说明 |
|---|---|---|
| `sid` | str | PTrade 持仓格式代码（`600000.SS` / `000001.SZ`） |
| `amount` | 股（int） | 持仓股数 |
| `enable_amount` | 股 | 可卖股数（T+1 后） |
| `avg_cost` | 元 | 持仓均价（含交易成本） |
| `cost_basis` | 元 | 成本基础 |
| `last_sale_price` | 元 | 最新价 |
| `market_value` | 元 | 该持仓市值（@property） |

> **重要**：`amount` 单位是**股**（不是金额）。策略可用 `pos.avg_cost` 直接算盈亏，无需自己手记成本价。

---

## 4. 订单失败契约（A3）⭐

`order_target_value` / `order` / `order_value` / `order_target` 返回 **Order 对象**（不再是字符串）。

### Order 对象字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `order_id` | str | 订单 ID |
| `security` | str | 证券代码 |
| `direction` | str | `"buy"`/`"sell"` |
| `target` / `filled` | float | 目标金额 / 实际成交金额 |
| `target_amount` / `filled_amount` | int | 目标股数 / 实际成交股数（Phase E 预留部分成交） |
| `price` | float | 成交价（含滑点） |
| `status` | str | `"filled"`/`"rejected"`/`"partial"`/`"pending"`(实盘预留) |
| `reason` | str | 失败原因（见下表） |

### status 与 reason 枚举

| status | reason | 含义 |
|---|---|---|
| `filled` | （空） | 全部成交 |
| `rejected` | `limit_up_blocked` | 买入涨停被阻断 |
| `rejected` | `limit_down_blocked` | 卖出跌停被阻断 |
| `rejected` | `no_price` | 取不到价格（数据缺失） |
| `rejected` | `insufficient_cash_or_rounding` | 资金不足或整股取整后为 0 |
| `rejected` | `below_rebalance_threshold` | 微调跳过（`< min_rebalance_pct`） |
| `rejected` | `no_operation_needed` | `order_target` delta=0（无需操作） |
| `partial` | - | 部分成交（Phase E 实盘） |
| `pending` | - | 实盘已提交未回报（Phase E 预留） |

### 策略侧用法

```python
# 新策略：检查订单状态（推荐）
order = order_target_value(stock, 10000)
if order.status != "filled":
    log.warning(f"下单未成交: {stock}, reason={order.reason}")

# 老策略：不检查返回值也能跑（__bool__ 兼容）
order_target_value(stock, 10000)  # 不接收返回值，照常工作
if order_target_value(stock, 0):  # 老式 if 判断：filled→True, rejected→False
    log.info("卖出成功")
```

---

## 5. 取数缺失契约

### get_fundamentals

- **永不返回 None**，永远返回 DataFrame
- 无数据时返回**空 DataFrame**（带正确字段名）
- 三大报表（`balance_statement`/`income_statement`/`cashflow_statement`）当前为空（待 Phase D 补齐）
- `valuation` 表（PE/PB/市值）有数据
- 策略判空应统一用 `if df.empty:`（无需判 None）

```python
df = get_fundamentals(stocks, "valuation", fields=["pe_ttm"], date=context.previous_date)
if df.empty:  # ✅ 正确
    return []
# 无需 if df is None:  ❌ 多余（不会返回 None）
```

### 其他取数 API 的缺失语义

| API | 无数据时返回 | 策略判空 |
|---|---|---|
| `get_industry(code)` | `None`（不是空 dict）。**APPROXIMATION_REQUIRES_CONFIRMATION（F4，非 PIT READY）**：当前回测日 as-of 查 SW2021 正式成员表。官方 `index_member` 仅给 `in_date`/`out_date`、无冲突裁决规则，canonical 表原样保留重叠区间、不应用自定义裁决；重叠命中时抛 `ReferenceDataCapabilityError`（fail-closed），绝不返回任意自定义裁决近似。无有效历史归属返回 `None`，绝不使用最新行业；正式表缺失抛 `ReferenceDataCapabilityError`（fail-closed），绝不回退 legacy `sw_industry` 快照 | `if industry and industry.get('sw_l1'):` 双重判空 |
| `get_security_info(code).start_date` | `None`。F2 起股票与 ETF 统一元数据层均可返回上市日 | `if start_date and start_date <= ...:` |
| `get_stock_info(stocks, field)` | 未知代码返回兼容空值记录（`stock_type='stock'`、日期 `None`、名称为入参代码）。股票行为与历史完全一致（名称=裸码、上市日=stock_daily 首根K线）；F2 仅扩展 ETF：`stock_type='etf'` 与真实名称/上市/退市日（`YYYY-MM-DD`） | `if info[code]['listed_date']:` |
| `get_index_stocks(code)` | `[]`。严格 PIT（F3）：显式 `date` 只取不晚于该日的最近 `complete` 快照（完整性由 `index_constituents_snapshot_meta` 批次契约在打点判定，不依赖未来数据）；无历史快照/无 meta 返回空（fail-closed，不向未来 fallback）；回测中不传 `date` 注入当前回测日期 | `if stocks:` |
| `get_history(...)` | 空 DataFrame | `if df.empty or len(df) < N:` |

### get_history 路由与前复权回退契约（F5/F6）

多表路由：普通股票→`stock_daily`；ETF→`etf_daily`；普通指数与申万行业指数（801xxx）→统一 `index_daily`（行业指数与普通指数共用 canonical schema：code/time/OHLC/pctChg/volume[股]/amount[元]）；指数历史不足时按既有契约用 `INDEX_ETF_MAP` 跟踪 ETF 代理（如 000300→510300）。`fq='pre'` 用 `*_front` 列替换原始价；指数无复权列时回退原始 OHLC（指数行情绝不套用 ETF 前复权逻辑）。行业指数不写入 `stock_daily`，无数据返回空而非静默转换。

---

## 6. filter_stock_by_status 退市状态契约（2026-07-18 新增）

### 6.1 与 Ptrade 的 4 种 filter_type 对齐

| filter_type | Ptrade 语义 | 本地实现 | 数据来源 |
|---|---|---|---|
| `ST` | ST/\*ST 股票 | `is_st_reliable==True OR is_delisting_risk==True` | `stock_namechange` PIT 推导 + 退市兜底 |
| `HALT` | 停牌 | `suspendFlag==1 OR volume==0` | `stock_daily` |
| `DELISTING` | 已正式退市 | 前一日无 `stock_daily` 数据行 | `stock_daily` 行存在性检查 |
| `DELISTING_SORTING` | 退市整理期 | `is_delisting_risk==True` | aligner 预计算（close<1 元 + 市值<5 亿）|

默认 `filter_type = ["ST", "HALT", "DELISTING"]`（向后兼容）。

### 6.2 已知近似实现（与 Ptrade 差异）

`DELISTING_SORTING` 的判定不与 Ptrade 内部精确状态 100% 一致，使用了两个工程化兜底规则：

1. **close < 1.0 元**（A 股面值退市触发线）
2. **近 20 日流通市值 < 5 亿元**（A 股市值退市触发线，2024 年新规）

这是对 Ptrade 内部退市整理期判定逻辑的**合理近似**，而非精确复写。

### 6.3 已知数据限制

| 问题 | 影响 | 缓解 |
|---|---|---|
| xtquant `isST` 字段不可靠（\*ST 不标记，如 002231 全程 isST=0） | 不可用 | `is_st_reliable` 替代（从 akshare namechange 推导）|
| 本地 DuckDB 无 `stock_basic.name` 字段 | 无法按 `\*ST` 名称兜底 | `is_st_reliable` 从 namechange 官方变更记录推导，不依赖当前名称快照 |
| 沪市无带日期的简称变更全表（akshare 仅深市有 `stock_info_sz_change_name`） | 沪市 ST 历史依赖退市兜底 | 沪市退市整理期股通过 close<1 + circ_mv<5 亿兜底覆盖；未来可补 data source |
| `check_limit` ST 涨跌停阈值未生效（依赖 name 字段）| ST 股用 10% 阈值而非 5% | 记入 `pipeline-tech-debt.md` TD-11，待数据层补 name 后修复 |

### 6.4 stock_daily 新增 4 个字段

```
is_st_reliable: bool              # 官方 ST/*ST（从 stock_namechange PIT 推导）
is_st_reliable_source: 'namechange' | 'none'

is_delisting_risk: bool           # 退市风险兜底（close<1 OR 近20日 circ_mv<5亿）
is_delisting_risk_source: 'price' | 'market_cap' | 'both' | 'none'
```

这 4 个字段在 aligner 层预计算，回测层 `filter_stock_by_status` / `get_stock_status` 直接读取，零额外计算。

### 6.5 回归案例

`002231`（\*ST 奥维通信，2025-04-30 变 \*ST，2026-03-27 摘牌）：
- 原 `isST` 全程为 0（xtquant 失真）
- 修复后 `is_st_reliable` 从 2025-04-30 起为 True
- `is_delisting_risk` 在退市整理期（close 0.6-0.73 元）为 True，source='both'

### 6.6 get_stock_status 同步语义

`get_stock_status(query_type='ST')` 与 `filter_stock_by_status(ST)` 语义一致：
- True = `is_st_reliable OR is_delisting_risk`（官方 ST 或退市风险股，都不该买）

`get_stock_status(query_type='DELISTING_SORTING')` 仅读 `is_delisting_risk`。

---

## 7. 代码后缀互通契约

Ptrade 代码后缀互通语义（已实现，策略可用任意后缀）：

| 写法 | 等价 |
|---|---|
| `600000.SH` | 沪市 |
| `600000.SS` | 沪市 |
| `600000.XSHG` | 沪市（Ptrade 原生） |
| `600000`（裸码） | 自动识别 |
| `000001.SZ` / `.XSHE` / 裸码 | 深市 |

引擎内部归一化到 QMT 格式（`.SH`/`.SZ`）。以下 API 支持后缀互通：
- `data[code]` 取 BarData
- `order_target_value(code, ...)` 下单
- `get_position(code)` 查持仓
- `get_history(code, ...)` 取历史

`context.portfolio.positions` 和 `get_positions()` 保持真实 PTrade 容器语义：key 为
`.SS/.SZ`，且 Python `in` membership 是精确匹配，不把 `.XSHE/.XSHG` 自动视为
同一个 key。需要跨后缀查单只持仓时使用 `get_position(code)`。该区别会影响直接写
`code in context.portfolio.positions` 的策略控制流，不得改成 alias-aware dict。

## 8. 配置与路径契约（A1）

### EngineConfig（外部注入）

引擎运行所需路径（数据库/输出目录）由入口（CLI/GUI）显式注入，源码不做硬编码推导：

```python
# CLI 入口（run_ptrade_strategy.py）显式构造
config = EngineConfig(
    db_path=ROOT / "data" / "quantstudio.db",
    output_dir=ROOT / "output",
    research_dir=ROOT / "output" / "research",
    rebalance_mode="legacy",  # F1：默认 legacy；"callback_basket" 仅 daily-bar-v1 + next_open
)
engine = BacktestEngine(config=config, ...)

### rebalance_mode 与篮子再平衡契约（F1）

- `rebalance_mode` 默认 `legacy`（现有单订单/pending 行为，修复前后逐项一致）；
- `callback_basket` 仅在 daily-bar-v1 + `next_open` 激活，导出记录
  `engine_semantics_version=0.4.0-next_open_basket`（`next_open + legacy` 保持
  `0.2.0-next_open_pending`）；分钟 Profile 显式 ValueError；
- 生命周期边界：`handle_data` 订单进入 basket；`run_daily`/`before_trading_start` 永不进入；
- PyQt 回测面板以通用下拉框透出（内部值 `legacy`/`callback_basket`，显示文本不作引擎参数），
  `close`/`open + callback_basket` 在点击运行前被 GUI 阻断。
```

**策略作者无需关心**：
- 数据库在哪里（由入口决定）
- 输出目录在哪里（由入口决定）
- 项目根路径（迁移机器只改入口，不改源码）

**策略作者只需知道**：调用 `get_fundamentals`/`get_history` 等取数 API，框架自动查对的库。

### 空交易日错误诊断契约

回测区间无交易日时，`BacktestEngine.run()` 仍抛出 `ValueError`，基础消息仍为
`No trading days in backtest range: {start} ~ {end}`。DuckDB 日历 provider 支持诊断时，
消息追加以下一种多行上下文，但不改变异常类型、交易日查询返回值或成功路径行为：

- 数据库无法连接：数据库路径、文件存在性、连接异常及占用/配置排查建议；
- 连接成功但区间无数据：`stock_daily` 实际最小/最大日期、不同交易日数及补采建议。

诊断钩子不存在、返回异常或诊断数据无法解析时，必须退化为原始单行消息，诊断失败不得掩盖
原始 `ValueError`。该诊断只在空交易日失败分支执行。

---

## 9. 数据源口径契约（D10 决策）

API 底层封装口径（§1-§8，函数语义/撮合逻辑）与**数据源口径**是两个正交维度，
必须分别 pin 死并相互校验，否则"已对齐 Ptrade"的结论会把近似对齐误当作精确对齐。

### 9.1 权威源

- 本地引擎 / `PtradeAPI` 封装层 / 回测引擎统一读 `EngineConfig.db_path`（本地 DuckDB）。
- 权威数据源口径 `EngineConfig.data_source` 默认 **`tushare`**（D10 决策：tushare 是离 Ptrade 聚源最近的可用代理源）。
- 已知口径集合：`tushare`（本地 DuckDB）/ `xtquant`（交易所直连源）/ `juyuan`（Ptrade 平台聚源）。

### 9.2 基准侧口径声明

- `PtradeBaseline` 加载 Ptrade 平台导出 CSV（= 聚源），`source_id` 默认 **`juyuan`**。
- 任何对照前，`FidelityComparator` 用 `assert_source_consistency(engine_source, baseline_source)` 校验两侧口径。

### 9.3 一致性校验规则（程序断言，非约定）

| 情形 | 行为 |
|---|---|
| 任一 `source_id` 为空 / 不在已知集合 | **拒绝**（沉默漂移最危险，永远拦截） |
| 两端口径相同（如 tushare vs tushare） | 一致放行 |
| 跨源但属白名单 `tushare↔juyuan` | **告警 + 标记 `cross_source=True`**（已知 float_value 精度容差，属 L3 软指标），放行 |
| 非白名单跨源（如 tushare vs xtquant） | **拒绝**对照 |
| `strict=True` | 连白名单跨源也拒绝 |

校验结果写入 `FidelityReport.source_check`，并在 `--compare` 输出与 `report.json` 中可见。
含义：**同口径或已声明容差的 tushare↔juyuan 对照是合法的**；其它任何口径漂移都会被程序拦下，
从机制上保证"两个对齐的口径两边一致"。

## 附录：契约对应的源码位置

| 契约 | 源码位置 |
|---|---|
| 撮合价（A2） | `backtest_engine.py:_build_match_prices` / `__init__(match_price_mode)` |
| 生命周期时序 | `backtest_engine.py:run()` 主循环 |
| portfolio 刷新 | `backtest_engine.py:refresh_portfolio` |
| Order 对象（A3） | `backtest_engine.py:Order` dataclass |
| 订单失败语义 | `backtest_engine.py:_immediate_execute`（返回 Order） |
| 4 个 order 函数 | `ptrade_api.py:order_target_value/order/order_value/order_target` |
| 代码后缀互通 | `ptrade_api.py:DataDict._bare` / `CodeDict._bare` |
| EngineConfig（A1） | `backtest_engine.py:EngineConfig` / `PtradeAPI._cfg` |

---

## 变更记录

| 版本 | 日期 | 改动 |
|---|---|---|
| v1.0 | 2026-07-17 | Phase A 完成：A0 benchmark bug、A1 路径解耦、A2 撮合价模式、A3 Order 对象、契约文档化 |
| v1.1 | 2026-07-17 | Phase B0 完成：Ptrade CSV 格式规格表（基于真实样本探样） |
| v1.2 | 2026-07-18 | ST/退市复写对齐：§6 新增 filter_stock_by_status 4 种 filter_type 契约 + is_st_reliable/is_delisting_risk 字段说明 + 已知数据限制 |
| v1.3 | 2026-07-19 | §9 新增数据源口径契约：EngineConfig.data_source(默认 tushare) 与 PtradeBaseline.source_id(默认 juyuan) 一致性断言（白名单跨源 tushare↔juyuan 告警放行，其余/未声明口径拒绝对照） |
| v1.4 | 2026-07-20 | 持仓容器后缀语义回归修复：portfolio/get_positions 恢复 .SS/.SZ 精确 dict membership；跨后缀兼容仅保留在 get_position/DataDict/CodeDict，防止 ETF 动量策略控制流漂移 |
| v1.5 | 2026-07-27 | F1-F6 框架修复：§5 取数缺失语义表更新（get_stock_info ETF 统一元数据/get_index_stocks 严格 PIT/get_industry 严格 PIT fail-closed）+ get_history 多表路由与前复权回退契约；§8 EngineConfig 新增 rebalance_mode 与篮子再平衡契约 |

---

## 附录 B：Ptrade CSV 导出格式规格表（B0，基于真实样本）

> 来源：2026-07-17 用户从 Ptrade 平台导出的真实样本（`私募工作文件/ptrade_samples/`）
> 标准对照区间：**2026-01-01 ~ 2026-04-29**（CSV 实际数据范围，用户钦定）
> 本规格表是 Phase B2 导入器的**唯一实现依据**，导入器严格按此解析，不做猜测。

### B.1 文件清单与编码

| 文件 | 用途 | 编码 |
|---|---|---|
| `交易详情<时间戳>.csv` | L1 信号一致性、L4 成本偏差 | **GBK**（Ptrade 中文导出，需 `encoding='gbk'`） |
| `持仓明细<时间戳>.csv` | L3 持仓重叠、净值反推 | **GBK** |
| `Log.txt` | L1 信号辅助校验（订单生成日志） | **UTF-16 LE**（文件头 BOM `fffe`，导入器优先 UTF-16 解码） |

> **重要**：文件名含中文且带时间戳后缀（如 `交易详情20260717110256.csv`）。导入器应按**前缀匹配**（`交易详情*.csv`）而非精确文件名查找。

### B.2 交易详情 CSV 规格

**表头**（固定 8 列）：
```
日期,时间,合约代码,买/卖,开/平,成交量,成交价,手续费
```

| 列 | 类型 | 格式/取值 | 单位 | 示例 | 说明 |
|---|---|---|---|---|---|
| 日期 | str | `YYYY-MM-DD` | - | `2026-01-05` | 交易日 |
| 时间 | str | `HH:MM:SS` | - | `15:00:00` | 固定 15:00（日线回测撮合时刻） |
| 合约代码 | str | `<6位裸码>.SZ` / `.SH` | - | `002830.SZ` | **Ptrade 用 .SZ/.SH 后缀**（非 .XSHE/.XSHG） |
| 买/卖 | str | `买` / `卖`（中文） | - | `买` | **方向用中文**（非 buy/sell、非 1/-1） |
| 开/平 | str | `-`（全为 `-`） | - | `-` | A股无开平概念，此列恒为 `-`，导入器忽略 |
| 成交量 | float | `XXXX.XXX` | 股 | `1000.000` | **带 3 位小数**（需转 int） |
| 成交价 | float | `XX.XXX` | 元 | `19.420` | 含滑点的成交价 |
| 手续费 | float | `XX.XXX` | 元 | `6.772` | 单笔总手续费（佣金+税+过户费合并） |

**样本统计**（2026-01-05 ~ 2026-04-28）：
- 55 笔交易（30 买 + 25 卖）
- 每笔手续费约 6-26 元（与万2.5佣金+印花税口径一致）

### B.3 持仓明细 CSV 规格

**表头**（固定 9 列）：
```
日期,时间,合约代码,最新价,仓位,多/空,持仓成本价,市值,累计盈亏
```

| 列 | 类型 | 格式/取值 | 单位 | 示例 | 说明 |
|---|---|---|---|---|---|
| 日期 | str | `YYYY-MM-DD` | - | `2026-01-05` | 持仓快照日 |
| 时间 | str | `HH:MM:SS` | - | `15:30:00` | 固定 15:30（盘后快照） |
| 合约代码 | str | `.SZ` / `.SH` | - | `002830.SZ` | 同交易详情 |
| 最新价 | float | `XX.XXX` | 元 | `19.420` | 当日收盘价（估值用） |
| 仓位 | int | `XXXX` | 股 | `1000` | 持仓股数（**整数，无小数**） |
| 多/空 | str | `多`（全为 `多`） | - | `多` | A股仅做多，此列恒为 `多`，导入器忽略 |
| 持仓成本价 | float | `XX.XXX` | 元 | `19.427` | 含手续费的买入均价（对应 Position.avg_cost） |
| 市值 | float | `XXXXX.XXX` | 元 | `19420.000` | 仓位 × 最新价 |
| 累计盈亏 | float | `±XX.XXX` | 元 | `-6.800` | **单持仓级**（该股票的累计盈亏，非账户总盈亏） |

**样本统计**（2026-01-05 ~ 2026-04-29）：
- 76 个交易日 × 5 只持仓 ≈ 380 行（实际 403 行，含调仓日 6 只的过渡快照）
- 每日固定 5 只持仓（小市值策略 buy_stock_count=5）

### B.4 Log.txt 规格（辅助校验）

**格式**（UTF-16 LE 编码）：
```
YYYY-MM-DD HH:MM:SS - INFO - <消息>
```

**关键消息模式**（正则）：
- 选股：`buy_stocks:\['(\d{6}\.\w+)', ...\]` → 当日目标股票池
- 订单生成：`生成订单，订单号:(\w+)，股票代码：(\d{6}\.\w+)，数量：(买入\|卖出)(\d+)股`
- 卖出标记：`sell:(\d{6}\.\w+)`

**用途**：Log.txt 比 CSV 更原始（记录策略意图而非成交结果），可用于 L1 信号对照的**交叉验证**（CSV 是成交结果，Log 是下单意图，两者应一致）。

> **B0 探样发现（重要语义）**：Log ⊇ CSV。实测 Log 103 条意图 vs CSV 55 条成交，差值 48 条是**未成交/废单**（涨停买不进、跌停卖不出）+ CSV 区间外的意图。这印证了 Ptrade 平台存在订单失败（与本地 A3 Order 对象的 rejected 语义对应）。**CSV 每笔成交在 Log 中都有意图**（csv_only = 0），反向不成立。L1 对照时这个差异本身就是宝贵数据（Ptrade 平台订单失败率）。

> **注意**：Log.txt 为 UTF-16 LE 编码（文件头 BOM `fffe`）。导入器优先用 UTF-16 解码，失败回退 GBK/UTF-8，errors='replace' 容错。

### B.5 净值反推方案（关键：无独立净值 CSV）

**Ptrade 未导出独立的逐日净值 CSV**。L2 净值偏差通过**反推**得到：

```
每日总资产(t) = 持仓市值合计(t) + 现金(t)
现金(t) = 初始资金 - Σ(截止 t 日所有买入支出) + Σ(截止 t 日所有卖出收入)
  买入支出 = 成交量 × 成交价 + 手续费
  卖出收入 = 成交量 × 成交价 - 手续费
每日净值(t) = 每日总资产(t) / 初始资金 × 初始净值基准
```

**初始资金**：100,000（Ptrade 默认，与本地一致）

**精度损失（必须标注）**：
- 交易详情的"成交价"是撮合价（含滑点），持仓明细的"市值"用"最新价"（收盘价）
- 反推现金用成交价，反推市值用最新价，两者口径不同会有小额差异
- **预期差异量级**：< 0.1%（滑点 0.1% 的影响）
- 导入器应在报告中输出"反推净值 vs 持仓市值合计+现金"的残差，残差过大则告警

### B.6 代码后缀与方向标准化

导入器解析后，统一标准化到本地引擎格式：

| Ptrade 导出 | 标准化为 | 说明 |
|---|---|---|
| `002830.SZ` | `002830.SZ` | 直接用（本地引擎接受 .SZ/.SH） |
| 买/卖（中文） | `"buy"` / `"sell"` | 方向枚举标准化 |
| `1000.000`（float） | `1000`（int） | 成交量转整 |
| `多`/`空` | 忽略 | A股仅做多 |

### B.7 对照区间与样本覆盖

| 项 | 值 |
|---|---|
| **标准对照区间** | 2026-01-01 ~ 2026-04-29（用户钦定，CSV 实际范围） |
| 交易详情覆盖 | 2026-01-05 ~ 2026-04-28（55 笔） |
| 持仓明细覆盖 | 2026-01-05 ~ 2026-04-29（76 个交易日） |
| Log.txt 覆盖 | 2026-01-05 ~ 2026-07-13（但末段编码混乱，有效段到 ~2026-06） |

> **后续扩展**：若用户后续导出完整 2025-07-13 ~ 2026-07-13 区间，规格表不变，仅样本替换。导入器设计应与区间无关。



## Local ETF universe API contract

`get_etf_list()` remains a PTrade-named compatibility API and is unavailable in the PTrade backtest profile. QuantStudio does not widen it.

`get_etf_list_local(query_date=None, etf_type="equity", active_only=True)` is a registered QuantStudio-only extension:

1. API layer resolves `query_date=None` from the active backtest date.
2. `ReferenceDataProvider.get_etf_list_local` normalizes the date.
3. `DuckDBDataAccess.query_etf_universe_pit` filters `etf_basic` by listing/delisting metadata and requires at least one `etf_daily` bar on or before the query date.
4. The API returns PTrade-style `.SS/.SZ` codes.
5. `etf_type="equity"` additionally requires `is_cross_border=false`.

The call is blocked when targets include PTrade. Dual ETF strategies use a customer-confirmed static whitelist; QuantStudio-only strategies may use the local API and `get_history_batch`.

## User-PyQt candidate contract

R4 PASS may expose a comment-marked QuantStudio candidate in the PyQt strategy directory. It is byte-derived from the canonical source and bound by SHA-256. No formal target may be published until reviewed R5 evidence proves the same candidate completed on the recorded provider/database/window/profile. Source drift invalidates R4. R6 regenerates formal files and retires the candidate.
