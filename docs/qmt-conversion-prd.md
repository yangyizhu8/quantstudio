# PRD：本地回测策略 → 大 QMT（迅投内置 Python）转换管线

> 状态：**方案稿 v0.1（待审计）** —— 六步流水线第①步产物；未经 ZCode 审计与用户确认不得实施。
> 参照系：既有 PTrade 转换管线（quantstudio/strategy_compiler/source_import.py + portability_rules.py + validate_ptrade_portability + check_fund_matrix 契约矩阵）。
> 文档依据：本仓 `docs/qmt/inner-api/`（迅投知识库 innerApi 全量转写，2026-10-06，可见文本行 100% 覆盖验收）+ Context7 `/websites/dict_thinktrader_net` 双源核验。
> 方法论：按用户指定框架展开 —— ①把需求说清楚（你要的/验收标准/边界/计划模式）②五步法（说清楚→拆小步→自动化→回头看→沉淀进化）③迭代环（查看结果→反馈偏差→修订方法→沉淀规则）。

---

## 1. 摘要

在既有「本地回测策略 → PTrade」转换管线旁，新建平行管线「本地回测策略 → 大 QMT 内置 Python」，复用同一套方法学（AST 定位 + 行级改写 + 规则单一来源 + 注入 wrapper + 校验器 + 契约矩阵 CI 门禁 + 保真开关默认关），实现**双端完全对齐**：转换产物在本地 QMT 语义模拟器上跑出的信号/订单/持仓/净值与本地回测引擎逐位一致。

## 2. 01 你要的（需求定义）

### 2.1 目标（一句话）

QuantStudio 本地零依赖策略（PTrade 契约形 API）经 `qs-compile qmt`（或 GUI「转 QMT」tab）一键转换为**大 QMT 内置 Python 策略文件**，信号与调仓语义与本地回测完全对齐，产物可直接贴入 QMT 策略编辑器运行。

### 2.2 用户故事

- 作为策略作者，我在本地完成回测验收后，希望用与「转 PTrade」相同的操作得到 QMT 版策略，不手工改任何策略代码。
- 作为框架维护者，我希望 QMT 侧契约与 PTrade 侧同样被机器约束（校验器 + 矩阵哈希 + CI 门），新本地 API 未登记 QMT 映射前**fail-closed 拦截**，杜绝 NameError 类事故（历史教训 2026-07-26 set_backtest）。
- 作为实盘用户，我希望转换产物在 QMT 回测与实盘两种模式下行为一致，且 E1 无未来函数语义由框架层固化，不依赖策略自觉。

### 2.3 范围内 / 范围外（What's in / out）

| In | Out |
|---|---|
| **单源生成 + 双平台转换**：本地策略只生成一份（PTrade 契约形），PTrade/QMT 差异全部由各自转换管线与 wrapper 吸收（设计 D0/§2.1） | skill 生成两份平台专属策略（**否决**：双份=逻辑分叉怪圈，违背零改动分工前提） |
| innerApi（内置 Python）全量契约映射（25 数据结构 / 25 枚举 / 124 函数） | nativeApi / XtQuant 外接 SDK（独立课题） |
| 股票 + ETF 日线策略（现有 6 策略横验证集） | 分钟/tick 策略、期货期权、两融（后续版本） |
| 生命周期映射（initialize/handle_data/run_daily/before|after → init/handlebar/run_time） | 策略源码任何改动（零改动铁律） |
| 注入 QMT wrapper 层（行情/持仓/下单/财务/宇宙） | 真实 QMT 终端实盘联调（M5 外部验证，单独呈批） |
| 本地 QMT 语义模拟 harness + 黄金对照 | 聚宽（JoinQuant）方向（不在本项目） |
| GBK 编码产物 + 幂等重转 + 保真开关（默认关） | |

## 3. 02 验收标准（Definition of Done）

> 全部可机器判定，逐条对应 PTrade 侧既有门槛（见 docs/strategy-compiler/ptrade-profile-contract.md 与 scripts/run_contract_gate.py）。

