# 输出目录与 Run Card 契约

> Derived from docs/strategy-compiler/output-and-run-card-contract.md @ 2026-07-22
> 权威源：docs/strategy-compiler/ 下的原始契约文档
> 本文件为 Skill 派生快照，契约变更时必须同步（见 SKILL.md 同步纪律）

## 1. 每策略输出结构

```text
<output-root>/<strategy_id>/<build_id>/
├── <strategy_id>_quantstudio.py
├── <strategy_id>_ptrade.py
├── strategy_spec.json
├── strategy_ir.json
├── capability_report.json
├── static_validation_report.json
├── variant_consistency_report.json
├── run_card.json
├── README.md
├── local_backtest/        # 仅实际执行后创建
└── fidelity/              # 导入 PTrade 结果并对照后创建
```

**不存在的执行结果目录不得用空文件伪造成功。**

## 2. 覆盖与可复现性

- `build_id` 必须稳定区分一次编译产物。
- 默认不覆盖既有构建；覆盖必须由 Spec 显式允许。
- Run Card 记录 Spec/IR/代码/报告路径及 SHA-256（产物存在后）。
- 保存所有契约版本、Profile、数据区间、Python/QuantStudio 版本、随机种子和数据指纹。
- 相同输入、数据和版本应生成相同业务代码；时间戳等运行元数据不得进入业务逻辑。
- **运行层复现性（G3.5，R5 门禁）**：相同策略产物必须在两个独立进程各跑一遍
  （同一窗口/资金/配置，`PYTHONHASHSEED` 固定或随机种子记录）；证据必须绑定两次运行的
  `config.csv`/`daily_stats.csv`/`trades.csv`（`user_backtest_evidence` 2.1 的
  `artifacts` + `reproducibility_artifacts`），两侧三件套 SHA-256 **逐位一致**才 R5 PASS；
  不一致即 R5 FAIL 并归因（策略非确定性——dict/set 迭代顺序/随机数——或数据漂移或环境差异）。
  运行日志因含时间戳不参与跨运行比对。

## 3. 状态语义

- `PASS`：当前阶段所有必需检查通过。
- `PARTIAL`：合法中间产物已生成，但后续阶段未运行。
- `BLOCKED`：能力门禁阻止执行；**不得写成回测通过**。
- `FAILED`：已运行的验证或执行失败。

`stage` 分为 `SPEC_ONLY`、`STATIC_VALIDATED`、`SMOKE_EXECUTED`、`FIDELITY_COMPARED`。

## 4. Run Card Schema

机器定义：`quantstudio/strategy_compiler/schemas/run_card.schema.json`

示例：`quantstudio/strategy_compiler/examples/run_card.example.json`

## 5. PR5 现实约束

Skill 当前版本（PR5）停在 Spec：Run Card `stage = SPEC_ONLY`、`status = PARTIAL`。`SMOKE_EXECUTED`/`FIDELITY_COMPARED` 需要 PR6 渲染产物（`.py`/`strategy_ir.json`/`local_backtest/`/`fidelity/`）实际存在后才能填入，PR5 不得伪造这些阶段或目录。

## 6. Stable target-aware publish paths (0.4.0; Chinese naming since 2026-08-22)

After validation and package creation, publish entry points to:

```text
<project-root>/quantstudio/backtest/strategies/<strategy_name>.py
<project-root>/ptrade/<strategy_id>_ptrade.py
```

The QuantStudio target uses the **Chinese `strategy_name`** as the filename (no ASCII suffix, per the Chinese naming contract 2026-08-22): at least one CJK character, filename-safe (no leading `_`/whitespace, no trailing `.`/whitespace, no `\ / : * ? " < > |`, ≤50 chars), and stem-conflict-checked against every existing strategy file at R4/candidate/publish (`STRATEGY-NAME-CONTRACT` / `STRATEGY-NAME-CONFLICT` BLOCK; `design.output.overwrite=true` is the explicit overwrite consent). `strategy_id` stays the lowercase-ASCII machine identifier. The QuantStudio directory is the exact directory scanned by the PyQt backtest strategy selector. Dual mode writes both paths after local validation, PTrade validation, and post-generation consistency. QuantStudio-only mode writes only the first path, creates no PTrade placeholder, and records PTrade validation / dual consistency as `NOT_APPLICABLE` plus PTrade output as `NOT_GENERATED`. If a different file already exists and `output.overwrite=false`, publishing fails closed.

## Candidate and promotion paths (0.5.0; Chinese naming since 2026-08-22)

User-PyQt mode exposes only `<project-root>/quantstudio/backtest/strategies/<strategy_name>__candidate_quantstudio.py`（中文策略名候选） after R4 PASS. It is explicitly not a formal or PTrade upload artifact. After hash-bound R5 PASS, R6 writes formal QuantStudio/PTrade targets and removes the candidate. Candidate retention after promotion is a publication failure.
