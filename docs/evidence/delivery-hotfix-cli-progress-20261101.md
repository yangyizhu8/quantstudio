# Hotfix 回执 · CLI `_Progress` 签名漂移（2026-11-01）

## 1 缺陷

用户实际执行 `qs-deliver verify` 抛
`TypeError: _Progress.__init__() got an unexpected keyword argument 'enabled'`。

## 2 根因（两环，不合并单因）

1. **签名漂移 × 调用点未同步**：`_Progress.__init__` 参数为 `quiet`，
   `cli.py:97`（export）写的是 `quiet=args.quiet`（对），
   **`cli.py:204`（verify）与 `cli.py:245`（import）仍是 `enabled=not args.quiet`（漂移）**。
2. **smoke 判据只覆盖 `--help`**：当时的 `test_cli_help_exit_zero` 只断言 `--help` 退出码 0，
   **handler 一行未执行** ⇒ 壳层调用点零执行覆盖，测试全绿却带病交付。

## 3 修复

- `cli.py:204` / `cli.py:245`：`_Progress(enabled=not args.quiet)` → **`_Progress(quiet=args.quiet)`**（与 97 行对齐）。
- `recover` / `set`：**无 progress 调用点**（grep 确认：`_Progress(` 全仓仅 3 处，97/204/245 之外无第四处）⇒ 无同款漂移。

## 4 新增测试（堵住"壳层调用点零执行覆盖"）

`tests/test_delivery_cli_commands.py`：

| 用例 | 判据 |
|---|---|
| `test_cli_all_six_subcommands_run_real` | **六个子命令各真实跑一次**（不 mock `_Progress`、不 mock handler）：`export`→0（六相位全跑）、`status`→0、`verify`→0（**原 TypeError 点**）、`import --dry-run`→0、`import` 真跑→0（**第二个 TypeError 点**）、`set`→0、`recover`（无 journal）→**7**（`RECOVERY_NEEDED`，仍是真实执行） |
| `test_cli_progress_signature_matches_call_sites` | 静态门：所有 `_Progress(` 调用点仅允许 `quiet=`，出现 `enabled` 即失败（防同类再漂移） |

**测试读数：143 passed / 0 failed / 58.19 s**（原 141 + 本次 2）

## 5 判据升级（审核方已定，落为后续特性验收下限）

> **CLI smoke 的最低判据 = 每个子命令都在真实路径上执行一次并断言退出码**；
> 仅 `--help` 退出码 0 **不再视为 smoke 通过**（本次缺陷正是该盲点的直接产物）。

## 6 纯增益与推送

- SC-013：**PASS**（36 保护路径 / 0 变化）
- 改动面：`quantstudio/delivery/cli.py`（2 行）、新增 `tests/test_delivery_cli_commands.py`、`knowledge/db/DB-2026-0010.md`、本回执
- 流程：铁律 03 写前快照 → 铁律 10 精确清单 add → 唯一临时 message 文件提交 → 双远程核对
