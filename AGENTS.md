# QuantStudio 项目工作规则（必须遵守）

> **本文件是常驻注入精简版**（2026-10-25 方案 B 瘦身治理：59,357 B → 32,307 B；25 条铁律语义零丢失）。
> 每条铁律 = 常驻版（触发 + 硬动作）＋ 完整版逐字归档（`knowledge/iron-rules/`，按索引表指针检索回读）。
> 档位：**A** = 逐字常驻｜**B** = 精简常驻（完整版归档）｜**C** = 摘要常驻（完整版归档）。
> 治理档案：方案 `docs/handoff/AGENTS-md-瘦身治理方案-20261025.md`｜盘点预演 `docs/handoff/AGENTS-md-瘦身-盘点预演-20261025.md`｜门禁 `scripts/check-iron-rules-budget.py`（四查，挂 `.githooks/pre-push`）。

## 铁律索引表

| # | 铁律 | 一句话触发 | 档 | 完整版 |
|---|---|---|---|---|
| 01 | 框架层改动六步流水线 | 任何框架层修复/改动 | C | knowledge/iron-rules/01-六步流水线.md |
| 02 | 性能优化不得改变引擎行为 | 性能类反馈审核/实施 | C | knowledge/iron-rules/02-性能优化纯等价.md |
| 03 | 代码层"写前快照" | 批量/破坏性 git 操作前 | C | knowledge/iron-rules/03-写前快照.md |
| 04 | 策略生成默认通过 skill | 用户给策略开发提示词 | A | knowledge/iron-rules/04-策略生成默认skill.md |
| 05 | MCP 进度报告实时更新 | MCP 替代项目推进 | C | knowledge/iron-rules/05-MCP进度报告.md |
| 06 | Rust 写前先查教程 | 写/改任何 .rs 前 | A | knowledge/iron-rules/06-Rust先查教程.md |
| 07 | 全链路修复仅限框架层 | 策略生成/转换阻断点 | C | knowledge/iron-rules/07-全链路修复限框架层.md |
| 08 | 框架问题立即解决 | 发现框架层缺陷 | B | knowledge/iron-rules/08-框架问题立即解决.md |
| 09 | 总调度常设职能与三件套 | 跨任务调度/三件套 | C | knowledge/iron-rules/09-总调度三件套.md |
| 10 | 共享核心文件提交纪律 | 改共享核心文件 | C | knowledge/iron-rules/10-共享核心文件提交.md |
| 11 | 客户仓库交付密钥对 | 客户只读权限开通/撤销 | C | knowledge/iron-rules/11-客户密钥对.md |
| 12 | 客户长期运维调度 | 客户问题反馈运维 | C | knowledge/iron-rules/12-客户长期运维.md |
| 13 | 日历双向同步机制 | 总调度↔线调度里程碑 | B | knowledge/iron-rules/13-日历双向同步.md |
| 14 | 主仓推送后同步 trading | 主仓 push 完成后 | B | knowledge/iron-rules/14-主仓同步trading.md |
| 15 | 审核回复分序 | 转发反馈给审核方 | A | knowledge/iron-rules/15-审核回复分序.md |
| 16 | 平台代码先查文档 | 平台代码复刻/转换/Rust | C | knowledge/iron-rules/16-平台代码先查文档.md |
| 17 | E1 日线取数（include=False） | 日线信号源策略 | C | knowledge/iron-rules/17-E1日线includeFalse.md |
| 18 | 修复前置纯增益审计 | 任何修复实施前 | B | knowledge/iron-rules/18-纯增益审计.md |
| 19 | daemon 生命周期占用 | 停/启/接管 daemon 前 | A | knowledge/iron-rules/19-daemon占用纪律.md |
| 20 | 三端框架层对齐 | Python 框架层优化/修复 | C | knowledge/iron-rules/20-三端框架层对齐.md |
| 21 | 方案①知识反哺留痕 | 框架层方案文档起草 | B | knowledge/iron-rules/21-知识反哺留痕.md |
| 22 | 知识反哺 R1/R4 | 实施轮开工/验收申报 | C | knowledge/iron-rules/22-知识反哺R1R4.md |
| 23 | WAL 残留处置 | 采集强杀/WAL>256MB | B | knowledge/iron-rules/23-WAL残留处置.md |
| 24 | schema 指纹：加列=契约变更 | 被指纹校验表做 DDL | B | knowledge/iron-rules/24-schema指纹加列.md |
| 25 | 上游可变因子一致性判据 | 现拉 vs 库内逐位比对 | C | knowledge/iron-rules/25-上游可变因子判据.md |

