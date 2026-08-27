# 会话交接说明 — backtest-align 可诊断性修复（2026-08-17 暂停）

> 依据《项目稳定化治理方案》（docs/project-stabilization-plan.md）暂停指令输出。
> 本会话全部产出物基于**治理方案落地前的数据状态**，待数据快照 + 黄金基线建立后重验。

## 1. 进行中任务清单及状态

| 任务 | 状态 | 说明 |
|---|---|---|
| 四项根因深度取证 | ✅ 完成 | 证据：`output/debug_align/rootcause_probe.py` / `rootcause_probe.txt`（对 `quantstudio_backup_pre_pipeline_20260816.db` 只读复算） |
| 对齐修复设计方案 | ✅ 完成（审计有条件通过） | `docs/backtest-align-diagnosability-design.md`（修订①②③已并入；决策点 1/2 已确认） |
| 引擎实施（改动点 1+2） | ✅ 代码完成，验收部分待重验 | `quantstudio/backtest/backtest_engine.py`：方向判定上移、`_finalize_immediate` 单出口集中采集、`_reject_drain`、`_emit_fill_audit`、run() 每日重置/日末输出 |
| 单元测试 | ✅ 9/9 通过 | `tests/test_fill_audit.py`（四种拒单场景 + 排除规则 + 审计行内容/截断 + 跨日重置端到端） |
| 黄金回归（同库 before/after） | ✅ 通过 | `data/quantstudio.db.bak_c4merge` 上修复前后重跑，trades.csv / daily_stats.csv / benchmark.csv **逐字节一致**（SHA 见证据文档） |
| 巡检脚本（改动点 3） | ✅ 完成 | `scripts/audit_etf_corporate_actions.py` → `output/debug_align/etf_ca_audit_202607.md`（停牌 209 只·日、公司行为 744 条、边界 150 条，对 bak_c4merge 运行） |
| 文档同步 | ✅ 完成 | `README.md`、`docs/strategy_toolbox.md` §3.7.2、`docs/prompt_engineering.md` |
| 验收证据文档 | ✅ 已出，§5 全量 pytest 待重验 | `docs/evidence/backtest-align-golden-20260817.md`（已标注数据状态） |
| **全量 pytest** | ⏸ 未完成（环境性阻断） | 三轮均未跑完：daemon GBK 失败（预存在）、GUI 测试段挂起（Qt/DB 锁竞争）、第三轮被暂停指令终止。**待快照/基线后重验** |
| **主库保存运行对照** | ⏸ 未执行 | 主库被后台任务独占；需主库可用后在 2026-07 区间重跑并与 `output/backtest_results/20260816_234542_*` 逐字节对照 |
| **用户确认 + 双仓库推送** | ⏸ 未执行 | 六步流水线第 5/6 步，待重验完成后由用户确认再推送 |

## 2. 新发现未处理问题清单（只记录，不处理，供登记表首批存量输入）

| # | 现象 | 疑似根因 | 层级 | 备注 |
|---|---|---|---|---|
| 1 | 2026-07-01 大量 ETF 无日线 bar，不同备份覆盖度不同（bak_c4merge@08-13 仅 73 只；pre_pipeline@08-15 基本齐全；主库@08-16 缺 588710/560780/589020/159739 等少量） | 07-01 日线**分批回填**（同步缺口/回填时序），叠加少量真实停牌/公司行为（589020 合并×2 等 preClose 实锤） | D1（数据） | 巡检报告：`output/debug_align/etf_ca_audit_202607.md` |
| 2 | 2026-07 全月 744 条 ETF 拆分/合并公司行为 + 密集停牌 | 模拟数据源特征（未定性为缺陷） | D1（数据） | 需数据管线侧确认是否预期 |
| 3 | 589020 两端（本地 vs PTrade）价格基准差 ≈1.7 倍；本地 07-01 评分第 510 名 vs PTrade 第 1 名 | PTrade 端把 07-01 合并×2 当普通交易日（序列含 ~+100% 假跳变）或 get_history include 语义含当日 bar | D3（两端对齐/API 语义） | 外部核对清单 4 项见设计文档 §6；本地框架不可修 |
| 4 | 两端 valid 池差 8 只（本地 1128 vs PTrade 1136） | 两端行情数据差异（bar 数/成交额口径） | D3（数据源差异） | 池定义本身一致（1271=1271，已实证） |
| 5 | 主库与备份数据漂移（bak_c4merge 期末净值 82406 vs 主库保存运行 80935） | 08-13~08-16 间数据变更（回填/qfq 管线） | D1（数据） | 佐证"数据快照版本 + 黄金基线"必要性 |
| 6 | `no_price`/`halted` 拒单 INFO 级零可见、审计行=计划层与成交不符 | 引擎拒单无日志 + 审计行由策略按计划打印 | D2（框架可诊断性） | **已实施修复（QS_FILL_AUDIT）**，待快照后全量重验 |
| 7 | `test_daemon_once_exit_contract` GBK 解码失败；`test_gui_dark_panels` 等 GUI 测试挂起/失败 | 环境性（控制台编码、Qt、DB 锁竞争） | D4（测试环境） | 与 backtest_align 改动无关（AST 验证无依赖） |
| 8 | `test_order_rejection.py::test_immediate_execute_buy_success_returns_filled_order` 失败（断言含滑点价） | 断言与当前默认滑点配置不符（基线同样失败） | D4（测试） | 预存在，未处理 |
| 9 | basket drain（`_drain_baskets`，G1-I callback_basket next_open-only）直连 `_execute_buy` 的拒单未纳入采集 | 方案范围外（目标策略 legacy close 不触达） | D2（框架扩展点） | 已知扩展点，记录待后续评估 |

## 3. 持有的锁 / 资源状态

- **本会话**：无任何数据库锁。全部取证为只读连接（已关闭）；后台任务（pytest×3、回归 runner）已全部终止；无残留 python 进程（已核查）。
- **外部持锁进程（非本会话，治理门槛检查前需先处理）**：
  - PID 17576：`scripts/restore_minutes_raw.py --only=stock` — 独占 `data/quantstudio.db`（写锁）
  - PID 26416：`scripts/etf_minute_reanchor.py reanchor --main-db data/quantstudio_backup_pre_pipeline_20260816.db --batch 1 --n-batches 10` — 独占 pre_pipeline 备份（写锁，多批次任务）
- **临时文件**：`%TEMP%\backtest_engine_modified.py`（修改版引擎备份，可删）；`%TEMP%\qs_ro_probe.db`（未生成成功，无残留）。

## 4. 恢复时优先事项（按治理方案恢复指令执行，不自行判断时机）

1. 门槛检查（确认无写者持锁）→ 快照机制 → 黄金基线 → 读写隔离四步完成后，按登记表优先级恢复。
2. 本任务重验项：① 主库 2026-07 区间重跑 etf_theme_rotation，与保存运行逐字节对照；② 全量 pytest 无新增失败；③ 完成后走六步流水线第 5/6 步（用户确认 + 双仓库推送）。
