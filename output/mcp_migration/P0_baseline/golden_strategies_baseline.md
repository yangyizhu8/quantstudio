# P0 黄金策略基线（冻结快照，零重跑）

> 本文件冻结当前 QuantStudio 回测 golden 校验机制入口与基线 commit。
> 红线：本轮**不重跑**任何回测/golden 校验（避免行为变更）；仅记录入口与基线锚点。

## 基线 commit
`da2dace`（与任务书 §0 一致）

## Golden 校验机制入口（只读引用）
| 机制 | 入口 | 说明 |
|------|------|------|
| FBP 回归 harness | `phase2_step1_harness.py` | 首板回踩日线策略 golden hash 锚点 |
| 通用 golden runner | `scripts/benchmarks/run_golden.py` | 多策略 golden 比对入口 |
| 证据清单生成 | `scripts/benchmarks/gen_evidence_manifest.py` | golden 证据 manifest |

## 冻结声明
- 上述 golden 脚本在 `da2dace` 的产出 hash 即为 P0 基线。
- MCP 数据集接入后（后续 C 阶段），回测结果须与本次冻结的 golden hash **逐项一致**（容差以现有测试契约为准，禁止放宽）。
- 若 MCP 客户端改变数据读取路径，须重新跑 golden 校验并对比本基线；差异即验收失败，立即回退。

## 与 MCP 的约束
- MCP server 返回的原始数据，经客户端复权/PIT 后，喂入回测须复现本基线 golden hash。
- 任何导致 golden hash 变更的数据契约改动，均视为行为变更，须单独立项 + 用户确认（不属本轮 P1B 草案范围）。
