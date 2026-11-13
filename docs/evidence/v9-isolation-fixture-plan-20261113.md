# V9 全库回归隔离/fixture 处置方案

> **依据**：节点⑳ R13「V9 副作用审计已有明确风险，不裸跑全库；**先完成隔离／fixture 处置方案**」「生产硬编码路径和真起 daemon 测试须**独立隔离**，**环境变量不被实际消费就不能作为隔离证明**」
> **性质**：方案文档。**未改任何代码、未跑任何测试、未碰 daemon/flag/DB 写**。
> **实测时钟**：2026-11-13 08:56–09:05（Asia/Shanghai）
> **基线**：HEAD 未变（上一轮审计件尚未入库，见 §六）

---

## 一、隔离可行性：env 已被**实测证明**被消费

### 1.1 唯一解析点

`quantstudio/_paths.py`（60 行，全文读过）：

```python
def get_data_root() -> Path:
    env = os.environ.get("QUANTSTUDIO_DATA_ROOT")   # L30 ← 真实消费点
    if env:
        return Path(env)
    ...config/data_config.json 的 path 父目录...
    return _ROOT / "data"

DATA_ROOT = get_data_root()        # L50 ← ★模块加载即固化，只算一次
def db_path(name="quantstudio.db") -> Path:
    return DATA_ROOT / name        # L55
```

**⇒ 关键约束：`DATA_ROOT` 在 `import quantstudio._paths` 那一刻固化。**
**⇒ env 必须在任何 `import quantstudio.*` 之前设置，否则无效。**

### 1.2 我上一轮的猜测是错的

| 我上一轮写 | 实测 |
|---|---|
| 建议用 `QS_DB_PATH` / `QS_DATA_DIR` | **二者在生产代码中零命中，不存在** |
| 标注「需确认生产代码是否读该变量（未核实）」 | **现已核实：正确的是 `QUANTSTUDIO_DATA_ROOT`** |

### 1.3 实测证明（两进程对照，2026-11-13 08:58）

```
[基线·不设 env]  rc=0
  DATA_ROOT= D:\miniQMT策略实盘\QuantStudio\data
  db_path  = D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db

[设 env → C:\...\Temp\qs_isolation_test_koun53tm]  rc=0
  DATA_ROOT= C:\Users\Administrator\AppData\Local\Temp\qs_isolation_test_koun53tm
  db_path  = C:\Users\Administrator\AppData\Local\Temp\qs_isolation_test_koun53tm\quantstudio.db

✅ 结论：QUANTSTUDIO_DATA_ROOT **被真实消费**，db_path() 已重定向
```

**⇒ 满足「环境变量被实际消费」的证明要求。但见 §三：它对 2 个文件无效。**

---

## 二、4 个副作用文件**逐个重新定性**（含对我上轮判定的修正）

### 2.1 ★ 修正：`test_daemon_gui_start_paths.py` 我判重了

**我上一轮**：列为「真起 daemon 子进程 ⇒ 危险」。
**实际**：**它是本仓隔离做得最好的样板**，自述与代码一致：

```python
L15  · 全部使用**临时 data_root**（monkeypatch `_data_root`），绝不触碰生产 data/；
L42  monkeypatch.setattr(dp, "_data_root", lambda: tmp_path)      # 进程内隔离
L103 env = {**os.environ, "QUANTSTUDIO_DATA_ROOT": str(tmp_path), ...}
L110 subprocess.Popen(..., env=env, cwd=str(ROOT))                # 子进程继承隔离 env
```

**⇒ 双重隔离（in-process monkeypatch + child env），**无需处置**，直接跑。
**⇒ 我的错误**：只用「subprocess + daemon 模块名」的形态判据，没读它上面的隔离 fixture。**命中形态 ≠ 真实风险**。

### 2.2 `test_daemon_once_exit_contract.py`：可隔离

| 行 | 内容 | 判定 |
|---|---|---|
| L108-109 | `subprocess.run([sys.executable, "-m", "quantstudio.pipeline.daemon", "--help"])` | **`--help` 无副作用** |
| L163-170 | `--mode once` 真实调用，`env = os.environ.copy()` | **继承父进程 env** ⇒ conftest 若设 env，子进程自动继承 |

**⇒ 处置**：conftest 顶层设 env 后，此文件**自动被隔离**，无需改测试。
**⇒ 但仍需**在无 daemon 的窗跑**（它启 real collector，可能与生产 daemon 抢资源）。

### 2.3 ★★ `test_fund_contract_wiring_matrix.py` / `test_fund_statement_acceptance.py`：env **无效**

```python
# test_fund_contract_wiring_matrix.py L18
DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"     # ★字面量，绝对路径
# L23
eng = BacktestEngine(db_path=DB, strategy={}, ...)               # 显式传入，不走 db_path()

# test_fund_statement_acceptance.py L15
eng = BacktestEngine(db_path=DB, strategy={}, ...)               # 同一模块级常量
```

**⇒ 路径以**字面量 + 显式参数**注入，`db_path()` 与 `QUANTSTUDIO_DATA_ROOT` **完全不参与**。
**⇒ 任何 env 隔离对这 2 个文件**无效**（R13「环境变量不被实际消费就不能作为隔离证明」正指此）。**

**⇒ 后果**：daemon 持锁期跑它们 ⇒ **必然 FAIL**（锁冲突），且**不可归因于 settle 改动**。

---

