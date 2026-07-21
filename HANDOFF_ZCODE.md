# QuantStudio 项目移交说明（zcode 接手入口）

> 移交日期：2026-07-21  
> 当前仓库：`https://github.com/yangyizhu8/quantstudio-backup-20260721`（Private）  
> 本地目录：`D:\miniQMT策略实盘\QuantStudio`  
> 默认分支：`main`  
> 当前基线提交：以 `git log -1` 为准；移交文档提交后应为远端最新 `main`  
> 当前全量测试：**249 passed in 15.29s**  
> 当前主阶段：**PR1 已完成，下一步先做 PR0.5 稳定性门禁，再进入 PR2**

---

## 1. 接手后先做什么

zcode 克隆或打开项目后，按顺序执行：

```powershell
cd D:\miniQMT策略实盘\QuantStudio
git status --short --branch
git log --oneline --decorate -5
python -m pytest -q
```

预期：

```text
工作树干净
main 跟踪 origin/main
249 passed
```

必须先阅读以下文件，优先级从高到低：

1. `HANDOFF_ZCODE.md`（本文件）；
2. `docs/strategy-compiler/master-implementation-plan-v1.0.md`（冻结总计划）；
3. `docs/strategy-compiler/implementation-status.md`（当前阶段状态）；
4. `docs/strategy-compiler/pr0-implementation-report.md`；
5. `docs/strategy-compiler/pr1-implementation-report.md`；
6. `docs/ETF-momentum-regression-20260720.md`（最重要的回归事故与永久约束）；
7. `docs/interface-contract.md`；
8. `docs/strategy-development.md`；
9. `docs/architecture-compliance-audit-20260720.md`。

不要只读 README 后直接修改引擎。

---

## 2. 项目目标

目标是建设 **QuantStudio Strategy Compiler Skill**：用户输入自然语言策略思路后，通过多轮交互生成 Strategy Spec，再从同一个 Spec/IR 生成：

- QuantStudio 本地可回测策略；
- 指定 PTrade Profile 的可运行策略；
- Capability Report、Run Card、静态检查和双版本一致性报告；
- READY Profile 的本地冒烟回测；
- PTrade 导出结果的 Fidelity 对照。

核心流水线：

```text
自然语言
→ 能力探测
→ 多轮需求澄清
→ Strategy Spec（唯一事实源）
→ 用户确认硬闸门
→ Strategy IR
→ QuantStudio/PTrade 双 Renderer
→ AST/时序/API/硬过滤/一致性检查
→ 冒烟回测
→ Run Card
→ PTrade Fidelity
```

这是“编译器式 Skill”，不是把所有 API 和提示词堆入一个超长 `SKILL.md`。

---

## 3. 当前框架能力

### 已具备

- PTrade 生命周期：`initialize`、`before_trading_start`、`handle_data`、`after_trading_end`；
- 统一 API 注入：`quantstudio/backtest/ptrade_import.py`；
- 策略隔离门禁：`StrategyIsolationGuard`；
- Provider 解耦：`DataProviderRegistry`；
- DuckDB 日线行情和基本面访问；
- T+1、涨跌停、100 股整手、佣金、最低佣金、印花税、过户费和滑点；
- ST、停牌、退市、退市风险过滤；
- 股票/ETF 日线回测；
- 股票/ETF 分钟采集配置、Schema、字段对齐和质量检查；
- PTrade 导出导入和 L1-L4 Fidelity Comparator；
- PyQt 回测入口和 CLI 回测入口。

### 尚未完成

- `next_open` 真实 pending-order 时序；
- Provider frequency 参数贯通；
- `stock_minutes` / `etf_minutes` 回测查询路由；
- 分钟事件循环和分钟调度器；
- 正式 Skill 骨架；
- Strategy IR 和双 Renderer；
- 自动能力探测、静态检查和冒烟编排；
- GUI `strategy_config.json` 自动导入；
- Tick/L2/高频执行引擎。

---

## 4. 已完成阶段

## PR0：契约与测试基线 — PASS

产出：

- Strategy Spec v1；
- Time Model；
- Engine Profile；
- 多维 Capability Model；
- 默认 PTrade Profile；
- A 股硬过滤契约；
- 日线代理契约；
- Run Card 和输出契约；
- 三个 JSON Schema；
- 合法示例和跨字段验证函数；
- 19 个契约测试。

