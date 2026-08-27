# 回测对齐可诊断性修复 — 验收证据（golden regression）

> **⚠️ 数据状态标注（2026-08-17 项目治理暂停指令）**：本证据基于《项目稳定化治理方案》落地前的数据状态（主库被后台任务独占，回归采用 `data/quantstudio.db.bak_c4merge` 备份）。**待数据快照 + 黄金基线建立后，需在主库/快照库上重验**：① 全量 pytest（本轮被环境性挂起阻断，见 §5）；② 保存运行对照（主库 2026-07 区间，trades/daily_stats/净值逐字节一致）。

- 日期：2026-08-17
- 方案：`docs/backtest-align-diagnosability-design.md`（审计有条件通过，修订①②③已并入）
- 改动文件：
  - `quantstudio/backtest/backtest_engine.py`（方向判定上移 + `_finalize_immediate` 单出口集中采集 + `_reject_drain` + `_emit_fill_audit` + run() 每日重置/日末输出）
  - `tests/test_fill_audit.py`（新增 9 用例）
  - `scripts/audit_etf_corporate_actions.py`（新增只读巡检脚本）
  - 文档同步：`README.md`、`docs/strategy_toolbox.md` §3.7.2、`docs/prompt_engineering.md`

## 1. 单元测试（tests/test_fill_audit.py）

```
9 passed in 0.59s
```

覆盖（方案 §5 验收标准 1）：
- no_price 拒单按 target_value/shares 符号归类 buy/sell（修订①：direction 不再 unknown）✓
- 涨停/跌停阻断采集 ✓
- halted（分钟 Profile）采集（原零日志路径）✓
- insufficient_cash_or_rounding（资金不足 + 整手取整不足 100 股兜底）采集 ✓
- below_rebalance_threshold 不采集（排除规则）✓
- QS_FILL_AUDIT 行内容（sell/buy_filled、sell/buy_rejected、positions_total、rejected_detail）✓
- 无拒单 INFO / 有拒单 WARNING 级别 ✓
- rejected_detail 截断 `...(+N more)`（12 条 → 10 条 + (+2 more)）✓
- 跨日重置（端到端：第一天 159999 no_price 拒单、第二天无拒单 rejected_detail=[]）✓

## 2. 黄金回归（行为等价性，方案 §5 验收标准 2）

方法：同一数据库（`data/quantstudio.db.bak_c4merge`，因主库被后台任务独占、pre_pipeline 备份被 reanchor 任务占用）上，分别用**修复前引擎**（git checkout 基线）与**修复后引擎**重跑 `etf_theme_rotation_quantstudio.py` 2026-07-01~07-31（参数与 08-16 保存运行一致：close 撮合、10 万、佣金 0.0003/最低 0.1、零印花税/过户费/滑点、legacy），逐字节对比产物。

| 产物 | before SHA-256(前16) | after SHA-256(前16) | 逐字节一致 |
|---|---|---|---|
| trades.csv | 4555f906932d7e13 | 4555f906932d7e13 | ✅ |
| daily_stats.csv | 3f0a6ff9cf04ff93 | 3f0a6ff9cf04ff93 | ✅ |
| benchmark.csv | 1de2af3e000717af | 1de2af3e000717af | ✅ |
| 期末净值 | 82406.45355999994 | 82406.45355999994 | ✅ |

**结论：纯日志/采集改动，成交、资金、持仓、净值行为零变化（逐字节等价）。**

注：bak_c4merge（08-13 备份）与 08-16 主库存在数据差异（期末净值 82406 vs 保存运行 80935，07-01 覆盖度 73 只 vs 主库基本齐全），故与保存运行的对照仅作参考；等价性验证以同库 before/after 为准（满足验收判据）。

## 3. QS_FILL_AUDIT 实际输出（after_run.log，节选）

```
WARNING ... QS_FILL_AUDIT date=2026-07-01 sell_filled=0 buy_filled=2 sell_rejected=0 buy_rejected=3 positions_total=2 rejected_detail=[589020.SH:no_price,588710.SH:no_price,560780.SH:no_price]
INFO    ... QS_FILL_AUDIT date=2026-07-02 sell_filled=0 buy_filled=5 sell_rejected=0 buy_rejected=0 positions_total=5 rejected_detail=[]
WARNING ... QS_FILL_AUDIT date=2026-07-03 sell_filled=1 buy_filled=2 sell_rejected=0 buy_rejected=2 positions_total=5 rejected_detail=[589020.SH:insufficient_cash_or_rounding,588710.SH:insufficient_cash_or_rounding]
WARNING ... QS_FILL_AUDIT date=2026-07-06 sell_filled=4 buy_filled=3 sell_rejected=0 buy_rejected=2 positions_total=4 rejected_detail=[159813.SZ:no_price,589020.SH:insufficient_cash_or_rounding]
...
```

