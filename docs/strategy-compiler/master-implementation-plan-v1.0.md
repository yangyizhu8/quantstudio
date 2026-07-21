# QuantStudio Strategy Compiler Skill 实施方案与执行计划

> 文档版本：v1.0  
> 冻结日期：2026-07-20  
> 目标项目：`D:\miniQMT策略实盘\QuantStudio`  
> 计划文档目录：`D:\miniQMT策略实盘\私募工作文件\本地回测框架策略开发skill的实施方案和计划`  
> 文档性质：后续由 Codex 按阶段执行、验证、迭代和验收的唯一主计划  
> 当前状态：**方案已冻结，尚未开始修改 QuantStudio 运行时代码或创建正式 Skill**

---

## 1. 项目目标

建设一个面向 QuantStudio 的专属策略开发 Skill：**QuantStudio Strategy Compiler**。用户输入自然语言策略思路后，Skill 通过多轮交互澄清需求，生成统一的 Strategy Spec 和平台无关中间表示，再分别生成：

1. 可直接放入 QuantStudio 策略目录、由 GUI/CLI 回测的本地策略代码；
2. 符合指定 PTrade Profile、可复制到目标 PTrade 平台运行的策略代码；
3. 策略规格、运行卡、能力检查报告、静态检查报告和双版本一致性报告；
4. 在对应数据和引擎能力真实就绪时，自动执行本地冒烟回测；
5. 用户导入 PTrade 回测结果后，复用现有 Fidelity Comparator 完成双平台对照。

项目最终目标不是“生成看起来合理的代码”，而是形成下面这条可审计、可验证、可扩展的策略编译流水线：

```text
自然语言策略
  → 多轮需求澄清
  → 环境/数据/引擎能力门禁
  → Strategy Spec（唯一事实源）
  → 用户确认硬闸门
  → 平台无关 Strategy IR
  → QuantStudio Renderer + PTrade Renderer
  → AST/时序/API/硬过滤/一致性检查
  → 本地冒烟与回归测试
  → Run Card
  → PTrade 回测结果 Fidelity 对照
```

---

## 2. 冻结的核心决策

以下决策在后续实施中作为强制约束，除非用户明确批准修改本计划。

### 2.1 架构决策

- 采用“编译器式 Skill”，不采用“超长提示词堆叠式 Skill”。
- `SKILL.md` 只承载工作流、门禁、路由规则和必要约束。
- 框架/API/时序/A股规则等详细知识按主题放入 `references/`，按需读取。
- Strategy Spec 是策略逻辑的唯一事实源，禁止分别独立手写本地版和 PTrade 版。
- 双版本代码必须由同一 Spec 和同一 IR 渲染，避免参数、过滤条件和时序漂移。
- 用户策略不追加进核心 `SKILL.md`；每个策略保存独立的 `strategy_spec.json`。

### 2.2 第一版产品范围

第一版产品设计覆盖：

- 日线原生模式；
- 分钟原生模式；
- 日线开盘价/收盘价代理模式；
- 股票和 ETF；
- QuantStudio/PTrade 双版本输出；
- A股硬过滤；
- 数据、Provider、Engine、平台能力门禁；
- 日线/分钟静态检查和冒烟回测；
- PTrade Profile；
- Tick、盘口、高频事件流的 Spec 字段预留。

第一版不把 Tick/L2/高频标为可执行能力。必须区分：

```text
Spec 可以表达
≠ 数据已经入库
≠ Provider 已经支持
≠ Engine 已经支持
≠ PTrade Profile 已经支持
≠ 冒烟回测已经通过
```

### 2.3 分钟能力决策

当前项目已经具备分钟数据采集配置、Schema、字段对齐、质量检查和复权基础，但仍需实施：

- Provider frequency 参数贯通；
- `stock_minutes` / `etf_minutes` 查询路由；
- 分钟事件循环；
- 分钟定时任务；
- 分钟撮合和日内多次下单；
- 分钟回归测试。

因此第一版 Skill 必须包含分钟 Profile 和分钟交互，但只有 Engine Profile 真实达到 `READY` 后，才允许宣称分钟冒烟回测通过。

### 2.4 next_open 决策

`next_open` 必须实现为真正的延迟订单：

```text
T 日产生订单
→ 进入 pending queue
→ T 日净值不受订单影响
→ T+1 开盘检查停牌/涨跌停/资金
→ T+1 成交或拒单
→ T+1 更新现金、持仓、订单和成交日期
```

禁止继续使用“在 T 日循环中提前读取 T+1 开盘价并立即改变持仓”的语义。

### 2.5 日线代理决策

用户提出精确盘中时间，但当前对应分钟能力不可用时，可以在用户明确确认后采用日线代理。

强制一致性：

```text
daily_open_proxy  <=> match_price_mode == open
daily_close_proxy <=> match_price_mode == close
```

其中 `daily_close_proxy` 如果信号也依赖当日完整收盘数据，不允许同一收盘价形成信号并成交；必须改为下一交易日执行或分钟级近收盘信号。

代理模式必须在 Spec 和报告中记录：

- 用户原始执行时间；
- 实际代理方式；
- approximation=true；
- approximation_reason；
- user_confirmed=true；
- “非真实指定时刻成交价”的风险说明。

### 2.6 数据和 API 降级决策