核心目录：

```text
quantstudio/strategy_compiler/
├── contracts.py
├── schemas/
└── examples/
```

版本：

```text
strategy_spec_version       1.0.0
engine_semantics_version    0.1.0-legacy
provider_contract_version   0.1.0-daily
ptrade_profile_version      1.0.0-default
renderer_version            0.0.0-planned
skill_version               0.0.0-planned
```

## PR1：证券代码权威规则 — PASS

产出：

- `quantstudio/backtest/libs/security_code_rules.py`：唯一运行时分类和后缀权威；
- `bse_legacy_code_mapping.json`：北交所 248 条官方旧码精确映射；
- `.SH/.SS/.XSHG/.SZ/.XSHE/.BJ/.XBJ/.XBSE/裸码` 兼容；
- 主板、创业板、科创板、北交所、ETF、指数、可转债分类；
- 不再将全部 4/8 开头代码一律视为北交所；
- 证券代码专项和边界测试；
- 生成式规则文档和官方映射快照。

权威来源：

```text
https://www.bse.cn/service/code_mapping.html
```

当前 `security_code_rules_version = 1.0.0`。

---

## 5. 最重要的回归事故：ETF 动量

PR1/后缀统一过程中曾把：

```python
context.portfolio.positions
get_positions()
```

改成 `.XSHG/.XSHE` 且 alias-aware 的 `CodeDict`，这改变了真实 PTrade 的 Python membership 语义：

```python
code in context.portfolio.positions
```

结果 ETF 动量从已对齐的约 -12.25% 漂移成 -50.94%，交易从 3 笔变成 31 笔。

### 已修复并冻结的语义

- `context.portfolio.positions`：普通 `dict`；
- key 使用真实 PTrade 策略/CSV 格式 `.SS/.SZ`；
- `in positions` 是精确 membership，不跨后缀；
- 跨后缀兼容只允许存在于：
  - `get_position()`；
  - `DataDict`；
  - `CodeDict`；
  - 行情/历史显式 API。

**永久禁止再次把 `portfolio.positions` 改成 alias-aware 容器。**

详细报告：`docs/ETF-momentum-regression-20260720.md`。

### 当前黄金结果

ETF 动量，2026-01-01 至 2026-07-13，10 万元，close，无滑点，ETF 费率：

```text
最终资金       87,752.56
总收益率       -12.2474%
最大回撤       23.3294%
交易笔数       3
Fidelity       PASS
L1             100%
L3             100%
末态净值偏差   0.0044%
```

PTrade 样本目录（本机外部数据，不在 Git）：

```text
D:\miniQMT策略实盘\私募工作文件\ptrade_samples\ETF动量ptrade
```

本地对照报告位于被 Git 忽略的：

```text
output/compare_ETF_momentum_final_regression_fix.json
```

小市值对照基准：

```text
Verdict          CLOSE
L1               72.31%
末态净值偏差     0.2635%
最大回撤偏差     1.7128%
L3               95.24%
```

小市值样本目录：

```text
D:\miniQMT策略实盘\私募工作文件\ptrade_samples\小市值ptrade
```

---

## 6. 当前测试与 Git 状态

移交前重新执行：

```text
249 passed in 15.29s
```

GitHub 私有仓库：

```text
https://github.com/yangyizhu8/quantstudio-backup-20260721
```

该仓库是新增备份项目，没有覆盖：

```text
yangyizhu8/trading-battle-back
```

`.gitignore` 已排除：

- 数据库和 `data/`；
- `output/`；
- 日志；
- `config/secrets.env`；
- Python 缓存和虚拟环境；
- 本地 AI/编辑器状态。

因此真实 DuckDB、PTrade CSV/Log 和历史回测输出不在仓库中。需要在本机用现有绝对路径，或由用户单独提供数据。

---

## 7. 接下来不能直接做 PR2：先做 PR0.5 稳定性门禁

冻结主计划原顺序是 PR1 → PR2，但 ETF 回归事故证明，仅靠 pytest 不足以防止策略结果漂移。

下一阶段必须先插入 **PR0.5 / Stability Gate**，然后才允许 PR2。

### PR0.5 目标

建立已对齐策略的黄金基准、数据指纹和自动回归阻断。

### 最少黄金策略

