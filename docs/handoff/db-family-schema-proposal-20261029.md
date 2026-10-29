# `db` 族 schema 登记提案（⑥↔⑲对齐件）—— 对齐回执后登记 kb-info.yaml

> **依据**：总调度 2026-10-29 治理裁定（登记新族 `db`＝数据库引擎与数据完整性域）；⑲候选件 `docs/handoff/duckdb-kb-candidates-20261029.md`（三条 M-4 入族候选，参数已备齐）。
> **状态**：⏳ 待⑲会话确认三个对齐点（§3）→ 回执后⑥执行登记（families 段 + `knowledge/db/` 目录 + README）。
> **R1 开工留痕**：`知识反哺：[命中] QD-2026-0013（qd/D4/knowledge/qmt/QD-2026-0013.md，2026-10-29）`——既有 duckdb 平台事实条目（ON CONFLICT 写放大/RETURNING 物化等），**留 qd 族不迁移**（历史条目），db 族边界见 §4。

## 一、提案登记块（kb-info.yaml families 段追加，YAML 预览）

```yaml
  - id: db
    dir: "knowledge/db"
    entry_glob: "knowledge/db/DB-*.md"
    id_pattern: 'DB-\d{4}-\d{4}'
    fix_layer_enum: [write_path, constraint, lint_guard, artifact_rule, upgrade]
    fix_layer_multi: true          # ②号候选为 constraint+lint_guard 双落点
    domain_vocab: [D1, D2, D3, D4, D5]
    required_fields: [symptom, root_cause, fix_target, evidence, fixture, trigger]
    fix_layer_keywords:            # 自动判层初版（顺序命中即定，⑲可增补）
      write_path: ["executemany", "写入形态", "批量写", "TRUNCATE", "CTAS", "INSERT INTO"]
      constraint: ["主键", "PK", "约束", "唯一", "NOT NULL", "指纹", "ON CONFLICT"]
      lint_guard: ["lint", "巡检", "check_", "守卫", "退出码", "启动即报"]
      artifact_rule: ["判据", "制品", "回扫", "grep 模式", "quickstart", "呈批件"]
      upgrade: ["版本门", "升级", "兼容", "1.4", "1.5", "回归面", "duckdb#"]
    notes: "数据库引擎与数据完整性域（2026-10-29 总调度裁定立族）；schema 独立于 qd/pd，枚举与⑲候选件对齐"
```

## 二、枚举与域词设计依据（三条候选落点归纳）

| 枚举值 | 语义（落点类型） | 覆盖候选 |
|---|---|---|
| `write_path` | 写入路径与形态（executemany／CTAS／TRUNCATE／批量写） | ①（index_constituents_meta.py:125） |
| `constraint` | 约束与数据完整性（PK／唯一／指纹／ON CONFLICT 语义） | ②（writers.py F2 改道主因） |
| `lint_guard` | 前瞻守卫与巡检（config_lint ERROR／check 巡检／退出码语义） | ②（config_lint.py 前瞻守卫）＋③ 的 PK 巡检退出码面 |
| `artifact_rule` | 制品判据与工程纪律（判据回扫／文档一致性） | ③（4 处失准回扫） |
| `upgrade` | 版本升级语义（版本门／兼容矩阵／回归面／上游 issue） | ①（1.4→1.5 劣化面；duckdb#23645）＋将来升级教训 |

**domain_vocab（db 族 D1–D5，独立定义）**：D1 引擎行为与性能｜D2 数据完整性与写入语义｜D3 约束与 schema 契约｜D4 版本升级与兼容｜D5 工程纪律与制品一致性。
（对⑲三条 domain 建议的映射：①→D1、②→D2〔D3 属性〕、③→D5——候选件「若无匹配取最接近并 notes 注明」不再需要，五域全覆盖。）

## 三、请⑲确认的三个对齐点（回执即登记）

| # | 对齐点 | 提案值 | 备选 |
|---|---|---|---|
| 1 | fix_layer_enum 五值 | `write_path / constraint / lint_guard / artifact_rule / upgrade` | 增删改值（如合并 lint_guard 入 constraint） |
| 2 | fix_layer_multi | `true`（②号双落点） | false＋强制单落点 |
| 3 | domain_vocab 五域 | 上表 D1–D5 定义 | 按⑲工作域改词 |

## 四、与既有族的边界（防混挂）

- **vs qd 族**：qd＝QMT 转换管线域（六枚举）。既有 QD-2026-0013（duckdb 平台事实）**留 qd 不迁移**（历史条目零扰动）；今后**纯数据库引擎/数据完整性**教训入 db（判据：fix_target 落 `writers.py`/`check_*`/`config_lint`/引擎行为且与 QMT 转换无关）。
- **vs ops 族（候选）**：ops＝运维探针（daemon 可观测/告警通道，2 条候选等第 3 条）；db 与 ops 不重叠（数据库≠进程运维）。
- **铁律 22 口径**：新族登记后，`kb_draft.py --kb knowledge/db` 即可用；三条候选按⑲件 §四序列 M-4 入族，机检不过不落盘。

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-10-29 | 建件：登记块 YAML 预览＋枚举/域词设计依据＋三对齐点呈⑲＋族边界；待回执后执行登记 |
