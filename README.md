# QuantStudio

统一数据管线 + PTrade 兼容量化回测框架。

## 快速开始（客户上手）

> 下面是一段**可直接复制给智能体的完整部署提示词**。你不需要懂编程——把它整段发给任意 AI 编程助手，它会替你从 GitHub 拉代码、装依赖、配好凭证，并启动 PyQt 回测界面，最后教你在界面上跑回测。

把下面整段（从【部署提示词开始】到【部署提示词结束】）复制，连同你的任何额外需求，一起发给智能体：

```text
【部署提示词开始】

请你帮我在本机部署 QuantStudio 量化回测框架，并启动它的 PyQt 图形界面，让我能在界面上跑策略回测。我是计算机小白，请严格按以下顺序执行，并在每一步用大白话向我说明你在做什么、是否需要我提供信息：

1. 获取代码：从 GitHub 仓库 https://github.com/yangyizhu8/quantstudio 克隆到本机一个好找的目录（例如 D:/QuantStudio），然后进入该目录。
2. 安装依赖：在该目录下执行 `pip install -e ".[all]"` 安装全部依赖（纯 Python + 预编译包，无需本地编译）。如果遇到报错，用通俗语言告诉我原因和解决办法，不要跳过。
3. 配置凭证：
   - 先询问我是否有 Tushare Pro 的 token。如果我有，请让我提供，然后做三件事：把 `TUSHARE_TOKEN=我的token` 写入 `config/secrets.env`（若文件不存在，先 `cp config/secrets.env.example config/secrets.env` 创建）；把同样的 `TUSHARE_TOKEN` 写入 `config/secrets.env.example`；并把 `TUSHARE_TOKEN` 加入我的系统环境变量。如果我【没有】Tushare token，这一步直接跳过，不要报错或中断。
   - 再询问我 miniQMT 客户端的本地目录路径（例如 `D:/国金QMT/userdata_mini`）。如果有，把 `QMT_PATH=该路径` 同样写入 `config/secrets.env` 与 `config/secrets.env.example`，并加入系统环境变量；如果我没有或暂时不需要，也跳过。
   - 说明：以上配置无需我手动 `source` 或 `export`，程序启动时会自动读取 `config/secrets.env`。
4. 准备数据库：明确提醒我把回测数据库文件 `quantstudio.db`（约 12GB，不随 git 分发，需我自行获取）放进项目的 `data/` 目录。等我确认文件已经放好后，才进行下一步。
5. 启动界面：运行 `python main_gui.py` 启动 PyQt 控制台。启动成功后，告诉我在界面里如何操作：进入「策略回测」模块 → 在策略文件栏选中一个策略 → 设置起止日期和初始资金 → 点击「运行回测」，即可可视化查看收益曲线、持仓和成交记录。

注意：整个过程你只负责把环境搭好并启动界面，**不要替我跑回测**；回测由我在界面上自己点。如果某一步卡住（例如缺数据库文件、网络失败、依赖装不上），请用通俗语言告诉我原因和下一步该怎么办。

【部署提示词结束】
```

要点（给智能体参考）：
- 依赖通过 pip 安装（纯 Python + 预编译 wheel，无需本地编译）。
- `secrets.env` 在程序启动时**自动加载**，无需手动 `source` / `export`。
- 数据库需单独获取并放到 `data/quantstudio.db`，否则回测/采集无数据可读（详见 `data/README.md`）。

## 架构保证

- **强制统一入库链**：`adapter -> aligner -> validator -> writer -> watermark -> quality audit`，任何数据源和运行模式都不能绕过。
- **三种运行方式，同一实现**：全量、单次增量、常驻增量只改变日期范围，清洗、质量门禁和幂等写入完全相同。
- **配置驱动标准化**：代码、时间、字段、单位、频率、复权、PIT 和派生字段在数据层统一；策略不识别数据源。
- **策略完全解耦**：策略只写 PTrade 生命周期并调用注入的数据、指标和交易 API；provider/adapter/DuckDB 均位于策略边界以下。
- **底层集中修复**：数据或平台语义问题修复在 adapter/aligner/provider/PtradeAPI/engine，不要求修改具体策略。
- **PTrade 保真验证**：内置 PTrade 导出导入器和 L1-L4 fidelity comparator。

详细合规结果见 `docs/architecture-compliance-audit-20260720.md`。

