# 大 QMT 转换管线 · 立项基线（M0，2026-10-07）

> 执行载体=优化后 PDO 预设（Plan-Mode/判型/证据化纪律自用）。M0=基线文档轮。
> 前轮 Plan-Mode 计划（`docs/qmt-pipeline-plan-mode-round1.md`）已批+四问代答（总调度 2026-10-07）。

## 🔹 M0 Plan-Mode 执行记录（八项）

① 阶段：立项基线轮（前轮=侦察澄清）；② 目标：基线三节成文（现状/需求/架构）呈确认节点 1；
③ 分工：主导=本会话（PDO 载体），协同=ZCode（下轮方案②审），用户=基线审批；
④ 事项：本文件撰写+IR/render 层结构侦察（已完成，只读）；
⑤ 待确认：本基线整体（确认节点 1）；⑥ 风险：见 §3.6；⑦ 合规：六步流水线，M1 实施另出计划；
⑧ 验收=三节齐+每条技术断言带依据；回退=纯文档无回滚需求。
⑧·判型：M0 纯文档（零改动）；管线整体=**新增检测型**（新能力，PTrade 管线零触碰）。

---

## 一、现状调研报告

### 1.1 PTrade 管线（参照系，12 模块）

| 模块 | 职责 | QMT 复用性 |
|---|---|---|
| `source_import.py`（268KB） | 平台注入 API 面导入+wrapper 模板 | 复用（IR 上游，平台无关） |
| `build_strategy_ir.py`+`ir_nodes.py` | **语义 IR**：IRNode 基类+9 类节点（Universe/HardFilter/DataLoad/Indicator/Factor/Signal/Ranking/Portfolio/Risk） | **直接复用**（IR 共享四问④裁定） |
| `contracts.py`+`portability_rules.py` | 契约+可移植规则（api_portability 校验） | 扩展（加 QMT 分支） |
| `render.py` | `render_strategy(ir, profile)` 分发：`render_quantstudio`/`render_ptrade`+Jinja2 模板+Golden 保护（`_assert_not_protected`） | **扩展点**（加 `render_qmt`+qmt 模板） |
| `orchestrator.py`+`cli.py`+`publish.py`+`design_metadata.py`+`reverse_spec.py` | 编排/入口/发布/元数据/逆规格 | 复用（profile 参数扩展） |

产物：33 个 ptrade 产物（`ptrade/<id>_ptrade.py`）。

### 1.2 QMT 平台契约锚（ctx7 `/websites/dict_thinktrader_net`，每条带依据）

| 面 | 契约 | 依据 |
|---|---|---|
| 生命周期 | `init(C)`（初始化一次）/`handlebar(C)`（每 bar 执行，实盘随主图 tick 更新）/`after_init(C)`（运行开始一次性代码） | strategy/JoinQuant2QMT.html+innerApi/system_function.html |
| 下单 | `passorder(23, 1101, accountid, stock, price, volume, -1, C)` 综合下单；quickTrade=2 立即委托（不待 K 线走完） | innerApi/system_function.html |
| 历史行情 | `C.get_market_data(["close"], [code], count=N)`→float/Series/DataFrame/Panel（随参数形态）；**推荐 `get_market_data_ex`**；`get_history_data` **deprecated**（需 set_universe 前置，禁用） | innerApi 示例实测 |
| 编码 | `#coding:gbk` 惯例（官方示例双形态：gbk/utf-8，以 gbk 为主流） | 同上 |
| 状态承载 | `ContextInfo`（C）——C.stockcode/C.market/C.stock/账号属性 | 同上 |

### 1.3 语义差集（PTrade→QMT 转换要吸收的差异）

| 语义 | PTrade | QMT | 处置 |
|---|---|---|---|
| 初始化 | `initialize(context)` | `init(C)` | 映射直译 |
| 主循环 | `handle_data(context, data)` | `handlebar(C)` | 映射（data 面由 C 取数替代） |
| 盘前钩子 | `before_trading_start` | **无直接对应**（候选：run_time 定时回调/handlebar 内时点判断） | **R2 勘察项①** |
| 数量下单 | `order(code, n)` | `passorder(23,1101,acct,code,price,n,-1,C)` | 参数型包装（注入层吸收） |
| 历史取数 | `get_history` 注入 API | `C.get_market_data(_ex)` | 注入层重定向 |
| 持仓查询 | `get_positions` | 待勘察（get_trade_detail_data 系候选） | **R2 勘察项②** |
| 编码 | UTF-8 | GBK（主流） | 渲染器输出编码专项 |
| 账号 | context 内 | C.accountid/模型交易界面选择 | 映射直译（回测可任意串） |

## 二、需求基线（确认节点 1，四问代答落档）

