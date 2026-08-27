# WP-E 实施与验收证据：审计与对账基建（E2/E3，2026-08-27）

- 流水线：Step 1 设计 → Step 2 审计通过（条件：复用引擎公式）→ Step 3 实施 → **Step 4 验收**
- 差异化：E1（QS_FILL_AUDIT）已在 08-17 方案落地（引擎 L754 + test_fill_audit）；本 WP 交付 E2/E3

## 1. 实施清单

| 文件 | 改动 |
|---|---|
| `scripts/dual_end_reconcile.py`（新） | E2：统一公式对账（**import 引擎 ptrade_metrics，审计条件：防第三口径**）+ 逐日对齐 + 差异归因分解 + --check-archive（E3）+ --verify-engine-consistency（T9） |
| `docs/dual-end-reconcile-material-spec.md`（新） | E3 归档规范（必备文件清单/时间戳同批/指标唯一权威=引擎公式） |
| `tests/test_dual_end_reconcile.py`（新） | T1~T9 矩阵 |

## 2. 验收结果

| 项 | 结果 |
|---|---|
| 测试矩阵 | **9/9 全绿**（T1 胜率平仓口径 = 3/(3+7) / T2 盈亏比引擎值 / T3 平台文本解析 / T4 逐日对齐 5 日 / T5 报告产出 / T6-T8 归档校验 PASS/缺文件/跨批次 / **T9 引擎一致性=审计条件**） |
| **真实数据实跑** | `--check-archive`：tech_etf 真实目录 **ARCHIVE-PASS**（4 平台文件齐全+同批）；`reconcile` 产出 `output/reconcile_tech_etf_20260827.md`（本地 -17.96% vs 平台 -15.50% Δ-2.46pp + 逐日对齐 + 归因分解） |
| 公式复用 | `from quantstudio.backtest.ptrade_metrics import calculate_ptrade_like_metrics`（引擎权威公式直接复用，T9 断言 reconcile 本地指标 == 引擎 summary 逐位一致） |
| 纯新增 | 零框架代码改动（符合"观测手段先行"） |

## 3. 审计条件落实

**「本地侧度量公式必须复用引擎实现」**→ `dual_end_reconcile.py` 胜率/盈亏比/夏普/索提诺全部 import 引擎 `ptrade_metrics`（不手写公式）；T9 断言对账重算 == 引擎 summary 逐位一致（win_rate 重算 30.0 == summary 30.0）——**无第三口径**。

## 4. 真实数据对账演示（tech_etf 修复后）

```
本地 strategy_return_pct = -17.96%  vs  平台策略收益 -15.50%  Δ -2.46pp
本地 max_drawdown_pct    = 19.53%   vs  平台最大回撤 17.12%   Δ +2.41pp
```
残余 Δ 归因：FE3/D3 数据微差（tech_etf 首日建仓路径已修）——已登记项，无 [未解释]。

## 5. 回退

- 纯新增脚本/测试/文档——删除即回退；
- 零 tracked 改动（本 WP 全部 untracked）。