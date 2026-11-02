# V3 口径修正 + D5-a/D5-b 守卫 · 实施与读数（2026-11-03 00:0x）

- 节点：`水位线和全部执行增量拉取修复执行会话`（图行 20）
- 依据：总调度 V3 裁决（passthrough 表按 FR-006 v5 归 NO-WATERMARK-SYSTEM，两侧同口径）；
  D5-a+D5-b 先于 ②审其余项特准实施（三条件：纯守卫零放宽 / V0-b+单测同批 / 明晨 ②审追认）
- 状态：**③实施完成，未推送**（等 ⑤用户确认）；②审明晨追认

---

## 1 B 项：V3 口径修正（4 张 `*_pt` 表归 NO-WATERMARK-SYSTEM）

### 1.1 判定顺序核对（总调度问"为何 passthrough 的 dataset_kind 没生效"）

- 旧实现：`in_watermark_system()` 只看 ①`NO_WATERMARK_TABLES` ②`watermark_tables` ③`table_map[table].dataset_kind`；
- 这 4 张表 **out_of_map**（`questdb_table_map.json` 零命中）⇒ `spec = table_map.get(table) or {}` ⇒
  `kind = None` ⇒ 代码走"保守视为属水位体系"分支 ⇒ **True**；
- ⇒ 判定入口**依赖 map 覆盖**，而 map 是生成型的，passthrough 标记在 `collector_tasks.json` 的 `task.passthrough`。
  **按要求改读 task 标记，不依赖 map**。

### 1.2 改动（两侧同口径）

| 文件 | 改动 |
|---|---|
| `quantstudio/delivery/stats.py` | `in_watermark_system(..., passthrough_tables=None)`：命中即 False；`collect_table_stats` 透传 |
| `quantstudio/delivery/verify.py` | 从 `collector_tasks.json`（**实际是 list**）解析 `passthrough=true` 的表集并透传 |
| `quantstudio/pipeline/quality_audit.py` | 新增 `no_watermark_tables`；`_audit_watermarks` 跳过这些表（不再产 `WatermarkConsistency`） |
| `quantstudio/pipeline/daemon.py` | 构造 `DataQualityAuditor` 时传入 `NO_WATERMARK_TABLES ∪ {task.passthrough 表}` |

### 1.3 读数（真实数据，只读）

| 项 | 修正前 | 修正后 |
|---|---|---|
| `state_counts` | `{NO-WATERMARK-SYSTEM: 51, BEHIND: 8, OK: 11, CALENDAR-NOT-APPLICABLE: 1}` | **`{NO-WATERMARK-SYSTEM: 55, BEHIND: 4, OK: 11, CALENDAR-NOT-APPLICABLE: 1}`** |
| BEHIND 清单 | 8 张（含 4 个假 BEHIND） | **`etf_daily` / `etf_minutes` / `stock_daily` / `stock_minutes`**（= 预期 4 张） |
| 4 张 PT 表归属 | BEHIND（假态） | **NO-WATERMARK-SYSTEM**（`ai_research_snapshot` / `block_trade` / `broker_recommend` / `xueqiu_sentiment`） |

**归属依据（FR-006 v5 原文要点）**：passthrough 表 = 同名全量覆盖、**不推进 `source_watermark`** ⇒
无水位语义，标 `NO-WATERMARK-SYSTEM` 且**不输出水位/max_date**、**不参与四态**。
本批只是实现未跟上，判据本身无需新立议题。

### 1.4 巡检侧口径确认（总调度要求"噪音不许换面板"）

两向读数（`tests/test_delivery_passthrough_no_watermark.py::test_audit_watermarks_two_way_on_synthetic_db`）：

| 场景 | `WatermarkConsistency` 条数 |
|---|---|
| 不排除（`no_watermark_tables=None`） | **4**（每张 PT 表一条） |
| 排除（传入 4 张） | **0** |

⇒ 交付侧与巡检侧**同口径**：不会出现"交付侧已排除、巡检侧仍报警"。

---

## 2 A 项：D5-a / D5-b 守卫（纯拒绝，零放宽）

### 2.1 实现

