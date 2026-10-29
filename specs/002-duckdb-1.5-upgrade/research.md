# Research & 技术决策：002-duckdb-1.5-upgrade

**Date**: 2026-10-29 | **输入**：spec.md、decision v9.1、PT-01 段 A 准据（`docs/evidence/passthrough-drop-repro-20261028.md`）、A-14 实测、六裁

> 每项格式：Decision / Rationale / Alternatives considered。全部决策已消解，无残留 NEEDS CLARIFICATION。

---

## R-01 放行集合的判定实现

**Decision**：`duckdb_version_gate.py` 内以「区间元组 + 精确点」表达：`((1,4,5), "<", (1,5))` 与 `((1,5,6), "==", (1,5,6))` 的并集；版本解析取前三段整数；解析失败/段数不足/非数字 ⇒ **拒绝**（fail-closed）。

**Rationale**：字符串前缀判断（现 `startswith("1.4.")`）无法表达「1.5.6 放行但 1.5.5 拒绝」，且对 `1.4.10`、`1.5.60` 一类会误判。元组比较一次解决下界（≥1.4.5）、上界（<1.5）与精确点（==1.5.6）。

**Alternatives**：
- 正则 `^1\.(4\.[5-9]|5\.6)$` —— 可读但不可扩展（1.4.10 需再改），且与「单一常量」要求冲突（常量语义被埋进字符串）。
- 语义化版本库（packaging）—— 新增依赖，本项目无需（版本恒为三段）。
- **声明面也用「区间∪精确点」并集写法** —— **pip 单行无法表达并集**（只能写单区间或单版本），故声明面落为**单版本精确锁定 `duckdb==1.5.6` ＋ 注释承载放行集合**（M-2 审核意见①，2026-10-29）；连带影响：回滚不得用 `pip install -r requirements.txt`，须用显式版本命令。

---

## R-02 单一判定源如何跨到 shell 脚本

**Decision**：`duckdb_version_gate.py` 增加 `python -m quantstudio.pipeline.duckdb_version_gate --check` 入口，输出单行 `PASS <ver>` / `FAIL <ver>` / `ERROR <reason>`，退出码 `0 / 1 / 2`；`check_wal.ps1` 与 `check_wal.sh` 改为**调用该命令并消费退出码**，删除脚本内版本正则。

**Rationale**：`.ps1` / `.sh` 无法 `import` Python 包，只能通过进程边界共享判定；契约见 `contracts/version-gate-cli.md`。

**实施期形态调整（2026-10-29 留痕，推翻本决策的「`-m` 单一形态」预设）**：`scripts/check_wal_regression.sh` 把 `check_wal.sh` 复制到临时目录运行，此时项目包**不可导入** ⇒ 纯 `python -m quantstudio.pipeline.duckdb_version_gate --check` 必然 `ModuleNotFoundError` ⇒ 判定退化为 ERROR（fail-closed 本身正确）但**回归门 T2–T9 连锁变红**。故判定源下沉为**无包内依赖的单文件** `quantstudio/pipeline/duckdb_version_policy.py`：既可单文件直调（ps1/sh 薄壳），又可被 `duckdb_version_gate` 相对 import 复用；`gate.py` 降为薄封装并转发同一 CLI。**单源性质不变**（契约测试以 `is` 同一性机检）。

**Alternatives**：
- 三处各写正则 + 人工同步 —— 历史事故成因（声明与门禁漂移），不可机检，拒绝。
- 由 Python 生成脚本片段（构建期代码生成）—— 引入生成物与源的双份维护，比 CLI 更脆。
- 脚本内 `python -c "from … import is_allowed; …"` —— 可行但与 `-m` 等价，且行内代码难维护、易被 PS 引号规则破坏。

---

## R-03 测试契约如何引用单一源

**Decision**：`tests/test_duckdb_version_gate.py` 只 `from … import is_allowed, ALLOWED_RANGES`，参数化表由**被测函数**驱动；不放任何第二份版本列表字面量。

**Rationale**：契约的价值是「验证判定源」，若测试自持列表，则测试只是自证；引用同一源后，规则变更会同时反映到测试与门禁，漂移无处藏。

**Alternatives**：保留现参数化字面量并把 2.0.0 改掉——改动最小，但保留「两份规则」，不满足 FR-009 的机检一致性。

---

## R-04 PT-01 修复方向：**F2 已验可行，采为基线**；F1 降为回退

**Decision**（2026-10-29 09:02 依实验更新，**推翻本文件初稿的「F1 基线」先验假设**）：
**F2 = `TRUNCATE TABLE` + `INSERT … SELECT`（同一事务包覆）** 作为基线；**F1 = 重建同构主键** 作为回退方案（当 `TRUNCATE` 不可用、或 df 列集与表列集不一致需要改表结构时）。

**Rationale**：`docs/evidence/pt01-f2-truncate-20261029.md` 实测（32 列 × 50,000 行 × {带 PK, 无 PK} × {1.4.5, 1.5.6} × {TRUNCATE, DELETE, DROP+RENAME}）：

- **A 臂（TRUNCATE）两版本均 PK 1→1、`ON CONFLICT` 仍可用、无 FATAL/invalidated**，耗时 0.566 s（1.4.5）/ 0.586 s（1.5.6）——与现状 `DROP+RENAME` 同量级；
- **C 臂（现状 DROP+RENAME）两版本均 PK 1→0**，且事后 `ON CONFLICT` 直接抛 `BinderException`（缺陷复现，与段 A 一致）；
- 既然 `TRUNCATE` 不触发 ART 事故语形，就不必承担「取约束 + 重建 PK」的额外机制（F1 需要读 `duckdb_constraints()`、处理复合键、重建成本随表增大），F2 改动更小、语义更直白。