| 问 | 答案（总调度 2026-10-07，用户可覆写） | 影响 |
|---|---|---|
| ①服务形态 | **回测先行**（实盘随 M5+） | 账号契约从简（回测账号任意串）；实盘面（模型交易界面/quickTrade）后置 |
| ②首批策略 | **6 策略横验证集**（CANSLIM/fall_reversal/tech_etf_mvo_rotation/vol_regime_mom_rev/weekly_smallcap_growth/周频小市值成长动量三层止损） | 与 PTrade 验收集同源——横验证铁律 |
| ③验证通道 | **用户域实测回传日志**→总调度审核（操作文档入库模式同构） | oracle 三级阶梯（§3.5） |
| ④架构 | **IR 共享**（D0 单源三目标裁定） | render_qmt 扩展点，零独立管线 |

**验收判据（M 系列总）**：
- 判型声明：**新增检测型/纯增益**——QMT 管线全部改动不触碰 PTrade 管线行为：
  `api_portability` 六策略回归全绿+`test_odd_lot_sell` 等既有套件全绿为证；
- QMT 产物：`qmt/<strategy_id>_qmt.py`（gbk 编码），6 策略全产出；
- 静态校验：portability_rules QMT 分支（禁止 API 白名单外调用）全 PASS；
- 用户域实测：首批 ≥1 策略 QMT 客户端回测跑通+日志回传对齐（对齐 S1 同构，阈值另立）。

**边界（不变面）**：PTrade 管线 12 模块行为零改动（render 分发新增分支不算行为变化）；
策略源码零改动（重转生效）；QuantStudio 本地回测引擎零改动。

## 三、架构方案草案（M1 起细化，②审待 ZCode）

### 3.1 总体：IR 共享三渲染器

```
source_import → build_strategy_ir（语义 IR，9 类节点）
                     │
        ┌────────────┼────────────┐
   render_quantstudio  render_ptrade  render_qmt（新）
                                     └→ qmt/<id>_qmt.py（gbk, Jinja2 qmt 模板）
```

### 3.2 新增模块面（预估）

| 模块 | 动作 | 预估 |
|---|---|---|
| `render_qmt.py`（或 render.py 扩展分支+`templates/qmt_*.j2`） | IR→QMT Python 渲染（生命周期映射+gbk） | 新文件+模板 |
| `portability_rules.py` QMT 分支 | QMT API 白名单+禁用面（deprecated API 拦截） | 扩展 |
| `contracts.py` profile='qmt' | 契约注册 | 扩展 |
| `orchestrator.py`/`cli.py` profile 参数 | qmt 目标透传 | 微扩 |
| `source_import.py` QMT 注入面 | wrapper 模板（order 包装/get_history→get_market_data 重定向/持仓适配） | 注入模板新增（矩阵哈希纪律适用） |

### 3.3 生命周期映射表（渲染核心）

| IR/本地语义 | QMT 渲染 | 备注 |
|---|---|---|
| initialize | `def init(C):` | g.account 类状态→C 属性 |
| handle_data | `def handlebar(C):` | data[code] 面→C 取数注入 |
| before_trading_start | R2 勘察后定（run_time/时点判断） | 差集吸收点 |
| order(-N)/order(N) | `passorder(23,1101,C.accountid,code,price,N,-1,C)` 注入包装 | 数量语义保持（零股语义 CORP-02 经验迁移） |
| get_history(...) | `C.get_market_data([...],[code],count=N)` 注入重定向 | include=False 语义（E1 铁律）在 QMT 侧等价实现——R2 勘察项③ |

### 3.4 编码策略

渲染输出 `#coding:gbk`+文件实际 gbk 编码写盘（`publish.py` 按目标 profile 选编码）；仓内源模板保持 UTF-8（项目纪律），仅产物层转码。

### 3.5 oracle 三级验证阶梯

1. **L0 静态契约校验**（本机）：portability_rules QMT 分支+语法编译（gbk 解码后 AST 过）；
2. **L1 桩回放**（本机，M3 评估）：ContextInfo 最小桩+历史数据桩——handlebar 驱动冒烟（可行性 M1 ②审评估，不强求）；
3. **L2 用户域实测**（M5）：QMT 客户端跑 6 策略回测→日志回传→总调度审核（对齐 S1 同构闭环，QMT 侧对账工具后置立项）。

### 3.6 风险与技术卡点（诚实申报）

- **R2 勘察项**：①盘前钩子等价 ②持仓查询 API 面 ③include=False 等价语义 ④ETF 动态池（get_etf_list_local）在 QMT 的数据源——四项各一轮 ctx7+实测定；
- passorder 参数型下单的容错面（拒单/资金不足路径）远比 PTrade 复杂——注入包装层需完整吸收；
- 本机无 QMT 环境——L2 依赖用户域节奏（通道③已裁定）。

### 3.7 M 系列路线（建议，逐轮 Plan 呈批）

M0 基线（本件）→M1 架构方案定稿+②审→M2 render_qmt+契约（首批 1 策略打通）→M3 六策略横验证+静态校验→M4 文档同步（README/docs/toolbox）→M5 用户域实测回传+对齐→M6 推送批（与 8 件待推批协调）。

---

## M0 验收自检

- [x] 三节齐（现状调研含 12 模块+契约锚；需求基线四问落档+判据+边界；架构方案含映射表+oracle 阶梯+M 路线）
- [x] 每条技术断言带依据（ctx7 文档标注/仓内模块实测）
- [x] R2 勘察项四条诚实标注（不臆断）
- [x] 纯文档轮零改动（判型兑现）

