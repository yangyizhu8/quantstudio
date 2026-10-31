# L3 窗口第 3 次执行 · 回执（SC-009 达成 + 全量基线达成）

**窗口**：2026-10-31 17:52 开窗 → 19:16 收窗（占用 84 分钟；批准 240 分钟，未用尽）
**占用声明**：`delivery-l3-window-declaration-v3-20261031.md`（含事故三核 / 独占探测硬门 / 双会话发布）

## 1 开窗门禁（全部通过，顺序未交换）

| 步 | 读数 |
|---|---|
| 三事实重核 | daemon pid=**40920**（started 16:53:48，create_time delta **0 s**）；六文件指纹已记录 |
| 停 daemon | 双形态核验通过，**1 s** 退出 |
| flag | 已写（reason=delivery_window_v3） |
| 三读数 | 目标进程不存在=True / daemon 进程=0 / flag=True |
| **陈锁体检** | BEFORE=`null` ⇒ **CLEAN / exit 0** |
| **主库独占探测** | `CreateFileW(GENERIC_READ, share=0)` → **handle 有效、err=0**（无人独占） |
| 复检 | `read_holder=None` |

## 2 SC-009：CLI 两次串行导出 + 逐文件比对

| 项 | run1 | run2 |
|---|---|---|
| 时刻/耗时 | 17:52:36 / **237.7 s** | 17:56:47 / **237.4 s** |
| 总量 | 74,409,730,048 B | 74,409,730,048 B |
| qualified | true | true |
| `quantstudio.db` | 62,766,198,784 / `18bf43e5…` | 62,766,198,784 / **`18bf43e5…`** ✅ |
| `qfq_aux.db` | 11,643,478,016 / `bf497691…` | 11,643,478,016 / **`bf497691…`** ✅ |
| `qfq_aux_mcp_gen1.db` | 53,248 / `8f546c10…` | 53,248 / **`8f546c10…`** ✅ |
| 清单条目 | 3 | 3 |

**SC-009 结论：一致率 100%（3/3 逐文件 SHA256 与字节数完全相同；`generated_at`/`export.*` 时间字段除外）**

**SC-003 生产侧（两份各自 ATTEST）**：三件 `db_open=ok`；三件 `-wal present=false bytes=0`；`zero_leak=true`（`outside=0/env=0/shm=0/log=0`）。

> **发现（W3 待修）**：两次的 `bundle_id` **不同**（`e82f2140…` vs `d90a8803…`）。原因是 `bundle_id_of()` 只剔除 `generated_at`/`lock_*_at`，**未剔除 `export.duration_sec`**（两次 237.732 vs 237.078）。建议把 `duration_sec` 也纳入剔除集（否则"同内容两次导出应同 bundle_id"不成立）。**不影响 SC-009**（SC-009 比对的是文件清单与哈希）。

## 3 FR-006 v5 / FR-018 v5：生产库实测逐表状态

`table_set.count=71`、`out_of_map=24`、`unknown_basis=5`、`row_count_degraded=false`

| 状态 | 数量 | 说明 |
|---|---|---|
| `NO-WATERMARK-SYSTEM` | **50** | map 覆盖的 passthrough 表（`dataset_kind=passthrough`），只报 `row_count` |
| `OK` | 10 | 水位与数据一致 |
| `BEHIND` | 6 | 水位落后数据（下次采集会重拉） |
| `UNKNOWN-BASIS` | 5 | 水位表但 map 与 alignment 均无 `time_key`（`ai_research_snapshot`/`block_trade`/`broker_recommend`/`xueqiu_sentiment`/`etf_basic`） |
| **`MISSING`** | **0** | 假 MISSING 已消除 ✅ |

**与审核方预期（24 张进四态）的差异（如实申报）**：库里水位表共 **21 张**进表集（`source_watermark` 有 24 行，其中 3 行的表在库内不存在）；21 = **16 进四态** + **5 UNKNOWN-BASIS**（这 5 张在 map 与 `alignment_rules.time_key` 两处均无基准列，按 FR-006「两处均无 ⇒ 不猜」标 UNKNOWN-BASIS）。**未凑数**。

## 4 全量测试基线（③）

| 项 | 读数 |
|---|---|
| 命令 | `python -m pytest tests -q --tb=no -p no:cacheprovider --junitxml=…` |
| 结果 | **4305 tests / 70 failed / 18 skipped / 0 errors / 1972.86 s（32.9 min）** |
| delivery 相关失败 | **0**（清单中无任何 `delivery` 用例） |
| 失败清单 | `docs/evidence/delivery-l3-junit-failures-20261031.txt`（70 行，逐条） |
| 预存失败（已知） | `test_writer_channel_contract.py::test_b1_chunked_variant_is_same_channel_not_third`（断言 `RENAME TO` 滞后于他线 PT-01a/F2 改道，`writers.py:1986` 注释为证）——定性不变：**他线债，非本特性回归** |

## 5 副作用申报（须记录）

`data/qfq_aux.db` 的 **mtime 在窗口内变为 18:58:58**（大小 11,643,478,016 未变）。时段对应全量 pytest 运行 ⇒ **全量测试在生产 `DATA_ROOT` 上产生了写入副作用**，违反既有铁律「跑含 GUI/daemon 的 pytest 前必须先隔离数据根」（本会话已入册的记忆 a37d3002）。
- 影响面：仅 mtime（大小未变），无 schema/行数证据变化；
- 处置建议：**今后全量基线一律在隔离 `DATA_ROOT` 下跑**，并把"隔离 runner"写进窗口流程；本次"真实 DATA_ROOT 全量"读数仅作**一次性的失败面基线**，不再复现。

## 6 SC-013 窗口前后（对照基线 v2）

| 时点 | 结论 |
|---|---|
| 开窗前（18:01） | **PASS**（36 保护路径 / 0 变化） |
| 收窗后（19:16） | **PASS**（36 保护路径 / 0 变化 / 新增未跟踪 无） |

## 7 收窗

| 项 | 状态 |
|---|---|
| flag 删除 | `flag_exists=False` |
| daemon 恢复 | `schtasks /Run` → **pid 37640，started_at 2026-10-31T19:16:29，status=running** |
| `D:\delivery_l3_tmp\` | **已删除**（`exists=False`） |
| 生产库六文件 | 大小全部未变；`qfq_aux.db` mtime 变化见 §5 |

## 8 证据索引

| 文件 | 内容 |
|---|---|
| `delivery-l3-run1-20261031.json` / `delivery-l3-run1-manifest-20261031.json` | 导出 #1 的 manifest 与逐文件 sha256 |
| `delivery-l3-sc009-compare-20261031.json` | 两次导出的逐文件比对结论与 run2 自检 |
| `delivery-l3-junit-20261031.xml` / `delivery-l3-junit-failures-20261031.txt` | 全量基线原始 XML 与 70 条失败清单 |
| `delivery-pure-gain-baseline-v2-20261031.json` | SC-013 基线 v2（36 保护路径） |
