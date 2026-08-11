# 输出目录与 Run Card 契约

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

PR0 只冻结结构；不存在的执行结果目录不得用空文件伪造成功。

## 2. 覆盖与可复现性

- `build_id` 必须稳定区分一次编译产物。
- 默认不覆盖既有构建；覆盖必须由 Spec 显式允许。
- Run Card 记录 Spec/IR/代码/报告路径及 SHA-256（产物存在后）。
- 保存所有契约版本、Profile、数据区间、Python/QuantStudio 版本、随机种子和数据指纹。
- 相同输入、数据和版本应生成相同业务代码；时间戳等运行元数据不得进入业务逻辑。

## 3. 状态语义

- `PASS`：当前阶段所有必需检查通过。
- `PARTIAL`：合法中间产物已生成，但后续阶段未运行。
- `BLOCKED`：能力门禁阻止执行；不得写成回测通过。
- `FAILED`：已运行的验证或执行失败。

`stage` 分为 `SPEC_ONLY`、`STATIC_VALIDATED`、`SMOKE_EXECUTED`、`FIDELITY_COMPARED`。

> **v1.1（2026-08-11，source entry）**：`stage` 新增 `SOURCE_IMPORTED`（源码导入转换阶段）；
> run_card_version 由 `1.0` bump 为 `1.1`。**1.1 不向后兼容**：旧消费者不识别新枚举值
> 时必须显式拒绝（不得静默忽略）。source entry 不产生 `SPEC_ONLY`（无 spec 输入）。

## 4. Run Card Schema

机器定义：`quantstudio/strategy_compiler/schemas/run_card.schema.json`

示例：`quantstudio/strategy_compiler/examples/run_card.example.json`

当前 PR0 示例为 `SPEC_ONLY/PARTIAL`，明确 Renderer 和冒烟尚未运行。

## Target-aware output status

For `targets=["quantstudio"]`, the Run Card records QuantStudio validation PASS, PTrade validation NOT_APPLICABLE, dual consistency NOT_APPLICABLE, and PTrade output NOT_GENERATED. Only the PyQt strategy file is published. Dual mode retains both physical outputs and post-generation consistency evidence.

## User-PyQt candidate Run Card

The R0 contract records the R5 execution owner. For user-PyQt mode the Run Card must include candidate/canonical SHA-256, candidate path, recommended and actual user-selected windows, project database provenance, capital, profile, match mode, completion/exception state, runtime checks, and evidence review status. Formal publication remains locked until evidence PASS. Successful R6 promotion removes the candidate and records both final hashes.
