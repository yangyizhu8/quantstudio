---
title: "FP-5 2.2 迁移生产窗证据（2026-10-23 晚）"
tags: [客户运维, 证据, FP-5, schema 2.2, 迁移, A35, 停服窗]
日期: 2026-10-23
窗口: 今晚超级窗第①步（20:00 定档，实际 21:5x 执行；与 N4 A11-13 合窗）
执行方: 客户运维2（客户专属总运维负责 N4 演练的"启"与拉起）
---

# FP-5 2.2 迁移生产窗执行记录（A35 生产取证）

## 窗口前状态

| 项 | 值 |
|---|---|
| 主库 | `complete_2_1` / 8 列 / **24 行**（未迁移） |
| 窗前快照 | `data/backup/quantstudio.db` 50.54GB（2.1 态，**复用未重做**） |
| 代码 | `SCHEMA_VERSION=reanchor-2.2`、COMPAT=('reanchor-2.1','reanchor-2.2')；`0dca284`（COMPLETE_2_2 分支）本地已在 |
| daemon | pid 69832（17:50 dev 窗拉起）→ 窗口开始前已停 |
| 窗前代码侧自检 | 临时库实测：空库 init→2.1 → ALTER → 重入 init=`complete_2_2`（④失败点已修）；迁移硬门未确认时 rc=5 正确拒执行 |

## 序列①-③（客户运维2 执行）

**① 停 daemon**：`Stop-Process 69832` → 6s 后确认退出；**独占读写打开成功**（无残留锁）；
WAL 已 checkpoint 清理（`quantstudio.db.wal` 不存在）。

**② 迁移**（`scripts/migrate_source_watermark_last_run_id.py`）：

```
DRY-RUN  rc=0：迁移前 status=complete_2_1 列数=8 行数=24
                bootstrap 版本行=['reanchor-2.0','reanchor-2.1']
                将执行：ALTER … ADD COLUMN last_run_id VARCHAR
                        UPDATE qfq_bootstrap_run SET schema_version='reanchor-2.2'
                           WHERE schema_version='reanchor-2.1'
                复检要求 → COMPLETE_2_2；行数 24 不变
EXECUTE  rc=0：迁移后 status=complete_2_2
                列=[…,cutover_id,last_run_id]（9 列）行数=24
                bootstrap 版本行 ['reanchor-2.0','reanchor-2.1'] → ['reanchor-2.0','reanchor-2.2']
```

**③ 独立复检**（不信脚本自述，单独连库复核）：

| 复检项 | 结果 |
|---|---|
| `detect_schema_status` | **complete_2_2** ✓ |
| 列集 / 列数 | 9 列，末列 `last_run_id` ✓ |
| 行数 | **24（不变）** ✓ |
| `verify_fingerprint(TARGET_MAIN_DB_2_2, reject_extra=True)` | **True** ✓ |
| bootstrap 版本行 | `reanchor-2.0`×6 + `reanchor-2.2`×2（历史行未动）✓ |
| `last_run_id` 非空行数 | 0（历史不可知 → 留 NULL，**不回填**，符合约定）✓ |
| **`init_duckdb_schema`（2.2 库）** | **complete_2_2；表数 101→101（零 DDL，只读 no-op）** ✓ ← ④失败点已消除 |
| `writers._assert_qfq_schema_init_safe` | **PASS** ✓ |

## 阶段④-⑤（客户专属总运维执行）

- ④ N4 A11-13 真实启停演练（**停/启本身即演练对象**；"启"= 在 **2.2 库**上拉起 ⇒ 同步构成 A35 的 daemon 可启动实证）；
- ⑤ 拉起 daemon + 双验证（2.2 库可启动 / 采集与水位链路正常）。

## 观察期衔接

- daemon 重启后将加载本地工作树 ⇒ **`becb5c3`（E1′ tick 归一 + E5 整段偏移检测）一并生效**；
- B 观察期首个新判据读数：**10-24 10:15 守护**（`schedule-f55fe56e`），基线对比
  `10-23 10:05:47 factor_equivalent=0 blocked=36 units=4632 放行率=0.0%`（旧判据）。

## 附加双实证（窗内顺带取得，2026-10-23 22:0x 采信入档）

本窗为「一次停启完成两案」，顺带产出两条**与启动/自愈链**相关的跨案实证
（结论归属见 `docs/daemon-gui-start-failure-design-20261023.md` 的 R-1~R-2 与 FP-1~FP-5 体系，
本节只记现场事实与指针，不复制条文）：

| 实证 | 现场事实 | 价值 |
|---|---|---|
| **正常启动 bootstrap 亦 0B** | 两次正常启动（pid 46576 @21:59:27、pid 69036 @21:59:35）日志均为「GUI 启动场景：已移除 StreamHandler，后续日志仅写 daemon.log（**bootstrap 文件停止增长**）」⇒ 正常启动路径下 bootstrap 文件**零增长（0B）** | **FP-1 价值扩展**：启动期可诊断性**不再依赖** bootstrap 文件，改由 daemon.log + 双源判据承载 ⇒ 启动失败取证链对「正常启动」同样成立 |
| **daemon 自清陈旧 status** | `21:59:27 清理陈旧 status 文件（原进程已退出）`（新进程启动时清旧值）+ `21:59:32 已清理本进程 status 文件`（优雅退出路径同清，重复两次） | **R-2 自愈链**：status 文件的清理由 daemon 生命周期**自持**，不需人工介入（避免陈旧 pid 误导运维） |

- **R-2 维持默认开**，待现场值确认后再议（总调度 2026-10-23 照准）。

## 关联

- 方案：`docs/qfq-schema-2.2-last-run-id-design.md`（§8 修复记录、§8.5 本窗记录）
- B 判据修正：`docs/qfq-e1-tolerance-fix-design.md`、`docs/qfq-reanchor-factor-equivalence-design.md`
- 证据脚本：`scripts/calib_e5_segment_thresholds.py`、`scripts/extract_minute_rho_replay.py`
