# Guard SYSTEM 不可读进程 QDB 域归因方案（白名单缺口修复）

- 版本：v1.1（2026-08-24 02:10 修订；v1 审计有条件通过，本版并入两项必改即批准实施）
- v1→v1.1 修订（总调度审计意见）：
  1. **marker 固定路径**：`D:\miniQMT策略实盘\trading-battle-back\data\qdb_domain_markers\`（禁 $env:TEMP——SYSTEM TEMP=C:\Windows\Temp 与 guard 用户 TEMP 不同域，marker 永不可见，v1 致命缺陷已改）；
  2. **marker 逐进程独立文件**：`<task>_<pid>.json`，finally 只删自身，防并发覆盖互删；
  3. S1 批准：marker 归因成功者豁免启动守卫；
  4. §7 裁定：今晚仍启用备用双禁用（①repair ②周二03:00同步），白名单作纵深防御照常实施；
  5. V2 增跨用户路径验证（SYSTEM 写 + 用户读）。
- 背景：08-23 04:30 SNAP_003 create ABORTED（SYSTEM 不可读 python fail_closed 命中，经核实为全量同步子进程）；总调度 08-23 05:00 改排期指令批准加急微流水线
- 审计第一判据（写目标核实）：**已完成，结论 = 三任务族全部 QDB 域，不写快照源 → 走白名单分支**（证据见 §1）

## §1 写目标核实（分支开关判定）

| 任务族 | 入口 | 实测代码证据 | 写目标 | 判定 |
|---|---|---|---|---|
| 全量同步 | `trading-battle-back/scripts/run_cloud_sync_full.ps1` → `data/sync_to_cloud/run_sync_full_now.py` | `LocalReader` 仅 HTTP `127.0.0.1:9000` SELECT（query_csv，L33-108）；`CloudWriter` 仅远端 `124.223.159.234:8812` INSERT/COPY（`_ALLOWED_PREFIXES`，L20/169） | 本地 QDB 只读 + 云端 QDB 写 | **非快照源写者** ✅ |
| 增量同步 | `run_cloud_sync.ps1` → `run_sync_now.py --all` | 同上 LocalReader/CloudWriter 复用 | 同上 | **非快照源写者** ✅ |
| 对账/缺口推送（全量 ps1 内嵌步骤3/4） | `reconcile_routine.py --fix` / `gap_cloud_push.py` | 同 sync_to_cloud 模块族（本地 QDB 读，云端写；reconcile 的 backfill 写本地 QDB） | QDB 域 | **非快照源写者** ✅ |
| 分钟修复 | `run_repair_minutes_scheduled.ps1` → `data/repair_minutes_loop.py` / `repair_legacy_qfq_fast.py` | `questdb.ingress Sender` ILP 写 QDB（repair_legacy_qfq_fast L59）；loop 仅 QDB 复查 | 本地 QDB 写 | **非快照源写者** ✅ |

**快照源** = QuantStudio 主库 main.duckdb + qfq_aux（18+2 表，sort_keys.json 实读核对）。上述任务族代码路径无任何 duckdb connect / 主库路径引用。

**不可归入白名单的名字（歧义名，维持 abort）**：
- `qfq_maintenance`：**同名双实现**——`trading-battle-back/data/qfq_maintenance.py`（QDB 域，log 实证）vs `QuantStudio/pipeline/qfq_maintenance.py`（**写 qfq_aux，快照源写者**）。按名白名单会误放 DuckDB 写者，禁止；
- 维持 abort 不变：`get_tushare_data`、`cyq_chips_fill`、`_start_all_incremental`、`run_daily_etl_with_health_check`、`gap_stage8`、`refresh_etf_daily`、`run_etf_adj_evening`、`repair_stock_daily`、`qfq_maintenance`、`fix_questdb_qfq`（未核实，保守）。

## §2 问题精确化

08-23 04:30 事故链：任务计划以 SYSTEM 运行 ps1 → 子 python 亦 SYSTEM → 非提权会话 psutil 读不到 cmdline → guard `fail_closed` 命中 → yield ABORT。**且父链 ps1 同样 SYSTEM 不可读**（powershell 不做 fail-closed，故只计 python），父链 cmdline 归因不可行（实测核实：非提权 CIM 同样读不到 CommandLine/ExecutablePath）。

另核实：非提权 `Get-ScheduledTask` **看不到 Trading\* 任务**（提权注册），任务状态归因同样不可行。

→ 归因判据只能依赖**任务侧自声明**。

## §3 设计：marker 文件自声明 + 白名单扩展

### 3.1 白名单扩展（可读命中部分）

`QDB_READ_ONLY_PATTERNS`（yield 豁免集）新增**无歧义 QDB 域名**：

```python
QDB_READ_ONLY_PATTERNS = {
    "check_etl_integrity", "qdb_snapshot_backup",          # 既有
    "run_cloud_sync",          # ps1 锚定：增量/全量包装器
    "run_sync_now", "run_sync_full_now",                   # python 本体（可读时）
    "reconcile_routine", "gap_cloud_push", "push_repair_window",
    "repair_minutes_loop", "run_repair_minutes_scheduled", "repair_legacy_qfq",
}
```

### 3.2 marker 自声明（SYSTEM 不可读命中部分）

**wrapper 改动**（trading-battle-back 三个 ps1，try/finally 包裹，v1.1：固定路径 + 逐进程文件名）：

```powershell
$MarkerDir = 'D:\miniQMT策略实盘\trading-battle-back\data\qdb_domain_markers'
New-Item -ItemType Directory -Force -Path $MarkerDir | Out-Null
$Marker = Join-Path $MarkerDir ('run_cloud_sync_' + $PID + '.json')
Set-Content -Path $Marker -Value ('{"task":"run_cloud_sync","pid":' + $PID + '}')
try { ...原逻辑（含 exit）... }
finally { Remove-Item $Marker -ErrorAction SilentlyContinue }
```

**guard 改动**（`_data_side_tasks_running` fail_closed 分支）：

```python
# fail_closed 命中 → 读 marker：
#   marker 存在 且 marker.task ∈ QDB_DOMAIN_TASKS 且 (marker pid 存活 或 mtime < 15min)
#     → 重分类 matched_pattern = "qdb_domain:"+marker.task（进入 hits，可被 yield 白名单过滤）
#   否则 → 维持 fail_closed（其余不可读进程语义不变，红线）
```

### 3.3 启动守卫语义决策（需总调度裁定，二选一）

**S1（推荐）**：启动守卫同样豁免"已归因 QDB 域"命中（仅 marker 归因成功者）。理由：QDB 域不写快照源，三重 hash 为最终防线；否则周一 23:30 启动时若修复任务（22:00-02:30）仍在跑将 REFUSED。
**S2（保守）**：启动守卫维持严格拒绝 → 周一晚必须启用备用授权①（禁用周一 repair，用户已预授权）。

### 3.4 不改变的红线

- 其余 SYSTEM 不可读进程（无 marker / marker 过期 / pid 已死）→ fail-closed 维持；
- DuckDB/qfq_aux 写者（可读命中歧义名或本名单外）→ abort 维持；
- 三重 hash（pre==post==copy）不受影响，仍为一致性最终防线；
- abort 语义本身不改为 pause（第 6 轮裁定 3 维持）。

## §4 影响面

| 文件 | 改动 |
|---|---|
| `QuantStudio/scripts/governance_snapshot.py` | QDB_READ_ONLY_PATTERNS 扩展 + fail_closed 归因分支（~25 行） |
| `trading-battle-back/scripts/run_cloud_sync.ps1` | marker 写/清（~6 行） |
| `trading-battle-back/scripts/run_cloud_sync_full.ps1` | 同上 |
| `trading-battle-back/scripts/run_repair_minutes_scheduled.ps1` | 同上 |
| `QuantStudio/tests/test_guard_extension_anchor.py`（或新增文件） | 新用例（§5） |

不触碰：快照 hash 逻辑、锁、manifest、任何生产数据。

## §5 验收用例（三类，对应总调度要求）

| # | 场景 | 期望 |
|---|---|---|
| V1 | 合成 SYSTEM 场景：无 marker 时 fail_closed 命中 | abort/REFUSED 维持（回归不变） |
| V2 | 真实同步进程识别：marker 存在+pid 存活+task∈集合 → fail_closed 重分类 | yield 不 abort；S1 下启动不拒 |
| V3 | 其余不可读仍 fail-closed：marker 不存在/过期/pid 死/task 不在集合 | abort 维持 |
| V4 | marker 过期清理：mtime>15min 且 pid 死 | 重分类不生效 + 首次 create/verify 启动时清扫过期 marker |
| V5 | 歧义名回归：cmdline 含 qfq_maintenance（任一实现）| abort 维持（不在白名单） |
| V6 | 既有 guard 测试全量重跑 | 全绿 |

## §6 回退条件

- 任一验收用例失败且 20:00 前不可修 → 整体回退（git 定向恢复两仓库文件）→ 启用备用双禁用（①周一 repair ②周二 03:00 增量，预授权已备）；
- 周一夜 create 期间出现**未归因**的 DuckDB 写者命中 → abort 正确行为，报总调度，不重试超过一次。

## §7 内存风险知会（非本方案范围）

白名单消除的是 abort，不消除 OOM 风险：周二 03:00 增量若并行（1-5.5h，QDB 读+云写，批 5 万行），create hash 峰值 + 同步内存叠加。建议（**决策权在总调度**）：即使本方案落地，也可考虑启用预授权②禁用周二 03:00 增量一次（零代码成本，同步幂等次日补），换取 create 全程独占内存。周一晚 repair（22:00-02:30）同理适用预授权①。
