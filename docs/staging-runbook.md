# W2 Staging Runbook — 财务成长指标与分红数据回填

> **状态**：框架代码已完成（W1→W2-0.7A）→ W2-0.9 最终收口（5 项缺陷 A-E + Phase 5 + C/F 补完）。
> 正式库 schema 和数据尚未变更。
> **日期**：2026-07-28（W2-0.9 最终收口）

> ⚠️ **本 runbook 只覆盖 staging 副本操作与 dry-run 审计。直接迁移/替换正式库
> 不在本 runbook 范围内**——那是独立的、需审核通过后再单独授权的门控流程。
> 在审核通过前，禁止执行真实 staging prepare / Tushare 回填 / promotion /
> 修改正式库 / Git stage-commit-push。

## 前置条件

1. W1→W2-0.7A 框架代码 + W2-0.8 框架修复已落地（writer DDL 类型保护、authority
   reconciliation、run_once/CLI 退出码、--quality-audit、baseline-delta audit）
2. 测试通过（W2 专项 + 相关回归 + 全量 1529 项，0 failure，唯一 warning 与 W2 无关原样保留）
3. 正式库 `data/quantstudio.db` 处于旧 schema（fin_indicator 11 列，167,028 行）
4. 采集 daemon 已停止（`verify_daemon_identity` 返回 stale / 无 status 文件）
5. 磁盘空间：生产 DB ~13 GB，建议预留 30 GB 以上（prepare 校验 free ≥ 2× source）

### 🔴 强制：长时运行 staging DB 必须位于 Git worktree 外（W2-0.8 缺陷 C/F）

**根因**：W2 第一次真实执行中，`git.exe`（IDE/Git 索引）持有 worktree 内的 staging.db，
导致 daemon 子进程 **2506 次真实 IO 失败**（`Cannot open file: 另一个程序正在使用此文件`），
batch 标记 failed。下次真实 W2 **必须**把 staging DB 放在 Git worktree 外：

- 推荐 staging 路径：`D:\QuantStudio_W2_Staging\fin_growth_dividend_20260728_r2`
- evidence 日志可仍放项目 `output/w2_fin_growth_dividend_20260728/`
- 任务运行期间**禁止**对 staging 路径执行 Git、文件索引、压缩或同步

### 任务前 preflight（文件占用检查）

启动 daemon 前，尝试独占读写打开 staging DB，确认无 git.exe/IDE/GUI/杀毒持有：

```python
import duckdb
# 预检：能以默认 read_write 打开并立即关闭
c = duckdb.connect(staging_db_path)
c.execute("SELECT 1").fetchall()
c.close()
```

- 若打开失败（file-in-use IO error）→ **不得启动任务**，先排查占用进程
- 出现任何 DuckDB file-in-use IO error，任务必须 **failed**，不得容忍失败率后继续

### 腾讯电脑管家白名单（W2-0.8 缺陷 C）

长跑 daemon（2-4 小时持续 HTTPS 调用 Tushare）会被腾讯电脑管家（QQPCRTP）定时扫描杀掉
（exit `0xC000013A`）。任务前必须确认已将以下加入信任区：
- 进程：`...\Python311\python.exe`
- 目录：QuantStudio 项目根 + staging 路径
- 或临时退出 QQPCRTP

## Step 1: Dry-Run 验证

```powershell
python scripts/backfill_fin_growth_dividend_staging.py `
  --prepare --dry-run `
  --source-db data/quantstudio.db `
  --staging-root data/staging `
  --start-date 2018-01-01
```

预期：9 个步骤全部打印 `[DRY-RUN]`，返回码 0，不创建任何文件。

## Step 2: Staging 准备

```powershell
python scripts/backfill_fin_growth_dividend_staging.py `
  --prepare `
  --source-db data/quantstudio.db `
  --staging-root data/staging `
  --start-date 2018-01-01
```

此步骤：复制生产 DB → staging（size + SHA-256 一致性校验），复制 4 个 config，
更新 `data_config.json` 路径指向 staging.db，创建独立 SQLite ledger/quarantine/log，
写入 `.quantstudio_staging.json` marker（供后续 `--reset-staging` 校验）。