三大财务报表、行业成分、ETF 成分券、IPO、板块、Tick 等能力不从 Skill 中删除，而是进入能力矩阵。

禁止以下静默行为：

- 依赖的数据表为空，但策略继续零交易并报告成功；
- API 降级返回空列表，策略把空列表当作有效股票池；
- Provider 不支持频率，却由日线数据冒充分钟数据；
- Engine 不支持 Profile，却只做语法检查后宣称回测通过。

### 2.7 PTrade 决策

- PTrade 的导入方式、代码后缀、API 白名单和回调能力由 PTrade Profile 决定。
- 不在 Skill 中固定写死 `from ptrade_api import *`。
- 默认 Profile 使用本地 PTrade 兼容层与目标平台的公共 API 子集。
- 未确认券商/版本时，只能声明“符合默认 PTrade Profile”，不能承诺适配所有 PTrade 部署。

---

## 3. 当前基线与已知事实

### 3.1 已具备基础

QuantStudio 当前已经具备：

- PTrade 生命周期：`initialize`、`before_trading_start`、`handle_data`、`after_trading_end`；
- 统一 API 注入层：`quantstudio/backtest/ptrade_import.py`；
- 策略隔离门禁：`StrategyIsolationGuard`；
- Provider 解耦：`DataProviderRegistry`；
- DuckDB 日线数据访问；
- T+1、涨跌停、整手、佣金、印花税、过户费和滑点；
- ST、停牌、退市和退市风险过滤；
- 上市日期查询；
- GUI 策略目录扫描；
- PTrade 导入和 Fidelity Comparator；
- 股票/ETF 分钟采集任务、分钟 Schema、复权和质量检查；
- Tick Schema 预定义。

### 3.2 已识别缺口

- 证券类型识别和后缀转换逻辑分散；
- 北交所代码规则存在文档和代码不一致风险；
- 当前 `next_open` 不是真正延迟执行；
- Provider 接口没有 frequency 参数；
- `get_history(unit='1m')` 和 `get_price(frequency='1m')` 未把频率传到底层；
- DuckDB bars 查询仍以日线表为主；
- 回测主循环仍是逐日事件循环；
- `run_daily(time=...)` 尚未按分钟时间精确触发；
- 停牌订单执行阻断需要专项审计；
- 部分 API 是降级实现；
- GUI 暂无 `strategy_config.json` 自动导入，第一版不阻塞 Skill。

### 3.3 初始测试基线

正式开始 PR0 时必须重新执行并记录：

```powershell
python -m pytest -q
```

并至少单独执行：

```powershell
python -m pytest -q `
  tests/test_strategy_runner.py `
  tests/test_order_rejection.py `
  tests/test_filter_stock_by_status.py `
  tests/test_match_price_mode.py `
  tests/test_etf_ptrade_compat.py `
  tests/test_strategy_alignment_regressions.py
```

已有一次相关子集记录为 `51 passed`，但实施时必须以当次执行结果为准。

---

## 4. 目标架构

```text
┌────────────────────────────────────────────────────────────┐
│ QuantStudio Strategy Compiler Skill                         │
├────────────────────────────────────────────────────────────┤
│ R-1 Capability Inspector                                    │
│ R0  Natural Language Parser                                 │
│ R1  Interactive Requirement Resolver                        │
│ R2  Strategy Spec Builder                                   │
│ R2.5 Human Confirmation Gate                                │
│ R3  Strategy IR Builder                                     │
│ R4  QuantStudio/PTrade Renderers                            │
│ R5  Validators                                              │
│ R6  Smoke Backtest Runner                                   │
│ R7  Fidelity Comparator Adapter                             │
└────────────────────────────────────────────────────────────┘
              │                    │
              ▼                    ▼
     Strategy Spec / IR       Capability Report
              │
       ┌──────┴──────┐
       ▼             ▼
QuantStudio Code   PTrade Code
       │             │
       ▼             ▼
Local Engine       Target PTrade
       └──────┬──────┘
              ▼
      Fidelity Comparison
```

### 4.1 推荐的 Skill 源码位置

项目内保存可版本化的 Skill 源码：

```text
D:\miniQMT策略实盘\QuantStudio\skills\quantstudio-strategy-compiler\
```

安装/发布到 Codex Skill 目录时再复制或安装到：

```text
C:\Users\Administrator\.codex\skills\quantstudio-strategy-compiler\
```

项目目录是唯一源码；用户 Skill 目录是安装产物，禁止两边独立修改。

### 4.2 目标 Skill 结构

```text
quantstudio-strategy-compiler/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── framework-contract.md
│   ├── lifecycle-and-timing.md
│   ├── frequency-and-engine-profiles.md
│   ├── strategy-spec-contract.md
│   ├── strategy-ir-contract.md
│   ├── api-capability-matrix.md
│   ├── ptrade-profiles.md
│   ├── ashare-hard-filters.md
│   ├── no-lookahead-rules.md
│   ├── output-contract.md
│   └── known-limitations.md
├── templates/
│   ├── quantstudio_daily.py.j2
│   ├── quantstudio_minute.py.j2
│   ├── ptrade_daily.py.j2
│   ├── ptrade_minute.py.j2
│   ├── strategy_spec.json
│   └── run_card.json
├── schemas/
│   ├── strategy_spec.schema.json
│   ├── capability_report.schema.json
│   └── run_card.schema.json
└── scripts/
    ├── inspect_capabilities.py
    ├── validate_strategy_spec.py
    ├── build_strategy_ir.py
    ├── render_quantstudio.py
    ├── render_ptrade.py
    ├── validate_local_strategy.py
    ├── validate_ptrade_portability.py
    ├── scan_lookahead.py
    ├── check_hard_filters.py
    ├── compare_strategy_variants.py
    ├── run_smoke_backtest.py
    └── install_skill.py
```

