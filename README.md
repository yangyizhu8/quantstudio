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

指数日线（F5）：`index_daily` 任务的正式动态宇宙（`get_index_daily_universe`）统一覆盖普通指数与 31 个 SW2021 L1 申万行业指数，full/incremental/resident 同一路径——tushare 普通指数走 `index_daily` 接口、申万指数走 `sw_daily` 正式接口，同一 canonical schema（股/元）；`industry_classification` / `industry_membership` 任务维护正式 SW2021 行业分类与 PIT 成员历史（tushare `index_classify`/`index_member`），旧 `sw_industry` 仅为审计快照。契约详见 `docs/data-pipeline-contract.md`。

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
- PTrade Profile 1.9.0 对股票双端常用 API 登记精确签名：`set_benchmark`、`run_daily`、`get_Ashares`、`get_index_stocks`、`get_stock_status`、`get_positions`、`get_position`、`get_trade_days`、`get_fundamentals`、`get_industry`。双端设计或源码调用未登记的顶层注入 API 时默认 `BLOCK`，不得用"近似确认"绕过。
- PTrade Profile 1.8.0 登记 `get_history(..., is_dict=True)` 返回形状契约：mapping item 可能是 pandas DataFrame / NumPy structured array / recarray。提取字段必须先 `np.asarray(...)` 归一化再参与数值计算；对 history item 的无保护 `.values`/`.iloc`/`.to_numpy()` 等 pandas 专属访问会被 Validator 阻断，并须通过 agent-first 运行时形状 fixture（`validate_runtime_shapes.py`）。
- `get_stock_status` 的可移植 `query_type` 仅为 `ST` / `HALT` / `DELISTING`；`DELISTING_SORTING` 仅是 `filter_stock_by_status` 的过滤类型及本地向后兼容别名。
- 数据 100% 来自 DuckDB（QuantStudio 数据管线产出），策略禁止直连数据库（强制隔离）。
- 框架取数（注入 API 与底层数据适配层）默认前复权（`fq='pre'`）：策略不传 `fq` 即获得前复权价；需不复权请显式 `fq=None`。
- **撮合/估值链路与取数链路前复权闭环**：引擎每日全市场快照（`query_daily_snapshot`，成交价、持仓估值、`data[code].price`、涨跌停比较价的唯一来源）OHLC 统一映射前复权列（`*_front`，缺失回退原始价），`preClose` 按 `close_front/close` 同因子缩放。信号价、成交价、持仓估值同一连续口径，ETF 份额拆分/股票分红除权日不再产生原始价缺口导致的虚假盈亏（分红等价于自动再投资，前复权回测标准口径）。`pctChg`/`volume`/`amount` 保持原始口径。
- **成交额列双端契约（B1，返回端逆映射）**：DB 物理列为 `amount`，Ptrade 官方契约列名为 `money`。`get_history`/`get_price` 返回的 DataFrame 在含 `amount` 列时**同步追加同值 `money` 列**（追加在列尾，`amount` 保留、数值不变）；请求 `fields=['money']` 时也正确返回 `money` 列。**双端/PTrade 目标策略必须只读 `money`**（读 `amount`/`close_front` 等本地物理列名会被 Validator 以 `PTRADE-LOCAL-COLUMN` 规则阻断）；本地单端策略读 `amount` 仍兼容。该映射为纯返回端别名，黄金回归证明对回测数值零影响。
- **Agent-first 唯一价格契约**：`market_data_contract.signal_price_adjustment="pre"` 且 `execution_price_basis="pre_adjusted_price"`。`raw_trade_price` 已从新设计 Schema 中移除；旧 raw 设计必须回到 R2 迁移并重新通过 R2.5/R4/R5。
- **统一证券元数据（F2）**：`get_stock_info` 股票行为与历史完全一致（名称=裸码、上市日=行情首根K线），仅扩展 ETF（真实名称、`stock_type='etf'`、上市/退市日 `YYYY-MM-DD`，fallback 显式标记）；未知代码保持兼容空值。本地 ETF 元数据支持 ≠ PTrade 真实 ETF 支持（未验证）。
- **指数成分严格 PIT（F3）**：`get_index_stocks(date)` 只取不晚于该日的最近 `complete` 快照——非历史并集、无未来泄漏、无快照返回空；完整性由 `index_constituents_snapshot_meta` 批次契约（expected_count/status）在打点判定，不依赖未来数据；回测中不传 date 自动用当前回测日期。
- **行业归属 APPROXIMATION_REQUIRES_CONFIRMATION（F4，非 PIT READY）**：`get_industry` 按当前回测日期 as-of 查正式 SW2021 成员表（`industry_classification`/`industry_membership`）。**关键语义边界**：官方 `index_member` 仅给 `in_date`/`out_date`，无冲突裁决规则；canonical 表**原样保留重叠区间**（如 SW2021 重新分类），**不应用自定义“生效日较新者胜”裁决**，每日唯一门控不再要求为 0。重叠命中时 `get_industry` 抛 `ReferenceDataCapabilityError`（fail-closed），绝不返回任意自定义裁决近似；能力标注 APPROXIMATION_REQUIRES_CONFIRMATION（因 canonical 表原样保留重叠区间、非 PIT READY，但运行时重叠一律 fail-closed）。无有效历史归属返回 `None`；正式表缺失 fail-closed；旧 `sw_industry` 仅为审计快照。
- **申万行业指数日线（F5）**：31 个 SW2021 L1 行业指数与普通指数统一入 `index_daily`（tushare `sw_daily` 路由，股/元单位）；`get_history` 对 801xxx 走 index_daily，`fq='pre'` 回退原始 OHLC。
- **调仓模式（F1）**：PyQt 回测面板透出 `rebalance_mode`（默认 `legacy`；`callback_basket` 仅 daily-bar-v1+next_open，导出 `engine_semantics_version=0.4.0-next_open_basket`，close/open 被 GUI 阻断；`run_daily` 订单永不进入 basket）。
- 读取**外部文件数据**（研报 CSV、信号表等）必须经由框架注入 API，例如 `load_research_signals(csv_path, fallback=...)`，文件 I/O 逻辑下沉到框架侧 `ptrade_api`；策略内直接 `open()` / `read_csv()` 会被 `StrategyIsolationGuard` 静态拦截并抛 `StrategyIsolationError`。

