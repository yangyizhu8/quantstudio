# 双端对账材料归档规范（WP-E E3，2026-08-27）

## 1. 强制随附材料（每次双端对账必须齐全）

**平台侧**（`D:\miniQMT策略实盘\私募工作文件\本地回测框架和ptrade平台双端回测数据汇总\<strategy>_ptrade\`）：
- `交易详情<时间戳>.csv`（成交明细）
- `持仓明细<时间戳>.csv`（持仓快照）
- `ptrade回测数据.txt`（平台 UI 指标文本）
- `ptrade平台日志.txt`（平台日志，含「生成订单」行/QS_ 审计行）

**本地侧**（`output/backtest_results/<run>/`）：
- `trades.csv`（成交流水）
- `daily_stats.csv`（每日统计）
- `ptrade_metrics.json`（引擎权威指标——指标公式唯一来源）
- （可选）回测日志（含 QS_FILL_AUDIT / QS_REBALANCE_AUDIT 审计行）

## 2. 校验规则（scripts/dual_end_reconcile.py --check-archive）

1. 必备文件齐全（缺一项即 FAIL 并指明缺项）；
2. **时间戳同批**：平台侧导出文件 mtime 跨度 ≤ 10 分钟（防跨版本/跨批次混杂——同一次回测导出的文件时间应相近）；
3. 命名一致性：`<strategy>_ptrade` 目录名与本地 `<run>` 的策略 id 对应。

## 3. 指标口径（唯一权威 = 引擎公式）

- 所有统一计算公式复用 `quantstudio.backtest.ptrade_metrics.calculate_ptrade_like_metrics`
  （import 复用，**禁止第三口径**）：
  - 胜率 = 平仓口径（sell 且 pnl>0 / 总 sell）；
  - 盈亏比 = avg 盈利 / avg 亏损（total_profit/total_loss）；
  - 年化/夏普/索提诺 = TRADING_DAYS_PER_YEAR 年化基。
- 平台 UI 指标（`ptrade回测数据.txt`）仅作**对照参考**：口径差异（胜率含未平仓、盈亏比反比）标注 `[口径待核]`，非框架缺陷。

## 4. 归档时机

- 每次 WP 验收的对账演示、合并基线重验双跑、新策略首次双端回测——均强制随附完整材料；
- 归档目录为上述 `<strategy>_ptrade\` 约定（现状已在用，本规范固化）。

## 5. 判定

- 对账报告 Δ>1e-6 指标全部 `[已归因]`/`[口径待核]`（无 `[未解释]`）= 对账 PASS；
- 任一 `[未解释]` = 驱动下一轮修复（与合并基线重验 PASS 判据一致）。