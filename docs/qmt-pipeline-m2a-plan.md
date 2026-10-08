# QMT 管线 M2a · Plan-Mode 八项计划（2026-10-08 呈批）

> M1 四版三轮终判通过（`aadf4f7`）；总调度 M2a 开工批照准（首策略四象限 spec 路径打通）。
> 本件=六步①方案轮产出，②审过后进③实施。依据：M1-rev2 定稿（§2.1/§2.2/§3/§7）。

## 🔹 M2a Plan-Mode 八项计划

**① 当前所处精准阶段**：
M1 架构定稿终判通过（P1 PIT 勘误 announce_time+全部笔误清零）；M2a=spec 路径首策略打通轮
（六步流水线：本计划①→②审→③实施→④验收→⑤确认→随批⑥）。

**② 本轮唯一核心工作目标**：
spec 路径端到端打通：四象限 design→orchestrate→render_qmt→`qmt/四象限ETF轮动_qmt.py`
（gbk 编码）——验证 IR→QMT 渲染面+gbk 写盘链+publish 新分支。

**③ 本轮严格角色分工**：
主导=本会话（PDO 载体）；**编码实施=zcode_code 委派**（实现型编码委派纪律，③轮执行，
产物本会话核对）；②审=ZCode；终审=总调度/用户。

**④ 本轮精准工作事项清单**（scope 六模块+验收）：

| # | 模块 | 动作 | 依据 |
|---|---|---|---|
| 1 | `templates/qmt_daily.py.j2`（**双目录**：`quantstudio/strategy_compiler/templates/`+skills 仓库回退目录） | QMT 日线模板：`#coding:gbk` 头+init(C)/handlebar(C) 骨架+注入区（`_QS_QMT_*` 最小面）+静态池直灌段 | M1 §2.1（render.py:41-46 双目录/105-113 命名） |
| 2 | `render_qmt.py`（新） | `render_qmt(ir: StrategyIR) -> str`（unicode；gbk 转码在写盘点）+QMT 专属上下文装配（生命周期映射 §3 表） | M1 §2.1 |
| 3 | `render.py` | `_PROFILE_TEMPLATE_MAP` 加 qmt 条目+`render_strategy` 分发（加法式，字典驱动本就预留 normalize_to_qmt） | render.py:105-113/138 |
| 4 | `orchestrator.py` | ①gbk 写盘（186-187/387 硬编码 utf-8→按 profile 选编码，qmt→gbk）②`--target qmt` 参数链 ③api_portability 汇总面（209-215/407-414）qmt 分支 | M1 §2.1 |
| 5 | `publish.py` | QMT 发布新分支（单目标读单文件——不触碰既有双平台双文件逻辑 59-71） | M1 §2.1 |
| 6 | `portability_rules.py` | **最小 QMT 白名单**（四象限消费面：order_target_value/get_history/get_positions/静态池/print 日志+基础 Python）+禁用集（get_history_data/get_market_data 非 ex 版） | M1 §2.1（SHIM 登记门禁模式） |
| 7 | `cli.py` | `--target qmt` 透传 | 微扩 |

**实施首步勘察**（③轮第一步）：确认四象限 design 存在性（`output/generated_strategies/` 下
对应目录+`agent_strategy_design.json`；若缺→改用 IR 直构路径，计划不变仅入口适配，如实登记）。

**Out of scope（M2b）**：source 路径（convert_source target 维度+AST 改写规则面）、全量 wrapper
（FR-QMT-01 fundamentals/run_daily 门控/filter_status/Ashares/trade_days/stock_info/order 简单版）、
分钟域、compare_strategy_variants 的 QMT 面（218）。

**⑤ 本轮待交互确认事项**：本计划批复（含 scope 裁剪边界：最小白名单是否满足 M2a 或须全量——
建议最小面，M2b 扩全量）。

**⑥ 本轮潜在风险与技术卡点**：
- render.py 分发表扩展的回归面（既有 quantstudio/ptrade 两分支零改变——byte-diff 验证）；
- 模板 Jinja2 语法面与 gbk 字符冲突（模板源 UTF-8，产物转码点单点，转码失败 fail-closed BLOCK）；
- 四象限 design 可能不含 QMT 所需语义节点（IR 节点覆盖面）——实施首步勘察+如实登记。

**⑦ 本轮合规约束**：
六步流水线全走（本计划①；②审后③）；多会话共享工作区纪律（写前快照+精确 add+edit 后 diff
自检）；共享核心文件（source_import 本轮**不涉及**——矩阵哈希追认不触发；render/orchestrator/
publish/portability 涉及时遵守共享文件提交纪律）。

**⑧ 本轮验收判据+回退条件**：
- **判据**：①`qmt/四象限ETF轮动_qmt.py` 产出：gbk 解码 ✓+AST 编译过 ✓+生命周期完备（init/
  handlebar 在+`#coding:gbk` 头）✓+静态池直灌段在 ✓；②portability QMT 白名单对产物 PASS；
  ③**PTrade 既有行为零改变**：四象限+六策略 PTrade 产物重转 byte-diff 逐字节不变（M1 §7 硬门
  前移验证）+既有测试套件全绿；④api_portability 六策略回归全 PASS。
- **回退**：git 还原至写前快照（③轮实施前建）；模块级单点回退（新文件整删/render.py 分发
  条目单点摘除）。
- **失败判定**：产物 gbk 转码失败未 BLOCK（静默替换）、PTrade 产物 byte-diff 出现任何差异、
  既有测试红非登记既有红。

⑧·**判型声明**：**新增检测型**——新增能力面（qmt 渲染目标+QMT 白名单）对新目标报告 verdict；
既有产物/行为零漂移以 byte-diff+回归实证（判型依据同 M1 §7）。修复前置三问：①影响其他功能=
无（加法式分支+byte-diff 门）；②影响性能=无（新增分支不在既有路径）；③影响精度=无（本地引擎
零触碰）。

---

**暂停语义**：本计划呈批——批复后：②审（ZCode）→③实施（zcode_code 委派+本会话核对）→
④验收（判据①-④）→⑤确认→随批⑥推送。