> **让 AI 帮你写策略**：想把策略需求交给其他智能体、自动产出可运行策略并落入 GUI 可选目录？参见 **[`docs/prompt_engineering.md`](docs/prompt_engineering.md)**。Agent-first 流程会在 R0 分别确认目标平台，以及R5由Agent执行还是由用户在PyQt执行；双端 ETF 策略固化用户确认的静态白名单，本地单端 ETF 策略才允许动态调用 `get_etf_list_local()`。用户PyQt模式在R4后只生成 `quantstudio/backtest/strategies/<strategy_id>__candidate_quantstudio.py`，用户自行选择回测日期并提交日志；R5 证据 2.0 要求绑定真实回测产物（`config.csv`/`daily_stats.csv`/`trades.csv`/运行日志及各自 SHA-256），由 review 脚本自动解析实际本金、持仓部署与拒单计数——"回测跑完无异常"不再是 PASS 依据；证据PASS后，R6生成正式文件并删除临时候选文件。默认正式输出分别为 `quantstudio/backtest/strategies/<strategy_id>_quantstudio.py` 与（仅双端）`ptrade/<strategy_id>_ptrade.py`。
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

## QFQ 重锚引擎（fresh_staged / ratio 双模型）

> 模块：`quantstudio.pipeline.qfq_reanchor_engine`。负责把日线/分钟前复权（QFQ）价格
> 从「比例锚（ratio）」修正升级到「fresh xtquant 逐值写入（fresh_staged，B-1，2026-07-27 批准）」。
> 本文档仅记语义边界，完整 API 见 [`docs/strategy_toolbox.md`](docs/strategy_toolbox.md) 第 4 节。

