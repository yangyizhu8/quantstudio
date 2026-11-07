# db 族候选备忘（第三批·待族批议）+ 首周观测预备卡

- **日期**：2026-11-07｜**归属**：duckdb 数据库升级执行会话（节点⑲）｜**状态**：候选起草（未入族，待族批议）

---

# 一、db 族第三批候选（两条，源自 11-07 窗口实战踩坑）

## 候选 ①：BIGINT epoch-ms 时间列不可按字符串比较（`$1` 覆盖全部时间类表）

| 字段 | 内容 |
|---|---|
| **symptom** | 对 `stock_daily` / 分钟类表按日期过滤时写 `where time >= '2026-10-01'` ⇒ `ConversionException: Could not convert string '2026-10-01' to INT64`——时间列是 **BIGINT epoch-ms**，不是字符串/日期类型 |
| **root_cause** | 本项目时间列统一为 epoch-ms BIGINT（`time` / `trade_date` 等），SQL 中与字符串字面量比较时 DuckDB 走隐式转换 ⇒ 试图把字符串转 INT64 而失败；且**列名本身也不统一**（`trade_calendar.cal_date`、`stock_daily.time`、其他表 `trade_date`），凭记忆写列名必然错 |
| **fix_target** | 查询纪律：①时间过滤一律 `time >= epoch_ms(TIMESTAMP 'YYYY-MM-DD')`；②列名先 `DESCRIBE <table>` 或看 `Candidate bindings` 确认，不凭记忆；③跨表脚本把「表→时间列名」做成映射表（避免逐表踩） |
| **evidence** | `docs/evidence/duckdb-1.5-upgrade-window-evidence.md` §7.2（1.4.5 下 SELECT 两次失败→修正后四项读数齐备） |
| **fixture** | 同 §7.2（可重跑：`select count(*) from stock_daily where time >= epoch_ms(TIMESTAMP '2026-10-01')` ⇒ 116,339） |
| **fix_layer** | `artifact_rule`（查询纪律/制品规则）或 `constraint`（schema 类型契约）⇒ 待族批定 |
| **domain** | 按族 README §2.2：**D3**（约束与 schema 契约）——时间列类型属 schema 契约面 |

## 候选 ②：工作目录中的同名文件会遮蔽标准库（`%TEMP%\inspect.py` 案）

| 字段 | 内容 |
|---|---|
| **symptom** | 临时脚本置于 `%TEMP%` 执行时 `import duckdb` 抛 `AttributeError: module 'inspect' has no attribute 'getmro'`——`importlib.metadata` 内部调用的标准库 `inspect` 被**同目录下的 `inspect.py`** 顶替 |
| **root_cause** | Python 以**脚本所在目录**为 `sys.path[0]`；`%TEMP%` 里存在其他工具/会话遗留的 `inspect.py`（实测存在）⇒ 标准库被遮蔽。`python -c` 时 `sys.path[0]=''`（cwd）⇒ 不触发，故同一代码「-c 能跑、脚本跑不了」 |
| **fix_target** | 临时脚本落点纪律：①**不要**把验证脚本写到 `%TEMP%`；改落仓库 `data/logs/`（已确认无同名遮蔽）或专用 `_tmp/` 目录；②或执行时加 `-E -s`（仅挡 PYTHONPATH/用户 site，挡不住脚本目录本身遮蔽——故**首选换落点**）；③报错信息出现标准库属性缺失（`inspect.getmro` / `json.dumps` 等）时，**先怀疑同名文件遮蔽**再怀疑依赖损坏 |
| **evidence** | 同 §7.2（`data/logs` 跑通、`%TEMP%` 失败，两处对照） |
| **fixture** | 复现：`Test-Path $env:TEMP\inspect.py` ⇒ True（本机实证） |
| **fix_layer** | `artifact_rule` |
| **domain** | **D5**（工程纪律与制品一致性） |

> 两条均经实战两轮实证（失败→定位→修正→成功），符合 db 族「缺陷→可回哺」要求；**入族需族批裁定**，本方不预写条目。

---

# 二、首周观测预备卡（T049 / T050，daemon 恢复供血后即跑）

> 窗口日 **2026-11-07** ⇒ 首周观测期 **11-08 ～ 11-14**（7 天，含 11-08/09 周末）。

## 2.1 T049 观测面六项（逐项须注来源）

| # | 指标 | 采集法 | 判据 | 来源标注 |
|---|---|---|---|---|
| 1 | 窗口实测分钟 | 已得：**11 分钟**（02:05:03→02:16:08） | ≤60 | 证据 §7.1 |
| 2 | S1–S6 通过数 | 已得：**6/6** | 6/6 | 证据 §7.1 |
| 3 | `Failed to delete all rows from index` 计数 | `Select-String -Path data/logs/*.log -Pattern 'Failed to delete all rows from index'` | **须 = 0** | daemon 运行日志 |
| 4 | WAL 体积 | 窗末实测 **无文件**；首周每日巡检 `data/quantstudio.db.wal` | <256MB | 文件系统实测 |
| 5 | daemon 首轮耗时 | daemon 日志首轮 `[batch] ... 完成` 时间戳差 | 记录即可（无硬阈值；A-14 风险项在辅助表） | daemon 日志 |
| 6 | E7 升级前后基线 | 升级前 = W3（62.47GB/1.4.5）；升级后 = W9（62.47GB/1.5.6） | 成对在案 | 证据 §7.1 |

## 2.2 T050 首周哨兵关键词（并入每日哨兵）

```
关键词 1（上游缺陷 FATAL 文本）: Failed to delete all rows from index
关键词 2（F4 PK 巡检）: python scripts/check_passthrough_pk.py --check   （退出码 2 = ALERT / 3 = SKIP 非失败）
判据: 关键词 1 命中数 = 0；哨兵执行率 7/7 天
```

## 2.3 我方待外部条件（不在本轮动作内）

| 任务 | 阻塞条件 | 解除后动作 |
|---|---|---|
| T049 / T050 | **daemon 恢复供血**（归节点1 P0 流程） | 按 2.1/2.2 跑并落 evidence |
| T041 | M-5 + 用户闸（≤11-17） | 通知下发（本会话不代发） |
| T055 | 第二批组批通过 | 双仓推送 + 双远程核对 |
| T058 | ④排期 | 哨兵第 6 步接入验证回执 |
