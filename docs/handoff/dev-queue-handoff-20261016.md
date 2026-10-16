# 交接件 · dev 线队列（2026-10-16 夜 · 上下文交接）

接手须知：本件即完整状态。**状态在 git 与文档里，不在上一位会话的记忆里**；按以下三项顺序做即可，无需重建上下文。

## 0 已完成（勿重做）

- **条件 B 口径更正**：客户面 `docs/customer-notice-d1-d2-a0-20261016.md` §3.4 已改（commit `0ea425a`）；方案件 `docs/rebase-failfast-d1-d7-unified-design-20261013.md` L69/L77/L83 三处已改（commit `486882d`）。口径＝**默认 1% 阈值 ⇒ 行为有意变化（显式声明）**；回旧严格语义须显式配置阈值为 0。
- **批推**：`19a4b19` 已双推（D1+a0+D2+D3骨架+客户口径）；当前 local ahead=2（上述两笔更正待下批推）。
- D2 最小面已入运行体（daemon 载 `0b9cfca`）。

## 1 队列三项（输入均已到位）

### 1.1 D1-补1（条件 A）· 三组键落 qfq_deep_audit_item

目标：把 D1 的 `skipped_rows_no_factor` / `missing_factor_codes` / `whole_code_missing(_count)` **落盘至 `qfq_deep_audit_item`**（qfq_aux.db·**免库窗可读**）——D1 方案 §1 承诺的通道补完。
落点：`quantstudio/pipeline/sources/mcp_adapter.py`（D1 隔离分支与 a0 整码分支处）+ 复用 `quantstudio/pipeline/qfq_unit_diag.py` 的落表函数（既有通道·`fed7f98`）。
纪律：**共享核心文件** ⇒ 落盘前按定 scope 四步配方建快照；每次 edit 后 `git diff <file>` 自检；四件门（三组键写入/未触发路径零副作用/异常兜住/免库窗可读）。

### 1.2 条件 C · 夹具入库

底稿：`docs/handoff/d1-a0-d2-fixture-draft-20261016.py`（**其头注含三条入库要点**）。
目标：迁为 `tests/test_restore_missing_factor_d1_a0_d2.py`（D1/a0/D2 首次自动化固件）。
要点：夹具须用**有因子历史的码**（我此前三次夹具错误即因用了无历史码/坏占比超阈值）；断言须覆盖 D1 按行隔离、a0 整码登记、D2 顶格背压。

### 1.3 P8-001 apply（含 P7-001→P7-002）

移交件：`QuantStudio-trading\docs\factor-engine\FR015-016-dev-apply移交件-20261016.md` + 其 `patches\` 下**三 diff**。
执行顺序：**P7-001 → P7-002 → P8-001**；目标文件含 `trading-battle-back\因子框架\factor_engine\monitoring\pool_writer.py`。
步骤：**快照 → 按序 apply → 三文件终态指纹逐位比对**（如 `pool_writer.py=c7f9c12d…`，以移交件所载为准）**→ 三路实测（不采信其结论）→ 回传因子引擎三项归卷**。
（本轮受阻记录：我按文件名/内容检索 battle-back 未命中 `P8-001`；移交件实际在 **QuantStudio-trading** 侧，路径已由上给出。）

## 2 在挂其他项

- **D3 完整方案** ≤10-18（周日）呈②审：补全 §6 四项（`_security_range` 逐行核对／水位×断点交互／并批可行性／验收机检脚本）。骨架件 `docs/qfq-applying-d3-incremental-resume-design-20261016.md`（commit `e1768b3`）。
- **WAL 细化比对 + `(code,time)` 重复行分布**：候下个库窗（上一轮读到旧输出，未产出）。
- **候交易运维 D1 导出确认**（15:40 窗内其段未闭环）。

## 3 纪律提醒（本会话血换来的）

- **开停服窗必须同刻挂收口守护**；**RM HOLDER ⇒ 禁启**（不是记录）；**收窗清场**；**读数前先清旧输出**。
- **裁定改变判定语义时，先全仓 grep 清点同源矛盾再逐处改**（本轮四处同源矛盾差点只改一处）。
- **内容不在手上就停手索取，不按关键词臆造**（BOM 钩子规格、P8-001 补丁两次同型）。