### 双模型选择语义（铁律）

`apply_reanchor_for_security(conn, *, asset_type, code, fresh_daily, calendar, ...,
model="ratio", model_reason=None, fresh_minutes=None, fresh_source=None,
fresh_capture_id=None, fresh_metadata_sha256=None)`：

- **`model` 必须显式传入**，引擎内**不存在**「ratio BLOCK → fresh_staged 静默回退」路径。
- **`ratio`**（默认）：方法 B + 方法 A 黄金抽验，原行为逐位不变；**禁止**传 `fresh_minutes`（防呆）。
- **`fresh_staged`**：fresh xtquant 分钟前复权逐值写入。必须提供 `fresh_minutes`
  （列 = `code/time/freq?` + OHLC raw + 四 `*_front`；多 freq 须含 `freq` 列），且
  **`model_reason` 必填**（写入事件审计，留痕为何切换模型）。
- 切换模型必须由调用方显式改写 `model` 并给出书面 `model_reason`，**禁止静默切换**。

### 事务与四态事件审计

单证券调用在一个显式连接上完成，成功与失败路径审计如下（全部带 `model` /
`model_reason` / `model_audit` 键，含 `fresh_source`、`fresh_capture_id`、
`metadata_sha256`、`tick_size`、`freqs`、`minute_coverage` / precheck 摘要）：

| status | 含义 | 事务 |
|--------|------|------|
| `committed` | 成功 | anchor 推进 + 价格修正 **同一事务** |
| `blocked` | 方法 B/A 或数据契约失败 | 回滚 + 独立短事务 `blocked` 事件 |
| `rolled_back` | postcheck 失败 | 回滚 + 独立短事务 `rolled_back` 事件 |
| `failed` | 其它异常 | 记录 `failed` 事件后**重新抛出** |

三种失败路径都**绝不推进 anchor**，绝不污染已提交数据。

### 纵深防御 postcheck（COMMIT 前硬门禁）

`minute_staged_match`（精确）> `scale_consistency`（容差）> `minute_tick_error`
（≤1 tick）> `minute_raw_match`（eps=1e-9 最精确）。其中：

- `minute_raw_match` 先显式拦截 raw 一侧 `IS NULL OR NOT isfinite() OR <=0`
  （SQL 三值逻辑陷阱：`ABS(NULL-x)>eps` 结果为 NULL，WHERE 按非真过滤会**静默漏检**），
  再比逐 bar abs diff；`n_invalid>0` 直接抛 `minute_raw_match`。
- `minute_tick_error` 同样先拦截 NULL/NaN/Inf/<=0，再比 `ABS(diff) <= tick_size`。

### tick_size 资产路由（第六轮阻断 4）

`tick_size` **不能写死 0.01**，须按资产/市场路由：`resolve_tick_size(asset_type, tol)`
= `STOCK=0.01` / `ETF=0.001`；显式 `tol.tick_size` 可覆盖；未知资产抛异常。
事件 `model_audit.tick_size` 记录实际使用值。

### 交易日历校验（第六轮阻断 2）

`stage_fresh_minutes` 对**每个**自然日调用 `CalendarService.is_trading_day` 校验：
周末或非开市日整券 BLOCK（`fresh_minutes_non_trading_day`）；日历 provider 未覆盖的
未知日 BLOCK（`fresh_minutes_unknown_day`）。钟面时刻合法（如周六 09:31）≠ 自然日开市，
必须逐日校验。`calendar=None` 直接抛 `ValueError`。

### 主动因子刷新与 detector degraded（常驻编排器）

