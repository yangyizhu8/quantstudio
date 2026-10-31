# Hotfix · 锁生命周期三缺陷（2026-11-01）

## 1 事故（用户实测）

00:20:51 导出成功、manifest 完整、进程正常退出，但 `data/snapshots/.write_lock` **残留 1.7 小时**：
`lock_task_id=delivery_export`、`pid=8700`（= 导出进程，已退出）、`heartbeat == acquired_at`（**零心跳**）。
用户侧已用协议自愈原语（`manual_heal_stale`）解锁。

## 2 根因（①，最小复现钉死）

`snapshot_lock` 的两条释放路径语义不同：

| 取锁方式 | 配对释放 |
|---|---|
| `ensure_write_lock()`（压 `_ensure_handles` 栈） | `release_write_lock()`（弹栈） |
| **`acquire_write_lock()`（返回 `WriteLock` 句柄）** | **`handle.release()`** |

export/import 用的是 `acquire_write_lock()`，却把**返回句柄丢掉**、改用 `release_write_lock()` ⇒ 弹的是空栈，**release 变成空操作**。

最小复现（干净进程，`QS_WRITE_LOCK_DIR` 指向临时目录）：

```
OLD_STYLE_AFTER = HELD      # acquire_write_lock() + release_write_lock()
NEW_STYLE_AFTER = CLEAN     # acquire_write_lock() + handle.release()
```

**测试盲区**：W1 只断言了 abort/fail 三处的 `rmtree`，**从未断言成功路径的锁释放**。

## 3 三项修复

| # | 修复 | 落点 |
|---|---|---|
| ① | 句柄式释放：`_acquire_lock()` 把 `WriteLock` 存进模块级 `_LOCK_HANDLE`，`_release_lock()` 调 `handle.release()`（export / import 两侧同改） | `export_kernel.py` / `import_kernel.py` |
| ② | **PRECHECK 陈锁自愈**：遇锁先 `diagnose_holder()`，`stale/reclaimable` ⇒ `try_reclaim_stale()` 回收一次；成功则写 `reclaimed` 进 warnings；仍被持有才拒 | `probe.heals_stale_lock()`（新增）+ 两侧 PRECHECK |
| ③ | **相位即心跳**：导出 `phase()` 内先 `_heartbeat()`（`handle.heartbeat()`），覆盖 CHECKPOINT/COPY/ATTEST 等长相位，防 >600s 导出被判陈锁回收致双写 | `export_kernel.py` |

## 4 新增测试（`tests/test_delivery_lock_lifecycle.py`，3 例）

| 用例 | 判据 |
|---|---|
| `test_export_success_releases_lock` | 真实取锁跑完六相位后：`read_holder() is None` 且锁文件不存在（**补上成功路径的盲区**） |
| `test_precheck_self_heals_stale_dead_lock` | 造一把持有者已死（pid 999999、心跳 2h 前）的陈锁 → 导出**继续成功**，不再被堵死 |
| `test_export_refreshes_heartbeat_between_phases` | 在 CHECKPOINT / COPY 相位读到锁文件，heartbeat 非零且单调推进（证明确有刷新） |

**测试读数：154 passed / 0 failed / 66.65 s**

## 5 纯增益

SC-013：**PASS**（36 保护路径；唯一变化 `writers.py` 判 `OTHER_LINE`，非本会话）
根因入册：`knowledge/db/DB-2026-0013.md`（族 `db` / `write_path`，`kb_hook` 机检通过）
