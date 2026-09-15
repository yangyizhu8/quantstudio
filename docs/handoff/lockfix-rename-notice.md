# 【即刻生效】文件改名通知 — GUI×daemon 锁冲突修复线

> 致 dev（开工先读本件，防止按旧文件名取件卡壳）
> 发出：策略研发专属会话｜2026-09-16

## ① 新文件名（旧名已失效，git 历史保留）

| 用途 | 旧名（取不到） | **新名（有效）** |
|---|---|---|
| 证据同步件 · 第一轮 | `docs/handoff/gui-daemon-lock-dev-sync-20260917.md` | **`docs/handoff/gui-daemon-lock-dev-sync-20260916.md`** |
| 证据同步件 · 第二轮 | `docs/handoff/gui-daemon-lock-dev-sync-round2-20260917.md` | **`docs/handoff/gui-daemon-lock-dev-sync-round2-20260916.md`** |
| A2 冻结基线（验收对照） | `docs/evidence/gui-daemon-lock-baseline-20260917.md` | **`docs/evidence/gui-daemon-lock-baseline-20260916.md`** |

> 说明：仅**日期标注与文件名**更正（基线轮实际执行 9/15 深夜–9/16 凌晨），**内容与数字一字未变**，冻结口径不受影响。

## ② 排期口径（总调度 2026-09-16 修正）

- **dev EOD 进度检查点 = 9/16（今日）**；
- **策略研发 A6 全量验收 = 9/17–18**（3×2 矩阵 + N=10 双轮 + 06:00 延迟实测 + 锁生命周期单列）；
- 此前文档中"9/18 EOD / 9/19–20 验收"为**勘误前旧排期**，已作废。

## ③ 与我方对接的三个口子（不变）

1. `db_lock_errors` 共享模块建成后知会我 → 我一行切换 `db_helper._is_db_busy_error`（re-export 兼容）+ 自跑 6 用例与 96 回归基线。**建模块时勿漏 POSIX 串**（`could not set lock` / `conflicting lock`），串表见第二轮同步件 §四。
2. T2 的 GUI 委托通道落地后知会我 → 我切 `acceptance_runner.drive_gui("pull")` 为委托入口，并增列"委托排队行为"记录。
3. T1 的持有者归因实际文案给我一句样例 → 我校准 `ATTRIB_PATTERNS` 后再跑终轮（避免误判"归因未命中"）。

---

相关提交（本地，留批 5）：`aa4287d`（A′/A4 实施）｜`b37a6e9`（同步件一）｜`76a711d`（A6 骨架）｜`6aa593c`（同步件二）｜`37d6fdc`（勘误+改名）