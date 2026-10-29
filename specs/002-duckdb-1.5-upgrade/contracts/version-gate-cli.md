# 契约：版本判定 CLI（唯一判定源对外接口）

**Date**: 2026-10-29 | **Version**: 1.1（2026-10-29 实施期形态调整：判定源下沉为单文件） | **Owner**: 执行会话 | **消费方**：启动门禁（GUI / daemon）、`check_wal.ps1`、`check_wal.sh`、pytest 契约、客户自检

## 1. 唯一判定源

**实现文件**：`quantstudio/pipeline/duckdb_version_policy.py` —— **无包内 import** 的单文件模块，承载全部版本规则（`ALLOWED_RANGES` / `parse_version` / `is_allowed` / `probe_version` / CLI）。

**放行集合**：`{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`（`ALLOWED_RANGES`，唯一真相）。

**两个等价入口**（同一实现、同一判定结果、同一退出码）：

| 入口 | 调用 | 适用 |
|---|---|---|
| 单文件直调 | `python quantstudio/pipeline/duckdb_version_policy.py --check [版本串]` | 双平台运维脚本（**不依赖可导入的项目包**） |
| 模块入口 | `python -m quantstudio.pipeline.duckdb_version_gate --check [版本串]` | 客户自检、进程内工具（转发同一 CLI） |

> 为什么要有单文件形态：`scripts/check_wal_regression.sh` 把 `check_wal.sh` 复制到临时目录运行，
> 此时项目包不可导入 ⇒ 纯 `-m` 形态必然 fail-closed ⇒ 回归门 T2–T9 连锁变红。
> 判定源下沉为无包内依赖的单文件后，两条路径共用同一份规则，**单源性质不变**。

## 2. 调用形态

```bash
# 省略版本串 ⇒ 自动探测当前解释器的 duckdb
python quantstudio/pipeline/duckdb_version_policy.py --check
# 指定版本串 ⇒ 只判定该版本（不 import duckdb）
python quantstudio/pipeline/duckdb_version_policy.py --check 1.5.6
# 机器可读
python quantstudio/pipeline/duckdb_version_policy.py --json --check 1.5.6
# 人类可读的集合描述
python quantstudio/pipeline/duckdb_version_policy.py --describe
```

- **不得**要求任何环境变量；`QS_DUCKDB_VERSION_GATE` 只影响**启动门禁**的放行行为，不影响判定本身。

## 3. 输出契约（stdout，单行）

| 场景 | 版本串形态 | stdout | 退出码 |
|---|---|---|---|
| 命中放行集合 | strict（恰三段整数） | `PASS <version>` | **0** |
| 严格三段但不在集合 | strict | `FAIL <version>` | **1** |
| **非严格形态**（两段 / 带预发布或构建后缀，如 `1.5`、`2.1`、`1.5.6.dev0`） | short / suffixed | `FAIL <version>` | **1** |
| 版本串不可解析（如 `unknown`、`1.5.6rc1`、空串） | unparsable | `ERROR unparsable` | **2** |
| 依赖不可导入（自动探测失败） | — | `ERROR import_failed` | **2** |

- 版本串原样回显，便于客户比对。
- **口径分层（2026-10-29 H1 裁定，严格三段 + fail-closed 优先）**：只有**恰三段整数**才是可参与集合判定的形态；
  带预发布/构建后缀的版本（`1.5.6.dev0`）与两段式（`1.5`、`2.1`）**可识别但不合规** ⇒ `FAIL`/退出码 1；
  完全无法解析 ⇒ `ERROR`/退出码 2。
- `--json`：`{"allowed": bool, "version": str|null, "parsed": [int,int,int]|null, "reason": "in_range|out_of_range|non_strict_shape|unparsable|import_failed"}`
  （`non_strict_shape` 另带 `shape: short|suffixed`）。
- **stderr 不承载判定结果**（只放诊断/详情），消费方一律以退出码 + stdout 判定。

## 4. 消费方约定

| 消费方 | 用法 | 处理 |
|---|---|---|
| `check_wal.ps1` | `& $Py <Root>\quantstudio\pipeline\duckdb_version_policy.py --check $ver`，取 `$LASTEXITCODE` | `0` 继续；非 0 → 报「不在项目放行集合」并中止（fail-closed） |
| `check_wal.sh` | `"$PY" quantstudio/pipeline/duckdb_version_policy.py --check "$VER"`，取 `$?` | 同上（**保留 `${VAR}` 花括号纪律**） |
| GUI / daemon | `import is_allowed`（进程内） | 不放行 ⇒ `SystemExit(3)`；逃生阀语义不变 |
| pytest 契约 | `from …duckdb_version_policy import is_allowed, ALLOWED_RANGES` | 驱动断言；**不得自持第二份规则**（以 `is` 同一性机检） |
| 客户自检 | 运行模块入口并展示 stdout | 通知里写明「看到 PASS 即正常」 |

## 5. 稳定性与兼容

- **破坏性变更**：退出码含义、stdout 首词（`PASS`/`FAIL`/`ERROR`）、`--check` 名、`ALLOWED_RANGES` 语义。
- **非破坏性变更**：新增 flag、新增 `reason` 枚举值（消费方须容忍未知 reason 并按失败处理）。
- 新增任何放行点**只能**改 `ALLOWED_RANGES`；消费方不得写特例。

## 6. 验收（可机检）

1. 三版本（1.4.5 / 1.5.6 / 2.0.1）执行 CLI ⇒ 退出码 `0 / 0 / 1`，stdout 首词对应；
2. 六边界（1.4.4 / 1.4.9 / 1.5.0 / 1.5.5 / 1.6.0 / 2.1）⇒ 退出码 `1 / 0 / 1 / 1 / 1 / 1`；
3. 形态分层：带预发布/构建后缀（`1.5.6.dev0`）与两段式（`1.5`、`2.1`）⇒ 退出码 `1` 且 stdout 首词 `FAIL `；
   不可解析（如 `unknown`、`1.5.6rc1`）⇒ 退出码 `2` 且 stdout 以 `ERROR ` 开头；
4. 两个入口对同一版本串给出**相同**结果与退出码；
5. 门禁模块与判定源的 `ALLOWED_RANGES` / `is_allowed` / `parse_version` 为**同一对象**（`is` 断言）；
6. `check_wal.ps1` / `check_wal.sh` 中版本正则**零残留**（grep 零命中）；
7. 回归门 `bash scripts/check_wal_regression.sh` ⇒ `FAIL=0`（含版本门双向往用例）。

**实测（2026-10-29）**：第 1–7 项全部通过（退出码矩阵见 `docs/evidence/duckdb-version-gate-matrix-20261029.md`；
pytest 27 passed；回归门 PASS=13 FAIL=0）。
**2026-10-30 H1 回修后**：形态分层用例新增 9 条（strict 0/1、非严格形态 1、不可解析 2），实测见
`docs/evidence/duckdb-version-gate-matrix-20261029.md` 增量节与 `tests/test_duckdb_version_gate.py`。