**暂停语义**：基线呈批（确认节点 1）——批复后进入 M1（架构方案定稿，另出 Plan-Mode 计划）。

---

## 🔹 M 系列进展台账（截至 2026-10-08，M2b ④验收轮刷新）

| 里程碑 | 状态 | 载体 / 证据 |
|---|---|---|
| M0 立项基线 | ✓ 完成 | 本文件 |
| M1 架构定稿（四版三轮②审终判） | ✓ 完成 | `docs/qmt-pipeline-architecture-m1.md` |
| M2a spec 路径打通（首策略 etf_hot_theme_rotation，四判据全 PASS） | ✓ 完成 | `docs/qmt-pipeline-m2a-plan.md` + `docs/evidence/qmt-m2a-acceptance-20261008.md` |
| **M2b source 路径（六策略全通）** | **✓ 六步 ①②③④⑤ 全部闭合 → ⑥推送批候批** | `docs/qmt-pipeline-m2b-plan.md`（rev2）+ `docs/evidence/qmt-m2b-acceptance-20261008.md` |
| **M3 桩冒烟（方案 B，②审批准）** | **✓ 六步 ①②③④⑤ 闭合 → ⑥候下批（批次 `e2faba3`，8 件）** | `docs/qmt-pipeline-m3-plan.md` + `docs/evidence/qmt-m3-acceptance-20261009.md` |
| **M3.1 F-1 修复**（产物 `_qs_fin_to_rows` 形态探测） | **已立项（用户 ⑤ 裁②）**：方案＝证券代码模式识别 + 显式形态声明（不依赖真实形态、可先行）；**正确目标形态由 M5-1 钉死后合并验收**；走小六步（方案→②审→实施→验收） | 发现记录见 M3 证据文档 §4 |
| M4 文档同步（README / strategy_toolbox / prompt_engineering） | 待启动（M2b/M3 未触发同步义务：既有行为零改变） | — |
| M5 用户域实测（**数值对照正式落位此**） | 待启动（**作业书 = M3 证据文档 §6 十四项**；P0：M5-1 财务返回形态 / M5-2 末根 bar 语义 / M5-3 交易日历形态 / M5-4 run_daily 节拍 + O-1 归因） | — |
| M6 推送批 | 待用户批准（**本会话不推送**；M2b 批已随 18 笔上远程，**M3 批 `e2faba3` 候下批**） | — |

**M2b 新增/改动件**：`source_import_qmt.py`（新建，1937 行）、`orchestrator.py`（+target 维度与 qmt 分支）、
`portability_rules.py`（QMT 白名单 3→44 条、`_QMT_CONTEXT_METHODS` 1→5）、`cli.py`（import 加 `--target`）、
`tests/test_source_import_qmt.py`（新建，6 测试）。`source_import.py` 零改动（架构 D）。

### M2b 未闭合项（2026-10-08）

1. **minute deny 负例未实现**（M2b 判据①所列负例之一）——source 路径尚无分钟域 deny 面；六策略均日线，
   实际影响 0，防护缺失 → 建议 M3 前补（**待用户裁定**）。
2. **M5 实测项**（详见 M2b 证据文档 §9）：`get_market_data_ex` 末根 bar 语义 / ACCOUNT 资金字段 /
   单股 orderType 取值 / `get_financial_data` 返回形态与 PERSHAREINDEX 是否含 `m_anntime`·`m_timetag` /
   `get_trading_dates` 元素形态与 init 内不可用 / `get_stock_status` 枚举对齐 / 板块名「沪深A股」 / bar 节拍。
3. **ETF 动态池固化 QMT 化**——六策略消费 0，按 M2b 计划缓提。
4. **影响面图 archify 独立产物未产出**——以 mermaid 结构图随 M2b ④ 呈报，**降级登记**
   （原因：会话执行预算已耗尽于缺陷修复与五判据验证；见 M2b 证据文档 §9）。

### 方法论沉淀（M2b 固化，跨轮复用）

- **同源同名终证法**（承 M2a）：判「框架改动是否影响既有产物」时，把**同一份源**同步至双侧框架重跑，
  隔离源码变量——本轮回溯至「他线在途 M + HEAD 推进」场景。
- **禁门不放宽（二次实证）**：白名单 / 编码门拦截均以**改设计或限定范围**解决，不为通过而放宽
  （承 M2a BOM 案；本轮两次触发：产物内循环变量调用、非 gbk 字符写盘）。
- **委派方无自测能力时的收尾纪律**：ZCode 侧 Python 执行权限缺失 → 全部机器验证由 DSH 侧承担（合 C4 审计禁区）；
  产物缺陷定位精确后按「局部阻断豁免」代修，**超出笔误范围者逐项登记**（M2b 证据文档 §7.2 共 7 项）。
- **共享工作区隔离**：实施期间他线推进 HEAD（c27d21f→68743cb，4 提交）——本会话改动全程未提交、零卷入，
  以 git status 集合差机械核对（不以委派方自述为准）。
