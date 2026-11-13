# V9 全库回归副作用审计（跑 398 前的安全前置）

> **依据**：R12 ③「不按 6 文件耗时线性推 398 文件时长。**先审测试/conftest 的 coexist 和生命周期副作用，用隔离数据根**；无安全资源窗则改期。」
> **性质**：只读审计。**未改任何代码、未跑任何测试**。
> **实测时钟**：2026-11-13 01:43–01:50（Asia/Shanghai）
> **基线**：HEAD `9b26291`（审计开始时）；全库 `tests/**/test_*.py` = **398**

---

## 一、结论先行（三条）

| # | 结论 |
|---|---|
| 1 | **conftest 只隔离写锁目录，未隔离数据根**——`QS_WRITE_LOCK_DIR` → tmp；**无** `QS_DB_PATH` 类隔离 |
| 2 | **全库存在 5 处真实副作用面**：3 处真起 daemon 子进程 + 2 文件硬编码绝对路径直连生产库 |
| 3 | **⇒ 直接裸跑 398 不安全**；且**耗时不可由 6 文件线性外推**（副作用面需逐个处置，非匀速） |

---

## 二、conftest.py 隔离现状（唯一 session 级 fixture）

`tests/conftest.py` 共 213 行，**仅 1 个 session 级 autouse fixture**：

```python
@pytest.fixture(scope="session", autouse=True)
def _isolate_write_lock_dir(tmp_path_factory):
    """L196-198 自述：
    处理：本会话把 QS_WRITE_LOCK_DIR 指向会话级临时目录，使**所有**测试文件
         （不只 tests/test_snapshot_lock.py）对生产锁路径零接触。
    生产行为零影响：该变量默认不设，snapshot_lock.lock_path() 解析路径与接入前逐位一致。
    """
    d = tmp_path_factory.mktemp("write_lock_dir")
    _os.environ["QS_WRITE_LOCK_DIR"] = str(d)
    _os.environ.setdefault("QS_WRITE_LOCK_AUDIT_LOG", str(d / "write_lock_reclaim.log"))
```

**L193-195 自述的风险（原文摘录）**：
> 「本机存在常驻采集 daemon（`--mode forever`，旧版代码，无死亡自愈）。测试若使用**生产锁路径** `<repo>/data/snapshots/.write_lock`，会与 daemon 抢锁；个别测试的锁卫生 fixture 更会**直接 unlink 该文件**——可能删掉 daemon 正在持有的锁（**双写风险**）。」

**conftest 全部 env 设置点（实测 grep，共 4 行）**：只有 `QS_WRITE_LOCK_DIR` / `QS_WRITE_LOCK_AUDIT_LOG`。
**⇒ 无任何数据库路径隔离**（无 `QS_DB_PATH`、无 `chdir`、无 monkeypatch 数据根）。

**conftest 前半部分**（L1-60）是 fixture 工具函数（`ms()` / `minute_row()` / `daily_row()`），用于**构造临时 DuckDB**——这是「每个测试自己建 tmp 库」的模式，不是全局隔离。

---

## 三、62 个「命中危险模式」文件的**逐类定性**（命中数≠判据，逐行读上下文）

### 3.1 无害类（绝大多数）

| 文件 | 命中内容 | 定性 |
|---|---|---|
| `test_db_lock_errors.py` L23/27/29 | `'IO Error: Cannot open file "data/quantstudio.db": 另一个程序正在使用此文件…'` | **纯错误字符串字面量**（该测试解析 DuckDB 错误消息）|
| `test_backtest_window_protocol.py` L146 | `from ...daemon_lifecycle import collector_run_lock_path` | **纯函数 import** |
| `test_delivery_pure_gain.py` L121 | `"quantstudio.pipeline.daemon"` | **AST 扫描用的禁入黑名单常量** |
| `test_delivery_pure_gain.py` L170 | `subprocess.run(["git","check-ignore",...])` | **git 查询**，非起 daemon |
| `test_duckdb_data_access_caching.py` L9 | 文档字符串「不依赖生产大库：使用自建 mini DuckDB 副本」 | **自述已隔离** |
| `test_daemon_lifecycle.py` / `test_daemon_qfq_integration.py` | `import ... daemon`、`DaemonLifecycle` | **进程内 import + 类引用**（未检出 `DaemonLifecycle(...).start()` 实调用）|

### 3.2 ★ 真实副作用类（5 处，必须处置）

**A. 真起 daemon 子进程（3 处）**

| 文件 | 行 | 代码 |
|---|---|---|
| `test_daemon_gui_start_paths.py` | **L118** | `p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, ...)` |
| `test_daemon_gui_start_paths.py` | **L150** | 同上（第二个路径分支）|
| `test_daemon_once_exit_contract.py` | **L108** | `r = subprocess.run(...)` |

