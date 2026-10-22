# 通用条目 schema 约定 v1（KMAP-M0 · 2026-10-22）

> **定位**：任何项目接入 KB 闭环（KMAP）时，条目族的 schema 以本约定为**共同底座**；
> 族差异（id 前缀、`fix_layer` 枚举）由各族 README **独立定谳**，禁止跨族强并。
> **机检映射**：QD 式族 → `scripts/kb_preflight.py::validate_entry`；
> PD 式族 → `ptrade-deploy/README.md` 定稿规则（prepush `kb_hook` 内联实现）；
> 通用钩子 → 推送范围 diff 内的条目件逐件体检（WARN 起步）。

## 1. 通用三字段（跨族强制，机检非空）

| 字段 | 类型 | 约束 | 为什么 |
|---|---|---|---|
| `domain` | `D1`–`D5`（或族自定义词表，须在族 README 声明） | 必填 | 检索与月度复核的分组轴；与 `fix_layer` **正交**（域按生效面判，层按落点判） |
| `trigger` | 字符串，非空 | **必填，一句话可判定是否命中，且含一句「问法」措辞** | R0 基线的检索桥：把「开发者会怎么问」写进条目，让关键词检索命中「不含 API 名的问法」 |
| `recurrence` | 整数 ≥0 | 必填，默认 0 | R 指标（同族复发次数）数据源；R=0 连续 3 轮＝收敛判据 |

### 1.1 `trigger` 问法口径（v1 新增·M0 定稿）

```yaml
trigger: "最小复现触发条件（技术措辞）；问法：<开发者口头会怎么问，可不含 API 名>"
```

- 前半段＝**复现条件**（什么情况下命中这个坑，供机检与根因定位）；
- 后半段 `问法：`＝**检索桥**（开发者/agent 开工时嘴上的问题），例：
  - 复现条件：`get_stock_list_in_sector 传入第二实参 realtime（毫秒时间戳）`
  - 问法：`板块或成份股的历史时点（as-of/PIT）怎么取`
- 两句以 `；问法：` 连接，均在同一个 YAML 字符串内（不新增字段，向后兼容）。

## 2. 必填字段清单（条目最小完整集）

`id` / `symptom` / `root_cause` / `fix_target` / `fix_layer` / `domain` / `trigger` / `recurrence` /
`evidence` / `fixture` / `feedback` / `status`（`closed` 时须有 `closed_round`）。

- `symptom`：可检索的现象一句话（**写现象不写结论**，供 grep 与人读）；
- `fix_target`：文件路径＋行号或描述＋（已修则）commit sha；
- `feedback`：`contract_row` / `gate_rule` / `template_patch` 至少一项非空（字面 `null` 视为空）；
- `evidence` / `fixture`：证据指针与复现固件路径（禁空）。

## 3. 族定谳（各知识族 schema 独立）

| 项 | 规则 |
|---|---|
| `id` 前缀 | 族自定（如 `QD-YYYY-NNNN` / `PD-YYYY-NNNN` / `XX-YYYY-NNNN`），**跨族不混号** |
| `fix_layer` 枚举 | **族独立定谳**：QD 六枚举（wrapper｜portability_rule｜matrix｜probe｜ledger｜engine）；PD 九枚举（contract_table｜transformer｜gate｜template｜kernel｜profiler｜config｜equiv｜engine）；新族自定并在 README 声明 |
| 多落点 | 允许 `+` 连接（如 `profiler+config+transformer+equiv`），**各组件须在枚举内**，禁止压成单词（压词＝丢语义） |
| 括注后缀 | 允许 `gate（gates.py）` 形态——机检时剥离全角括注再比对枚举 |
| 族间同步 | 某族扩枚举 ⇒ **须在他族同步同值**（防双库 schema 分叉），同步状态在 README 显式记录 |

## 4. 落盘与机检流程

1. 起草：`python scripts/kb_draft.py --kb knowledge/<族> --symptom ... --evidence ... --fix-target ... --domain D3`
   （`fix_layer` 缺省自动判并**打印判据**；`validate_entry` 不过则拒绝落盘）；
2. 提交前：推送范围 diff 内的条目件自动过族校验（`kb_hook`，WARN 起步）；
3. 月度：`recurrence` 记账 ＋ R0 题集复测（见 `kb-golden-questions-v1.md`）。

## 5. 示例（两族对照）

```yaml
# QD 式（六枚举族）
id: QD-2026-0005
fix_layer: portability_rule
domain: D2
trigger: "get_stock_list_in_sector 传入第二实参 realtime（毫秒时间戳）；问法：板块或成份股的历史时点（as-of/PIT）怎么取"
recurrence: 0
```

```yaml
# PD 式（九枚举族，多落点 + 括注）
id: PD-2026-0008
fix_layer: "gate（gates.py）"
domain: D2
trigger: "产物对局部变量调用方法（如 history.keys()），跑 P5 未知 API 门禁；问法：P5 把 history.keys() 这类局部方法调用报成未知 API"
recurrence: 0
```

## 变更记录

| 日期 | 内容 | 影响范围 |
|---|---|---|
| 2026-10-22 | v1 建立（KMAP-M0）：三字段通用约定＋`trigger` 问法口径（2 段式，单字段内，向后兼容）＋族定谳规则＋机检映射 | 全部接入族；QuantStudio 19 条存量条目已按 v1 补问法 |
