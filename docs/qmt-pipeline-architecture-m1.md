# 大 QMT 转换管线 · 架构定稿（M1-rev2，2026-10-08 呈复审终判）

> **rev2 版本说明**：rev1 复审（ZCode）判定 11/12 落位 ✓+P1 一项 A 级必改（report_type PIT
> 语义方向选反——**PIT 安全项=announce_time**（按实际公告日期返回，不取未来数据，且即 API
> 默认值）；report_time 不考虑公告日期「可能取到未来数据」，07:1875-1877 官方警告，rev1 误抄
> 07:1910 示例）+P2-P5 笔误+P6 模板冗余。本版定向小修（~15 行，不动骨架）。
> 版本链：4bc7195（M1）→481af82（rev1）→本版 rev2。基线 git diff 可审。

## 🔹 M1 Plan-Mode 执行记录（rev1）

① 阶段：架构定稿修订（②审「修订后通过」回执后）；② 目标：A 类 9 项全落+基本面勘察并入；
③ 分工：主导=本会话，复审=ZCode，终审=用户；④ 事项：快照保护二代文档+ctx7 基本面勘察 2 次+
二代文档吸收扫描+本文件修订；⑤ 待确认：rev1 复审；⑥ 风险 §6；⑦ M2 实施②审过后另出 Plan；
⑧ 验收=A 类 9 项逐项落位可勾验+证据源全切 inner-api 行级引用；回退=git checkout 4bc7195。
⑧·判型声明（补依据）：**新增检测型**——既有产物/行为零漂移以回归+六策略 PTrade 产物
byte-diff 逐字节不变实证（§7 硬门）；新增能力面（qmt 渲染+QMT 白名单检测）对新目标报告
verdict。修复前置三问：①影响其他功能=无（加法式分支，PTrade 既有行为零改变）；②影响性能=
无（新增分支不在既有路径上，既有 render/publish 路径零额外开销）；③影响精度=无（本地引擎
与 PTrade 产物零触碰）。

---

## 1. R2 勘察结论（rev1：三处勘误+基本面新增）

### 1.1 生命周期与下单（勘误后，依据源=inner-api 转写行级引用）

| # | 项 | 结论（勘误后） | 依据 |
|---|---|---|---|
| ① | 盘前钩子等价 | **主路径定谳：handlebar 内时点判断**（日周期=每根日 bar 前段执行盘前逻辑段）。run_time 兜底通道**文档已明**：3 参签名 `run_time(funcName, period, startTime)`，且「**模型回测时无效**」——回测先行（四问①）下 run_time 仅作实盘增强路径（M5+）。run_daily 调度器（3/6 策略消费）→ `_qs_should_run_daily` 门控 wrapper（注册于 init 的时点判定） | 06-系统函数.md:225-229（3 参签名）；06:257（回测无效明文——R2① 提前闭合）；01:333+06:160-223（run_daily 映射先例，schedule_run 族节首） |
| ② | 持仓查询 | `get_trade_detail_data(accountID, 'STOCK', 'POSITION')`；m_nVolume/m_nCanUseVolume 对齐本地 amount/enable_amount 面；'ACCOUNT'/'ORDER'/'DEAL' 同源 | 08-交易函数.md:583-601（枚举）+8:645-649（m_nVolume/m_nCanUseVolume 字段行 648） |
| ③ | include=False 等价 | E1 上移渲染层不变：注入 `_qs_get_history`→`C.get_market_data_ex`+**剔除当日 bar**（count 多取一根取 `[-2]`，对齐本地 `count>=2 取[-2]` 惯例，本地锚定 ptrade_api.py:1479-1480）。**统一用 _ex 版**（官方对非 ex 版标「不推荐」：00:114-115/07:1641）；「最后一根是否含当前未完成 bar」M5 实测钉死 | 00:114；07:1641；本地 ptrade_api 实测 |
| ④ | ETF/股票池 | **`C.get_stock_list_in_sector(sector_name)`**（innerApi 版，归属勘误：去 xtdata 前缀——xtdata 属 nativeApi 不在转写册）；ETF 池=转换期固化 ETF_POOL_STATIC（双端铁律）；`get_Ashares`(5/6 策略)→`C.get_stock_list_in_sector('沪深A股')` | 07:3288-3326；01:328（A股先例） |

**下单（勘误+覆盖倒挂修复）**：passorder 以 **08:75-90 参数表为准**（第 5 位 prType/第 6 位 price/第 7 位 volume——M0 速写位序作废）；**23=买入/24=卖出分支**（05:42-44），order(n) 正买负卖按符号分支。gbk-only 决策获转写实测支持（全册 106 处 gbk/0 处 utf-8）。

