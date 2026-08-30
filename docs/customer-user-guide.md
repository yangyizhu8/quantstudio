# QuantStudio 客户使用文档（含运维提示词包）

> **适用对象**：QuantStudio 回测平台的最终用户（无需编程经验）
> **使用方式**：本文档分两部分——第一部分是日常操作指南（人看的），第二部分是运维提示词包（贴给 AI 智能体用的）。两部分配合使用。
> **最后更新**：2026-08-30

---

## 目录

- 第一部分：系统概述与日常操作
  - 1. 系统是什么、能做什么
  - 2. 每天需要做什么（2 分钟）
  - 3. 数据源与采集任务
  - 4. MCP API Key 配置
  - 5. 策略研发（从想法到回测）
  - 6. 回测运行与结果查看
  - 7. 策略转换为 PTrade 平台格式
  - 8. 常见问题与注意事项
- 第二部分：运维提示词包（贴给 AI 智能体）
  - 提示词 1：日常巡检确认
  - 提示词 2：数据质量查询
  - 提示词 3：事故响应
  - 提示词 4：周度维护检查
  - 提示词 5：策略回测与转换
  - 红线卡：什么绝对不能做

---

# 第一部分：系统概述与日常操作

## 1. 系统是什么、能做什么

QuantStudio 是一套**本地量化回测平台**，主要功能：

| 功能 | 说明 |
|---|---|
| **数据自动采集** | 每天自动从云端拉取股票/ETF 行情、财务、因子等数据（约 88 个数据任务） |
| **数据自动清洗** | 拉取后自动校验、对齐、入库（DuckDB 本地数据库） |
| **策略回测** | 用 Python 写策略，在历史数据上模拟交易，看收益曲线 |
| **策略转换** | 将本地回测通过的策略一键转换为 PTrade 平台可运行的代码 |
| **数据质量防线** | 自动检测数据异常、自动修复已知问题、告警未知问题 |

**技术架构简图（不需要理解，仅供智能体参考）**：
```
云端 MCP 数据源（唯一数据源）
    ↓ HTTP + API Key 鉴权
本地采集守护进程（daemon）
    ↓ 自动拉取 → 对齐 → 校验 → 入库
本地 DuckDB 数据库（data/quantstudio.db）
    ↓ 读取
回测引擎（quantstudio/backtest/）
    ↓ 运行
回测结果（output/backtest_results/）
    ↓ 转换
PTrade 平台代码（output/ptrade_export/）
```

## 2. 每天需要做什么（2 分钟）

### 正常情况（99% 的时间）

**你只需要做一件事**：每天早上打开 AI 智能体会话，贴入「提示词 1：日常巡检确认」（见第二部分），等智能体回报结果。

- 如果显示 **全部 PASS** → 什么都不用做，数据正常。
- 如果显示有 **FAIL 或 WARN** → 贴入「提示词 3：事故响应」，智能体会告诉你怎么处理。

### 自动任务时间表（不需要你操作，仅供了解）

| 时间（交易日） | 自动执行的内容 |
|---|---|
| 06:00 | 数据采集守护进程启动（若未常驻） |
| 08:40 | 晨间证据包生成（巡检数据落盘） |
| 16:00 | 每日增量 ETL（拉取当日新数据） |
| 21:30 | 晚间补拉（部分数据源发布较晚，补齐当日） |
| 22:00 | 分钟数据修复（如有需要） |
| 03:00（次日） | 云端数据同步（本地 ↔ 云端对齐） |
| 周日 03:00 | 全量数据同步 |

**注意**：这些任务全部自动运行，你不需要手动触发。如果某天任务没有跑，贴「提示词 3」让智能体排查。

## 3. 数据源与采集任务

### 3.1 数据源概述

系统使用**唯一数据源：云端 MCP**（mcp_only profile）。

- **地址**：由 MCP_API_KEY 鉴权连接（见第 4 节配置）
- **覆盖范围**：A 股+ETF 的日线/分钟线/财务/因子/指数/行业等 88 个数据任务
- **为什么唯一**：避免多数据源混入导致的复权基准不一致（已彻底封死其他源）

**你不需要修改数据源配置**——除非 MCP 服务地址变更（极罕见），此时联系技术支持。

### 3.2 查看采集任务列表

打开文件 `config/profiles/mcp_only/collector_tasks.json`，可以看到所有 88 个任务。每个任务包含：

