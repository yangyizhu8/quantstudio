# 契约：导入/校验内核 `verify_bundle` / `import_bundle` / `recover_journal`

**模块**: `quantstudio/delivery/verify.py` + `import_kernel.py` + `journal.py`（经 `__init__.py` 导出） | **spec**: FR-009~019 / FR-033 / FR-036~040 | **plan**: §4 / §5 / §6

---

## 1 四个函数

```python
def verify_bundle(*, import_dir: Path, config_dir: Path | None = None,
                  progress: ProgressCB | None = None,
                  cancel: CancelToken | None = None,
                  now: datetime | None = None) -> VerifyReport
    """只读校验。MUST NOT 修改 DATA_ROOT 下任何文件（含日志/journal）。"""

def import_bundle(*, import_dir: Path, config_dir: Path | None = None,
                  dry_run: bool = False,
                  progress: ProgressCB | None = None,
                  cancel: CancelToken | None = None,
                  confirm_sync_dir: bool = False,
                  allow_repair: bool = True,      # False ⇒ 修复模式改判为拒绝（保守调用方用）
                  now: datetime | None = None) -> ImportResult
    """九步导入。dry_run=True ⇒ 只做 Phase 1–4（零改动）。"""

def detect_pending_journal(*, data_root: Path | None = None) -> JournalSummary | None
    """检测残留 journal（GUI 启动与导入入口各调一次）。返回摘要，不改任何状态。"""

def recover_journal(*, action: Literal["continue", "rollback"],
                    config_dir: Path | None = None,
                    progress: ProgressCB | None = None,
                    now: datetime | None = None) -> RecoveryResult
    """据残留 journal 继续/回滚。MUST NOT 重新推断模式（FR-014/T6）。"""
```

## 2 `verify_bundle` 契约

### 2.1 行为

1. `locate_package(import_dir)`：扫描 `import_dir` 下**一层**子目录，找 `manifest.json`；**恰一个**才继续（FR-038）。零个/多个 ⇒ `failure_kind=①`。
2. 版本门（data-model §6 顺序）：解析 JSON → 读 `manifest_schema_version` → ④/②判定 → v1 字段级校验。
3. 白名单完整性 + 文件存在性（先判存在，再算哈希）。
4. **逐文件 fail-fast**：顺序 = **体积升序（小文件优先）**（spec FR-019 已定为主序；同体积按 manifest 声明序稳定排序）。逐文件流式 SHA256，**首个不符即返回**（不跑完全量）。
5. 主库 schema 实测（对**包内副本**只读连接 `detect_schema_status`）vs 程序期望 vs manifest 记录。
6. 路由一致性断言（plan §6），产出 `route` 段。
7. 逐表统计（`stats.py` 同口径）→ `tables[]`（供报告与后续复验比对）。

### 2.2 `VerifyReport` 契约点

| 字段 | 保证 |
|---|---|
| `ok` | 全部通过才 `True`；任何一项失败即 `False` 且 `failure_kind` 非空 |
| `failure_kind` | ∈ {`"1_package"`, `"2_program_version"`, `"3_config"`, `"4_manifest_version"`, `None`}（字符串常量，CLI 直接映射退出码） |
| `reasons` | 人可读，**按类聚合**（不得用"更新项目"这种混合话术覆盖四类） |
| `route` | `{resolved_path, reason, target_path, matched, legacy_security_note}`；`reason` 原样来自 `resolve_runtime_aux_path` |
| `warnings` | 未知字段、绝对路径被拒后的补充说明、`released` 与 cutover 不一致、**`row_count_method=="estimate"` 的表**（须在 `tables[]` 逐行标「该表行数为估算值」，禁与 exact 行静默混排——②审 A3 强化②）等 |

### 2.3 硬约束

- **零修改**：函数体内不得出现对 `DATA_ROOT` 的写（含 `open('w')`/`mkdir`/`os.replace`/日志文件）。测试用 monkeypatch 包裹写函数，任何写调用即 FAIL。
- **只读打开必须 `immutable=1`**（W1 实测教训）：对 SQLite 件用普通 `mode=ro` 打开会**在同目录生成 `-shm`/`-wal` 边车**——对交付包做自检时那等于往包里写非白名单文件（W1 首跑即被 SC-002 抓出）。统一走 `verify.sqlite_ro_uri()`（`file:…?mode=ro&immutable=1`）。
- **不触发采集**：不得 import/调 `daemon`、不得 `subprocess`。
- **可重复**：同一包两次调用返回相同 `ok`/`failure_kind`/`tables`（`elapsed_sec` 除外）。

## 3 `import_bundle` 契约

### 3.1 相位与顺序（FR-013，机检序列）

```
LOAD_CONFIG → PRECHECK → VERIFY_READONLY → (fail ⇒ 返回, 零改动)
           → ACQUIRE_AND_JOURNAL → BACKUP → OVERWRITE → REVERIFY → RELEASE_AND_PASS
```
- `progress` 回调第 3 参数即相位名，测试直接断言序列（顺序不可交换）。
- `dry_run=True` ⇒ 序列止于 `VERIFY_READONLY`，`ImportResult.mode` 仍给出**预期模式**（便于客户先看要发生什么）。

### 3.2 模式判定（FR-014，唯一入口 `detect_mode()`）

