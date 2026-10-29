# Phase 7 治理条文呈批件：constitution 替换稿 + 铁律/活文档 diff（T036 / T037 / T038）

- **日期**：2026-10-29 23:20（Asia/Shanghai）｜**归属**：duckdb 数据库升级执行会话（图节点⑲）
- **呈报对象**：总调度（②审）→ 按治理流程转对应裁定方
- **性质**：**只出替换稿与 diff，不落码**（T036 措辞随②审呈批后落码；T037/T038 明文「只出 diff 与提交信息草稿」）
- **关联**：spec `specs/002-duckdb-1.5-upgrade/`（FR-003/FR-011/FR-012/FR-013）；判定源 `quantstudio/pipeline/duckdb_version_policy.py`

---

## 一、T036 · `.specify/memory/constitution.md:38` 替换稿

### 现文（第 38 行，单行）

```markdown
- **运行环境**：Python 项目，本地零依赖策略；数据主库为 DuckDB（1.4.x 钉版），不经手升级 duckdb 版本。
```

### 替换稿（两行，三要素齐备）

```markdown
- **运行环境**：Python 项目，本地零依赖策略；数据主库为 DuckDB。duckdb 版本治理按**放行集合**执行（当前集合：`{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`；唯一判定源 `quantstudio/pipeline/duckdb_version_policy.py`，门禁/双脚本/契约三个判定面共用同一实现，不得各自维护规则）。集合外版本一律由启动门禁拒启（固定退出码 3）；紧急放行只经 `QS_DUCKDB_VERSION_GATE=0`，且必须留醒目告警。升级决策须满足 **90/60/30 升级判据**（见下）。
- **到期复核装置**：放行集合的每条版本线登记 EOL 日期；按 **90/60/30 升级判据**推进——**距 EOL 90 天出方案**、**60 天进实施**、**30 天完成或书面接受风险**（**判据以 spec FR-010 为唯一口径**）。到期、以及缺陷单关闭或新补丁发布时，由承载方（**总调度 EOL 日历守护** + 仓库内守护条目；落地实现见 `specs/002-duckdb-1.5-upgrade/tasks.md` T056）自动落一条复核提醒；未完成复核的版本线不得继续放行。
```

### 三要素对照

| 要素 | 落点 |
|---|---|
| ① 放行集合 | 第 1 行：`{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}` + 判定源唯一 + 三面同源 + 拒启退出码 3 + 逃生阀受控 |
| ② 90/60/30 升级判据 | 第 2 行：**距 EOL 90 天出方案 / 60 天进实施 / 30 天完成或书面接受风险**（逐字取自 spec FR-010，为唯一口径；2026-10-30 H3 回修：删除初稿自造的「监控/评估/冻结期」表述） |
| ③ 到期复核装置 | 第 2 行：EOL 登记 + 三个节点动作 + 缺陷单关闭/新补丁触发的自动提醒 + 承载方（总调度 EOL 日历守护 + 仓库内守护条目，T056 落地）+ 未复核不得放行 |

> 说明：90/60/30 的**具体动作**为本稿自洽定义（如与既有约定冲突，以②审裁定为准）；到期复核装置的**承载方已明确**（总调度 EOL 日历守护 + 仓库内守护条目），落地实现由 T056 承接。

---

## 二、T037 · 铁律条文 diff（**只出 diff，不落码**）

### 2.1 `AGENTS.md:207`（23 号铁律常驻摘要，第 ④ 句）

```diff
- …④经验钉死：体积阈值不可作唯一判据（探测优先）；bash 脚本 `${VAR}` 花括号界定；duckdb 版本门必须 1.4.x；`stock_daily` 日期列自动探测。
+ …④经验钉死：体积阈值不可作唯一判据（探测优先）；bash 脚本 `${VAR}` 花括号界定；duckdb 版本门须落在**放行集合**内且以**判定源为唯一实现**（当前集合 `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`，判定源 `quantstudio/pipeline/duckdb_version_policy.py`；集合外版本拒启退出码 3）；`stock_daily` 日期列自动探测。
```

### 2.2 `knowledge/iron-rules/23-WAL残留处置.md:6`（第 4 条依据 ③）

```diff
- 4. **依据**（2026-10-19 客户 Mac 实弹）：…③ duckdb 1.5.x 与钉版 1.4.x 不兼容（`Conflict on tuple deletion`，版本门必须 1.4.x）；…
+ 4. **依据**（2026-10-19 客户 Mac 实弹；**2026-10-29 首跳升级后修订**）：…③ duckdb 版本门须落在**放行集合**内且以**判定源为唯一实现**（当前集合 `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`，判定源 `quantstudio/pipeline/duckdb_version_policy.py`）—— 1.5.0–1.5.5 存在 ART index-delete 致命回归（`duckdb#23645`：1.5.x 由静默错删变为 FATAL，**1.5.6 未修复**）；本项目对该缺陷暴露面为 0（非唯一 ART = 0；41 个主键三臂验证不丢），故 1.5.6 纳入放行集合、1.5.5 及以下不放行；…
```

> 口径更正说明：原「1.5.x 与钉版 1.4.x 不兼容」表述与实测不符 —— 实测 1.4.5↔1.5.6 **无存储格式门**（E4a/E4b），真正的问题是 1.5.0–1.5.5 的 ART index-delete 缺陷面。

### 2.3 提交信息草稿（两笔，uuid 唯一文件名纪律）

```
笔①（治理条文批）
  docs(docs): 修订 duckdb 版本门口径 —— 放行集合 + 判定源唯一（constitution:38 / AGENTS:207 / iron-rules/23:6）
  - constitution.md:38 替换稿：放行集合 + 90/60/30 判据 + 到期复核装置（承载方：总调度 EOL 日历守护 + 仓库内守护条目）
  - AGENTS.md:207 与 iron-rules/23:6：「版本门必须 1.4.x」→「须落在放行集合内且以判定源为唯一实现」
  - 更正 1.5.x「不兼容」表述：实测 1.4.5↔1.5.6 无格式门，缺陷面在 1.5.0–1.5.5 的 ART index-delete
  - 关联：spec 002-duckdb-1.5-upgrade / FR-003、FR-011、FR-012

笔②（活文档同步批）
  docs(docs): 同步 duckdb 版本口径到四处活文档 + 登记只读消费者与历史卷宗不回改
  - customer-notice-4:64 / gui-startup-nonblocking-design:61 / writers-export-three-requests-design:37,195：钉版表述改为放行集合
  - case008（事故复盘卷宗）：**只追加口径更新节，不改历史动作记录**
  - environment-map-20260923：追加「只读消费者」节（venv_miniQMT 1.5.3 不升级、只读、写者入口将被拒启）
```

> 提交纪律（铁律 10 + 多会话共享工作区）：**精确路径 `git add`（禁 `-A`）**；message 文件用 uuid 唯一名（`io.open(path,"w",encoding="utf-8",newline="\n")`）；提交后核 `git log -1 --format="%s" HEAD` 首行 == 期望标题；双仓推送在**用户确认闸**之后。

---

## 三、T038 · 四处活文档同步 diff（**只出 diff，不落码**）

### 3.1 `docs/handoff/customer-notice-4-duckdb-component-warning-20260921.md:64`

```diff
- pip install "duckdb>=1.4.5,<1.5"
+ pip install "duckdb==1.5.6"     # 放行集合：1.4.5 ≤ x < 1.5 或 x == 1.5.6
```

### 3.2 `docs/case008-duckdb-version-mixing-unification-incident.md`（**追加节，不改历史行**）

```diff
@@ 文末追加 @@
+ ## 口径更新（2026-10-29 · 首跳升级后）
+ 本文 §1–§6 记录的是 2026-09-21~23 当时的事实与动作，**不回改**。版本口径现更新为：
+ 钉版 `duckdb==1.5.6`（单版本精确锁定），放行集合 `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`，
+ 唯一判定源 `quantstudio/pipeline/duckdb_version_policy.py`（门禁 / 双脚本 / 契约三面同源）。
+ 文中「钉版 `duckdb>=1.4.5,<1.5`」「降级 `pip install "duckdb>=1.4.5,<1.5"`」等表述为**当时动作记录**。
```

> 处置理由：本件是**事故复盘卷宗**（记的是 09-21 降级动作与「钉版从未覆盖实际运行环境」这一事实），改写会扭曲历史；故**只追加口径更新节**。

### 3.3 `docs/gui-startup-nonblocking-design.md:61`

```diff
- …启动即校验 `duckdb.__version__` 属 **1.4.x**；非 1.4.x **拒启**并打印修复指引（钉版依据：`pyproject` 双处 `duckdb>=1.4.5,<1.5`）；…
+ …启动即校验 `duckdb.__version__` 落在**放行集合** `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}` 内（判定源唯一：`quantstudio/pipeline/duckdb_version_policy.py`）；集合外**拒启**（固定退出码 3）并打印修复指引（声明面依据：`pyproject` 双处 `duckdb==1.5.6`）；…
```

### 3.4 `docs/writers-export-three-requests-design.md:37` 与 `:195`

```diff
@@ :37 @@
- | 隐性风险 | 大批量 `DO UPDATE` 与 1.5.x 的 ART index-delete **fatal 回归同族**（`duckdb#23645`）；本仓已用 `duckdb>=1.4.5,<1.5` 钉版 + 版本闸拒启上锁 | `pyproject.toml:20`、`pyproject.toml:50`、`duckdb_version_gate.py:45-52` |
+ | 隐性风险 | 大批量 `DO UPDATE` 与 1.5.x 的 ART index-delete **fatal 回归同族**（`duckdb#23645`；1.5.0–1.5.5 命中，**1.5.6 未修复**）；本仓已用 `duckdb==1.5.6` 单版本锁定 + 版本闸（放行集合 `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`，判定源唯一 `quantstudio/pipeline/duckdb_version_policy.py`）上锁 | `pyproject.toml:20`、`pyproject.toml:50`、`quantstudio/pipeline/duckdb_version_policy.py` |

@@ :195 @@
- - **不升级** DuckDB（保持 `>=1.4.5,<1.5` 钉版）；
+ - **不升级** DuckDB（保持 `duckdb==1.5.6` 声明面；放行集合 `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`）；
```

---

## 四、历史卷宗**不回改**登记（记的是当时事实）

| 路径 | 记的是 |
|---|---|
| `.specify/assessments/**` | 当时对需求的评估结论 |
| `.specify/bugs/**` | 当时的缺陷定谳 |
| `docs/handoff/dispatch-calendar-*.md` | 当时的调度日历快照 |
| `docs/case008-…incident.md` 的 §1–§6 | 09-21~23 的钉版与降级动作记录（仅**追加**口径更新节） |
| `docs/evidence/duckdb-version-environment-map-20260923.md` §1–§7 | 09-23 的环境实况（仅**追加** §8「只读消费者」节，**已落码**） |

---

## 五、落码顺序建议（②审通过后）

1. 先落**治理条文批**（笔①：`constitution.md` + `AGENTS.md` + `iron-rules/23`）—— 口径源头；
2. 再落**活文档同步批**（笔②：四处活文档 + 环境地图追加节 —— 环境地图 §8 本轮已落码，提交时并入笔②）；
3. 两笔各自**精确路径 add**、uuid message 文件、提交后核标题；
4. **双仓推送在用户确认闸之后**（六步第⑤步）。

> 本件**未落码**部分：§一 constitution 替换稿、§二两处铁律 diff、§三四处活文档 diff —— 全部等②审呈批后按上序落码。

---

## 六、落码记录（2026-10-30 · ②审通过后执行）

| 笔 | commit | 标题 | 文件清单（精确） | 备注 |
|---|---|---|---|---|
| ① | `76106cf` | `docs(docs): 修订 duckdb 版本门口径 —— 放行集合 + 判定源唯一（constitution:38 / AGENTS:207 / iron-rules/23:6）` | `.specify/memory/constitution.md`（**首次入库 56 行**）／`AGENTS.md`（2±）／`knowledge/iron-rules/23-WAL残留处置.md`（2±） | AGENTS.md 改动后已过 `scripts/check-iron-rules-budget.py` 四查（AGENTS.md 33,408 B / 链 51,559 B / 25 条×91 词命中 / 指针 25 双向闭合 / 孤儿 0） |
| ② | `e941944` | `docs(docs): 同步 duckdb 版本口径到四处活文档 + 登记只读消费者` | `customer-notice-4-…md`（2±）／`gui-startup-nonblocking-design.md`（2±）／`writers-export-three-requests-design.md`（**首次入库 320 行**）／`case008-…incident.md`（+10）／`environment-map-20260923.md`（+20） | case008 只**追加**口径更新节（历史动作记录未改） |

**提交纪律留痕**：

- 共享工作区**暂存区曾混入他线在途文件**（`docs/ISSUES.md`、`docs/PROJECT_STATE.md` 等 5 项）⇒ 两笔均用
  **`git commit --only -F <uuid 唯一 message 文件> -- <精确路径>`** 提交，**他线暂存内容原样保留**、未被带入；
- commit message 文件用 `io.open(path,"w",encoding="utf-8",newline="\n")` + `uuid4().hex[:8]` 唯一名；
- 每笔提交后核 `git log -1 --format="%s" HEAD` 标题一致 **且** `git show --name-only` 文件清单与预期**逐项相同**（两笔均 PASS）；
- **未推送**（⑥双仓推送待⑤用户确认闸门后另行执行）；
- 两处「此前未被 git 跟踪、首次入库」的文件已在各自 commit message 中显式记录体量与「除本会话改动外为其余既有内容」。
