# QMT 对齐知识库（QMT Alignment Knowledge Base）

> 建立：2026-10-16（D-33 件A，用户裁定 **B·对等双库**）
> 性质：**QMT（迅投知识库内置 Python）转换/回测管线**领域知识的专属条目库——与 PTrade 侧
> `knowledge/ptrade-deploy/`（`PD-2026-*`，2026-10-22 起已迁入主仓、与本目录同级；原 trading `vault/` 已解散）**结构对等**。
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
fix_layer: wrapper | portability_rule | matrix | probe | ledger | engine   # 强制框架层（engine 为 2026-10-22 扩容）
domain: D1 | D2 | D3 | D4 | D5          # 域编码（§2.1 判域规则；必填）
trigger: "最小复现触发条件（非空；一句话可判定是否命中）"
recurrence: 0                            # 同族复发次数（整数；R 指标数据源，默认 0）
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

**`fix_layer` 枚举语义（六枚举，2026-10-22 扩 `engine`）**：`wrapper`（wrapper 模板/代码）｜`portability_rule`（转换校验规则/门禁）｜
`matrix`（契约矩阵/哈希）｜`probe`（探针/形态库）｜`ledger`（台账/验收覆盖面）｜
**`engine`（宿主引擎/本地回测内核——`quantstudio/backtest/*` 等宿主侧代码；2026-10-22 总调度令扩容）**。

> **`fix_layer` 机械判定规则（②审裁定 2026-10-16，替代原「枚举内最接近」口径）**：按 `fix_target` 的
> **实际落点域**判定——**契约测试/矩阵域 ⇒ `matrix`**；纯台账登记类 ⇒ `ledger`；wrapper 模板/代码 ⇒ `wrapper`；
> 校验规则/门禁 ⇒ `portability_rule`；探针/形态库 ⇒ `probe`。
> **扩枚举纪律（2026-10-22 修订，取代原「禁止扩枚举」）**：原五枚举系与 PD schema **1:1 对齐**的硬设计；
> **扩容即打破 1:1 ⇒ 须 PD 库同步同值**，否则双库 schema 分叉。**本次扩 `engine` 的 PD 侧同步状态：已同步（2026-10-22）**
> （PD 族 schema 已增 `engine`，见 `knowledge/ptrade-deploy/README.md`；`validate_entry` 枚举集与草稿器判据同批同步）。
> 已按此规则落定：QD-2026-0006 落点域＝契约测试域 ⇒ `matrix`（原「待②审确认项」**已销**）；
> **QD-2026-0010 落点域＝宿主引擎（`backtest_engine.py` 印花税分期间）⇒ `engine`**（2026-10-22 由权宜 `probe` 正式改判）。

## 2.1 域编码（D1–D5，A1 · 2026-10-21）

> 用途：`--domain` 过滤与月度人工复核的分组轴。**与 `fix_layer` 正交**——域按 `fix_target` 的**生效面**判（改哪个面的行为），
> 层按落点文件类型判（改的是哪种工件）；两套维度不得互相替代。

| 域 | 名称 | 判定含义（生效面） |
|---|---|---|
| D1 | 对齐 | 平台口径差异 / 字段语义映射 / 能力边界登记（`registry` 锚点、字段语义、探针定谳） |
| D2 | 转换 | 转换产物生成与门禁（reconvert、`portability_rules`、`orchestrator` 写盘、契约矩阵 / verify） |
| D3 | 运维与缺陷（含 BUG 修复） | QMT 产物内**运行时注入 API**：字段映射 / 空值与哨兵语义 / 资金持仓视图 / 取数适配 / 撮合口径 |
| D4 | 性能 | 性能优化与等价性（逐位一致门） |
| D5 | 扩展与研发 | 新功能 / 新 API 面 / 脚手架 |

**判域规则（落点优先，顺序命中即定，前条命中不再下看）**：

1. `fix_target` 指向**转换 / 门禁 / 产物文本生成**（`scripts/qmt_reconvert_six.py`、`portability_rules.py`、
   `orchestrator.py::_write_qmt_product`、矩阵与 verify）⇒ **D2**；
2. 指向**产物内注入 API 的运行时行为**（`_QS_QMT_*_EXT` 模板串内的 `_qs_*` / `get_*` / `_Qmt*View`：字段映射、
   空串与哨兵语义、资金持仓视图、取数适配、撮合口径）⇒ **D3**；
