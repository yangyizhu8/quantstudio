# D-30 方案①：QMT 端测 PIT 冻结合约快照层（instrument snapshot）设计文档

- 版本：v0.3（②审计两轮闭环：第一轮 FAIL→v0.2 修订→第二轮 PASS-with-findings→本轮折叠 2 项 P2 规格钉死；**候⑤用户审批**）
- 日期：2026-10-13
- 立项依据：`docs/ISSUES.md` D-30（用户裁定 2026-10-13「立项目标 A」）；背景实证 `docs/evidence/qmt-d27-close-batch-20261013.md` §六
- 审计记录：第一轮 ZCode sess_29a2d2aa **FAIL**（P0=第 4 消费点 :502 遗漏——已独立复核属实；P1=判型枚举+三问审计；P2×6）——本版逐条采纳
- 关联：D-31 realtime 探针（已 closed：B4 无源定谳）；与 D-27b/D-28/D-29 正交，不捆绑

## 知识反哺检索留痕

- 知识反哺：[命中] `QMT大转换管线-部署通道与innerApi实测形态坑`（06-数据与工具/QMT大转换管线-部署通道与innerApi实测形态坑.md，2026-10-13）——采纳要点：`get_instrument_detail` 实测**单位置参数**（第二参 TypeError），快照访问器必须沿用该形态；D-31 新增坑：`get_stock_list_in_sector` 传 realtime 毫秒时间戳→静默空列表（快照访问器严禁传 realtime）。
- 知识反哺：[命中] `端测纪律-QMT端测须盘后同窗同源黄金对照`（11-方法论与工程体系/端测纪律-QMT端测须盘后同窗同源黄金对照.md，2026-10-13 二次修正版）——采纳要点：问题定义（合约信息表=活表，漂移源）与判据来源（逐位门禁、L1/L2 哨兵）。
- 文档正源：`docs/qmt/inner-api/07-行情函数.md:2446-2475`（get_instrument_detail 无 as-of 参数）、`:3288-3313`（get_stock_list_in_sector realtime 无历史语义，D-31 实测定谳）。

## 一、问题定义

QMT 回测引擎逐K遍历本地历史K线（行情读数经 `_qs_snap_kwargs` 锚定回测 bar 当日，跨运行稳定），但**四**个消费点读**活的本地合约信息表**：

| # | 消费点 | 位置 | 读的活数据 |
| --- | --- | --- | --- |
| 1 | `_qs_get_stock_info` | `source_import_qmt.py:978`（live 调用 `:981`） | `get_instrument_detail` 全字段（名称/上市日/退市日/停牌/涨跌停/流通股本） |
| 2 | `_qs_status_flags` | `:1003`（live 调用 `:1017`） | 同上（ST/HALT/DELISTING 三判） |
| 3 | `get_Ashares`（L0 名单） | `:1913`（live 调用 `:886`） | `get_stock_list_in_sector('沪深A股')` 当前成份 |
| 4 | `_QmtDataBar.__init__`（执行层 bar 视图，`_QS_QMT_DATA_VIEW_EXT` 模板，`:2527-2530` 装配渲染进全部产物） | live 调用 `:502`，消费 `:505-507` | `PreClose/UpStopPrice/DownStopPrice` → `preclose/high_limit/low_limit`（执行层涨跌停判断，E1 铁律域）；`:481` 自认快照口径【M5 实测项】未证实按 bar 锚定 |

供应商推送更新该表（实证：L1 4410→4409、L2 3816→3815）→ 跨推送窗口的两次回测漂移（含执行层涨跌停判断域）→ 逐位门禁不可判。**本方案根治可复现性**（非历史真相——PIT 三层另案，见 §七）。

## 二、目标与非目标

**目标**：同一快照指纹下，任意时间、任意次数回测同一区间，漏斗全层读数逐位一致（含消费点 4 的执行层判断链）。
**非目标**：① 历史 PIT 真相（B1 在场性已半实现/B2 停牌换源/B3 ST 历史无源/B4 板块历史已定谳无源）；② 财务数据侧可复现性（v1 不覆盖，E2E 判据 B 分支预案）；③ 实盘语境（见 §六防线改口）。