## 【铁律】框架层改动六步流水线：方案 → 审计 → 实施 → 验收 → 用户确认 → 双仓库推送

- **触发**：任何本地回测框架层修复/改动（框架 API 函数、数据适配层、生命周期函数、编译器契约、校验器、QFQ/复权管线、数据巡检等框架内核组件）。
- **摘要**：六步缺一即违规——①先书面方案（`docs/*-design.md`，含问题定义/改动范围/影响面/验收标准/回退条件，禁止未给方案先改码）→②提交独立审核（ZCode 等），未过禁实施 →③实施绝不擅自提交/推送 GitHub →④验收（测试全绿+回归/黄金对比，证据落 `docs/evidence/`）→⑤用户明确确认后 →⑥双仓推送并核对两远程一致（quantstudio-plus / quantstudio）；README 正文与 `docs/strategy_toolbox.md`、`docs/prompt_engineering.md` 等引用文档必须同步更新，禁止只改代码漏文档。
- **完整版**：knowledge/iron-rules/01-六步流水线.md

## 【铁律】性能优化不得改变回测引擎行为与逻辑

- **触发**：收到性能/内存/速度/缓存/SQL/批量取数/并发优化类反馈（CodeBuddy 等）要求审核或实施。
- **摘要**：只接受**语义完全等价**的纯性能优化——不改 API 名/签名/默认值/返回契约/列序/dtype，不改取数边界/PIT/复权口径/数据源优先级，不改生命周期调用时机，不改撮合/费用/滑点/T+1/涨跌停处理，不改信号/订单/净值等一切可观察结果；先核对真实生产调用链再定最小可逆改动；性能修复与正确性修复必须拆分禁捆绑；验收 = 修复前后黄金结果逐项一致（仅允许既有测试契约明确的浮点容差），任何可观察差异即验收失败立即回退；不得以「差异很小/结果更合理」放行。
- **完整版**：knowledge/iron-rules/02-性能优化纯等价.md

## 【铁律】代码层"写前快照"纪律（多会话共享工作区防护）

- **触发**：任何批量/破坏性 git 操作（checkout -- / reset / revert / clean / stash pop 冲突覆盖）前。
- **摘要**：①先建零副作用回退点四步：`git add -A -- <本会话涉及路径>` → `git stash create -m "baseline-<时间戳>"` → **随即 `git stash store <hash>` 持久化** → `git reset`（`create` 不接受 `-u`，未跟踪件先入暂存才被捕获；不 store 会被 gc 清掉），hash 记入会话记录；②操作前 `git status --porcelain` 核对待动文件——非本会话的 M 文件视为他人工作，禁盲回退；③每轮会话开工把 status+diff 落盘 `docs/handoff/`；④回退优先逐文件定向手段，批量 git 回退是最后手段且必须在回退点建立后；⑤引用 stash 前必 `git stash show --stat <hash>` 核对，提取一律按路径单文件 `git show <hash>:<path>`，禁整仓 pop。
- **完整版**：knowledge/iron-rules/03-写前快照.md

## 【铁律】策略生成默认通过 skill 开发，仅本地 QuantStudio

1. **默认行为**：用户给出策略开发提示词时，默认通过 `quantstudio-strategy-compiler` skill 开发，
   生成 **QuantStudio 本地零依赖策略**并落盘 `quantstudio/backtest/strategies/`。
2. **skill 默认 QuantStudio-only**（不生成 PTrade）。PTrade 转换由 PyQt 的"转 PTrade"tab
   或终端命令（`qs-compile import`）承接，不在策略开发期承担。
3. **ETF 策略默认用 `get_etf_list_local` 做 PIT 动态池**。转 PTrade 时由转换功能
   根据用户确认的回测起始日固化静态池（`ETF_POOL_STATIC`，见 07-ETF动态池固化补充规格）。
4. **用户可明确要求"跳过 skill 直接手写"**来绕过完整流程（应对简单策略）。
5. **历史教训**（2026-07-26）：旧版 skill 产出的 PTrade 代码在真实 IQEngine 报
   `NameError: name 'set_backtest' is not defined`——本地自创 API 混入 PTrade 目标。
   新版分工（skill 只产出本地代码 + 转换管线承接 PTrade）从源头消除该问题；
   旧产出物 `ptrade/tech_etf_mvo_rotation_ptrade.py` 已被真实平台证伪，视为失效。

## 【铁律】MCP 全数据源替代任务进度报告必须实时更新