| 子项 | 实现 |
|---|---|
| **判据** | `qfq_aux_router.looks_foreign_absolute(raw, *, windows_host)`：POSIX 宿主识别 `X:\` / `X:/`；Windows 宿主识别 `/abs`（排除 UNC/`//?/`） |
| **D5-a** | `_resolve_path()` 入口守卫 + **构造期守卫**（`explicit_default` 在 `Path().resolve()` **之前**拦） |
| **D5-b** | `path_for()` 入口守卫（点名校验对象是 `qfq_source_cutover.aux_db_path` 记录） + **解析后兜底**（内嵌盘符 `[A-Za-z]:[\\/]` 再拦一次） |

### 2.2 实施中两处自纠（如实申报）

1. `stats.collect_table_stats` 首次透传没打上（目标串写作 `watermark_tables`，实为 `wm_tables`）⇒ 症状：
   PT 表仍触发 `SELECT max("scan_date")` 报 Binder Error ⇒ 已修并复测；
2. 首次只在 `_resolve_path` 守卫，**构造期 `Path(...).resolve()` 会把 `D:\…` 洗成 `<cwd>/D:\…`**，
   事后形态判据查不出来（= 客户报错串的成因）⇒ 追加**构造期守卫 + 解析后内嵌盘符兜底**两处。

### 2.3 客户侧建议（不改库）

- 不建议手改 `qfq_source_cutover`（不可变记录，activation 会校验一致性）；
- 修复版下发前的临时规避：暂停 `stock_daily` / `etf_daily` / `stock_minutes` 三个增量（共用同一 mcp-gen1 路由），其余 70 任务不受影响；
- 记录校正走**正式修复工具**（另议题，需 ②审 + 客户窗口）。

---

## 3 测试与门禁读数

| 项 | 读数 |
|---|---|
| 交付套件（含 2 个新文件） | **196 passed / 0 failed**（隔离 `QUANTSTUDIO_DATA_ROOT` + `QS_WRITE_LOCK_DIR`） |
| 管线侧（quality_audit / qfq_aux 共 8 文件） | **75 passed / 0 failed** |
| 新增用例 | `test_qfq_aux_router_guard.py` **11** 例（V0-b 双向 + 构造期 + path_for + 内嵌兜底 + 健康路径零收窄）；`test_delivery_passthrough_no_watermark.py` **5** 例（含巡检两向） |
| **V5** ConfigLint | **0 错误 / 0 警告**（exit 0） |
| **V6** SC-013 | **PASS**（36 保护路径；有变化 2） |

**SC-013 两处变化的判读（须 ②审 知悉）**

| 文件 | 判定 | 我的说明 |
|---|---|---|
| `quantstudio/pipeline/qfq_aux_router.py` | `OTHER_LINE` | **实为本会话本批改动**（D5-a/b）。审计按"本会话改动清单"判定，本批为特准实施、清单未含该文件 ⇒ 归为他线。**人工复核：确属我方本批** |
| `quantstudio/pipeline/writers.py` | `OTHER_LINE` | 与我方无关（既有他线变化，历轮同判） |

---

## 4 知识沉淀（R4）

| 条目 | 主题 | 校验 |
|---|---|---|
| **DB-2026-0019** | 审计范围泄漏 ⇒ 60 任务误判失败 | `kb_hook.validate_record` 通过 |
| **DB-2026-0020** | 成败判据建立人类日志精确串 ⇒ 变体漏计 | 通过 |
| **DB-2026-0021** | **跨平台记录路径（`qfq_source_cutover.aux_db_path`）在 POSIX 上被相对解析 ⇒ 畸形路径 + 三表零取数** | 通过（本轮新增） |

**回哺落点**：`tests/test_qfq_aux_router_guard.py`（形态判据双向）、`tests/test_delivery_passthrough_no_watermark.py`（四态/巡检同口径）；
待建契约表 `knowledge/contracts/aux-path-contract.md`（路径必须可迁移 + 谁可决定路由）。

---

## 5 待办与风险

| 项 | 状态 |
|---|---|
| ②审（明晨） | D5-a/b 首项追认；D1 退出码契约 + A6 合并口径定谳；D5-c 待批 |
| N-子2..5 | 待 ②审 后推进（D1 → manifest 判定字段 → 结构化判据 → 四态文案） |
| 推送 | **未推送**（等 ⑤用户确认；铁律 03/10/14 照常） |
| 风险 | `_resolve_path` 构造期新增抛错：若他处传入异平台模板路径会由"静默畸形"变为"显式失败"——属预期语义变化，测试已固定；健康路径零收窄已验证 |
