# QuantStudio Strategy Compiler 项目移交手册（ZCode）

> 生成日期：2026-07-21  
> 项目根目录：`D:\miniQMT策略实盘\QuantStudio`  
> 面向接手者：ZCode  
> 当前阶段：**PR1 技术完成 + Fidelity 防漂移加固完成，等待确认后进入 PR2**

---

## 0. 接手结论（先看这一节）

当前项目已经完成：

1. **PR0：契约冻结与测试基线**；
2. **PR1：证券代码分类/后缀统一**；
3. PR1 之后针对 ETF 动量策略发生的严重回测漂移，已经完成**真实 PTrade Fidelity 回归加固**。

当前**不要直接开始 PR2 编码**，除非用户明确确认 PR1/Fidelity 加固通过。

接手后的第一目标不是重新设计，而是：

```text
阅读本移交手册
→ 运行现有测试和 Fidelity 门禁
→ 确认工作区状态与报告一致
→ 获得/确认 PR1→PR2 授权
→ 严格按主计划实施 PR2
```

核心原则：

> `pytest` 全部通过，不代表策略行为没有漂移。任何涉及持仓、代码后缀、订单、撮合、生命周期、Provider 或 GUI 参数的修改，都必须重新运行真实 ETF + 小市值 Fidelity 门禁。

---

## 1. 权威资料阅读顺序

### 1.1 必读主计划

```text
D:\miniQMT策略实盘\私募工作文件\本地回测框架策略开发skill的实施方案和计划\QuantStudio_Strategy_Compiler_Skill_实施方案与执行计划_v1.0.md
```

这是总体实施顺序和验收标准的唯一主计划。

### 1.2 项目内实施状态

```text
D:\miniQMT策略实盘\QuantStudio\docs\strategy-compiler\implementation-status.md
```

当前状态、版本、测试结果、限制和 PR2 闸门以该文件为准。

### 1.3 PR1 报告

```text
D:\miniQMT策略实盘\QuantStudio\docs\strategy-compiler\pr1-implementation-report.md
```

### 1.4 ETF 漂移根因报告

```text
D:\miniQMT策略实盘\QuantStudio\docs\ETF-momentum-regression-20260720.md
```

必须理解这次事故：曾经把 `context.portfolio.positions` 改成 alias-aware 容器，导致 ETF 动量策略控制流改变，成交由 3 笔变为 31 笔，最终资金由约 87,752.56 漂移至 49,064.37。

### 1.5 Fidelity 强制门禁契约

```text
D:\miniQMT策略实盘\QuantStudio\docs\strategy-compiler\strategy-fidelity-regression-gate.md
```

### 1.6 PR1 后的真实门禁配置和执行器

```text
D:\miniQMT策略实盘\QuantStudio\config\strategy_fidelity_gates.json
D:\miniQMT策略实盘\QuantStudio\scripts\run_strategy_fidelity_gates.py
D:\miniQMT策略实盘\QuantStudio\tests\test_strategy_fidelity_gates.py
```

---

## 2. 当前阶段状态

| 阶段 | 状态 | 说明 |
|---|---|---|
| PR0 | PASS | Schema、契约、示例和 220 项全量基线完成，用户已确认 |
| PR1 | PASS / WAITING_CONFIRMATION | 证券代码权威模块、北交所规则、调用点迁移和回归完成 |
| PR1 Fidelity 加固 | PASS / WAITING_CONFIRMATION | ETF 动量真实 Fidelity PASS，小市值防退化门禁通过 |
| PR2 | NOT_STARTED | `next_open` 仍为 legacy，不能提前宣称完成 |
| PR3 | NOT_STARTED | 多频率 Provider 尚未打通，分钟能力 BLOCKED |
| PR4 | NOT_STARTED | 分钟事件引擎尚未实现 |
| PR5 | NOT_STARTED | 正式 Strategy Compiler Skill 尚未创建 |
| PR6 | NOT_STARTED | IR、双 Renderer、静态验证尚未实现 |
| PR7 | NOT_STARTED | Fidelity 闭环工程化尚未完成 |

当前契约版本：

```text
strategy_spec_version       = 1.0.0
engine_semantics_version    = 0.1.0-legacy
provider_contract_version   = 0.1.0-daily
security_code_rules_version = 1.0.0
ptrade_profile_version      = 1.0.0-default
renderer_version            = 0.0.0-planned
skill_version               = 0.0.0-planned
```

---

