# Quickstart: 两条操作路径 + 窗口纪律

**Feature**: 003-delivery-bundle-import | **Date**: 2026-10-31 | **关联**: `plan.md` §测试环境与窗口、`contracts/cli.md`

> 本文件只描述**已验证可行的操作序列**与**预期读数形态**，不含实现代码。所有体积/状态读数必须现场实测，禁止照抄本文件里的样例数字。

---

## 0 前置（两端共同）

| 项 | 检查 | 命令 / 位置 | 预期 |
|---|---|---|---|
| duckdb 版本在放行集合内 | `{1.4.5≤x<1.5} ∪ {x==1.5.6}` | `python -c "import duckdb;print(duckdb.__version__)"` | 打印版本且落在集合内（集合外启动门禁会拒启，码 3） |
| `DATA_ROOT` | 唯一真相源（env → `data_config.json` → 项目根/data） | `python -c "from quantstudio._paths import DATA_ROOT;print(DATA_ROOT)"` | 打印真实路径（中文路径属正常） |
| 配置项 | `config/delivery_config.json` | 缺失 ⇒ 视为未选择，按钮禁用（不报错） | 首次用 `qs-deliver set` 或 GUI 选择目录生成 |
| 写锁 | 无其他持有者 | `qs-deliver status` 或 GUI 页签状态区 | `write_lock: free` |
| daemon | 未运行 | `qs-deliver status` 的 daemon 行 | `daemon: not running` |
| 磁盘 | 导出/导入需要空间 | `qs-deliver status`（或 `shutil.disk_usage` 读数） | 导出：≥ 三件套 × 1.1；导入：同卷 ≥ 包体积 × 1.1 |

---

### 0.1 测试回归也在窗口内（W0 实测所得）

**既有测试回归（相关面 / 全量）必须在 daemon 停止窗口内跑**。2026-10-31 W0 实测：daemon 运行状态下用真实 `DATA_ROOT` 跑相关面测试，与写锁争用 **240 s 未完成**；隔离 `QUANTSTUDIO_DATA_ROOT` 虽能出读数，但会让"生产路径必须被拒绝"一类断言失配（实测 6 项），**不能据此判回归**。纯单元测试（`quantstudio/delivery/**` 的 manifest/stats 等，不触库、不取锁）不受此限。

---

## 1 路径 A：本机导出交付包（实施机）

### 1.1 开启窗口（铁律 19：先声明占用）

```
① 声明：时段 + 操作（停 daemon / 只读导出验证）+ 预期恢复时点（落会话群或信箱）
② 精确 PID 停 daemon：
   PID = (data/daemon_status.json).pid        # 候选集只有这一个 + 排除自身 $PID
   打印候选清单核对归属 → Stop-Process -Id $PID（禁宽关键词匹配、禁 -Force 群杀）
③ 写挡板：data/backtest_stop.flag
   {"reason":"delivery_window","at":"<ISO8601>","owner_pid":<本进程PID>}
④ 窗口就绪三读数（缺一不可）：
   - daemon 进程不存在（按 daemon_status.json 里记录的 pid + create_time 双验）
   - data/daemon_status.json 无 running
   - data/backtest_stop.flag 存在
```

> **为什么写 flag**：计划任务 `QS_DaemonForever` 现为 **Boot 触发**（Daily 已于 2026-10-30 移除），拉起路径是 `scripts/start_daemon_guarded.ps1`，该脚本在 `-Force` 未给时**见 flag 即跳过**（`start_daemon_guarded.ps1:31-34`，`README.md:123`）。每日 06:00 的采集轮次来自 daemon 自身调度，daemon 已停 ⇒ 无轮次。

### 1.2 导出

```
qs-deliver export --export-dir "D:\export"
# 或 GUI：页签「数据交付」→ 选择导出目录 → 「交付包导出」
```