1. ETF 动量；
2. 小市值 PTrade；
3. 双均线；
4. ETF 轮动；
5. 无交易策略；
6. 涨停拒单策略；
7. T+1 策略。

### 每个黄金基准应保存

- 策略 SHA256；
- 引擎/Provider/规则模块 SHA256；
- 策略参数、区间、成本、撮合模式；
- 数据表 row_count/min/max/content hash；
- 交易序列；
- 每日持仓；
- 每日 NAV；
- 最终净值、回撤、交易笔数；
- PTrade L1-L4 指标；
- 容差和是否必须 exact。

### 必须区分

```text
代码 hash 变、数据 hash 不变 → 代码回归
代码 hash 不变、数据 hash 变 → 数据漂移
代码/数据均不变、结果变化 → 非确定性或状态泄漏
```

### 每个后续 PR 的强制流程

```text
修改前全量 pytest + 黄金回归 + 数据指纹
→ 只修改当前 PR 范围
→ 专项测试
→ 全部日线黄金回归
→ ETF/PTrade Fidelity
→ 小市值 Fidelity
→ 全量 pytest
→ 全部通过才允许进入下一 PR
```

非通过状态必须标记 `BLOCKED`，不得继续。

---

## 8. PR0.5 建议落地内容

建议新增：

```text
quantstudio/regression/
├── fingerprints.py
├── golden_manifest.py
├── runner.py
└── comparator.py

config/golden_regressions/
├── etf_momentum.json
├── small_cap.json
├── double_ma.json
└── ...

scripts/run_golden_regressions.py

tests/test_golden_manifest.py
tests/test_data_fingerprint.py
tests/test_golden_comparator.py
```

黄金结果的机器可读产物建议放：

```text
tests/golden/
```

但不要提交真实 PTrade 原始 CSV/Log，除非用户确认数据可上传。可以提交脱敏摘要和 hash。

建议命令：

```powershell
python scripts/run_golden_regressions.py --suite daily-core
```

完成后更新：

- `docs/strategy-compiler/implementation-status.md`；
- 新建 `docs/strategy-compiler/pr0.5-stability-gate-report.md`；
- 全量测试；
- 单独 Git commit；
- 推送 GitHub；
- 等用户确认后再进入 PR2。

---

## 9. PR2 边界（PR0.5 后执行）

PR2 只处理 `next_open`，不得混入新的证券代码、Provider frequency 或分钟引擎修改。

当前 legacy 行为在 T 日预取 T+1 open，并在 T 日立即改现金、持仓和净值；这是错误的。

目标：

```text
T 日信号
→ pending order
→ T 日现金/持仓/NAV 不变
→ T+1 开盘用 T+1 状态和 open 检查停牌/涨跌停/资金
→ T+1 成交或拒单
→ 成交日期和账本更新为 T+1
```

PR2 必须保证：

- `close` 模式逐日结果不变；
- `open` 模式逐日结果不变；
- 只有 `next_open` 语义升级；
- 旧语义如需保留，必须显式叫 `legacy_next_open` 或使用语义版本，不得隐藏兼容；
- ETF 动量 Fidelity 继续 PASS；
- 小市值指标不越过冻结容差。

---

## 10. PR3/PR4 的关键事实

当前分钟能力不是“只差入库”。

已有：

- `stock_minutes` / `etf_minutes` 采集任务；
- 分钟 Schema；
- 分钟字段对齐；
- 复权和质量测试；
- Tick Schema 预留。

缺少：

- Provider `frequency` 参数；
- `get_history(unit='1m')` 真正路由分钟表；
- `get_price(frequency='1m')` 真正路由分钟表；
- 日内 Bar 事件流；
- 分钟 `run_daily(time=...)` 调度；
- 分钟撮合和日内多次下单。

PR3 应优先保留现有日线路径原样：

```python
if frequency == "1d":
    return existing_daily_implementation(...)
```

不要为了抽象统一重写日线查询。

PR4 应先增加 `DailyBarEventStream` / `MinuteBarEventStream` 或等价 Profile，不要一开始大规模重写当前日线主循环。

---

## 11. Skill 阶段的硬约束

正式 Skill 源码建议放：

```text
skills/quantstudio-strategy-compiler/
```

安装目录只是发布产物，不作为唯一源码。

Skill 必须：