## 三、改动范围（允许写入清单）

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `quantstudio/strategy_compiler/source_import_qmt.py` | **模板落点（第二轮审计 P2-1 钉死）**：`_qs_snapshot_load`/`_qs_instr_detail` 一律落位于既有 `_QMT_HASH_SCOPE` 清单内载体（MODULE_HEADER 段——全部产物先行渲染），**不新增独立 EXT 常量**（避免矩阵 scope 外漂移）；若实施确需扩 scope，须同步把 `scripts/check_fund_matrix.py` 补入允许写入清单并呈批。① 新增 `_QS_QMT_INSTR_SNAP_PATH` 约定路径常量 + `_qs_snapshot_load()`（惰性单次加载，**三态缓存**：loaded/absent/broken 均缓存于模块级——absent 亦缓存，**运行中新建文件不生效**（须重跑回测），文档与审计行披露；损坏/schema 不符→broken 态=全回落 live+披露，不抛异常）；② 抽公共访问器 `_qs_instr_detail(code, C)`——**只负责快照命中替换**：命中→返回快照 raw dict；未启用/缺码→返回 `None`。**live 调用不挪位**：各调用点原位执行（保持原 try/except 语义），访问器返回 None 时走原 live 路径——三处既有异常语义**逐点保留**（`:982`→`{}` 续字段映射 / `:1017`→`({},False)` / `:503`→`det={}`），访问器不吞噬任何异常；③ 消费点 1/2：`_qs_get_stock_info:981`、`_qs_status_flags:1017` 改走访问器；④ 消费点 3：`get_Ashares:886` 快照启用时返回快照名单（**严禁传 realtime**——D-31 实测静默空列表坑）；快照路径**保留** date≠None 时的 `QS_QMT_SEMANTIC_DIFF` 登记行（冻结名单≠历史 PIT，登记语义仍真）；⑤ 消费点 4：`_QmtDataBar.__init__:502` 改走访问器（`:505-507` 三字段随快照冻结）；⑥ 审计行两枚（`_QS_QMT_ONCE` 限一次）：(a) 快照文件**存在**（含 broken）时首印静态行 `QS_QMT_SNAPSHOT mode=on|off(broken) file=<名> sha=<16> n=<codes>`（**broken 态 n=-1**——第二轮审计 P2-2 钉死，消解判据 4/5 歧义；**无文件＝零输出**，与默认零漂移对齐）；(b) 首个 live 回落 miss 时补印 `QS_QMT_SNAPSHOT first_miss=<code>`（一次）——首印不含 hit/miss 累计量（首印时恒近 0，统计失义，P2-3(b) 修订） |
| 2 | `docs/probe/qmt_instrument_snapshot_export-20261013.py` | QMT 侧导出脚本（粘贴运行型，同 D-31 形态）：取 `get_stock_list_in_sector('沪深A股')` + 逐码 `get_instrument_detail` → **raw dict 原样序列化**写约定路径 JSON（UTF-8；int 保持 int 禁 str 化；含 `enabled/meta.exported_at/meta.n/records`），打印导出摘要行 |
| 3 | `tests/test_qmt_instrument_snapshot.py` | 单测（见 §五，≥12 用例） |
| 4 | `docs/evidence/fundamentals-contract-matrix.{yaml,md}` | wrapper 渲染常量改动 → 同 commit 追认 `qmt_wrapper_hash`（M5.2 纪律） |
| 5 | 本文档 + `docs/ISSUES.md` + `docs/PROJECT_STATE.md` | 簿记 |