---

## 5. Strategy Spec 核心设计

### 5.1 顶层结构

```json
{
  "spec_version": "1.0",
  "strategy_id": "example_strategy",
  "strategy_name": "示例策略",
  "strategy_type": "stock_selection",
  "target_platforms": ["quantstudio", "ptrade-default"],
  "time_model": {},
  "engine_profile": {},
  "universe": {},
  "hard_filters": {},
  "signals": {},
  "portfolio": {},
  "execution": {},
  "risk": {},
  "costs": {},
  "data_requirements": {},
  "capability_requirements": {},
  "approximations": [],
  "user_confirmations": [],
  "validation_policy": {},
  "output": {}
}
```

### 5.2 Time Model

必须分离：

```json
{
  "time_model": {
    "market_data_frequency": "1m",
    "factor_frequency": "1d",
    "signal_frequency": "1d",
    "decision_clock": "09:35:00",
    "execution_clock": "current_bar",
    "portfolio_valuation_frequency": "1m",
    "holding_period_unit": "trading_day",
    "signal_data_cutoff": "T-1-close"
  }
}
```

禁止用一个 `frequency` 字段同时代表数据、指标、决策和成交频率。

### 5.3 Engine Profile

```json
{
  "engine_profile": {
    "event_type": "bar",
    "bar_frequency": "1m",
    "market_depth": "L1",
    "order_book_levels": 0,
    "schema_supported": true,
    "execution_status": "READY"
  }
}
```

Tick/L2 预留示例：

```json
{
  "engine_profile": {
    "event_type": "tick",
    "bar_frequency": null,
    "market_depth": "L2",
    "order_book_levels": 10,
    "schema_supported": true,
    "execution_status": "PLANNED"
  }
}
```

### 5.4 能力状态模型

不建议只使用单一状态，正式 Schema 拆成多个维度：

```json
{
  "capability": "stock_1m_backtest",
  "schema_status": "AVAILABLE",
  "data_status": "DATA_MISSING",
  "adapter_status": "AVAILABLE",
  "provider_status": "PROVIDER_MISSING",
  "engine_status": "ENGINE_MISSING",
  "platform_status": "PLATFORM_DEPENDENT",
  "execution_status": "BLOCKED"
}
```

允许值至少包括：

```text
AVAILABLE
READY
DATA_MISSING
ADAPTER_MISSING
PROVIDER_MISSING
ENGINE_MISSING
PLATFORM_DEPENDENT
DEGRADED
SCHEMA_ONLY
PLANNED
UNSUPPORTED
BLOCKED
```

### 5.5 A股硬过滤默认值

股票策略默认强制：

```json
{
  "hard_filters": {
    "exclude_st": true,
    "exclude_suspended": true,
    "exclude_delisted": true,
    "exclude_delisting_sorting": true,
    "exclude_star_market": true,
    "exclude_bse": true,
    "min_listing_trade_days": 252,
    "exclude_invalid_price": true,
    "exclude_zero_volume": true,
    "block_limit_up_buy": true,
    "block_limit_down_sell": true,
    "enforce_t1": true,
    "round_lot": 100
  }
}
```

ETF、可转债、期货等 Profile 应覆盖相应规则，而不是盲目套用股票 T+1 和税费。

---

## 6. 多轮交互流程

### R-1：运行环境与能力探测

自动检查：

- DuckDB 是否存在；
- 日线、分钟、ETF 分钟、Tick 表是否存在及数据范围；
- 关键字段是否存在；
- Provider 是否支持请求频率；
- Engine Profile 是否就绪；
- 财务、行业、ETF 成分等数据能力；
- PTrade Profile；
- 目标策略目录是否可写。

输出 `capability_report.json`。

### R0：策略思路解析

将自然语言拆成：

- 标的和股票池；
- 指标/因子；
- 买入条件；
- 卖出条件；
- 调仓条件；
- 仓位；
- 风控；
- 回测区间；
- 数据频率；
- 指定执行时间。

### R1-A：数据和频率确认

确认：

- 日线/分钟/Tick；
- 股票/ETF/其他；
- 数据字段和历史长度；
- 数据是否已就绪；
- 缺失时是否允许代理。

### R1-B：信号和执行时序确认

必须分别确认：

1. 信号可以看到的数据截止时间；
2. 产生决策的时刻；
3. 实际成交的时刻；
4. 使用当前 Bar、下一 Bar 还是下一交易日；
5. 是否允许日线代理；
6. 代理方式。

### R1-C：A股交易约束确认

展示默认硬规则。用户可以确认参数，但不能静默关闭 T+1、涨跌停、停牌和整手等基础真实交易约束。

### R1-D：PTrade Profile 确认

确认：