## 3. PR0 已完成内容

PR0 的主要交付物：

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
├── security-code-rules.md
├── strategy-fidelity-regression-gate.md
├── implementation-status.md
├── pr0-implementation-report.md
└── pr1-implementation-report.md
```

Schema 和示例：

```text
D:\miniQMT策略实盘\QuantStudio\quantstudio\strategy_compiler\schemas\
├── strategy_spec.schema.json
├── capability_report.schema.json
└── run_card.schema.json

D:\miniQMT策略实盘\QuantStudio\quantstudio\strategy_compiler\examples\
├── strategy_spec.example.json
├── capability_report.example.json
└── run_card.example.json
```

---

## 4. PR1 已完成内容

### 4.1 唯一权威证券代码模块

```text
D:\miniQMT策略实盘\QuantStudio\quantstudio\backtest\libs\security_code_rules.py
```

主要导出：

```python
classify_security
normalize_security_code
normalize_to_qmt
normalize_to_ptrade
is_main_board
is_chinext
is_chinext_market
is_star_market
is_bse_market
is_etf
is_convertible_bond
is_index
is_st_stock
```

### 4.2 后缀规则

支持：

```text
上海：.SH / .SS / .XSHG / 裸码
深圳：.SZ / .XSHE / 裸码
北京：.BJ / .XBJ / .XBSE / 裸码
```

公共输出：

```text
QMT     上海 .SH，深圳 .SZ，北交所 .BJ
PTrade  上海 .SS，深圳 .SZ，北交所 .BJ
```

### 4.3 北交所规则

不能恢复旧的宽泛规则：

```python
code.startswith(("8", "4"))
```

当前规则：

- 当前北交所股票：`920xxx`；
- 历史北交所代码：只按官方 248 条精确映射识别；
- `400xxx`、`420xxx`、未映射 `8xxxxx` 不自动认定为北交所；
- 项目数据库中的未映射 `832/833` 历史样本不使用北交所 30% 涨跌停语义。

官方映射快照：

```text
D:\miniQMT策略实盘\QuantStudio\docs\strategy-compiler\sources\bse-official-code-mapping-20260720.json
```

打包映射：

```text
D:\miniQMT策略实盘\QuantStudio\quantstudio\backtest\libs\bse_legacy_code_mapping.json
```

### 4.4 已迁移调用点

- `quantstudio/backtest/libs/shared_ashare_rules.py`
- `quantstudio/backtest/ptrade_api.py`
- `quantstudio/backtest/backtest_engine.py`
- `quantstudio/backtest/providers/duckdb_data_access.py`
- `quantstudio/backtest/providers/duckdb_provider.py`

`ptrade_api.py`、BacktestEngine、Provider 目标文件中已通过 AST 检查，独立数字 `startswith()` 市场分类规则为 0 个。

---

## 5. 最重要的持仓容器语义

这是接手后最不能破坏的契约。

### 5.1 原生持仓容器

```python
context.portfolio.positions
```

必须是：

```python
builtins.dict
```

必须保持：

```text
普通 Python dict
精确 key membership
公开 key 使用 .SS/.SZ/.BJ
```

例如：

```python
"159870.SZ" in context.portfolio.positions      # True
"159870.XSHE" in context.portfolio.positions    # False
```

这不是缺陷，而是为了复现真实 PTrade 策略控制流。

### 5.2 可以跨后缀兼容的接口

以下接口可以提供 alias-aware 查询：

```text
get_position()
get_positions()
DataDict
CodeDict
get_history()
get_price()
order_target_value()
```

但是不能把 alias-aware 语义扩散到 `context.portfolio.positions` 本身。

### 5.3 禁止事项

禁止：

- `Portfolio.positions = CodeDict(...)`；
- `Portfolio.positions = AliasAwareDict(...)`；
- 用 `.XSHG/.XSHE` 作为公开持仓容器 key；
- 为了 API 后缀统一而改变 `code in context.portfolio.positions` 的结果；
- 只跑 pytest，不跑真实 ETF Fidelity。

---

## 6. 当前真实 Fidelity 基线

### 6.1 ETF 动量硬基线

样本目录：

```text
D:\miniQMT策略实盘\私募工作文件\ptrade_samples\ETF动量ptrade
```

包含：

```text
Log.txt
交易详情*.csv
持仓明细*.csv
```

当前冻结结果：

```text
最终资金：87,752.56 ± 1
成交笔数：3
最后买入：159870
Fidelity：PASS
L1：100%
L3：100%
```

精确成交序列：

```text
2026-01-05 buy  515880
2026-01-07 sell 515880
2026-01-07 buy  159870
```

已知坏结果必须被门禁拒绝：

```text
最终资金约 49,064.37
成交笔数 31
```

### 6.2 小市值防退化基线

样本目录：

```text
D:\miniQMT策略实盘\私募工作文件\ptrade_samples\小市值ptrade
```

当前允许的跨源 `CLOSE` 基线：

```text
最终资金：118,551.21 ± 10
成交笔数：57
L1 >= 72%
NAV 偏差 <= 0.30%
回撤偏差 <= 1.80%
夏普偏差 <= 0.03
L3 >= 95%
L4 <= 0.30%
```

`CLOSE` 不是无条件通过；必须同时满足以上子指标。

### 6.3 最新真实门禁结果

```text
D:\miniQMT策略实盘\QuantStudio\output\strategy-fidelity-gates\current-full-run\summary.json
```

结果：

```text
ETF momentum  : PASS
smallcap guard: CLOSE accepted inside frozen envelope
```

---

## 7. 当前测试结果

当前项目最终全量测试：

```text
255 passed in 10.87s
```

最近专项：

```text
42 passed in 3.37s
```

核心回归：

```text
60 passed in 3.38s
```

重要测试：

```text
D:\miniQMT策略实盘\QuantStudio\tests\test_strategy_alignment_regressions.py
D:\miniQMT策略实盘\QuantStudio\tests\test_strategy_fidelity_gates.py
D:\miniQMT策略实盘\QuantStudio\tests\test_security_code_rules.py
D:\miniQMT策略实盘\QuantStudio\tests\test_security_code_aliases.py
D:\miniQMT策略实盘\QuantStudio\tests\test_bse_filtering.py
D:\miniQMT策略实盘\QuantStudio\tests\test_etf_ptrade_compat.py
```

---

## 8. 接手后第一轮验证命令

工作目录必须是：

```powershell
cd D:\miniQMT策略实盘\QuantStudio
```

### 8.1 基础导入和语法

```powershell
python -m compileall -q quantstudio scripts
python -c "from quantstudio.backtest.libs.security_code_rules import *; print(normalize_to_qmt('920017.XBSE'))"
```

预期：

```text
920017.BJ
```

### 8.2 语义和专项测试

```powershell
python -m pytest -q `
  tests/test_strategy_alignment_regressions.py `
  tests/test_strategy_fidelity_gates.py `
  tests/test_security_code_rules.py `
  tests/test_security_code_aliases.py `
  tests/test_bse_filtering.py `
  tests/test_etf_ptrade_compat.py
