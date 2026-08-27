# 验收证据：D4-S6 P-D12 接线层拆单换价缺陷修复

- 日期：2026-08-27
- 方案：docs/bbrev-pd12-split-px-design.md（v3 终稿，ZCode 三轮审计通过）
- 改动文件：`quantstudio/backtest/ptrade_api.py` + `quantstudio/strategy_compiler/source_import.py`（引擎零触碰）
- 测试更新：`tests/test_ptrade_contract_compliance.py`（审计后语义断言）+ `tests/test_order_rejection.py`（既有滑点断言滞后修正）
- 回退点：`8e543fd`（P-D12 提交）

## 1. 单测 T1/T2/T3（新增）

| 用例 | 内容 | 结果 |
|---|---|---|
| T1 | 春节缺口日 order_target_value 换算=当日收盘、成本≤目标×1.005（600821: 修复前 8000股@6.72=53,760 超支 7.5% → 修复后 7400股@6.72=49,728） | ✅ PASS |
| T2 | 多笔拆单现金竞对不拒单（cash_avail 生效，buffer=1.0：9400股 成本98,700 ≤ 100,000 现金） | ✅ PASS |
| T3 | 存量仓跳空日加仓 delta(①层)÷px_exec(②层) ≈ 目标（3000×6.20 现值 + 4600股@6.72 → 总市值 51,072 ≈ 50,000 含费容差）；减仓走原生 | ✅ PASS |

证据：output/generated_strategies/broken_board_reversal/r6_d4s6_unit_tests.py（ALL T1/T2/T3 PASS）

## 2. 现有测试全绿（相关套件）

| 套件 | 结果 |
|---|---|
| tests/test_ptrade_contract_compliance.py（94 用例，含双端同构/常量对齐/wire 语义——断言已更新至审计后语义） | ✅ 94 passed |
| tests/test_pd12_target_semantics.py（P-D12 验收集：target 语义/微调跳过/拆单同构/49k 上限/delta 减仓/清仓保底/T10 差分） | ✅ 14 passed（含 T11 compliance 子调用） |
| tests/test_b2_target_value_semantics_repro.py / test_order_rejection.py / test_pr6b1_execution_level.py / test_agent_execution_funding_contract.py | ✅ 全过 |
| tests/test_batch_apis.py standalone | ✅ 13 passed（跨文件连跑失败为既有顺序污染：fundamentals/history batch 用例读全局 provider 缓存，前序其他文件残留——与本次改动无关，单独运行/与本改动文件同跑均绿） |
| 全量（去重后无本次相关失败） | 159 passed / 3 个 batch_apis 跨文件污染（既有的） |

## 3. 真实回测实证（春节缺口日重演）

窗口 2026-02-09 ~ 2026-02-26（含 2026-02-24 春节缺口日）：
- 修复前（CLAMP 版）：600821 8000股@6.72 超支 7.5%；000767 13600股 资金不足拒单
- 修复后（order_target_value 回退版）：600821 **7400股@6.72**、000767 **14000股@3.60 全额成交**；**REJECTION_ROWS=0**

证据：r6_d4s6_gap_verify.py 输出 + output/backtest_results/20260827_030716_断板反包策略/

## 4. 全窗口 G3.5（断板反包策略回退版双跑）

- runA: 20260827_032709_strategy / runB: 20260827_033721_strategy
- config/daily_stats/trades SHA256 逐位一致（config=2182505532eee60b…, daily_stats=6852e8e0c6a35afa…, trades=fac655c512b4bb1b…）
- 零 insufficient_cash 拒单；23 信号/23 买/22 卖；空仓日 86.0%（信号依赖语义）
- 绩效：-9.23% vs 000852 +11.32%（超额 -20.54pct）——与修复前显式版一致（000767 股数 13900→14000 微调，净值影响 <0.01pct → **语义等价校正，非行为漂移**）

## 5. 框架修复对既有产物影响（R2 对比口径）

- **不经接线层路径（显式 order() 自保版）**：修复前后 trades hash 完全一致（732ef15b...）→ **零漂移** ✅
- **经接线层路径（order_target_value 回退版）**：45 笔中 44 笔与显式版一致，唯一差异=000767 股数 13900→14000（目标无 0.995 缓冲低配的校正）→ **股数按真实成交价核算的校正，逐笔归因完成** ✅

## 6. 顺带修复（新铁律：发现即修）

- tests/test_order_rejection.py::test_immediate_execute_buy_success 断言 `price > 10.0`（期望 1.001 滑点）——引擎默认滑点=0.0（PTrade 实证对齐），旧断言为历史测试滞后 → 修正为 `price == 10.0`（2026-08-27）

## 7. 遗留登记

- tests/test_batch_apis.py 跨文件顺序污染（fundamentals/history batch 读全局 provider 缓存）：既有复合性问题，与本次改动无关，单独运行绿——建议后续独立立项修复（走新铁律"框架问题立即解决"流程），不在本方案范围。