**快照文件约定**：路径 `D:\miniQMT策略实盘\QuantStudio\output\qmt_snapshot\instrument_snapshot.json`（约定优于配置，v1 不做参数化）；启用=文件存在且 `enabled:true`；禁用=删文件/改 false；刷新=重跑导出（新指纹）；**三态缓存下运行中变更文件不生效，须重跑回测**。快照文件不进 git（产物级，~5MB）。

**双端对等豁免说明**：v1 导出工具为 QMT 侧粘贴脚本（无仓库 CLI 入口），不触发 CLI/PyQt 对等义务；后续若新增仓库侧导出命令（xtdata 形态），对等义务即触发。

## 四、影响面与判型

- **三问前置审计（P1 修订，显式结论）**：①影响其他功能——默认关：零接触（无文件=零输出零行为变化）；enabled：仅数据来源替换（live 表→冻结快照），返回结构/契约/签名不变，既有测试零衰减；②回测性能——enabled 时消费点 1/2/4 的逐码 live API 调用转为内存 dict 查找（净提速或持平），loader 惰性单次 + 三态缓存避免文件 stat 风暴；默认关路径仅多一次「模块级标记判读」，开销可忽略；③回测精度——enabled 口径=冻结名单/状态 vs live 口径，差异属能力预期（可复现性换快照时点），已入非目标与审计行披露；默认关零影响。
- **判型声明（P1 修订）**：按「修复前置纯增益审计」四型枚举归 **新增检测型**（新增能力开始报告真相/提供新口径，非既有行为漂移；默认关时既有行为逐位不变）；与「新功能模块添加」铁律原文表述「新增能力型」系同一语义的两种措辞——**该映射呈⑤用户裁定确认**（ISSUES D-30 行已同步修订表述）。
- 影响面：四条读取路径新增「快照/live」分支；无策略源码改动；无契约/签名/返回结构改动。
- **影响面图（archify-gate）**：实施轮随 Plan 第⑧项子项提交流程分支图（快照三态加载→四消费点→回落链）；设计阶段不产图（零运行时改动）。
- 实施时序：排在 D-27b R1-B/R1-C 之后；wrapper 模板改动触发矩阵哈希同 commit 追认。

## 五、验收标准（可复核判据）

单测 `tests/test_qmt_instrument_snapshot.py`（≥12 用例全绿，P2-7 修订展开）：
1. 快照文件不存在 → 四消费点行为与现版本逐位一致（mock ctx 断言 live 调用发生、**零审计行输出**、absent 结果被缓存——中途建文件不生效）；
2. 快照存在且 enabled → 消费点 1/2/3/4 全部命中快照，零 live 调用；
3. 快照缺某码 → 该码回落 live（原调用点异常语义逐点保持：`:982`/`:1017`/`:503` 三形态分别断言），`first_miss` 审计行恰一次，其余码仍命中；
4. JSON 损坏/schema 不符 → broken 态全回落 live，首印 `mode=off(broken)` 披露行恰一次；
5. 首印静态行字段完整（mode/file/sha/n）且整次运行仅一次；
6. 导出脚本产物 round-trip（raw dict 原样；**ExpireDate 三形态钉死**：int 20261015 / int 99999999（永续）/ str '2026-10-15' 经 `_qs_norm_ymd` 与 `_qs_status_flags` 判定与 live 同判）；
7. 消费点 4 专项：`data[code].high_limit/.low_limit/.preclose` 快照启用时读冻结值、无文件时与现行为逐位一致；
8. `get_Ashares` 快照路径 date≠None 时 `QS_QMT_SEMANTIC_DIFF` 登记行保留。

回归门禁：既有测试套件全绿（零衰减）+ 六策略重转 api_portability 全 PASS + 矩阵哈希同 commit 追认。

