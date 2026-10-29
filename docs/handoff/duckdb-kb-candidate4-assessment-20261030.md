# db 族第四条候选评估：共享工作区「暂存区混入他线文件」的处置范式

- **日期**：2026-10-30｜**归属**：duckdb 数据库升级执行会话（图节点⑲）｜**来源**：总调度 2026-10-30 指示（「可作 db 族第四条候选，随 M-4 一并评估」）
- **背景**：2026-10-30 Phase 7 落码时实测两处险情 —— ①我方 `git add` 精确路径后，**暂存区出现他线 5 个文件**（`docs/ISSUES.md` / `docs/PROJECT_STATE.md` 等），直连 `git commit -F` 会把他线内容一并提交；②`git diff --cached --name-only` 对中文路径按 `core.quotePath` 转义（`23-WAL残留处置.md` → `"23-WAL\346\256\213…"`）导致清单比对误判「多余文件」。
- **处置（已执行并验证）**：改用 `git commit --only -F <uuid 唯一 message 文件> -- <精确路径>`（只提交指定路径，**不 `git reset`** 以免打乱他线待提交状态）；清单核对一律加 `-c core.quotePath=false`；提交后核 `git log -1 --format="%s"` 标题 **且** 核 `git show --name-only` 与预期逐项相同，并核他线暂存项仍在。

## 评估结论：**不入 db 族**（域不符），改落「铁律 10 完整版增补」

| 判据 | 结论 |
|---|---|
| **族域匹配** | ❌ `db` 族域 = 「**数据库引擎与数据完整性域**」（`knowledge/kb-info.yaml` families 段，2026-10-29 总调度裁定立族）。本条目属**多会话协作 / 提交纪律**域，与「引擎版本治理」「数据完整性」两个语义面**无交集** ⇒ 入 db 族会稀释族域、破坏族内条目语义纯度（族 schema 的 `notes` 已声明「数据库引擎与数据完整性域」，条目须服从族域） |
| **fix_layer 可映射** | ⚠️ 勉强可映射为 `artifact_rule`（流程/制品规则），但该枚举在 db 族的语义是「制品判据规则」，与「提交命令用法」**不同物**，映射为**凑枚举**而非真实落点 |
| **domain 可映射** | ⚠️ 可挤进 D5「制品与文档一致性」，但 D5 已被候选③（制品判据回扫）占用同一语义位，两条不同性质的条目挤同一域会削弱域的可检索性 |
| **替代落点（更合适）** | ✅ **铁律 10 完整版增补**：`knowledge/iron-rules/10-共享核心文件提交.md` —— 该文件本就是「共享核心文件提交纪律」的逐字归档，现文只写到「禁 `git add -A` / 一律精确文件清单 add」，**未覆盖「暂存区可能混有他线文件 ⇒ 须用 `--only`」与「中文路径核清单须加 `core.quotePath=false`」** ⇒ 这正是本条目该沉淀的位置 |
| **已完成的部分** | ✅ 跨会话记忆库已沉淀（memory id `78769860`，与既有「我方 add 后暂存区可能为空」条目互为反向同族） |

## 待办（**需授权后再改，本轮未擅动**）

1. **铁律 10 完整版增补**（治理面）：在 `knowledge/iron-rules/10-共享核心文件提交.md` 增「暂存区可能混有他线在途文件 ⇒ `git commit --only` + `core.quotePath=false` + 提交后核他线暂存仍在」；连带 `AGENTS.md` 摘要档（档位 C）与预算门四查（`scripts/check-iron-rules-budget.py`）复核。**属治理面改动，须经②审 → 用户批 → 落码**（与 Phase 7 治理批同流程）。
2. **族内不新增条目**：`db` 族维持三条（`DB-2026-0001..0003`）。

## 若日后另立协作族，可再入族

若⑥后续立「工程纪律 / 多会话协作」族（族域 = 协作与提交纪律），本条目可按 `fix_layer=artifact_rule` + 该族自有的 domain 码入族，字段与本文件§背景/§处置两段可**直接复用**（symptom / root_cause / fix_target / evidence / fixture / trigger 六项齐备，trigger 取：「我方 `git add` 后 `git diff --cached --name-only` 出现非我方路径」）。

---

**附：四条候选最终落点一览**

| 候选 | 内容 | 落点 | 状态 |
|---|---|---|---|
| ① | duckdb 1.5.x `executemany` 批量写劣化 4–6 倍 | `knowledge/db/DB-2026-0001.md`（D2 / `write_path+upgrade`） | ✅ 已入族（commit `8149565`） |
| ② | `DROP+RENAME` 静默清空主键 vs `TRUNCATE` 保留 | `knowledge/db/DB-2026-0002.md`（D3 / `write_path+constraint+lint_guard`） | ✅ 已入族（commit `8149565`） |
| ③ | 实施期制品判据随改道失准 ⇒ 须回扫 | `knowledge/db/DB-2026-0003.md`（D5 / `artifact_rule`） | ✅ 已入族（commit `8149565`） |
| ④ | 共享工作区暂存区混入他线文件的处置范式 | **不入 db 族**（域不符）⇒ 跨会话记忆库已沉淀；铁律 10 完整版增补待授权 | ⏳ 评估结论已出，改文待授权 |
