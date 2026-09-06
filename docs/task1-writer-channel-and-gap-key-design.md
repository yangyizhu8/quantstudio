# 任务一合并方案包：#16 写入通道契约显式化 + gap 登记键形态统一（2026-09-05）

- **流水线**：Step 1 合并方案包（两独立修复项一包送审，落点/验收分别钉死）→ 总调度复核 → 实施
- **铁律归依**：框架问题立即解决（两项均已实证，非臆测）；纯增益（现状锁定断言迁移为修复后语义）
- **回退点**：逐项实施前 stash create + store 持久化

---

## 修复项 A：#16 族——写入通道契约显式化

### A1. 实测定性（修正旧"双写入点缺陷"认知）

writer.write 调用点全量实测 = **2 处**，均为**合法设计通道**：

| 通道 | 落点 | 语义 |
|---|---|---|
| **stamp 通道** | `_stamp_and_write`（L2587，内部 L2611 writer.write） | 增量 upsert 主通道：QFQ 自检防线①（L2609）+ data_source 标签 + snapshot 过滤 + index_constituents snapshot_meta 契约（L2612-2622）——9 处消费 |
| **passthrough 通道** | L1076 `writer.write(raw_df, table, batch_id, passthrough=True)` | 全量覆盖语义（CREATE OR REPLACE）：不走 aligner/validator upsert、不推水位——d960d33 8/3 引入的合法基础设施 |

**重定性**：chokepoint 断言"全管线仅 1 处 writer.write"期望过时——双通道是 d960d33 起的设计。**#16 族真实价值 = 通道契约显式化**：禁止第三条裸 write 路径（未来新增写入必须走两通道之一，防绕过 QFQ 自检/审计/snapshot 契约）。

### A2. 修复方案

1. **契约测试锁定**（`tests/test_writer_channel_contract.py` 新）：
   - AST 扫描 `daemon.py`：`writer.write(` 调用点必须 ⊆ {L1076 passthrough 上下文, `_stamp_and_write` 方法体}——出现第三处即 FAIL（报通道违规）；
   - `_stamp_and_write` 必须含 `_qfq_invariant_after_align` 调用（防线①不被绕过）；
   - passthrough 通道必须含 "CREATE OR REPLACE" 语义注释/不推水位的断言锚（`source_watermark` 不推进）。
2. **命名显式化**（轻量）：L1076 处注释升级为通道契约声明（PASSTHROUGH-CHANNEL：全量覆盖/无水位/独立语义）；
3. **台账 #16 族关单**：以"契约测试 + 通道声明"关单（修复+回归证据标准满足——契约测试即防复发回归）。

### A3. 验收（A 线）

- A-T1 契约测试 PASS（两合法通道锁定）；
- A-T2 人为注入第三处 writer.write（测试内 mock 场景）→ 契约测试 FAIL（防复发有效性）；
- A-T3 既有写入行为零变化（writer.write 调用点零改动——只加测试与注释）。

---

## 修复项 B：gap 登记键形态统一（income 简名 vs 全名失配）

### B1. 实测定性

- **现象**：income ghost 场景二次请求未短路（每次 1 平台调用），但二次告警未重复（select_fields 层已登记命中）；
- **机理**：`_qs_fund_select_fields` mark_gap 登记 `table='income_statement'`（全名）；wrapper 短路查询 `_qs_gf_gap_shortcut` 在 **range 路由层**以**简名**（'income'，见 L2347 gap 遍历元组形态）或不同 table 键查询 → 键不匹配 → 短路未命中；
- **影响面**：无 g/有 g 均可能受影响（键失配与 g 无关）；生产 wsgm10v2 未暴露因其消费字段集不同。

### B2. 修复方案

1. **键形态统一**：定位 range 路由层短路查询的实际 table 键（实施第一步：插桩打印 `_qs_gf_gap_shortcut(table, ...)` 的 table 实参 vs mark_gap 的 table 实参——**一次运行取证钉死失配对**）；
2. **归一函数**：新增 `_qs_gap_table_key(table)`（全名↔简名映射：income↔income_statement / balance↔balance_statement / valuation 恒等），mark_gap 与 gap_shortcut 两端统一经此函数归一；
3. **契约断言迁移**：`test_p10_wrapper_gap_shortcut_single_alarm` 从"现状锁定（每请求 1 次调用）"迁移为"修复后语义（二次短路 0 调用）"——原现状锁定断言保留为回归对照注释。

### B3. 验收（B 线）

- B-T1 实测失配对钉死（插桩取证入 evidence）；
- B-T2 归一后：income ghost 二次请求 0 平台调用（短路命中）+ 告警仍 1 次不重复；
- B-T3 纯增益：valuation/eps 等其他表场景调用次数不变（对照断言）；
- B-T4 fund_matrix 16/16 + p10 21/21 保持绿（gap 修复不触其他契约）。

---

## 4. 涉及文件

| 文件 | 修复项 |
|---|---|
| `quantstudio/pipeline/daemon.py` | A2 通道契约声明注释（L1076）——零逻辑改动 |
| `quantstudio/strategy_compiler/source_import.py` | B2 归一函数 + 两端接入（**共享核心文件**——叠加显式申报+精确 add） |
| `tests/test_writer_channel_contract.py`（新） | A2 契约测试 |
| `tests/test_ptrade_contract_compliance.py` | B-T2 断言迁移 |
| 台账/证据 | #16 族关单记录 + gap 修复 evidence |

## 5. 实施顺序

1. **B 先行**（插桩取证 → 归一修复 → B-T1~T4）——B 有行为改动需先验证；
2. **A 后行**（零逻辑改动：契约测试+注释）——纯增益随 B 同 commit；
3. 同域契约测试联动断言（A-T1/A-T2 + B-T2 迁移）一次性交付；
4. 共享核心文件纪律：stash create+**store** 持久化回退点、精确清单 add、叠加申报（source_import 当前工作树含探针 v2 = 他线已提交态，本次 diff 仅 B2 归一改动——需先核对该文件零 M 再动手）。