| # | 判据 | 门槛 |
|---|---|---|
| A1 | 6 策略横验证（CANSLIM / fall_reversal / tech_etf_mvo_rotation / vol_regime_mom_rev / weekly_smallcap_growth / 周频小市值成长动量）转换 `validate_qmt_portability` | 全 PASS |
| A2 | 转换保真（口径无关，D11）：qmt_sim 驱动转换产物 vs 本地引擎运行同一策略，**在同一 match_profile 下**信号日、订单序列、期末持仓、逐日净值逐位一致（共享核算核心）；profile 取 ptrade/qmt 两档各验一遍 | 逐位一致（浮点容差仅限既有契约规定） |
| A3 | 产物编码 = GBK（`# coding:gbk` 声明 + 文件字节 GBK 可解码），语法树可解析 | 100% |
| A4 | 幂等性：二次转换零动作（INJECTED_MARKER 同款机制） | 100% |
| A5 | 契约矩阵扩展：QMT wrapper 模板哈希纳入 `check_fund_matrix` 同款 CI 门（qmt-contract-matrix.yaml/md） | CI 绿 |
| A6 | 未登记映射的本地 API 引用 | fail-closed BLOCK（QMT-UNREGISTERED-SHIM） |
| A7 | E1 语义：wrapper `_qs_get_history` 恒返回 D-1 及以前日线（include=False 等价），含实盘盘中场景测试 | 单测全绿 |
| A8 | 既有功能零衰减：全量测试套件 + run_contract_gate（PTrade 侧） | 全绿 |
| A9 | 文档同步：README + docs/strategy_toolbox.md + docs/prompt_engineering.md + 本 PRD/design 更新 | 随实施同批提交 |
| A10 | 生成期双平台能力门：skill R1/R3 产出策略 API 消耗清单，逐项对照 PTrade+QMT 双登记册；缺 QMT 映射即 WARN/BLOCK（单平台策略须用户显式放行） | 机器强制 |
| A11 | 知识库闭环：对齐差异按「修一洞·排一类·立一墙」三步落 knowledge/（registry QMT 维度 + contracts + playbook）；落档纳入六步④验收；新策略放行门禁 = 探针矩阵双平台 diff=0 | 每差异 100% |
| A12 | 撮合口径运行时开关（D10/D11，2026-10-07 用户方案采纳）：本地引擎新增 match_profile 运行级选项（'ptrade' 默认 / 'qmt'），**默认档与现状逐位一致（纯增益硬门）**；qmt 档取值 = 可配置半（转换产物附「QMT 回测配置清单」钉死，15-界面操作.md:94-102）+ 固有半（真机探针标定 MCH-QMT 条目；首轮可文档推断值上线、真机 diff 逐项修正）；开关为运行级配置，**双入口强制**（2026-10-07 用户裁定）：①终端命令模式=引擎/CLI 旗标 `--match-profile ptrade|qmt`（M4 落位）；②PyQt 策略回测界面=回测参数区「撮合口径」选择器（M6 落位）；两入口同名同默认（ptrade）、**缺一不验收**；不进策略源码；真机 QMT 回测净值 vs 本地 qmt 档净值容差对照（阈值 M5 呈裁） | 分层达标 |

## 4. 03 边界（约束与红线）

