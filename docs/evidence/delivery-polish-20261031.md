# Polish 收口读数（003-delivery-bundle-import）

- 抓拍时间：2026-10-31 22:06（Asia/Shanghai）
- 本轮把 W4 的**唯一条件**（SC-009 GUI 半边 + D-29）补成实测，并完成 SC-008 豁免路径、R4 沉淀、README 文案

## 1 测试读数

**139 passed / 0 failed / 56.19s**（W4 的 136 + Polish 新增 3：SC-009 GUI 半边 1 + 薄壳行为面 1 + SC-008 豁免链 1）

## 2 SC-009 GUI 半边 + D-29（offscreen 实测，**非论证替代**）

测试：`tests/test_delivery_gui_offscreen.py`（`QT_QPA_PLATFORM=offscreen`）

| 环节 | 读数 |
|---|---|
| GUI 实例化（D-29） | `DeliveryTab(...)` 真实构造成功；`export_edit` / `export_btn` / `log_edit` 均存在 |
| 走完整导出 | 点击「开始导出」→ worker 跑满六相位（日志实测：`[PRECHECK] [ACQUIRE] [CHECKPOINT] [WAL_ZERO] [COPY] … [ATTEST]`） |
| 结果回传 | `qualified=True`；结果槽真实执行（日志含「✅ 导出完成」） |
| **GUI 产物 == CLI 产物** | `manifest.files` 的 `(bytes, sha256)` **逐文件全等** |
| **GUI 产物 == 内核产物** | 同上，逐文件全等 |
| 薄壳约束（行为面） | 页签 import 行无 `quantstudio.delivery`；worker 必须经内核 `export_bundle` |

> 说明：为消除 QThread 与事件循环的竞态，测试把 `start()` 替换为同步 `run()`；**接线顺序未变**（信号先连后跑），导出仍走真实内核与真实文件系统。

## 3 SC-008：跨复权世代豁免（**按 spec 走豁免，不硬凑绿**）

测试：`tests/test_delivery_sc008_exemption.py`

| 断言 | 读数 |
|---|---|
| 豁免条件成立 | 导入前 `c_future\|g_future` → 导入后 `c_pkg\|g_pkg`（**确实跨世代**） |
| 传导链前提 A：字节一致 | 三件文件 `sha256` 与包内 manifest 全等 |
| 传导链前提 B：结构一致 | 导入前后主库**全部表的列集逐表相等**（DDL 未变 ⇒ 取数契约不变） |
| **未断言** | 「回测结果零变化」——spec 明确跨世代时该判据不适用（合法重算）；**不把它当判据，也不宣称绿** |

> 结论口径：SC-008 在**跨世代场景**下按豁免成立；**不跨世代**的主判据（同参回测逐位一致）需要真实库窗口，本轮未做，如实申报。

## 4 R4 沉淀（六条候选全部落库，机检通过）

族 `db`（`knowledge/db`），每条经 `kb_hook.validate_record` 校验（recurrence=0 / status=open）：

| 编号 | 内容 | fix_layer |
|---|---|---|
| `DB-2026-0004` | fixture 必须经 `init_duckdb_schema` 造 `COMPLETE_2_1`（`DuckDBWriter` 初始化只造 `EMPTY_OR_NEW`） | artifact_rule |
| `DB-2026-0005` | SQLite WAL 库只读必须 `immutable=1`（否则 `-shm`/`-wal` 边车污染被检物） | write_path |
| `DB-2026-0006` | 表集口径：配置文件覆盖面 ≠ 语义真源（map 64 表与水位 24 行零交集 ⇒ 假 MISSING） | artifact_rule |
| `DB-2026-0007` | 水位 source 维度多义，取水位必须指明 source 口径 | constraint |
| `DB-2026-0008` | 内容指纹必须剔除派生时间量（`duration_sec`）+ 单测用字段全量审查 | artifact_rule |
| `DB-2026-0009` | 隔离数据根 ≠ 隔离锁（`QUANTSTUDIO_DATA_ROOT` + `QS_WRITE_LOCK_DIR` 两个变量都要设） | lint_guard |

## 5 `data/README.md` 文案（FR-032）

- 删除「解压到本目录」旧指引 → 改为指向 **GUI「数据交付」页签** 与 `qs-deliver` 三命令（verify / import / recover）。
- **体积句删数字**（原文「约 12 GB / 100 MB」→「GB 级单文件 / 远超 git 单文件上限」）。
- 新增「备份与回滚」节（`backup_import_<时间戳>/` + `RESTORE_README.txt` + 释放权在客户）。

## 6 SC-013

**PASS**（36 保护路径 / 0 变化 / 新增未跟踪 无）——Polish 全部改动落在 `tests/`、`knowledge/db/`、`data/README.md`，均不属保护路径。

## 7 偏离与未完成申报

- **未做（可选）**：D-28（ATTEST 的 `BinderException` 错误可读性）——时间优先给了必做项，如实申报。
- **未做（需裁定）**：SC-008 **不跨世代**主判据（同参回测逐位一致）——需要真实库窗口或可驱动的 K 线 fixture 子集；本轮没有硬凑。
- **委派**：本轮零委派（`zcode_code` 侧 Python 执行被拦，且三个测试件与内核契约强耦合，自写更快更稳）。
