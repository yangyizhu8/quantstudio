# duckdb 1.5.6 首周哨兵计数台账（T050 回传基础）

- **首周窗口**：2026-11-08 ～ 2026-11-14（哨兵执行率判据 7/7 天）
- **归属**：客户专属总运维会话（④）｜**回传对象**：duckdb 数据库升级执行会话（⑲）
- **建立依据**：`docs/handoff/duckdb-firstweek-sentinel-keywords-20261108.md` §四（回传五项）
- **台账建立日**：2026-11-09 23:5x（11-08/11-09 两日按已跑读数回填，11-10～11-14 逐日追加）
- **口径变更**：11-08 为 v4 口径（index-delete 仅扫 daemon 系日志）；**11-09 起为 v5 口径**（T050 项 1 强化，改扫 `data\logs\*.log` 全量）

## 一、五项回传字段定义（判据来源：T050 清单 §一/§二）

| # | 字段 | 判据 |
|---|---|---|
| 1 | 哨兵执行 | 11-08～11-14 共 7 天，须 **7/7** |
| 2 | `Failed to delete all rows from index` 命中总数 | **恒须 = 0**（>0 即 ALERT 当日上报总调度） |
| 3 | F4 PK 巡检 ALERT 次数 / SKIP 次数 | **ALERT 须 = 0**；SKIP 非失败但须记原因 |
| 4 | EOL 守护退出码分布 + 1.4 线状态 | 退出码 0/1/2 分布；1.4 线应持续 `close_or_accept`（11-17 后转 `expired`） |
| 5 | 采集轮质量审计失败条数 | 首周合计（**归⑳/A 类域，本方只记录不修**） |

## 二、逐日台账

| 日期 | 星期 | 执行 | keyword 命中<br>（累计） | F4 ALERT / SKIP<br>（SKIP 原因） | EOL 退出码 | 1.4 线状态 / 距 EOL | 采集轮审计失败 | 备注 |
|---|---|---|---|---|---|---|---|---|
| **11-08** | 日 | ✅ | **0**（v4 口径：daemon 系日志） | 0 / **1**（once 任务 pid=40344 持库） | **1** | `close_or_accept` / **+9 天** | 未采集（以⑲日表为准） | v5 于 22:49 落地，次日生效 |
| **11-09** | 一 | ✅ | **0**（v5 全量口径：`data\logs\*.log` 7 件扫描） | 0 / **0**（F4 **PASS exit 0**，41/101 一致） | **1** | `close_or_accept` / **+8 天** | 未采集 | v5 首轮；A9b 3/3、A7 held=0 |
| 11-10 | 二 | ✅ | **0**（v5 全量口径：`data\logs\*.log` **10** 件扫描） | 0 / **0**（F4 **PASS exit 0**，41/101、索引 0/101） | **1** | `close_or_accept` / **+7 天** | 未采集 | daemon **陈旧告警**：status pid=49060（03:03:01 启动）进程不存在、`exitcode.log` **无对应 START/EXIT** ⇒ 无痕死亡（绕记录器第 6 例）；A9b committed+absent=**0** |
| 11-11 | 三 | ✅ | **0**（v5 全量口径：`data\logs\*.log` **14** 件扫描） | 0 / **0**（F4 **PASS exit 0**，41/101、索引 0/101） | **1** | `close_or_accept` / **+6 天** | 未采集 | daemon **ALIVE**（pid=36372，05:56:23 起）；A9b committed+absent=**1**（经 A-3 判为**分钟数据缺口**，非判据缺陷，见 §七）；**observe 期满合格**（201 轮零误判，`15089cc`） |
| 11-12 | 四 | ✅ | **0**（v5 全量口径：`data\logs\*.log` **19** 件扫描；int(None)=0 / 结构性为空=1 / FP-7=0） | **0 / 1**（F4 **SKIP exit 3**——daemon 常驻持锁，库被独占；非失败不计失败数） | **1** | `close_or_accept` / **+5 天** | 未采集 | daemon **ALIVE**（pid=53244，18:30:04 起；50560 于 18:30:02 有 EXIT 对账记录，**非无痕**）；A9b **SKIP**（`IOException` 库占用）；QFQ 周期 4 条（末条 12:44 中断）；复活日志 **345 行** CALLED=0/REVIVED=0/ARMED=29 |
| **11-13** | 五 | ✅ | **0**（v5 全量口径；`index-delete`=0） | 0 / **0**（F4 **PASS exit 0**，41/101、索引 0/101） | **1** | `close_or_accept` / **+4 天** | 未采集（归⑳） | daemon **ALIVE**（pid=53244，18:30:04 起）；**15:33:54 收到停止请求 reason=user → 16:06:17 EXIT ExitCode=0 normal-exit**（uptime 1,296.3min）；**22:57:41 重启 pid=39200**（owner 显式拉起，非自动）；复活日志观察期内 CALLED=0/REVIVED=0 |
| **11-14** | 六 | ✅ | **0**（v5 全量口径：`data\logs\*.log` **266** 件扫描；`int(None)`=0 / 结构性为空=**116** / FP-7=0 / **index-delete=0**） | 0 / **0**（F4 **PASS exit 0**，41/101、索引 0/101） | **1** | `close_or_accept` / **+3 天** | **9 条 ERROR**（04:49:04，另 31 类警告 / checks=336；明细见 `qualityaudit-attribution-20261114.md`） | daemon **ALIVE→EXIT**：`14:50:49` 停采请求（`owner_pid=12140`）→ **`18:18:31` EXIT ExitCode=0 normal-exit**（uptime 1,160.8min）；`backtest_stop.flag` 至回传时**未清**；**11-14 为周六 `is_open=False` ⇒ 全表 0 行属非交易日，非缺口** |

