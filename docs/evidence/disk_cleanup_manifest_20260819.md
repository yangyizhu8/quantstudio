# D 盘整理清理清单（2026-08-19）

用户要求：`A+B 全部清理`。以下为本次实际删除项与跳过项，供追溯。

## 预检结论
- 扫描时活跃进程：`governance_snapshot.py create`(PID 42580)、`get_tushare_data.py --incremental`(PID 38224)、`run_ptrade_strategy`(PID 29796)、`monitor.py`(PID 45024)、questdb.exe(50172)。
- 对全部待删项做锁检测（File.Open 文件级 + 目录重命名法）。

## A 档（临时/缓存，明确安全）— 已删除
| 路径 | 大小 |
|---|---|
| QuantStudio\output\pytest_tmp_fullclean_1785259793 | ~0.88GB |
| QuantStudio\output\pytest_tmp_full_011631 | ~0.88GB |
| QuantStudio\output\pytest_tmp_qfq83 | ~0.42GB |
| QuantStudio\output\pytest_tmp_dqfqclean_1785259751 | ~0.06GB |
| QuantStudio\output\pytest_tmp_dqfq_1785259639 | ~0.03GB |
| QuantStudio\data\staging_batch2_20260730 | ~2.19GB |
| qa2\results\pickle_cache | ~37.76GB |

### A 档跳过（进行中任务占用，不可删）
- QuantStudio\data\quantstudio.db.tmp（~3.98GB，正被 DB 重建/取数进程占用，文件级锁确认）
- QuantStudio\data\snapshots\SNAP_20260818_002_pending.tmp（~24.26GB，governance_snapshot 任务进行中）

## B 档（历史 DB 备份，确认后清理）— 已删除
| 路径 | 大小 |
|---|---|
| QuantStudio\data\quantstudio.db.bak_c4merge | 13,942MB |
| QuantStudio\data\quantstudio.db.bak_c4merge_run1 | 13,942MB |
| QuantStudio\data\quantstudio_backup_20260729_100725.db | 13,157MB |
| QuantStudio\data\quantstudio_backup_pre_pipeline_20260816.db | 19,417MB |
| QuantStudio\data\quantstudio_backup_stepB_20260731_171119.db | 13,866MB |
| QuantStudio\data\quantstudio.20260807T041035.db | 14,302MB |
| QuantStudio\data\quantstudio.zip | 2,912MB |
| QuantStudio\data\qfq_aux.db.bak.etf_cleanup.20260803_040147 | 2,307MB |
| QuantStudio\data\backup\（quantstudio.db.bak_frontfix_20260814_113014 单文件） | 14,318MB |
| D:\questdb\quest_database_bak_周一022607_010929 | ~63.68GB |

## B 档保留（活跃数据，未删除）
- QuantStudio\data\quantstudio.db（21,483MB，活跃主库 2026-08-18）
- QuantStudio\data\qfq_aux.db（3,677MB，活跃 2026-08-17）
- D:\questdb\quest_database（94.61GB，questdb 服务当前活跃库）
- QuantStudio\data\qfq_aux_mcp_gen1.db（1,433MB，2026-08-15 较新，不在 A/B 清单内）
- QuantStudio\data\quarantine.db（1,049MB，2026-08-16 较新，不在 A/B 清单内）
- QuantStudio\data\qfq_aux_backup_stepB（773MB，7/31 旧备份但不在原 A/B 清单，未动）

## 备注
- C/D 档未执行（需用户确认）：output\mcp_migration(48.6GB)、output\staging(38.4GB)、data\mcp_landing(18.4GB)、data\staging(14.6GB)、output\t5_roundtrip(13.6GB)、QuantStudio_W2_Staging(25.7GB)、qs_iso_a(12.9GB)、p0_snapshot(12.9GB)、stash-recovery(5.7GB,建议暂留)、trading-battle-back(72.5GB)、QMT 多余安装(~62GB)、pagefile.sys(64GB,系统级)。
