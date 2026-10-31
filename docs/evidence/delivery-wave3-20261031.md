# W3 收口读数（003-delivery-bundle-import · 导入变更区）

- 抓拍时间：2026-10-31 21:05（Asia/Shanghai）
- **红线遵守**：W3 全部测试在 `tmp_path` fixture 内，**未触碰生产 `data/`**（唯一例外见 §7 事故）
- 门禁前置：`--wave 0/1/2` 均 PASS

## 1 测试读数

```
python -m pytest tests/test_delivery_pure_gain.py tests/test_delivery_verify.py \
  tests/test_delivery_stats.py tests/test_delivery_precheck.py tests/test_delivery_fixtures.py \
  tests/test_delivery_export.py tests/test_delivery_import.py tests/test_delivery_rollback.py \
  tests/test_delivery_journal.py -q
```
**测试: 130 passed / 0 failed / 43.86s**（W2 的 110 + W3 新增 20：导入 5 / 回滚 2 / journal 13）

## 2 SC-004 更新模式成功路径

- `ok=True`、`mode=update`、`exit_code=0`、`journal_finalized=True`、**journal 已删除**
- 备份目录存在且含 `RESTORE_README.txt`；`reverify.can_incremental=True`
- 三件替换后 `quantstudio.db` 的 SHA256 与包内一致

## 3 SC-007 安装模式（三保留件）

- `mode=install`、`backup={'dir': None,'bytes': 0,'method': 'none'}`（明示"无文件可备"）
- `snapshots/`、`.gitkeep`、`README.md` **全部保留**（FR-017）
- 三件全部由包创建；无 journal 残留

## 4 SC-011 备份方式证据链

- `backup.method=rename`（同卷）
- **inode 一致**（`st_ino` 前后相同）+ `size` 一致 + **耗时 < 5 s**（fixture 级 O(1) 证据）

## 5 SC-015 坏库修复模式

- 起点：`data/quantstudio.db` 0 字节
- `mode=repair`、`ok=True`；`forensic` 四字段齐（bytes / sha256 / open_error / schema_status）
- **原件保留**：备份目录内 `quantstudio.db` 仍为 0 字节（探测失败 ≠ 数据无价值）
- 导入后主库可读且非空

## 6 SC-006 / SC-017 回滚

| 场景 | 注入点 | 结果 |
|---|---|---|
| SC-006 覆盖期失败 | 第 2 个 `os.replace` 抛错 | `ok=False`、`rollback_ok=True`、`exit_code=9`；主库 schema 与行数**回到导入前**；无 `.incoming` 残留；journal 清理 |
| SC-017 磁盘耗尽 | 复制第 2 个文件时 `OSError(ENOSPC)` | `ok=False`、`rollback_ok=True`；主库**大小与 schema 恢复**；journal 清理 |

> 修复过程中发现并修掉一个真缺陷：**回滚原先只看 `state=done`**，而备份是同卷 rename（原件已不在原位）⇒ 若失败发生在 staging 阶段（尚未替换）会**丢失主库**。现改为"凡有备份且备份仍在即还原"，SC-017 正是这条缺陷的复现用例。

## 7 SC-014 崩溃恢复（真强杀 + 二选一）

方案：子进程 + `QS_DELIVERY_TEST_HOOKS=1` + `QS_DELIVERY_TEST_KILL_AFTER_REPLACES=1` ⇒ 替换第 1 项后 `os._exit(9)`（不跑 atexit，锁与 journal 半写保留）。

| 断言 | 读数 |
|---|---|
| 真崩溃 | `returncode=9`（非正常退出） |
| journal 留存 | `phase=overwrite_started`，`files_done≥1`，`backup_import_*` 恰 **1** 个 |
| 恢复「回滚」 | `ok=True`；主库 schema 回到导入前；journal 删除；**备份目录仍为 1（无二次备份）** |
| 恢复「继续」 | `ok=True`；journal 删除；**备份目录仍为 1**；三件 SHA256 与包内逐一致 |
| 残留 journal 时再导入 | `exit_code=7`（`recovery_required`），**不重推模式**（T6 防护） |

**SC-014: 崩溃 → 二选一恢复均达成；"半新半旧被当原始态再备份"未被复现（备份目录数恒为 1）**

## 8 SC-013 基线相对审计

