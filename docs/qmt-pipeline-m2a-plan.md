# QMT 管线 M2a · Plan-Mode 八项计划（rev2，2026-10-08 呈②审复核放行）

> **rev2 说明**：②审（ZCode，commit 7a98bf4 审）判定「修订后通过」：P1×2（spec 入口件缺失/
> byte-diff 载体不可执行）+P2×2（run_card schema 风险/白名单与行号勘误）+P3×3 全落本版。
> 版本链：eebebb2（M2a）→7a98bf4（rev1 换策略令）→本版 rev2。
> 关键勘误（②审抓漏）：**spec 路径入口=strategy_spec.json（orchestrator.py:92-103），非 design
> json**——全仓仅 2 策略有 spec，etf_hot_theme_rotation 无 spec，须编制（工作项 0）；
> rev1「动态池→固化链全验证」论据跨路径挪用（spec DSL universe 全静态，build_strategy_ir.py:
> 109-142；固化链=source 路径 M2b 机制）——**本版勘误：spec 路径池=静态物化（universe.codes）**。

## 🔹 M2a Plan-Mode 八项计划（rev2）

**① 当前所处精准阶段**：
M1-rev2 终判通过+M2a ②审「修订后通过」回执后修订轮；六步流水线：本计划①（rev2）→②审复核
放行→③实施（zcode_code 委派）→④验收→⑤确认→随批⑥。

**② 本轮唯一核心工作目标**：
spec 路径端到端打通：etf_hot_theme_rotation **strategy_spec.json（编制）**→orchestrate→
render_qmt→`qmt/etf_hot_theme_rotation_qmt.py`（gbk）——验证 IR→QMT 渲染面+gbk 写盘链+
publish 新分支。

**③ 本轮严格角色分工**：
主导=本会话（PDO 载体）；**编码实施=zcode_code 委派**（③轮，产物本会话核对：读文件/git diff/
跑验收命令）；spec 编制件=数据工件（人工编制+approximations/user_confirmations 声明，③轮
与框架件同批）；②审复核=ZCode；终审=总调度/用户。

**④ 本轮精准工作事项清单**（rev2：0+7 模块+基线预备）：

| # | 模块/工件 | 动作 | 依据 |
|---|---|---|---|
| **0** | **`output/generated_strategies/etf_hot_theme_rotation/strategy_spec.json`（编制件，P1-1）** | 人工编制：universe=**etf_list 静态物化池**（codes 从 strategy.py 实际池取）+signals DSL 表达力核对（amount 热度面等；表达不了处声明 approximations+user_confirmations——先例 etf_smooth_momentum_rotation spec 5 项 user_confirmed） | ②审缺陷一；build_strategy_ir.py:109-142（DSL universe 枚举全静态） |
| 1 | `templates/qmt_daily.py.j2`（双目录：包内+skills 回退） | QMT 日线模板：`#coding:gbk` 头+init(C)/handlebar(C) 骨架+注入区（`_QS_QMT_*` 实际消费面）+静态池直灌段（数据源=**spec universe.codes**） | render.py:39-46（双目录）/49-52（`_PROFILE_TEMPLATE_MAP`）/105-113（命名） |
| 2 | `render_qmt.py`（新） | `render_qmt(ir: StrategyIR) -> str`（unicode；gbk 转码在写盘点）+QMT 上下文装配（M1 §3 映射表） | M1 §2.1 |
| 3 | `render.py` | `_PROFILE_TEMPLATE_MAP` 加 qmt 条目+分发（加法式；render.py:138 非 ptrade 分支自动走 normalize_to_qmt） | ②审 P2-4 行号勘误后 |
| 4 | `orchestrator.py` | ①gbk 写盘（186-187/387 utf-8 硬编码→按 profile 选编码）②`--target qmt` 参数链 ③**仅 orchestrate（spec 路径）209-215 汇总面 qmt 分支；orchestrate_source 407-414 qmt 分支留 M2b**（P3：source 路径 QMT 转换尚不存在，现加=零功能纯回归面）④**run_card schema 策略（P2-3）**：qmt 汇总若加键→**同 commit 扩 run_card.schema.json**（profile 对象 additionalProperties:false+ptrade_profile_id 必填）；优先方案=profile 对象不动、qmt 面放顶层新键并同步扩 schema | run_card.schema.json 现场实读 |
| 5 | `publish.py` | QMT 发布新分支（单目标读单文件，不触碰双平台逻辑 59-71） | M1 §2.1 |
| 6 | `portability_rules.py` | 最小 QMT 白名单（**以 spec 产物实际注入面定稿**，必含：`_qs_order_target_value`/`_qs_get_history`（E1 剔除当日 bar）/`_qs_get_positions`/`_qs_should_run_daily` 及 spec 渲染实际引用集）+禁用集（get_history_data/get_market_data 非 ex 版） | ②审 P2-4（hot_theme strategy.py 实际消费=get_history_batch 非 get_history+run_daily+before_trading_start+filter_stock_by_status 等——**以最终 IR 注入面为准**） |
| 7 | `cli.py` | `--target qmt` 透传 | 微扩 |
| **基** | **etf_hot_theme_rotation PTrade 基线先行生成（P1-2a）** | ③实施首步：经 source 路径 `qs-compile import` 生成其 PTrade 产物并**固化基线**（现 ptrade_output: NOT_GENERATED 无从 byte-diff） | ②审缺陷三处之一 |