## 三、处置方案（三层，互不替代）

### 第 1 层：conftest 数据根隔离（覆盖绝大多数）

**位置要求（关键）**：必须写在 `tests/conftest.py` **模块顶层**，不能写成 fixture。

```python
# tests/conftest.py 顶层（在 import quantstudio.* 之前）
import os as _os, tempfile as _tf, atexit as _at, shutil as _sh
_QS_ISO_ROOT = _tf.mkdtemp(prefix="qs_test_data_root_")
_os.environ.setdefault("QUANTSTUDIO_DATA_ROOT", _QS_ISO_ROOT)
_at.register(_sh.rmtree, _QS_ISO_ROOT, True)
```

**为什么不能用 fixture**：`DATA_ROOT` 在 `import quantstudio._paths` 时固化（L50）；
pytest 的 session fixture 在 collection **之后**才执行，此时模块可能已 import ⇒ 失效。
conftest 模块顶层则在 collection 之前执行，**时序正确**。

**验证要求（不得自称有效）**：落盘后必须实测
`python -c "import quantstudio._paths as p; print(p.db_path())"` 在 pytest 上下文中确认指向 tmp，
**并留痕**；仅「设了变量」不算证明。

**不解决的**：§2.3 的 2 个硬编码文件。

### 第 2 层：2 个硬编码文件**独立隔离**（R13 明确要求「须独立隔离」）

三条路，择一（**我不自决，见 §五**）：

| 选项 | 做法 | 代价 |
|---|---|---|
| **乙-1 改测试** | 把 `DB = r"D:\..."` 改为 `DB = str(db_path())` | 改测试代码（2 行×2 文件）；**但改变了「验收基线」**，需重新确认该测试意图不被破坏 |
| **乙-2 临时副本 + 环境变量注入** | 跑前把生产库 `COPY` 到 tmp，用 `pytest --override-ini` + 环境变量？**不可行**（字面量） | — |
| **乙-3 排除出安全批次，记为「未跑·硬编码路径」** | 不跑，在 V9 报告标「未跑」而非 PASS/FAIL | **分母留缺**，需 R13 认可（见 §四） |

**技术事实**：乙-2 不可行（字面量无法覆盖）；**实际只有乙-1 与 乙-3 可选**。

### 第 3 层：真起 daemon 的窗内约束

`test_daemon_once_exit_contract.py` 会启 real collector：

- **必须**在无生产 daemon 的窗跑（否则抢锁/抢资源，读数不可信）
- **不得**用「停 daemon」换取窗口（**我无此授权**，R13 明示）
- 若窗内 daemon 仍在 ⇒ 该文件记「未跑·资源冲突」，**不记 FAIL**

---

## 四、V9 门槛声明（R13 明确：不得降低验收）

**R13 原文**：「排除 4 个副作用文件可以作为安全排错批次，但**不能据此把全库验收改成「其余全过即闭」**；未跑项如实开放，需改变门槛时另呈。」

**本方明确接受，并落到报告口径**：

| 情形 | 记录方式 |
|---|---|
| 跑了且过 | `PASS` |
| 跑了且失败 | `FAIL`（并判定是否 settle 引入）|
| 因环境未跑 | **`未运行·原因`（不得计 PASS，不得计 FAIL）** |
| 因资源窗未开 | **`未运行·待窗`** |

**⇒ V9 结论只有两种**：
- **全 398 全部有 PASS/FAIL 结论** ⇒ 可按结果判定 V9
- **存在「未运行」项** ⇒ **V9 保持开放**，不得写「其余全过即闭」

**⇒ 若要改变门槛（如「排除项不计入分母仍判通过」），由我**另呈裁定申请**，不在本轮自决。**

## 五、待裁定（我不自决）

| # | 事项 | 选项 |
|---|---|---|
| 1 | **第 1 层 conftest 改动**是否授权 | 甲：授权我改 conftest 顶层加 env 隔离（**属测试基础设施改动**，非生产代码）<br>乙：不授权，改由他线/后续提交 |
| 2 | **2 个硬编码文件**走哪条 | **乙-1 改测试**（2 行，改为 `db_path()`）／**乙-3 排除并记「未跑」**（分母留缺）<br>**我倾向乙-3**：改测试等于改验收基线，且该测试意图可能就是「验证真实库可用」；排除+如实标注更保守 |
| 3 | 「未运行」项是否阻断 V9 关闭 | 我的理解：**是**（R13「未跑项如实开放」）⇒ V9 保持开放直到全 398 有结论。请确认 |

## 六、本轮边界自证

| 项 | 状态 |
|---|---|
| 改代码 / 改 conftest | ❌ **未改**（方案待裁）|
| 跑测试 | ❌ **未跑** |
| 停/启 daemon | ❌ **未做** |
| 清 flag | ❌ **未做** |
| DB 写 | ❌ **未做** |
| 上一轮审计件入库 | ❌ **仍阻塞**：门1 因 index 含他线 3 项（`occupancy-failopen-fix-plan-20261112.md` / `occupancy-input-contract-20261113.md` / `anchor-daemon-alive-20261112235837.txt`）而拒；我已撤回我方暂存，文件在工作区 |


**知识反哺**：`[无命中]` knowledge/**（族：QFQ/测试基础设施）+ knowleage_base，2026-11-13
**本轮沉淀**：N/A（理由：方案件，未实施；「隔离证明 = 实测消费，非设 env」拟入册方法论族）