- 默认或指定券商 Profile；
- API 注入方式；
- 代码后缀；
- 生命周期；
- 可用 API；
- 数据字段权限；
- 严格公共 API 模式或本地优化模式。

### R2：生成 Strategy Spec

只生成 Spec，不写代码。

### R2.5：用户确认硬闸门

向用户展示：

- 策略逻辑摘要；
- 时序图；
- 数据要求；
- 股票池和过滤；
- 仓位和风控；
- 成交口径；
- 代理和近似；
- 本地/PTrade 已知差异。

用户未确认，不进入 R3。

### R3：生成 Strategy IR

IR 使用平台无关步骤：

```text
build_universe
apply_hard_filters
load_market_data
compute_features
generate_signals
rank_and_select
build_target_weights
apply_risk_constraints
submit_orders
record_diagnostics
```

### R4：双目标渲染

生成：

```text
<strategy_id>_quantstudio.py
<strategy_id>_ptrade.py
strategy_spec.json
strategy_ir.json
capability_report.json
run_card.json
README.md
```

### R5：静态验证

必须检查：

- Python 语法；
- 生命周期完整；
- API 白名单；
- StrategyIsolationGuard；
- 未来函数；
- 信号和成交时序；
- 硬过滤；
- 数据依赖；
- PTrade 版本是否泄漏本地扩展 API；
- 双版本参数、条件和默认值一致；
- 代理模式与撮合模式一致；
- 空股票池是否来自能力缺失。

### R6：本地冒烟回测

仅在 Profile `execution_status=READY` 时执行。

否则输出“代码已生成/静态检查已通过/执行验证被能力门禁阻止”，不得宣称回测通过。

### R7：PTrade Fidelity 对照

导入 PTrade 结果后比较：

- L1 信号；
- L2 净值、回撤、收益和风险指标；
- L3 持仓重合；
- L4 成本和有效费率；
- 交易日期、数量和拒单原因。

---

## 7. 分阶段实施计划

# PR0：冻结契约与测试基线

## 7.0 目标

在修改运行时代码前，冻结所有跨模块契约，防止后续重复返工。

## 7.1 产出

建议在项目中新建：

```text
D:\miniQMT策略实盘\QuantStudio\docs\strategy-compiler\
├── architecture.md
├── strategy-spec-contract.md
├── lifecycle-and-timing-contract.md
├── frequency-and-engine-profile.md
├── capability-model.md
├── ashare-filter-contract.md
├── ptrade-profile-contract.md
├── output-and-run-card-contract.md
└── implementation-status.md
```

并新建：

```text
D:\miniQMT策略实盘\QuantStudio\quantstudio\strategy_compiler\schemas\
├── strategy_spec.schema.json
├── capability_report.schema.json
└── run_card.schema.json
```

## 7.2 任务清单

- [ ] 执行全量测试并记录基线；
- [ ] 冻结 Strategy Spec v1；
- [ ] 冻结 Time Model；
- [ ] 冻结 Engine Profile；
- [ ] 冻结能力状态模型；
- [ ] 冻结 PTrade Profile；
- [ ] 冻结 A股硬过滤默认项；
- [ ] 冻结日线代理契约；
- [ ] 冻结输出目录结构；
- [ ] 建立 JSON Schema 验证测试；
- [ ] 建立版本兼容规则。

## 7.3 测试

新增建议：

```text
tests/test_strategy_spec_schema.py
tests/test_capability_model.py
tests/test_timing_contract.py
tests/test_proxy_mode_contract.py
```

## 7.4 验收标准

- 所有 Schema 示例可验证；
- 不合法的 time model 被拒绝；
- `daily_open_proxy + close` 被拒绝；
- Tick 字段可表达但执行状态不能错误标记 READY；
- 旧有全量测试不退化；
- 用户确认 PR0 文档后再进入 PR1。

---

# PR1：统一证券代码和市场分类规则

## 7.5 目标

建立代码级唯一权威规则，消除分散的 `startswith()`、后缀推导和北交所识别差异。

## 7.6 主要文件

新增：

```text
quantstudio/backtest/libs/security_code_rules.py
```

修改候选：

```text
quantstudio/backtest/libs/shared_ashare_rules.py
quantstudio/backtest/ptrade_api.py
quantstudio/backtest/backtest_engine.py
quantstudio/backtest/providers/duckdb_data_access.py
```

## 7.7 导出接口

```python
classify_security(code)
normalize_security_code(code, target="qmt" | "ptrade" | "bare")
normalize_to_qmt(code)
normalize_to_ptrade(code)
is_main_board(code)
is_chinext_market(code)
is_star_market(code)
is_bse_market(code)
is_etf(code)
is_convertible_bond(code)
is_index(code)
```

## 7.8 实施要求

- [ ] 核验权威北交所代码规则和项目实际数据编码；
- [ ] 统一 `.SH/.SS/.XSHG/.SZ/.XSHE/裸码`；
- [ ] 补全北交所目标后缀；
- [ ] 保持已有策略后缀兼容；
- [ ] 所有 A股过滤和涨跌停规则引用新模块；
- [ ] 删除重复规则；
- [ ] 保证 ETF/指数/可转债不被误分类。

## 7.9 测试

新增：

```text
tests/test_security_code_rules.py
tests/test_security_code_aliases.py
tests/test_bse_filtering.py
```

## 7.10 验收标准