端测 E2E（盘后，双仓推送后）：
- **判据 A（可复现性）**：导出快照→开启→同区间回测两次（间隔≥10 分钟故意跨时段）→ L0/L1/L2/L3/L3v/L4/L5/L6/R/R_selected/buy_submitted/positions/cash/total_value **逐位一致**（消费点 4 冻结经 buy_submitted/positions 链路覆盖）；
- **判据 B（残余源分支预案）**：判据 A 失败 → diff 归因定位（嫌疑清单：财务侧完备性/财务表侧浮动数据——**执行层涨跌停已随消费点 4 冻结出嫌疑集**）→ 登记新 Issue 不扩 v1 范围私修；
- **判据 C（默认零漂移）**：**无快照文件**时＝零审计行输出+行为逐位同现行基线机制（注意区分：文件存在但 broken 时有披露行——P2-3(a) 修订）。

## 六、风险与回退

| 风险 | 缓解 |
| --- | --- |
| 快照老化 | 审计行披露指纹+导出时点；文档明示「要复现就冻结」取舍；刷新=重跑导出+重跑回测 |
| **实盘误用（P2-6 改口）** | **wrapper 无语境判别能力——文件存在即任何语境消费（含同机 QMT 实盘运行）**；防线＝①文件管理纪律（实盘运行机不部署快照文件，删除即禁用）②审计行首印警示 ③文档红字；可选加固（后续议）：快照路径迁出实盘可达目录 / 探得语境信号后加硬闸 |
| 快照损坏 | sha16+schema 校验→broken 态自动全回落+披露 |
| 三态缓存误判 | 「运行中变更文件不生效」入文档+审计行披露；刷新流程明示重跑 |
| 与 D-27b 纠缠 | 实施时序隔离（D-27b 推送后才动工） |
| 残余漂移源（财务侧） | 判据 B 分支预案：登记不私修 |

**回退条件与手段**：①运行时回退＝删快照文件（秒级禁用）；②代码回退＝按「写前快照」纪律 stash 基线 `git reset --hard <hash>` + 矩阵哈希同回退；③验收失败（单测红/E2E 判据 A 失败且归因本层）＝立即回退回炉。

## 七、PIT 三层路线（背景备忘，不在 v1 范围）

B1 在场性（OpenDate/ExpireDate 推导）已半实现；B2 停牌可换源 T 日 bar 有无；B3 ST 历史 QMT 无源（能力边界）；B4 板块历史成份＝**D-31 已定谳无源**（2026-10-13 14:35 实测：传 realtime 毫秒时间戳——now/历史时点均同——静默返回空列表 0 只不抛错；不传参正常 base=5227）。

**定谳推论**：D-30 快照层 + B1 在场性推导是 QMT 端可复现性的**唯一路径**；快照访问器严禁向 `get_stock_list_in_sector` 传 realtime（静默空返回坑，wrapper `:886` 现状正确）。

## 变更日志

| 日期 | 版本 | 内容 |
| --- | --- | --- |
| 2026-10-13 | v0.1 | 初稿（DSH 起草） |
| 2026-10-13 | v0.2 | ②审计第一轮 FAIL（sess_29a2d2aa，P0×1+P1×1+P2×6）逐条修订：P0 补第 4 消费点 `:502`（`_QmtDataBar` 三字段）入拦截范围+单测 7+E2E 链路；P1 补三问审计结论段+判型归四型枚举「新增检测型」映射呈裁；P2-3 审计行改「文件存在（含 broken）首印静态行+first_miss 单行」、判据 C 区分无文件/broken；P2-4 三态缓存+live 异常语义逐点保留+raw dict 序列化钉死；P2-5 快照路径保留 SEMANTIC_DIFF 登记行；P2-6 实盘防线改口（无语境判别，纪律性防线）；P2-7 单测展开 ≥12 条 |
| 2026-10-13 | v0.3 | ②审计第二轮增量重审（sess_82a9a1c0）**PASS-with-findings**（7 项全闭环，grep 证实无第 5 消费点）→ 折叠 2 项新 P2：①模板落点钉死 MODULE_HEADER（既有 `_QMT_HASH_SCOPE` 载体，不新增独立 EXT；扩 scope 须补 check_fund_matrix.py 入允许写入清单并呈批）②broken 态 n=-1（消解判据 4/5 歧义） |