**Alternatives**：
- **F1 重建同构 PK**（初稿基线）—— 机制更复杂、需先取约束再重建；现降为回退位（`TRUNCATE` 不可用 / 需变更列集时）。
- 只加 F4 巡检不修根因 —— 巡检是止损不是修复，缺陷仍在（每轮覆盖仍丢 PK）。
- 直接改 `CREATE OR REPLACE TABLE`（docstring 所声称的形态）—— 未验证其在 1.5.6 上是否保留约束，且同样是「重建表」；不采。

**Implementation constraints（实验新增，已进 plan）**：
1. **列集一致前提**：`TRUNCATE+INSERT` 不能自动加列；实现须先比对 df 列集与表列集，不一致时走 DDL 路径（`ALTER ADD COLUMN` 或回退 `DROP+RENAME`）。
2. **事务包覆是硬要求**（`TRUNCATE` 与 `INSERT` 同一事务，否则中途失败留下空表）。
3. `write_passthrough_chunked` 的 staging + ledger 语义须保留，不能只换 SQL。
4. 文档缺陷登记：`writers.py:1573-1581` docstring 声称 `CREATE OR REPLACE`，实为 `DROP+RENAME`（`:1618-1619`），实施期一并更正。

---

## R-05 静态守卫落点

**Decision**：在 `config_lint.py` 的 `_lint_one_task()` 内加一条 **ERROR 级**规则：当 `task.get("passthrough")` 为真且 `task["table"]` 出现在 writer 的主键字典中 ⇒ 报错并指出 task 名与表名；复用文件内既有的 writer pk 字典（`_lint_schema_pk_consistency` 已在用，避免第二份主键表）。

**Rationale**：lint 在 daemon 加载配置后 fail-fast（`ConfigLintError`），属启动前拦截；当前 67 个 passthrough 任务与 25 张 canonical 带 PK 表交集为空 ⇒ 现状零告警，规则是**前瞻守卫**而非现状修复。

**Alternatives**：
- 运行时拦截（写入前查 PK）—— 过晚，覆盖已发生；且每次写都查约束，成本落在热路径。
- 只在文档里写「不要指向带 PK 表」—— 不可机检，配置一改即引爆。

---

## R-06 A-14 超阈的处置

**Decision**：**只登记风险 + 通知提示，不在本期改码**；把「改写入形态（`executemany` → `INSERT … SELECT`）」登记为独立纯性能型项，待用户批准后立项。

**Rationale**：实测（`docs/evidence/a14-executemany-scale-20261029.md`）1.5.6 相对 1.4.5 在 300 行 6.14×、1000 行 4.04×、5000 行 >90s 超时；绝对耗时在真实百量级 3.9s，不影响正确性。改写入形态超出本期授权（升级 + PT-01），零越权要求只登记不擅动。

**Alternatives**：
- 趁窗口顺手改成 `INSERT … SELECT` —— 引入未评审的性能改动，且属新范围；违反六步①（无方案无审核）。
- 完全忽略 —— 违背 FR-026「超阈须列入风险项并通知」。

---

## R-07 窗口末 daemon 拉起方式

**Decision**：`schtasks /Run /TN QS_DaemonForever`；**禁用** `Start-Process` / 直接 `python -m …` 拉起。

**Rationale**：现形态为计划任务（Boot + 手动 Run + IgnoreNew），手动 Run 与开机拉起走同一任务定义 ⇒ 环境变量、工作目录、实例令牌一致；`Start-Process` 拉起的是**另一种形态**，会造成「窗口内跑得好、重启后形态不同」的隐性偏差。IgnoreNew 亦防止重复实例。

**Alternatives**：
- `Start-Process`（快、可看到控制台）—— 形态不一致，拒绝。
- 等下次开机自动起 —— 窗口末无可观测的「已拉起」证据。

---

## R-08 治理条文替换稿（方案级，措辞待审）

**Decision**：`constitution.md:38` 整条替换为三要素：①**放行集合** `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`（以判定源为唯一实现）；②**升级判据**（距 EOL 90 天出方案 / 60 天进实施 / 30 天完成或书面接受风险）；③**到期复核装置**（EOL 日历守护 + 缺陷单关闭或新补丁发布后自动落复核提醒）。

**Rationale**：原句「不经手升级 duckdb 版本」是当初为规避缺陷而写的禁令，其副作用是「到期无判据、无复核」；替换为「集合 + 判据 + 装置」把禁令变成可执行政策。

**Alternatives**：追加例外条款 —— 条文自相矛盾；只改集合不写判据 —— 下次 EOL 重演。

---

## R-09 旧口径同步的边界

**Decision**：`AGENTS.md:207`（23 号铁律摘要）与 `knowledge/iron-rules/23-WAL残留处置.md:6` 的「duckdb 版本门必须 1.4.x」改为「必须落在放行集合内，且以判定源为唯一实现」；**只出 diff 与提交信息草稿**，随 M-2 审核呈批后落码。

**Rationale**：铁律层属跨域治理（quotation：治理规则 AGENTS.md 铁律级固化属跨域），执行会话不得自行改写；但不同步则知识库与新现实矛盾（21/22 号铁律要求条目与实态一致）。

**Alternatives**：本期不动 —— 留下「门禁已放宽、纪律仍要求 1.4.x」的直接冲突，下一位执行者会照纪律回退。
