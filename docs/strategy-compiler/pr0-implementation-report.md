# PR0 实施报告

阶段：PR0  
状态：PASS（等待用户确认）

## 完成内容

1. 记录修改前全量测试基线。
2. 冻结 Strategy Spec v1、Time Model、Engine Profile、多维能力模型、默认 PTrade Profile、A股硬过滤、日线代理、输出目录和 Run Card 契约。
3. 创建 `strategy_spec`、`capability_report`、`run_card` 三个 Draft-07 JSON Schema。
4. 创建三个持续验证的合法示例。
5. 新增 Python 契约验证入口，对 JSON Schema 之外的跨字段语义执行阻断检查。
6. 新增 19 个契约测试，覆盖非法时序、代理撮合不一致、Tick 虚假 READY、必需能力阻断和版本字段。
7. 将 Schema/示例声明为 Python 包数据。
8. 未修改 BacktestEngine、Provider、证券代码规则或既有策略，严格不提前进入 PR1/PR2。

## 修改文件

- `pyproject.toml`
- `quantstudio/strategy_compiler/__init__.py`
- `quantstudio/strategy_compiler/contracts.py`
- `quantstudio/strategy_compiler/schemas/strategy_spec.schema.json`
- `quantstudio/strategy_compiler/schemas/capability_report.schema.json`
- `quantstudio/strategy_compiler/schemas/run_card.schema.json`
- `quantstudio/strategy_compiler/examples/strategy_spec.example.json`
- `quantstudio/strategy_compiler/examples/capability_report.example.json`
- `quantstudio/strategy_compiler/examples/run_card.example.json`
- `docs/strategy-compiler/architecture.md`
- `docs/strategy-compiler/strategy-spec-contract.md`
- `docs/strategy-compiler/lifecycle-and-timing-contract.md`
- `docs/strategy-compiler/frequency-and-engine-profile.md`
- `docs/strategy-compiler/capability-model.md`
- `docs/strategy-compiler/ashare-filter-contract.md`
- `docs/strategy-compiler/ptrade-profile-contract.md`
- `docs/strategy-compiler/output-and-run-card-contract.md`
- `docs/strategy-compiler/implementation-status.md`
- `tests/test_strategy_spec_schema.py`
- `tests/test_capability_model.py`
- `tests/test_timing_contract.py`
- `tests/test_proxy_mode_contract.py`

## 新增测试

```text
tests/test_strategy_spec_schema.py
tests/test_capability_model.py
tests/test_timing_contract.py
tests/test_proxy_mode_contract.py
```

## 测试结果

```text
修改前基线：201 passed in 14.84s
新增专项：  19 passed in 0.19s
核心回归：  51 passed in 4.50s
最终全量： 220 passed in 13.40s
```

日志保存在 `output/strategy-compiler-pr0/`。

## 兼容性影响

- 未改变现有日线策略生命周期、撮合、成本、API 注入或输出。
- 新增依赖 `fastjsonschema>=2.18`（当前环境已具备）。
- 新包只提供契约和验证入口，不介入现有回测路径。

## 已知限制

- `next_open` 仍是 legacy 行为，等待 PR2。
- 证券代码规则仍分散，等待 PR1。
- 分钟 Provider/Engine 未 READY，能力必须 BLOCKED。
- Tick/L2 仅可表达，执行必须 PLANNED/BLOCKED/UNSUPPORTED。
- 正式 Skill、IR、Renderer、静态代码检查和冒烟执行器分别等待 PR5/PR6。
- 当前目录不是 Git 仓库，不能生成提交记录。

## 下一阶段前置条件

依据冻结主计划第 7.4 和第 13 节，用户确认 PR0 文档后才允许进入 PR1。