**判定方法**：`subprocess.*` 调用且 ±3 行内出现 `quantstudio.pipeline.daemon` / `daemon.py` 模块名（严格排除注释行）。
**风险**：这些测试会**真的拉起 daemon 进程**。若它指向生产库 ⇒ 与 PID 53244 抢锁；若它没带隔离 env ⇒ 可能写生产库。

**B. 硬编码绝对路径直连生产库（2 文件）**

| 文件 | 行 | 代码 |
|---|---|---|
| `test_fund_contract_wiring_matrix.py` | **L18** | `DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"` |
| `test_fund_statement_acceptance.py` | **L10** | 同上 |

**风险**：**绝对路径写死**，无论 cwd 与 env 如何，都会打开生产库文件。
**当前后果**：daemon PID 53244 持锁期间 ⇒ 这两文件**必然失败**（非代码缺陷，是锁冲突）。
**⇒ 这是 V9 结果解释的关键**：若全库跑出这两处 FAIL，**不能归因于 settle 改动**。

---

## 四、为什么「不按 6 文件线推 398」

| 我的旧错误 | 实情 |
|---|---|
| 「6 文件 ≈3.5 分钟 ⇒ 398 ≈3–4 小时」 | 分母含**副作用面需逐个处置**（3 处 daemon 起停、2 处生产库直连），处置成本非线性 |
| 假设「所有测试都是纯单元」 | 62/398 触及 proddb/daemon/coexist/flag；其中 5 处真实副作用 |
| 假设「同一个窗能跑完」 | 起 daemon 的测试会**改变环境状态**，后续测试读数受污染 |

**⇒ 正确做法**：**先隔离再跑**，且 `nodeid` 级对账（R12 ①要求）必须记录「因副作用未跑」而不是记 FAIL。

---

## 五、隔离方案建议（供裁定，本轮不实施）

### 方案甲：环境变量级数据根隔离（改动最小）

在 `conftest.py` 增一个 session autouse fixture（与 `_isolate_write_lock_dir` 并列）：

```python
@pytest.fixture(scope="session", autouse=True)
def _isolate_data_root(tmp_path_factory):
    d = tmp_path_factory.mktemp("data_root")
    _os.environ.setdefault("QS_DATA_DIR", str(d))       # 需代码侧支持该变量
    _os.environ.setdefault("QS_DB_PATH", str(d / "quantstudio.db"))
```

**前提**：需确认生产代码是否读 `QS_DB_PATH`/`QS_DATA_DIR`（**未核实**）。
**不解决**：`test_fund_*` 的**硬编码绝对路径**（env 覆盖不掉字面量）。

### 方案乙：副作用文件白名单排除 + 单独处置

| 处置 | 对象 |
|---|---|
| 从全库回归**排除**并单独记为「环境依赖·未跑」 | `test_fund_contract_wiring_matrix` / `test_fund_statement_acceptance`（直连生产库）|
| 在**无 daemon 窗**单独跑 | `test_daemon_gui_start_paths` / `test_daemon_once_exit_contract`（真起 daemon）|
| 正常跑（conftest 已隔离写锁）| `test_snapshot_lock` |

**优点**：不改生产代码、不改测试代码，只调运行编排。
**缺点**：留 4 个文件不进全库分母 ⇒ **必须在报告里显式标注「N 未跑（环境依赖）」，不得计入 PASS**。

### 方案丙：专用隔离副本仓

复制仓库到临时目录 + 临时库，全库在其中跑。
**缺点**：398 文件的 import 依赖与路径假设可能失效；成本最高。

**我方倾向**：**方案乙**（不改任何代码，纯编排层处置），并在 V9 报告里对 4 个文件标注「环境依赖·未跑」而非 FAIL。
**理由**：符合「纯增益」铁律（零代码改动），且 R12 ① 已允许「未运行」作为合法状态之一。

---

## 六、本轮边界自证

| 项 | 状态 |
|---|---|
| 改代码 | ❌ 未改（纯只读审计 + 本文档）|
| 跑测试 | ❌ 未跑 |
| 停/启 daemon | ❌ 未做（PID 53244 仅观察）|
| 清 flag | ❌ 未做 |
| 数据根隔离 | ⏸ **未实施**（方案待裁定）|

## 七、待裁定

| # | 事项 | 我方可自决？ |
|---|---|---|
| 1 | 隔离方案选甲/乙/丙 | ❌ **需裁定**（涉及是否改 conftest、是否允许排除 4 文件出全库分母）|
| 2 | 4 个副作用文件在 V9 报告里记「未跑」是否可接受 | ❌ **需裁定**（R12 ① 允许「未运行」状态，但「4/398 因环境未跑」是否影响 V9 通过需总调度确认）|

**本轮沉淀：N/A**（理由：只读审计件；「跑全库前必审 conftest 副作用面、命中数须逐行判型」拟入册方法论族）