## 三、当前累计（**截至 11-14 · 首周收官**）

| 项 | 累计值 | 判据 | 结论 |
|---|---|---|---|
| 哨兵执行率 | **7 / 7 天**（11-08~11-14 全执行） | 须 7/7 | ✅ **达成** |
| keyword 命中总数 | **0**（累计扫描 7+10+14+19+266 件/日，v5 全量口径） | 须 = 0 | ✅ 满足（连续 7 日 0） |
| F4 ALERT | **0**（全周） | 须 = 0 | ✅ 满足 |
| F4 SKIP | **2**（11-08 once 持库 pid=40344；11-12 daemon 常驻持锁 `IOException`） | 非失败，须记原因 | ✅ 已记原因（两次均非失败） |
| EOL 退出码分布 | `0`:0 / `1`:**7** / `2`:0 | 分布记录 | ✅ exit 1 属 T−30 实态，非故障 |
| 1.4 线状态 | `close_or_accept`（+9→+8→+7→+6→+4→**+3** 天） | 持续态 | ✅ 正常推进，**11-17 到期** |

## 四、11-14 回传模板（届时直接填末行数据后回传⑲）

```text
【T050 首周哨兵回传 · 2026-11-08~11-14】（2026-11-15 00:55 补齐 · ④侧）
1. 哨兵执行率：7/7 天（目标 7/7）✅
2. 缺陷关键词命中总数：0（须 0）✅（连续 7 日 0；11-14 扫 266 个 *.log 全量）
3. F4 PK 巡检：ALERT 0 次（须 0）✅／SKIP 2 次；SKIP 原因：
   ① 11-08 once 任务 pid=40344 持库；② 11-12 daemon 常驻持锁 IOException。
   两次均非失败，不计失败数。
4. EOL 守护退出码分布：0→0 次／1→7 次／2→0 次；
   1.4 线状态：close_or_accept（距 EOL +9→+3 天，11-17 到期后转 expired）
   ※ exit 1 属 T−30 实态，非故障（按 §五 判据）
5. 采集轮质量审计失败条数：9 条（11-14 04:49:04，另 31 类警告/checks=336）
   ※ 归⑳/A 类域，本条只作记录不修；明细 docs/evidence/qualityaudit-attribution-20261114.md
台账：docs/evidence/duckdb-firstweek-sentinel-ledger-20261109.md
```

## 五、判据与失败处置（速查，来自 T050 清单 §五）

