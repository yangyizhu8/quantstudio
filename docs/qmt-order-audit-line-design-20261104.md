# QMT 产物订单审计行补齐 · 设计方案（六步第①步，候②审）

> 授权：用户 2026-11-04 21:5x 三裁之①「订单审计行修复批照准——唯一硬阻塞，走完整六步（落点=_QS_QMT_ORDER_EXT 模板串加审计行·判型新增检测型·SHA16 换锚新旧对照留痕·168 测试面+test_qmt_align_check 新用例）——方案件即起（总调度②审后实施批，⑤呈用户）」。
> 问题发现：`docs/evidence/qmt-fidelity-step1-20261104.md` §二（Step 1 盘点：产物 18 个审计打印点无一订单事件）。
> 知识反哺：`知识反哺：[命中] QD-2026-0017（qd/D1，2026-11-03）`——交付完整性族第三例（设计前提「审计事件行含订单」与实现间缺口）；`知识反哺：[无命中] 订单审计行族（主库 knowledge/qd 无 wrapper 订单审计条目），2026-11-04`。
> R4 计划：实施轮落 `QD-2026-0020`（订单审计行族：症状/根因/落点/冻结词法/回哺）。

---

## 一、问题定义

**现象**：转换产物下单链零审计打印——`_qs_order`（`source_import_qmt.py:856`）→ `_qs_passorder`（`:850`）→ `passorder`（`:852-853`）全程无 print；`_qs_order_target_value`（`:875`）亦无。
**后果**：对照器 `scripts/qmt_align_check.py`（冻结词法 `date/code/side|op/qty/price`，`op=23/24→buy/sell`，`:111`）的订单侧输入在真机日志无产出源 ⇒ 保真对照（Step 3）五维中订单/调仓日/持仓三维无米下锅。
**下单路径全集**（grep 实证，`_qs_passorder(` 共 4 个调用点）：

| # | 调用点 | 语义 | 语境 |
|---|---|---|---|
| 1 | `:866` `_qs_order` 买入 | `order(code, +amount)` 显式股数 | 全语境 |
| 2 | `:872` `_qs_order` 卖出 | clamp 至 closeable | 全语境 |
| 3 | `:903` `_qs_order_target_value` 实盘链买入 | 现价折股 | 实盘语境 |
| 4 | `:910` `_qs_order_target_value` 实盘链卖出 | clamp | 实盘语境 |

**⚠️ 已知盲区（本方案不掩盖）**：`_qs_order_target_value` 的**回测语境**分支（`:891-892`）**转发 QMT 原生 `order_target_value`**（D-18 修复：回测语境 positions 恒空，原生引擎自管差额）——**绕过 `_qs_passorder`，wrapper 层观测不到股数/价格**。而 **CANSLIM 源仅用 `order_target_value`**（源 L162 清仓/L247 买入，grep 实证）⇒ 用户 QMT 回测模式下 CANSLIM 订单**全部**落此盲区。

## 二、改动范围（精确）

| 序 | 文件 | 位置 | 改动 | 行量 |
|---|---|---|---|---|
| 1 | `quantstudio/strategy_compiler/source_import_qmt.py` | `_QS_QMT_ORDER_EXT` 模板串（`:834-911`）内 `_qs_passorder` 函数体 | 加审计 print（见 §三冻结格式 A） | +6 |
| 2 | 同上 | `_qs_order_target_value` 原生转发分支（`:891-892` 前） | 加观测 print（见 §三格式 B，**不进五维**） | +4 |
| 3 | `tests/test_qmt_align_check.py` | 文件尾追加 | 新用例：格式 A 行解析为订单事件（op=23→buy/24→sell、code 归一、qty/price 提取）；格式 B 行不进五维（`non_audit` 或独立前缀计数） | +30 |
| 4 | `tests/test_qmt_stub_smoke.py` | 追加 | 桩驱动产物冒烟断言：下单路径触发时产出 ≥1 条格式 A 或 B 行（用既有确定性合成数据） | +15 |
| 5 | `docs/evidence/qmt-conv-correctness-baseline-20261103.md` | 变更日志 | 新旧 SHA16 六值对照留痕（旧锚作废声明） | +3 |
| 6 | `knowledge/qmt/QD-2026-0020.md` | 新建 | R4 沉淀（订单审计行族） | ~55 |

**禁触面**：撮合/取数/生命周期逻辑零改动；`qmt_align_check.py`（冻结判据件）零改动；策略源零改动（修复经重转生效，铁律 07）。

## 三、审计行冻结格式（与对照器词法逐项对齐）

