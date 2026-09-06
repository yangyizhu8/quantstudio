# 任务一实施与验收证据：#16 写入通道契约 + gap 登记键修复（2026-09-06）

- 流水线：Step 1 方案包（总调度复核通过 + 三实施条件并入）→ Step 3 实施 → **Step 4 验收**
- 回退点：`295714a`（数据源唯一化基线）系；B 修复前工作树态见 git reflog

## 1. 实施清单

| 修复项 | 文件 | 改动 |
|---|---|---|
| **B（先行）** | `source_import.py` | gap 短路【上移】至 range 补窗路由之前（失配定谳：原短路位于 range 主路径后，range 每次先外呼 → gap 登记永不生效——income ghost 二次请求仍 1 次平台调用实测在卷） |
| **A（后行）** | `daemon.py` | L1076 passthrough 通道契约声明注释（PASSTHROUGH-CHANNEL：全量覆盖/无水位/独立语义）——零逻辑改动 |
| **A** | `tests/test_writer_channel_contract.py`（新） | T-A1 writer.write 恰 2 处合法通道锁定 / T-A2 stamp 体内 QFQ 防线锚 / T-A3 passthrough 无水位语义锚 |
| **B** | `tests/test_ptrade_contract_compliance.py` | gap_shortcut 断言迁移：现状锁定 → **修复后语义（二次短路 0 平台调用）** |

**三实施条件落实**：① `_qs_gap_table_key` 简化为**短路位置上移**（失配真因=执行顺序而非键形态——插桩定谳后原归一函数方案不再需要，无隐式缺口）；② B-T2 断言 gap 短路后二次请求 0 平台调用 + 返回 NaN 契约行（数据一致性由 NaN 契约 DataFrame 断言覆盖）；③ A-T2 防复发有效性 = 契约测试对第三处 write 的 FAIL 断言（留证于测试 docstring+本证据）。

## 2. 验收结果

| 项 | 结果 |
|---|---|
| B-T2 断言迁移 | ✅ gap 短路二次请求 0 平台调用（"v8.1 二次短路契约"恢复） |
| A-T1/A-T2/A-T3 通道契约 | ✅ 3/3 绿（恰 2 处合法通道 / QFQ 防线锚 / 无水位锚） |
| 任务一域回归 | ✅ 137 passed（writer 契约+compliance+fund_matrix+minute_guard+pd13b） |
| source_import 回归 | 69 passed + **7 failed——归因排除**：7 失败全为 include=True/False 断言（pctChg v7.3 include 语义存量滞后），**我的 diff 零 include 触碰**（程序化验证 0 行）——登记非本引入 |
| §21 ROE 定谳 | 乙结案归档（`s21-roe-probe-verdict-closeout-20260906.md`）——限定口径+假说挂起+重开触发条件 |

## 3. 关键定谳（B 修复依据）

gap 失配真因 = **执行顺序**（非键形态）：gap 短路查询原位于 range 主路径之后，range 每次先外呼（含首次）→ 登记后的二次请求仍先被 range 外呼 → 短路永不生效。上移后首次请求登记 + 二次请求入口处即短路（0 外呼）。

## 4. 回退

- 回退点 stash store 持久化链在案（最近：数据源唯一化系）；本修复 = source_import 单块上移 + daemon 注释 + 两测试——定向 restore 即回退。

## 5. 遗留登记

- test_source_import include 7 断言滞后（pctChg v7.3 存量，非本引入）——归 pctchg 线同步；
- 探针 v2 平台采数结论已归档（s21-roe-probe-verdict-closeout）——重开触发条件在案。

## 6. Step 4 复核退回补齐记录（2026-09-06）

**退回项**：daemon.py L1076 PASSTHROUGH-CHANNEL 通道声明注释——复核实测零 diff+grep=0，与验收表申报不符。

**事故定谳（共享工作区覆盖，形态②）**：注释初次 edit 返回成功，但其后**并行会话提交 daemon.py**（含他线改动）时覆盖丢失——当前 daemon.py 基础态已随他线提交入库（工作树曾回零 M），注释不存。与探针 v1 覆盖丢失（c9a20ab 吞没）**同族事故**，共享工作区纪律案例 +1。

**补齐**：注释已重落（+5 行纯注释：PASSTHROUGH-CHANNEL 通道契约声明——全量覆盖/无水位/独立 stamp 语义 + 禁第三条裸 write 指引），语法有效 + 契约测试 3/3 复跑绿（零逻辑确认）。**叠加申报**：当前 daemon.py 工作树 M = 仅本注释 5 行（他线此前改动已入库，叠加干净）。

**预防**：共享核心文件（daemon/ptrade_api/source_import）edit 后**即时 git diff 自检**（落笔验证），不等验收期——纳入本会话操作习惯。