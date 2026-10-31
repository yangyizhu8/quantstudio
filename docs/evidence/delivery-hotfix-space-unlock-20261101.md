# Hotfix · 空间拒绝可执行化 + `qs-deliver unlock` + COPY 心跳（方案 v2 实施）

- 实施：2026-11-01 03:50（delivery 会话）
- 依据：用户裁定（余量不足**不放行**；A-3 必须经 CAS；心跳 64 MiB / 2 s）

## 1 B 段：门槛不动，拒绝变「可执行」

- `probe.space_report(need, free, data_root)`（**只读**）：
  - 返回 `need / free / shortfall / candidates`；
  - candidates 扫描三处：`backup_import_*`、**`backup/*`（旧库备份）**、`snapshots/*.bak-*`；
  - 深度受限（1 层、最多 400 文件），拒绝路径 < 100 ms。
- `import_kernel` 拒绝文案改为：

```
空间不足：还差 1.62 GiB
  包体 69.30 GiB ｜ 安全余量 6.99 GiB（1.10× 包体 + 64 MiB）
  门槛 76.29 GiB ｜ 可用 74.68 GiB
  可清理位置（只读扫描；程序不会删除任何备份，释放权在客户）：
    - data/backup/*：3 项 / 2 文件，50.54 GiB（旧库备份…）   ← 用户实测的真实出处
```

- 门槛公式**未改**：`free < bundle×1.1 + 64 MiB ⇒ 拒绝`（按裁定不放宽，无 `--require-safe-margin`）。

**真实读数**（本机 data/）：`need=76.29 GiB / free=74.67 GiB / shortfall=1.62 GiB / backup/*=50.54 GiB`。

## 2 A 段：锁诊断 + 自助回收（A-3 硬条件已落实）

- **A-1 诊断**：`unlock`（默认 dry-run）输出 `task / pid / 心跳年龄 / host / verdict / 可回收`，
  可回收时给出 `qs-deliver unlock --force` 指引，不可回收时明确"先停该任务，勿强删锁文件"。
- **A-3 回收**：`--force` **只经 `try_reclaim_stale()`**（CAS 三字段比对 + 复核 + 审计），
  **无任何裸删锁文件代码**；持有者存活 → `Exit.REFUSED` 并保留锁。审计行落 `write_lock_reclaim.log`。
- **A-2 自愈扩条**：原计划「同 host 同 pid 且未持句柄 ⇒ 回收」在实现时发现会**绕过 CAS**
  （`try_reclaim_stale` 内部复核要求 `verdict == stale_dead`，pid 存活时必然拒绝）⇒
  **改判为「只诊断不回收」**：给出 `self_residual` 标记 + 明确指引（重启进程 / 确认后用 unlock）。
  这是实施中的自纠，如实申报。
- 实施中顺带修掉：`probe.py` 缺 `import os` 导致自愈扩条被 `except Exception: pass` 静默吞掉
  （T4 用例暴露；已补 import，并把该分支从"静默 pass"改为可断言的显式结果）。

## 3 P3 心跳（COPY 内，无线程）

- 阈值提为模块常量 `_HB_BYTES=64 MiB` / `_HB_SECONDS=2.0`；
- `export_kernel._copy_file` 每块后判定（先到者）→ `_heartbeat()`；
- `import_kernel._copy` 同款（导入 staging 也会跑十几分钟）；
- 判据：`test_t9` 把 `_HB_BYTES` 调小 + `CHUNK=1024`，断言 COPY 期间 `_heartbeat()` 调用 **≥2 次**。

## 4 测试（161 passed / 0 failed / 76.49 s，新增 7 例）

| 用例 | 结果 |
|---|---|
| T1 `unlock` dry-run 不删锁 | ✅ |
| T2 `unlock --force` 回收死锁 + 审计行落盘 | ✅ |
| T3 `unlock --force` 拒绝活锁（保留文件） | ✅ |
| T4 自身残留 → 只诊断（`self_residual`），不删锁 | ✅ |
| T5 空间拒绝含「还差/门槛/可用/安全余量/只读」 | ✅ |
| T6 `space_report` 列出 `backup/*` 且不删文件 | ✅ |
| T9 COPY 期间心跳 ≥2 次 | ✅ |

SC-013：**PASS**（36 保护路径；唯一变化 `writers.py` 判 `OTHER_LINE`）
根因入册：`knowledge/db/DB-2026-0014.md`（族 db / artifact_rule，机检通过）

## 5 用户侧操作提示

- **清空间**：只需补足差额；`data/backup/`（旧库备份）是最大项（本机 50.54 GiB），确认新库正常后可删。
- **看锁**：`qs-deliver unlock`（只读诊断）→ 可回收时 `qs-deliver unlock --force`。
- **导入**：`qs-deliver import --import-dir <包目录>`（GUI 同源）。
