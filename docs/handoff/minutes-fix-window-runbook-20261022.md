# 今夜合并窗运行手册（minute-fix ④验收 + FP-5 条件并窗）

- 裁定：总调度 2026-10-22「今夜合并窗」；时点=daemon 当轮 post_ingest 完成后（动态 19:00–21:00 起）
- 范围：**minute-fix 合入主树 + 重启新码 + A8 多轮补跑 + A1 实弹指认**；FP-5 2.2 迁移**仅在就绪时并入**，否则顺延单独窗（不硬并）
- 合入件：`docs/handoff/wf-minutes-fix-20261022.patch`（28,187 B，`git apply --check` 已过）+ 新测试文件（单独拷贝，主树已有同名未跟踪文件，patch 不含）

## 0. 前置门（任一不满足 → 不启窗/缩窗）

| # | 条件 | 判据 |
|---|---|---|
| P1 | 当轮 post_ingest 完成 | `daemon_run_state.json` 的 `status=completed` 或日志出现轮次完成行；引擎段不再持 3A 锁 |
| P2 | 无他线在跑 ETL | 进程清单无其他 daemon/once；客户运维2 确认 |
| P3 | 占用声明已发（联合） | 会话群/信箱：时段+操作+预期恢复时点 |
| P4 | FP-5 就绪（仅并窗时需要） | `qfq_reanchor_schema.SCHEMA_VERSION == "reanchor-2.2"` 且 `qfq_schema_status` 含 `complete_2_2` 分支且迁移 `--dry-run` 可跑通 |

## 1. 序列（逐段检查点，任一红停止并回退）

### S1 停 daemon（优雅）
```powershell
# 1) 读 status（键名=instance_token，2026-10-22 实测；勿用 token）
$st = Get-Content data\daemon_status.json -Raw -Encoding UTF8 | ConvertFrom-Json
$req = @{ instance_token = $st.instance_token; reason = "merge-window-20261022"; requested_at = (Get-Date -Format o) } | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText("$PWD\data\daemon_stop.request", $req, (New-Object System.Text.UTF8Encoding($false)))
# 2) 轮询 ≤120s（每 3s），daemon 在批次边界消费 stop.request 后优雅退出
# 3) 超时未退 → Stop-Process -Id $st.pid -Force（记录为强停）→ check_wal.ps1 -Force
```
检查点：进程不存在；`data/daemon_status.json` 不再指向活进程；`daemon_stop.request` 已被消费（文件消失）。
红线：**先停后动树**（运行中切树=混合加载坑，已立族）。

### S2 合入 minute-fix（主树）
```
# 回退点（写前快照纪律）
git add -A -- quantstudio/pipeline/writers.py quantstudio/pipeline/qfq_reanchor_engine.py quantstudio/pipeline/daemon_lifecycle.py
git stash create -m "pre-merge-$(Get-Date -Format yyyyMMdd_HHmmss)"; git stash store <hash>; git reset
# 合入
git apply docs/handoff/wf-minutes-fix-20261022.patch
Copy-Item "D:\miniQMT策略实盘\QuantStudio.wt-minutes\tests\test_minutes_write_conflict.py" "tests\test_minutes_write_conflict.py" -Force
```
检查点：`git status` 仅本方案 4 文件 + 3 新增件；`py_compile` 三文件 exit 0；`pytest tests/test_minutes_write_conflict.py -q` = 19 passed；`pytest tests/test_writers_rw_backoff.py -q` = 8 passed。

### S3 FP-5 2.2 迁移（**仅 P4 满足时**）
```
python scripts/migrate_source_watermark_last_run_id.py --db data/quantstudio.db --dry-run
python scripts/migrate_source_watermark_last_run_id.py --db data/quantstudio.db --allow-schema-contract-change
# 复检
python -c "from quantstudio.pipeline.qfq_schema_status import detect_schema_status; ..."  # 期望 complete_2_2
```
检查点：dry-run 无异常 → 实跑成功 → 复检 `COMPLETE_2_2` → 幂等复跑 no-op。
红线：迁移期间**禁跑任何 once/GUI 写入口**（schema 窗口纪律）；失败 → 立即回退复检 `complete_2_1`。

### S4 重启新码
```
Start-Process python -ArgumentList "-m","quantstudio.pipeline.daemon","--mode","forever","--config-dir","config\profiles\mcp_only" -WorkingDirectory <repo> -WindowStyle Hidden
```
检查点：status pid 有效；日志无 ImportError；`write_retry` 字段出现在 run_state；黄金行冒烟 1/1。

### S5 A8 多轮补跑（etf_minutes 若已重新启用；否则对 stock_minutes 同验）
```
python -m quantstudio.pipeline.daemon --mode once --task mcp_stock_minutes --config-dir config\profiles\mcp_only --quality-audit none
python scripts/verify_minutes_recover.py --db data/quantstudio.db --target 2026-10-22 --tables etf_minutes,stock_minutes
```
协议：未追平 → 记录轮数继续下一轮；连续两轮无推进 → 停并按「补不齐即回报」。
红线：**勿硬拉**；每轮后收敛残留 WAL（`check_wal.ps1 -Force`）。