23 天全部输出：拒单日 WARNING 并给出 code:reason 明细（可机器解析），干净日 INFO。与策略层 `QS_REBALANCE_AUDIT`（计划）配对即"计划 vs 实际"（例：07-01 计划 buy_submitted=5、实际 buy_filled=2 + buy_rejected=3）。

## 4. Order 行为检查（方案 §5 验收标准 3）

- `no_price` 拒单 `Order.direction`：unknown → buy/sell（修订①）。既有测试 `test_immediate_execute_no_price_returns_rejected_order` 仅断言 status/reason，不受影响；`test_order_rejection.py` 9 用例中 8 通过、1 个**预存在失败**（`test_immediate_execute_buy_success_returns_filled_order` 断言 price>10.0 含滑点，与当前默认滑点配置不符——基线验证确认修复前后同样失败，与本次改动无关）。
- `no_instruction` 分支（target_value 与 shares 均 None）语义保留（价格检查之后、涨跌停之前，reason=no_instruction）。

## 5. 全量 pytest（状态：被环境性挂起阻断，待快照后重验）

- 目标命令：`pytest tests/ -q --ignore=test_gui_smoke --ignore=test_daemon_once_exit_contract --deselect=test_order_rejection.py::test_immediate_execute_buy_success_returns_filled_order`
- 实际：三轮尝试均未能跑完——① 首轮 `-x` 于 `test_daemon_once_exit_contract.py::test_cli_flag_present` 失败停止（220 passed / 1 failed，19.6s，**预存在环境性失败**：daemon `--help` 子进程 GBK 解码，daemon.py 不 import backtest_engine，AST 验证无依赖）；② 全量轮在 GUI 测试段（test_gui_dark_panels 之后）**挂起**（20s CPU 增量为 0）；③ 排除 GUI 模块重跑亦未完成即被暂停指令终止。
- **与本次改动的相关性**：失败/挂起项均为环境性（GBK 编码、Qt GUI、DB 锁竞争——主库被 `restore_minutes_raw.py`、备份被 `etf_minute_reanchor.py` 后台任务独占），且发生在 backtest_engine 无触达路径；`test_order_rejection.py` 9 用例基线/修复后对比一致（1 个预存在滑点断言失败，与改动无关）。
- **重验项**：数据快照/黄金基线建立后，在主库可用状态下完成全量 pytest，确认无新增失败。

## 6. 数据侧发现（巡检脚本，改动点 3）

`scripts/audit_etf_corporate_actions.py` 输出 `output/debug_align/etf_ca_audit_202607.md`（对 bak_c4merge 运行）：
- **07-01 数据缺口**：bak_c4merge（08-13）07-01 仅 73 只 ETF 有 bar（其余 1215 只缺失），而 pre_pipeline 备份（08-15）07-01 基本齐全（仅 588710/560780/589020/159739 等少量缺失）→ **2026-07-01 日线是分批回填的**（不同备份覆盖度不同），"07-01 停牌群"以同步缺口为主、少量真实停牌/公司行为（589020 合并×2、588710/560780 停牌等 preClose 实锤）。
- **7 月公司行为密集**：744 条 preClose 跳变（拆分/合并，±1.5% 阈值）+ 150 条边界样本（阈值邻域列出，避免阈值倒推结论）——模拟数据源特征实锤，与本地引擎"因子反推"（`_apply_factor_derived_split`）处理路径吻合。

## 7. 未纳入本次范围（方案 §3）

- 两端结果对齐（净值/持仓一致）：根因 A/C 主因数据源差异，本地框架不可修（外部核对清单见方案 §6）。
- 撮合/资金/订单行为（停牌拒单、跌停阻断、T+1）：A 股正确语义，本次仅增强可见性。
- basket drain（`_drain_baskets`，G1-I callback_basket next_open-only）直连 `_execute_buy` 的拒单未采集——已知扩展点，目标策略（legacy close）不触达。

## 8. 回退条件

全部改动为增量日志/内存统计 + 新增测试/脚本/文档；删除引擎两处新增（`_day_rejections` 采集与 `_emit_fill_audit` 调用）即可完全回退，无行为残留（黄金回归证明行为等价，回退同理）。
