# 配置债批次 · 5 张表基准列声明清零（2026-11-02 用户批准 · 低优先）

- 实施：2026-11-02 21:1x（delivery 会话）
- 目标：消除交付包复验中 5 张表的 `UNKNOWN-BASIS` 诊断缺口（spec 003 债表登记项）
- 纪律：不改 `questdb_table_map.json`（生成型）、不改 stats 解析逻辑（仅 etf_basic 一处排除集）、不碰生产库（只读连库取证）

## 知识反哺（R1）

- 知识反哺：[命中] DB-2026-0015（族 db / D4 / can_incremental 只由有害态驱动，2026-11-01）——本批沿用其判据口径
- 知识反哺：[无命中] 「UNKNOWN-BASIS / 基准列声明 / alignment time_key」检索面无既有条目，本批沉淀为 DB-2026-0018

## 1 定列取证（只读 `data/quantstudio.db`，`read_only=True`）

| 表 | 选定列 | 类型/取值 | NULL | distinct | min → max | 选择理由 |
|---|---|---|---|---|---|---|
| ai_research_snapshot | `scan_date` | VARCHAR `YYYY-MM-DD 00:00:00` | 0 | 93 | 2026-06-12 → **2026-10-30** | 业务扫描日；`scan_time` 是单次运行墙钟（16,313 个取值、含夜间跨日 2026-10-31 01:41），不是业务日 |
| block_trade | `trade_date` | VARCHAR `YYYY-MM-DDTHH:MM:SS` | 0 | 360 | 2024-01-16 → **2026-10-30** | 大宗交易成交日 |
| broker_recommend | `timestamp` | VARCHAR `YYYY-MM-DDTHH:MM:SS` | 0 | 80 | 2020-03-01 → **2026-10-01** | 报告日（本表唯一日期列） |
| xueqiu_sentiment | `trade_date` | VARCHAR `YYYY-MM-DDTHH:MM:SS` | 0 | 72 | 2026-06-10 → **2026-10-20** | 情绪归属交易日 |
| etf_basic | **不采用** | `update_time` 全表**单值** `2026-11-02T06:02:52+08:00`；`list_date` 为上市日（BIGINT） | 0 | 1 / 971 | — | 整表刷新时刻 + 上市日都不是"随数据推进的业务日期" ⇒ 无水位语义，按用户裁定不硬造 |

（`data/quantstudio.db` 体量：quantstudio.db 62.7 GB；上述 4 表行数 16,659 / 22,581 / 14,385 / 1,184）

## 2 改动（共 4 文件，+32/−2 行 + 新测试）

| 文件 | 改动 | 行数 |
|---|---|---|
| `config/profiles/mcp_only/alignment_rules.json` | `schemas` 新增 4 条：`{_note, time_key, columns:{}}` | +21/−1 |
| `quantstudio/delivery/constants.py` | 新增 `NO_WATERMARK_TABLES = {"etf_basic"}`（值出自既有 `alignment_rules.schemas.etf_basic.dataset_kind="snapshot"`，非新增真源） | +6 |
| `quantstudio/delivery/stats.py` | `in_watermark_system()` 早退（唯一入口，最小 diff） | +10/−1 |
| `tests/test_delivery_basis_config.py` | 新增 10 例（解析契约/守卫/etf_basic 归法/不得手改 map） | 新文件 |

**etf_basic 处置 = 方案 A（排除集，非方案 B 改判定表）**：判定函数 `judge_state` 零改动，
只在 `in_watermark_system` 入口加一条排除分支（不改 watermark_basis 解析链）。
`columns: {}` 是**防御性声明**——`aligner.py:440/442` 存在 `schema["columns"]` 裸索引，
留空 dict 可确保即便未来误把该表配成非 passthrough 也不会 KeyError（空 = 不声明列预期）。

## 3 V1–V6 实测读数

| 判据 | 读数 | 结论 |
|---|---|---|
| **V1** UNKNOWN-BASIS 数 | **0**（改前 5） | ✅ |
| V1b etf_basic 归处 | `NO-WATERMARK-SYSTEM` | ✅ |
| **V2** 4 表 basis | `scan_date` / `trade_date` / `timestamp` / `trade_date`，`basis_source=alignment_rules.time_key`（非 unknown） | ✅ |
| **V3** 4 表进四态 + 水位 vs 数据 | 见下表（全部 BEHIND，**无 AHEAD**） | ✅ |
| **V4** can_incremental | **True**（reason 空字符串） | ✅ |
| **V5** ConfigLint | `0 错误, 0 警告 / [OK] 配置校验通过`（exit 0） | ✅ |
| **V6** 交付测试 | **180 passed / 0 failed**（隔离 `QUANTSTUDIO_DATA_ROOT`+`QS_WRITE_LOCK_DIR`，未触生产库） | ✅ |
| **V6b** SC-013 | **PASS**，36 保护路径，有变化 1（`writers.py` 判 `OTHER_LINE`，非本批） | ✅ |

### V3 逐表明细（水位 vs 数据止点，只报告不修正）

| 表 | 水位 last_date | 数据 max | 差 | 态 |
|---|---|---|---|---|
| ai_research_snapshot | 2026-08-03 | 2026-10-30 | 88 天 | BEHIND |
| block_trade | 2026-08-03 | 2026-10-30 | 88 天 | BEHIND |
| broker_recommend | 2026-06-01 | 2026-10-01 | 122 天 | BEHIND |
| xueqiu_sentiment | 2026-07-29 | 2026-10-20 | 83 天 | BEHIND |

**AHEAD = 0**（无异常差值）；BEHIND 全表共 8 张（原 4 + 新 4），均只进「需关注」不阻塞增量。
差值成因属运行时水位推进问题（passthrough 任务不推进 `source_watermark`），本批**不修**、如实上报。

## 4 运行期副作用申报（必须知道）

`alignment_rules.schemas` 由谁读：`aligner` / `daemon._max_date` / `quality_audit` / `delivery.stats`。

| 消费方 | 影响 | 结论 |
|---|---|---|
| 采集路由 | 由 `task.passthrough=True` 决定走 `_run_passthrough_task`，**不看 schema 是否存在** ⇒ 4 表仍绕过 aligner | 无变化 |
| `daemon._max_date` / `_get_safe_watermark` | 均为 `schemas.get(table, {}).get("time_key")` 宽容取；且 passthrough 任务不推进水位 | 无变化 |
| **`quality_audit`** | 默认全库审计 = `list(self.schemas.items())` ⇒ **审计表白名单 23 → 27 张**，这 4 张将被纳入巡检（`max(time_key)` vs 水位） | **新增覆盖（能力扩张，非回归）**；未在本批跑审计实测（60 GB 级库，按资源治理纪律不做全量巡检） |
| `delivery.stats` | 本批目标 | ✅ |

## 5 客户生效方式

- `alignment_rules.json` 在客户本地 `config/profiles/mcp_only/`，**不随数据包交付**。
- 生效通道（二选一，推荐前者）：**随程序更新版下发**（git pull 即含该配置）；或单独发送该配置文件覆盖。
- **客户升级前导入仍会看到该 `[warn]`（5 张表 UNKNOWN-BASIS），属预期**——这是配置未更新，不是数据问题。
- 已导出的旧交付包用新版复验即可清零，**无需重新导出**（复验只读包 + 本地配置）。