- 参考 `simple-quant-factory` 采用多轮交互；
- R2 先生成 Spec，不直接生成代码；
- R2.5 用户确认前禁止渲染；
- 双版本只能从同一 Spec/IR 渲染；
- 能力非 READY 时可以生成草案，但不得宣称冒烟通过；
- `daily_open_proxy` 必须对应 `match_price_mode=open`；
- 09:35 等代理必须记录 approximation 和 user_confirmed；
- 禁止空股票池因数据缺失而静默回测成功；
- 不自动覆盖已有策略；
- 黄金策略 `ETF动量.py`、`小市值策略ptrade.py`、`双均线策略.py` 加保护清单；
- 生成策略先落入 `output/generated_strategies/<strategy_id>/`，验证和确认后再复制到正式策略目录。

---

## 12. 数据和本地环境

数据配置：

```text
config/data_config.json
```

当前指向：

```text
D:/miniQMT策略实盘/_runtime/data/quantstudio.db
D:/miniQMT策略实盘/_runtime/data/quarantine.db
```

敏感信息来自环境变量或被忽略的：

```text
config/secrets.env
```

不要把真实 Token 提交到 Git。

CLI 在部分 Windows GBK 环境打印 `✓/❌` 会触发 `UnicodeEncodeError`。运行 CLI 对照时建议：

```powershell
$env:PYTHONUTF8='1'
# 或
$env:PYTHONIOENCODING='utf-8'
```

这是输出编码问题，不是回测语义错误。

---

## 13. 常用验证命令

全量：

```powershell
python -m pytest -q
```

ETF 真实 PTrade 对照：

```powershell
$env:PYTHONUTF8='1'
python -m quantstudio.backtest.run_ptrade_strategy `
  "quantstudio/backtest/strategies/ETF动量.py" `
  2026-01-01 2026-07-13 `
  --match-price close `
  --compare `
  --ptrade-dir "D:/miniQMT策略实盘/私募工作文件/ptrade_samples/ETF动量ptrade" `
  --output "output/compare_ETF_momentum_handoff_check.json"
```

预期 `PASS`，L1/L3 100%。

小市值对照：

```powershell
$env:PYTHONUTF8='1'
python -m quantstudio.backtest.run_ptrade_strategy `
  "quantstudio/backtest/strategies/小市值策略ptrade.py" `
  2026-01-01 2026-04-29 `
  --match-price close `
  --compare `
  --ptrade-dir "D:/miniQMT策略实盘/私募工作文件/ptrade_samples/小市值ptrade" `
  --output "output/compare_smallcap_handoff_check.json"
```

预期 `CLOSE`，不是 PASS；关键是保持净值偏差约 0.26% 和持仓重叠约 95%。

---

## 14. Git 工作协议

当前已使用 Git，之前文档中“不是 Git 仓库”的说明已经过时。

后续每个阶段：

1. `git status` 确认干净；
2. 修改前运行基线；
3. 只做一个阶段；
4. 运行专项、黄金回归、全量测试；
5. 更新 `implementation-status.md` 和阶段报告；
6. 创建独立 commit；
7. 推送 `origin/main`；
8. 用户确认后进入下一阶段。

建议不要 force push，不要覆盖原仓库，不要修改 `trading-battle-back`。

远端：

```text
origin  https://github.com/yangyizhu8/quantstudio-backup-20260721.git
```

---

## 15. 已知文档不一致

- 冻结总计划首部仍写“尚未开始运行时代码或 Skill”，这是创建计划时的历史描述；实际 PR0/PR1 已完成。以 `implementation-status.md` 和本移交说明为准。
- PR0/PR1 旧报告中可能写“目录不是 Git 仓库”，现已初始化并推送 GitHub，该说法过时。
- `output/` 被忽略，报告 JSON 不在 GitHub；关键指标已写入本文件和 ETF 回归报告。

接手时不要因为这些历史描述误判阶段。

---

## 16. 建议的第一项工作

**不要直接实现 `next_open`。**

第一项工作是：

```text
PR0.5 Stability Gate
```

交付要求：

- 黄金基准清单；
- 数据/代码/结果指纹；
- 自动比较器；
- `daily-core` 自动回归命令；
- ETF 动量真实样本强门禁；
- 小市值容差门禁；
- 新阶段报告；
- 全量测试通过；
- 独立 Git 提交并推送；
- 等用户确认。

只有 PR0.5 通过后才开始 PR2。
