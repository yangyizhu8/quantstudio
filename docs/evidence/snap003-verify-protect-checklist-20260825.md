# SNAP_003 分片 verify → protect 执行清单（周二 2026-08-25 10:00-12:00 版）

> 预写于周日 08-23（总调度 05:00 改排期指令）。SNAP_003 id 以周一夜 create 实际产出为准（下文占位 `<SNAP003>`）。
> 前提：周一 23:30 create 完成（周二 ~09:30），白名单修复已验收或备用授权已启用登记。

## T-0（10:00）启动前门禁（5 min）

| # | 命令/检查 | 判据 | 产物 |
|---|---|---|---|
| P1 | `python scripts/governance_snapshot.py list` | `<SNAP003>` 在列、protected=false | stdout 存 `output/golden_baseline/snap003_verify_20260825.list` |
| P2 | 读 `data/snapshots/<SNAP003>/manifest.json` | `verify_status` 缺省/pending、三重 hash（pre==post==copy）字段一致 | 摘要记入本清单执行记录 |
| P3 | 内存预检（可用 ≥10GB，guard 内建门槛同源） | PASS 才进 T-1 | 记录数值 |
| P4 | `data/snapshots/failed.log` 无新失败、无孤儿 tmp | 干净 | — |

## T-1（10:05-~11:30）分片 verify SNAP_003（严禁全量路径）

```
python scripts/governance_snapshot.py verify <SNAP003> \
  > output/golden_baseline/snap003_verify_20260825.stdout 2>&1
```

- 判据：退出码 0，stdout `verify <SNAP003>: PASS`；manifest `verify_status=PASS` + `verify_recomputed_sha256 == logical_total_sha256`
- **耗时风险显式登记**：create 单遍 hash 历史约 3h 级；分片实现目标为内存有界（RSS≤4GB, T7），耗时未必显著缩短。**若 12:00 未完成不强行中断**（verify 幂等可重跑、不写库），延续至 PASS/FAIL，但需监控 RSS：超 4GB 或系统内存吃紧 → 停止、落证据、报总调度（SNAP_002 三轮 OOM 红线）
- guard 中途拦截（exit 6）→ 正确行为，读 `guard_refused.log` 拒因，报总调度，**不擅自重试超过一次**
- verify 后：复制 manifest 关键字段（逐表 hash + total + verify 字段）为 `docs/evidence/snap003-verify-20260825.json`（=交付②verify JSON）

## T-2（PASS 后 ~5 min）bind --protect

```
mkdir -p output/golden_baseline/snap003_bind
python scripts/governance_snapshot.py bind <SNAP003> output/golden_baseline/snap003_bind --protect
```

- 判据：退出码 0；`protect.log` 新增 `bind --protect user_approved` 行；index.json + manifest `protected=true`；`snapshot_meta.json` 生成（=交付③protect 绑定记录）
- FAIL 分支：禁止 bind（CLI 自身拒绝 verify_status!=PASS）；FAIL 即时报总调度，禁止带病快照进入 bind

## T-3（~11:40）SNAP_002 独立分片 verify 回填（关闭批2 gate exception 遗留承诺）

```
python scripts/governance_snapshot.py verify SNAP_20260818_002_1f745d17 \
  > output/golden_baseline/snap002_backfill_verify_20260825.stdout 2>&1
```

- 判据：PASS（与 v1.17 入口证据：create 三 hash 一致互证）；SNAP_002 已 protected，verify 不改变保护位
- 产物：stdout + manifest verify 字段快照 → `docs/evidence/snap002-backfill-verify-20260825.json`

## T-4（~11:50）gate exception 正式关闭记录（=交付④）

1. `私募工作文件/QuantStudio-MCP全数据源替代任务文件/issue_registry.md` 变更记录追加 v1.x：
   「批2 gate exception 关闭：SNAP_002 独立分片 verify PASS（2026-08-25，证据 snap002-backfill-verify-20260825.json）；SNAP_003 create+verify PASS+protected（证据 snap003-verify/protect）；例外按约不沿用，恢复每批一快照纪律评估」
2. 本清单执行记录节填实各步时间戳/退出码/manifest 摘要
3. 更新 `docs/evidence/final-snapshot-20260822-briefing.md` 收尾状态

## 交付物对照（总调度核验清单①-⑤）

| 交付 | 实物 |
|---|---|
| ① manifest+三重 hash | `data/snapshots/<SNAP003>/manifest.json` |
| ② 分片 verify JSON | `docs/evidence/snap003-verify-20260825.json` |
| ③ protect 绑定记录 | `protect.log` + `snap003_bind/snapshot_meta.json` + index.json |
| ④ SNAP_002 回填+例外关闭 | `docs/evidence/snap002-backfill-verify-20260825.json` + issue_registry v1.x |
| ⑤ 简报 | `docs/evidence/final-snapshot-20260822-briefing.md`（滚动更新） |

## 红线（执行期不变）

- 严禁全量 verify（机器红线）；guard 真实拦截不绕过；无用户确认不 commit/push；不动生产库数据
- 周二为交易日：verify 跨入盘中时密切监控内存竞争（pagefile 64GB / commit ~98GB 已知会）；异常即停落证据报总调度
