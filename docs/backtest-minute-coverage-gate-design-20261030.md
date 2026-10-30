---
title: 方案 · 回测前分钟数据覆盖闸可执行化（fail-closed 可配跳过）
date: 2026-10-30
status: 六步流水线 ① 方案（待 ② 审；未过审禁实施）
decision_ref: 总调度 2026-10-30 批准立项（判据=QD-2026-0015 三型；落点=回测引擎前置检查；fail-closed 可配跳过）
knowledge_refill: [命中] QD-2026-0015（qd/D1/knowledge/qmt/QD-2026-0015.md，2026-10-30）
knowledge_refill: [命中] QD-2026-0011（qd/D1/knowledge/qmt/QD-2026-0011.md，2026-10-28）——日键须 CST 整除口径
---

# 一、问题定义（实证）

- 分钟表存在 **9 整日零覆盖 + 6 低覆盖（8%~27%）** 交易日（`minute-coverage-gap-census-20261028.md`、`main-gap-split-20261028.md`）。
- 依赖分钟数据的过滤环节在缺数日**静默失效** ⇒ 漏买：06-01/06-02 补齐前 0 覆盖且 0 下单，补齐后 buy_submitted=2/1；5 笔漏买 100% 命中缺口日。
- 缺口期结论失真：补齐后全窗共同/聚宽 **21/30 = 70.0%**（缺数据基线 19/30 = 63.3%）⇒ **数据缺口可解释上限 70%**。
- 现有回测链路**无按日覆盖闸**，缺口只能靠人工普查发现（`run_ptrade_strategy.py` 现有前置检查仅含策略文件与 DB 可用性）。

**问题陈述**：回测在分钟数据不完整的库上运行**不失败、不告警**，只在缺数日静默少买/错选，产出「看似正常」但系统性偏低的结论。

# 二、改动范围与落点（策略产物零改动）

**推荐落点**：`quantstudio/backtest/run_ptrade_strategy.py` 前置检查链 —— 在既有 `_precheck_db_available`（L268）之后、`_run_backtest_impl`（L308）之前新增 `_precheck_minute_coverage(db_path, start, end, engine_profile)`。理由：形态与 `_precheck_db_available`/`_precheck_strategy_before_stop`（L208）同族；拦截点最前（避免跑完 4 小时才发现缺数据）；**仅分钟 profile 生效，日线 profile 零行为改变**。

**备选（不取）**：`backtest_engine.py:BacktestEngine.__init__`（L320）——覆盖更广但触及全部调用方（含 GUI/CLI），影响面显著变大。

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `quantstudio/backtest/run_ptrade_strategy.py` | 新增 `_precheck_minute_coverage()` 并在 `_run_backtest_impl` 前调用 |
| 2 | `quantstudio/backtest/libs/coverage_gate.py`（新） | 闸实现（纯函数：db_path/start/end/profile ⇒ 报告+判定） |
| 3 | `scripts/check_minute_coverage.py`（新） | CLI 入口，复用同一实现（禁双份逻辑） |
| 4 | `tests/`（新） | 合成 fixture 覆盖三型 + 阈值与跳过开关 |

> 铁律 07：`output/generated_strategies/**` 与 `quantstudio/backtest/strategies/**` 一律不动，修复经回测入口生效。

# 三、判据（三型，来自 QD-2026-0015）

逐**交易日**（基准取 `trade_calendar.is_open=1`；**不用** `stock_daily` 行数当日键推断，避免复现 UTC/CST 偏移坑）：

| 型 | 判据 | 默认动作 | 可配 |
| --- | --- | --- | --- |
| **① 整日缺** | 该日 `stock_minutes` 的 `count(distinct code) = 0` | **BLOCK**（fail-closed，退出码非 0） | `QS_MINUTE_COVERAGE_STRICT=0` 降为 WARN |
| **② 低覆盖** | 分钟 distinct code ÷ 同日 `stock_daily` distinct code `< 阈值` | **WARN**（列日期与覆盖率，不阻断） | `QS_MINUTE_COVERAGE_MIN_RATIO`（默认 **0.5**；设 0 ⇒ 不报） |
| **③ 副本陈旧** | 主库该日覆盖达标、所用库不达标（**仅在可读主库时判定**） | **WARN**（提示刷新副本） | 主库不可读（daemon 独占）⇒ **跳过该型并说明**，不得误报 |