3. 只改**口径 / 台账登记**（`knowledge/registry.md` 锚点行、字段语义登记、能力边界定谳）⇒ **D1**；
4. 性能优化与等价性 ⇒ **D4**；新功能 / 新 API 面 / 脚手架 ⇒ **D5**。
5. `fix_layer` **仅作辅助校验**（如 `portability_rule` 通常落 D2、`probe` 通常落 D1/D3），**不作判域主依据**。

> **wrapper 归属澄清（2026-10-21）**：wrapper 模板的**载体**在 `strategy_compiler/`，但其**生效面**是产物运行时的注入 API
> ⇒ 归 **D3**，不归 D2；只有「转换 / 门禁 / 产物文本生成」这一层归 D2。不这样切开，九条中 5 条会同时踩 D2/D3，域就失去区分度。

**九条落域（现行 9 条，按上述规则一次性判定）**：

| 条目 | fix_layer | 落点生效面 | 域 |
|---|---|---|---|
| [QD-2026-0001](QD-2026-0001.md) | portability_rule | 产物落盘编码契约 + 写前拦截（reconvert / orchestrator） | **D2** |
| [QD-2026-0002](QD-2026-0002.md) | wrapper | 必填参数空串 → 取数适配运行时语义 | **D3** |
| [QD-2026-0003](QD-2026-0003.md) | wrapper | hist 财务帧报告期折叠（运行时取数适配） | **D3** |
| [QD-2026-0004](QD-2026-0004.md) | wrapper | PIT 冻结合约快照层（运行时取数适配） | **D3** |
| [QD-2026-0005](QD-2026-0005.md) | probe | `realtime` 无 as-of ＝ 平台能力边界登记 | **D1** |
| [QD-2026-0006](QD-2026-0006.md) | matrix | 验收套件 exec 命名空间（契约 / verify 门禁域） | **D2** |
| [QD-2026-0007](QD-2026-0007.md) | wrapper | `_QS_QMT_FUND_FIELD_MAP` 表行（运行时字段映射） | **D3** |
| [QD-2026-0008](QD-2026-0008.md) | wrapper | 柜台字段语义（空串 / 哨兵 / 交易所后缀）＝口径对齐登记 | **D1** |
| [QD-2026-0009](QD-2026-0009.md) | wrapper | 消费面调用契约（钩子时机 / 显式 C / 零视图 / 码形态） | **D3** |

**未标注 `domain` 的条目行为（写死，A3 实现判据）**：**不匹配任何 `--domain` 过滤**，且读取器必须输出
`warn`（禁止静默归入某域或静默丢弃）——历史条目在补字段前一律走此支。

## 3. 回哺消费（件 D，批 2）

预检读取器（`scripts/qmt_kb_preflight.py`，暂名）读本目录全部条目 frontmatter → 生成「已知坑清单」机读索引
→ 转换预检命中即 **WARN/BLOCK**（**判据边界先呈后落**——②审条件②，防预检误杀正常转换）。

## 4. 迁移节（trading 合并预案，写死）

未来 **QuantStudio-trading 建独立远程仓、QuantStudio 回测部分并入 trading** 时：
**本目录与 `knowledge/ptrade-deploy/` 一并 `mv` 至 trading 仓 `knowledge/`**（`vault/` 已于 2026-10-22 解散，不再作为落点），二者同仓对等。
迁移成本＝一次目录 mv ＋ 本 README 相对链一行改——由**自包含设计**保证：单目录、无跨库硬链、条目自包含
（不依赖 `knowledge/` 上层文件即可读）。

## 5. 变更记录

| 日期 | 内容 |
|---|---|
| 2026-10-16 | 建库（D-33 件A 批 1）：README＋首批 P1 级 6 条 QD 条目（D-21/22/28/30/31/32）；schema 1:1 对齐 PD |
| 2026-10-16 | ②审裁定入册：**`fix_layer` 机械判定规则**（按 fix_target 落点域；禁止扩枚举）；QD-2026-0006 ⇒ `matrix` |
| 2026-10-22 | **总调度令扩 `fix_layer` 六枚举（＋`engine`）**：host 引擎/本地回测内核类缺陷无所属层（QD-2026-0010 权宜 `probe` 暴露）；同时把「禁止扩枚举」改写为「扩容须 PD 库同步同值」——**PD 侧同步状态＝待办**（未同步前 `engine` 为 QMT 库单侧扩容）；QD-2026-0010 ⇒ `engine` |
| 2026-10-21 | **A1 域编码表落位**：新增 §2.1 五域（D1 对齐／D2 转换／D3 运维与缺陷／D4 性能／D5 扩展与研发）＋落点优先判域规则＋wrapper 归属澄清（生效面判 D3）＋九条一次性落域＋未标注 domain 行为写死 |
