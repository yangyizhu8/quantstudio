# <族名> 知识库（<项目名>）

> 本族 schema 依《通用条目 schema 约定 v1》为底座（`docs/templates/kb-entry-schema-v1.md`），
> 族差异在此**独立定谳**：id 前缀、`fix_layer` 枚举、是否允许多落点。
> 机检：`scripts/kb_hook.py`（推送分族体检）／`scripts/kb_draft.py`（草稿器）。

## 1. 条目 schema（frontmatter）

```yaml
---
id: <ID前缀>-YYYY-NNNN
symptom: "现象一句话（可检索；写现象不写结论）"
root_cause: "根因一句话"
fix_target: "修复落点：文件路径:行（已修则加 commit sha）"
fix_layer: <族枚举值>              # 枚举见 §2
domain: D1 | D2 | D3 | D4 | D5     # 域编码（与 fix_layer 正交）
trigger: "最小复现触发条件；问法：<开发者口头会怎么问，可不含 API 名>"
recurrence: 0                      # 同族复发次数（R 指标数据源）
evidence: "验证证据指针"
fixture: "复现固件/测试路径"
feedback:                          # 至少一项非空
  contract_row: "<契约表新增行描述或 null>"
  gate_rule: "<门禁规则新增描述或 null>"
  template_patch: "<模板改动描述或 null>"
status: open | closed
closed_round: null                 # closed 时填轮次号
---
```

## 2. `fix_layer` 枚举（本族定谳）

| 值 | 含义 | 典型落点 |
|---|---|---|
| `<值1>` | <含义> | <文件/模块> |
| `<值2>` | <含义> | <文件/模块> |

- **多落点**：<允许/不允许>以 `+` 连接（`true` 时各组件须在枚举内，禁止压成单词）。
- **括注后缀**：允许 `gate（gates.py）` 形态（机检剥离全角括注再比对）。

## 3. 域编码（D1–D5）

| 域 | 语义 |
|---|---|
| D1 | 对齐/口径差异（平台语义、字段映射、能力边界） |
| D2 | 转换/门禁（产物生成、校验、模板、契约表） |
| D3 | 运维与缺陷（运行时、静默失效、链路断裂） |
| D4 | 性能与资源 |
| D5 | 扩展与集成（新数据源、新平台、外部系统） |

**落点优先规则**：域按 `fix_target` 的**生效面**判（改哪个面的行为），`fix_layer` 按落点判；
两者正交，禁止用层反推域。

## 4. 登记纪律

- 条目入库与「框架修复 + 验证证据 + 复现固件」四同步；
- 每轮登记 `recurrence`（同族复发）；R=0 连续 3 轮视为收敛；
- 同坑复发 R>0 ⇒ 先修管线（回哺链）再修漏洞；
- 条目改动后跑 R0 题集复测（防「库在检索哑」）。

## 变更记录

| 日期 | 内容 |
|---|---|
| YYYY-MM-DD | 建族：schema 定稿（枚举 N 项）、域编码、登记纪律 |
