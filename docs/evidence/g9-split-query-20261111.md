# G9 三联查分解 SQL 执行记录（2026-11-11 10:23–10:29）

- 指示：节点2「G9 分解 SQL 今日执行（多轮顺延候静默窗）——绝对行数 + dead_letter + MAX(updated_at) 三联查，分钟级只读」
- 结论：**生产库仍无静默窗，改在副本执行完毕**；三联查 + 三判据复算均已完成

---

## 一、生产库无静默窗（客观实证）

```
duckdb.connect(read_only=True) →
IOException: Cannot open file "...\data\quantstudio.db": 另一个程序正在使用此文件
File is already open in python.exe (PID 36372)
```

- daemon `PID 36372`：`-m quantstudio.pipeline.daemon`，**start 11-11 05:56:18**，CPU 13,241 s，仍存活
- 主库 `quantstudio.db` 63,553.5 MB，`mtime 11-11 08:25:14`（今晨在写）
- ⇒ **DuckDB 单写者排他，daemon 从启动到收工不释放锁**（与既有铁律「`--mode forever` 常驻持锁」一致）
- ⇒ **「轮次任务间有间隙窗」这一前提不成立**——`轮次间隙` 不等于 `锁释放`

## 二、副本执行读数（`D:\qs_g6_replica_20261110\quantstudio.db`，62.06 GB，只读打开 1.72 s）

### ① 绝对行数（三联查之一）

| 表 | 行数 | 耗时 |
|---|---|---|
| `qfq_trigger_queue` | **123,573** | 0.19 s |
| `qfq_factor_observation` | **（不在主库）** | — |
| `qfq_factor_revision_alert` | **（不在主库）** | — |

> 注：`qfq_factor_observation` / `qfq_factor_revision_alert` / `qfq_obs_incremental_cursor` 均在**辅助库 `qfq_aux.db`**（G6 首窗读数 45,122,324 行），主库无此表 —— 属**既有架构**，非缺失。

### ② dead_letter（QfqDeadLetter 原 SQL，`1=1` 全范围）

| status | n |
|---|---|
| pending | 46,035 |
| committed | 30,488 |
| superseded | 27,836 |
| **dead_letter** | **18,713** |
| retryable_failed | 501 |

**dead_letter 明细**：`created_at` 区间 **2026-08-16 03:35:23 ~ 2026-11-06 14:13:55**；峰值日 10-22（954）、10-28（651）；**最近 4 天无新增**（MAX 为 11-06）。

### ③ MAX(updated_at) / 时间界（三联查之三）

| 项 | 值 |
|---|---|
| `MAX(updated_at)` | **2026-11-10 13:44:37** |
| `MIN(updated_at)` | 2026-08-01 01:23:45 |
| `MAX(created_at)` | 2026-11-10 09:37:39 |
| `MAX(claimed_at)` | 2026-11-10 09:37:39 |
| 副本快照时刻 | 2026-11-10 14:29:05（G6 首窗 daemon finished） |

## 三、三判据复算（替代整轮 QA）

**阈值来源（实读 `quantstudio/pipeline/quality_audit.py:731-733`）**：

| 键 | 默认值 |
|---|---|
| `dead_letter_max` | **0** |
| `pending_sla_hours` | **72**（此前我按 24 估算，**错误**） |
| `stale_in_progress_hours` | 24 |

| 判据 | 读数 | 阈值 | 判定 |
|---|---|---|---|
| **QfqDeadLetter** | **18,713** | > 0 即 error | 🔴 **error** |
| **QfqPendingSla** | **45,945**（快照基准 72h） | > 0 即 error | 🔴 **error** |
| **QfqStaleInProgress** | **0** | > 0 即 error | ✅ **pass** |

**SLA 敏感度（快照基准）**：24h ⇒ 46,015 ｜ 48h ⇒ 45,945 ｜ 72h ⇒ 45,945 ⇒ **对阈值不敏感 = 真积压**。

## 四、🔴 自我纠正：上一判断「假阳性」是错的

我先按「副本 `NOW()` = 11-11 10:24 而数据止于 11-10 ⇒ 时间流逝造成假阳性」推断 46,046 不可信。
改用**快照时刻**（11-10 14:29:05）重算后：72h ⇒ **45,945**，与真 `NOW()` 的 46,046 仅差 **101 条（0.2%）**。

⇒ **结论修正**：**不是假阳性，是真实的长期积压**（4.6 万条 pending/retryable_failed 超 72h）。
⇒ 可复用判据：**在历史快照上跑含 `NOW()` 的判据，必须先用「快照时刻」重算对照**——差值若小（<1%）说明是真信号，差值若近全量才是时间基准问题。

## 五、副本处置：清理（无后续复测需求）

| 判断项 | 结论 |
|---|---|
| G6 首窗对拍 | 已完成（sha256 三文件全 IDENTICAL），无需副本 |
| L2（`_inc_fetch_last`）修复验证 | 已完成（`b37117b`，A1/A2/A3/A6 绿） |
| L3 测量条件 | 已收口归档（改进项关闭）；若重开前提＝同缓存态对照测法，**可重拷** |
| G9 三联查 | **本轮已用副本完成**（含阈值实读）⇒ 使命终结 |
| ㉑ L2 是否需 DuckDB 副本 | **不需要**：L2 是 SQLite（`qfq_aux.db`），且修复已入库 |

⇒ **无后续复测需求，执行清理**（释放约 74.4 GB）。

2026-11-11 10:29