### 1.2 after_trading_end 映射（②审实质缺陷修复）

弃 `is_last_bar()`（语义=全部 K 线最后一根/实盘当前未完成 bar，05:260-262/06:106——日周期回测中仅末日 True，与本地每日一次 backtest_engine.py:615-618 不等价）。**重写**：日周期=每根日 bar 尾段（时间无关）；分钟周期=15:00 bar（时间比较）尾段。同构 wrapper：`_qs_should_run_after`（时点判定，单测锁定触发次数=交易日数）。

### 1.3 QMT 基本面数据面（裁定二 A 勘察闭合；rev2：PIT 语义勘误）

| 面 | API | 说明 |
|---|---|---|
| 下载补充 | `xtdata.download_financial_data(stock_list, table_list)` / `download_financial_data2(..., start_time, end_time, callback)`（ctx7 nativeApi/xtdata.html，不在转写册——与 07:1837「使用前需补充本地数据」两源一致） | 同步执行；**部署前提**：QMT 客户端「数据管理-财务数据」补充（M5 用户域操作清单项） |
| innerApi 读取 | `C.get_financial_data(fieldList, stockList, startDate, endDate, report_type)`（转写册 07-行情函数.md:1839-2010；另有 get_raw_financial_data 07:1980 起） | **PIT 安全=announce_time（rev2 勘误）**：按财报实际公告日期返回，「不会取到未来数据」，**且即 API 默认值**（07:1867/1852）；report_time 按报告期、不考虑公告日期，**「可能取到未来数据」**（07:1875-1877 官方警告）——PIT 取数禁用 |
| 数据表 | ASHAREBALANCESHEET（资产负债）/ASHAREINCOME（利润）/ASHARECASHFLOW（现金流）/CAPITALSTRUCTURE（股本结构）/PERSHAREINDEX（财务指标）；字段载体 `表.英文字段`（07:1863） | 表名大小写不敏感；单位元或 %（公告/报告期字段为毫秒时间戳） |

**结论（rev2）**：fundamentals 缺口**可入方案**（非降级豁免）——`get_fundamentals(query, date)` →
注入 `_qs_get_fundamentals` wrapper：字段映射表（本地 query 字段→ASHARE 表英文字段）+
**report_type='announce_time'（PIT 对齐点，显式钉死不依赖默认值）**+本地 PIT 断言保留
（对齐本地 date 锚定语义 ptrade_api.py:979-982）。M2 立项 FR-QMT-01，字段映射表按六策略
实际消费字段盘点后定稿。

## 2. 模块设计（rev1：模板命名/写盘点/source 路径规格化）

### 2.1 新增/扩展面

| 模块 | 动作（rev1 修订处加粗） | 接口 |
|---|---|---|
| `render_qmt.py`（新） | QMT 渲染器 | `render_qmt(ir: StrategyIR) -> str`；**产物编码约定**：返回 unicode，gbk 转码在写盘点（见 orchestrator 行） |
| `templates/qmt_daily.py.j2`（新，**命名合规**；minute deny 前置故 **M2a 仅落 daily 模板**，qmt_minute.py.j2 待 minute 域立项再落——P6 消冗余） | QMT 模板（`_select_template_name` 强制 `{prefix}_{daily\|minute}.py.j2`，render.py:105-113） | **双目录同步**：包内 templates/+skills 仓库回退目录（render.py:41-46）两处齐放 |
| `render.py` | 分发表 `_PROFILE_TEMPLATE_MAP` 加 qmt 条目（字典驱动，代码本就预留 normalize_to_qmt，render.py:138） | 加法式；非 ptrade profile 已走 QMT 码制路径 |
| `orchestrator.py` | **gbk 写盘点在此**（现硬编码 utf-8：186-187/387）：按 profile 选编码 qmt→gbk；`--target qmt` 参数链贯穿 cli→orchestrate/orchestrate_source；209-215/407-414 api_portability 汇总+218 compare_strategy_variants 的 profile 面扩展 | 参数链+写盘编码+汇总面三处扩展 |
| `publish.py` | **修正认知**：`_atomic_publish` 为字节级 copy 无转码层（publish.py:35），59-71 硬编码双平台读双文件——**QMT 产物发布走新分支**（单目标读单文件，不触碰既有双平台逻辑） | 新分支加法式 |
| `portability_rules.py` | QMT 白名单分支（**继承 SHIM_CONTRACT_REGISTRY 式登记门禁：_QS_QMT_* 未登记即 BLOCK**）；**统一 get_market_data_ex**（非 ex 版列不推荐面）；**禁用集**：get_history_data（deprecated）+get_market_data（非 ex） | 扩展 |
| `contracts.py` | profile='qmt' 契约：生命周期映射+gbk+**E1 D-1 语义+复权口径**（§4） | 扩展 |
| `source_import.py` | **source 路径规格化（A 类 8）**：`convert_source`/`orchestrate_source` 的 target 维度（现硬编码 PTrade）；**AST 改写 QMT 变体规则面**：生命周期签名改写（initialize(ctx)→init(C)/handle_data(ctx,data)→handlebar(C)）、coding:gbk 头注入、`g.`→`C.` 属性改写、PTrade 专属归一规则适用性裁剪、`_inject_all`（4784-4918）的 _QS_QMT_* EXT 注入序 | **六策略门槛实际通道（主路径）** |
| `cli.py` | `--target qmt` 透传 | 微扩 |