| 现象 | 处置 |
|---|---|
| 第 6 步 exit 0 | 正常，无需处理 |
| 第 6 步 exit 1 | **不是故障**，产出待办；按 90/60/30 推进 |
| 第 6 步 exit 2 | 计入失败；排查 `docs/duckdb-version-eol-calendar.json` 与工作目录 |
| keyword 命中 > 0 | **ALERT 当日上报总调度**（duckdb#23645 致命回归） |
| F4 exit 2 | 按 runbook §3 处置（`docs/handoff/duckdb-pt01-passthrough-runbook-20261029.md`） |
| F4 exit 3 | SKIP 非失败、不计失败数，日报标注原因 |

## 六、边界申报

- 本台账为**只读观测的汇总**，不改变任何哨兵行为；
- 每日数据由既有哨兵（`schedule-b59df0be`，每日 20:30）产出，本台账仅做计数累积；
- 采集轮质量审计失败条数**不由本方采集**（归⑳/A 类域），回传时以⑲日表为准，本方标注"未采集"以示不越界。

## 七、A9b `absent` 判定结论与分钟数据缺口登记（2026-11-12 03:33 只读核查）

### 7.1 触发

11-11 哨兵轮 A9b 报 `committed + postcheck_summary LIKE '%absent%' = 1`（11-10 为 0）。判据片段：

```
event_id=fd30291214c2777c471d53fa2f584f2e03f7f4fe  created_at=2026-11-11 14:01:18  status=committed
daily_staged_match : {staged_count:1465, matched:1465, missing_target:0, mismatch:0}   ← 日线全匹配
minute_front_coverage : "absent"                        ← 结果
minute_staged_match   : {"skipped": true, "reason": "minute_table_empty"}
minute_table_total    : 257,064,849                     ← 全表非空（易误读为"非空却报空"）
```

**关键陷阱**：`minute_table_total` 是**全表累计行数**，而 `minute_table_empty` 判的是**当日分区**——两者不是同一测点，不能互相否证。

### 7.2 判定证据（直接查库，非推断）

| 表 | 时间列类型 | 11-09 | 11-10 | **11-11** | 11-12 | `max(time)` |
|---|---|---|---|---|---|---|
| `stock_minutes` | `time BIGINT`（**epoch 毫秒**） | 1,258,502 | 1,354,858 | **0** | 0 | **2026-11-10 15:30:00 +08** |
| `etf_minutes` | `time BIGINT`（epoch 毫秒） | 418,858 | 526,585 | **0** | 0 | **2026-11-10 15:00:00 +08** |

> 取证要点：`CAST(time AS DATE)` 会报 `Unimplemented type for cast (BIGINT -> DATE)`——`time` 是 **epoch 毫秒**（如 `1794295800000`），**不是** `YYYYMMDDHHMMSS` 整数、也不是 DATE。按错误区间过滤会得到「全 0」的假读数（首次判定即踩此坑，已纠正）。

### 7.3 判定

> **结论：`minute_table_empty` 判定正确 ⇒ `minute_front_coverage = absent` 属真实状态。**
> **判据无缺陷，不立案、不走六步（R2 审核口径：「判当日分区空 ⇒ 记台账转数据缺口」）。**

理由：11-11 14:01 执行 rebase 时，分钟表**当日分区确实为 0 行**（最新数据止于 11-10 收盘），故 `minute_staged_match` 跳过、`minute_front_coverage` 报 `absent` 是**如实反映**。

### 7.4 ⚠️ 由此暴露的新数据缺口（独立于 A9b，需另线处置）

| 事实 | 读数 |
|---|---|
| 11-11 是**交易日**（周三） | 是 |
| `stock_minutes` 11-11 行数 | **0**（11-09 有 125.8 万、11-10 有 135.5 万） |
| `etf_minutes` 11-11 行数 | **0**（11-09 有 41.9 万、11-10 有 52.7 万） |
| 最新分钟数据时点 | **2026-11-10 15:30 / 15:00** |
| 同期 daemon 状态 | 11-11 05:56:23 起 ALIVE（pid=36372），至今存活 |

⇒ **daemon 在跑但 11-11 全天分钟数据未入库** = 分钟采集链路缺口，**不是 A9b 判据问题**。
**归口**：数据面（分钟采集/写入管线，归 ⑳ 或相应采集线）；**本方只登记不修**（遵守 §六 边界）。
**上报**：随本台账与哨兵日报呈总调度，供派单。
