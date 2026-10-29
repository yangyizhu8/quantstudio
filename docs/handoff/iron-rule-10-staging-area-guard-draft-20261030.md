# 铁律 10 完整版增补草案：暂存区混入他线文件的处置（待下一治理批）

- **日期**：2026-10-30｜**来源**：db 族第四条候选评估（`docs/handoff/duckdb-kb-candidate4-assessment-20261030.md`；该条目**不入 db 族**，改落此处）｜**裁定**：总调度 2026-10-30「铁律 10 完整版增补照准纳入下一治理批（--only 保护他线暂存 + quotePath 两经验；②审→用户批流程）」
- **状态**：**草案（未落码）** —— 落码须走治理面流程：**②审 → 用户批 → 落码**（与 Phase 7 治理批同流程），落码后须过 `scripts/check-iron-rules-budget.py` 四查

## 一、增补背景（两处险情，均为实测）

**险情①（暂存区混入他线文件）**：2026-10-30 Phase 7 落码时，我方 `git add` 3 个治理文件后、`git commit` 之前，**暂存区出现他线 5 个文件**（`docs/ISSUES.md`、`docs/PROJECT_STATE.md`、`docs/evidence/api-surface-behavior-verification-20261028.md`、`docs/handoff/batch10-attribution-and-blindspot-20261029.md`、`docs/handoff/push-batch-execution-card.md`）⇒ 直连 `git commit -F` 会把**他线内容一并提交**，违反本条 2（禁全量提交）与 3（提交内容自查）的**意图**。

**险情②（中文路径清单转义致误判）**：同一轮，`git diff --cached --name-only` 对中文路径按 `core.quotePath` 转义（`knowledge/iron-rules/23-WAL残留处置.md` → `"knowledge/iron-rules/23-WAL\346\256\213\347\225\231\345\244\204\347\275\256.md"`）⇒ 清单比对被误判为「多余文件」⇒ 脚本**在 commit 前 `sys.exit(1)` 中止**（未产生误提交），加 `-c core.quotePath=false` 后复跑通过。

**已验证的处置**：`git commit --only -F <uuid 唯一 message 文件> -- <精确路径>` —— 只提交指定路径，他线暂存内容原样保留；提交后核 `git log -1 --format="%s"` 标题 **且** 核 `git show --name-only` 与预期清单**逐项相同**，并核他线暂存项仍在。本会话 5 笔提交全部按此执行，**他线零影响**。

## 二、`knowledge/iron-rules/10-共享核心文件提交.md` 增补 diff（草案）

```diff
@@ 末尾（第 6 条之后）追加第 7 条 @@
+7. **暂存区可能混有他线在途文件 ⇒ 提交须用 `--only` 精确限定路径**
+   （2026-10-30 固化·多会话共享工作区 Phase 7 两处险情）：
+   - 险情①：多会话并发下，我方 `git add <精确路径>` 之后、`git commit` 之前，
+     **他线可能把自己的文件 add 进同一暂存区**（2026-10-30 实测：我方 add 3 个治理文件后
+     暂存区出现他线 5 个文件）⇒ 直连 `git commit -F` 会把**他线内容一并提交**，
+     违反本条 2 与 3 的意图；
+   - 险情②：`git diff --cached --name-only` 对**中文路径**按 `core.quotePath` 转义
+     （`23-WAL残留处置.md` → `"23-WAL\346\256\213…"`）⇒ 清单比对必然误判为「多余文件」
+     （2026-10-30 实测误判一次；脚本在 commit 前中止，未产生误提交）；
+   - **硬动作**：①核暂存清单一律加 `-c core.quotePath=false`；②暂存区含他线文件时
+     **禁 `git reset`**（会打乱他线待提交状态），改用
+     `git commit --only -F <uuid 唯一 message 文件> -- <我方精确路径>`
+     （只提交指定路径，他线暂存内容原样保留）；③提交后除核标题外，须核
+     `git show --name-only` 与预期清单**逐项相同**，并核**他线暂存项仍在**。
```

## 三、`AGENTS.md` 摘要档（档位 C）增补 diff（草案）

```diff
@@ 铁律 10 摘要末句之后 @@
 - 摘要：①提交前按 03 号铁律四步配方建回退点 + `git status --porcelain` 核对**精确文件清单**只含本会话改动 + 提交信息自查不含他人改动内容；②禁 `git add -A` / `git add .`，一律精确文件清单 add；③批提交/推送后核对双远程 HEAD 逐位一致；④检测到他人未提交改动叠加须在提交信息与设计文档显式记录；⑤共享文件每次 edit 后**即时 `git diff` 自检**（并行提交会无声覆盖，edit 成功≠改动仍在）；⑥**矩阵哈希同 commit 追认**：改 PTrade wrapper 模板串（`_QS_FUNDAMENTALS_EXT` 等）须同 commit 带 `check_fund_matrix.py --check --reverify` 的矩阵 yaml/md 哈希；QMT 侧同等适用（`source_import_qmt.py` 的 `_QMT_HASH_SCOPE` 20 常量 → `meta.qmt_wrapper_hash`，与 PTrade `wrapper_hash` 分立防串）；CI 已红则补 chore(matrix) 追认并登记案例。
+  ⑦**暂存区可能混有他线在途文件** ⇒ 提交须 `git commit --only -- <精确路径>`（禁 `git reset` 以免打乱他线待提交态）；核暂存清单加 `-c core.quotePath=false`（中文路径否则被转义致误判）；提交后须核 `git show --name-only` 与预期清单**逐项相同**且**他线暂存项仍在**。
```

## 四、落码前必过项（预算门四查）

| 查项 | 预期 |
|---|---|
| AGENTS.md 体积 | 现 **33,408 B**；增补约 +190 B ⇒ ≈ **33,600 B**（预算门自判，须实测为准） |
| 链体积 | 现 51,559 B；增补同量级 ⇒ 仍须门禁实测 |
| **25 条×91 词命中** | 增补为**同条内追加**，不新增铁律条 ⇒ 该判据应不受影响，**落码后必须复跑实测** |
| 指针 25 双向闭合 / 孤儿 0 | 不新增指针 ⇒ 应不受影响，**落码后必须复跑实测** |

> 纪律：落码后 `python scripts/check-iron-rules-budget.py` 四查**必须全绿**（`AGENTS.md` 为共享治理核心文件，每次 edit 后即时 `git diff` 自检）。

## 五、流程与归属

| 步骤 | 责任方 | 状态 |
|---|---|---|
| 起草增补 diff（本件） | ⑲ duckdb 升级执行会话 | ✅ 已完成（2026-10-30） |
| ②审（独立审核） | 总调度 / 审核方 | ⏳ 随下一治理批 |
| ⑤用户确认 | 用户（治理面硬闸） | ⏳ 随下一治理批 |
| 落码（完整版 + AGENTS.md 摘要） | ⑲ 或治理批执行方 | ⏳ 待批 |
| 预算门四查复跑 | 落码方 | ⏳ 落码后必跑 |
| 双仓推送 | 落码方 | ⏳ 待⑤用户确认后 |

## 六、关联

| 用途 | 路径 |
|---|---|
| 第四条候选评估（为何不入 db 族） | `docs/handoff/duckdb-kb-candidate4-assessment-20261030.md` |
| 跨会话记忆（已沉淀） | 记忆库条目（共享工作区·暂存区混入他线文件 ⇒ commit 必须用 --only） |
| 实证（5 笔提交均按此执行） | `fdca384` / `8149565` / `53a6afd` / `76106cf` / `e941944` |