1. **策略源码零改动**（铁律）：一切修复落框架层（wrapper/转换器/校验器/模板）；修复经重转生效。
2. **不承诺适配全部券商 QMT 部署**：以迅投知识库公开契约为唯一权威（版本漂移以 Context7 + 本地文档集复核）。
3. **真机验证是独立闸门**：本地对齐（A2）≠ 真实 QMT 回测一致；真机差异（撮合/费用/数据源）单独归因台账，不混入本 PRD 验收。
4. **保真开关默认关**：任何 QMT 侧行为补丁（如持仓视图归一）默认关闭，开启须显式配置（与 PTrade 侧一致）。
5. **Rust 三端对齐铁律当前处保温态**：无 Rust 改写代码义务，但设计文档须在映射登记册登记 QMT 侧契约变化点（激活时即有据可查）。
6. **GBK 编码红线**：产物文件必须 GBK；转换器内部处理一律 UTF-8，仅输出层转码并校验（Windows 控制台不打印 emoji 约定照旧）。
7. **单源原则**（2026-10-07 用户难点1 裁定）：本地回测策略永远只生成一份；双平台差异的吸收点 = 转换器规则 + wrapper 层 + 校验器；禁止以「生成本地第二份平台专属策略」消化平台差异。
8. **知识库闭环义务**（2026-10-07 用户难点2 裁定）：QMT 对齐工作并入 knowledge/ 五设计闭环（alignment-loop-guide.md）；落档是验收的一部分，不做不算验收完成；knowledge/ 随代码同 commit 双仓推送。
9. **对齐三层语义**（2026-10-07 用户撮合质疑落档）：**L1 订单意图层**逐位硬门（A2，与撮合无关）= 转换保真；**L2 本地 QMT 语义净值**（本地引擎 qmt 撮合档核算，match_profile 开关，D11/A12）= QMT 预期净值；**L3 真机 QMT 回测净值** vs L2 容差对照 + 差异归因台账 = 平台实证闭环。本地引擎撮合=PTrade 口径（探针标定成果）这一事实不变更；**不承诺跳过 QMT 撮合标定直接达成 L3 一致**——两份策略代码同样不能（撮合是运行时属性，非代码属性）；标定投入不足时的降级选项（只交付 L1+L2，L3 如实呈报差异）呈裁。

## 5. 04 计划模式（五步法 × 里程碑）

### 5.1 五步法映射

| 步骤 | 本项目落位 |
|---|---|
| ① 说清楚 | 本 PRD + 架构设计文档（docs/qmt-conversion-design.md），ZCode 审计后冻结 v1.0 |
| ② 拆小步 | M0–M6 里程碑（下表），每步独立可验收、可回退 |
| ③ 自动化 | 校验器 + 契约矩阵哈希 + CI 契约门 + 黄金对照脚本 + 幂等重转（机器强制，非口头纪律） |
| ④ 回头看 | 每个 M 收口跑「结果 vs 判据」对账：A1–A9 逐条打勾，差异归因入台账 |
| ⑤ 沉淀进化 | GAP 登记册→修订映射规则→回写本 PRD/契约文档；每次真机偏差反哺 wrapper 规则库 |

### 5.2 里程碑（每个 M 走六步流水线子循环）

| M | 内容 | 关键交付 | 验收映射 |
|---|---|---|---|
| M0 | 契约登记册：innerApi 124 函数逐项分类（PASS映射/SHIM/REMOVE/BLOCK/GAP）+ registry QMT 维度骨架 + skill 能力门接线（R1/R3 API 消耗清单） | qmt-portability-contract.md + qmt_portability_rules.py 骨架 + knowledge/registry.md QMT 分区 | A6/A10/A11 |
| M1 | 生命周期映射 + 产物骨架生成（init/handlebar/run_time 门控模板，GBK 输出） | qmt_source_import.py v0（结构层，无行为 wrapper） | A3/A4 |
| M2 | 行情/宇宙/财务 wrapper（get_history→get_market_data_ex 等）+ E1 固化 | wrapper 层 + 单测 | A7 |
| M3 | 持仓/下单 wrapper（get_trade_detail_data + passorder）+ 订单语义对齐 | wrapper + 订单序列单测 | A2(订单段) |
| M4 | 本地引擎 match_profile 参数化（D11：默认 ptrade 档与现状逐位一致）+ qmt_sim 复用同一核算核心 + 黄金对照（A2 同口径逐位×两档）+ 探针矩阵 QMT 腿 + 撮合标定启动（配置清单先行、qmt 档首轮文档推断值上线） | backtest_engine.py profile 参数化 + **CLI 旗标 --match-profile（终端命令模式入口，A12 双入口之一）** + qmt_sim/（复用核算核心）+ 对照脚本 + knowledge/probes QMT 扩充 + 回测配置清单 | A2/A11/A12 |
| M5 | 6 策略横验证 + CI 契约门扩展（qmt 矩阵哈希）+ 新策略放行门禁前移生效 | 横验证报告 + CI 绿 + 探针矩阵双平台 diff=0 门禁 | A1/A5/A8/A11 |
| M6 | GUI「转 QMT」tab + **PyQt 策略回测界面「撮合口径」选择器（match_profile GUI 入口，A12 双入口之二）** + `qs-compile qmt` CLI + 文档同步 + 双仓推送呈批 | 全链路交付 | A9/A12 |

