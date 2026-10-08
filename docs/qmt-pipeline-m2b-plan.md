# QMT 管线 M2b · Plan-Mode 八项计划（rev2，2026-10-08 呈②审复核放行）

> **rev2 说明**：②审（ZCode，commit 4d13281 审）判定「修订后通过」，必改 R1-R8 全落本版。
> 两裁已定：架构 **D**（独立 `source_import_qmt.py`）；FR-QMT-01 独立常量 `_QS_QMT_FUNDAMENTALS_EXT`
> （矩阵哈希不触发）。版本链：4d13281（M2b）→本版 rev2。
> **②审抓漏（会致产物运行时炸且现判据拦不住的三项）**：context/data 视图缺失（六策略 100% 用
> `context.current_dt`；S5/S6 重度用 `data[code].*`——校验器只查 `ast.Call` 节点
> `portability_rules.py:412-465` → 属**静默放行**）+ `log` shim 缺失（73 调用点，六策略全用）
> + `.SS`→`.SH` 后缀归一缺失（S1/S3/S4 用 `.SS`，QMT 侧认 `.SH`）。

## 🔹 勘察事实（rev2 勘误后，②审已复核）

| 事实 | 证据（rev2 勘误） |
|---|---|
| `convert_source()` 无 target 维度 | `source_import.py:5191-5203` |
| `SourceConverter` 1790 行（PTrade 专用+内嵌 AST 基建） | `3460-5250`；方法行号②审逐项核实命中 |
| EXT 常量：**11 个常量块承载 40+ helper 函数**（rev2 勘误，原「40+ 常量」表述不准） | 矩阵哈希域仅 `_QS_FUNDAMENTALS_EXT:1568`/`_QS_INDUSTRY_EXT:2764`（`check_fund_matrix.py:34-37`） |
| **可零提取复用面**（②审实测更强）：`security_code_rules`（独立纯函数模块，含 `normalize_to_qmt:174`）、`design_metadata`（独立模块） | 直接 import 即用 |
| ETF 池固化耦合实例状态（`_freeze_etf_pool:4634`/`_query_etf_snapshot:4709`）——**六策略 `get_etf_list_local` 消费=0**（S3 为静态硬编码池） | **M2b 缓提**（首个动态池策略 QMT 化时立项） |
| AST 工具族（`_apply_replacements:3420` 等 7 件模块级纯函数） | **复制入新模块**（不提取，避免触碰共享核心文件触发纪律负担） |
| orchestrator 行号漂移（rev2 勘误）：汇总面现状 **492-497**、`_write_qmt_product` **102-116**（原引 407-414 现为 docstring） | ②审核实 |
| 校验器拦截面：只查 `ast.Call` 节点——`data[code]` Subscript/`context.portfolio.*` 属性读/非 C 前缀属性调用**均放行** | `portability_rules.py:412-465`（②审实测） |

## 🔹 架构 D 落实（两裁之一）

- **零提取零触碰默认**：`source_import.py` 零改动（复用面=import 独立模块；AST 工具族=复制）；
- 新模块 `quantstudio/strategy_compiler/source_import_qmt.py`：自持 AST 改写链+视图/wrapper shim 注入；
- 复用面：`security_code_rules`（`.SS`→`.SH` 归一用现成 `normalize_to_qmt`）+`design_metadata`
  （run_card 同构携带——**R6 补入**）；
- FR-QMT-01 独立常量 `_QS_QMT_FUNDAMENTALS_EXT` 置新模块内（PTrade 侧 `_QS_FUNDAMENTALS_EXT` 零触碰
  →矩阵哈希不触发——两裁之二）。

## 🔹 AST 改写规则面（rev2：九项扩充，②审 R1）

**三机制钉死**：
1. **g/context/data = 别名注入式视图 shim**：模块级 `g = C` / `context = _qs_context_view(C)` / `data = _qs_data_view(C)`——**天然全覆盖 `hasattr(g,'name')` 55 处守卫**（无需 AST 节点改写）。先例：`_qs_capture_ctx` def 在 `source_import.py:983-986`（注入点 `:4968` 所引）；
2. **wrapper = 同名遮蔽 + C 捕获**（先例：`source_import.py:448-456` get_history 同名重绑定+orig 类属性捕获）；
3. **参数兼容 keyword/位置双形态**（S3/S4 用 `order_target_value(security=,value=)`，其余位置式；注入序先例依据 `source_import.py:4802-4804`）。

> **命名口径（rev2-fix 勘正，②审复核轮追认）**：模板侧与 source 侧 wrapper **均为小写 `_qs_*`**
> （两套独立实现：`qmt_daily.py.j2` 注入区 6 个 `def _qs_*`／M2b 新模块自持一套）；
> **`_QS_QMT_*` 仅限 M2b 新模块的 EXT 常量**（`_QS_QMT_FUNDAMENTALS_EXT` 等，尚不存在）——
> M1/M2a 文档中「模板侧 `_QS_QMT_*`」表述以代码为准。

**九项规则**（②审判定 2 a-i 逐项落）：

