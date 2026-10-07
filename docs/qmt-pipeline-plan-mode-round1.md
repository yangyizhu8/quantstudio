# QMT 转换管线 · 首轮 Plan-Mode 执行计划（2026-10-07，呈批）

> 按 PDO（项目开发与优化专属Agent）纪律出计划——**输出本计划后本轮即停，等审后实施**。
> 前置铁律已履行：QMT 平台 API 文档已查（Context7 `/websites/dict_thinktrader_net`，实测取到
> init/handlebar/ContextInfo/passorder/after_init 契约）；PTrade 管线现状已侦察。

## 🔹 本轮 Plan-Mode 八项计划

**① 当前所处精准阶段**：
QuantStudio 主仓 PTrade 转换管线在役（12 模块：source_import→IR（build_strategy_ir/ir_nodes）
→契约→render→orchestrator→cli/publish；33 个 ptrade 产物）；**大 QMT 转换管线=新建功能模块**（0→1）。
CORP-01/02 已闭环（双端对齐达标 0.013%），闭环治理 A/B/E 件已落地——无在途冲突。

**② 本轮唯一核心工作目标**：
完成大 QMT（迅投知识库内置 Python）转换管线**立项基线**：现状调研报告+需求澄清清单+
架构方案草案（呈批后进入实施）。

**③ 本轮严格角色分工**：
主导=本会话（PDO 载体，方案/调研/计划）；协同=ZCode（方案②审）；用户=需求澄清与基线审批。

**④ 本轮精准工作事项清单**：
**本轮无实施动作**（纯侦察+文档调研+计划产出——已完成：①ctx7 查 QMT 策略框架契约
（init(C)/handlebar(C)/ContextInfo/passorder(23,1101,…)/after_init/coding:gbk）②PTrade 管线
12 模块结构盘点 ③本计划撰写）。后续轮实施清单（获批后启动）：
- 需求澄清（多轮）：目标策略域/回测 or 实盘/账户模式/数据源（xtdata vs 本地库）
- 架构方案：QMT 目标 profile 契约（生命周期映射：initialize→init、handle_data→handlebar）+
  render_qmt.py+portability_rules QMT 分支+校验器扩展+产物 `qmt/<id>_qmt.py`（gbk 编码）
- oracle：QMT 产物可运行性验证方案（本机无 QMT 环境——模拟/桩/客户实测三级）

**⑤ 本轮待交互确认事项**（用户澄清清单）：
1. 管线服务对象：**实盘交易**（模型交易界面）还是**回测**（或两者）？——影响账户/数据契约设计；
2. 首批目标策略：从既有 33 个 ptrade 产物对应策略中选（如四象限）还是新策略直接双端？
3. 验证通道：本机无 QMT 环境——QMT 侧验证走「用户域实测回传日志」模式（对齐 S1 同构闭环）是否可行？
4. 与 PTrade 管线关系：共享 IR 层（一套 IR 双渲染器）还是独立管线？（建议共享 IR——架构决策待审）

**⑥ 本轮潜在风险与技术卡点**：
- QMT API 面大（passorder 参数型下单/xtdata 数据面/ContextInfo 状态面）——R2 勘察期需系统文档化；
- 本机无 QMT 运行环境=oracle 缺位——须先设计验证阶梯（静态契约校验→桩回放→用户域实测）；
- coding:gbk 与项目 UTF-8 纪律冲突——渲染器输出编码需专项处理（P 同 PTrade 管线经验）。

**⑦ 本轮合规约束与变更管控**：
框架层改动走六步流水线（方案→审计→实施→验收→确认→推送）；本计划为①方案轮前置澄清；
QMT 管线全链路修复适用「仅限框架层·纯增益」铁律+平台代码前置查文档铁律。

**⑧ 本轮验收判据+回退条件**：
本轮无实施动作→无回滚需求。后续立项验收判据（供审批参考）：
- 立项基线三件套齐（现状调研报告/需求基线文档/架构方案草案）；
- QMT profile 契约文档覆盖生命周期映射+下单+数据面（每条带 ctx7 文档依据标注）；
- 判型（后续实施轮）：**新增检测型/纯增益**（新管线零触碰既有 PTrade 管线行为——
  api_portability 六策略回归全绿为证）。

⑧·**判型声明**：本轮=纯侦察澄清（无改动）；管线整体=新增检测型（新能力，不改既有行为）。

---

## 侦察已得技术锚（证据留档）

| 项 | 结论 | 依据 |
|---|---|---|
| QMT 生命周期 | `init(C)` / `handlebar(C)` / `after_init(C)`；ContextInfo 承载状态 | ctx7 `/websites/dict_thinktrader_net` strategy/JoinQuant2QMT.html+innerApi/system_function.html |
| 下单 | `passorder(23, 1101, accountid, stock, 5, -1, 1, C)` 综合下单；quickTrade=2 立即委托 | 同上 |
| 编码 | `#coding:gbk` 惯例 | 同上（innerApi 示例实测） |
| PTrade 管线参照 | 12 模块 IR 架构（source_import 268KB/IR/render/contracts/orchestrator） | 本仓 quantstudio/strategy_compiler/ 盘点 |
| 共享 IR 可行性 | ir_nodes.py 9.2KB 中间表示+render.py 15.9KB 渲染分离——加 QMT 渲染器=架构自然延伸 | 模块结构实测 |

**暂停语义**：本计划输出后本轮终止——等待澄清回复（⑤四问）与计划批复后进入下一轮。