> ⚠️ 如果 staging root 已存在，需要 `--reset-staging` 参数（marker + 路径双重校验，
> 目标不得为磁盘根/项目根/data 根/源库父或含源库）。
> ⚠️ 此步骤**不修改**正式数据库。

## Step 3: 单任务执行

```powershell
# 任务 1: fin_indicator（预计 2-4 小时）
python scripts/backfill_fin_growth_dividend_staging.py `
  --run-task fin_indicator `
  --staging-root data/staging

# 任务 2: stock_dividend（预计 4-6 小时）
python scripts/backfill_fin_growth_dividend_staging.py `
  --run-task stock_dividend `
  --staging-root data/staging `
  --timeout-sec 28800
```

子进程（daemon `--mode once`）写 runtime manifest（atomic `os.replace` + nonce 防重放），
父进程严格校验 manifest 全部字段（`format_version/task/nonce/QUANTSTUDIO_DATA_ROOT/
imported_DATA_ROOT` + 七个路径字段全部 resolve 到 staging root）。

两个任务共享同一 ledgers/quarantine，第二个任务不会清空第一个的记录（batch ID 唯一 +
一任务一批）。`timeout=0` 表示无超时，`timeout>0` 按 elapsed 终止（SIGTERM→60s→SIGKILL），
heartbeat 每 30s 打印 task/pid/elapsed/log/staging_db size。

## Step 4: 质量审计

```powershell
python scripts/backfill_fin_growth_dividend_staging.py `
  --audit `
  --staging-root data/staging
```

生成 `staging-root/audit_evidence.json`，由统一 strict validator `validate_audit_evidence`
门控。验证点（任一不满足返回码非 0）：

- `passed=true`；`audit.checks_run > 0` 且 `audit.errors_count == 0`
- 版本分离：`data_schema_version == "2.0"`（来自 alignment_rules.json）
  且 `ptrade_profile_version == "1.10.0"`（来自 ptrade-api-signatures.json）
- staging/source DB path/size/sha256 与磁盘实际一致；4 个 config 文件 hash 一致
- authority_rules：fin_indicator / stock_dividend 均 `authoritative_source=tushare` 且
  `allow_fallback=false`；runtime manifest ≥ 2 且内容完整；ledger 可读
- batch conservation：每任务 `rows_passed + rows_rejected == rows_raw`；
  batch ID 唯一 + 一任务一批 + status=success + source=tushare
- growth_field_stats（np_yoy/or_yoy/tr_yoy 键存在）+ dividend_stats（税前/税后/stk 键存在）
  + watermark 含两个任务

## Step 5: Promotion Dry-Run

```powershell
python scripts/backfill_fin_growth_dividend_staging.py `
  --promote `
  --staging-root data/staging
```

确认所有 evidence 字段齐全且一致后，打印 promotion plan（含备份路径 + 时间戳）。
**`--promote` 仅 dry-run，绝不移动/重命名任何文件，绝不修改正式库。**

## 直接迁移正式库 — 不在本 runbook 范围

`--promote` dry-run 通过 ≠ 可以替换正式库。实际 promotion（备份正式库、schema 迁移、
原子替换、daemon 重启、authority/data/engine 回测 A/B 验收）是独立的、需在 W2-0.7B
审核通过后**单独授权**的门控流程，由用户主导执行，不在本 runbook 给出可直接照搬的命令。
staging 目录可保留作为审计留痕，或按需手动清理。

## 回滚（仅当 promotion 已被单独授权并执行后才适用）

```powershell
Move-Item data/quantstudio.db data/quantstudio.db.broken
Move-Item data/quantstudio.db.backup-<timestamp> data/quantstudio.db
```

## 中止条件

- 任何阶段返回码非 0
- audit evidence `passed=false` 或 `validate_audit_evidence` 返回 (False, reason)
- staging/source DB size 或 SHA-256 不一致
- 磁盘空间不足或磁盘检测失败
- daemon PID 仍活跃（alive/denied）或 daemon_status.json 损坏
- ledger 被意外清空 / batch conservation 失败 / batch ID 重复
- runtime manifest 缺失 / nonce 不符 / 路径未指向 staging root
- 版本不符（data_schema != 2.0 或 ptrade_profile != 1.10.0）