### S6 A1 实弹指认（新码生效后的首个冲突或首个写批次）
判据（日志/失败槽）：
- 失败行含 `ctx_src=self` 且 `pid/thread` 为真实值；
- 冲突时 `peers_n>0` → 列出同表同时段写者（thread/path/batch）→ **指名并发实体**；
- `peers_n==0` 且 `cross_process_candidate=True` → **跨进程写者成立**（R6 上报，另立跨进程锁方案）；
- `write_retry.attempts>0` 且任务成功 → 重试兜住（W-1 关单证据）。

### S7 明晨 06:00 自然验证
- 日轮 success/failed；分钟任务是否冲突（有重试则看 `write_retry`）；
- `etf_minutes`/`stock_minutes` 止点；B 方案放行率；FP-3 回收；WAL。

## 2. 回退点

| 件 | 位置 |
|---|---|
| minute-fix patch | `docs/handoff/wf-minutes-fix-20261022.patch` |
| 主树写前快照 | `git stash store`（S2 建立，记录 hash） |
| worktree 源 | `D:\miniQMT策略实盘\QuantStudio.wt-minutes`（分支 wf-minutes-fix） |
| DB 快照 | 迁移前 `check_wal.ps1 -Force` + 库文件备份（FP-5 并窗时必做） |

## 3. 留痕

- 窗口起止、每段检查点结果、失败处置 → 本手册末尾追加（或 `docs/handoff/` 窗口记录件）；
- 推送：⑥双仓推送在⑤用户确认后执行（本方案 4 文件 + 3 新增件 + 本手册）。

---

## 4. 窗口执行记录（2026-10-22 夜间，逐段留痕）

| 段 | 结果 | 证据 |
|---|---|---|
| S1 优雅停 | ✅ 5s 消费 stop.request（19:01:38 优雅退出）；WAL 收敛 0；库可读 0.28s | instance_token 键坑已捕获 |
| S2 合入 minute-fix | ✅ 写前快照 3873ae0；patch exit 0；主树 vs worktree 仅 FP-5 COMPLETE_2_2 放行差（无冲突共存）；**19+8 单测绿** | A1-A7 符号 4-6 处命中 |
| S3 FP-5 迁移 | ⚠️ **迁移成功但随即回退**（init DDL 缺口）：dry-run 通过 → 50.54 GB 快照 → 实跑 COMPLETE_2_2（24 行不变）→ **once 冒烟报 [qfq_schema] 空库建表后物理契约回读校验失败** → DROP COLUMN + 版本行回 2.1 → 复检 complete_2_1（8 列/24 行）→ 重启后冒烟 exit 0 | 缺口定位：qfq_reanchor_schema.init_duckdb_schema 仍用 2.1 常量（TARGET_MAIN_DB_2_1_FINGERPRINT/SOURCE_WATERMARK_2_1_DDL），遇 2.2 库走「空库建表」分支必失败 |
| S4 重启新码 | ✅ pid 23068（2.1 库 + 2.2 码；once 冒烟 mcp_etf_basic exit 0） | 前 pid 46624 已优雅停 |
| S5 A8 补跑 | ⏳ once mcp_stock_minutes（pid 66732 @20:15:57）进行中 | 观察 A6 重试行与止点 |
| S6 A1 指认 | 待 S5 完成后按 ctx_src=self/peers_n/cross_process_candidate 执行 | — |

### 台账（总调度 2026-10-22 裁定口径）

1. **FP-5 部分 = 迁移后即回退（init DDL 缺口），代码保留修复后重做**：
   - 迁移脚本本身无缺陷（dry-run/实跑/幂等均通过）；
   - 缺口在 init_duckdb_schema 未适配 2.2（DDL 与 target 指纹双轨）；
   - 修复责任：客户运维2（补 COMPLETE_2_2 分支：TARGET_MAIN_DB_2_2 + SOURCE_WATERMARK_2_2_DDL + ssert_init_allowed/ssert_code_ddl_matches_target_2_2）；修复后**单独窗**重做迁移；
   - **50.54 GB 迁移前快照保留位**：data/backup/quantstudio.db（mtime 14:57:12，complete_2_1 状态；保留至 FP-5 重做成功 + 一周观察后清理）。
2. **minute-fix = 合入生效**：A1-A7 在树（writers.py / qfq_reanchor_engine.py / daemon_lifecycle.py），2.1 库路径验证通过（once 冒烟 exit 0），主树 19+8 单测绿。
3. **明晨 06:00 日轮 = 双案自然验证**：minute-fix 重试实弹（写冲突重试 N/3 + write_retry 统计首现 run_state）+ A8 观察字段 + FP-3 回收被中断周期（本窗中断的 cyc_e683502ac70f 由 4h 门回收）。