# QMT 管线 M2b · Plan-Mode 八项计划（source 路径主路径，2026-10-08 呈批）

> M2a 全闭环（四判据 PASS，`1215324`+`bbc64fa`）；总调度 M2b 照准出计划（与推送批并行，
> M2b 属①方案阶段）。本件=六步①方案轮产出，②审过后进③实施。
> 依据：M1-rev2 §2.2/§3/§4（双路径声明+映射定稿+六策略 API 消费决策表）、M2a-rev2（Out of
> scope 移交清单）。

## 🔹 M2b 勘察事实（本轮实测，②审可复核）

| 事实 | 证据 |
|---|---|
| `convert_source()` 签名**无 target 维度**（PTrade 专用） | `source_import.py:5191-5203`（strategy_id/inject_helpers/etf_pool_start_date/db_path/etf_type/active_only/exclude_bse/engine_profile） |
| `SourceConverter` 类 **1790 行**（3460-5250）=PTrade 转换器 + **内嵌 AST 基础设施** | 方法族：`_handle_lifecycle:3619`（生命周期改写）/`_scan_calls:3690`/`_normalize_call:4027`/`_normalize_ptrade_contract_calls:4087`/`_inject_fq_pre:4121`/`_rewrite_asharess_date:4159`/`_rewrite_asharess_exclude_bse:4261`/`_inject_all:4784` |
| `_QS_*` EXT/常量 **40+ 个**（矩阵哈希域仅锁 2 个：`_QS_FUNDAMENTALS_EXT:1568`/`_QS_INDUSTRY_EXT:2764`） | `check_fund_matrix.py:34-37`（M2a-rev2 §2.1 已勘误边界） |
| 六策略消费面（M1-rev2 §4 决策表） | order_target_value 6/6 · get_Ashares 5/6 · filter_stock_by_status 4/6 · get_fundamentals(+_batch) 3/6 · run_daily 3/6 · get_history_batch 3/6 · get_trade_days 1 · get_stock_info 3 |

## 🔹 架构方案（⑤待确认事项，供②审与批复选择）

| 方案 | 内容 | 优点 | 风险 |
|---|---|---|---|
| **D. 独立 QMT source 转换器（推荐）** | 新模块 `source_import_qmt.py`：自持 AST 改写链（生命周期签名/coding:gbk/`g.`→`C.`/EXT 注入），复用共享底件（`security_code_rules`/ETF 池固化逻辑按需提取共享 helper） | **PTrade 侧零改动**（source_import.py 零触碰→矩阵哈希不触发+byte-diff 天然成立）；与 M2a 独立新模块成功模式同构；边界干净 | AST 改写逻辑重写工作量；共享 helper 提取需谨慎（提取动作本身碰 source_import） |
| A. SourceConverter 加 target 维度 | 在 1790 行内分支 qmt 路径 | 复用 AST 基础设施 | PTrade 路径回归风险高（六策略 byte-diff 硬门须守住）；共享核心文件大改（多会话+他线在途叠加风险） |

**推荐 D 的核心理由**：M2a 已证明「独立新模块+加法式接入」模式能在不触碰既有路径的前提下打通
（render_qmt.py 独立+四判据零回归）；A 方案把六策略 byte-diff 硬门押在一次 1790 行文件的
内部分支改造上，风险收益不匹配。**共享 helper 提取的边界**：仅提取纯函数（无 PTrade 语义），
提取后 PTrade 侧调用点零行为变化（同代 byte-diff 验证）。

## 八项计划

**① 当前所处精准阶段**：M2a 全闭环（④验收 PASS+⑤确认+推送批候批）；M2b=source 路径方案轮
（六步①）。

**② 本轮唯一核心工作目标**：source 路径端到端——`qs-compile import <strategy.py> --target qmt`
产出 `qmt/<strategy_id>_qmt.py`（gbk），**六策略全通**+PTrade 产物 byte-diff 逐字节零回归。

**③ 本轮严格角色分工**：主导=本会话（PDO 载体）；编码实施=zcode_code 委派（③轮，M2a 先例：
超时则本会话核对收尾并如实披露）；②审=ZCode；终审=总调度/用户。

**④ 本轮精准工作事项清单**：