- 所有支持的后缀双向转换一致；
- 北交所过滤不漏判；
- 主板、创业板、科创板涨跌停比例不退化；
- 持仓、历史数据和订单可用任意兼容后缀访问；
- 全量测试通过。

---

# PR2：修正 next_open 和订单执行时序

## 7.11 目标

实现真实 pending order queue，消除跨日价格和持仓记账穿越。

## 7.12 设计

新增订单生命周期：

```text
created → pending → filled/rejected/expired/cancelled
```

建议新增：

```python
@dataclass
class PendingOrder:
    order_id: str
    created_dt: str
    scheduled_dt: str
    execution_event: str
    security: str
    instruction_type: str
    target_value: float | None
    shares: int | None
    status: str
```

## 7.13 任务清单

- [ ] T 日信号只创建 pending order；
- [ ] T+1 开盘前执行 pending queue；
- [ ] 使用 T+1 开盘价和 T+1 状态；
- [ ] 正确记录 created_dt 和 filled_dt；
- [ ] T 日净值不包含未执行订单；
- [ ] 未执行订单不解锁 T+1 持仓；
- [ ] 末日订单标记 expired 或保留 pending；
- [ ] 停牌、涨停、跌停、资金不足均返回明确原因；
- [ ] 保持 `close` 和 `open` 模式向后兼容；
- [ ] Run Card 记录执行模式。

## 7.14 测试

新增/修改：

```text
tests/test_next_open_pending_orders.py
tests/test_next_open_nav_timing.py
tests/test_next_open_limit_and_halt.py
tests/test_pending_order_end_of_backtest.py
```

## 7.15 验收标准

- T 日生成、T+1 成交；
- T 日现金和持仓不变；
- 交易记录日期为 T+1；
- T+1 涨停时买单拒绝；
- T+1 停牌时不成交；
- T+1 跌停时卖单拒绝；
- 与 PTrade 样本时序更接近；
- 全量测试通过。

---

# PR3：打通多频率 Provider 数据链路

## 7.16 目标

让 API 的 `unit/frequency` 真正传到 Provider 和 DuckDB 查询层。

## 7.17 接口改造

```python
get_bars(codes, start_date, end_date, frequency="1d", fields=None, fq=None)
get_bars_by_count(codes, count, end_date, frequency="1d", fields=None, fq=None)
get_snapshot(date_or_dt, frequency="1d", fields=None)
```

## 7.18 路由规则

```text
1d    → stock_daily / etf_daily / index_daily
1m    → stock_minutes / etf_minutes
5m    → 原生 5min；无原生时允许从 1min 按严格交易时段聚合
15m   → 按能力矩阵决定原生或聚合
30m   → 按能力矩阵决定原生或聚合
60m   → 按能力矩阵决定原生或聚合
tick  → tick，第一阶段返回能力门禁而非空数据
```

## 7.19 任务清单

- [ ] Provider 抽象接口增加 frequency；
- [ ] 所有 Provider 实现更新；
- [ ] `get_history` 传递 unit；
- [ ] `get_price` 传递 frequency；
- [ ] 分钟表查询支持股票和 ETF；
- [ ] 分钟复权口径明确；
- [ ] 分钟字段映射与日线字段分离；
- [ ] 1m→5m 聚合处理午间休市和交易日边界；
- [ ] 严禁频率缺失时回退到日线；
- [ ] 数据缺失返回结构化能力错误；
- [ ] 更新 API 能力矩阵。

## 7.20 测试

新增：

```text
tests/test_provider_frequency_routing.py
tests/test_minute_bars_query.py
tests/test_etf_minute_bars_query.py
tests/test_minute_qfq_query.py
tests/test_minute_aggregation.py
tests/test_frequency_no_daily_fallback.py
```

## 7.21 验收标准

- `get_history(unit='1m')` 真实读取分钟表；
- `get_price(frequency='5m')` 返回正确 5m 数据；
- 午间休市不生成虚假 Bar；
- 股票和 ETF 均可查询；
- 数据缺失时明确阻断；
- 日线结果不退化；
- 全量测试通过。

---

# PR4：分钟事件驱动回测引擎

## 7.22 目标

实现真实分钟策略生命周期、分钟撮合和日内定时任务。

## 7.23 引擎建议

优先避免复制一套完全独立的分钟引擎。推荐将现有引擎抽象为：

```text
TradingCalendar
EventStream
MarketSnapshotProvider
LifecycleScheduler
ExecutionSimulator
PortfolioLedger
ResultRecorder
```

日线和分钟共享：

- 账户；
- 订单；
- 成本；
- T+1；
- 涨跌停；
- 证券代码规则；
- 结果输出。

仅 EventStream 和 Scheduler 不同。

## 7.24 生命周期

```text
每个交易日开始
  → T+1 解锁
  → before_trading_start（一次）
  → 09:30 Bar
  → 09:31 Bar
  → ...
  → 每 Bar 更新 data/current_dt
  → 精确触发 run_daily(time)
  → handle_data（按策略 Profile）
  → 15:00 Bar
  → after_trading_end（一次）
  → 日终净值
```

## 7.25 任务清单

