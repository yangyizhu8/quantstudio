# SNAP_003（最终快照）全链证据汇总——路径清单

> 生成：2026-08-26 03:50（收官链 T-1~T-4 全部完成）。交付推送 docs 引用本清单。
> 快照：`SNAP_20260825_003_81260e83`（create 08-25 00:13:38→10:44:26，三重 hash 全等 `81260e83befccf24…`）

## 一、create 链（含两次事故与 guard 拦截记录）

| # | 事件 | 证据路径 |
|---|---|---|
| 1 | 周六窗口 create ABORTED（04:30，同步 SYSTEM python fail-closed） | `docs/evidence/final-snapshot-20260822-briefing.md` §事故 + `data/snapshots/final_snapshot_20260822_launch_evidence.json`（outcome 字段） |
| 2 | 白名单缺口修复 v1.1（marker 机制）方案→审计→实施→验收 | `docs/governance-guard-system-proc-design.md`（v1.1）+ `tests/test_guard_qdb_domain_marker.py`（10/10） |
| 3 | 周一窗口 create REFUSED（bash 裸提及误报，23:29:58） | 简报 §第二次事故；guard 子串匹配缺陷已立项技术债（WP8 后修复） |
| 4 | 总调度 00:15 直跑 create 成功 | `data/snapshots/SNAP_20260825_003_81260e83/manifest.json`（duration_s=37887.1） |
| 5 | verify 三次启动拦截（10:50 交易时段 / 15:06 内存 8.5GB / 17:00 ETL abort）+ 22:26 门控通过 | `data/snapshots/guard_refused.log` + `output/golden_baseline/snap003_verify_20260825.stdout` |
| 6 | A-豁免（verify-only yield，总调度 2026-08-25 批准，五条件） | 代码 `scripts/governance_snapshot.py`（_VERIFY_YIELD_EXEMPT）+ `tests/test_verify_yield_exemption.py`（2/2） |

## 二、verify / protect / 回填 / gate 关闭（收官链 08-26 晨）

| # | 事件 | 证据路径 |
|---|---|---|
| 7 | SNAP_003 分片 verify PASS（22:26:45→01:40:10，recomputed==manifest） | `output/golden_baseline/snap003_verify_20260825.stdout` + `docs/evidence/snap003-verify-20260826.json`（20 表逐表 hash+rows） |
| 8 | RSS 越线决断（峰值 6.9GB 平台放行，停止线监控） | 简报 §verify RSS 越线决断记录 |
| 9 | marker 归因实战首验（repair 豁免 + ETL 正确 abort 双向验证） | 简报同节里程碑段 |
| 10 | bind --protect（01:40:34，三快照全 protected） | `data/snapshots/protect.log` + `output/golden_baseline/snap003_bind/snapshot_meta.json` + `data/snapshots/index.json` |
| 11 | SNAP_002 独立分片 verify 回填 PASS（01:42→03:38:57，`1f745d17` 吻合） | `output/golden_baseline/snap002_backfill_verify_20260826.stdout` + `docs/evidence/snap002-backfill-verify-20260826.json` |
| 12 | **批2 gate exception 正式关闭** | `私募工作文件/QuantStudio-MCP全数据源替代任务文件/issue_registry.md` **v1.46** |
| 13 | 验收证据（全链字段化） | `docs/evidence/snap003-acceptance-20260825-template.md`（已填实） |
| 14 | 备用双禁用启用与恢复登记 | `docs/evidence/backup-disable-authorization-20260824.md` |
| 15 | 总简报（时间线全集） | `docs/evidence/final-snapshot-20260822-briefing.md` |

## 三、执行清单

| # | 文件 |
|---|---|
| 16 | `docs/evidence/snap003-verify-protect-checklist-20260825.md`（预写版，含 T-0~T-4 判据） |

## 四、待办（交付后）

- guard pattern 子串匹配缺陷（bash 裸提及 + pid3 幻影）技术债：WP8 后修复（词边界+排除 bash 宿主 / marker 专责）；
- 周三独立 governance commit（六步流水线，需用户确认，含 governance_snapshot.py v1.1+A-豁免+测试+docs）。
