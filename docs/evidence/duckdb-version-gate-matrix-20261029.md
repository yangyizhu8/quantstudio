# T015 门禁矩阵与四面一致性证据（duckdb 版本放行集合落地）

- **日期**：2026-10-29 16:36（Asia/Shanghai）
- **归属**：duckdb 数据库升级执行会话（图节点⑲）｜spec 002-duckdb-1.5-upgrade · FR-003/FR-004/FR-009 · design V-1/V-3
- **放行集合**：`{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`（唯一判定源 `quantstudio/pipeline/duckdb_version_policy.py`）

## 一、判定源 CLI 退出码矩阵

命令：`python quantstudio/pipeline/duckdb_version_policy.py --check <版本串>`

| 版本 | stdout | 退出码 | 期望（契约 §5.2） | 符合 |
|---|---|---|---|---|
| 1.4.5 | `PASS 1.4.5` | 0 | 0（集合下界含端点） | ✅ |
| 1.4.9 | `PASS 1.4.9` | 0 | 0 | ✅ |
| 1.5.6 | `PASS 1.5.6` | 0 | 0（精确放行点） | ✅ |
| 1.4.4 | `FAIL 1.4.4` | 1 | 1（下界外） | ✅ |
| 1.5.0 | `FAIL 1.5.0` | 1 | 1 | ✅ |
| 1.5.5 | `FAIL 1.5.5` | 1 | 1（事故版本） | ✅ |
| 1.6.0 | `FAIL 1.6.0` | 1 | 1 | ✅ |
| 2.0.1 | `FAIL 2.0.1` | 1 | 1（第二跳，不在首跳集合） | ✅ |
| 2.1 | `FAIL 2.1` | 1 | 1 | ✅ |
| 1.5（两段式） | `FAIL 1.5` | 1 | 1（补 0 ⇒ (1,5,0) 不在集合） | ✅ |
| unknown | `ERROR unparsable` | **2** | 2（不可解析 ⇒ fail-closed） | ✅ |
| （自动探测当前解释器） | `PASS 1.4.5` | 0 | — | ✅ |

`--json` 形态：`{"allowed": true, "version": "1.5.6", "parsed": [1,5,6], "reason": "in_range"}`。
`--describe` 输出：`1.4.5 ≤ x < 1.5 或 x == 1.5.6`。

## 二、四面一致性（design V-1）

| 面 | 实现 | 读数 |
|---|---|---|
| **门禁面**（进程内） | `duckdb_version_gate.require_duckdb_version()` → `policy.is_allowed()` | 9 个契约点断言通过（放行不抛、拒绝 `SystemExit(3)`） |
| **双脚本面** | `check_wal.sh:45-52`、`check_wal.ps1:67-77` 调用判定源单文件，取退出码 | 回归门 T1（1.5.5 拒）/ T1b（1.5.6 放行）双向往用例 PASS |
| **契约面** | `tests/test_duckdb_version_gate.py` import 判定源 | **27 passed**（含 CLI↔进程内逐点比对 9 项） |
| **声明面** | `requirements.txt:1`、`pyproject.toml:20`、`:50` | 三处均为 `duckdb==1.5.6`（单版本精确锁定，注释承载集合）⇒ **落于集合内** ✅ |

**单源同一性机检**：`gate.ALLOWED_RANGES is policy.ALLOWED_RANGES`、`gate.is_allowed is policy.is_allowed`、
`gate.parse_version is policy.parse_version` —— 契约用例 C6 断言通过 ⇒ **不存在第二份规则**。

**两脚本零正则残留**：`check_wal.ps1` / `check_wal.sh` 中 `^1\.4\.` / `1.4.*` 判定**零命中**，
仅保留对判定源路径的引用。

## 三、WAL 回归门（铁律 23，design V-3）

```
bash scripts/check_wal_regression.sh   （Git Bash，PY=python）
PASS=13 FAIL=0 SKIP=0        exit=0
```

用例：T1 版本门拒（1.5.5）｜T1b 版本门放（1.5.6）｜T2 健康库早退｜T3 合法 WAL 残留｜T4 外来 WAL 失败｜
T5 --force 收敛 + WAL 归零｜T6 --force 无 WAL｜T7 未知参数｜T8 --recover-stale-wal（含 orphan 闭环）｜T9 缺库拒绝。

> 回归门同步项：伪造 shim 由「仅 1.5.6」拆为 **1.5.5（集合外）/ 1.5.6（集合内）** 两份；
> 判定源文件随 `check_wal.sh` 一并复制到临时工作目录（因脚本以相对路径单文件直调它）。

## 四、未在本轮闭环的项（如实申报）