```json
{
  "name": "mcp_stock_daily",       ← 任务名
  "enabled": true,                   ← 是否启用
  "source": "mcp",                   ← 数据源（固定为 mcp）
  "table": "stock_daily",           ← 写入的表名
  "freq": "daily",                   ← 频率
  "start_date": "2018-01-01",       ← 起始日期
  "codes": ["ALL"],                  ← 覆盖代码
  "max_workers": 8,                  ← 并发数
  "retry": { "max": 5, "backoff_sec": [60, 120, 240, 480, 960] }
}
```

**注意事项**：
- 不要将 `enabled` 改为 `false`（会导致该表数据停更）
- 不要修改 `start_date`（会影响数据起始范围）
- 如果需要新增数据表，联系技术支持

### 3.3 数据存储位置

| 内容 | 路径 |
|---|---|
| 主数据库 | `data/quantstudio.db`（DuckDB 格式，约 20GB） |
| QFQ 辅助库 | `data/qfq_aux.db` |
| 快照备份 | `data/snapshots/` |
| 日志文件 | `trading-battle-back/logs/`（如部署了 ETL 侧） |
| 回测结果 | `output/backtest_results/` |

**重要**：不要直接用工具打开或修改 `quantstudio.db`——所有数据操作都通过系统自动完成。

## 4. MCP API Key 配置

### 4.1 首次部署时配置

1. 找到文件 `config/secrets.env`（如果不存在，创建一个）
2. 写入一行：
   ```
   MCP_API_KEY=你的密钥
   ```
3. 保存文件

### 4.2 验证配置是否正确

贴入以下提示词给智能体：
> 检查 config/secrets.env 中 MCP_API_KEY 是否存在且格式正确（不回显值），并验证数据管线今日是否有正常拉取记录。

### 4.3 注意事项

- **此文件不入 git**（已被 .gitignore 忽略）
- **不要将密钥写在任何文档、代码或日志中**
- 如果密钥泄露，立即联系 MCP 服务方更换
- 如果更换了密钥，更新 `config/secrets.env` 后重启系统即可

## 5. 策略研发（从想法到回测）

### 5.1 使用 AI 智能体开发策略（推荐）

**推荐方式**：使用 `quantstudio-strategy-compiler` skill（AI 智能体自动完成策略编写→校验→回测全流程）。

对智能体说：
> 请帮我开发一个[描述你的策略想法]的量化策略，使用 quantstudio-strategy-compiler skill。

智能体会自动：
1. 生成策略设计文档（IR 规范）
2. 编写 Python 策略代码
3. 运行校验（语法/API 合规/风险控制）
4. 生成回测产物（三件套）

### 5.2 手动编写策略（高级用户）

策略文件放在 `quantstudio/backtest/strategies/` 目录下。最简模板：

```python
def initialize(context):
    """策略初始化（回测开始时调用一次）"""
    context.target_list = []  # 你的选股列表
    set_backtest(start_date='2026-01-01', end_date='2026-06-30',
                 capital=1000000)

def before_trading_start(context, data):
    """每日开盘前调用（选股/调仓逻辑）"""
    # 获取全市场股票列表
    stocks = get_Ashares()
    # 筛选（示例：选出市值最小的 10 只）
    selected = filter_small_cap(stocks, count=10)
    context.target_list = selected

def handle_data(context, data):
    """每日盘中调用（下单逻辑）"""
    for code in context.target_list:
        if code not in context.portfolio.positions:
            order_target_value(code, context.portfolio.total_value / 10)

def after_trading_end(context, data):
    """每日收盘后调用（日志/统计）"""
    log.info(f"今日持仓 {len(context.portfolio.positions)} 只")
```

### 5.3 常用 API 速查

| API | 功能 | 示例 |
|---|---|---|
| `get_Ashares()` | 获取全 A 股列表 | `stocks = get_Ashares()` |
| `get_etf_list_local()` | 获取 ETF 列表 | `etfs = get_etf_list_local()` |
| `get_history(code, count, field, fq)` | 获取历史行情 | `df = get_history('600519.SH', 20, 'close', fq='pre')` |
| `get_fundamentals(table, fields, date)` | 获取财务数据 | `df = get_fundamentals('valuation', ['pe','pb'], '20260801')` |
| `order_target_value(code, value)` | 按目标市值下单 | `order_target_value('600519.SH', 100000)` |
| `get_position(code)` | 查询当前持仓 | `pos = get_position('600519.SH')` |
| `log.info(msg)` | 写日志 | `log.info(f"持仓数：{n}")` |