> 模块：`quantstudio.pipeline.qfq_factor_refresh`（`QFQFactorRefresher`）+
> `quantstudio.pipeline.qfq_maintenance`（`resolve_ts_codes`）+
> `quantstudio.pipeline.aligner`（`raw_to_tushare_ts_code`）。

常驻 QFQ 编排器在事件发现之前主动刷新股票 `adj_factor` 与 ETF `fund_adj`（分别写
独立 SQLite 表），避免把陈旧因子快照误解释为"今天没有事件"。

- **默认关闭**：`qfq_orchestrator.factor_refresh_enabled` 默认 `False`（独立 opt-in）。
  主动因子刷新默认关闭。生产启用前必须通过股票/ETF ts_code 转换、刷新失败降级、
  水位 hold 和全量回归测试，并取得用户明确部署确认。
- **degraded 契约**：某资产类别**全部逐码请求失败**（`fetch_adj_factor` 抛
  `FactorRefreshError`）→ `degraded=True` → daemon 四价格表水位强制 hold、
  `qfq_cycle_run.detector_degraded=1`。**正常返回空数据不降级**（区间内无复权事件，
  返回 0 行不误报）。**部分码失败不降级**——保留成功结果落库，失败码仅 WARNING；
  这是当前明确但有风险的契约（部分失败码可能继续使用旧快照），是否升级为
  "任意单码失败即 degraded"另立后续正确性变更审核。
- **ts_code 转换**：`QFQFactorRefresher` 在调用 Tushare `adj_factor`/`fund_adj` 前，
  在各资产类别**自己的 try 块内**统一将裸码解析为 Tushare ts_code——`resolve_ts_codes`
  对裸码优先查 `stock_basic`/`etf_basic` 元数据表权威 ts_code，**已带合法 Tushare 后缀
  （.SH/.SZ/.BJ，含 .SS→.SH）的输入幂等保留、不被元数据覆盖**；裸码元数据 miss 时用资产类型感知的
  前缀规则（`raw_to_tushare_ts_code`：STOCK 6→SH/0,3→SZ/4,8→BJ；ETF 5→SH/1→SZ）
  fallback，不丢弃任何码；未知首位前缀防御性 fallback 到 .BJ 并记 WARNING。股票转换异常不影响 ETF（跨资产类别隔离）。
  > 注：本转换仅作用于 `QFQFactorRefresher` 的主动刷新路径，不覆盖 daemon 其它
  > Tushare 因子调用方（如 `daemon._fetch_adj_factor` 收到裸 ETF 时仍依赖现有
  > `market_of_code()`，可能错误推导 `.BJ`，作为独立残余风险）。
- **职责分离**：Tushare 负责因子（`adj_factor`/`fund_adj`），xtquant 负责价格
  （fresh_capture 单源锁定）；两者来源隔离，因子刷新不触碰价格表。

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

## W2 staging / 框架修复（2026-07-27 session, file timestamp 2026-07-28）

> 状态：框架代码补正完成（W1→W2-0.7A）→ staging 安全闭环测试（W2-0.7B，58 项）→ W2-0.8 框架修复（审核不通过）→ **W2-0.9 最终收口**（5 项缺陷 A-E + Phase 5 + C/F 补完 + 性能回归回退，全量 1571 passed）。W2 真实执行（Phase 0-4）暴露的框架缺陷已全部本地修复。数据等待用 **Git worktree 外** staging 路径重新执行完整 W2。禁止在审核通过前执行真实 staging prepare / Tushare 回填 / promotion / 修改正式库 / Git stage-commit-push。

### W2-0.9 框架修复（2026-07-28 最终收口）

W2-0.8 审核发现 9 项问题，W2-0.9 逐项关闭（详见 `docs/framework-fix-report-20260728.md` 与 `output/w2_fin_growth_dividend_20260728/phase4_defect_report.md`）：

