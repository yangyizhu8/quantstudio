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

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-10-30 | 建族：families 登记（五枚举+五域词+multi=true）＋本 README；负向自检过（safe_load OK／空族不误报）；首批三条候选由⑲ M-4 入族 |