**完整 API 文档**：`docs/strategy_toolbox.md`

## 6. 回测运行与结果查看

### 6.1 运行回测

**方式一：GUI 界面（推荐新手）**
1. 启动 QuantStudio GUI（`python -m quantstudio.gui.main_window`）
2. 在"回测"标签页选择策略文件
3. 设置起止日期、初始资金
4. 点击"开始回测"

**方式二：命令行**
```bash
python scripts/run_ptrade_strategy.py --strategy quantstudio/backtest/strategies/你的策略.py --start 2026-01-01 --end 2026-06-30 --capital 1000000
```

### 6.2 查看结果

回测完成后，结果保存在 `output/backtest_results/日期_策略名/` 目录：

| 文件 | 内容 |
|---|---|
| `result.json` | 回测指标（收益/回撤/夏普等） |
| `trades.csv` | 逐笔交易记录 |
| `daily_positions.csv` | 每日持仓明细 |
| `equity_curve.png` | 净值曲线图 |

### 6.3 回测注意事项

| 事项 | 说明 |
|---|---|
| **交易日历** | 系统自动识别交易日，非交易日自动跳过 |
| **复权方式** | 默认前复权（`fq='pre'`），无需手动处理 |
| **停牌处理** | 停牌股票自动跳过买入，持仓停牌股票保留 |
| **涨跌停处理** | 涨停不买入、跌停不卖出（模拟真实约束） |
| **T+1 规则** | 股票当日买入次日才能卖出（A股规则） |

## 7. 策略转换为 PTrade 平台格式

### 7.1 前提条件

- 策略已通过本地回测验证
- 回测结果符合预期

### 7.2 转换步骤

**方式一：GUI 界面**
1. 打开 GUI → "转 PTrade"标签页
2. 选择策略文件
3. 点击"转换"
4. 转换产物在 `output/ptrade_export/策略名/策略名_ptrade.py`

**方式二：命令行**
```bash
python -m quantstudio.strategy_compiler.cli import quantstudio/backtest/strategies/你的策略.py
```

### 7.3 ETF 策略特殊处理

ETF 策略使用动态池（`get_etf_list_local`）时，转换会自动将池冻结为静态列表（`ETF_POOL_STATIC`），适配 PTrade 平台不支持动态池的限制。

### 7.4 转换后验证

转换完成后，建议将产物上传 PTrade 平台跑一次回测，对比双端结果。正常差异应在 3% 以内（超出则联系技术支持）。

## 8. 常见问题与注意事项

### Q1：某天数据没有更新？
**A**：先贴「提示词 1」让智能体检查。常见原因：网络中断、MCP 服务临时不可用、数据源晚发布。系统会在次日自动补齐（T+1 自愈）。

### Q2：看到飞书告警/巡检 FAIL？
**A**：贴「提示词 3：事故响应」。智能体会自动分析原因并给出修复建议。

### Q3：回测结果和 PTrade 平台不一致？
**A**：小差异（<3%）正常（撮合细节不同）。大差异请联系技术支持，可能是数据或策略转换问题。

### Q4：想新增数据表？
**A**：联系技术支持。新增表需要修改 collector_tasks.json + alignment_rules.json + 数据源映射，涉及多处配置。

### Q5：数据库文件太大？
**A**：`quantstudio.db` 约 20GB 属正常。不要手动删除——如需清理历史数据，联系技术支持。

### Q6：忘记 MCP API Key？
**A**：联系 MCP 服务提供方重新获取，更新 `config/secrets.env`。

### ⚠️ 绝对不要做的事

| 禁止操作 | 后果 |
|---|---|
| 直接修改 `quantstudio.db` 数据库文件 | 数据损坏，不可恢复 |
| 将 `enabled` 改为 `false` 停用采集任务 | 该表数据停更 |
| 删除 `data/snapshots/` 下的快照 | 丢失数据基线，无法验证数据质量 |
| 将 API Key 写入代码或提交到 git | 密钥泄露 |
| 在没有备份的情况下修改任何配置文件 | 配置损坏，系统无法启动 |
| kill 自动任务进程 | 数据断更，需人工恢复 |

