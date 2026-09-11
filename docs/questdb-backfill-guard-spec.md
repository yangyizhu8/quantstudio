# 回填编排器 守护规格（S1 四件套 + S5 避让点）

> 来源：运维侧实测（2026-09-11 夜 / 09-12 凌晨） · 总调度裁定"你方探测点清单即规格"（2026-09-12）
> 用途：并入回填编排器实现（不单独出脚本） · 判据与动作均取自实测事故

## 一、S1 单写者探测（编排器启动前 + 每表切换时）

| # | 探测 | 命令/判据 | 动作 |
|---|---|---|---|
| 1 | 陈旧锁残留 | `data/.daemon.lock`、`data/.collector_run.lock` 存在且 **0 字节** = 陈旧 | 无进程持有时**删除**（2026-09-11 夜实测两锁均残留） |
| 2 | write_lock | `data/snapshots/.write_lock` 存在 → 读 holder JSON（`STALE_SECONDS=600`） | 超时=陈旧→清理；否则**等待** |
| 3 | 写者进程 | `Get-CimInstance Win32_Process` 筛 `CommandLine -match 'quantstudio'` | 有 → **拒启动**（等其退出） |
| 4 | duckdb 单写者 | 尝试只读打开 `data/quantstudio.db` | 报 `IO Error: ... File is already open in <exe> (PID n)` = **被占** → 拒启动 |

**四件套全 PASS 才可写**；每张表切换前重跑 #3/#4（长任务跨时段）。

## 二、S5 避让窗口（写入期暂停、表级断点续跑）

| 时间 | 任务 | 动作 |
|---|---|---|
| **16:00–20:5x** | `Trading_Daily_ETL_1600`（含 check_etl_integrity）| **全停**（ETL 独占写）；建议 15:45 起不再开新表 |
| **21:45** | `Trading_EtfAdjEveningFill` | 停写，待其完成 |
| **22:30** | `Trading_CyqChips_Evening_Fill` | 停写（cyq_chips 相关，与回填表重叠） |
| **21:00** | `Trading_MinutesDailyBackfill_2100` | 停写（分钟表，虽移出范围但共用 QDB） |
| **03:00** | `TradingCloudSync` | 停写（QDB 重度读） |
| **05:00** | `TradingCloudParity` | 可读不可写（对拍需稳定水位） |
| **08:55** | `Trading_MinutesCoverage_0855` | 停写 |
| **09:05** | `Trading_PreMarket_Check` | 停写（盘前检查） |

> 窗口判定建议：读计划任务 `NextRunTime` 或直接按上表时段守则（跳过窗口 + 前后各留 5 分钟缓冲）。
> 断点续跑：按表级水位（方案 §2.1 已具备）；**禁止半表残留**（方案 §七：未完成表 truncate+reload）。

## 三、WAL / 磁盘守护

| 项 | 判据 | 动作 |
|---|---|---|
| duckdb WAL | `data/quantstudio.db.wal` 体积（实测基线 0 GB） | 显著增长 → 暂停并 checkpoint |
| QuestDB WAL | ⚠️ **QuestDB 9.3.5 不支持 `CHECKPOINT` 语句**（HTTP/PG 双通道实测报错：400 / `'create' or 'release' expected`）；改用 `wal_tables()` 查 `suspended=false` 且 `writerTxn==sequencerTxn`（=无堆积）+ WAL 目录体积 | 有堆积 → 停写待应用 |
| 磁盘 | D 盘（2026-09-12 00:5x 实测余 **311.1 GB**）| 余量 < 80 GB → 暂停（快照 35 GB + 增长 20-40 GB） |

## 四、附：结构基线交付（供 `questdb_table_map.json` 打底）

**文件**：`data/logs/questdb_structure_baseline.json`（64 表全量）

每表含：`columns[(name,type)]` + `timestamp_cols` + `qdb_rows` + `basis_col/min/max`（唯一时间列时自动派生）

| 项 | 结果 |
|---|---|
| 双通路一致性（本地 `SHOW COLUMNS` × 云端 `describe_dataset`）| **10/10 逐列完全一致**（列序/类型/时间列指定；含 T1 三样本） |
| 时间列唯一（**日期基准可自动派生**）| **55 / 64** |
| 时间列缺失 | 0 |
| **多时间列 → 需人工裁决基准** | **9 表**（详见下表） |

**9 表待裁决清单**（候选时间列，需按业务语义择一）：

| 表 | 候选时间列 |
|---|---|
| `index_classify` | in_date / out_date / ingest_time |
| `sector_top300_daily` | trade_date / created_at |
| `rsshub_raw` | pub_time / fetched_at |
| `news_sentiment` | trade_date / news_date |
| `llm_text_events` | publish_time / fetch_time / source_watermark / ingest_time |
| `llm_text_events_enriched` | 同上 |
| `llm_text_raw_feed` | 同上 |
| `report_rc` | report_date / create_time |
| `ths_member` | in_date / out_date / ingest_time |

> 建议：业务基准优先取「事件时间」（publish_time / trade_date / report_date）；
> 但**水位推进基准**可能需取 ingest_time/source_watermark（与云端更新语义对齐）——
> 二者可分离声明（`date_basis` 业务键 vs `watermark_basis` 水位键），请数据会话裁定。

## 五、日期基准分布（自动派生，供交叉核对）

`trade_date` 39 表 · `ann_date` 2 · `publish_date` 2 · 其余 14 类各 1
（inserted_at / list_date / report_date / subscribe_date / report_end_date / datetime /
ingest_time / hold_date / scored_at / ts / surv_date / event_time …）