- [ ] 实现分钟 EventStream；
- [ ] Context.current_dt 精确到分钟；
- [ ] DataDict 使用当前分钟快照；
- [ ] `handle_data` 每 Bar 或按配置调用；
- [ ] `run_daily(time=...)` 精确调度；
- [ ] 支持日内多次下单；
- [ ] T+1 只在新交易日解锁；
- [ ] 分钟成交价模式；
- [ ] 停牌/涨跌停/成交量约束；
- [ ] 订单和持仓每笔成交后刷新；
- [ ] 日终和可选分钟 NAV；
- [ ] 分钟进度回调；
- [ ] GUI/CLI 暂通过参数选择 Profile；
- [ ] 日线引擎兼容回归。

## 7.26 测试

新增：

```text
tests/test_minute_engine_lifecycle.py
tests/test_run_daily_scheduler.py
tests/test_minute_order_execution.py
tests/test_minute_t1.py
tests/test_minute_limit_halt.py
tests/test_minute_nav.py
tests/test_minute_daily_compatibility.py
```

黄金策略：

1. 09:35 买入、14:55 卖出 ETF；
2. 1m 双均线；
3. T 日分钟买入、当日禁止卖出股票；
4. ETF Profile 允许相应 T+0 规则；
5. 分钟涨停买入拒单；
6. `run_daily('10:00')` 每日只触发一次。

## 7.27 验收标准

- 生命周期时序与设计一致；
- 定时任务精确触发；
- 股票 T+1 正确；
- ETF 规则由 Profile 控制；
- 分钟数据不足不静默回退；
- 日线原有策略结果在允许误差内不变；
- 全量测试通过。

---

# PR5：创建 QuantStudio Strategy Compiler Skill

## 7.28 目标

建立正式 Skill、多轮流程、References、Schemas、Templates 和工具脚本。

## 7.29 实施要求

- [ ] 使用 `skill-creator` 规范初始化 Skill；
- [ ] `SKILL.md` 保持简洁；
- [ ] Description 明确触发场景；
- [ ] 详细知识拆入 references；
- [ ] 建立 `agents/openai.yaml`；
- [ ] 创建环境能力探测脚本；
- [ ] 创建 Spec 验证脚本；
- [ ] 创建用户确认门禁；
- [ ] 创建输出目录约定；
- [ ] 通过 Skill quick validation。

建议验证命令：

```powershell
python C:\Users\Administrator\.codex\skills\.system\skill-creator\scripts\quick_validate.py `
  D:\miniQMT策略实盘\QuantStudio\skills\quantstudio-strategy-compiler