## 安装

```bash
pip install -e ".[all]"
```

## 凭证配置（config/secrets.env）

程序在启动时（包导入 / 各入口）会**自动加载** `config/secrets.env` 到进程环境变量，无需手动 `source` 或 `export`。

1. 复制模板并填入真实凭证（文件不提交 git，含明文）：
   ```bash
   cp config/secrets.env.example config/secrets.env
   ```
   - `TUSHARE_TOKEN`：Tushare Pro 付费 token（采集 / 回测数据源）
   - `JQ_TOKEN`：聚宽 token（可选）
   - `QMT_PATH`：miniQMT 客户端目录（如实盘 / xtquant 数据源需要，如 `D:/国金QMT/userdata_mini`）
   - `CUSTOM_API_KEY` / `ALERT_WEBHOOK`：自定义 API / 告警 webhook（可选）

2. `${ENV_VAR}` 占位符：`config/sources_config.json` 中可写 `${TUSHARE_TOKEN}`、`${QMT_PATH}` 等，
   运行时自动展开为对应环境变量的值；也可直接填实际值（如 QMT 直接填路径）。

3. 优先级：**进程已有环境变量 > secrets.env**。若你已 `export` 同名变量，或在测试中以
   `monkeypatch` 注入，则文件中的值不会覆盖它们。

## 数据采集

```bash
# 全量（使用任务 start_date/end_date）
python -m quantstudio.pipeline.daemon --mode once --pull-mode full_range --task kline_1d

# 增量（水位下一日 -> 当前日期）
python -m quantstudio.pipeline.daemon --mode once --pull-mode incremental --task kline_1d

# 常驻增量（每天按 daemon_schedule 执行）
python -m quantstudio.pipeline.daemon --mode forever
```

GUI 中的“全量拉取”“增量拉取”“进程常驻增量拉取”调用相同公共入口。

## 策略回测

```python
from quantstudio.backtest.strategy_runner import StrategyRunner

runner = StrategyRunner()
engine, payload = runner.run(
    "my_ptrade_strategy.py",
    "2026-01-01",
    "2026-06-30",
    match_price_mode="close",
)
result, output_dir = payload
```

策略文件无需导入数据库或 provider，只实现 `initialize`、`before_trading_start`、`handle_data` 和可选 `after_trading_end`。

## 策略工具箱（PTrade 兼容 API + QuantStudio 本地扩展）

写策略 / 移植 PTrade 策略时，可直接使用的**全部生命周期回调、注入式 API 函数、MyTT 指标库与 A股交易规则**，详见 **[`docs/strategy_toolbox.md`](docs/strategy_toolbox.md)**。其中 `get_etf_list()` 保持 PTrade 同名契约且禁止用于回测动态池；本地单端策略可使用 `get_etf_list_local(query_date=None, etf_type="equity", active_only=True)`，该接口经 ReferenceDataProvider → DuckDB 数据适配层按 `etf_basic` + `etf_daily` 做 PIT 查询。

