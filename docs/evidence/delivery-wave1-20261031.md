# W1 收口读数（003-delivery-bundle-import · 内核导出）

- 抓拍时间：2026-10-31 04:15（Asia/Shanghai）
- git HEAD：`7a0c6a30bee19fcb346d53987ba329dc9ce3db3d`
- daemon 状态：**running**（PID 29152，`--mode forever`）——**本轮未停/未启**；W1 全程 L2 fixture
- 波次门禁前置：`delivery_wave_gate.py --wave 0` 已 PASS（W0 收口时）

## 1 测试读数

```
python -m pytest tests/test_delivery_pure_gain.py tests/test_delivery_verify.py \
  tests/test_delivery_stats.py tests/test_delivery_precheck.py \
  tests/test_delivery_fixtures.py tests/test_delivery_export.py -q
```
**测试: 100 passed / 0 failed / 11.58s**（W0 的 83 + W1 新增 17 项导出用例）

派生子集：`tests/test_delivery_export.py` 单独 **17 passed / 9.95s**。

> **TDD 实效**：首跑时 8 项红（`_copy_file` 目标目录未建 → 7 项连带失败；SC-002 边车泄露 → 1 项），
> 修完后二次跑 **17 passed**。修复过程中被抓出两个真缺陷（见 §4）。

## 2 SC-001 拦截有效性（五场景各一次 ⇒ 零产出）

| # | 场景（注入方式） | 拒绝码 | 产出 |
|---|---|---|---|
| 1 | `partial_or_mixed` 主库（fixture） | `SCHEMA_STATUS_INVALID` | 0 |
| 2 | `probe.write_lock_holder` 返回持有者 | `WRITE_LOCK_HELD` | 0 |
| 3 | `probe.daemon_running` 返回 running（pid 29152） | `DAEMON_RUNNING` | 0 |
| 4 | `probe.disk_free_bytes` 返回 10 字节 | `DISK_INSUFFICIENT`（required=…, free=10） | 0 |
| 5 | checkpoint 置 no-op + 主库 `-wal` 写 32 字节 | `WAL_NOT_ZERO`（readings.wal[0].bytes=32） | 0 |

**SC-001: 五场景全部拒绝且导出目录零产出（`_assert_no_output` 逐项断言通过）；额外覆盖 WHITELIST_FILE_MISSING、PATH_OVERLAP、TARGET_NOT_WRITABLE/TARGET_HANDLE_BUSY 的拒绝路径**

## 3 SC-002 / SC-003 / SC-012 实测读数（fixture）

fixture 路径：`%TEMP%\qs_w1_measure_4y785lzh\`（data + config + export 全在临时目录）

| 项 | 读数 |
|---|---|
| 产出目录条目 | `['manifest.json', 'qfq_aux.db', 'qfq_aux_mcp_gen1.db', 'quantstudio.db']`（**恰 4 项**） |
| `env_hits` / `shm_hits` / `log_hits` / `outside_whitelist` | `0 / 0 / 0 / 0`，`zero_leak=true` |
| 单文件 | `quantstudio.db` 2,633,728 B `8406d98c16818fa1…`；`qfq_aux.db` 12,288 B `03237f291cb6cc53…`（头 `SQLite format 3`）；`qfq_aux_mcp_gen1.db` 12,288 B `4b854f50ff178ca2…`（头 `SQLite format 3`） |
| 三件连续只读打开 | `source_watermark` 行数 1；`qfq_aux.db` 行数 3；`qfq_aux_mcp_gen1.db` 行数 1 ⇒ **SC-003: 三文件一次连续只读会话全部打开且关键查询成功（未用 mtime 作证）** |
| 体积 | `total_bytes = 2,658,304` == 三件 `stat().st_size` 之和 == manifest `files[].bytes` 之和 ⇒ **SC-012: 体积为实测值，manifest 与磁盘逐项一致** |
| 逐表状态 | `stock_daily / trade_date / row_count=2 / max_date=2026-10-30 / watermark=2026-10-30 / OK` |
| 表集 | `count=1`（map 登记 2 张，`stock_minute` 库内不存在 ⇒ 被裁剪，符合 Q2「①∩实际存在」） |
| 降级 | `row_count_degraded=false`、`[]` |
| WAL | 三件 `present=false / bytes=0` |
| `copy_sha256_pass` / `bundle_id` | `true` / `ee7a1e66be0f4002…` |
| 耗时 | 0.485 s（fixture 2.5 MB；生产 68.9 GB 的读数留 W2 的 L3 窗口） |

## 4 本轮抓到并修掉的两个真缺陷（证据链）

1. **`_new_bundle_dir` 只算路径不建目录** ⇒ 首跑 7 项连带 `ExportFailed(COPY, FileNotFoundError)`。修复：建目录后再返回；`_assert_no_output` 与"中断即作废整包"用例确认清理语义仍成立。
2. **对交付包内的 SQLite 件做自检会在包内生成 `-shm/-wal` 边车**（普通 `mode=ro` 打开 WAL 库的副作用）⇒ 首跑 SC-002 直接抓到产出目录 7 项（多出 `qfq_aux.db-shm` 等）。修复：统一改 `file:…?mode=ro&immutable=1`（`verify.sqlite_ro_uri`），并在 docstring 写明原因。
   **这条是红线自检自身抓住的**——若自检写成"只看白名单三件是否都在"而非"枚举目录全集"，这个泄露会一路带到客户机。

## 5 SC-013 基线相对审计

```
python scripts/delivery_pure_gain_audit.py --mode audit \
  --baseline docs/evidence/delivery-pure-gain-baseline-20261031.json
```
**SC-013: 与基线一致（PASS，退出码 0）——保护路径 35 个，有变化 0，新增未跟踪 无**

APPEND_ONLY 对照：`main_window.py` / `workers.py` / `pyproject.toml` 仍与基线一致；`.gitignore` 仍为 W0 那 430 B 变化（本会话预期，W1 未再改动）。

## 6 生产库未被触碰

`data/*.db` 六个文件（大小 + mtime）与 W0 开工快照对比：**变化数 0 / 6**（`data_db_changed_count=0`）。

## 7 委派申报（D-14 续）

- **T017（export_kernel 六相位）：未委派 `zcode_code`，本会话自写** — 原因：①六相位与三条不变式（锁窗口顺序 / 中断即作废 / 统计取副本）高度耦合，任务书要求"自写并显式声明"；②zcode 近期有 3 次失败记录；③实现需边跑边对照本会话已定的契约原文。
- **T019（ATTEST 组装）与 T018（红线自检）**：与 T017 同文件/同判据，一并自写（拆开委派会产生两套判据，违反单一权威）。
- 下一波（W2）的**机械任务**（如退出码映射表、reasons 聚合）可视情况委派；**测试委派须附 SC 判据原文**。

## 8 波次门禁字段（供 `delivery_wave_gate.py --wave 1`）

测试: 100 passed / 0 failed（交付特性全套 11.58s；导出子集 17 passed）
SC-001: 五场景（schema/锁/daemon/空间/WAL）全部拒绝且零产出
SC-002: 产出恰 4 项，env/shm/log/白名单外=0，zero_leak=true
SC-003: 三件一次连续只读打开 + source_watermark/因子库查询全部成功
SC-012: total_bytes=2658304 与磁盘和 manifest 三方一致
SC-013: 与基线一致（PASS，35 保护路径 0 变化）