```

## 7.30 SKILL.md 核心职责

- 何时触发；
- 必须先运行哪个能力检查；
- 何时读取哪个 reference；
- 多轮交互顺序；
- 用户确认硬闸门；
- 何时允许生成代码；
- 何时必须停止；
- 输出和验证要求。

## 7.31 验收标准

- Skill 元数据有效；
- 在无上下文的新会话中能正确触发；
- 不读取不必要的大文档；
- 能区分日线、分钟、代理和 Planned Profile；
- 用户未确认 R2.5 时不生成代码；
- 能力非 READY 时不宣称冒烟通过。

---

# PR6：策略 IR、双目标渲染和自动验证

## 7.32 目标

完成真正的“编译器”功能。

## 7.33 IR 设计

IR 节点建议：

```text
UniverseNode
HardFilterNode
DataLoadNode
IndicatorNode
FactorNode
SignalNode
RankingNode
PortfolioNode
RiskNode
ExecutionNode
DiagnosticNode
```

每个节点包含：

- 输入；
- 输出；
- 参数；
- 所需能力；
- 时序；
- 平台映射；
- 验证规则。

## 7.34 Renderer

QuantStudio Renderer：

- 只调用注入 API；
- 通过 StrategyIsolationGuard；
- 输出到 `quantstudio/backtest/strategies/` 或独立生成目录；
- 可被 GUI 自动扫描。

PTrade Renderer：

- 只使用 Profile 白名单 API；
- 按 Profile 决定 import；
- 按 Profile 决定代码后缀；
- 不泄漏本地批量 API，除非 Profile 明确支持。

## 7.35 自动验证脚本

至少实现：

```text
validate_strategy_spec.py
validate_local_strategy.py
validate_ptrade_portability.py
scan_lookahead.py
check_hard_filters.py
compare_strategy_variants.py
run_smoke_backtest.py
```

## 7.36 双版本一致性检查

比较：

- 策略参数；
- 股票池；
- 硬过滤；
- 指标参数；
- 买入条件；
- 卖出条件；
- 调仓频率；
- 持仓数；
- 仓位；
- 止损止盈；
- 信号数据截止时间；
- 成交时点；
- 成本和滑点；
- API 能力差异。

不能只做文本 diff，应比较 Spec→IR→Renderer 映射清单。

## 7.37 验收策略集

至少建立以下黄金样例：

1. 日线双均线单股票；
2. 日线 ETF 动量轮动；
3. 日线小市值选股，包含全部硬过滤；
4. 日线多因子 TopN；
5. 09:35 日线开盘代理策略；
6. 真实 1m 定时策略；
7. `next_open` 延迟执行策略；
8. 依赖缺失行业数据的失败策略；
9. PTrade Profile 不支持 API 的失败策略；
10. Tick Spec 可生成但执行被 PLANNED 门禁阻断。

## 7.38 验收标准

- 所有黄金样例通过对应门禁；
- 双版本参数和逻辑一致；
- 本地版本可加载；
- READY Profile 冒烟通过；
- 非 READY Profile 明确阻断；
- 未来函数扫描无高危项；
- 硬过滤检查通过；
- Skill quick validation 通过；
- 全量回归测试通过。

---

# PR7：PTrade Fidelity 闭环和稳定性加固

## 7.39 目标

把“生成代码”升级为“生成、运行、对照、定位差异”的完整闭环。

## 7.40 任务清单

- [ ] 将生成策略输出与现有 Fidelity Comparator 接通；
- [ ] 统一本地/PTrade Run Card；
- [ ] 对比信号、订单、成交、持仓、净值和成本；
- [ ] 输出差异原因分类；
- [ ] 区分数据源差异、时序差异、撮合差异和策略逻辑差异；
- [ ] 建立可接受误差阈值；
- [ ] 建立回归基准策略；
- [ ] 建立性能基准；
- [ ] 建立失败重现包。

## 7.41 验收标准

- 至少完成日线和分钟各一个真实 PTrade 对照样例；
- 差异报告可定位到 Spec、数据、API 或撮合层；
- 不把跨数据源的合理排名差异误判为代码错误；
- 生成结果可复现；
- 所有报告带版本和数据区间。

---

# PR8：GUI 和配置集成（后续扩展，不阻塞第一版）

## 7.42 目标

实现生成策略的一键导入和参数自动填充。

## 7.43 功能

- `strategy_config.json` 识别；
- 策略参数自动填入 GUI；
- 生成策略刷新；
- Spec 版本检查；
- Engine Profile 选择；
- 能力状态显示；
- 代理模式警告；
- PTrade 导出入口；
- Fidelity 结果展示。

---

# PR9：Tick、盘口和高频执行能力（长期扩展）

## 7.44 前置条件

- Tick/逐笔/盘口数据源和入库完成；
- Provider 支持事件流；
- 精确交易时间和序列号；
- L1/L2 字段契约；
- 高频订单和撤单模型；
- 成交队列、部分成交和流动性模型；
- 性能和内存基准。

## 7.45 扩展原则

保持 Strategy Spec 和 IR 向后兼容，只增加：

```text
TickDataNode
OrderBookNode
MicrostructureSignalNode
LimitOrderNode
CancelOrderNode
PartialFillNode
LatencyNode
MarketImpactNode
```

日线和分钟策略不得因高频扩展而修改源码或结果语义。

---

## 8. 数据门禁设计

`inspect_capabilities.py` 应至少检查：

1. 数据库文件存在且可读；
2. 表是否存在；
3. 表是否非空；
4. 数据覆盖策略回测区间；
5. 股票池覆盖率；
6. 关键字段完整；
7. 频率值存在；
8. 复权列可用；
9. PIT 字段可用；
10. 状态字段可用；
11. Provider 路由支持；
12. Engine Profile 支持；
13. PTrade Profile 支持；
14. 输出目录可写。

示例阻断：

```text
策略依赖：stock_minutes 1min，2025-01-01 至 2026-06-30
检测结果：表存在，但数据覆盖仅到 2025-03-31
执行状态：BLOCKED
处理建议：先补拉 2025-04-01 至 2026-06-30 数据
```

---

## 9. 防未来函数与时序门禁

静态检查与语义检查共同覆盖：

- `before_trading_start` 使用当日完整 close；
- T 日 close 形成信号并以同一 close 成交；
- 使用当前完整日线的 high/low 判断盘中触发；
- 排名或基本面使用当前日未来可得数据；
- `include=True` 使用不当；
- 财务数据未按公告日 PIT；
- `next_open` 订单在 T 日提前入账；
- 日线代理模式的撮合口径不一致；
- 分钟信号错误使用未来分钟；
- 1m 聚合 5m 时提前看到尚未结束的 5m Bar。

高危项必须阻断生成或回测，不只发出警告。

---

## 10. 测试策略

### 10.1 测试层级

1. 单元测试：规则、Schema、路由、订单；
2. 契约测试：Provider、Engine、PTrade Profile；
3. 集成测试：Spec→IR→双 Renderer；
4. 冒烟测试：短区间可执行；
5. 回归测试：旧策略和旧结果；
6. Fidelity 测试：本地 vs PTrade；
7. 性能测试：分钟全市场或目标股票池；
8. 故障注入：空表、缺字段、缺日期、不可写目录。

### 10.2 每个 PR 的固定验证顺序

```powershell
# 1. 新增专项测试
python -m pytest -q <new_tests>

# 2. 回测核心测试
python -m pytest -q `
  tests/test_strategy_runner.py `
  tests/test_order_rejection.py `
  tests/test_filter_stock_by_status.py `
  tests/test_match_price_mode.py

# 3. 全量测试
python -m pytest -q