- **A — writer VARCHAR 类型保护（补完）**：`writers.py` 入库前用 `DESCRIBE` 取目标列 DuckDB 类型，VARCHAR 列不再被 `pd.to_numeric` 吞掉（修复 `div_proc="实施"`→NULL）；**fallback 白名单补 div_proc/div_rat**（W2-0.8 漏）；DESCRIBE 失败记录 warning 不静默。
- **B — 通用 authority_reconciliation（补完）**：完整契约严格校验（mode=`purge_non_authoritative`/scope=`full_range_only` 未知值 config_lint + 运行时 fail-fast）；全触发条件（full_range + authority + fallback=false + actual source==authority）；**原子事务**（BEGIN/DELETE/校验/COMMIT 或 ROLLBACK）；cleanup_source_watermark=false 不删 watermark；空表不静默 PASS；标识符校验防注入。
- **D — run_once/CLI 退出码传播**：`run_once` 返回 `{task_found, task_ok, audit_run, audit_ok}`；task 不存在/failed/audit failed → CLI exit 1；staging `phase_run_task` 增加 batch ledger final status cross-check + 真实 CLI 子进程退出契约测试。
- **E — --quality-audit full|none**：daemon CLI 新增该参数（默认 full，生产语义不变）；staging 分阶段装载用 none。
- **Phase 5 — baseline-delta audit（接入重做）**：`validate_audit_evidence` 不再要求 `errors_count==0`，改为要求 `baseline_delta_passed=True`；目标表零 error，非目标表允许继承，new/regressed/severity-upgrade BLOCK；phase_audit 顺序调整（先 delta 再 strict validate）。
- **C/F — Git worktree 外 staging 路径（强制）**：非 dry-run prepare 必须 fail-closed BLOCK staging_root 在 Git worktree/项目根/data 内；preflight 独占检查；detached runner **不自动 kill**（scan 只报告；kill 显式+再验证；同 root 阻断）。
- **性能回归**：duckdb_data_access/duckdb_provider/ptrade_api 3 文件**回退到 HEAD**（batch 优化无条件查不存在的表 + 破坏测试替身）；纯性能等价修复组=回退状态。
- **G — watermark 审计口径对齐（W2-0.9 修正）**：`quality_audit._audit_watermarks` 优先用 schema 的 `time_key` 选业务时间列，回退再用硬编码优先级，与 daemon `_advance_actual_watermark`/`_get_safe_watermark` 推进口径对齐；修复 stock_dividend（`time_key=ex_date`）按除权日推进水位却被审计误用 `end_date` 比较导致的 `WatermarkConsistency` 误报。
- **H — batch 守恒口径修正（W2-0.9 修正）**：守恒等式由 `rows_passed + rows_rejected == rows_raw` 放宽为 `<= rows_raw`（DataValidator 分流前主键去重属合法收敛，去重只减不增）+ 新增 `rows_written == rows_passed` 入库一致性；`evidence.passed` 改为仅标记结构收集成功（`len(evidence_errors)==0`），raw audit 的 inherited error 归 baseline_delta 门禁负责，不再误伤 evidence 通过。

测试：新增 5 个测试文件（43 项）+ 现有 staging 测试适配；W2-0.9 专项 **165 passed**；全量 **1676 passed, 1 warning**（唯一 warning 与 W2 无关，原样保留）。

### Profile 1.10.0

- PTrade Profile 升级至 1.10.0：正式登记 `get_stock_exrights(security, date=None)`（返回 DataFrame，index=date，列: allotted_ps/rationed_ps/rationed_px/bonus_ps/exer_forward_a/exer_backward_a/bexer_backward_a/b）。portable usage 必须显式传 `date`；`date=None` 返回 `None`（底层查询需具体日期）。
- 注：`get_stock_exrights` 受 Tushare 接口频率限制（每分钟最多 200 次），批量调用需加间隔。

### 增长字段（fin_indicator）

