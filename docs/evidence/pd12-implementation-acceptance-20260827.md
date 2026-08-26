# P-D12 实施与验收证据：order_target_value 目标市值语义修复（WP-B / B1+B2+B3，2026-08-26/27）

- 流水线：Step 1 设计 v1（审计通过 + 两处细化）→ Step 3 实施 → Step 4 验收（本地 + tech_etf 平台 + 周频零影响）→ Step 5 用户确认（待）→ Step 6 双仓库推送
- 回退点：`ae2594a45439fb345b34affc2a50c39684c72c31`（baseline-wpb-20260826，实施前）
- 依赖：WP-A（f0c0bd7 已关闭）——平台侧 `get_position` 归一 + P-POS F3/F5 实证

## 1. 实施清单（tracked 2 文件 + 新增测试）

| 文件 | 改动 |
|---|---|
| `quantstudio/backtest/ptrade_api.py` | **B2**：`_qs_wire_order_target_value` delta 修复（清仓保底→px 缺失回退→现值/delta→微调跳过 0.5%→减仓走原生→加仓对 delta 拆单→delta<1 手告警 no-op）+ `_qs_current_value`（T-1 px，不用 market_value——D1 成本回退低估）+ `_QS_MIN_REBALANCE_PCT=0.005`（D7，与引擎 `min_rebalance_pct` 默认一致，T10 钉死）+ `_qs_noop_target` + `_qs_warn_zero_order` |
| `quantstudio/strategy_compiler/source_import.py` | **B1**：`_QS_ORDER_SPLIT_EXT` 内 `order_target_value` 同构 delta 修复 + `_qs_pos_amount`（白名单安全内联归一 5/6/9→.SS）+ 模板内 `_QS_MIN_REBALANCE_PCT` / `QS_ZERO_ORDER` / `QS_NOOP_ORDER` |
| `tests/test_pd12_target_semantics.py`（新） | T1~T13 共 14 用例（含审计细化① 三面等价 T13：wiring == engine-native 逐笔） |

**实施中发现并修正**：
1. 引擎 `min_rebalance_pct` 实际默认 **0.005（0.5%）** 而非设计草案假设的 5%——D7 常量以实测为准，T5/T6 场景按正确阈值重设（防漂移机制在实施期兑现）；
2. B1 模板 `globals().get('_qs_norm_code')` 中间变量被 LOCAL-API-WHITELIST 拦截——改为自包含内联归一（5/6/9→.SS），消除跨模板依赖。

**D5 fail-open 继承语义显式登记（审计细化②）**：`_qs_current_value`/`_qs_pos_amount` 异常时返回 0（视为空仓 → delta=全额买入）——**继承修复前行为**（现值不可得时全额买入），非新增设计。合并基线重验时不应视为未解释差异。

## 2. 本地验收（Step 4 本地部分，审核通过）

| 套件 | 结果 |
|---|---|
| P-D12 矩阵 | **14/14 绿**（T1 空仓全额/T2 存量补差/T3 减仓/T4 清仓保底/T5 微调跳过/T6 delta<1手/T7 px 回退/T8 拆单作用 delta/T9 双端同构/T10 阈值一致/T11 compliance 回归/T13 三面等价/B1 模板标记×2） |
| B2 复现测试 | **5/5 绿（两红转绿）**：存量补差 ✓ 减仓 ✓（修复目标）；清仓保底 ✓ 空仓等价 ✓ 原生对照 ✓（三绿保底仍绿） |
| compliance | **94/94 绿**（5 个存量接线用例零改动通过——空仓 delta=全额等价） |
| 四套件合跑 | **132/132 绿**（pd11 19 + pd12 14 + B2 5 + compliance 94） |
| WP-A 回归 | pd11 19/19 独立复跑全绿（审核方复核确认） |
| 6 策略重转 | api_portability **全 PASS**；B1 模板含 P-D12 标记（`_QS_MIN_REBALANCE_PCT`/`_qs_pos_amount` ×4 命中） |

## 3. tech_etf 平台验收（Step 4 平台部分，审核通过）

### 3.1 修复前后总览

| 指标 | 修复前(08-25) | 修复后(08-27) | 改善 |
|---|---|---|---|
| 收益差 | -6.89pp（本地 -19.74% vs ptrade -26.63%） | **-1.37pp**（本地 -14.13% vs ptrade -15.50%） | ✅ **收窄 5.52pp** |
| ptrade 期末仓位 | 79,700 股 ≈ 99.9%（满仓裸奔） | **41,700 股 ≈ 50%**（目标权重 0.475） | ✅ 降仓风控生效 |
| 本地期末仓位 | ~96%（07-27 金字塔后） | **42,400 股 ≈ 50%** | ✅ 同左 |
| B3 告警 | 「委托数量为0」无上下文 | `QS_ZERO_ORDER code=511260.SS delta=2505.3 px=134.85 one_lot_value=13485.4` | ✅ 完整上下文 |

### 3.2 金字塔消除逐日对照（ptrade 成交明细）

| 日期 | 修复前买入 | 修复后买入 | 修复语义 |
|---|---|---|---|
| 07-01 | 33,900 股（空仓全额） | 33,900 股 | ✅ 等价（空仓 delta=全额） |
| 07-06 | **36,400 股**（全额→持仓 70,300=88.5%） | **2,500 股**（delta 补差→持仓 36,400≈50%） | ✅ **金字塔消除** |
| 07-13 | 9,400 股（现金下调→99.9%） | **零成交**（`below_rebalance_threshold` delta=77.8 < 0.5%） | ✅ 微调跳过 |
| 07-20 | 下单失败（现金枯竭） | **600 股**（delta 补差） | ✅ 现金恢复 |
| 07-27 | 下单失败 | **4,700 股**（delta 补差） | ✅ |

本地端同步验证：07-27 **买 1,800 股**（delta=1,904.4/1.058——修复前全额 42,400 股金字塔）✅

### 3.3 残余差异归因（-1.37pp，100% 已登记）

1. **FE3**：本地 07-01 首日 `no_price` 拒单（`rejected_detail=[515050.SH:no_price]`），空仓过第一周——WP-D 范畴；
2. **D3**：评分 ±1/7 网格差 → 07-13 换仓分岔（本地清仓 515050 换 512480，ptrade 微调跳过）——数据层已登记。

## 4. WP-A 零影响确认（周频复跑）

| 端 | 验证方式 | 结果 |
|---|---|---|
| **本地** | WP-B 后重跑 trades 逐笔比对（20260826_232153） | **完全一致**：07-01 建仓 9 只 → 07-06/13/14/20/27 全部交易相同；最终资金 91,840.79 不变 |
| **ptrade** | 代码层面分析（未重跑） | 周频的 `order_target_value` 全部为 value=0 清仓或空仓买入——P-D12 分支序分别走"原生保底"和"空仓 delta=全额"，与修复前行为**代码等价**（T1/T4 测试锁定） |

## 5. P-POS-2 探针状态

脚本已就绪（`ptrade/probe_portfolio_positions_ptrade.py`），**平台端尚未执行**（无回贴日志）——`context.portfolio.positions` 容器取证待用户安排（4 交易日回测即可），结果将用于 P-D11b 设计增补。

## 6. 回退

- 回退点 `ae2594a`（实施前）；
- 改动 = 2 tracked（ptrade_api.py / source_import.py）+ 1 新增测试 + 若干文档——回退 = 定向 restore + 删除新增文件；
- 与 slippage 线（L398-1593 九 hunk）零冲突（B2 改动区 L2304-2420）。