- **触发**：QuantStudio MCP 全数据源替代项目（云端数据设施/同步管线/MCP server/客户端接入等全部子阶段）推进。
- **摘要**：①唯一权威进度档案 = `D:\miniQMT策略实盘\私募工作文件\QuantStudio-MCP全数据源替代任务文件\实时进度报告.md`；②每阶段 **ZCode 审核通过后**立即更新：移入已完成+证据（SHA/行数/测试结果）、刷新待需完成、新债入待办、更新总览对照表、变更记录追加一行；③禁跳审更新、禁只改代码不改报告、禁模糊表述（「基本完成」）、禁删未解决历史债；④宣布完成前逐项对照该报告闭环核查，未闭环项不得宣布完成。
- **完整版**：knowledge/iron-rules/05-MCP进度报告.md

## 【铁律】Rust 开发：写 .rs 前必须检索 rust教程 校准语法

1. **强制前置动作**：编写/修改任何 `.rs` 文件之前，必须先检索
   `D:\miniQMT策略实盘\rust教程\` 目录中的对应编号文件（如 `02.Rust rustup.md`、
   `24.Rust Result 类型.md`、`33.Rust match.md` 等，目前共 104 个编号教程文件），
   按主题（变量/类型/函数/控制流/错误处理/生命周期等）找到对应编号文件校准语法后再动手。
2. **适用范围**：所有 Rust 代码开发任务，无论代码落在哪个工作区/仓库。
3. **核对内容**：语法写法、API 用法、惯用模式（Idiomatic Rust）均以教程文件为准；
   不确定的语法不得凭印象编写，先检索确认。
4. 本铁律长期有效，除非用户明确要求修改或撤销。

> 用户原话要求：rust开发铁律——写 .rs 前必须检索 D:\miniQMT策略实盘\rust教程\ 对应编号文件校准语法。

## 【铁律】策略生成与转换全链路修复：仅限框架层·纯增益·不遗留策略层

- **触发**：「策略生成 skill → 本地回测策略 → 转换管线 → PTrade/QMT 策略」全链路上的任何阻断点/问题修复。
- **摘要**：修复落点必须全部在通用框架层（引擎/注入 API/数据访问层/数据源管线/skill/转换管线/校验器/保真配置）；**具体策略源码（含 skill 产物与转换产物）零改动**，修复经重转/重新生成生效；通用手段 = 注入/包装 API、数据层容错、模板与门控、保真开关（默认关）、审计行/诊断日志、参数链；修复须通用（多策略覆盖）+ 鲁棒 + 纯增益（不引入新问题、不修一送一）；验收门槛 = 6 策略（CANSLIM / fall_reversal / tech_etf_mvo_rotation / vol_regime_mom_rev / weekly_smallcap_growth / 周频小市值成长动量三层止损）重转 api_portability 全 PASS + 相关测试全绿 + 既有功能回归全绿；与六步流水线协同，例外须显式立项经用户批准。
- **完整版**：knowledge/iron-rules/07-全链路修复限框架层.md

## 【铁律】框架问题立即解决：禁止"登记挂账"替代修复

- **触发**：发现任何框架层缺陷（引擎/注入 API/数据适配/转换管线/校验器/QFQ 管线等，适用范围同六步流水线）。
- **硬动作**：①登记（issue_registry/进度报告/TODO）只是追溯记录，**绝不构成已处置**——禁止挂账搁置；②发现即定位根因：能复现先复现，**根因未证实不得动手**，严禁按猜测的「框架债」方向提前改码或登记错误根因；③修复当轮进入六步流水线推进，「需要走流程」≠「可以挂账」；④唯一例外 = 外部依赖或用户明确裁定暂缓：标注 `BLOCKED(外部依赖/用户裁定)` + 解除条件，依赖解除立即恢复；⑤关单只认「修复 + 回归证据」，不允许以「已登记」关单。
- **完整版**：knowledge/iron-rules/08-框架问题立即解决.md

## 【铁律】跨任务总调度常设职能与三件套实时更新纪律（2026-08-30 修订）

- **触发**：跨任务调度治理与三件套（总日历/彻底闭环清单/技术债清单）维护。
- **摘要**：①总调度 = 主线会话（三件套唯一维护/跨线冲突终审/共享层修复排程/分割点管理/转发守护），trading 实盘线调度 = 交易线审计会话（M 阶段排程/同步门复核/交易线审计/结构化周报），执行会话单线汇报（执行→线调度→总调度，禁双头禁越级）；②共享层只修于主仓（六步流水线），副本共享层零本地改动（AGENTS.md 跟随制），增量层（trading-runtime）只存副本不回流；③不可逆动作（推送/移交/M5/M6）保留用户确认闸门，调度会话不可用须落盘交接文档；④三件套随任务完成实时更新禁陈旧；⑤依赖驱动守护联动：任务依赖前置时，前置完成时点必须设/更新守护提醒；守护提示词避开 guard 白名单关键词、跨日守护核对目标星期、到点执行前核实现场状态。
- **完整版**：knowledge/iron-rules/09-总调度三件套.md

## 【铁律】共享核心文件提交纪律（2026-09-03 追加·多会话写前快照案例固化）

- **触发**：改动 `source_import.py` / `ptrade_api.py` / `backtest_engine.py` 等共享核心文件及其 wrapper 模板常量。
- **摘要**：①提交前按 03 号铁律四步配方建回退点 + `git status --porcelain` 核对**精确文件清单**只含本会话改动 + 提交信息自查不含他人改动内容；②禁 `git add -A` / `git add .`，一律精确文件清单 add；③批提交/推送后核对双远程 HEAD 逐位一致；④检测到他人未提交改动叠加须在提交信息与设计文档显式记录；⑤共享文件每次 edit 后**即时 `git diff` 自检**（并行提交会无声覆盖，edit 成功≠改动仍在）；⑥**矩阵哈希同 commit 追认**：改 PTrade wrapper 模板串（`_QS_FUNDAMENTALS_EXT` 等）须同 commit 带 `check_fund_matrix.py --check --reverify` 的矩阵 yaml/md 哈希；QMT 侧同等适用（`source_import_qmt.py` 的 `_QMT_HASH_SCOPE` 20 常量 → `meta.qmt_wrapper_hash`，与 PTrade `wrapper_hash` 分立防串）；CI 已红则补 chore(matrix) 追认并登记案例。
- **完整版**：knowledge/iron-rules/10-共享核心文件提交.md

## 【铁律】客户仓库交付密钥对标准流程（quantstudio-plus 只读分发，2026-09-06 用户确立）

- **触发**：向客户分发 quantstudio-plus（合并后 quantstudio-trading）私有仓只读拉取权限（开通/交付/撤销）。
- **摘要**：①每客户独立 `ssh-keygen -q -t ed25519`（禁复用），私钥统一命名 `id_ed25519` 落 `私募工作文件\客户密钥对\<客户名>\`，key comment 与目录用客户名；pwsh 空口令主径 `-N '""'`（`-N ''` 会被丢参）；②公钥由用户人工加入仓库 Deploy key，**必须勾选 Read-only**；③交付私钥 + 《客户使用说明.md》（方案 A 零配置：`.ssh` 放置 + Git Bash clone，提醒显示文件扩展名防 id_ed25519.txt）；④撤销 = 删 deploy key，秒级生效；⑤红线：私钥禁入任何 git 仓库、禁聊天输出、禁交付可写 key；⑥两仓合并时须一并处置老客户远端切换（通知含换远端/pull 步骤与验证清单）与 deploy key 延续评估。
- **完整版**：knowledge/iron-rules/11-客户密钥对.md

## 【铁律】客户长期运维调度会话职责（2026-09-08 用户确立）

- **触发**：客户问题反馈经用户转发至调度会话。
- **摘要**：总调度（ZCode）兼任客户运维调度，标准六步：①归因分析（证据链纪律，先复现/取证禁未证实归因）→②任务派分（派单至少含所属会话/原因/意见/期限/完成目标含验收判据，回执确认理解无偏差）→③执行反馈审核（实测复验优先于纸面申报）→④闭环判定（任一未过不判闭环）→⑤闭环后出具「修复完成通知」完整版（原因/修复内容/客户侧更新操作/验证清单/预期行为变化）呈用户审定后下发→⑥案例归档入库；公开仓纯回测边界永久开放，VIP 仓仅对 deploy key 客户开放；涉客户侧更新的通知必须含 git pull 步骤与验证清单；推送/下发保留用户确认闸门。
- **完整版**：knowledge/iron-rules/12-客户长期运维.md

## 【铁律】总调度↔线调度日历双向同步机制（2026-09-08 用户确认）

- **触发**：总调度里程碑报备、线调度日历版本更新。
- **硬动作**：①总调度每份里程碑/事件报告末尾固定附加「日历同步段」（节点 + 状态 + 证据指针，一节点一行），用户免编辑原样转发线调度；②线调度每次日历更新附一行回执（版本号 + 变更节点数）经用户转回总调度；**回执到达即本回合终止**——回执是终止符非新事件触发器，收讫后不再产生新同步段（除非新的实质性里程碑），禁「回执的回执」死循环；③每日一次对账点（挂靠 FW6/晨检时段），双方在途清单 diff 一次，差异项互催，不新增会议负担。
- **完整版**：knowledge/iron-rules/13-日历双向同步.md

## 【铁律】主仓双端推送后同步 QuantStudio-trading 回测部分（2026-09-09 用户确立）

- **触发**：主仓每次 `git push origin` 完成且三方一致性核验通过后。
- **硬动作**：①**同一工作周期内**在 QuantStudio-trading 执行同步门：`git fetch origin && git merge origin/main` → check-drift 九项 PASS + ci-smoke 共享层回归段 PASS → 同步结果登记台账 + 日历回执；②同步门是验证步骤不是决策点——**必须同步**，仅在检测到冲突/回归时阻断并上报；③前置条件 = 副本工作树干净（他线在途改动须协调后清理）；④豁免仅当该次主仓推送未触共享层文件（quantstudio/、config/、skills/、scripts/、tests/、main_gui.py 全部无改动，如纯 docs 推送），**须在台账登记豁免依据**。
- **完整版**：knowledge/iron-rules/14-主仓同步trading.md

## 【铁律】总调度审核回复分序：裁定事项先行，指示后发（2026-09-19 用户确立）

1. 用户每轮转发各会话工作反馈交总调度（或任一审核方）审核时，若本轮存在
   **必须用户裁定的事项**，应**先且仅**呈现裁定事项（问题 + 选项 + 建议），
   **不随件给出任何会话的工作指示**。
2. 待用户作出裁定后，再依据裁定结果**按会话分块**给出各会话的
   审核总结、裁定与工作指示（与「回复按会话分块便于逐个转发」惯例协同）。
3. 若本轮无待裁定事项，则照常按会话分块直接给出审核与指示。
4. 本条跨会话、跨智能体（dsh/CodeBuddy/workbuddy/ZCode 等）、跨项目共同遵守，
   长期有效，除非用户明确要求修改或撤销。

> 用户原话要求（2026-09-19）：以后我每轮转发各会话的工作反馈让你审核，如果本轮
> 有必须我裁定的事项，请先直接只给出让我裁定的事项，不必一并给出其他各个会话的
> 工作指示；等我裁定之后，再根据我的裁定结果给出各个会话的工作指示。请将以上铁律
> 写入跨会话、跨智能体和项目的记忆。

## 【铁律】平台代码工作前置查询纪律：先查文档再动手（2026-09-19 用户确立）

- **触发**：①聚宽/PTrade/QMT 间 1:1 复刻/互转/行为比对/差异归因；②把量化策略思路写成任一平台可运行实现；③Rust 代码的检查/设计/编写/修改/优化；④本地策略→PTrade 转换管线开发修复；⑤本地回测→大 QMT 转换管线（含将来新建）。
- **摘要**：动手前必须先查文档且**产出标注文档依据**（无标注视为未查；查不到须如实声明「未取到对应文档，以下基于 X」，禁静默凭记忆伪装查证）——平台 API 查 Context7（库 ID：聚宽 `/websites/joinquant_help_api`；PTrade `/kay-ou/ptradeapi`（另有 `/websites/ptradeapi`）；QMT/XtQuant `/websites/dict_thinktrader_net`（另有 `/imlida/qmt-docs`）；Rust `/rust-lang/reference` + `/rust-lang/rust`）；Rust 场景双源核对 = 本地 `D:\miniQMT策略实盘\rust教程\`（104 编号文件，第一权威）+ Context7 在线校验；查询纪律 = 单主题查询、每问题最多 3 次、查询串禁含密钥/凭据/私有策略数据。与全局 `$DSH_HOME/AGENTS.md` 同名纪律并行（项目层更细，冲突以本文件为准）。
- **完整版**：knowledge/iron-rules/16-平台代码先查文档.md

## 【铁律】日线策略信号取数：执行日以 include=False 读取前一交易日日线（E1，2026-09-22 定稿）

- **触发**：任何以日线为信号源的策略（股票/ETF/指数择时）。
- **摘要**：①信号计算一律经通用历史 API（`get_history` / `get_price` / `get_history_batch`）以 `include=False` 读取前一交易日（D-1）日线（`fq='pre'`）——从 API 口径层杜绝未来函数，不依赖 profile/时点选择；②`include=True` 的日线取数全面禁止（校验器 `NO-LOOKAHEAD-INCLUDE` 已实现 BLOCK），不存在「当日已收盘」例外；③成交价模式由策略语义裁定，开盘执行策略必须显式 `match_price_mode='open'`（默认 close 会漂移到 D 日收盘）；④`data[code]` 当日 raw 快照仅限执行层判断（涨跌停/停牌），**禁作信号输入**；⑤专门 API（`get_index_day_bar` 等）保留已审定契约不因本条变更，但成交早于当日收盘的策略消费时必须显式取 D-1 行（`count>=2` 取 `[-2]`，或断言末行 `trade_date==D` 后弃用）。
- **完整版**：knowledge/iron-rules/17-E1日线includeFalse.md

## 【铁律】修复前置纯增益审计（三型判定，2026-09-24 用户批准落位）

- **触发**：任何修复（客户反馈修复、框架层修复、数据修正）实施前。
- **硬动作**：①前置三审计：是否影响项目其他功能 / 是否影响回测性能 / 是否影响回测精度——任一项为「影响」必须放弃原方案（或改判类型单独立项），严格评估纯增益才可执行；②修复分型判定：**纯性能型**（可观察结果不变，黄金结果逐位一致为证）/ **纯恢复型**（回到损坏前原态，diff 纯新增无行为漂移）/ **数据修正型**（结果按正确口径变化：显式声明影响面 + diff 只能归因于修正行集 + 准确度只升不降 + 出现任何其他 diff 即验收失败回退）/ **新增检测型**（verdict 可被新证据改变，如巡检规则扩充使 PASS→FAIL，属新增能力开始报告真相，方案阶段须单列不得与纯恢复型混办）。
- **完整版**：knowledge/iron-rules/18-纯增益审计.md

## 【铁律】daemon 生命周期跨会话占用纪律（2026-09-30 用户批准，Q3 并发接管案固化）

同工作区多会话对 daemon 的停机/启动/接管/残锁改名/跑 once 窗口等操作**无进程级互斥**，
会互相误杀并污染对方窗口（2026-09-30 实证：客户运维 1 的 A 受控窗被并发会话接管终止，
窗口证据整段失效，白跑 19 分钟）。

1. **声明前置**：任何「停/启/接管 daemon、改锁文件、跑 once/受控窗」操作前，
   先在会话群/信箱声明占用（含时段+操作类型+预期恢复时点）。
2. **避让不接管**：并发会话遇他线已声明的窗口期，应避让而非接管——
   发现 daemon 状态异常时先查是否有他线在跑窗口，不可直接重启。
3. **专用窗口优先**：涉及 daemon 生命周期的受控窗/端到端验证/pytest 全库跑，
   尽量集中到专用会话或已协调的窗口执行，避免多线并发操作同一 daemon。

> 用户批准（2026-09-30）：「立 daemon 生命周期跨会话占用纪律（转会话群）」。

## 【铁律】三端框架层对齐：Python 侧改动须同步 Rust 副本改写（2026-09-27 用户确立）

- **触发**：Python 主仓任何框架层优化或修复（引擎/注入 API/数据适配层/转换管线/校验器等）落地时。
- **摘要**：①须同步副本（C:\QuantStudioHybrid）Rust 改写的对应逻辑、语义与契约（API 语义/签名/契约、数据适配层、撮合口径、指标计算、信号计算、生命周期调用时机）——PTrade ↔ Python ↔ Rust 三端框架层时刻对齐，任一端语义变化其余两端下一工作周期跟进，禁长期分叉；②Rust 侧验收硬门 = **逐位一致**（g17 全精度字符串镜像 sha 比对，工具 `scripts/ab_perf_chain_runner.py` / `scripts/double_run_engine.py`，沿 U1-U5 门禁），不接受「差异很小/结果更合理」；③**激活条款（保温模式）**：同步义务自副本 Rust 段实施启动之日起生效——保温态（G0 基线同步 + 映射登记册骨架，无 Rust 改写代码）不产生同步义务；触发条件（tick 数据实际接入 / 全市场研究真被卡死 / 准实时回测需求兑现）任一兑现时，重启决定呈用户裁定后本条全面生效；开工条件 = Python 当轮闭环（六步+双推+同步门 PASS）→ 副本 fetch+merge + U1-U5 新基线 → 总调度出提示词。
- **完整版**：knowledge/iron-rules/20-三端框架层对齐.md

## 【铁律】六步流水线方案①强制知识反哺留痕（2026-10-10 总调度批准落位）

- **触发**：起草框架层改动方案文档（`docs/*-design.md`）。
- **硬动作**：①正文必须含固定字段「**知识反哺检索留痕**」：记录对**结构化条目库 `knowledge/**`（按族 + `domain` 过滤）为主、通用库 `knowleage_base` 对应域 MOC 为辅**的检索——命中条目（条目名+所属族+所属域+文件名+检索日期+被采纳要点，摘入方案依据节）**或显式声明「检索面无相关条目」**；②例外亦须留痕：纯文案/紧急热修复轮可标 `[豁免]`，禁止静默跳过；③格式可机检（grep-able）：`知识反哺：[命中] <条目名>（<域>/<文件名>，YYYY-MM-DD）` 或 `知识反哺：[无命中] <检索域清单>，YYYY-MM-DD`；④双闸拦截：缺失该字段②审计判不完整退回、⑤用户审批呈报必附；⑤落点顺序：主仓 AGENTS.md → 副本跟随 → 全局预设 Plan-Mode 模板同步。
- **完整版**：knowledge/iron-rules/21-知识反哺留痕.md

## 【铁律】知识反哺 R1/R4：开工留痕与验收沉淀（2026-10-23 ②审照准 · M2-① 笔②）

- **触发**：框架层/转换管线/知识库机制相关实施轮的开工（R1）与验收申报（R4）。
- **摘要**：①**R1 开工留痕**（本轮 Plan 或方案文档，复用既有字段不另设）：`知识反哺：[命中] <条目名>（<族>/<域>/<文件名>，YYYY-MM-DD）`（多条各占一行）/ `知识反哺：[无命中] <检索面清单>，YYYY-MM-DD` / `知识反哺：[豁免] 本轮无技术决策内容`；检索面唯一口径 = 主 `knowledge/**`（按 `kb-info.yaml` families 分族 + `domain` 过滤）+ 辅 `knowleage_base` 对应域 MOC（只读不回写）；族 schema 独立（QD 六枚举 / PD 九枚举，多落点 `+` 连接），跨族枚举不得混用；②**R4 验收沉淀**一行：`本轮沉淀：<族前缀>-YYYY-NNNN` 或 `本轮沉淀：N/A（理由：<一句话>）`——条目必须过 `kb_hook.validate_record` **按族**校验；起草 `python scripts/kb_draft.py --kb knowledge/<族> --symptom … --domain …`（机检不过不落盘）；新族须先在 `kb-info.yaml` families 段登记；N/A 不是免检：连续 3 轮 N/A 须下轮说明原因；③跨项目接入走 `kb-kit/`（install.ps1 幂等 + pre-push 钩子 WARN 起步 + 可选 CI），接入后 R1/R4 格式不变；④本条是 21 号铁律的开工面扩展 + 闭环铁律的每轮钩子，②审、⑥用户确认节点不增删。
- **完整版**：knowledge/iron-rules/22-知识反哺R1R4.md

## 【铁律】WAL 残留处置与双平台运维脚本（2026-10-19 总调度裁定）

- **触发**：采集进程被强杀/崩溃后，或 `data/quantstudio.db.wal` > 256MB。
- **硬动作**：①先关 GUI 与 daemon，再运行项目根 `check_wal.sh`（macOS/Linux）或 `check_wal.ps1`（Windows）——脚本幂等（自动备份 + 收敛 CHECKPOINT + 验证）；**早退 ≠ 已收敛**，彻底收敛加 `--force` / `-Force`；②红线：运行期间禁 CHECKPOINT；禁手工删除 `.wal`（Windows 版默认只备份 WAL，主库备份用 `-BackupDb` 显式开）；③回归门：改任一 check_wal 脚本必须 `bash scripts/check_wal_regression.sh` 全绿（合成 fixture 绝不触生产库）；④经验钉死：体积阈值不可作唯一判据（探测优先）；bash 脚本 `${VAR}` 花括号界定；duckdb 版本门须落在**放行集合**内且以**判定源为唯一实现**（当前集合 `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`，判定源 `quantstudio/pipeline/duckdb_version_policy.py`；集合外版本拒启退出码 3）；`stock_daily` 日期列自动探测。
- **完整版**：knowledge/iron-rules/23-WAL残留处置.md

## 【铁律】schema 指纹严格比对库：加列＝契约变更（2026-10-21 总调度备案）

- **触发**：对被指纹校验的表（`source_watermark` / `trade_calendar` / QFQ 15 表等）做任何 DDL（加列/删列/改类型/改 NOT NULL/改 PK）。
- **硬动作**：①该表参与 `verify_fingerprint(..., reject_extra=True)` 逐字比对时，**加一列即判 `partial_or_mixed`**，`DuckDBWriter._init_tables` 安全闸拒绝启动、采集全线停摆——加列不是元数据操作，是契约变更；②契约变更**六件套同 commit 缺一不得执行**：契约期望列集（`qfq_schema_contracts.py`）+ 版本常量（`SCHEMA_VERSION`）+ `detect_schema_status` 新状态分支（新旧两版同时放行）+ `writers.py` 安全闸放行集 + 迁移 runner（预检→迁移→复检→幂等→`--dry-run`→失败回退）+ 测试（状态矩阵/幂等/回退/双版本兼容）；③迁移硬门：脚本默认拒执行，须显式开关（如 `--allow-schema-contract-change`），脚本内注明事故依据；④窗口纪律：迁移期不仅 daemon 停，**一切会 `create_writer`/`init_schema` 的入口（`--mode once`、GUI 跑任务等）禁跑**，窗口内只允许只读探测、迁移脚本、迁移后验证。
- **完整版**：knowledge/iron-rules/24-schema指纹加列.md

## 【铁律】上游可变因子下的一致性判据：必须区分「结构化版本差」与「数据损坏」（2026-10-21 总调度备案）

- **触发**：「现拉数据 vs 库内已存数据」逐位比对的健康闸/预检（复权重锚 rebase、跨源对拍、回填校验），且数据生成链上存在可变第三方量（如复权因子 `fund_adj`/`adj_factor` 上游持续微调）。
- **摘要**：①失效机理必须识别：库内值 = 写入时上游量的产物，逐位精确判据（`|Δ| ≤ 1e-9`）在上游微调下永不可满足 → **闸门自锁**（被拦下的恰是唯一能修正它的重算操作）；②判据设计三原则：**标量一致**（同一记录各列比值必须相同，单列异常即损坏）、**连续有界**（比值有界且相邻跳变连续——上游微调是平滑小量，损坏是突变）、**证据量下限**（样本 <8 条不做结构推断，fail-closed 维持拒绝）；③分层语义：结构化版本差可放行到下一阶段但**放行 ≠ 提交**，最终正确性由后段（apply/postcheck：覆盖审计、黄金自洽、K 线关系）裁决，保留双轨回退开关（env 可还原逐位旧判据）；④每轮并列留痕放行率/仍拒绝数/业务推进量，放行率异常高（>90%）须回查比值是否掩盖真实漂移，回退条件预先写明。
- **完整版**：knowledge/iron-rules/25-上游可变因子判据.md

## 变更记录

- **2026-10-26 v2.1（门禁挂载实态补齐 · 修复「声称已挂、实态零引用」）**：v2 声明「四查挂 `.githooks/pre-push`」在 2026-10-25 入库时**仅脚本入库（4bb6aac）、钩子零引用**，声明与实态不符。本笔补齐：`.githooks/pre-push` 在 C7 闸**之前**新增铁律预算门（**内容级检查 ⇒ 对一切 remote/分支生效**，含非 main、sha 形态 refspec、多 push URL），`fail-closed`（找不到 python 或脚本即 exit 9，与既有「未找到 pwsh 即拒推」同纪律）。**自本笔起「挂 `.githooks/pre-push`」的声明为真。** 实测：`sh -n` 语法 OK；非 main 分支命中预算门且 exit 0；main 分支两闸串联（budget PASS → `[prepush-gate]` 范围列举）。当前四查：AGENTS.md 32,307 B / 链 48,811 B / 25 条×91 词命中 / 指针 25 双向闭合 / 孤儿 0。
- 2026-10-25 v2（方案 B 瘦身治理）：59,357 B → 32,307 B；25 条铁律零丢失（A×4 逐字常驻 / B×7 精简 / C×14 摘要），完整版逐字归档 `knowledge/iron-rules/`（sha 对拍，manifest 见 `docs/handoff/iron-rules-archive-manifest-20261025.json`）；门禁 `scripts/check-iron-rules-budget.py` 四查挂 `.githooks/pre-push`（**挂载实态见 v2.1**）；kb-info.yaml 登记 `kb-iron-rules`。方案：docs/handoff/AGENTS-md-瘦身治理方案-20261025.md；盘点预演：docs/handoff/AGENTS-md-瘦身-盘点预演-20261025.md。
- v1（2026-10-25 前）：全文 59,357 B，25 条铁律全文常驻；完整内容即 `knowledge/iron-rules/` 各归档文件（git 历史可 `git show a6079073:AGENTS.md` 取回）。
