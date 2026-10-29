# T010 全仓调用点兼容核查（duckdb 版本常量语义变更影响面）

- **日期**：2026-10-29 16:30（Asia/Shanghai）
- **归属**：duckdb 数据库升级执行会话（图节点⑲）｜M-2 审核意见②
- **变更内容**：`REQUIRED_SERIES = "1.4"`（字符串单值）→ 放行集合结构（`ALLOWED_RANGES`），常量**删除**
- **结论**：**外部引用点 = 0**；需同批适配的仅**判定源模块内部 4 处**；活文档旧口径另有清单（只出 diff）

## 一、核查方法

```powershell
# 代码面（排除 .git/.claude/build/output/agent_workspace/node_modules）
Get-ChildItem -Recurse -Include *.py,*.ps1,*.sh,*.md,*.toml,*.txt,*.json -File |
  Where-Object { $_.FullName -notmatch '\\\.git\\|\\\.claude\\|\\build\\|\\output\\|\\agent_workspace\\|\\_runtime\\|\\node_modules\\|\\data\\' } |
  Select-String -Pattern 'REQUIRED_SERIES|duckdb_version_gate|require_duckdb_version|check_duckdb_version|GATE_ENV|QS_DUCKDB_VERSION_GATE' -Encoding UTF8
```

## 二、`REQUIRED_SERIES` 引用点（变更前）

| 文件:行 | 内容 | 处置 |
|---|---|---|
| `quantstudio/pipeline/duckdb_version_gate.py:30` | 定义 `REQUIRED_SERIES = "1.4"` | **删除**（改为 re-export `ALLOWED_RANGES`） |
| `quantstudio/pipeline/duckdb_version_gate.py:52` | `ver.startswith(REQUIRED_SERIES + ".")` | **替换**为 `is_allowed(ver)` |
| `quantstudio/pipeline/duckdb_version_gate.py:59` | debug 文案 `需 {REQUIRED_SERIES}.x` | **改写**为集合描述（否则输出裸元组） |
| `quantstudio/pipeline/duckdb_version_gate.py:64` | 逃生阀告警文案 | **改写**为集合描述 |
| `quantstudio/pipeline/duckdb_version_gate.py:70` | 拒启文案 `需 {REQUIRED_SERIES}.x，pyproject: duckdb>=1.4.5,<1.5` | **改写**为集合描述 + 新声明面 pin |

**外部引用点（代码）= 0**：
- `main_gui.py:17,19` → 只 import/调用 `require_duckdb_version`；
- `quantstudio/pipeline/daemon.py:4075,4077` → 同上；
- `tests/test_duckdb_version_gate.py`（改前）→ 只用 `check_duckdb_version` / `require_duckdb_version` / `GATE_ENV`，**不引用** `REQUIRED_SERIES`；
- `.specify/assessments/**`、`.specify/bugs/**`、`docs/handoff/dispatch-calendar-*.md`、`knowledge/registry.md` → **历史卷宗/台账**，记的是当时事实，**不回改**（登记为历史事实）。

## 三、判定源与门禁调用点（不变）

| 消费方 | 位置 | 形态 |
|---|---|---|
| GUI 入口 | `main_gui.py:19` `require_duckdb_version("GUI (main_gui.py)")` | 进程内调用 |
| daemon 入口 | `quantstudio/pipeline/daemon.py:4077` `require_duckdb_version("daemon (quantstudio.pipeline.daemon)")` | 进程内调用（`-m` 与直启同路） |
| GUI 拉起子进程 | 继承 GUI 解释器 + 子进程自身过闸 | 进程内调用 |
| 运维止损脚本 | `check_wal.ps1:66-72`、`check_wal.sh:41-48` | **改为单文件直调判定源**（见下） |
| 测试契约 | `tests/test_duckdb_version_gate.py` | import 判定源 |

## 四、实施期形态调整（重要留痕）

原计划（plan/contract 草案）令两个运维脚本调用 `python -m quantstudio.pipeline.duckdb_version_gate --check`。
**实施时发现该形态不可行**：`scripts/check_wal_regression.sh` 把 `check_wal.sh` 复制到临时目录后运行，
此时项目包**不可导入** ⇒ `-m` 形态必然 `ModuleNotFoundError` ⇒ 版本判定退化为 ERROR ⇒
**T2–T9 全部用例连锁失败**（fail-closed 本身正确，但回归门会全线变红）。

**处置**：判定源下沉为**无包内依赖的单文件** `quantstudio/pipeline/duckdb_version_policy.py`：

- 该文件**无包内 import**，故既可被 `python <file> --check` 单文件直接执行（供 ps1/sh 薄壳调用），
  也可被 `quantstudio.pipeline.duckdb_version_gate` 以相对 import 复用；
- `duckdb_version_gate.py` 降为**薄封装**（取版本 → 调策略 → 放行/拒启），并提供 `-m` 入口转发同一 CLI；
- 回归脚本同步：把判定源文件一并复制到临时工作目录（`$work/quantstudio/pipeline/`）。

**单源性质不变**：判定规则只存在于 `duckdb_version_policy.py`；门禁、ps1、sh、pytest 四个消费面
全部引用它；契约测试以 `gate.ALLOWED_RANGES is policy.ALLOWED_RANGES` 机检「同一对象」。

## 五、活文档旧口径清单（只出 diff，待②审呈批后落码）

| 文件:行 | 旧口径 |
|---|---|
| `AGENTS.md:207` | 23 号铁律摘要「duckdb 版本门必须 1.4.x」 |
| `knowledge/iron-rules/23-WAL残留处置.md:6` | 同上 |
| `docs/handoff/customer-notice-4-duckdb-component-warning-20260921.md:64` | `pip install "duckdb>=1.4.5,<1.5"` |
| `docs/case008-duckdb-version-mixing-unification-incident.md:5,49` | 「钉版 `duckdb>=1.4.5,<1.5`」叙述 |
| `docs/gui-startup-nonblocking-design.md:61` | 「版本闸（非 1.4.x 拒启）」 |
| `docs/writers-export-three-requests-design.md:37,195` | 「本仓已用 `duckdb>=1.4.5,<1.5` 钉版 + 版本闸拒启」 |
| `docs/evidence/duckdb-version-environment-map-20260923.md:5,25` | 环境地图（**追加**「只读消费者」节，不改历史读数） |
