# minutes 表写冲突治理 · 验收证据件（六步③④，2026-10-22）

- 件名：minute 表写写冲突治理——A1-A10 实施与验收证据
- 方案：`docs/minutes-write-conflict-design.md`（v2，②审有条件通过）
- 实施位置：**worktree `D:\miniQMT策略实盘\QuantStudio.wt-minutes`（分支 wf-minutes-fix）**——主树保持基线至 ④验收窗（A 案裁定）
- 判型：纯恢复型（任一批不因并发丢）+ 新增检测型（登记表/断言/重试统计，只增观测）

## 一、功能点与证据

| 点 | 内容 | 证据 |
|---|---|---|
| A1 | 失败槽补主 upsert 路径 + 写者登记表（pid/thread/table/path/batch/time 段/TTL） | `writers.py`（record_write_failure / price_write_enter / price_write_exit / concurrent_writers）；单测 3 条 |
| A2 | 并发写者指认（同表同时段→指名；无 peer ⇒ 跨进程候选） | 单测 2 条（同段指认 / 异段不计） |
| A3 | 锁覆盖断言（域限 `_SERIAL_WRITE_TABLES`；`QS_PRICE_WRITE_ASSERT=0` 可关；fail-fast） | 单测 4 条（无锁触发 / 持锁通过 / daily 不误伤 / 开关关闭） |
| A4 | 持锁并发写 ⇒ 0 冲突 | 真库夹具 2 线程 × 200 行同 key → 0 异常 |
| A5 | 负向对照：重叠事务同键 ⇒ 确定性冲突；绕过锁 ⇒ A3 fail-fast | 单测 2 条（`Conflict on update` 确定性复现 / bypass 触发断言） |
| A6 | 有界重试（默认 3；整操作重放；`QS_WRITE_RETRY_MAX=0` 关闭；冲突族判定） | `write()` 重试循环 + `is_write_conflict/_retry_backoff/write_retry_stats`；单测 5 条（成功一次 / 耗尽 / 非冲突不重试 / 关闭 / 分类器） |
| A7 | 幂等（重试重放同批不产生重复行；重复写不增行） | 单测 2 条（写后冲突→重试→行数仍 50、PK 无重复；重复写 30 行不变） |
| A8 | 多轮补跑至追平（止点至当日） | **待 ④验收窗**（需新码 daemon/once + 数据库；生产现为基线）；配套脚本 `scripts/verify_minutes_recover.py` |
| A9 | 观测：run_state 附 `write_retry`（attempts/exhausted/success） | `daemon_lifecycle.write_run_state` 合并 `write_retry_stats()` |
| A10 | 本证据件 + 回退开关 | `QS_WRITE_RETRY_MAX=0`（关重试）/ `QS_PRICE_WRITE_ASSERT=0`（关断言）/ `QS_PRICE_WRITE_SERIAL=0`（仅测试进程负向对照） |

### 测试汇总（worktree）

```
pytest tests/test_minutes_write_conflict.py -q   → 19 passed（A1×3 + A2×2 + A3×4 + A4×1 + A5×2 + A6×5 + A7×2）
pytest tests/test_writers_rw_backoff.py -q      → 8 passed（既有回归零衰减）
py_compile writers.py / qfq_reanchor_engine.py / daemon_lifecycle.py → exit 0
```

## 二、关键机理发现（2026-10-22 实证矩阵）

| 构造 | 结果 | 结论 |
|---|---|---|
| 两线程单语句 upsert 同 key（无锁） | 不冲突 | DuckDB 单语句写内部串行——**短写复现不了实弹冲突** |
| 两事务同时 UPDATE 同一行 | 第二个写者 **`Conflict on update!`**（确定性） | 冲突需**重叠事务**——实弹形态 = QFQ 引擎长事务 × 任务写 |
| 绕过 table_write_lock | A3 断言 fail-fast | 无锁写不再静默（新增检测型生效） |

⇒ 根因候选收窄为：①引擎长事务与任务写的**提交时序重叠**；②**跨进程写者**（A1 登记表 + pid 将在 ④验收实弹定谳）。

## 三、实弹记录（2026-10-21/22）

| # | 时间 | 任务 | key | 结果 |
|---|---|---|---|---|
| 14 | 10-21 22:31:14 | mcp_etf_minutes | `159512, 1789090200000, 1min` | 失败（once 补跑） |
| 15 | 10-22 00:23:25 | mcp_etf_minutes | 同上 | 失败（path=`_write_passthrough`） |
| 16 | 10-22 01:07:23 | mcp_stock_minutes | `000055, 1788744600000, 1min` | 失败 |
| 17 | 10-22 07:55:31 | mcp_stock_minutes | 同上 | 失败（path=`_write_passthrough`） |

- `etf_minutes` 自 10-21 23:37 起被他线止损**停用**（`e7f366b`，W-7 落盘分片损坏）——**停用≠复原**，止点仍 2026-10-09；10-30 收口须 W-7 + 本方案双族同治后重新启用。
- 第 17 次后（06:00 轮）daemon 6080 健康、72/73 成功、无新增失败族。

## 四、A8 验收协议（④验收窗执行，不在本件预跑）

1. 停启窗（声明占用）→ 新码 daemon（或 `--mode once --task mcp_etf_minutes/stock_minutes`）；
2. 每轮后执行 `python scripts/verify_minutes_recover.py --db data/quantstudio.db --target 2026-10-22`；
3. 未追平则记录轮数并继续下一轮（finish 条件：max(time) 到达 target 或连续两轮无推进）；
4. 追平后核：`SELECT count(*)` 与 PK 重复检查（A7 同口径）、WAL 收敛。

## 五、遗留与边界

- **引擎侧重试未纳入**（A6 仅 writer 侧；引擎事务含 event/anchor 写，重放需避免重复事件）→ 若 ④验收实弹显示失败方为引擎，另立 A6b；
- 分片变体（`write_passthrough_chunked`）复用既有 ledger resume（不盲重放）——本轮未改动，③④复核；
- 混合加载教训（2026-10-22 02:31）：**运行中切树 + 懒加载 = 版本混载风险**；本方案实施改在独立 worktree，④验收一次性切树 + 重启。