预期（形态，非具体数值）：
```
[delivery] export  → <export_dir>/<YYYYMMDD_HHMMSS>
  quantstudio.db        <实测>   sha256=<64hex>  ok
  qfq_aux.db            <实测>   sha256=<64hex>  ok
  qfq_aux_mcp_gen1.db   <实测>   sha256=<64hex>  ok
  同刻一致: WAL 已归零（3/3）    红线自检: 通过
  耗时 <实测> s
```
拒绝时的预期提示（任一命中即零产出）：库状态不合格 / 库被占用（写锁）/ 请先停止采集 / 路径重叠 / 目标不可写或目录受保护 / 同步盘需确认 / 空间不足 / **同刻一致未达成：WAL 未归零（实测 X MB）**。

### 1.3 关闭窗口（唯一高危点）

```
⑤ 删除挡板：Remove-Item data/backtest_stop.flag
⑥ 复核：flag 不存在（Test-Path 返回 False）
⑦ 恢复采集（二选一，按你的判断）：
   - 手动启动：GUI 点「启动采集」；或 scripts/start_daemon_guarded.ps1（不带 -Force）
   - 保持停止：明确记录"采集由用户择时手动恢复"
⑧ 回执：flag 删除确认 + daemon 当前状态一行（停 / 运行 + PID）
```

**⛔ 窗口内禁止手动启 daemon**：`daemon_lifecycle.run_forever()` 起始会 `clear_stop_flag()`（`daemon_lifecycle.py:585-586`）。一旦有人手动启 daemon，flag 被静默清除、Boot 保护失效 ⇒ **判窗口作废**，本轮读数作废，须重写 flag 后重测。

**⛔ 机器在窗口内重启**：Boot 触发会先看 flag（跳过启动）；但重启后若 daemon 曾启动过一次，flag 已被清除 ⇒ 重启完成后必须**重新确认 flag 存在**，不存在则立即重写。

---

## 2 路径 B：客户侧导入（客户机）

### 2.1 前置（客户机）

```
① 确认程序能启动、duckdb 版本合格（同上 §0）
② 把交付包放到 import_dir（例如 D:\delivery），其下**恰好一个**含 manifest.json 的子目录
③ qs-deliver set --import-dir "D:\delivery"      # 或 GUI 选择
④ qs-deliver status                              # 看 DATA_ROOT / 两项配置 / journal / daemon
⑤ 关闭占用：停止采集（GUI「停止采集」），确认 daemon: not running
```

### 2.2 先只读校验（零改动）

```
qs-deliver verify --import-dir "D:\delivery" --json
```
期望 `ok: true`；若 `ok: false`，按 `failure_kind` 处置：

| `failure_kind` | 提示 | 退出码 | 处置 |
|---|---|---|---|
| `1_package` | 「交付包不完整或已损坏，请重新获取」 | 4 | 重新取包（不要重试） |
| `2_program_version` | 「程序版本与数据版本不匹配，请更新程序」 | 6 | 更新程序 |
| `3_config` | 「配置不匹配，请检查」+ 两路径对照 | 5 | 修 `config/profiles/mcp_only/qfq_aux_paths.json` 或改用 legacy 判定 |
| `4_manifest_version` | 「包由更新版本的程序生成，请先更新程序」 | 6 | 更新程序（**不要**尝试用旧程序解析） |

同时看 `route` 段：
- `reason` 以 `gen1:` 开头 ⇒ 客户将走 gen1 世代，包内 `qfq_aux_mcp_gen1.db` 必须校验通过；
- `reason` 为 `legacy:released=false` / `legacy:no_active_cutover` / `legacy:fail-secure:*` ⇒ 报告会明写「配置缺失，已按 legacy 判定；包内 `qfq_aux.db` 必须校验通过；如需 gen1 请修复 config」。

### 2.3 再导入（有备份、可回滚）