要点：
- 运行时依赖分层：QuantStudio 本地会注入 API、`g`/`log` 以及 `np`/`pd`；真实 PTrade 不注入 `numpy`/`pandas` 别名，因此双端/PTrade 源码使用它们时必须显式写 `import numpy as np` / `import pandas as pd`。数据库驱动、框架内部模块和直接文件 I/O 仍被禁止。
- `log` 对象兼容 **printf 风格多参**：`log.info("信号=%s 条数=%d", src, n)` 按 `%` 风格格式化；双端/PTrade 可移植代码仅使用 `debug/info/warning/error/critical`，`log.warn(...)` 会被 Validator 阻断。
- 生命周期分层：PTrade 可移植回调为 `initialize`（必需）+ `before_trading_start` / `handle_data` / `after_trading_end`（可选）；`set_backtest()` 与 `is_trade()` 仅属 QuantStudio 本地扩展，双端/PTrade 代码会被 Validator 阻断。
- PTrade Profile 1.7.0 对股票双端常用 API 登记精确签名：`set_benchmark`、`run_daily`、`get_Ashares`、`get_index_stocks`、`get_stock_status`、`get_positions`、`get_position`、`get_trade_days`、`get_fundamentals`。双端设计或源码调用未登记的顶层注入 API 时默认 `BLOCK`，不得用“近似确认”绕过。
- `get_stock_status` 的可移植 `query_type` 仅为 `ST` / `HALT` / `DELISTING`；`DELISTING_SORTING` 仅是 `filter_stock_by_status` 的过滤类型及本地向后兼容别名。
- 数据 100% 来自 DuckDB（QuantStudio 数据管线产出），策略禁止直连数据库（强制隔离）。
- 框架取数（注入 API 与底层数据适配层）默认前复权（`fq='pre'`）：策略不传 `fq` 即获得前复权价；需不复权请显式 `fq=None`。
- **撮合/估值链路与取数链路前复权闭环**：引擎每日全市场快照（`query_daily_snapshot`，成交价、持仓估值、`data[code].price`、涨跌停比较价的唯一来源）OHLC 统一映射前复权列（`*_front`，缺失回退原始价），`preClose` 按 `close_front/close` 同因子缩放。信号价、成交价、持仓估值同一连续口径，ETF 份额拆分/股票分红除权日不再产生原始价缺口导致的虚假盈亏（分红等价于自动再投资，前复权回测标准口径）。`pctChg`/`volume`/`amount` 保持原始口径。
- **Agent-first 唯一价格契约**：`market_data_contract.signal_price_adjustment="pre"` 且 `execution_price_basis="pre_adjusted_price"`。`raw_trade_price` 已从新设计 Schema 中移除；旧 raw 设计必须回到 R2 迁移并重新通过 R2.5/R4/R5。
- 读取**外部文件数据**（研报 CSV、信号表等）必须经由框架注入 API，例如 `load_research_signals(csv_path, fallback=...)`，文件 I/O 逻辑下沉到框架侧 `ptrade_api`；策略内直接 `open()` / `read_csv()` 会被 `StrategyIsolationGuard` 静态拦截并抛 `StrategyIsolationError`。

> **让 AI 帮你写策略**：想把策略需求交给其他智能体、自动产出可运行策略并落入 GUI 可选目录？参见 **[`docs/prompt_engineering.md`](docs/prompt_engineering.md)**。Agent-first 流程会在 R0 分别确认目标平台，以及R5由Agent执行还是由用户在PyQt执行；双端 ETF 策略固化用户确认的静态白名单，本地单端 ETF 策略才允许动态调用 `get_etf_list_local()`。用户PyQt模式在R4后只生成 `quantstudio/backtest/strategies/<strategy_id>__candidate_quantstudio.py`，用户自行选择回测日期并提交日志；哈希绑定的R5证据PASS后，R6生成正式文件并删除临时候选文件。默认正式输出分别为 `quantstudio/backtest/strategies/<strategy_id>_quantstudio.py` 与（仅双端）`ptrade/<strategy_id>_ptrade.py`。
>
> ⚠️ **AI 生成策略同样受 `StrategyIsolationGuard` 约束**：自动生成的策略代码**不得包含 `open()` / `read_csv()` 等直接文件 I/O**，也不得 import 框架内部模块；需要外部数据（如研报/信号 CSV）时，必须调用框架注入的 `load_research_signals` 等 API，否则加载即报 `StrategyIsolationError`。提示词中应明确该约束。

## 质量与对齐

```bash
python -m quantstudio.pipeline.quality_audit
python -m pytest -q
```

PTrade 对照：

```bash
python -m quantstudio.backtest.run_ptrade_strategy strategy.py 2026-01-01 2026-06-30 \
  --match-price close --compare --ptrade-dir <Ptrade导出目录> --output output/compare.json
```

---

## Strategy Compiler 0.3.0-mvp — 策略编译器

> **⚠ 完善中（请勿使用）**：该模块仍在开发中，**暂时不要用于策略生成**，短期内会开放。待完善完成后会移除本提示。

将自然语言策略想法编译成经验证的双平台（QuantStudio + PTrade）策略包。

### 三层关系

| 层 | 角色 | 谁用 |
|---|---|---|
| **Skill** | AI 工作流：理解自然语言 → Spec → 用户确认 → 编排验证 + 交付 | 普通用户（通过 AI） |
| **orchestrator** | 验证引擎：Spec → IR → 双 Renderer → 7 validators → run_card | Skill 自动调用 |
| **qs-compile** | CLI 交付：Spec → IR → dual Renderer → strategy package | Skill 自动调用；高级用户直接用 |