| 项 | 原因 | 闭环时点 |
|---|---|---|
| `check_wal.ps1` 端到端跑通 | 该脚本第 4 步起会**打开主库并可能执行 CHECKPOINT**；daemon 在跑时不得触发（本机 `bash`/WSL 不可用，只能用 PowerShell） | **停服窗内**（11-07/11-08），与 S1–S6 同批 |
| 三入口真实进程触发（GUI / daemon / GUI 子进程） | 需真实进程形态，且 daemon 在跑时不可打断 | 窗口内（S6） |
| F4 守卫 `--check` 取 `OK` 读数 | 当前 daemon 占用主库 → 脚本按设计 SKIP（exit 3） | 窗口内或副本上 |
| `AGENTS.md` / `iron-rules` 旧口径落码 | 跨域治理，须随②审呈批 | ②审通过后 |

## 五、落地文件清单（本轮改动）

| 文件 | 动作 |
|---|---|
| `quantstudio/pipeline/duckdb_version_policy.py` | **新增**（唯一判定源，单文件可直调） |
| `quantstudio/pipeline/duckdb_version_gate.py` | 改写为薄封装（`REQUIRED_SERIES` 删除，文案改集合描述，保留 `GATE_ENV` 逃生阀与 `SystemExit(3)`） |
| `requirements.txt` | `:1` → `duckdb==1.5.6`；`:2` 缺陷说明更正（1.5.x 全线含 1.5.6 未修复，仅静默→FATAL） |
| `pyproject.toml` | `:20`、`:50` → `duckdb==1.5.6` + 集合注释 |
| `check_wal.ps1` / `check_wal.sh` | 版本门改调判定源（删除本地正则） |
| `scripts/check_wal_regression.sh` | 版本门双向往用例 + 判定源随行复制 |
| `tests/test_duckdb_version_gate.py` | 契约重写：9 契约点 + 逃生阀 + 导入失败 + 不可解析 + **单源同一性** + CLI↔进程内比对 |


## 六、增量（2026-10-30 · H1 回修：严格三段口径 + 四层形态判定）

> H1 裁定（总调度 2026-10-29）：**只有恰三段整数**才是可参与集合判定的形态；带预发布/构建后缀与两段式**可识别但不合规**（`FAIL`/退出码 1）；完全无法解析 ⇒ `ERROR`/退出码 2。

### 6.1 形态分层矩阵（实测）

| 版本串 | 形态 | stdout | 退出码 | reason（--json） |
|---|---|---|---|---|
| `1.4.5` | strict | `PASS 1.4.5` | 0 | in_range |
| `1.5.6` | strict | `PASS 1.5.6` | 0 | in_range |
| `1.5.6.dev0` | suffixed | `FAIL 1.5.6.dev0` | **1** | non_strict_shape |
| `1.5.6.post1` | suffixed | `FAIL 1.5.6.post1` | **1** | non_strict_shape |
| `1.5` | short | `FAIL 1.5` | 1 | non_strict_shape |
| `2.1` | short | `FAIL 2.1` | 1 | non_strict_shape |
| `1.5.6rc1` | unparsable | `ERROR unparsable` | **2** | unparsable |
| `unknown` | unparsable | `ERROR unparsable` | 2 | unparsable |
| `not-a-version` | unparsable | `ERROR unparsable` | 2 | unparsable |

`--json` 样例：`{"allowed": false, "version": "1.5.6.dev0", "parsed": null, "reason": "non_strict_shape", "shape": "suffixed"}`

### 6.2 回修后复跑读数

| 门 | 读数 |
|---|---|
| 契约 pytest（`tests/test_duckdb_version_gate.py`） | **38 passed**（9 契约点 + 5 类判据 + 单源同一性 + **形态分层 9 例** + CLI↔进程内比对） |
| PT-01a pytest（`tests/test_passthrough_pk_preservation.py`） | **7 passed** |
| 两文件合计 | **45 passed** |
| 三判定面机检（`gate_matrix_check.py`） | **PASS**（9 版本串逐项一致 + 声明面 pin 落于集合内 + 单源同一性 True） |
| 三入口替身触发（`version_gate_entrypoint_check.py`） | **PASS**（30 组合：3 入口 × 3 放行 + 6 拒绝 + 逃生阀） |
| WAL 回归门 | PASS=13 FAIL=0（上一轮读数，本轮未改脚本） |

### 6.3 同步落点（口径四处一致）

`spec.md`（Edge Case / Assumptions / FR-002）· `contracts/version-gate-cli.md`（§2 调用形态 / §3 输出契约表 + reason 枚举 / §6 验收项）·
`tests/test_duckdb_version_gate.py`（C5 扩充 + 新增 `test_cli_shape_layers`）· `quantstudio/pipeline/duckdb_version_policy.py`（`classify()` + 严格 `parse_version()` + CLI 分层）。