**矩阵哈希边界写准（B 类 11）**：`check_fund_matrix` 只锁 `_QS_FUNDAMENTALS_EXT`/`_QS_INDUSTRY_EXT` 两常量（check_fund_matrix.py:34-38）——新增 _QS_QMT_* **不触发**矩阵追认；仅当 M2 改动这两常量时同 commit 追认。

### 2.2 双路径声明（A 类 8）

- **spec 路径**（render_qmt/IR 渲染）：M2a 首策略打通用（四象限）——验证 IR→QMT 渲染面；
- **source 路径**（orchestrate_source→convert_source，AGENTS.md 明确 qs-compile import 承接）：**六策略横验证门槛的实际通道（M3 主路径）**——source_import 的 QMT 变体规则面是 M2b 主体。

## 3. 生命周期映射定稿表（rev1 重写）

**profile 对齐基准声明（A 类 3）**：QMT 产物对齐 **daily-bar-v1+日线订阅**（handlebar 频率=订阅
K 线周期；本地 handle_data 随 engine_profile 变：daily 每日一次 backtest_engine.py:2190-2244）。
**分钟策略（minute-bar-v1）M2 不支持、portability 显式 deny**（QMT 面后续按需立项）。

| 本地语义 | QMT 渲染（rev1） | 依据/备注 |
|---|---|---|
| `initialize(ctx)` | `def init(C):`；g.*→C.* 动态属性；账号 'testS' 占位 | 06:12（仅开始运行一次）；05:132 |
| `before_trading_start` | handlebar 内时点判断段（**日周期=每根日 bar 前段**；分钟周期禁用域不适用） | §1.1① |
| `handle_data` | `def handlebar(C):`（daily-bar-v1 对齐） | §2.2 基准声明 |
| `after_trading_end` | handlebar 尾段 wrapper `_qs_should_run_after`（日=每根 bar 尾；分钟=15:00 时间比较）；**弃 is_last_bar** | §1.2 |
| `run_daily(time, func)` | init 内注册时点判定+`_qs_should_run_daily` 门控 | 01:333；06:160-223（schedule_run 族节首） |
| `order(code, n)` | `_qs_order(code, n, C)`→passorder **23/24 按符号分支**，位序依 08:75-90 | 05:42-44 |
| **`order_target_value(code, value)`（六策略 6/6 全用，实测调用点 13 处——ORDER 件真实主体）** | `_qs_order_target_value(code, value, C)`：get_trade_detail_data POSITION 持仓库存+get_market_data_ex 最新价→目标量换算→delta→passorder 下单（买 23/卖 24） | 三端旅程：本地 ptrade_api 同构（差额→引擎撮合）；QMT wrapper 吸收差额逻辑 |
| `get_history(..., include=False)` | `_qs_get_history`→C.get_market_data_ex+剔除当日 bar（count+1 取 [-2]） | §1.1③ |
| `get_positions()` | `_qs_get_positions(C)`→get_trade_detail_data POSITION 视图 | 08:583-601 |
| `get_open_orders`/`has_open_order` | get_trade_detail_data 'ORDER' 面映射（四象限试点 4 处消费） | 08 枚举 |
| `get_fundamentals(query, date)` | `_qs_get_fundamentals`→C.get_financial_data（**report_type='announce_time' PIT 钉死**）+字段映射 | §1.3（FR-QMT-01） |

## 4. 六策略 API 消费清单 → QMT 映射/禁用决策表（A 类 5，新）

实测（②审 grep 七策略）：6/6 order_target_value；5/6 get_Ashares；4/6 filter_stock_by_status；
3/6 get_fundamentals(+_batch)/run_daily/get_history_batch；get_trade_days 1 策略（vol_regime）；get_stock_info 3 策略（vol/weekly/周频）。