```
qs-deliver import --import-dir "D:\delivery"            # 会打印动作摘要并要 [y/N]
qs-deliver import --import-dir "D:\delivery" --dry-run  # 只看将要发生什么，零改动
```
预期结果（`ImportResult`）：
```
mode: update | repair | install
backup: data/backup_import_<ts>  (<实测体积>)  method: rename
files: quantstudio.db replaced | qfq_aux.db replaced | qfq_aux_mcp_gen1.db replaced
复验: OK=<n>  BEHIND=<n>  AHEAD=<n>  MISSING=<n>  UNKNOWN-SOURCE=<n>  EMPTY=<n>  UNKNOWN-BASIS=<n>
结论: 可增量    基线: <过时/可能过时>
```
- `mode: install` ⇒ 明示「首次安装，未做备份（无文件可备）」，`data/` 下 `snapshots/`、`.gitkeep`、`README.md` 保留；
- `mode: repair` ⇒ 结果页额外给「旧库原件已保留在 `backup_import_<ts>/`（同卷移动，零额外占用）；确认新库正常后可手动删除该目录释放空间——释放权在客户」+ 取证摘要（字节数 / SHA256 / 打开错误 / schema 探测）；
- `结论: 可增量` 后，客户手动点「全部执行（增量）」；程序**不会**自动触发采集。

### 2.4 回滚与崩溃恢复

```
# 正常回滚：把备份目录里的文件移回 data/（RESTORE_README.txt 有说明）
# 或程序路径：
qs-deliver status                         # 有残留 journal 会显示相位与已完成项数
qs-deliver recover --action rollback      # 回到导入前
qs-deliver recover --action continue      # 继续完成剩余替换并复验
```
**禁止**：有残留 journal 时直接再跑一次 `import`（内核会拒绝并给退出码 7——不允许"按文件存在性重新推断模式"）。

---

## 3 验证清单（读数的预期形态）

| 检查 | 命令 | 预期 |
|---|---|---|
| 白名单外文件数 = 0 | `Get-ChildItem <bundle> -Recurse -File` 与白名单比对 | 只得 4 项（3 db + manifest.json） |
| `*.env` / `-shm` 命中 = 0 | `Get-ChildItem <bundle> -Recurse -Filter *.env`、`*-shm` | 空 |
| SHA256 可复算 | `Get-FileHash <bundle>\*.db -Algorithm SHA256` 与 manifest 逐项比对 | 全等 |
| 三件同刻一致 | 用一次 python 脚本连续只读打开三件并查询 | 三次打开成功，主库 `qfq_active_cutover` / `source_watermark` 可读，因子库 `count(*)` 可读 |
| 双端一致 | `qs-deliver export` 两次（CLI/GUI）+ 比对 manifest（剔除时间字段） | 文件清单与逐文件 SHA256 全等 |
| 纯增益 | `python scripts/delivery_pure_gain_audit.py --mode audit --baseline <基线json>` | 受保护路径 diff 与基线逐字一致（或已申报他线来源） |
| 既有测试 | `python -m pytest tests -q` | `failed == 0`，且相对开工基线**零新增失败** |

---

## 4 常见误操作（都已被程序拦住，列出来避免"绕过"）

| 误操作 | 程序行为 |
|---|---|
| 把导出目录设成 `data/` 或 `data/` 的子目录 | 拒绝（`PATH_OVERLAP`），给三路径对照 |
| 直接把交付包解压到 `data/` 手动覆盖 | 不受本特性保护（旧方式）；GUI 页签会在文案里指向「数据导入」 |
| 拿旧程序打开新版本 manifest | 第④类提示 + 退出码 6，不崩、不尝试按旧格式解析 |
| 在 daemon 运行时导入 | 拒绝（`DAEMON_RUNNING` + 退出码 3） |
| 覆盖失败后手动删半成品再重跑 | 有 journal 时内核拒绝重推模式（退出码 7），必须先 `recover` |
| 备份目录确认无用后手工删除 | 允许（释放权在客户），但删除后即失去该次回退能力 |