### 5.3 迭代环（解决一次，更要沉淀规则）

每个偏差（无论本地对照还是真机反馈）必走四拍：**查看结果**（复现取证）→ **反馈偏差**（归因到契约条目）→ **修订方法**（改 wrapper/规则/模板，框架层）→ **沉淀规则**（登记册 + 矩阵哈希 + 文档同步，禁止只修不登）。与「框架问题立即解决」铁律协同：禁止登记挂账。

## 6. 依赖与风险（Top）

| 风险 | 等级 | 处置 |
|---|---|---|
| R1 handlebar 回测按 bar 驱动 / 实盘按订阅推送，时机差异 | 高 | barpos+period 门控模板固化为唯一入口；A2 只认门控后信号 |
| R2 QMT 无交易日历 API（GAP-1） | 中 | wrapper 内以基准指数日 bar 推导交易日序列，单测锁定 |
| R3 ST/停牌过滤无直接 API（GAP-2） | 中 | 板块法（get_sector_list/get_stock_list_in_sector）+ Bar.suspendFlag 双路径，M2 定标 |
| R4 财务字段口径差异（get_financial_data vs get_fundamentals） | 中 | M0 逐字段登记 + 数值归一（参照 P-D10 先例） |
| R5 真机撮合/费用差异 | 高（外部） | M5 后单独呈批真机验证，不阻塞本地验收 |
| R6 GBK 转码丢字符 | 低 | 输出层转码 + A3 字节级校验；策略名/注释白名单字符集 |
| R7 生成期双平台门禁过严，拖慢策略开发 | 中 | WARN→BLOCK 分级；「单平台策略」显式放行通道（用户确认后仅转该平台） |
| R8 wrapper 合成面膨胀（日历/ST 等合成逻辑复杂化） | 中 | 每个合成面 = registry 条目 + 探针 + 契约测试；超阈值时呈裁拆分，禁止静默吸收 |
| R9 QMT 撮合标定投入不足，净值层（L3）长期无法收敛 | 高 | 分层交付：L1/L2 不依赖真机标定先行落地；L3 走探针标定+容差+归因台账（PTrade 同法）；降级选项（L1+L2 only）呈裁 |

## 7. 开放问题（呈审计方裁定）

1. QMT 回测专用同形函数（order_target_value 等，08-交易函数.md:1549-1583）是否在产物中使用？建议：**不使用**，统一走 passorder wrapper（回测/实盘同一实现，消除模式分叉）。
2. ETF 宇宙固化（ETF_POOL_STATIC 同款）是否复用 PTrade 侧机制？建议：复用，转 QMT 时同规则固化。
3. 产物文件命名与落盘目录（qmt/<strategy>_qmt.py？）。
4. registry.md QMT 维度 schema：共享面（POS/DAT/IDX——本地语义一份、平台锚点两个）加平台锚点列 vs QMT 专属面（LC handlebar 时机/MCH 撮合/日历合成）独立分区——建议混合式，M0 出样张呈裁。
5. QMT「平台实证黄金」获取通道：真机 QMT 回测日志导出格式与回灌节奏（对齐 PTrade 日志回灌 SOP），M5 呈批。
6. QMT 回测引擎固有撮合口径清单（成交时点/价格来源/复权与分红处理/T+1 细节）：需真机探针标定产出 MCH-QMT 条目集（M4 启动、M5 出首版）；同步确认「QMT 回测配置清单」字段与本地约定的逐项映射表（15-界面操作.md:86-102 面板项 + 下单报价类型 prType）。