新增：`or_yoy`（营收同比增长率，%）、`tr_yoy`（营业总收入同比增长率，%）`、`update_flag`（0=初版/1=修订版，PIT 去重）、`diluted_eps`（稀释 EPS，独立于 eps 列）。

### 分红字段（stock_dividend）

标准化：`cash_div_before_tax`（税前每股现金分红）、`cash_div_after_tax`（税后每股现金分红）、`stk_bo_rate`（送股比例）、`stk_co_rate`（转增比例）、`div_proc`（仅入库"实施"记录）。公司行为引擎税务策略：`pre_tax × 0.80`。

### get_stock_exrights API

完整签名：`get_stock_exrights(security, date=None)`。contexts: research/backtest/trade。返回 DataFrame（date 索引, 8 列 PTrade 兼容）或 None。源表 `stock_dividend`（tushare 权威源），schema 兼容旧列 `cash_div`。portable usage 必须显式传 `date`；`date=None` 返回 `None`。受 Tushare 频率限制（~200/min），批量建议间隔。

### W2-0.7B staging 安全闭环（2026-07-28）

staging 回填工具 `scripts/backfill_fin_growth_dividend_staging.py` 提供 `prepare / run-task / audit / promote` 四阶段，全程只操作 staging 副本，绝不修改正式库；`--promote` 仅 dry-run（打印命令，不执行）。安全门控：

- **prepare**：daemon/collector 活跃检测（`verify_daemon_identity`，stale 放行 / alive+denied+corrupt BLOCK）、磁盘 ≥ 2x 源库、源库 SHA-256 一致性、`--reset-staging` 需 `.quantstudio_staging.json` marker 校验且目标不得为磁盘根/项目根/data 根/源库父或含源库。
- **run-task**：config `db_path` 必须指向 staging.db（否则 SAFETY BLOCK）；子进程写 runtime manifest（atomic `os.replace` + nonce replay 防护），父进程严格校验 `format_version/task/nonce/QUANTSTUDIO_DATA_ROOT/imported_DATA_ROOT` + 七个路径字段全部 resolve 到 staging root；`timeout=0` 表示无超时，`timeout>0` 按 elapsed 终止；heartbeat 每 30s 可见。
- **audit / promote**：版本分离（`data_schema_version=2.0` 来自 alignment_rules vs `ptrade_profile_version=1.10.0` 来自 ptrade-api-signatures）；batch conservation（`rows_passed + rows_rejected == rows_raw`）；batch ID 唯一性 + 一任务一批；runtime manifest 内容校验；authority_rules 锁定 tushare 单源（`allow_fallback=false`）；audit `checks_run>0` 且 `errors_count==0`。

测试覆盖：`tests/test_fin_growth_dividend_staging_tool.py` 共 **58 项**（原 13 + W2-0.7B 新增 45），含负向门控（daemon alive/denied/corrupt BLOCK、stale 放行；collector lock held BLOCK、stale 放行；timeout=0/正数/heartbeat；reset marker 四类 BLOCK；缺配置/磁盘失败/源库不可读/size mismatch/SHA mismatch BLOCK；child manifest 缺失/stale nonce/wrong task/wrong DATA_ROOT/wrong path/wrong PID/stale/future timestamp BLOCK；duplicate batch/same-task/conservation/schema/profile/audit errors BLOCK；growth/dividend 全零 BLOCK；manifest audit 后漂移/删除 BLOCK）+ 两类双任务 E2E：**真实子进程 E2E**（真实 `python -m quantstudio.pipeline.daemon` 子进程 + `sitecustomize.py` 注入 FAKE `TushareAdapter`，验证 manifest pid == 实际 Popen PID、created_at ∈ 生命周期窗口、源库字节不变）与快速模拟 E2E（prepare→fin→div→audit→promote dry-run）。详见 `docs/staging-runbook.md` 与 `docs/framework-fix-report-20260728.md`。

### 当前 DB 状态

正式库仍为旧 schema（fin_indicator 11 列 167,028 行，增长字段全 NULL；stock_dividend 旧 cash_div 列，口径不明）。数据等待 W2 staging 回填：schema 迁移（`_migrate_add_columns` 幂等）-> staging prepare + fin_indicator 回填 -> staging stock_dividend 回填 -> 质量审计 -> promotion（原子替换）。正式库旧 schema 待 W2 回填后才会迁移；当前 staging 工具与所有 DataAccess 查询均支持旧/新 schema 兼容（动态检测列存在性）。

详见 `docs/framework-fix-report-20260728.md`（W2-0.7B 最终测试矩阵）与 `docs/framework-fix-report-20260727.md`（W1→W2-0.7A 框架修复）。

## 框架层变更审阅记录（perf/datadict-day-index）

本次 `quantstudio/backtest/ptrade_api.py`、`quantstudio/backtest/backtest_engine.py` 新增 DataDict/BacktestEngine 当日 DataFrame 的 `{raw_code: first_iloc}` 实例代码索引，将 `df['code'] == bare` 的 O(N) 布尔过滤替换为 O(1) 索引查找；`None`（无法构建）时严格回退原布尔过滤。

**AGENTS.md 框架铁律适用**：本变更为纯性能优化，已审阅确认未改变任何公共/注入 API 的函数名、签名、默认值、返回类型、返回字段、列顺序、索引、dtype、空值行为、异常行为或兼容行为；未改变行情取数范围、复权口径、生命周期调用时机、撮合/费用/持仓/现金/涨跌停处理、策略信号或回测指标；`data[code]`（DataDict）、`get_current_price`、`is_halted`、`pct_chg` 等接口语义与返回结构不变。因此本文档相关表述不受影响，无需修改。

## 框架层变更审阅记录（perf/backtest-data-cache）

`quantstudio/backtest/providers/duckdb_data_access.py` 的内部语义等价性能优化（仅 1 项）：
`_existing_tables()` 缓存 `SHOW TABLES` 结果，消除 `preload_daily_bars` /
`query_strategy_events` / `query_corporate_actions` 三处直接 `SHOW TABLES` 查询（小市值策略
76 交易日实测 SHOW TABLES 调用 **152 → 1**）；返回防御性 `set` 副本；`close()` 将
`_tables_cache` 恢复 `None`；原直接 `SHOW TABLES` 路径统一走 `_existing_tables()`。

**调用路径事实（b41400d）**：当前共有 **10 个**表存在性检查调用方共享 `_existing_tables()`，
其中 **7 个**是 b41400d 既有调用方，另有 `preload_daily_bars` / `query_strategy_events` /
`query_corporate_actions` 3 个原直接执行 `SHOW TABLES` 的调用方在本次收敛至统一入口。
`query_daily_snapshot` 在 b41400d 中已直接查询 `stock_daily`/`etf_daily`，不属于上述 10 个调用方。
旧基线 d8a0791 曾包含该调用方，因此历史数量为 11，但不适用于本次发布基线。

**AGENTS.md 框架铁律适用**：本变更为纯性能优化，语义完全等价——`_existing_tables()` 返回集合
与优化前逐字符一致，仅避免重复 `SHOW TABLES`；未改变任何公共/注入 API、取数范围、复权口径、
生命周期、撮合/费用/持仓/现金/涨跌停处理、策略信号或回测指标。完整验证见
`docs/performance_optimization.md`。

**provider-level get_history 缓存（`query_bars_by_count_multi_table`）本轮拒绝实施、进入
backlog**：小市值与双均线真实策略 `get_history` 命中均为 0；`PtradeAPI.get_history()` 已有
`_query_cache` 层；synthetic 86× 不构成生产收益证据；4096 条目上限是条目数而非字节内存上限；
后续实施须满足 byte-bounded LRU + 真实生产命中证据。