| # | 事项 | 内容 | 依据 |
|---|---|---|---|
| 1 | **架构落位（按批定方案）** | D：新模块 `source_import_qmt.py`；A：SourceConverter target 分支 | 上表 |
| 2 | **AST 改写规则面**（QMT 变体核心） | ①生命周期签名改写：`initialize(ctx)`→`def init(C):`、`handle_data(ctx,data)`→`def handlebar(C):`、`before_trading_start(ctx,data)`→handlebar 前段内联、`after_trading_end`→`_qs_should_run_after` 尾段、`run_daily(ctx,fn,time)`→init 内 `_qs_should_run_daily` 门控注册；②`#coding:gbk` 头注入+模块 docstring 保留；③`g.`→`C.` 属性改写（赋值/读取两侧）；④`sell_all`/`get_positions` 等 PTrade 归一规则的**适用性裁剪**（QMT 走 `_qs_*` wrapper 而非 PTrade shim） | `_handle_lifecycle:3619` 参照；M1-rev2 §3 映射表 |
| 3 | **EXT wrapper 全量注入** | `_QS_QMT_*` 全集（M2a 已建四件）+ 新增：`_qs_get_fundamentals`（**announce_time PIT**，FR-QMT-01）/`_qs_get_history_batch`/`_qs_filter_stock_by_status`/`_qs_get_ashares`/`_qs_get_trade_days`/`_qs_get_stock_info`/`_qs_order`（简单版 23/24 分支） | M1-rev2 §1.3/§4 决策表 |
| 4 | **`_inject_all` 等价注入序** | QMT 版注入器（对齐 `:4784` PTrade 侧注入序语义：模块头→helper 区→生命周期壳） | `:4784` |
| 5 | **orchestrator `orchestrate_source` qmt 分支** | import 路径 target 贯通（cli `import --target qmt`）+写盘 gbk+**汇总面 407-414 qmt 分支**（M2a 留此，现补） | orchestrator:407-414 |
| 6 | **portability QMT 白名单全量** | 六策略消费面全集入 `_QMT_API_WHITELIST`（M2a 最小面扩全）+禁用集维持 | M1-rev2 §4 |
| 7 | **复权口径定稿** | `dividend_type` 枚举值依 07-行情函数转写定稿（M2a 为占位，M2b 钉死）+M5 实测项登记 | M1-rev2 §5 |
| 8 | **六策略横验证（M3 前置）** | 六策略各产出 QMT 产物：gbk/AST/契约点/白名单 PASS | AGENTS.md 铁律 5 门槛 |

**Out of scope（留 M3/M5）**：六策略**回测数值**对照（M3：桩回放 L1）；用户域实测（M5）；
分钟域（继续 deny）；QMT GUI 集成。

**⑤ 本轮待交互确认事项**：
1. **架构方案选择（D 独立模块 vs A 加 target 维度）**——建议 D（§上表理由）；
2. FR-QMT-01（fundamentals）新建 QMT 侧独立常量（`_QS_QMT_FUNDAMENTALS_EXT` 置于新模块内）
   ——**不触碰 PTrade 侧 `_QS_FUNDAMENTALS_EXT`**，即矩阵哈希不触发（若②审认为应共享常量则
   需矩阵追认同批）。

**⑥ 本轮潜在风险与技术卡点**：
- **共享文件纪律**：若选 D，`source_import.py` 零改动（仅可能提取纯函数 helper——提取须同代
  byte-diff 验证）；若选 A 则适用共享核心文件提交纪律（写前快照+精确 add+矩阵追认判定）；
- **AST 改写保真**：六策略源码形态各异（`g.` 属性/嵌套 def/动态表达式）——逐策略产物契约点核验
  +AST 编译门；表达不了的形态 fail-closed BLOCK（不静默降级）；
- **E1 设计假定**：`_qs_get_history` 取 `[-2]` 依赖「最后一根含当前未完成 bar」假定——M5 实测
  钉死前，六策略回测数值对照（M3）不得作为契约证据（只作一致性参考）；
- **六策略语义差异**：`get_fundamentals` 3/6 策略（announce_time PIT 语义差异面）、`get_Ashares`
  北交所口径（`exclude_bse` 在 QMT 面的烘焙语义）——逐策略登记差异；
- **他线叠加**：工作区多会话在途（strategies/ 他线改动）——精确 add+提交信息自查。

**⑦ 本轮合规约束**：六步流水线全走（本件①→②审→③实施→④验收→⑤确认→随批⑥）；共享核心文件
提交纪律（矩阵哈希追认同批判定）；策略源码零改动（重转生效）；平台代码前置查询纪律（QMT API
依据=inner-api 转写册行级，产物内注释标注）；写前快照。

**⑧ 本轮验收判据+回退条件**：
- **判据①（六策略产物）**：六策略各产出 `qmt/<id>_qmt.py`：gbk 解码+`ast.parse` 过+生命周期
  完备（`#coding:gbk`/`init(C)`/`handlebar(C)`）+静态池/池固化段在+四 wrapper 注入在；
- **判据②（白名单）**：六策略产物过 `validate_qmt_portability` 全量白名单；
- **判据③（PTrade 零回归硬门）**：六策略 PTrade 产物**同代对照法** byte-diff 逐字节不变
  （M2a 已固化的方法学：HEAD worktree 双侧重转+同源同名终证法）；
- **判据④（既有回归）**：`scripts/run_contract_gate.py --strategies` PASS（矩阵哈希+套件+六策略
  api_portability 冒烟）；矩阵哈希若被触碰则同 commit 追认；
- **判据⑤（策略源码零改动）**：`git status` 六策略 `.py` 零改动（重转生效）。
- **回退**：③前写前快照；模块级单点回退（新模块整删/orchestrator 分支摘除）。
- **失败判定**：任一策略产物契约点缺失、白名单 FAIL、PTrade byte-diff 出现任何差异（非他线
  源码归因）、策略源码被编辑。

⑧·**判型声明**：**新增检测型**——新增能力面（QMT source 转换路径+QMT 白名单全量）对新目标
报告 verdict；既有产物零漂移以判据③④实证。修复前置三问：①影响其他功能=无（独立模块加法式+
byte-diff 门）；②影响性能=无（新路径不在既有链路）；③影响精度=无（本地引擎/PTrade 产物零触碰）。

---

**暂停语义**：本计划呈批（含⑤两项待确认）——批复后：②审（ZCode）→③实施（zcode_code 委派）
→④验收（判据①-⑤）→⑤确认→随批⑥。