```
if detect_pending_journal() is not None: → RecoveryRequiredError（调用方必须先走 recover_journal）
elif not (DATA_ROOT/"quantstudio.db").exists():                → INSTALL
elif can_open_readonly(main_db) and schema_status != UNKNOWN:  → UPDATE
else:                                                          → REPAIR（allow_repair=False ⇒ ConfigMismatch/拒绝）
```
- `RecoveryRequiredError` 携带 `JournalSummary`（相位 + 已完成项数），GUI 据此弹二选一。
- 判定后**写入 journal.mode 并锁定**，之后不再重算。

### 3.3 备份契约（FR-015）

| 情形 | 方式 | 空间门槛 | 备注 |
|---|---|---|---|
| 同卷（`st_dev` 相同，默认） | `os.replace` 移入 `backup_import_<ts>/` | 包体积 × 1.1 | O(1)、零额外空间；`RESTORE_README.txt` 必写（FR-039） |
| 跨卷（`st_dev` 不同：`DATA_ROOT` 为挂载点/符号链接） | 复制到备份目录 + 保留原件 | 备份体积 + 包体积 × 1.1 | 界面明示额外占用（SC-011） |
| 修复模式（源导入前不可读） | `os.replace` 原件入备份（**取证保留，零额外占用**） | 同上卷 | 记 `forensic`；**不得删除原件**；结果页给释放提示 |

**回验两分**：
- 源文件导入前**可读** ⇒ 备份后必须能只读打开 + `schema_status` 可判；失败 = **真失败** ⇒ 回滚并终止。
- 源文件导入前**不可读** ⇒ 回验降级为取证（记 `bytes`/`sha256|null`/`open_error`/`schema_status`）⇒ **继续**。

### 3.4 覆盖契约（FR-016/017）

1. 全部 `*.incoming` 先落 `DATA_ROOT` 根部（与目标同目录 ⇒ 保证 `os.replace` 同卷原子）；
2. 逐个 `os.replace(tmp, target)`；每完成一个立即写 journal（`state=done`）；
3. 任一失败 ⇒ 反向回滚已 `done` 项（`backup_name` → 原位）；**禁止**留混合态；
4. **只增改不删**：不删除任何白名单外文件；删除动作仅允许作用于 `created_files`（安装模式回滚）。

### 3.5 `ImportResult` 契约点

| 字段 | 保证 |
|---|---|
| `mode` | 与实际执行分支一致（`dry_run` 时为预期模式） |
| `backup` | `method ∈ {rename, copy, none}`；`install` ⇒ `none` 且 `bytes=0` |
| `files[]` | 逐文件 `action ∈ {replaced, created}` + `sha256_ok` |
| `reverify` | `tables[]` 与导出侧同一实现产出的 `TableStat`；`can_incremental = 无 MISSING/BEHIND/AHEAD` |
| `forensic` | `mode=="repair"` 时非空（data-model §5.3） |
| `journal_finalized` | 成功路径必须 `True`（journal 已删） |
| `exit_code` | 与 CLI 退出码表一致（见 `contracts/cli.md`） |

### 3.6 幂等（SC-016）

同一包连续导入两次：第二次的 `reverify.tables` 必须与第一次**逐字段相等**（`row_count`/`max_date`/`watermark_last_date`/`state` 全等；`elapsed_sec` 除外）。第二次仍会备份（把第一次导入的结果再备份一次）——这是**预期行为**（备份是"导入前状态"的快照，不是"原始状态"），但必须在结果页显示备份体积，不隐藏占用增长。

## 4 `recover_journal` 契约

| 前置 | 行为 |
|---|---|
| 无 journal | 返回 `RecoveryResult(ok=False, reason="no_journal")`（不抛异常） |
| `journal_version` 未知 | 拒绝自动恢复，提示人工（fail-closed） |
| `journal.data_root != 当前 DATA_ROOT` | 拒绝（防止把 A 机的 journal 用在 B 机数据上） |
| `phase ∈ {rolling_back}` 且 `action=="continue"` | 拒绝（回滚未完成不得继续） |
| 取锁失败（残留锁 + 持有者存活） | 拒绝并给持有者诊断（不得强抢） |

**不变式**
1. 恢复流程**禁止**调用 `detect_mode()`；
2. 每个动作后 journal 原子更新；终态（成功收尾/回滚完成）删除 journal；
3. 「继续」在 `phase==backup_done` 时**不得再次备份**（避免二次备份污染唯一回退资产，SC-014 明文）；
4. 回滚的删除动作仅限 `created_files`。

## 5 取消与错误语义

| 触发 | 语义 |
|---|---|
| `cancel` 在 Phase 1–4 | 干净返回（零改动） |
| `cancel` 在 Phase 5–7 | 按「程序内失败」处理 ⇒ 回滚到备份状态（FR-019：取消必须回滚） |
| `cancel` 在 Phase 8 复验 | 已完成替换；**不回滚**（复验是只读），返回 `ok=False, reason="cancelled_during_verify"` 并提示可重跑复验 |
| 异常（未预期） | 捕获后按 Phase 决定回滚或保 journal 待恢复；异常原文进 `warnings` 与日志，**不得静默吞掉** |

## 6 禁止项（AST/评审可核）

- 不 import `quantstudio.pipeline.writers` / `daemon` / `backtest.*`；
- 不调用 `subprocess`（不启停 daemon、不调外部工具）；
- 不写 `config/` 下除 `delivery_config.json` 之外的任何文件；
- 不写 `batch_audit.db` / `quarantine.db` / `task_resume/` / 日志目录。
