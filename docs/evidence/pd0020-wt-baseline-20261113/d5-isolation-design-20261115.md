# D5 只读设计：如何让 `test_fund_statement_acceptance` 的取证不触生产库

> 状态：**只读设计**。本件**未运行任何测试**、**未复制/链接/覆盖/打开生产库**。
> 目的：在「D5 仍为 unknown」的前提下，核清 fixture 属主、静态 DDL、必要数据、测试有效性，
> 并给出**可证明生效**的隔离路径；**全部入口未证明避开生产库前不运行**。

## 一、D5 的对象与当前状态

| 项 | 值 |
|---|---|
| 对象 | `tests/test_fund_statement_acceptance.py` 的 5 项失败（`test_income_yoy_two_periods`／`test_pit_boundary_visible_same_day`／`test_pit_boundary_no_future`／`test_balance_fields_flow_ratio`／`test_missing_col_warns_not_silent`） |
| 映射状态 | **D 桶「基线未取证」＝5**，**保持 unknown**（不作为已通过） |
| 为何未取证 | 基线 `7826aed` 的该文件把**主仓生产库绝对路径**直接传给 `BacktestEngine` ⇒ 跑它即打开生产库 ⇒ 不可运行 |

## 二、只读取证结果

### 2.1 主仓工作树存在**未提交**的「隔离 v2」改造

```
git status --porcelain -- tests/test_fund_statement_acceptance.py tests/fund_synthetic_fixture.py
 M tests/test_fund_statement_acceptance.py      （+17 / −3）
?? tests/fund_synthetic_fixture.py               （未跟踪新文件，298 行）
```

`git diff 7826aed HEAD -- 该文件` = **0 字节** ⇒ **HEAD 中仍是生产库版**；
**工作树**已改为合成库版 ⇒ 该改造**尚未入库**，**不属我域**，我方**未擅自改动**。

### 2.2 两侧对照

| | 基线 `7826aed`（= worktree） | 主仓工作树（未提交） |
|---|---|---|
| 取库方式 | `DB = r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db"` | `_synthetic_db(tmp_path_factory)` → `tmp_path_factory.mktemp("fund_synth_stmt")/"quantstudio.db"` |
| 是否触生产 | **是（绝对路径直指生产库）** | 否（临时目录内新建） |
| 依赖的 fixture | 无 | `tests/fund_synthetic_fixture.py::build_fund_db` |

### 2.3 合成 fixture 的隔离性质（docstring 逐字）

> 「⚠ 本模块的隔离性质（R7 定谳后的版本）
> **不连接、不 ATTACH、不读取生产库——包括 READ_ONLY 与 `LIMIT 0` 的结构复制。**
> R7 审核撤掉的正是上一版的 `ATTACH ... (READ_ONLY)` + `CREATE TABLE AS SELECT * FROM prod.t LIMIT 0` 做法…
> 本模块实测复核（自我纠错，留痕）：上一版声称「CTAS 保留完整约束」是**错误声明**…**三重实证一致**。
> 因此本版改为：1. **仓内静态权威 DDL**（列名/类型逐位对齐生产）；2. 建表后**显式校验签名**
> （列名+类型、PK 列、NOT NULL 列），不符时抛 `FixtureSchemaError` —— **明确失败，不静默降级**；
> 3. **取消任何 fallback 分支**。」

### 2.4 入口清单（逐行扫描 298 行）

| 类别 | 结果 |
|---|---|
| 绝对生产路径 | **无** |
| `ATTACH` / `READ_ONLY` / `LIMIT 0` 结构复制 | **无**（R7 已撤） |
| `duckdb.connect` | **仅 L240** `conn = duckdb.connect(str(p))`，`p` 为传入的临时路径 |
| 网络（http/requests/urllib） | **无** |
| 子进程（Popen/subprocess） | **无** |
| 凭据读取（environ/secrets/.env） | **无** |
| DDL | L243 `CREATE OR REPLACE TABLE {table} ({cols})`（仓内静态 `_DDL`） |
| 数据 | L253–L292 `INSERT INTO`：`income_statement`／`balance_statement`／`cashflow_statement`／`fin_indicator`／`stock_daily`／`stock_daily_valuation`／`stock_float_share` |

### 2.5 测试有效性（断言未被反改）

- 测试文件 docstring 逐字：「数据由 `tests/fund_synthetic_fixture.py` 提供；
  **本文件所有断言一字不动**（R4：不为示例数据反改业务预期）」；
- 公告可见日期按真实披露节奏取（2019-01-05 可见 2018 三季报，非 2018 年报）⇒ 满足 R6。

## 三、隔离路径的**可证明性**设计

### 3.1 目标进程解析到隔离路径

```
tmp_path_factory.mktemp("fund_synth_stmt") / "quantstudio.db"
  → 位于 pytest 临时根（与仓库、data/ 完全无关）
  → BacktestEngine(db_path=<该路径>)  ← 唯一库入口
```

⇒ 需补证项（**未完成**）：`BacktestEngine` 是否在 `db_path` 之外另有默认库路径／锁文件落点
（如 `data/` 下的 `.lock`、默认 `quantstudio.db` 回退），须逐入口列举。

### 3.2 全部入口清单（待补证后闭环）

| 入口 | 现状 | 待补证 |
|---|---|---|
| `duckdb.connect`（fixture 内） | 仅临时路径 | ✅ 已证 |
| `BacktestEngine(db_path=…)` | 传入临时路径 | ⚠️ 需查引擎内部是否有默认路径/锁文件 |
| DuckDB **写锁** | fixture 在临时目录建库 ⇒ 锁在该目录 | ⚠️ 需查引擎是否另取锁路径 |
| 子进程 | 无 | ✅ 已证 |
| 凭据 | 无 | ✅ 已证 |

### 3.3 取证方案（**待批准后才执行**）

- **基线侧**：`7826aed` 版本**硬编码生产库绝对路径** ⇒ **不可运行** ⇒
  基线状态**无法取证** ⇒ **D5 基线侧保持 unknown**（与总调度裁定一致）；
- **当前侧**：可在**主仓工作树**（合成库版）运行 ⇒ 证明的是「**当前版本该 5 项的状态**」，
  **不是**「基线是否同样失败」⇒ 两者**不可混同**；
- ⇒ 若要补齐基线侧，只能采用**路径注入**（在 worktree 内以参数/环境把 `DB` 常量指向
  隔离库），但这**需要改基线测试文件**（`7826aed` 不可变）⇒ 只能以「等价替换副本」方式，
  且**必须先证明替换后除该常量外无其他差异**。

## 四、结论

1. **D5 保持 unknown**（基线侧不可安全取证；不作为已通过）；
2. **当前侧**的隔离性已被逐行扫描证明「fixture 内无任何生产库入口」，但
   `BacktestEngine` 内部入口与锁落点**尚未逐项证明** ⇒ **不运行**；
3. 该「隔离 v2」改造属**他线未提交工作**，我方**只读审查、不擅动、不提交**；
4. 待 §3.2 待补证项闭环且获批后，再申请该隔离范围内的运行。