**跳过开关**：`QS_MINUTE_COVERAGE_SKIP=1` ⇒ 整闸跳过（仅排障/已知受控场景；默认 0 ⇒ fail-closed）；跳过时**必须在 stdout 与结果目录留痕**「已跳过 + 原因」，防静默绕过。

# 四、影响面与纯增益判定（铁律 18）

- **型别**：新增检测型（新增能力开始报告真相；PASS↔FAIL 可被新证据改变）。
- 不影响既有功能（仅新增前置检查；日线 profile 不触发；数据完整时零行为变化）；不影响回测精度（不改取数/撮合/信号口径）；性能影响极小（窗口级按日聚合查询，须实测并设上限）。
- **黄金结果逐位一致**为验收硬门（闸在完整数据上不产生拦截/告警）。
- **与 dev 写入链、因子链问题分案**：本闸只读分钟覆盖，不触碰写入链、不改复权因子、不参与 schema 契约，两案互不依赖、互不混办。

# 五、验收标准

| # | 项 | 标准 |
| --- | --- | --- |
| 1 | 合成 fixture | 构造三库覆盖三型 ⇒ 判定与动作逐项符合（测试入 `tests/`，禁只靠手测） |
| 2 | 阈值与开关 | `MIN_RATIO`/`STRICT`/`SKIP` 行为各自正确；跳过留痕可查 |
| 3 | 实库回归 | 对真实副本跑闸：补齐后窗口内**零整日缺** ⇒ 不阻断 |
| 4 | 黄金一致 | 既有回测黄金结果逐位一致（日线 + 分钟各一套），无任何数值漂移 |
| 5 | 性能 | 闸耗时 ≤ 拟 60s/窗口；超预算先优化再实施 |
| 6 | 回归门 | 既有测试套件全绿（禁带病入库） |
| 7 | 文档同步 | 回测入口 README、`docs/strategy_toolbox.md` 等同步说明闸与开关 |

# 六、回退条件

| 触发 | 动作 |
| --- | --- |
| 闸误报 | 立即置 `QS_MINUTE_COVERAGE_SKIP=1` 解除阻塞（留痕），再修判据 |
| 耗时超预算 | 保留 WARN 型、关闭 BLOCK 型统计开销；或改按需触发（仅出现零覆盖日时再全量统计） |
| 与他线写入/采集窗冲突 | 副本陈旧型自动跳过并说明；整日缺判定只用所用库自身 |
| 验收不过 | 整体回退：删新增文件 + 还原调用点（**不得留半挂状态**） |

# 七、未决与风险

| # | 风险 | 处置 |
| --- | --- | --- |
| 1 | `trade_calendar` 自身缺口 ⇒ 误判 | 闸内做日历一致性检查（日历开市日 vs 日线 distinct 日），不一致则 WARN 并提示 |
| 2 | 「副本陈旧」需读主库，daemon 独占时不可读 | 该型自动跳过并说明；独立 CLI 可在停服窗补跑 |
| 3 | 与 `scripts/audit_watermark_coverage.py` 职责相邻 | 实施前核其可否复用底层查询，能复用则复用，**禁两份重复实现** |
| 4 | 策略产物零改动约束 | 实施前后 diff 校验两处策略目录无变更 |

# 八、实施顺序（六步）

① 本方案（待 ② 审）→ ② 独立审核（ZCode 等），未过禁实施 → ③ 实施（绝不擅自提交/推送）→ ④ 验收（测试全绿 + 黄金逐位一致 + 证据落 `docs/evidence/`）→ ⑤ 用户确认 → ⑥ 双仓推送并核对两远程一致。