**格式 A（`_qs_passorder` 内，进五维对照）**：
```python
print('[QS-QMT log.info] QS_QMT_ORDER date=%s code=%s op=%d qty=%d price=%.4f prType=%d'
      % (_qs_order_dt(C), str(order_code).split('.')[0], op_type, int(volume), float(price), pr_type))
```
- `date`：`_qs_order_dt(C)`＝`datetime.fromtimestamp(C.get_bar_timetag(C.barpos)/1000).strftime('%Y-%m-%d')`（timetag 语义＝M5-13 实测北京午夜锚定毫秒；同款推导先例 `:356`/`:463`）；**推导失败（异常语境）⇒ 整行降级为不带 date**（对照器 `excluded_no_date` 计数排除，不污染判据——fail-safe）；
- `code`：**`split('.')[0]` bare 码**（先例 `:823`）⇒ 与 Step 1 基线归一口径**天然一致**，Step 3 零预处理；
- `op=23/24`：对照器 `OP_TYPE_MAP`（`:111`）冻结语义；
- `qty`：`int(volume)` 股数（本地语义原样；QMT 侧单位口径属 M5 实测项，×100 系统差 ⇒ 归因不预设——用户 21:5x 裁认可）；
- `price`：传参原样（prType=5 时为 0 ⇒ 对照器「price≤0 记 na 不作坏行」，冻结词法 L44-45）。

**格式 B（原生转发分支，纯观测不进五维）**：
```python
print('[QS-QMT log.info] QS_QMT_ORDER_TARGET_NATIVE date=%s code=%s target_value=%.2f'
      % (_qs_order_dt(C), str(code).split('.')[0], float(value)))
```
- 语义：回测语境订单交 QMT 原生引擎，wrapper 观测不到股数/价格 ⇒ **只记发生与目标市值**，不冒充订单明细（禁令②：禁脑补 qty）。

## 四、影响面与判型（铁律 18）

- **判型：新增检测型**（verdict 可被新证据改变——日志从「无订单事件」到「有」；不改撮合/取数/生命周期任何行为）。
- **SHA16 必然漂移**：`_QS_QMT_ORDER_EXT` 属 wrapper 渲染面 ⇒ 六策略产物全部重转后 SHA16 变化 ⇒ **新锚入册、旧六值（`7f31a86b…` 等）作废并留痕对照**（用户裁①「SHA16 换锚新旧对照留痕」）。
- **行为零变**：纯 print 追加（观测器不污染引擎——与 `qmt_align_check.py` 禁令③同精神）；产物可驱动性不变（stub 冒烟回归验证）。
- **共享层**：`source_import_qmt.py` 属共享核心（铁律 10）⇒ 写前快照四步＋精确 add＋整 index 核对；出仓后 11-05 副本门过门。
- **【v1.1 ②审硬补改写】契约哈希面内预期变更（非「不触红线」）**：`_QS_QMT_ORDER_EXT` 在 `_QMT_HASH_SCOPE` 契约哈希面内（`scripts/check_fund_matrix.py:42` 亲验）⇒ 模板内容变更 ⇒ `meta.qmt_wrapper_hash` **必然漂移**；处置＝**V4b 同 commit 追认**（`check_fund_matrix.py --check --reverify` 矩阵追认，铁律 10 第⑥款「矩阵哈希同 commit 追认」**QMT 侧同等适用**）。原 v1.0「20 常量零改动＝不触红线」表述**作废**（常量清单不变≠哈希不变，属误导性表述，②审指正）。

## 五、验收标准（V1-V7）

| 门 | 判据 | 方法 |
|---|---|---|
| V1 | `tests/test_qmt_align_check.py` 全绿＋**新用例三条**：①格式 A 解析（op=23→buy/24→sell、bare 码、qty/price 提取）②格式 B 不进五维（`non_audit`/独立前缀计数）③**【v1.1 混合负向】A/B 行同流时 A 计数不含 B**（防 `QS_QMT_ORDER` 子串误吞 `QS_QMT_ORDER_TARGET_NATIVE`——前缀匹配须加词边界/全 token 比对） | `pytest tests/test_qmt_align_check.py -q` |
| V2 | `tests/test_qmt_stub_smoke.py` 全绿＋新断言（下单触发 ≥1 条 A/B 行） | `pytest tests/test_qmt_stub_smoke.py -q` |
| V3 | **168 测试面全绿**（`tests/test_qmt_*.py` 14 件） | `pytest tests/test_qmt_*.py -q` |
| **V4a** | 六策略重转 ALL PASS ＋ **产物 SHA16 新六值入册**（旧值作废留痕，`docs/evidence/qmt-conv-correctness-baseline-20261103.md` 变更日志） | `scripts/qmt_reconvert_six.py` |
| **V4b** | **wrapper_hash 新旧对照留痕 ＋ 同 commit 矩阵追认**：`meta.qmt_wrapper_hash` 漂移值记入基线件变更日志；同 commit 执行 `check_fund_matrix.py --check --reverify` 追认（铁律 10 QMT 侧同等适用；`_QS_QMT_ORDER_EXT` 在 `check_fund_matrix.py:42` 哈希面内——②审亲验） | `python scripts/check_fund_matrix.py --check --reverify`（CI 已红则补 chore(matrix) 追认并登记） |
| V5 | **【v1.1 口径消歧·静态 vs 运行时分层】**静态（产物内模板**格式串**各 ≥1：`QS_QMT_ORDER` 与 `QS_QMT_ORDER_TARGET_NATIVE` 均为**渲染进产物的固定文本** ⇒ 六产物各 ≥1 各 ≥1）；运行时断言**按策略下单面分层**，**禁一刀切**：`order_target_value` 类策略（CANSLIM、周频三层等用 OTV 者）可期 `TARGET_NATIVE` 行 ≥1；`order()` 类策略（其余 5 个走 `order()`）运行时**只出 A 行**、`TARGET_NATIVE` **合法为 0** | 静态：GBK 读＋正则（六产物）；运行时：stub 冒烟按策略分面断言 |
| V6 | 既有功能无新增失败（裁一口径：对 `5cd6327` 基线 94 failed 零新增） | `pytest tests -q`（或按批定子集） |
| V7 | 知识反哺：`kb_hook.py --strict` 对 `QD-2026-0020` 不合规 0 件 | `python scripts/kb_hook.py --files knowledge/qmt/QD-2026-0020.md --strict` |