---

# 第二部分：运维提示词包（贴给 AI 智能体）

> **使用方式**：将提示词完整复制粘贴到你的 AI 智能体会话中。智能体会按照提示词的指令执行操作并回报结果。

## 提示词 1：日常巡检确认

```
你是 QuantStudio 数据管线运维助手。

工作目录：[填写部署路径]
背景：QuantStudio 本地量化回测平台，每日自动从云端 MCP 拉取数据。晨间证据包在 logs/morning_evidence_YYYYMMDD.json（YYYYMMDD 为当日日期）。

任务：执行今日数据巡检确认。

步骤：
1. 读取今日晨间证据包 logs/morning_evidence_[今日日期].json
2. 逐项核对以下内容：
   - stock_daily / etf_daily / stk_factor_pro / ths_daily 的当日行数是否 ≥ 阈值
   - stock_minutes / etf_minutes 的水位是否最新
   - 云端对等巡检（cloud_parity）的 verdict 是否为 PASS
   - 六项完整性检查是否有 FAIL
3. 汇报格式：
   ✅ [项名]：PASS（数值）
   ⚠️ [项名]：WARN（原因）
   ❌ [项名]：FAIL（原因 + 建议动作）
4. 如有 FAIL 或 WARN：
   - 已知模式（规则库有匹配）：给出修复建议
   - 未知模式：列出详细证据（表/日期/行数/异常描述），标注"需人工排查"

红线：不要修改任何数据。只读取和报告。
```

## 提示词 2：数据质量查询

```
你是 QuantStudio 数据质量查询助手。

工作目录：[填写部署路径]
背景：主数据库 data/quantstudio.db（DuckDB），数据血缘表 lineage_batch_log。

任务：查询指定数据的质量与来源。

我要查：[填写表名，如 stock_daily] [填写日期，如 2026-08-30]

步骤：
1. 查询该表该日期的行数：
   python -c "import duckdb; con=duckdb.connect('data/quantstudio.db',read_only=True); print(con.execute('SELECT count(*) FROM [表名] WHERE trade_date=[日期]').fetchone())"
2. 查询数据血缘（谁写的）：
   python scripts/lineage_query.py --table [表名] --date [日期]
3. 查询数据质量（是否有 NULL/异常值）：
   python scripts/backfill_eps_gap.py --check
4. 将以上结果整合汇报：
   - 行数：X 行
   - 写入来源：[批次 ID / 写入者 / 时间 / 日志路径]
   - 质量状态：[正常/有异常描述]

红线：只读操作。不要执行任何 UPDATE、DELETE、INSERT。
```

## 提示词 3：事故响应

```
你是 QuantStudio 数据管线事故响应助手。

工作目录：[填写部署路径]
背景：每日自动数据管线（06:00 采集 → 16:00 ETL → 21:30 补拉 → 03:00 同步）。
已知问题模式库：docs/handoff/ 下技术债与事故归因档案。

我收到了告警/巡检 FAIL：[粘贴告警内容或 FAIL 项]

任务：分析根因并给出修复建议。

步骤：
1. 读取相关日志：
   - 晨间工件：logs/morning_evidence_[日期].json
   - ETL 日志：trading-battle-back/logs/incremental_etl.log（最后 100 行）
   - 巡检日志：trading-battle-back/logs/etl_integrity_check_[日期].log
2. 定性归因：
   - 匹配已知模式（如：Tushare 晚发布/WAL 延迟误报/补拉缺口/云端镜像滞后）
   - 已知模式 → 给出修复方案（含具体命令）
   - 未知模式 → 收集证据，标注"需联系技术支持"
3. 修复方案格式：
   [问题]：一句话描述
   [根因]：一句话定性
   [修复方案]：具体操作步骤
   [是否需要用户确认]：是/否（涉及数据修改的需要确认）
   [预计恢复时间]：X 分钟/小时

红线：
- 不要直接修改数据库
- 不要 kill 任何进程
- 涉及数据修改的修复，必须先给方案等用户说"批准"再执行
- 遇到从未见过的问题，收集证据后标注"需联系技术支持"，不要猜测性修复
```

## 提示词 4：周度维护检查

