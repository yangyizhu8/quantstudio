# QuantStudio

统一数据管线 + PTrade 兼容量化回测框架。

## 快速开始（客户上手）

```bash
git clone <你的仓库地址>
cd QuantStudio
pip install -e ".[all]"
cp config/secrets.env.example config/secrets.env   # 编辑填入你的 TUSHARE_TOKEN / QMT_PATH
# 数据库约 12GB，不随 git 分发：按 data/README.md 解压到 data/quantstudio.db
python main_gui.py                                   # 启动 PyQt 控制台（GUI）
# 或常驻采集：python -m quantstudio.pipeline.daemon --mode forever
```

要点：
- 依赖通过 pip 安装（纯 Python + 预编译 wheel，无需本地编译）。
- `secrets.env` 在程序启动时**自动加载**（详见下方「凭证配置」），无需手动 `source` / `export`。
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
- 策略文件**零 import**：引擎加载时自动注入全部 API（`get_history` / `get_fundamentals` / `order_*` 等 50+ 函数）、MyTT 指标库、`shared_ashare_rules` A股规则，以及 `g` / `log` / `pandas` / `numpy`。
- `log` 对象兼容 **printf 风格多参**：`log.info("信号=%s 条数=%d", src, n)` 按 `%` 风格格式化，对齐真实 Ptrade；同时兼容 f-string / 单字符串写法。
- 完整 Ptrade 生命周期：`initialize`（必需）+ `before_trading_start` / `handle_data` / `after_trading_end` / `set_backtest`（可选）。
- 数据 100% 来自 DuckDB（QuantStudio 数据管线产出），策略禁止直连数据库（强制隔离）。
- 框架取数（注入 API 与底层数据适配层）默认前复权（`fq='pre'`）：策略不传 `fq` 即获得前复权价；需不复权请显式 `fq=None`。
- 读取**外部文件数据**（研报 CSV、信号表等）必须经由框架注入 API，例如 `load_research_signals(csv_path, fallback=...)`，文件 I/O 逻辑下沉到框架侧 `ptrade_api`；策略内直接 `open()` / `read_csv()` 会被 `StrategyIsolationGuard` 静态拦截并抛 `StrategyIsolationError`。

> **让 AI 帮你写策略**：想把策略需求交给其他智能体、自动产出可运行策略并落入 GUI 可选目录？参见 **[`docs/prompt_engineering.md`](docs/prompt_engineering.md)**。Agent-first 流程会在 R0 首先要求明确选择“双端（QuantStudio + PTrade）”或“仅 QuantStudio 本地”；双端 ETF 策略固化用户确认的静态白名单，本地单端 ETF 策略才允许动态调用 `get_etf_list_local()`。默认输出分别为 `quantstudio/backtest/strategies/<strategy_id>_quantstudio.py` 与（仅双端）`ptrade/<strategy_id>_ptrade.py`。
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
