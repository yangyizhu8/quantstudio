# DB 族知识条目库（数据库引擎与数据完整性域）

> **立族**：2026-10-29 总调度治理裁定；schema 枚举与⑲（duckdb 数据库升级执行会话）对齐定谳（回执全照准）。
> **登记**：`knowledge/kb-info.yaml` families 段 `id: db`；提案与对齐记录见
> `docs/handoff/db-family-schema-proposal-20261029.md` ＋ ⑲候选件 `docs/handoff/duckdb-kb-candidates-20261029.md`。

## 1. 适用范围（与既有族边界）

**纯数据库引擎行为与数据完整性教训**——判据：`fix_target` 落在写入路径/约束/巡检守卫/制品判据/版本升级面
（如 `quantstudio/pipeline/writers.py`、`check_*.py`、`config_lint.py`、引擎版本行为），且与 QMT/PTrade 转换无关。

- **vs qd 族**：qd＝QMT 转换管线域。既有 `QD-2026-0013`（duckdb 平台事实）**留 qd 不迁移**（历史条目零扰动）。
- **vs ops 族（候选）**：ops＝运维探针（daemon 可观测/告警通道）；数据库引擎≠进程运维，不重叠。

## 2. Schema（六必填字段，同 qd/pd）

`symptom / root_cause / fix_target / evidence / fixture / trigger`（trigger 两段式「复现条件；问法：<口头问法>」）。

### 2.1 fix_layer 五枚举（独立于 QD 六枚举/PD 九枚举；多落点 `+` 连接）

| 枚举 | 语义（落点类型） |
|---|---|
| `write_path` | 写入路径与形态（executemany／CTAS／TRUNCATE／批量写） |
| `constraint` | 约束与数据完整性（PK／唯一／指纹／ON CONFLICT 语义） |
| `lint_guard` | 前瞻守卫与巡检（config_lint ERROR／check 巡检／退出码语义） |
| `artifact_rule` | 制品判据与工程纪律（判据回扫／文档一致性） |
| `upgrade` | 版本升级语义（版本门／兼容矩阵／回归面／上游 issue） |

### 2.2 域编码（D1–D5，db 族独立定义）

| 域 | 名称 |
|---|---|
| D1 | 引擎行为与性能 |
| D2 | 数据完整性与写入语义 |
| D3 | 约束与 schema 契约 |
| D4 | 版本升级与兼容 |
| D5 | 工程纪律与制品一致性 |

## 3. 起草与机检（铁律 22）

```bash
python scripts/kb_draft.py --kb knowledge/db --symptom "<症状>" \
  --root-cause "<根因>" --evidence "<证据件>" --fix-target "<落点>" \
  --domain <D?> --fixture "<固件>"
```

机检 `kb_hook.validate_record` 按本族枚举校验，**不过不落盘**；条目 id `DB-YYYY-NNNN` 连续分配。

## 4. 条目一览（首批三条 · 2026-10-30 ⑲ M-4 入族）

| 条目 | 域 | fix_layer | 一句话症状 | 证据 / 固件 |
|---|---|---|---|---|
| `DB-2026-0001` | **D1** 引擎行为与性能 | `write_path+upgrade` | duckdb 1.5.6 的 `executemany` 批量写耗时较 1.4.5 劣化 4–6 倍（300 行 0.64→3.94s／1000 行 3.31→13.40s／5000 行 >90s 超时），而 `INSERT INTO … SELECT` 零拷贝路径不受影响 | 证据 `docs/evidence/a14-executemany-scale-20261029.md`｜固件 同左 |
| `DB-2026-0002` | **D3** 约束与 schema 契约 | `write_path+constraint+lint_guard` | passthrough 全量覆盖用「CTAS→DROP→RENAME」**静默清空目标表主键**（1→0，不报错），改 `TRUNCATE`+`INSERT` 后主键保持 1→1 且 `ON CONFLICT` 继续可用 | 证据 `docs/evidence/pt01-f2-truncate-20261029.md`｜固件 `tests/test_passthrough_pk_preservation.py` |
| `DB-2026-0003` | **D5** 工程纪律与制品一致性 | `artifact_rule` | 改码后制品里写死的判据（grep 模式／测试计数／退出码语义）悄悄失准——判据不再成立但不报错，读者会据此误判「已通过」 | 证据 `docs/handoff/duckdb-1.5-upgrade-governance-approval-20261029.md`｜固件 `specs/002-duckdb-1.5-upgrade/quickstart.md` |

## 5. 入族后逐条域定义复核（硬动作 · 2026-10-30 ⑲自纠后固化）

**每一条新条目落盘后，必须按本 README §2.2 的域定义逐条复核 `domain` 归属**（不得沿用起草前的语义表）。

- 依据：2026-10-30 ⑲自纠实例 —— `DB-2026-0001`（duckdb 1.5.x `executemany` **性能**劣化）按本 README 定义应为 **`D1` 引擎行为与性能**，而⑲入族时按自身回执件的语义表标为 `D2`（写入语义）⇒ **错位一格**；`kb_hook.validate_record` 机检**只验取值合法性**（`D2` ∈ vocab ⇒ PASS），**验不出语义归属错位**；
- 动作：①对照 §2.2 域定义逐条复核 `domain`；②不一致即改并登记 §变更记录（含错位事实）；③重跑 `kb_hook.validate_record` 复核；
- **schema 增强（与⑥协同推进中）**：族 schema 增「域语义对照校验项」，使语义错位可被机检提醒（命中 0 域 ⇒ 不落盘；命中多域 ⇒ notes 提醒）—— 草案见 `docs/handoff/duckdb-kb-domain-semantic-check-draft-20261030.md`。

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-10-30 | 建族：families 登记（五枚举+五域词+multi=true）＋本 README；负向自检过（safe_load OK／空族不误报）；首批三条候选由⑲ M-4 入族 |
| 2026-10-30 | ⑲ **语义自纠**：`DB-2026-0001` 的 domain 由 `D2` 改为 **`D1`** —— 按本 README §2.2 定义「D1＝引擎行为与性能」，该条为 `executemany` **性能**劣化；原映射用的是⑲回执件内的语义表（D2＝写入语义），与族 README 命名**错位一格**。**教训**：族 schema 机检只验取值合法性，**验不出语义归属错位** ⇒ 入族后须按族 README 域定义逐条复核（另两条 D3/D5 复核无误） |
| 2026-10-30 | 回填「条目一览」表（三条）：id／域／fix_layer／一句话症状／证据与固件指针 |
