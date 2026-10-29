# `db` 族 schema 确认回执（⑥案件 7a78e15 · 2026-10-30）

- **归属**：duckdb 数据库升级执行会话（图节点⑲）｜KB 族登记前置确认
- **背景**：总调度 2026-10-29 裁定「批准登记 `db` 族（数据库引擎与数据完整性域）」，登记动作由⑥会话在 `knowledge/kb-info.yaml` families 段执行；本会话回执 schema 三问，供⑥登记时对齐。

## 一、三问逐条回执

### 问①：`fix_layer` 五枚举 `write_path` / `constraint` / `lint_guard` / `artifact_rule` / `upgrade` 可否？

**回执：全部照准**（五枚举完整覆盖我方三条候选的落点类型，无冗余、无缺口）。三条候选的映射建议：

| 候选 | 主落点 | 附落点 | 建议 `fix_layer` |
|---|---|---|---|
| ① duckdb 1.5.x `executemany` 批量写劣化 | `quantstudio/pipeline/index_constituents_meta.py:125`（唯一 `executemany` 落点 → `index_constituents_snapshot_meta`） | 缺陷来源为版本升级（1.5.x） | `write_path` + `upgrade` |
| ② `DROP+RENAME` 静默清空主键 vs `TRUNCATE` 保留 | `quantstudio/pipeline/writers.py` 两分支（F2 改道） | 显式重建同构约束（`ADD PRIMARY KEY`）＋ 前瞻守卫 `config_lint.py:_lint_one_task` | `write_path` + `constraint` + `lint_guard` |
| ③ 实施期制品判据随改道失准 ⇒ 须回扫 | 制品判据（quickstart §0 检查项 / design §5 / 证据判据写法） | — | `artifact_rule` |

> `constraint` 单独成枚举是必要的：候选②的落点跨越「写入路径改法」与「约束重建机制」两层，若无 `constraint` 只能把两层混记，违反「族 schema 独立、不跨枚举混用」。

### 问②：`fix_layer_multi = true` 可否？

**回执：照准，且为必需**（不是"可以"，是"必须"）：候选②一个条目即跨三个落点（`write_path` + `constraint` + `lint_guard`），候选①跨两个（`write_path` + `upgrade`）⇒ `fix_layer_multi=false` 将导致这两条**无法如实落层**（只能择一记，等于丢失落点信息）。

### 问③：`domain` 五域词可否？

**回执：照准**。建议映射（域词命名与枚举由⑥最终定，我方给**语义建议**避免歧义）：

| 域语义 | 承载候选 |
|---|---|
| D1 引擎与版本治理 | 候选①的 `upgrade` 侧（放行集合 / 版本升级引入的行为面） |
| D2 写入语义与执行路径 | 候选①的 `write_path` 侧、候选②的 `write_path` 侧 |
| D3 数据完整性约束 | 候选②的 `constraint` 侧（主键/约束保持机制） |
| D4 守卫与巡检 | 候选②的 `lint_guard` 侧、候选③（回扫纪律） |
| D5 制品与文档一致性 | 候选③的 `artifact_rule` 侧 |

## 二、⑥登记时建议一并确认的技术参数（我方建议值）

| 字段 | 建议值 | 理由 |
|---|---|---|
| `id` | `db` | 总调度已裁定族名 |
| `dir` | `knowledge/db` | 与 `knowledge/qmt`（qd）、`knowledge/ptrade-deploy`（pd）同构 |
| `entry_glob` | `knowledge/db/DB-*.md` | 机检消费方按族分体检 |
| `id_pattern` | `DB-\d{4}-\d{4}` | 与 QD/PD 同形（族前缀 + 年 + 序号） |
| `required_fields` | 同 qd 六项：`symptom` / `root_cause` / `fix_target` / `evidence` / `fixture` / `trigger` | 三条候选均可满足（参数已备于 `docs/handoff/duckdb-kb-candidates-20261029.md`） |
| `domain_vocab` | `D1..D5`（语义见上表） | 与 `domains_vocab` 主表一致 |
| `fix_layer_enum` | `write_path` / `constraint` / `lint_guard` / `artifact_rule` / `upgrade` | 问①已照准 |
| `fix_layer_multi` | `true` | 问②已照准 |

## 三、登记后的动作（M-4）

1. ⑥完成 `knowledge/kb-info.yaml` families 段登记（**本会话不代改**，避免两线同改注册卡）；
2. 本会话按 `docs/handoff/duckdb-kb-candidates-20261029.md` §四用 `scripts/kb_draft.py --kb knowledge/db --domain <D?>` 逐条起草三条；
3. 起草器内置 `kb_hook.validate_record` **按族**校验（不过不落盘）；
4. 落盘后核对 `knowledge/db/DB-*.md` 三件、id 连续、`entry_glob` 命中，并回填 `kb-info.yaml` 的 `updated` 与 `entry_count_at`（登记时读数，仅供参考不作强断言）。

> 纪律提醒（铁律 22）：条目必须过族校验才落盘；`fix_layer` 须落在枚举内（跨枚举不得混用）；`fix_layer_multi` 为 `true` 时多落点以 `+` 连接。