| API（消费面） | 决策 | QMT 实现 |
|---|---|---|
| order_target_value（6/6，13 处） | **wrapper** | §3 行（ORDER 件主体） |
| order（试点 1 处） | wrapper | §3 行 |
| get_history / get_history_batch（3/6） | wrapper | _qs_get_history（批量=循环+合并，_batch 同构） |
| get_fundamentals(+_batch)（3/6） | **wrapper（FR-QMT-01 立项）** | §1.3；字段映射表 M2 盘点定稿；**announce_time PIT** |
| run_daily（3/6） | wrapper | _qs_should_run_daily 门控 |
| filter_stock_by_status（4/6） | wrapper | 状态面由 get_instrument_detail/停牌停权数据组合（M2 勘察细节定稿） |
| get_Ashares（5/6） | wrapper | C.get_stock_list_in_sector('沪深A股')（01:328） |
| get_trade_days（1 策略：vol_regime） | wrapper | QMT 交易日历 API（M2 勘察定稿） |
| get_stock_info（3 策略） | wrapper | get_instrument_detail（07:2459） |
| get_open_orders/has_open_order（试点） | wrapper | get_trade_detail_data 'ORDER' |
| minute-bar-v1 策略（若有） | **deny** | portability 显式 BLOCK（§2.2 基准） |

## 5. 复权口径契约（A 类 7）

本地 `fq='pre'`（前复权，QFQ 基建深重）↔ QMT `get_market_data_ex(dividend_type=...)` 参数映射
**入契约硬写**：wrapper 默认 dividend_type 对齐本地前复权口径（具体枚举值 M2 依 07-行情函数
转写定稿+M5 实测验证）；复权不一致=对齐门 BLOCK 项。

## 6. 风险清单（rev1：补四项）

1. R2③「最后一根含未完成 bar」M5 实测钉死（设计假定=渲染层单点可改，策略零改动兜底）；
2. passorder 容错面对齐本地 `_QS_ORDER_SPLIT_EXT` 同构物（ptrade_api.py:3157-3216 作基准）；差集 M5 补；
3. gbk 不可编码字符 fail-closed（BLOCK 报错不静默替换；转写实测 gbk-only 支持）；
4. **撮合口径执行层语义**：QMT 侧撮合与本地引擎差异面（二代文档「三层对齐」并入 M5 对账设计）；
4b. **T+1/涨跌停规则**：QMT 侧交易规则面与本地引擎差异（M5 对账清单项）；
5. **数据订阅链**：get_market_data_ex 依赖本地已下载补充数据（QMT 客户端数据管理操作面，M5 清单）——标注待核；
6. **run_card 双产物 schema**：orchestrator 209-218 双 profile 汇总/比对面扩展时的 schema 适配。

## 7. M3 验收硬门（A 类 9）

- 「PTrade 既有行为**零改变**」（改述，替代"零触碰"）：**六策略 PTrade 产物 byte-diff 逐字节不变**
  （重转前后比对，golden/compare_roundtrip 承载）+api_portability 六策略全 PASS+既有测试套件全绿；
- QMT 产物：qmt/<strategy_id>_qmt.py（gbk）六策略全产出+portability QMT 白名单全 PASS+gbk 解码 AST 过；
- 判型判定依据=本节 byte-diff 实证（新增检测型成立凭证）。

## 8. M 系列路线（rev1 重排）

M1-rev1（本件，复审）→ M2a spec 路径首策略打通（四象限：render_qmt+qmt_daily.py.j2 双目录+
publish 新分支）→ **M2b source 路径主体**（convert_source target 维度+AST 改写规则面+_QS_QMT_*
四件 wrapper+FR-QMT-01 fundamentals 字段映射）→ M3 六策略横验证+L0 校验+**byte-diff 硬门** →
M4 文档同步（README/toolbox/prompt_engineering）→ M5 用户域实测（R2③ 钉死+撮合对账+数据补充
操作清单）→ M6 推送批（与在途批协调）。

---

**rev1 自检（A 类 9 项）**：1✓证据勘误三处+行级引用切换（§1.1）；2✓after_trading_end 重写（§1.2）；
3✓触发条件周期精确化+基准声明（§2.2/§3）；4✓order_target_value+订单状态（§3/§4）；5✓决策表（§4）；
6✓fundamentals 立项（§1.3 FR-QMT-01）；7✓get_market_data_ex 统一+复权契约（§1.1③/§5）；
8✓source 路径规格化+双路径声明（§2.1/§2.2）；9✓零改变改述+byte-diff 硬门（§7）。
B 类：10✓模板命名+双目录（§2.1）；11✓登记门禁+矩阵边界写准（§2.1）；12✓风险补四项（§6）；
13✓判型依据+性能声明（Plan ⑧·）。

**暂停语义**：rev1 呈 ZCode 复审——复审通过后 M2a 另出 Plan-Mode 实施计划呈批。