```
你是 QuantStudio 周度维护检查助手。

工作目录：[填写部署路径]
背景：每周日 03:00 执行全量数据同步。

任务：执行本周周度维护检查。

步骤：
1. 核对全量同步是否完成：
   查看 trading-battle-back/logs/run_cloud_sync_full_[上周日日期].log.err 是否正常结束
2. 核对云端对等巡检：
   读取最近 7 天的 logs/cloud_parity_*.json，统计 verdict 分布
3. 核对快照体系：
   查看 data/snapshots/index.json，确认最近快照日期
4. 核对数据血缘：
   抽查 1 个表的写入批次是否完整
5. 输出周报格式：
   ## 周度维护报告 [日期范围]
   - 全量同步：[完成/未完成]
   - 云端对等：[7 天中 X 天 PASS / Y 天 WARN / Z 天 FAIL]
   - 快照状态：[最新快照日期]
   - 数据血缘：[正常/异常描述]
   - 建议：[无/具体建议]

红线：只读操作。发现问题列出但不要自行修复。
```

## 提示词 5：策略回测与转换

```
你是 QuantStudio 策略开发助手。

工作目录：[填写部署路径]
背景：使用 quantstudio-strategy-compiler skill 进行策略开发。

我的策略想法：[用一句话描述你的策略逻辑]

任务：帮我开发这个策略并运行回测。

步骤：
1. 使用 quantstudio-strategy-compiler skill 的 R0-R6 流程：
   R0 需求确认 → R1 数据验证 → R2 组件规划 → R3 代码生成 → R4 校验 → R5 回测
2. 生成策略文件到 quantstudio/backtest/strategies/
3. 运行回测（默认参数：初始资金 100 万，最近 6 个月）
4. 汇报回测结果：
   - 年化收益 / 最大回撤 / 夏普比率
   - 交易次数 / 胜率
   - 净值曲线描述
5. 询问是否需要转换为 PTrade 格式

注意：策略代码使用 QuantStudio 本地 API（get_history/order_target_value 等），
不生成 PTrade 代码（转换由专门的转换功能承接）。
```

## 红线卡：什么绝对不能做

```
你是 QuantStudio 运维助手。以下是你的操作红线，任何情况下都不得违反：

1. 【禁止直接修改数据库】
   不执行任何 UPDATE、DELETE、INSERT 到 quantstudio.db 或 qfq_aux.db。
   所有数据修复必须通过系统提供的修复工具（backfill_eps_gap.py 等），
   且涉及数据修改的必须先获得用户明确批准。

2. 【禁止 kill 自动任务进程】
   采集守护进程(daemon)、ETL 任务、同步任务等全部自动运行。
   如果需要停止某个任务，必须由用户在任务计划程序中操作。

3. 【禁止在没有用户确认的情况下推送代码】
   任何 git push 操作必须获得用户明确批准。

4. 【禁止修改配置文件而不做备份】
   修改任何 .json/.yaml/.env 文件前，必须先复制一份备份。

5. 【禁止删除快照】
   data/snapshots/ 下的快照是数据质量基线，不可删除。

6. 【遇到未知问题：收集证据，不要猜测性修复】
   - 记录：什么时间、什么表、什么异常、日志片段
   - 标注："此问题未在已知模式库中找到匹配，建议联系技术支持"
   - 绝对不要："我觉得可能是XX原因，试试改一下XX"

如果你不确定某个操作是否安全，回复"此操作需要确认"并等待用户指示。
```

---

## 附录：关键文件路径速查

| 用途 | 路径 |
|---|---|
| MCP API Key | `config/secrets.env` |
| 采集任务配置 | `config/profiles/mcp_only/collector_tasks.json` |
| 数据源配置 | `config/profiles/mcp_only/sources_config.json` |
| 字段对齐规则 | `config/profiles/mcp_only/alignment_rules.json` |
| 主数据库 | `data/quantstudio.db` |
| 快照备份 | `data/snapshots/` |
| 策略代码目录 | `quantstudio/backtest/strategies/` |
| 回测结果 | `output/backtest_results/` |
| PTrade 转换产物 | `output/ptrade_export/` |
| 晨间巡检工件 | `logs/morning_evidence_YYYYMMDD.json` |
| ETL 日志 | `trading-battle-back/logs/` |
| 策略工具箱文档 | `docs/strategy_toolbox.md` |
| 提示词工程文档 | `docs/prompt_engineering.md` |
| GUI 启动命令 | `python -m quantstudio.gui.main_window` |
