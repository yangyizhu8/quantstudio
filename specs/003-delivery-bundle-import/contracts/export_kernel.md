# 契约：导出内核 `export_bundle`

**模块**: `quantstudio/delivery/export_kernel.py`（经 `quantstudio/delivery/__init__.py` 导出） | **spec**: FR-001~008 / FR-034 / FR-035 / FR-041 | **plan**: §3

---

## 1 签名

```python
def export_bundle(
    *,
    export_dir: Path,
    progress: ProgressCB | None = None,
    cancel: CancelToken | None = None,
    config_dir: Path | None = None,      # 默认 config/profiles/mcp_only
    accept_sync_dir: bool = False,       # W-2 同步盘警示的显式确认
    verify_hashes: bool = False,         # R3 的二次全量哈希开关（默认关）
    now: datetime | None = None,          # 测试注入时刻（默认 timezone-aware now）
) -> ExportResult
```

- **纯同步阻塞**：GUI 端在 `QThread`（薄 worker）里调用；内核自己不创建线程、不弹窗、不读 stdin。
- **无全局状态**：所有路径来自参数 + `_paths.DATA_ROOT`（唯一例外，属既有唯一真相源）。
- **幂等性**：非幂等（每次产生新时间戳子目录）；同一 `export_dir` 重复调用**永不覆盖旧包**。

## 2 输入约束（调用方保证，内核仍复核）

| 约束 | 违反行为 |
|---|---|
| `export_dir` 已存在或可创建 | 抛 `ExportRefused(TARGET_NOT_WRITABLE)` |
| `export_dir` 与 `DATA_ROOT` 互不包含 | 抛 `ExportRefused(PATH_OVERLAP)` |
| `export_dir` 与 `import_dir` 互不包含（若已配置） | 同上 |
| 调用方不得在自己持有写锁时调用 | 抛 `ExportRefused(WRITE_LOCK_HELD)`（附持有者诊断） |

## 3 输出 `ExportResult`

```python
@dataclass(frozen=True)
class ExportResult:
    bundle_dir: Path                 # <export_dir>/<YYYYMMDD_HHMMSS>
    manifest_path: Path
    files: tuple[BundleFileInfo, ...]     # path/bytes/sha256（相对包根）
    total_bytes: int
    duration_sec: float
    qualified: bool                  # 红线自检全绿
    self_check: dict                 # 与 manifest.self_check 同构
    warnings: tuple[str, ...]        # 同步盘/estimate 降级/lock 释放失败 等
```

## 4 拒绝码（`ExportRefused.code`，全部 ⇒ **零产出**）

| code | 触发 | GUI/CLI 呈现要点 |
|---|---|---|
| `SCHEMA_STATUS_INVALID` | `detect_schema_status` 不在 {`COMPLETE_2_1`,`COMPLETE_2_2`}（含 `EMPTY_OR_NEW`） | 显示实测状态值 + 「需先修复」 |
| `WHITELIST_FILE_MISSING` | 三件套任一不存在 | 显示缺失文件名 |
| `WHITELIST_FILE_UNREADABLE` | 只读打开/`SELECT 1`/`quick_check(1)` 失败 | 显示文件名 + 错误原文 |
| `WRITE_LOCK_HELD` | `read_holder()` 非空 | 显示持有者 task_id/pid/host + 存活判定 |
| `DAEMON_RUNNING` | daemon 存活（status + PID/create_time 双验） | 显示 pid + started_at + 「请先停止采集」 |
| `PATH_OVERLAP` | 三路径互不包含校验失败 | 三路径对照 |
| `TARGET_NOT_WRITABLE` | 目录 create+delete 探针失败 | 「文件被占用/目录受保护」 |
| `TARGET_HANDLE_BUSY` | 源文件 `msvcrt.locking(LK_NBLCK)` 试探失败 | 提示杀软/预览/同步客户端 |
| `SYNC_DIR_UNCONFIRMED` | 同步盘特征命中且 `accept_sync_dir=False` | 醒目警示 + 要求确认 |
| `DISK_INSUFFICIENT` | 可用 < 三件套 × 1.1 + 余量 | 报所需/可用（字节，实时） |
| `WAL_NOT_ZERO` | Phase 3 断言失败 | 报「同刻一致未达成」+ 实测字节数 |

**技术细节**：每个 code 携带 `readings: dict`（实测读数），便于 GUI 原样展示、CLI `--json` 输出；**禁止**把读数写死成文案常量（SC-012 的延伸）。

## 5 失败异常（非拒绝，发生在 Phase 1 之后）

| 异常 | 语义 | 清理保证 |
|---|---|---|
| `ExportAborted(reason)` | 用户取消（`cancel` 置位） | 清理本会话新建的时间戳子目录 |
| `ExportFailed(phase, cause)` | 复制/自检阶段错误（IO、句柄、磁盘耗尽） | 同上；**旧包不受影响** |
| `ExportQualificationFailed(reason)` | 复制完成但红线自检红 | **不清理**（保留取证），`qualified=False` 返回 |

## 6 不变式（invariants，契约测试逐条覆盖）

1. **锁窗口**：Phase 1 取锁成功 → Phase 5 完成后才释放（`finally` 保证）；任何拒绝路径**不取锁**。
2. **WAL 归零**：进入 Phase 4 前，三件套的 `-wal` 均不存在或 `size==0`；否则不产生任何产物。
3. **白名单刚性**：产出目录内文件集合 == {`quantstudio.db`, `qfq_aux.db`, `qfq_aux_mcp_gen1.db`, `manifest.json`}；任何其他条目 ⇒ `qualified=False` + `warnings`。
4. **旧包不可变**：同一 `export_dir` 下既有子目录的文件清单/SHA256 在调用前后逐位一致（FR-034）。
5. **manifest 最后写**：`manifest.json` 的时间戳晚于所有数据文件；目录内无 manifest ⇒ 视为不完整。
6. **零 DDL/DML**：除 Phase 2 的 `CHECKPOINT` 与 SQLite 的 `wal_checkpoint` 外，对三件套无任何写操作（测试用写调用捕获 + AST 断言）。
7. **进度单调**：`progress(done_bytes, total_bytes, phase)` 中 `done_bytes` 单调不减，`phase` 只前进。
8. **取消语义**：`cancel.is_set()` 后 ≤ 1 个块的延迟内返回 `ExportAborted`，且不留半包。
9. **中断即作废整包（②审补充）**：复制阶段任何中断（IO 异常 / 磁盘耗尽 / 用户取消）⇒ **删除本会话新建的时间戳子目录**，目录中不得残留"部分文件 + 无 manifest"的中间态（半包比不出包更危险：客户会以为拿到了完整的）。与第 8 条的区别：第 8 条约束返回时机，本条约束**磁盘终态**。红线自检不过属另一类——保留产物供取证并置 `qualified=False`。

## 7 进度回调契约

```python
ProgressCB = Callable[[int, int, str], None]   # done_bytes, total_bytes, phase
```
- `total_bytes` 在 Phase 4 开始时一次性给出（三件套字节和）；Phase 0–3 传 `total_bytes=0`（不定长阶段）。
- 调用频率 ≤ 20 次/秒（内核自我节流），GUI 侧无需再节流。
- 回调抛异常视为调用方错误：内核记录并继续（不得因 UI 异常中断导出）。

## 8 边界（不做的事）

- 不压缩、不上传、不分发（spec 非范围）；
- 不删除/清理 `export_dir` 内旧包（旧包是客户回退资产）；
- 不修改任何 `config/` 既有文件；
- 不启动/停止 daemon（只检测）。