| # | 规则 | 依据/形态 |
|---|---|---|
| a | **context 视图**：`current_dt`/`portfolio.{total_value,cash,market_value,positions}`/`portfolio_value`（S3 异名）/`getattr(portfolio,'str')` 回退链（S4） | 六策略 100% 用 current_dt；`context.portfolio` 实测 6/6（复核勘正） |
| b | **data 视图**：`data[code].{close,volume,high_limit,low_limit,preclose}`（S5/S6）+data 作形参传 helper | `_qs_data_view` 实现 Subscript+属性；②审核实校验器静默放行→**须自建负例断言** |
| c | **g 机制**=别名注入（覆盖裸 `g.`+`hasattr(g,...)` 55 处守卫） | 不逐节点改写 |
| d | **`.SS`→`.SH` 归一**：常量与池（S1/S3/S4）经 `normalize_to_qmt` 改写 | `security_code_rules:174` |
| e | **编码头**：`# -*- coding: utf-8 -*-`（S1/S2 已有）→**替换**为 `#coding:gbk`；S3-S6 无声明→注入 | 非「追加」 |
| f | **`set_benchmark`(6/6)/`set_commission`(S2) 处置**：**DENY_REMOVE（剥除+审计行）**——QMT 基准/费率设置 API 未入白名单，剥除并打印 `QS_QMT_DENY_REMOVED` 审计行 | M2a 白名单注解 |
| g | **调用点机制**=同名 wrapper 遮蔽（调用点零改动） | R1 机制 2 |
| h | **生命周期缺件形态**：S3 **无 handle_data/before/after**（handlebar 主体须由 run_daily 注册的 weekly_rebalance 生成）；S2 无 before/after；S1/S4 空壳——**不得因缺件 fail-closed 误杀**（按实有回调映射） | ②审实测形态 |
| i | **时间坍缩登记**：run_daily `time='15:00'` 在 QMT 日线 handlebar 内坍缩为「每根日 bar 一次」——登记为语义差异（不改行为） | ②审判定 5 |

## 🔹 wrapper/视图清单（rev2：全量，②审 R2；每件③实施须落 QMT API 行级依据）

| 件 | 内容 | 消费面 |
|---|---|---|
| **log shim**（R2 新增） | `log.{debug,info,warning,error,critical}` → print/审计行映射（**73 调用点，六策略全用**） | 6/6 |
| `_qs_context_view(C)` | context 视图（规则 a） | 6/6 |
| `_qs_data_view(C)` | data 快照视图（规则 b） | 2/6（S5/S6） |
| `_qs_get_history` / `_qs_get_history_batch` | E1 剔当日 bar 取 `[-2]`+dividend_type；**_batch 并入**（3/6） | 3/6 |
| `_qs_order_target_value` | 持仓换算 delta→passorder 23/24 | 6/6 |
| `_qs_order` | 简单版（符号分支 23/24） | 试点 |
| `_qs_get_positions` / `get_position` | **按本地对象视图契约重设计**（dict 视图+Position 对象 `.amount/.avg_cost`；toolbox L158/214-215） | 6/6 |
| `_qs_get_fundamentals` | FR-QMT-01：**announce_time PIT**+字段映射表+ms 时间戳契约 | 3/6 |
| `_qs_filter_stock_by_status` / `get_stock_status` | **get_stock_status 3 处（S2 L71/S4 L489-490）——M1-rev2 §4 决策表漏登，本版补** | 4/6+3 处 |
| `_qs_get_ashares` | `get_stock_list_in_sector`；**get_Ashares(date) PIT 差异登记**（S2 L67 位置传参，QMT sector=当前上市名单无历史 PIT） | 5/6 |
| `_qs_get_trade_days` / `_qs_get_stock_info` / `current_price` | 交易日历/instrument_detail/现价兜底（**current_price 补登**） | 1/3/试点 |
| `_qs_should_run_daily` / `_qs_should_run_after` | run_daily→门控注册；after 尾段（**M2a 模板件 ≠ source 路径件**——命名口径 rev2 勘正：模板侧 `_QS_QMT_*`，source 侧 `_qs_*`） | 3/6 |
| **`_QMT_CONTEXT_METHODS` 登记面**（R3 新增） | QMT ContextInfo 方法白名单（C.get_market_data_ex 等）与属性面分开登记 | portability |

**FR-QMT-01 字段映射表（R4）**：三张 ASHARE 表七字段已可枚举（income/balance/cashflow 关键字段）
——**列为③首项交付物**（盘点六策略实际消费字段→映射表→announce_time 显式+毫秒时间戳契约）。

## 八项计划

**① 阶段**：M2b ②审「修订后通过」→rev2 复核放行轮；六步：本件①→②审复核→③实施→④验收→⑤确认→随批⑥。

**② 目标**：`qs-compile import <strategy.py> --target qmt` 产出 `qmt/<strategy_id>_qmt.py`（gbk），
**六策略全通**+PTrade 产物 byte-diff 逐字节零回归。

**③ 分工**：主导=本会话（PDO 载体）；编码实施=zcode_code 委派（超时则本会话核对收尾并披露）；
②审复核=ZCode；终审=总调度/用户。

