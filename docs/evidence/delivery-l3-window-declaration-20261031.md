# 占用声明 · L3 生产库只读窗口（003-delivery-bundle-import · W2 硬停点）

**声明时刻**：2026-10-31 14:52（Asia/Shanghai）｜**批准来源**：审核方 2026-10-31 裁定（窗口批准：14:45 起，240 分钟硬上限）
**执行会话**：SDD 执行会话（specs/003-delivery-bundle-import）｜**铁律 19**（daemon 生命周期跨会话占用）

## 1 占用窗

| 项 | 值 |
|---|---|
| 时段 | 2026-10-31 **14:45 – 18:45**（240 分钟硬上限；预计 18:30 前收口） |
| 操作 | 停 daemon 进程（精确 PID）→ 写 `data/backtest_stop.flag` → 跑窗口三件套 → 删 flag → 恢复 daemon |
| 预期恢复时点 | 窗口结束即刻（恢复命令见 §5），或按用户要求保持停止 |
| 影响的采集轮次 | **无**（`daemon_schedule.daily_time=06:00` 已过；窗口不跨 06:00） |

## 2 开窗前重核的三件事实（2026-10-31 14:47 实测，不沿用旧值）

| 事实 | 读数 |
|---|---|
| daemon PID | **19148**（`--mode forever`，started 2026-10-31T02:51:59，create_time 1793386318.6922874） |
| `daemon_status.json` | status=running，instance_token `20e7f0bd…`，config_dir `config\profiles\mcp_only` |
| `data/*.db` 指纹 | `quantstudio.db` 62,766,198,784 B / `qfq_aux.db` 11,643,473,920 B / `qfq_aux_mcp_gen1.db` 53,248 B / `qfq_aux_c4resume_staging.db` 53,248 B / `batch_audit.db` 13,881,344 B / `quarantine.db` 1,408,012,288 B（mtime 见窗口回执） |
| 磁盘 | D: 可用 **145.7 GB**（单份包 ≈ 69.2 GB；两次串行、中途删除 ⇒ 峰值 1 份） |

## 3 他线占用观察（铁律 19「避让不接管」）

开窗前发现**另一个会话**在跑一次性采集：
`PID 40500 · --mode once --task mcp_etf_daily · 14:47:42 启动 · 父进程 powershell.exe`。

**处置**：**不杀、不接管**，等其自然结束；仅当它退出后才停 daemon 并开窗。若长时间不退（>15 分钟）⇒ 本次窗口顺延，不与他线并发操作同一 daemon。

## 4 窗口内三件套（顺序固化，缺一不开）

| 顺序 | 内容 | 命令 | 判据 |
|---|---|---|---|
| ① | 6 项隔离失败的无隔离复验 | `pytest tests/test_qfq_schema_migration.py::TestProductionRefuse tests/test_qfq_schema_migration.py::TestReportPathSafety tests/test_writer_channel_contract.py::test_b1_chunked_variant_is_same_channel_not_third -q` | 全绿；若仍红 ⇒ 判真回归并停 |
| ② | **CLI 两次串行导出 + 逐位比对** | `python -m quantstudio.delivery.cli export --export-dir D:\delivery_l3_tmp\run1 --json` → 把 manifest + 逐文件 sha256 清单落 `docs/evidence/delivery-l3-export1-20261031.json` → **删除 run1 目录** → 同命令导出到 `D:\delivery_l3_tmp\run2` → 比对 | 文件清单与逐文件 SHA256 一致率 **100%**（`generated_at`/`export.*` 时间字段除外） |
| ③ | **全量测试基线**（最后跑） | `python -m pytest tests -q`（真实 `DATA_ROOT`） | passed/failed 计数 + **既有失败逐条**；整份才算落成；240 分钟耗尽 ⇒ 中止并出「已完成 N / 剩余 M」边界报告 |

**输出目录**：`D:\delivery_l3_tmp\`（**不在 `data/` 内**，FR-035）；窗口末清理并回执。

## 5 恢复动作（唯一路径，禁现场发挥）

```powershell
# 删除挡板
Remove-Item data\backtest_stop.flag
# 复核不存在
Test-Path data\backtest_stop.flag   # 必须 False
# 恢复采集（唯一拉起路径；禁 Start-Process）
schtasks /Run /TN QS_DaemonForever
# 复核：daemon_status.json 变 running 且 pid 存在
```

## 6 中断与异常处置

- **窗口内禁手动启 daemon**（`run_forever()` 起始会 `clear_stop_flag()`，保护即失效）。
- 机器若在窗口内重启：Boot 触发会经 `start_daemon_guarded.ps1` 看 flag 跳过；**重启后必须重新确认 flag 存在**，不存在则立即重写。
- **任一异常**（daemon 意外起来 / 磁盘异常 / 锁冲突 / 测试挂死超出预算）⇒ **判窗口作废**：立即删 flag → 恢复 daemon → 出边界报告停手；**读数作废，不硬凑**。

## 7 回执必含（缺项即不完整）

占用声明全文 → 开窗三读数（进程不存在 / status 无 running / flag 存在）→ 三件套逐项读数（全量 passed/failed + 既有失败逐条；两次导出比对结论；6 项复验结果）→ SC-013 窗口前后各一次 → flag 删除确认 → daemon 恢复状态一行（PID + started_at）→ `D:\delivery_l3_tmp\` 清理确认。