```
python scripts/delivery_pure_gain_audit.py --mode audit \
  --baseline docs/evidence/delivery-pure-gain-baseline-v2-20261031.json
```
**SC-013: 与基线 v2 一致（PASS，退出码 0）——36 保护路径 / 0 变化 / 新增未跟踪 无**

## 9 两小件（W3 首批）

1. **bundle_id 剔除 `duration_sec`**：`bundle_id_of()` 现剔除 `generated_at` / `lock_acquired_at` / `lock_released_at` / **`duration_sec`**；单测改为**字段全量审查**（逐个变更字段断言 bundle_id 是否应随之变化），防"剔除已知时间字段"再次漏项。
2. **UNKNOWN-BASIS 报告脚注**：复验/校验报告新增 warning ——「以下表缺基准列声明（questdb_table_map 的 watermark_basis 与 alignment_rules 的 time_key 均无），无法判定四态、已标 UNKNOWN-BASIS；请联系实施方补齐配置：`[…]`」（诊断可见、不引入猜测）。

## 10 更正上轮停点包的一处事实（如实申报）

上轮记录「3 行幽灵水位（指向库内不存在的表）」——**核实后不成立**：

- `source_watermark` **24 行 / 21 张表**，`ghost_rows=[]`（无指向不存在表的行）；
- 多出的 3 行是**同表多 source 行**：`etf_minutes`、`stock_minutes`、`stock_namechange` 各有 2 条（`mcp` 与 `questdb`/`akshare`）。
- 影响：按表名回退匹配时存在**选错 source** 的风险（两行 `last_date` 不同），已修：优先同 `freq` → source 优先级 `mcp > akshare > questdb` → 日期较新者。
- 转 watermark-preset 另案的正确材料应为：**「水位存在 source 维度多义（同一表最多 2 条），取水位必须指明 source 口径」**，不是幽灵行。

## 11 事故与框架缺陷（须申报）

- **事故**：SC-014 首跑时，崩溃子进程的写锁落到了**真实** `data/snapshots/.write_lock`（`snapshot_lock._lock_dir()` 刻意不跟随 `QUANTSTUDIO_DATA_ROOT`，只认 `QS_WRITE_LOCK_DIR`）。已归档锁文件至 `docs/evidence/delivery-w3-stale-lock-20261031_205842.json` 并移除；锁目录现为 `clean`。**测试已改为强制设置 `QS_WRITE_LOCK_DIR`（覆盖而非 setdefault）**，重跑后锁目录保持干净。
- **框架缺陷（登记，不擅动）**：`snapshot_lock.diagnose_holder()` 对"PID 已死但心跳较新"返回 `verdict=live_fresh / reclaimable=false`（实测：holder pid 37960 已不存在，心跳 37 s ⇒ 预检脚本判 BLOCKED）。后果：**残留锁无法自愈，生产写锁可能被永久挡住**。建议由 `snapshot_lock` 归属线按铁律 08 立项：`diagnose` 应先判 PID 存活，存活才看心跳。

## 12 委派申报

- **T037 journal 相位机 / T038 九步 / T039 备份 / T040 覆盖回滚 / T041 复验：全部自写**（状态机与事务语义，按裁定自写并申报）。
- 机械件（journal 序列化 IO）**未委派**：与相位机同文件、判据共用，拆出去会产生两处真源；如实申报为零委派。

## 13 波次门禁字段

测试: 130 passed / 0 failed（43.86s；W3 新增 20 项）
SC-004: update 成功路径 ok/journal 清理/备份 README/可增量 true
SC-006: 覆盖期失败 → rollback_ok=True、schema 与行数回到导入前、无 .incoming、exit 9
SC-007: install 模式三保留件在、backup method=none、三件由包创建
SC-011: method=rename、st_ino 一致、size 一致、耗时 < 5 s
SC-014: 真崩溃 rc=9 → 回滚/继续均成功；备份目录恒 1（无二次备份）；残留 journal 时再导入 exit 7
SC-015: repair 模式 forensic 四字段齐、0 字节原件保留、导入后主库非空
SC-016: 两次导入 reverify.tables 逐字段一致
SC-017: ENOSPC → rollback_ok=True、主库大小与 schema 恢复
SC-013: 与基线 v2 一致（PASS，36 保护路径 0 变化）