## 六、回退条件

| 触发 | 手段 |
|---|---|
| V1/V2/V3 任一红 | 还原 `source_import_qmt.py`（铁律 03 写前快照 stash，单文件 `git show <hash>:<path>` 定向还原） |
| V5 命中异常（如审计行进产物哈希面致部署链断裂） | 同上还原＋缺口登记 ISSUES 重开 |
| 真机冒烟发现格式 A 行被 QMT 客户端日志面板吞掉（print 不落盘） | **不回退代码**——改取日志途径（QMT 日志文件直读），登记实测项 |

## 七、回测语境五维输入的替代源（盲区处置，随 Step 2 冒烟验证）

CANSLIM 回测模式订单明细在 QMT 原生引擎内，wrapper 不可观测 ⇒ 判据轮订单侧输入二选一（Step 2 冒烟轮定）：
- **路径 1**：QMT 回测界面「交易/委托明细」导出 CSV ⇒ 我方写一次性格式转换（映射到对照器词法）喂 Step 3；
- **路径 2**：用户以**实盘模拟（shadow）语境**重跑 ⇒ 走 `_qs_passorder` 4 路 ⇒ 格式 A 直接产出。
（格式 B 行无论哪条路径都提供「转发发生/频次」观测，用于交叉核对。）

## 八、Step 2 冒烟操作说明（用户侧一页纸，随本方案附呈）

1. **取产物**：`output/qmt_import/CANSLIM突破成长选股策略.py`（GBK，112,182B，sha16 `7f31a86baa6344e1`——**审计行补齐前旧锚**，冒烟不计判据）；
2. **粘贴**：QMT 客户端 → 策略交易 → 新建策略 → 全选粘贴保存（沿用 10-12 剪贴板直通路线）；
3. **跑**：回测模式，窗口 **2026-06-01..2026-09-30**（与本地基线同窗），初始资金 100,000（默认）；
4. **导两样给我**：①运行日志（含 `[QS-QMT log...]` 行的输出面板全文/日志文件）②回测「交易/委托明细」导出 CSV（若界面可导）；
5. **我看什么**：产物可驱动性（零报错跑完）＋数据面审计行格式正确＋`QS_QMT_ORDER_TARGET_NATIVE` 行出现频次＋明细 CSV 可作判据输入（§七路径 1 可行性）。
（不计任何保真判据——判据轮等审计行补齐后重出产物、盘后窗重跑。）

## 九、六步推进状态

| 步 | 状态 |
|---|---|
| ① 方案 | v1.0（2026-11-04 22:0x） |
| ② 审 | **有条件通过（2026-11-04 22:5x 总调度，四处代码亲验）**——(a) 盲区分层照准／(b) 格式 A 词法照准／(c) V4 换锚照准＋🔴硬补（V4b 契约哈希追认）＋修订二（V5 分层）＋修订三（V1 混合负向）⇒ **v1.1 落盘，不必再候二审** |
| ③ 实施 | 候②审后（实现型编码 ⇒ 委派 `zcode_code`，O4 七件套） |
| ④ 验收 | V1-V7 |
| ⑤ 用户确认 | 呈批 |
| ⑥ 双仓推送 | ⑤后，随下一出仓窗；11-05+ 副本门过门 |

## 十、变更日志

| 时间 | 内容 |
|---|---|
| 2026-11-04 22:5x | **v1.1 ②审三修订落盘**：🔴硬补 V4 拆 V4a/V4b（`_QS_QMT_ORDER_EXT` 在 `_QMT_HASH_SCOPE` 哈希面内 ⇒ wrapper_hash 必然漂移 ⇒ 同 commit `check_fund_matrix.py --check --reverify` 追认；§四红线段改写为「契约哈希面内预期变更」）；修订二 V5 静态/运行时分层＋按策略下单面断言（OTV 类期 TARGET_NATIVE、order 类合法 0）；修订三 V1 加 A/B 同流混合负向用例（防子串误吞） |
| 2026-11-04 22:0x | v1.0 方案：4 路下单汇聚点单落点＋回测语境原生转发盲区分层处置（格式 B 观测行＋判据输入替代源两路径）＋冻结词法逐项对齐＋V1-V7＋冒烟一页纸 |