**④ 事项清单**：
0. **FR-QMT-01 字段映射表盘点**（③首项交付物，R4）；
1. 架构落位：新模块 `source_import_qmt.py`（零提取零触碰+AST 工具族复制）；
2. AST 改写规则面九项（上表 a-i）+三机制钉死；
3. wrapper/视图全量注入（上表，含 log shim/context/data 视图/get_stock_status/current_price/_batch 并写）；
4. 注入序：模块头（gbk 头+import）→别名视图区（g/context/data/log）→wrapper 遮蔽区→生命周期壳；
5. `orchestrator` import 路径 target 贯通+**汇总面 492-497 qmt 分支**（rev2 勘误行号）+
   `_write_qmt_product:102-116` 复用+run_card qmt_target（source 路径同构携带 design_metadata）；
6. portability QMT 白名单全量+**`_QMT_CONTEXT_METHODS` 登记面**；
7. 复权口径定稿（dividend_type 枚举依 07 转写+M5 实测项登记）；
8. 六策略横验证（M3 前置）。

**Out of scope**：六策略回测数值对照（M3 桩回放）；用户域实测（M5）；分钟域（deny）；ETF 动态池
固化 QMT 化（六策略消费 0，缓提立项）；QMT GUI 集成。

**⑤ 待确认**：本 rev2 复核放行；（原两项架构/常量裁定已定）。

**⑥ 风险（rev2 按②审补全）**：
- 规则面 a-i 全量落位为硬前提（遗漏项会静默通过现有判据直到 QMT 运行时炸——负例断言兜底）；
- **按代码实测判 API 面，不据 docstring**（S2 docstring 声称 next_open/callback_basket 但代码未用）；
- E1 设计假定（`[-2]`）待 M5 钉死——M3 数值对照不作契约证据；
- 语义差异登记三项：run_daily 时间坍缩/get_Ashares PIT/set_benchmark·set_commission DENY 剥除；
- 共享文件纪律（D 方案下 source_import.py 零改动）；他线叠加（精确 add+基线化判据）；
- 新增面 QMT API 依据（log 映射/data 快照字段 preclose·high_limit·low_limit 等价源/get_stock_status
  组合/交易日历/dividend_type 枚举）多为 M1 未勘察域——③逐项落行级依据进证据文档。

**⑦ 合规**：六步流水线；平台代码前置查询纪律（行级依据+产物注释+证据文档）；策略源码零改动；
写前快照；共享核心文件纪律（矩阵哈希不触发已定，D 方案）。

**⑧ 验收判据（rev2 修订）**：
- **判据①（六策略产物，检查面扩全）**：gbk 解码+`ast.parse` 过+生命周期完备+**全量 wrapper/视图注入在**
  （log/context/data/g/四 M2a 件+六策略消费面全集）+**未定义名负例断言**（产物内 `context`/`g`/`data`/
  `log` 等残留裸引用扫描=0——②审实测 ast.parse+白名单均不查未定义裸名）+minute deny 负例；
- **判据②**：六策略产物过 `validate_qmt_portability` 全量白名单+PASS；
- **判据③（PTrade 零回归硬门）**：六策略 PTrade 产物**同代对照法** byte-diff 逐字节不变；
- **判据④（既有回归）**：`run_contract_gate.py --strategies` PASS+**新模块单测**（正/负例+gbk 负例+
  BLOCK 负例，R8 建议采纳）；
- **判据⑤（策略源码零改动，R5 基线化）**：以 **M2b 开工快照（六策略源文件 hash）** 为基线逐文件比对
  ——**不按 `git status` 字面**（fall_reversal 已有他线在途 M，字面即刻 FAIL）；差异须走同源终证法
  归因（HEAD 版源码×双侧框架）。
- **回退**：③前写前快照；新模块整删/orchestrator 分支摘除。
- **失败判定**：契约点缺失/白名单 FAIL/PTrade byte-diff 差异（非他线源码归因）/策略源码被编辑/
  负例断言出现残留裸引用。

⑧·**判型声明**：**新增检测型**——新增能力面（QMT source 转换路径+QMT 白名单全量+`_QMT_CONTEXT_METHODS`）
对新目标报告 verdict；既有产物零漂移以判据③④实证。修复前置三问：①影响其他功能=无（独立模块+
byte-diff 门）；②影响性能=无（新路径不在既有链路）；③影响精度=无（本地引擎/PTrade 产物零触碰）。

---

**rev2 自检（②审 R1-R8）**：R1✓（规则面九项+三机制）；R2✓（wrapper 清单全量：log shim/context/
data 视图/get_stock_status/current_price/get_position 契约重设计）；R3✓（`_QMT_CONTEXT_METHODS`+
命名口径勘正）；R4✓（映射表列③首项+announce_time+ms 契约）；R5✓（判据⑤基线化+①扩全量+负例断言+
同源终证法预置）；R6✓（design_metadata 补入+零提取默认+AST 工具族复制）；R7✓（行号勘误 492-497/
102-116+常量块表述勘误）；R8✓（语义差异三项+按代码实测规则）。

**暂停语义**：rev2 呈 ZCode 复核放行 → ③实施（zcode_code 委派）。
