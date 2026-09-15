# GUI×daemon 锁冲突修复线 · 证据同步件（策略研发 → dev）

- 日期：2026-09-17｜来源裁定：总调度 §审核+两裁定（裁定①：证据即刻同步 dev，不等 EOD）
- 同步方：策略研发专属会话（承担 A′/T3 + A4 + A2 基线轮）
- 接收方：dev（承担 T1 writers 重试层 / T2 委托+锁链 / T5 文档 / T6 测试）

---

## 一、直接服务 T2（GUI 委托 + 锁链）的三项工程障碍（实测踩坑 + 处置）

### 障碍 1｜daemon 主库强校验会把相对 path 解析成**生产路径** ⚠️ 最危险
- 事实：`daemon.py:3341-3363` 用 `data_config.json` 的 `path` 解析目标库，并与 `db_path()`（受 `QUANTSTUDIO_DATA_ROOT` 影响）比较；**非主库即 exit 2 拒绝**。
- 陷阱：`config/profiles/mcp_only/data_config.json` 的 `path = 'data/quantstudio.db'` 是**相对路径**，锚定项目根 → 解析结果**恒为生产主库**。因此在影子化环境下（`db_path()`=影子）校验必然 BLOCK。
- **禁止的走法**：加 `--allow-non-main-target` 只是"跳过拒绝"，写入目标仍是解析出的**生产路径** → **会写生产库**。
- 正确处置（A2 实测通过）：**影子 profile** —— 复制 profile 后仅改 `data_config.json` 的 `path`/`quarantine.path` 指向影子库，用 `--config-dir <影子配置目录>` 运行 → 校验通过且目标=影子。
- **对 T2 的意义**：GUI 委托子进程若接受"配置目录"参数，务必区分**生产 profile**（真实使用）与**影子 profile**（测试）；任何"允许非主库目标"的旁路都不得用于真实路径。

### 障碍 2｜QFQ schema 安全闸只放行 `EMPTY_OR_NEW` / `COMPLETE_2_1`
- 事实：`writers.py:124 _assert_qfq_schema_init_safe` → `_WriterSchemaMigrationRequired`；手工建过任意表的库会被判 `partial_or_mixed` 并**禁止 writer init**。
- 处置：测试用影子库必须是**纯空库**（0 表），由 daemon 自行建表；或完整复制为 `COMPLETE_2_1`。
- **对 T2 的意义**：委托子进程首次运行若遇到该异常，属**环境库状态问题**而非锁冲突——GUI 的错误提示需能区分（不要一律显示"采集中"）。

### 障碍 3｜后台作业 `exit code 1` 是 pwsh 包装假阳性
- 事实：`python -m ... 2>&1` 经 pwsh 时，原生命令 stderr 触发 `NativeCommandError`，作业被判失败；而 daemon 自身日志明确 "once 完成（task + audit 全部通过）"、水位推进、数据写入。
- **对 T2 的意义**：GUI 委托若经 PowerShell 包装启动子进程，**不要用 pwsh 退出码判定成败**——应读 `--runtime-manifest`（`daemon.py:3392-3428`，含 `writer_db_path`/`batch_audit_db_path`/锁路径/pid/nonce）或 `batch_audit.db` 记录。

---

## 二、直接服务 T1（writers 重试层）的平台串缺口（本轮额外收获）

- 事实：`gui/db_helper.py:_is_db_busy_error` 原只匹配 `"could not lock"`，**缺 POSIX 串**：
  - Windows 实测：中文「另一个程序正在使用此文件」/ 英文 `File is already open in ...`
  - POSIX（macOS/Linux）：`Could not set lock on file ...` / `Conflicting lock is held`
- 后果（红态用例捕获）：macOS 上锁冲突被判为**非 busy** → 异常上抛而非优雅降级。
- 我方已修（提交 `aa4287d`，仅 db_helper 侧）：补 `"could not set lock"`/`"conflicting lock"`/`"already open in"`。
- **对 T1 的意义**：`writers._open_rw_with_backoff` 的**判据必须用同一套串表**（建议统一为一个共享判据函数，避免"重试层只认 Windows 串 → macOS 上永不重试"）。红态用例：`tests/test_gui_db_helper_retry.py::test_is_db_busy_error_recognizes_cn_and_en`。

---

## 三、T2 的排队 UX：实测有界路径（非无限等）

- `daemon.py:3366` once 模式：`CollectorRunLock(timeout=30)`；采集期锁被占 → **30s 超时后 exit 1**，日志 "collector_run.lock 获取失败（daemon 或 GUI 正在采集）"（`daemon.py:3449-3451`）。
- 空闲期锁可获取（实测 `try_acquire=True`）→ 委托可立即执行。
- 结论：GUI 应实现"**提示 + 重试入口**"（有界），不要做无限等待；预检建议：**委托前先 `try_acquire` 预检**（秒级反馈"采集中"，总调度已列为非阻塞优化建议）。

---

## 四、A2 冻结基线数字（A6 验收轮对照口径）

| 态 | 影子真实基线 | 说明 |
|---|---|---|
| A 空闲（真实 GUI 在场） | **0.0%** | 24/24 成功 |
| B 浏览（真实 `DbHelper` 周期查询） | **4.2%** | 1/24 失败；holder 主库值 53.8% 仅作参照 |
| C 采集期（真实 once 拉取中） | **100.0%** | 0/22 成功 |
| D 采集结束后 | **0.0%** | 恢复验证 19/19 |
| 反向：采集期 GUI 只读 | **7/12 返回空（显式降级）** | 采集结束后恢复 |

冻结件：`docs/evidence/gui-daemon-lock-baseline-20260917.md`；原始证据 7 件见其 §5。

---

## 五、可复用的影子化配方（A6 与 dev 自测通用）

1. 影子根：`agent_workspace/shadow_lockprobe/`（`data/` 可省，库直接放根）
2. **`QUANTSTUDIO_DATA_ROOT` 必须在进程启动前注入**（`_paths.py:49-50` 模块加载即解析一次；进程内改无效）
3. 影子 profile：复制 `config/profiles/mcp_only/` → 改 `data_config.json` 的 path/quarantine.path → `--config-dir`
4. 影子库初始化：**纯空库**（`duckdb.connect(p).close()`），让 daemon 自行建表
5. 三方（GUI/daemon/探测）各自注入同一环境变量；**启动前校验六条路径均在影子根**（DATA_ROOT/db_path/quarantine/collector_run.lock/daemon.lock/daemon_status）
6. 生产零接触复核：`(Get-Item data\quantstudio.db).LastWriteTime` 应早于操作时间、且无 `.wal`

---

## 六、我方已交付（供 dev 对照边界）

- `quantstudio/gui/db_helper.py`、`quantstudio/gui/tabs/browser_tab.py`、`tests/test_gui_db_helper_retry.py`
- 本地提交 `aa4287d`（**未推送**，推送留批 5）；回退点 `42fac47fcb31c5e2bb621b70b6938df4db52882f`
- 契约零变化（AST 签名零移除零修改）+ GUI 回归 96 passed + 黄金对照逐项等价

> 我方文件面：`gui/db_helper.py` + `gui/tabs/*` + 新增测试文件。`writers.py`/`workers.py`/`daemon_process.py`/`daemon.py`/设计文档归 dev，本轮未触碰。
