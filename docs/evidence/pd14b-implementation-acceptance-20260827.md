# P-D14b 实施与验收证据：K-001 时刻戳管线根治（双表清洗，2026-08-27）

- 流水线：Step 1 v1（D2 驳回）→ v2 修正 → Step 2 审计通过 → 范围扩展批准（8244/16619）→ **Step 3-4 实施验收完成**
- 回退点：`6666cb0b1e960226e4542ea6fe0acc5d0311fef4`（baseline-pd14b）
- 执行令：范围扩展已批 + DB 占用退出 + 带退避重试循环照案执行

## 1. 实施清单

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/daemon.py` | **写入点归零（防复发）**：`table in ("stock_daily","etf_daily")` 时 raw_df trade_date 去时刻归零（含 08:00:00 → 纯 YYYYMMDD），align 前、不动全局 to_ms_timestamp（分钟数据保护） |
| `scripts/etf_daily_time_normalize.py`（新） | 存量归一 CLI：--check（只读断言）/--apply --write（备份+UPDATE）/--revert（回滚） |
| `tests/test_pd14b_time_normalize.py`（新） | T1~T8 矩阵 |
| 设计文档 | v2 修正 + 范围扩展记录 + §9 技术债门禁 + §10 验收 |

## 2. 存量清洗执行（带退避重试）

- **第 5 次重试成功**（前 4 次 DB 锁冲突遭退避 60s——multiprocessing 子进程只读连接竞争，未杀任何进程）；
- 备份：`etf_daily_backup_pd14b`（8244）+ `stock_daily_backup_pd14b`（16619）；
- UPDATE `time = time - 28800000 WHERE time % 86400000 = 0`（UTC 日界 = CST 08:00 → CST 00:00；正常行 %=57600000 零触碰）。

## 3. 验收结果（Step 4）

| 项 | 判据 | 实测 |
|---|---|---|
| 备份完整性 | 8244/16619 | ✅ T8（备份行数精确） |
| 正向断言（apply 后） | 两表 mod=0 残留 = 0、distinct_mods = 1 | ✅ `mg=0 / mods=1（57600000 单锚）` |
| 数据保真 | 515050 07-01 close 不变 | ✅ 归一后 `1782835200000 close=1.334`（原 08:00 组 close 值保留） |
| 回滚可行 | 备份保留原值、主表可逆向 | ✅ 备份 mod=0 原值 + 主表 1786636800000（-8h）可定位 |
| D3 引擎兼容 | D3 矩阵重跑全绿 | ✅ 6/6（窗口匹配在新单锚下行为一致） |
| 测试全量 | P-D14b + 相关套件 | ✅ 8/8 + 6/6 + 124/124 |
| 写入点防复发 | daemon 归零生效 | ✅ 双表覆盖（souronce MCP 补拉路径同一处） |

## 4. 关键数字（终审可复验）

```
归一前: etf_daily mod0=8244 / stock_daily mod0=16619（值域 07-01~08-14 08:00）
归一后: 两表 distinct_mods = 1 @ 57600000（全单锚 = CST 00:00 UTC 16:00）
备份表: etf_daily_backup_pd14b=8244 / stock_daily_backup_pd14b=16619（原值保留）
```

## 5. 技术债门禁（审计令⑤）

「补拉/修复通道数据质量门禁」S1 时间戳归一 = 本 WP 写入点归零（已实施）；S2 窗口边界（sync_repairs<=end 已在案）；S3 去重断言（新登记）——后续补拉任务一律过此三查（设计文档 §9）。

## 6. 回退

- 回退点 `6666cb0`；
- 存量：备份表恢复（revert CLI 已就绪）；
- 管线：定向 restore daemon.py 归零段。