# QMT 对齐知识库（QMT Alignment Knowledge Base）

> 建立：2026-10-16（D-33 件A，用户裁定 **B·对等双库**）
> 性质：**QMT（迅投知识库内置 Python）转换/回测管线**领域知识的专属条目库——与 PTrade 侧
> `QuantStudio-trading/vault/knowledge/ptrade-deploy/`（`PD-2026-*`）**结构对等**。
> 落位：主仓 `knowledge/qmt/`（QMT 管线在主仓；共享层只修主仓、trading 副本零本地改动）。
> 纪律：**指针不复制**（条目存结论＋证据件指针，原文留 `docs/evidence/`）；**git 是同步机制**（Obsidian 仅阅读层）。

## 1. 条目索引（首批 P1 级 6 条，2026-10-16 D-33 批 1 回填）

| 编号 | 症状摘要 | fix_layer | status | 对应 D |
|---|---|---|---|---|
| [QD-2026-0001](QD-2026-0001.md) | 临时重转脚本 UTF-8 落盘 → QMT 端 SyntaxError（产物物理编码契约） | portability_rule | closed | D-21 |
| [QD-2026-0002](QD-2026-0002.md) | 必填参数空串 → 平台静默返回空（无异常无告警） | wrapper | closed | D-22 |
| [QD-2026-0003](QD-2026-0003.md) | hist 财务日频全史帧 → 报告期折叠（行集回归本地 PIT 契约） | wrapper | closed | D-28 |
| [QD-2026-0004](QD-2026-0004.md) | 合约信息活表漂移 → PIT 冻结合约快照层 | wrapper | closed | D-30 |
| [QD-2026-0005](QD-2026-0005.md) | `realtime` 参数无 as-of 能力 → 静默空列表 | probe | closed | D-31 |
| [QD-2026-0006](QD-2026-0006.md) | 验收套件 exec 命名空间缺 `_QS_QMT_MODULE_HEADER` → NameError | matrix | closed | D-32 |

## 2. 条目 schema（1:1 对齐 PD；`fix_layer` 为 QMT 适配枚举）

```markdown
---
id: QD-2026-xxxx
symptom: "现象一句话（可检索）"
root_cause: "根因一句话"
fix_target: "修复落点：文件路径 + commit sha"
fix_layer: wrapper | portability_rule | matrix | probe | ledger   # 强制框架层
evidence: "验证证据指针（主仓路径）"
fixture: "复现固件/测试路径"
feedback:                              # 回哺方式（至少一项非空）
  contract_row: "registry/矩阵新增行描述或 null"
  gate_rule: "校验规则新增描述或 null"
  template_patch: "wrapper 模板改动描述或 null"
status: open | closed
closed_round: <n>                      # 闭环轮次：历史 D 条目按回填批次记（首批＝1），新条目按 D-33 批次递增
---
正文：复现步骤 / 影响面 / 讨论（散文允许，frontmatter 必须完整）
```

**`fix_layer` 枚举语义**：`wrapper`（wrapper 模板/代码）｜`portability_rule`（转换校验规则/门禁）｜
`matrix`（契约矩阵/哈希）｜`probe`（探针/形态库）｜`ledger`（台账/验收覆盖面）。

> **`fix_layer` 机械判定规则（②审裁定 2026-10-16，替代原「枚举内最接近」口径）**：按 `fix_target` 的
> **实际落点域**判定——**契约测试/矩阵域 ⇒ `matrix`**；纯台账登记类 ⇒ `ledger`；wrapper 模板/代码 ⇒ `wrapper`；
> 校验规则/门禁 ⇒ `portability_rule`；探针/形态库 ⇒ `probe`。
> **禁止扩枚举**（五枚举系与 PD schema 1:1 对齐的硬设计；扩值＝双库 schema 分叉）。
> 已按此规则落定：QD-2026-0006 落点域＝契约测试域 ⇒ `matrix`（原「待②审确认项」**已销**）。

## 3. 回哺消费（件 D，批 2）

预检读取器（`scripts/qmt_kb_preflight.py`，暂名）读本目录全部条目 frontmatter → 生成「已知坑清单」机读索引
→ 转换预检命中即 **WARN/BLOCK**（**判据边界先呈后落**——②审条件②，防预检误杀正常转换）。

## 4. 迁移节（trading 合并预案，写死）

未来 **QuantStudio-trading 建独立远程仓、QuantStudio 回测部分并入 trading** 时：
**本目录整体 `mv` 至 trading 仓 `vault/knowledge/qmt/`**，与 `ptrade-deploy/` **同仓对等**。
迁移成本＝一次目录 mv ＋ 本 README 相对链一行改——由**自包含设计**保证：单目录、无跨库硬链、条目自包含
（不依赖 `knowledge/` 上层文件即可读）。

## 5. 变更记录

| 日期 | 内容 |
|---|---|
| 2026-10-16 | 建库（D-33 件A 批 1）：README＋首批 P1 级 6 条 QD 条目（D-21/22/28/30/31/32）；schema 1:1 对齐 PD |
| 2026-10-16 | ②审裁定入册：**`fix_layer` 机械判定规则**（按 fix_target 落点域；禁止扩枚举）；QD-2026-0006 ⇒ `matrix` |