### 推荐安装

```bash
# 独立虚拟环境
python -m venv qs-env
qs-env\Scripts\activate
pip install quantstudio-0.3.0+mvp-py3-none-any.whl jinja2 jsonschema pyyaml packaging

# 验证
qs-compile --help

# 安装 Skill
python skills/quantstudio-strategy-compiler/scripts/install_skill.py --agent zcode
```

### 普通用户使用方式

向 AI 说："帮我生成一个双均线策略。" AI 自动完成：
1. 理解策略 → 2. 能力检查 → 3. 生成 Spec → 4. 展示确认 → 5. 验证 → 6. 交付包

**普通用户不需要手动调用 qs-compile。** Skill 自动串联 orchestrator + qs-compile。

### 高级用户 CLI

```bash
qs-compile package spec.json --out output/packages [--g2-frozen-dir <dir>]
```

### 输出目录

```
output/strategy_deliveries/<strategy_id>/
├── validation/      ← orchestrator 验证产物 (run_card, capability_report, ...)
├── package/         ← qs-compile 策略包 (manifest, dual .py, spec/IR)
└── DELIVERY_REPORT.md  ← 统一交付摘要
```

### 验证报告

- `run_card.json`：总验收卡（stage/status/各验证结果）
- `capability_report.json`：数据/执行能力就绪状态
- `variant_consistency_report.json`：QS vs PTrade 14 维一致性
- `manifest.json`：artifact SHA-256 digests（可审计）

### data_digest blocked 说明

`data_digest_status=blocked`：真实市场数据 digest **deferred**（后置，不伪造）。本 MVP
使用 Hermetic/Synthetic 场景验证 pipeline 正确性，**不等于**真实数据 Fidelity 已验证。
真实 Fidelity/Reference 验证、live QMT、resident daemon 均不在本次 MVP 范围。

### 常见错误

| 场景 | exit code | 说明 |
|---|---|---|
| success | 0 | 包生成成功 |
| invalid/missing spec | 2 | Spec 不存在或 JSON 无效 |
| Golden Protection | 3 | 策略 ID 在保护名单中 |
| G2 frozen incomplete | 4 | G2 frozen artifact 缺失 |

### 不在本 MVP 范围

真实市场数据接入、live QMT、resident daemon、PR7 自动 Fidelity、GUI 集成、部署产品化。

### 更多文档

- 用户指南：`docs/strategy-compiler/USER_GUIDE.md`
- Skill 操作手册：`skills/quantstudio-strategy-compiler/SKILL.md`
- 发布说明：`quantstudio/strategy_compiler/release/RELEASE_NOTES.md`
- 项目状态：`docs/strategy-compiler/implementation-status.md`

### ETF basic reference collection

`etf_basic` is now a first-class pipeline task. Its authority and only configured source are Tushare (`fund_basic(market="E")`). Full, incremental, and resident modes all use the same path: fetch snapshot -> canonical baseline standardization -> validation -> changed-row DuckDB upsert. Tushare `YYYYMMDD` list/delist dates are converted to Asia/Shanghai midnight milliseconds, `.SH` is normalized to `SS`, and fields with unrelated units (such as `issue_amount` and `p_value`) are excluded. Missing list/delist dates may be filled from the first/last `etf_daily` bar. Restart an already-running resident collector after changing this task configuration。

## 框架层变更审阅记录（perf/datadict-day-index）

本次 `quantstudio/backtest/ptrade_api.py`、`quantstudio/backtest/backtest_engine.py` 新增 DataDict/BacktestEngine 当日 DataFrame 的 `{raw_code: first_iloc}` 实例代码索引，将 `df['code'] == bare` 的 O(N) 布尔过滤替换为 O(1) 索引查找；`None`（无法构建）时严格回退原布尔过滤。

**AGENTS.md 框架铁律适用**：本变更为纯性能优化，已审阅确认未改变任何公共/注入 API 的函数名、签名、默认值、返回类型、返回字段、列顺序、索引、dtype、空值行为、异常行为或兼容行为；未改变行情取数范围、复权口径、生命周期调用时机、撮合/费用/持仓/现金/涨跌停处理、策略信号或回测指标；`data[code]`（DataDict）、`get_current_price`、`is_halted`、`pct_chg` 等接口语义与返回结构不变。因此本文档相关表述不受影响，无需修改。