```

### 8.3 全量测试

```powershell
python -m pytest -q
```

### 8.4 真实 Fidelity 门禁

```powershell
python scripts/run_strategy_fidelity_gates.py
```

预期：

```text
[PASS] etf_momentum: verdict=PASS
[PASS] smallcap_guard: verdict=CLOSE
```

Windows 控制台若因 GBK 无法输出 `✓/❌`，运行门禁脚本通常不受影响；直接运行 CLI 冒烟时可先设置：

```powershell
$env:PYTHONIOENCODING='utf-8'
```

---

## 9. 下一阶段 PR2 明确要求

主计划 PR2 位于主计划约第 736–799 行。

PR2 目标：

```text
实现真实 pending order queue，消除跨日价格和持仓记账穿越。
```

目标订单生命周期：

```text
created → pending → filled/rejected/expired/cancelled
```

建议增加：

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

PR2 必须实现：

- T 日信号只创建 pending order；
- T+1 开盘前处理 pending queue；
- 使用 T+1 开盘价和 T+1 状态；
- T 日现金、持仓、成交记录不变化；
- 正确区分 `created_dt` 与 `filled_dt`；
- 未执行订单不解锁持仓；
- 末日订单标记 `expired` 或保留 `pending`；
- 停牌、涨停、跌停、资金不足返回明确拒绝原因；
- 保持 `close` 和 `open` 模式向后兼容；
- Run Card 记录执行模式。

PR2 验收标准：

```text
T 日生成，T+1 成交
T 日现金和持仓不变
交易记录日期为 T+1
T+1 涨停时买单拒绝
T+1 停牌时不成交
T+1 跌停时卖单拒绝
全量测试通过
真实 Fidelity 门禁通过
```

计划要求新增测试：

```text
tests/test_next_open_pending_orders.py
tests/test_next_open_nav_timing.py
tests/test_next_open_limit_and_halt.py
tests/test_pending_order_end_of_backtest.py
```

### PR2 特别注意

不要只把当前 `next_open` 的价格读取改成 pending 标志；必须确保：

```text
T 日下单调用不会立即修改 account.cash
T 日下单调用不会立即创建/增加持仓
T 日净值不包含 T+1 尚未成交订单
T+1 执行时才检查开盘价、停牌、涨跌停、资金和整手
```

同时不要混入：

- 新的证券代码规则；
- 分钟 Provider；
- Skill 骨架；
- Renderer；
- GUI 配置改造。

---

## 10. PR2 开始前建议的代码勘察点

优先阅读：

```text
quantstudio/backtest/backtest_engine.py
quantstudio/backtest/ptrade_api.py
quantstudio/backtest/libs/shared_ashare_rules.py
quantstudio/backtest/libs/security_code_rules.py
tests/test_match_price_mode.py
tests/test_order_rejection.py
tests/test_strategy_alignment_regressions.py
```

重点定位：

```text
BacktestEngine.run()
BacktestEngine._build_match_prices()
BacktestEngine._execute_order()
BacktestEngine._get_ptrade_positions()
PtradeAPI order/order_value/order_target/order_target_value
Account / Position / Order
T+1 解锁逻辑
净值记录逻辑
```

当前 `next_open` 仍然是 legacy 语义：历史实现会提前取下一交易日开盘价并在当前循环中即时处理，PR2 要改成 pending queue，而不是继续扩展当前 shortcut。

---

## 11. 不可变质量门禁

以下内容未经用户明确批准不得修改：

1. ETF 动量 3 笔成交黄金序列；
2. ETF 最终资金 `87,752.56 ± 1`；
3. `context.portfolio.positions` 普通 dict 精确 membership；
4. PTrade 公开持仓后缀 `.SS/.SZ/.BJ`；
5. `security_code_rules.py` 作为唯一代码规则源；
6. 北交所 920 + 248 条精确映射规则；
7. 小市值 CLOSE 防退化 envelope；
8. 每阶段都必须运行真实 Fidelity gate；
9. 不能用修改 golden 数值的方式“修复”门禁失败。

如果需要更新黄金基线，必须先提交：

```text
变更原因
旧/新结果对比
真实 PTrade 重新导出或复核依据
差异归因
用户明确批准记录
```

---

## 12. 当前工作区特殊情况

- 项目目录不是 Git 仓库，无法依赖 `git diff` 或 commit 记录；以文件、测试日志、报告和门禁结果审计。
- 项目使用 Windows + PowerShell；中文路径正常，但直接 CLI 输出 Unicode 勾叉时可能遭遇 GBK 编码问题。
- 当前没有创建正式 `skills/quantstudio-strategy-compiler/`；按照主计划，正式 Skill 在 PR5 才创建。
- 分钟数据表和 Tick Schema 可能存在，但分钟 Provider/Engine 未打通，不能宣称分钟 READY。
- `next_open` 在 PR2 前不能宣称符合最终时序契约。

---

## 13. ZCode 最短接手流程

```powershell
cd D:\miniQMT策略实盘\QuantStudio

# 1. 阅读移交手册、实施状态、PR1 报告、PR2 主计划和 Fidelity 契约
# 2. 验证当前工作区
python -m pytest -q
python scripts/run_strategy_fidelity_gates.py

# 3. 确认结果为：
#    255 passed
#    ETF PASS
#    smallcap CLOSE within frozen envelope

# 4. 阅读 PR2 代码和测试要求
# 5. 若用户确认进入 PR2，先新增四个 pending-order 契约测试
# 6. 再实现最小 pending queue
# 7. 按固定顺序跑专项、核心、全量、真实 Fidelity
# 8. 更新 implementation-status.md 和 PR2 实施报告
# 9. 停在下一用户确认闸门
```

---

## 14. 交接结束状态

当前交接时的准确结论：

```text
PR0：PASS，用户已确认
PR1：代码实现 PASS
PR1 Fidelity 加固：PASS
当前全量测试：255 passed
ETF 真实 Fidelity：PASS
小市值真实防退化：CLOSE，但在冻结 envelope 内
PR2：尚未开始
下一工作：等待/确认 PR1 后实施真正 next_open pending queue
```
