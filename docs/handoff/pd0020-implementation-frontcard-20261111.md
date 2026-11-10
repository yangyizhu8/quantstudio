# 11-11 实施批 · 开工前置卡（回退点方案 · 按新铁律 03 修订版）

> **成因**：AGENTS.md 铁律 03 于 2026-11-10 更新——**脏树禁 `git stash create`**（实测会吞他线未提交改动）
> ⇒ 改 **per-path 定向 patch 锚**；净树才用四步（`git stash create "baseline-…"` 位置参数、**无 `-m`**、随即 `store`）。
> **实测树态（2026-11-10 11:1x）**：总变更 **869**（他线 **867**／我方 **0**）⇒ **判脏树**。

## 一、判据与结论

| 项 | 实测 | 结论 |
|---|---|---|
| `git status --porcelain` 变更总数 | **869** | — |
| 其中他线（非本会话） | **867**（含 `AGENTS.md`／`README.md`／`agent_workspace/**`／`config/profiles/**`／`docs/evidence/**` 等） | **禁 `stash create`** |
| 其中我方 | **0**（`tests/test_order_result_audit.py`、`tests/test_qmt_eps_snapshot.py` 两个 `??` **非我方创建**，他线亦在 `tests/` 下新增） | 本批我方起点**干净** |
| HEAD / 领先 origin | `1046cc2` ／ 53 笔 | 他线已推进，开工前须重取 HEAD |

## 二、11-11 回退点方案（per-path patch 锚）

**目标文件（本批将改动·仅此两项）**：

```
quantstudio/strategy_compiler/source_import.py      ← a 项（_qs_cash_avail / _qs_cash_budget / 告警）
tests/                                               ← 新固件（卖出路径预算日志数=0 等）
```

**建锚命令（逐条执行，禁 PowerShell `>` 写 patch〔会成 UTF-16〕）**：

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$anchor = ".git/qs_cb_anchor_$stamp.patch"
git diff --output=$anchor -- quantstudio/strategy_compiler/source_import.py
# 记录 sha256 供事后核对
Get-FileHash $anchor -Algorithm SHA256 | Format-List
```

**建后必立验可用（不过 ⇒ 视同无回退点，停手重建）**：

```powershell
git apply --check --reverse $anchor     # 必须在当前树上可反向应用
```

**回退方式**（单文件定向，不整仓 reset）：

```powershell
git checkout -- quantstudio/strategy_compiler/source_import.py
# 或按锚精确反向应用
git apply --reverse $anchor
```

## 三、开工前必做（铁律 03 ②③）

1. `git status --porcelain` 落盘 `docs/handoff/status-20261111-<HHMM>.txt`；
2. `git diff --stat -- quantstudio/strategy_compiler/source_import.py` 落盘（确认开工前该文件**无我方未提交改动**）；
3. **不使用** `git add -A`／`git add .`；提交一律 `git add -- <显式路径>` ＋ `git diff --cached --name-only` 三查。

## 四、本批不动的面（防误伤）

| 面 | 原因 |
|---|---|
| 他线 867 个改动 | 非本会话工作，**禁盲回退/禁携带** |
| `quantstudio/ptrade_deploy/templates/isolation_template.py` | 隔离层本批**免改**（§11.3 e 项：iso 整体覆盖 `_qs_cash_budget`，无数值叠加） |
| `order_value` 卖出不拆单（§11.7） | P3 登记待议，**本批不动** |
| 策略源码 | 铁律 07：零改动 |