# 4. 对应冒烟策略
python -m quantstudio.backtest.run_ptrade_strategy <strategy.py> <start> <end>
```

### 10.3 禁止事项

- 不得用删除或放宽测试来使 PR 通过；
- 不得将真实错误改为 warning 后继续；
- 不得使用日线数据伪装分钟数据；
- 不得将空股票池视为成功；
- 不得在策略文件中直接访问 DuckDB；
- 不得独立修改两份策略使对照通过，应修复 Spec/IR/Renderer；
- 不得在未验证的 Profile 上报告 PASS。

---

## 11. 兼容性和迁移策略

### 11.1 旧策略

- 保持现有 PTrade 生命周期；
- 保持零 import 注入；
- 保持已有代码后缀兼容；
- 保持 `close/open` 默认行为，除非发现明确错误；
- `next_open` 修正可能改变历史结果，必须在 Changelog 和 Run Card 标记语义版本。

### 11.2 版本字段

建议增加：

```text
strategy_spec_version
engine_semantics_version
provider_contract_version
security_code_rules_version
ptrade_profile_version
renderer_version
skill_version
```

任何影响回测结果语义的修改必须升级相应版本。

### 11.3 回滚

每个阶段：

- 修改前记录全量测试结果；
- 每个 PR 写清变更文件；
- 保持小步提交；
- 新旧语义如需并存，使用显式版本/Profile，不使用隐藏开关；
- 回滚不删除生成策略和历史 Run Card。

---

## 12. 稳定运行标准

系统只有同时满足以下条件，才可以称为“稳定运行”：

### 正确性

- Strategy Spec 验证通过；
- 时序无高危未来函数；
- T+1、涨跌停、停牌和成本正确；
- 双版本逻辑一致；
- READY Profile 冒烟通过。

### 可复现性

- 保存 Spec、IR、代码、Run Card、版本号和数据区间；
- 相同输入、相同数据和相同版本产生相同输出；
- 结果目录不依赖临时状态。

### 可诊断性

- 订单拒绝有原因；
- 能力门禁有修复建议；
- 空股票池能定位是策略结果还是数据缺失；
- PTrade 差异能定位到数据/API/时序/撮合/逻辑。

### 兼容性

- 旧日线策略继续运行；
- 日线结果无无意漂移；
- 分钟扩展不破坏日线；
- Tick 扩展不破坏日线和分钟。

### 测试

- 全量测试通过；
- 黄金策略集通过；
- 日线/分钟冒烟通过；
- Skill quick validation 通过；
- 至少一组真实 PTrade 对照通过预设门槛。

---

## 13. Codex 后续执行协议

后续 Codex 应按以下规则执行本计划：

1. 每次只推进一个 PR/阶段，除非任务之间完全独立且用户允许并行；
2. 开始阶段前读取本计划和当前 `implementation-status.md`；
3. 先检查代码和测试基线，再修改；
4. 不跳过 PR0；
5. 每个阶段先实现最小契约测试，再实现代码；
6. 每个阶段结束必须运行专项测试和全量测试；
7. 更新实施状态、变更文件、测试结果和遗留风险；
8. 未达到验收标准不得把阶段标记完成；
9. 如果发现本计划与代码现实冲突，先记录差异并向用户说明，再修改契约；
10. 不为追求进度生成伪分钟、伪 Tick 或静默降级结果。

### 每阶段交付报告模板

```text
阶段：PRx
状态：PASS / PARTIAL / BLOCKED
完成内容：
修改文件：
新增测试：
测试结果：
兼容性影响：
已知限制：
下一阶段前置条件：
```

---

## 14. 总体执行检查表

### 第一里程碑：契约和日线语义稳定

- [ ] PR0 契约冻结；
- [ ] PR1 证券代码规则统一；
- [ ] PR2 next_open 时序修正；
- [ ] 日线回归和 Fidelity 通过。

### 第二里程碑：分钟引擎可执行

- [ ] PR3 多频率 Provider；
- [ ] 分钟数据完整性门禁；
- [ ] PR4 分钟事件引擎；
- [ ] 股票/ETF 分钟黄金策略通过。

### 第三里程碑：Skill 编译器可用

- [ ] PR5 Skill 骨架；
- [ ] 多轮交互；
- [ ] Spec 和用户确认闸门；
- [ ] PR6 IR 和双 Renderer；
- [ ] 静态验证；
- [ ] 日线/分钟冒烟；
- [ ] 默认 PTrade Profile。

### 第四里程碑：双平台闭环稳定

- [ ] PR7 Fidelity 闭环；
- [ ] 日线真实 PTrade 对照；
- [ ] 分钟真实 PTrade 对照；
- [ ] 性能和故障注入测试；
- [ ] 稳定运行验收。

### 后续里程碑

- [ ] PR8 GUI 配置集成；
- [ ] PR9 Tick/L2/高频 Provider；
- [ ] Tick/L2/高频执行引擎；
- [ ] 高频 PTrade/目标平台 Profile；
- [ ] 高频稳定性和性能验收。

---

## 15. 下一步

下一次开始实施时，严格从 **PR0：冻结契约与测试基线** 开始，第一批具体动作应为：

1. 运行当前全量测试并保存基线；
2. 创建 `docs/strategy-compiler/`；
3. 编写 Strategy Spec v1、Time Model、Engine Profile 和能力模型；
4. 创建三个 JSON Schema；
5. 新增契约测试；
6. 输出 PR0 实施报告，用户确认后进入 PR1。

本文件是后续逐步实施和完善 QuantStudio Strategy Compiler Skill 的主计划。所有阶段状态、验收结果和计划变更均应回写到项目内的 `docs/strategy-compiler/implementation-status.md`，本文件只在总体范围或实施顺序发生重大变更时升级版本。
