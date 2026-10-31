# W4 收口读数（003-delivery-bundle-import · 壳与双端对等）

- 抓拍时间：2026-10-31 21:58（Asia/Shanghai）
- 红线：W4 只做壳与注册，测试全在 `tmp_path`/子进程 fixture 内，**未触碰生产 `data/`**

## 1 测试读数

**测试: 136 passed / 0 failed / 45.86 s**（W3 的 130 + W4 新增 6：对等 2 + 配置健壮 3 + CLI smoke 1）
- 新增文件：`tests/test_delivery_parity.py`

## 2 SC-009 双端对等（哈希门）

- **CLI 子进程导出 vs 内核直调导出**，同一 `DATA_ROOT` + 同一 `--config-dir`：
  两次产物 `manifest.files` 的 `(bytes, sha256)` **逐文件全等** ✅
- **薄壳无第二套实现**（静态门）：`DeliveryExportWorker` 必须出现 `export_bundle` 调用；
  worker 段内**不得**出现 manifest 拼装；`delivery_tab.py` 的 import 行**不得**出现 `delivery`（只能经 worker）。
- 读数：`test_sc009_cli_and_kernel_produce_identical_hashes`、`test_sc009_gui_shell_uses_same_kernel` 均通过。

## 3 SC-010 配置健壮性

| 场景 | 读数 |
|---|---|
| 半截 JSON（`{"export_dir": "D:/x", `） | 容错加载：**给 warning**、路径退化为空、不崩 |
| 多余键 `evil` + 非字符串 `import_dir: 123` | 多余键被丢弃；非字符串退化为空（不使用） |
| set 往返 + 原子写 | 写后可读回原值；**无 `*.tmp*` 残留** |

## 4 CLI smoke

```
python -m quantstudio.delivery.cli --help
```
**exit 0**，列出 **export / status / verify / import / recover / set** 六个子命令；
`cli.main(["--help"])` 在 pytest 内同样返回 **0**（`test_cli_help_exit_zero`）。

## 5 最小 append 申报（三文件 + .gitignore）

`git diff --numstat`：

| 文件 | 增 | 删 | 归属 |
|---|---|---|---|
| `quantstudio/gui/main_window.py` | **+8** | **−0** | 本会话 **+4**（`("数据交付", …)` 1 行 + `elif row == 11` 块 3 行）；**他线在途 +4**（`("QMT 转换部署", …)` 1 行 + `elif row == 10` 块 3 行） |
| `quantstudio/gui/workers.py` | **+161** | **−0** | 本会话（**委派 ZCode**）：`_fmt_bytes` + `_DeliveryProgress` + `DeliveryExportWorker` / `DeliveryVerifyWorker` / `DeliveryImportWorker` |
| `pyproject.toml` | **+1** | **−0** | 本会话：`qs-deliver = "quantstudio.delivery.cli:main"` |
| `.gitignore` | **+6** | **−0** | W0 已落（`data/backup_import_*/`、`data/import_journal.json` 等） |

**只增不改声明**：四文件删除行数均为 **0**；`main_window.py` 的 **−0** 与他线在途 4 行同时成立，
本会话新增行与他线在途行在 `git diff -U0` 中分属**不同 hunk**（`_NAV_ITEMS` 段与他线行同 hunk 相邻，
但新增行内容可逐行区分：`数据交付` / `row == 11` / `DeliveryTab` 三处特征串仅本会话引入）。

## 6 GUI 页签（薄壳）

- 新文件 `quantstudio/gui/tabs/delivery_tab.py`（232 行）：`PageHeader` + 三张 `GroupHeaderCardWidget`
  （导出 / 只读校验 / 导入 + dry-run 复选框）+ 进度行 + 只读日志（`setMaximumBlockCount(2000)`）。
- 导航注册：`_NAV_ITEMS` 追加 `("数据交付", FluentIcon.SHARE, TOP)`，`_create_tab` 追加 `elif row == 11`。
- **未启动 GUI**（无显示环境），以 AST + 静态门覆盖：三文件 AST 全通过、无 BOM/CRLF。

## 7 SC-013 基线相对审计

**SC-013: 与基线 v2 一致（PASS，36 保护路径 / 0 变化 / 新增未跟踪 无）**
（`main_window.py` / `workers.py` / `pyproject.toml` / `.gitignore` 均属 `APPEND_ONLY`，D-6 只核不判，读数见 §5）

## 8 委派申报

- **委派 1 次**：`zcode_code`（GLM）生成 `workers.py` 的三个 worker + `delivery_tab.py` 页签；
  按任务书约束（只追加 / 不得改其他文件 / 不写测试）。
- **ZCode 未能自验**（其环境 Python 代码执行被拦），**由本会话补验**：AST 解析通过、无 BOM/CRLF、
  `git diff --numstat` = `161 0`（纯追加）、三类齐全、页签 import 仅 `..workers` / `..skin`。
- **未委派部分（自写并申报）**：`main_window.py` 注册段（他线在途文件，按裁定自写）、`pyproject.toml` 一行、
  CLI 四子命令（`verify`/`import`/`recover`/`set`，与内核契约强耦合）、全部 W4 测试。

## 9 波次门禁字段

测试: 136 passed / 0 failed（45.86s；W4 新增 6 项，含对等 2 / 配置健壮 3 / CLI smoke 1）
SC-009: CLI 子进程导出与内核直调导出，同一 DATA_ROOT + 同一 config-dir ⇒ manifest.files 逐文件 (bytes, sha256) 全等；薄壳静态门通过（worker 调 export_bundle、页签不 import 内核）
SC-010: 半截 JSON 容错加载给 warning 且路径退化为空；多余键丢弃；非字符串退化；set 往返原子写无 *.tmp 残留
CLI smoke: `python -m quantstudio.delivery.cli --help` exit 0，列出 export/status/verify/import/recover/set 六个子命令；pytest 内 cli.main(["--help"]) 返回 0
SC-013: 与基线 v2 一致（PASS，36 保护路径 0 变化，新增未跟踪 无）；四文件 append 均 −0（只增不改）
