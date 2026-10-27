---
title: 件2 close_front_ratio 主库回写 · 明晚 daemon 切换窗执行预案（Runbook）
date: 2026-10-27（编写）/ 2026-10-28（执行窗）
task: A4 前置静态审读产出（总调度 2026-10-27 裁：A4 占明晚 QS_DaemonForever 切换窗，与件2 回写同批）
status: 预案已备，待窗口执行
---

# 一、脚本审读结论（`agent_workspace/first_board_high_open_1to2/_backfill_ratio.py`，37 行）

| 项 | 实现 | 判定 |
| --- | --- | --- |
| 默认模式 | `duckdb.connect(db, read_only=not apply)`；无 `--apply` 即只读 | ✅ **默认 DRY-RUN，安全** |
| 幂等性 | `WHERE close_front_ratio IS NULL`（只填 NULL）+ 结尾回查 `left` | ✅ **幂等**（二次跑 todo=0） |
| 除零/空值保护 | `close is not null and close <> 0 and close_front is not null` | ✅ 已保护 |
| 时区对齐 | `ms()` = UTC 语义 − 8h ⇒ CST 零点 ⇒ 与库内 true epoch ms 一致 | ✅ 对齐 |
| **分块** | 注释称「按日分块」，**实际为单条区间 UPDATE**（L28-31 一次覆盖 whole range） | ⚠ **注释与实现不符** |
| **断点续跑** | 靠 `IS NULL` 天然可续；但**单事务大 UPDATE** 若中途中断则事务回滚 | ⚠ **长窗风险** |

# 二、执行清单（窗口内照做）

## 步骤 0 · 窗口前置（daemon 生命周期纪律铁律 19）

1. 会话内**声明占用窗**（时段 + 操作类型 + 预期恢复时点）
2. 确认 `QS_DaemonForever` 已停（与 dev 出窗方案同批执行，不单独停）
3. 确认无他线在跑窗口（避让不接管）

## 步骤 1 · DRY-RUN（只读，零风险）

```
python agent_workspace/first_board_high_open_1to2/_backfill_ratio.py \
  <主库路径> 2026-01-05 2026-08-13
```

**预期**：`待补行数 = 813140`（副本口径；主库若已有部分则小于此数）+ 3 条样例 ratio
**判定**：样例 `close_front/close` 应在合理区间（同一标的 ratio 恒定；跨标的可不同）

## 步骤 2 · 计时应用（写主库）

```
Measure-Command { python agent_workspace/first_board_high_open_1to2/_backfill_ratio.py \
  <主库路径> 2026-01-05 2026-08-13 --apply }
```

**记录**：实际耗时（供 daemon 窗时长预算）
**判定分岔**：
- 耗时 **< 5 分钟** ⇒ 单区间 UPDATE 可接受，**不改脚本** ✅
- 耗时 **5~15 分钟** ⇒ 可接受但记录风险（中断即回滚重跑）⚠
- 耗时 **> 15 分钟** ⇒ **建议改按日/按月分块**（需另行批准，属编码改动）⚠

## 步骤 3 · 幂等复跑验证（只读）

再跑一次步骤 1 ⇒ **预期 `待补行数 = 0`** ⇒ 幂等成立 ✅（**这是 A4 的核心验收判据**）

## 步骤 4 · 回查与登记

```sql
select count(*) from stock_daily
 where time between <start> and <end>
   and close_front_ratio is null and close is not null and close <> 0
```
⇒ 预期 0（或仅剩 close/close_front 为 NULL 的行，那些**本就不该补**）

登记：台账 §五 件2 条目更新为「✅ 主库回写完成（时长 X 分，幂等 PASS）」

# 三、风险与回退

| 风险 | 触发条件 | 处置 |
| --- | --- | --- |
| 单事务超长 | 步骤 2 耗时 > 15 分钟 | 中断（Ctrl-C）⇒ DuckDB 回滚 ⇒ 主库无残留 ⇒ 申请分块改造 |
| ratio 异常 | 样例 ratio 与同标的其它日期不一致 | 停止，先查 close/close_front 数据质量（**不得带病写入**） |
| daemon 争用 | 窗口内 daemon 被他人启动 | 立即停止写操作，声明冲突，重排窗口 |

> **回退**：若已写入但发现 ratio 口径错误 ⇒ 需 `UPDATE stock_daily SET close_front_ratio = NULL WHERE ...`（**须先经 ⑤**，因为这是产出侧批量改写）

# 四、本件性质

静态审读 + 预案，**未执行任何写操作**（脚本单测 `--apply` 未跑；主库未触碰 ✓）。执行结果待明晚窗另行出件。