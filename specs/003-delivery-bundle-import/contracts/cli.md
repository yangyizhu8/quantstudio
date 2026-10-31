# 契约：CLI `qs-deliver`

**模块**: `quantstudio/delivery/cli.py` | **入口**: `pyproject.toml` → `[project.scripts] qs-deliver = "quantstudio.delivery.cli:main"` | **spec**: FR-026 / FR-027 / FR-028 | **plan**: §1 / §4.4

---

## 1 子命令

```
qs-deliver export   --export-dir PATH [--config-dir PATH] [--accept-sync-dir]
                    [--verify-hashes] [--json] [--quiet]
qs-deliver verify   --import-dir PATH [--config-dir PATH] [--json]
qs-deliver import   --import-dir PATH [--dry-run] [--yes] [--confirm-sync-dir]
                    [--config-dir PATH] [--json]
qs-deliver recover  --action continue|rollback [--config-dir PATH] [--json]
qs-deliver status   [--json]
qs-deliver set      --export-dir PATH | --import-dir PATH    # 写 delivery_config.json（原子）
```

### 1.1 各子命令语义

| 子命令 | 内核调用 | 说明 |
|---|---|---|
| `export` | `export_bundle()` | `--accept-sync-dir` 对应 `accept_sync_dir=True`（同步盘显式确认，FR-003②）；`--verify-hashes` 打开二次全量哈希（R3） |
| `verify` | `verify_bundle()` | **只读**，不改任何文件；`--dry-run` 语义与本命令相同（保留别名便于客户习惯） |
| `import` | `import_bundle()` | `--dry-run` ⇒ 只到 Phase 4；默认**需要交互确认**（打印将要发生的动作摘要 + `[y/N]`），`--yes` 跳过（自动化用） |
| `recover` | `recover_journal()` | 有残留 journal 时才可用；无 journal ⇒ 退出码 7 |
| `status` | `detect_pending_journal()` + `config.py` | 打印：`DATA_ROOT`、两项配置、残留 journal（相位/已完成项数）、daemon 状态一行 |
| `set` | `config.save()` | 只改 `config/delivery_config.json`（原子写） |

### 1.2 参数约束

- 路径参数一律**绝对路径或相对 CWD 的可解析路径**；解析后做三路径互不包含校验（FR-035）。
- `--config-dir` 默认 `config/profiles/mcp_only`（与 `questdb_table_map.json` / `qfq_aux_paths.json` / `collector_tasks.json` 同目录）。
- `DATA_ROOT` **不可传参**（FR-012 的唯一真相源）：CLI 也只从 `_paths.DATA_ROOT` 取，`--data-root` 参数**故意不提供**（防止"备份了 A、覆盖了 B"）。

## 2 退出码表（稳定契约，逐项有测试）

| 码 | 含义 | 触发函数 |
|---|---|---|
| `0` | 成功 | 全部 |
| `2` | 用法/参数错误（缺参、未知子命令、路径非法） | argparse / 参数校验 |
| `3` | 前置检查拒绝（fail-closed，零改动） | `export_bundle` 的 `ExportRefused`、`import_bundle` 的 `ImportRefused` |
| `4` | 包问题（第①类） | `verify`/`import` 的 `failure_kind=="1_package"` |
| `5` | 配置不匹配（第③类） | `failure_kind=="3_config"` |
| `6` | 程序版本不匹配（第②类）或包格式代差（第④类） | `failure_kind in {"2_program_version","4_manifest_version"}` |
| `7` | 存在残留 journal，需先 `recover` | `RecoveryRequiredError`；`recover` 在无 journal 时也返回 7 |
| `8` | 内部错误（未预期异常） | 兜底 `except` |
| `9` | 导入失败但**已成功回滚** | `ImportResult.ok=False, rollback_ok=True` |
| `10` | 导入失败且**回滚失败**（需人工介入） | `rollback_ok=False` |

**约定**：`export` 的红线自检未过（`qualified=False`）返回 `9`（产物保留但不可交付）；`export` 自身 IO 失败返回 `8`。
**Smoke（D-3 裁定）**：`qs-deliver --help` 与各子命令 `--help` MUST 返回 `0`——W4 DoD 以此为入口可用性证据（script entry 写错模块名时 diff 干净但命令跑不起来，diff 审计看不见这类问题）。

## 3 输出格式

### 3.1 人读（默认）
```
[delivery] export  → /mnt/export/20261031_031500
  quantstudio.db        58.03 GB   sha256=ab12…  ok
  qfq_aux.db            10.81 GB   sha256=cd34…  ok
  qfq_aux_mcp_gen1.db   52.00 KB   sha256=ef56…  ok
  同刻一致: WAL 已归零（3/3）    红线自检: 通过
  耗时 412.5 s   产出目录: /mnt/export/20261031_031500
```

### 3.2 机读（`--json`）
- 结构 = 对应返回对象的 `dataclasses.asdict()`（`Path` 转 `str`，`datetime` 转 ISO8601），顶层再包 `{"ok": bool, "exit_code": int, "subcommand": str, "result": {...}}`。
- **stdout 只输出 JSON**（进度与告警走 stderr），保证 `qs-deliver verify --json | jq` 可用。
- 禁止在 JSON 中出现绝对路径以外的不稳定字段（时间戳允许，SC-009 比对时剔除 `generated_at` / `export.*` 时间字段）。

## 4 进度输出（stderr）

- 默认：每 2 s 一行 `[phase] 12.3 GB / 68.9 GB (17.8%) 210 MB/s 剩余 ~4m`
- `--quiet`：只在相位切换时一行。
- **取消**：`Ctrl+C`（`KeyboardInterrupt`）⇒ 置 `cancel` ⇒ 内核按 Phase 语义回滚/清理（见 `import_kernel.md §5`），并以 `130` 退出（沿用 shell 惯例；**本码不在上表业务码内**，文档明示）。

## 5 与 GUI 的对等义务（FR-026/028）

| 能力 | CLI | GUI | 对等判据 |
|---|---|---|---|
| 导出 | `export --export-dir` | 页签「交付包导出」 | SC-009：同输入产物逐位一致 |
| 校验 | `verify --import-dir` | 页签「导入校验」（先校验后导入，同一内核） | 校验结论与逐表状态一致 |
| 导入（含 dry-run） | `import [--dry-run]` | 页签按钮 + 确认框 | `ImportResult` 逐字段一致（时间字段除外） |
| 恢复 | `recover --action` | 启动时/进页签时二选一弹窗 | 同 `RecoveryResult` |
| 状态 | `status` | 页签顶部状态区 | 同 `detect_pending_journal` + 配置读数 |
| 配置 | `set --export-dir/--import-dir` | 目录选择框 | 同一 `config.save()`（原子写） |

**约束**：CLI 与 GUI 都**不得**在壳里做业务判断（如自己判定模式、自己算状态），只能调用内核 + 展示 `readings`。

## 6 禁止项

- 不提供 `--force` 这类绕过前置检查的开关（防止把"零改动"保障关掉）；
- 不提供 `--data-root`；
- 不在 CLI 里实现任何复制/校验逻辑（只调内核）。