**Out of scope（M2b）**：source 路径转换器（convert_source target 维度+AST 改写）、orchestrate_source
汇总 qmt 分支、全量 wrapper（FR-QMT-01/run_daily 全功能门控/filter_status/Ashares/trade_days/
stock_info）、分钟域（**minute deny 机制显式化：qmt_minute.py.j2 缺席=有意 fail-closed deny，
非遗漏**——P3）、compare_strategy_variants QMT 面（218）。

**⑤ 本轮待交互确认事项**：②审复核放行；spec approximations 声明面（编制后随③呈报）。

**⑥ 本轮潜在风险与技术卡点**（rev2 补两真空）：
- render.py 分发表扩展回归面（byte-diff 验证）；
- 模板 Jinja2 语法面与 gbk 字符冲突（模板源 UTF-8；转码失败 fail-closed BLOCK+**负例单测**）；
- **spec 编制表达力风险（②审真空 2）**：hot_theme 逻辑（amount 热度面）能否被 signals DSL 完整
  表达待编制时核对——rev1「IR 节点覆盖面已勘察」**降级为：待 spec 编制后以实际 IR 勘察**；
- **run_card schema 适配（②审真空 1）**：见工作项 4④ 字段策略；
- IR→QMT 语义缺口：模板层最小吸收，不扩 scope，如实登记。

**⑦ 本轮合规约束**：
六步流水线全走；多会话共享工作区纪律（写前快照+精确 add+edit 后 diff 自检——render/orchestrator/
publish/portability 四共享文件点名）；source_import 本轮不涉及（矩阵哈希不触发）；平台代码
前置查询纪律（模板落码延续 M1 inner-api 行级标注）。

**⑧ 本轮验收判据+回退条件**（rev2：载体全部落地）：
- **判据①**：`qmt/etf_hot_theme_rotation_qmt.py` 产出：gbk 解码 ✓+AST 编译过 ✓+生命周期完备
  （init/handlebar+`#coding:gbk` 头）✓+静态池直灌段在 ✓+**E1 wrapper（_qs_get_history 剔除
  当日 bar）与复权映射（dividend_type）注入区存在性 ✓**（P3 补）+gbk fail-closed 负例单测 ✓；
- **判据②**：portability QMT 白名单对产物 PASS（白名单=实际注入面）；
- **判据③（byte-diff 硬门，载体钉死）**：**比对对象=`*_ptrade.py` 与 `*_quantstudio.py` 产物
  文件本体**（run_card.json 允许登记性差异：仅 qmt 相关新增键，字段白名单声明）；**命令清单**：
  七策略（六铁律+etf_hot_theme）逐一 `qs-compile import --out <临时目录>` → `git diff --no-index`
  （或 sha256）对基线（六策略基线=`output/ptrade_export/` 现存；hot_theme=基线预备步骤生成）；
  M1 §7「golden/compare_roundtrip 承载」表述**勘误**（compare_roundtrip=NAV/trades 等价器非
  字节对比器；逐字节 golden 在共享工作区挂起中 test_source_import.py:1011-1015——本计划
  命令清单即替代载体）；
- **判据④**：api_portability 六策略回归全 PASS（`scripts/run_contract_gate.py --strategies`）；
- **回退**：git 还原至③前写前快照；模块级单点回退（新文件整删/分发条目摘除）；
- **失败判定**：gbk 转码静默替换未 BLOCK；byte-diff 产物文件任何差异；既有测试红非登记既有红。

⑧·**判型声明**：**新增检测型**——新增能力面（qmt 渲染目标+QMT 白名单）对新目标报告 verdict；
既有产物/行为零漂移以判据③④实证。修复前置三问：①影响其他功能=无（加法式+byte-diff 门）；
②影响性能=无（新增分支不在既有路径）；③影响精度=无（本地引擎零触碰）。

---

**rev2 自检（②审修订清单）**：P1-1✓（工作项 0 spec 编制+论据勘误三处：DSL 静态/IR 勘察降级/
12→23 键不再引用）；P1-2✓（判据③载体钉死+基线预备步骤+M1 §7 表述勘误）；P2-3✓（run_card
schema 字段策略入工作项 4④）；P2-4✓（白名单以 IR 注入面定稿+行号 49-52 勘误）；P3-5✓
（orchestrate_source 留 M2b+minute deny 显式化+判据①补 E1/复权/负例）。

**暂停语义**：rev2 呈 ZCode 复核放行（②审预设「P1 修订回执复核即可」）→③实施。
