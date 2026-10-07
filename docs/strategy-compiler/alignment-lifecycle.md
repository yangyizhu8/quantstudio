# 对齐生命周期契约（Alignment Lifecycle）

> 件 A · 对齐生命周期门（`docs/alignment-lifecycle-gate-design.md`，2026-10-07 总调度批准）。
> 对齐从事后审计升格为**管线门禁**：未过对齐门的策略不得标记毕业/分发。

## 策略生命周期状态机（六态）

```
generated → converted → local-passed → platform-run → aligned → graduated
    │           │             │              │            │          │
 R6 验收过   qs-compile    本地回测黄金    平台日志交付  S1 残差门   registry 登记
             api_portability 指标落盘      （Log.txt 契约， 达标       +状态归档
              PASS                        用户域）
```

| 态 | 进入判据 | 产出物 |
|---|---|---|
| `generated` | R6 验收过（skill 管线） | 策略源码+design json |
| `converted` | qs-compile 转换+api_portability PASS | 转换产物 |
| `local-passed` | 本地回测黄金指标落盘 | backtest_results 产物 |
| `platform-run` | 平台侧回测日志交付（用户域） | Log.txt 契约 |
| **`aligned`（唯一硬门）** | **S1 报告残差达标**（首策略族阈值 <0.5%，维度宣布后该维度收紧） | **`alignment/report.md`**（S1 `--output` 标准路径） |
| `graduated` | registry 登记+状态归档 | registry 行+快照 |

## 降级/回环路径

- 平台版本演化 / 新案立案 → `graduated → re-aligned`（重新过 aligned 门）；
- 对齐门 BLOCK 后修复（六步流水线）→ 重跑 S1 → 达标再入 `aligned`。

## ALIGNMENT-GATE 校验规则（三态）

校验器：`skills/quantstudio-strategy-compiler/scripts/validate_agent_strategy.py`
规则码 `ALIGNMENT-GATE`，检查策略产物 `alignment/report.md`（候选路径：
`output/generated_strategies/<stem>/alignment/report.md` 与
`strategies/<stem>/alignment/report.md`）：

| 报告状态 | 判定 | 语义 |
|---|---|---|
| 存在且 verdict「已对齐」 | 无 issue（PASS 态） | 过门 |
| 缺失 | **WARN**（不阻断） | 新策略提示补跑 S1；**存量策略豁免（登记制）** |
| 存在但 verdict 非「已对齐」 | **BLOCK** | 跑了对齐未达标——禁止 graduated |

**缺失与失败分态**是关键设计：没跑≠跑失败，一刀切阻断存量策略会瘫痪在役流程。

## 存量豁免登记

六策略+四象限等存量策略按登记制豁免（首次跑 S1 前为 WARN 态）；**四象限已达标
0.013%（CORP-01+02 合并终判，2026-10-07）为首个 `aligned` 归档样例**，产物
`output/backtest_results/20261007_210721_四象限ETF轮动策略`（对齐报告随案归档）。

## 联动

- **S1 产物落盘约定**：`scripts/align_diff_report.py --output <产物>/alignment/report.md`
  （B 件 S3 路由 §6 消费同路径）；
- **S4 收敛台账**（B 件）：每策略每次 S1 跑自动追加台账行（残差/分歧组/verdict）；
- **维度宣布**（闭环计划件 G）：连续 N=3 新策略首跑「已对齐」⇒ 该 API 维度宣布对齐
  转维护态（首例候选：ETF 日线策略域）。