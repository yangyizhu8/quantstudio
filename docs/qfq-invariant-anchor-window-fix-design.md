# 错误一方案：QFQ 自检锚修订窗口误判治理与 re-anchor 收敛恢复（六步①，2026-09-22）

- 定性（E+A~D+B 取证后修正版，**主因修正已获批准**）：**锚修订 × 检查/写入锚不一致 ×
  收敛环停摆**三因耦合。**fallback 错写为次要面**（713 行级），**非** 1442 万行式历史重演。
- 两案独立、互不捆绑；但本文 T4 以错误二案（污染拦截）为**运行前置**（§四 R4 明说）。

---

## 一、背景与动机

### 1.1 现象（实测账目）

| 证据 | 值 | 锚点 |
|---|---|---|
| 影子库 etf_daily 不自洽面 | **63,426 行 / 85 code / rate 2.95%** | agent_workspace/probe_b_v3.py 输出（fund_adj 全量精确匹配 100%） |
| ratio 形态 | 每 code 仅 **2~3 个离散值**（2100+ bad 行） | 同上——**锚台阶签名**：行自洽于各自写入时锚，非批次内随机错写 |
| fallback 触发史 | **16 次 / 713 行 / 峰值 202**（9-06 集中 8 次） | B-1 全量提取 daemon.log* |
| 阻断 CRITICAL 注册 | **33 次**（9-06 00:36~00:37 etf_daily，rate 5.38%~11.40%）+ 9-10 23 行 ERROR 级 | C7 式全日志检索「自洽偏离/阻断」 |
| 阻断**实际生效**证据 | **「QFQ 写入自检阻断…本轮跳过」= 0 命中（全历史）** | ⇒ 阻断被注册但从未拦下过一轮任务（9-06 once 单轮/后续轮不重叠）；**"误报会停采集"当前未证实，代码路径风险真实存在** |
| 检查器锚不一致 | 9-22 06:44/06:49 WARNING：`stock_minutes 自检锚缺失，回退重新加载（可能与写入锚不一致，R3 兜底路径）` | daemon.log——**检查锚 ≠ 写入锚的直接留痕** |
| 告警通道断裂 | `qfq_factor_revision_alert` rows=**0**、`qfq_jump_audit` rows=**0**（aux 实测）——而 520550 锚 9-15 实际演进 1.0538→1.0576 | probe_b_v3 |
| 收敛环停摆 | `qfq_orch` 周期崩溃：9-17/9-18 traceback（`_discover`）+ **9-22 01:48:02 `周期异常: 非法 code 'FIXTEST'`**；trigger 积压 **53,925**（9-18 QualityAudit SLA>72h） | daemon.log + rotated |

### 1.2 机理（钉死后的完整因果链）

1. ETF 因子**合法演进**（除权事件，fund_adj 按时间追加：520550 九月内 1.0538→1.0576）；
2. 历史行 front 按**写入时锚**计算 ⇒ 与当前锚出现 = 修订比的**离散台阶偏离**（不是坏数，是旧锚正确值）；
3. **检查器不同源**：行内无 adj_latest 时走 R3「回退重新加载」→ 用**检查时刻** aux 重算 →
   与**写入时刻**锚不一致即误报 bad（9-22 stock_minutes 留痕即此路径）；
4. 误报计入 streak → `rate>0.05 单批即阻断` 规则 33 次注册 CRITICAL（9-06 即 ETF 冷启动
   fallback 真偏离 + 本误判混合触发）；
5. **本应消化台阶的 re-anchor 收敛环被错误二的 FIXTEST 卡死**（`_discover` 整轮 ValueError）
   ⇒ 53,925 trigger 积压 ⇒ 台阶不自愈；
6. 观测断裂：revision_alert 应在因子修订注入时记 outbox，实测双空表 ⇒ 无人知道锚在动。

### 1.3 报告引用数据的偏差（如实呈报）

报告所引 **9-22 01:57~02:12 的 9 批次 ERROR/CRITICAL 行（含 3 次 CRITICAL 9.38/19.66/6.36%）
在本机 daemon.log 全部日志中 0 命中**（该窗口批次号 `20260922_01/02` 亦 0 命中；9-22 当日
[QFQ-Invariant] 实际仅 06:44/06:49 两条 WARNING）。

**定谳（2026-09-22 总调度裁定，与「方案文档虚报」同根因之更正）**：该运行**在客户机**
——**客户机 daemon log 有自己的路径，本机未落盘属实，两边本就不该混同**；客户机在其
客户端有独立日志路径，**不在本线可搜面内**。与错误二方案文档「本机全仓检索 0 命中 ⇒
推断虚报」为**同一判据错误**：检索边界 ≠ 不存在证据。建议报方提供客户机日志以对齐
本线与机侧可核面。方案取证以「本机可核面 + 客户机日志（若提供）」双轨为准（9-06
33 CRITICAL 为本机落盘实证，不受此更正影响）。

### 1.4 主库现态待办（读窗复验项）

主库当前被 daemon 持锁（PID 持有，probe 如实失败）⇒ **「现库台阶面 = 63,426?」需读窗复验**；
影子库数值为 9-12 快照口径，9-15 修订后现库台阶面应更大（520550 类新台阶 +9 月后段）。
**回测影响面结论以该复验 + T1 台阶清单为据，不预设零影响**（用户裁定）。

---

## 二、范围与边界

| 改动文件 | 内容 | 边界 |
|---|---|---|
| `quantstudio/pipeline/daemon.py`（~L2698-2754） | T1 检查锚同源化；T2 阻断规则修订 | 不改任务调度框架 |
| `quantstudio/pipeline/qfq_invariant.py` | T1 复算输入契约（`anchor_source` 参数：同批写入 map 必需；缺失→UNKNOWN 不计 bad） | **REL_TOL=1e-6 不放宽**（防借道放宽容差） |
| `quantstudio/pipeline/sources/mcp_adapter.py`（`_sync_factor_snapshot`/`_inject_adjfactor`） | T3 因子注入时**锚演进检测** → upsert `qfq_factor_revision_alert` outbox（observed/changed 事件，含 old/new/批号） | 注入写路径语义不变；告警失败不连带主路径 |
| `quantstudio/pipeline/qfq_resident_orchestrator.py` | T4 `_discover` per-code try/except（脏码 skip+计数，配合错误二 T2 双保险）；积压排水参数 | 不改水位门控语义 |
| `quantstudio/pipeline/aligner.py`（L962-974） | T5 次项（原裁定保留）：fallback 从「批次内 groupby 锚」改「跳过该 code 的 front 计算 + 置 NULL + 计数告警」——**宁 NULL 勿错值** | 主快照路径逐字节不动 |
| `tests/test_qfq_invariant.py` 等 | T1/T2/T5 行为测试 + 黄金行 | — |
| 不改 | re-anchor 引擎数学、水位契约、`adj_factor/fund_adj` 数据语义、passthrough 通道（错误二范围） | — |

## 三、任务拆解

- **T1 检查器锚同源加固（主项①）**：`check_qfq_invariant` 显式接收**本批写入 map**；
  map 缺失/与 align 不同源 → 返回 `status=UNKNOWN`（不计 bad、不计 streak），日志记
  `anchor_source=mismatch`。消灭 R3「回退重载」误报路径（9-22 留痕）。
  **【审核增补·硬约束】UNKNOWN 必须配占比观测指标，防其沦为吞异常后门**：
  ① 每批复算记 `unknown_rows / sampled / unknown_rate`，随 selfcheck 结果入台账（可 grep）；
  ② `unknown_rate > 0.20`（抽样两成走 UNKNOWN）→ 单独 WARNING
  `anchor_source=mass_unknown（检查器覆盖退化，须排查 map 传递链）`，**不计 bad 但必须可见**；
  ③ 验收含「UNKNOWN 吞不掉真异常」证明：重放 9-06 真 fallback 批 → 那些行须落 **bad 而非
  UNKNOWN**（锚 map 在但批次内回退产生的错值，与 map 缺失是两回事，不得混为一谈）。
- **T2 阻断规则修订（主项①）**：`len>=3 or rate>0.05` → **`len>=3`（连续 3 distinct 批真 bad）
  为唯一阻断条件，或 (单批 rate>20% 且 双批连续)**；CRITICAL 附「台阶比」诊断字段
  （ratio 分布）；**skip 行加硬计数**（阻断生效必须留下「本轮跳过」日志，杜绝注册-生效脱节）。
- **T3 修订观测通道修复（主项②）**：注入路径检测 `(code, 新值≠旧值@max_time)` →
  revision_alert outbox；orchestration `_discover` 消费（打通既有 consume_revision_alerts）；
  9-15 类事件今后**必留痕**。双空表由 0→非 0 作为验收硬指标。
- **T4 re-anchor 收敛恢复（主项③，依赖错误二）**：错误二 T2 落地（FIXTEST/TEST999 不再入
  aux/fund_adj 且既有 1 行清出）→ `_discover` 恢复 → 53,925 积压按批排水（每轮限额 +
  hold 语义不变）→ **85 code 台阶面由重锚收敛**（数据修复的正确姿势是走引擎，不是 UPDATE
  价格）。排水进度入台账。
- **T5 次项 fallback fail-safe（原裁定）**：快照缺失 code → front 列置 NULL + 计数 + WARNING
  （`fallback_disabled=n_count`）；不再产生 713 行级新错写。**历史 713 行**由 T4 重锚收敛
  覆盖（9-06 面），覆盖不到的入台阶清单人工判。
- **T6 影响面与台阶清点（验收前提）**：读窗执行——主库全表复算 bad 面（code×月×ratio 值）
  → 产出**台阶边界清单**（85+ code，含 520550/561960/511010/510020 各台阶日期与比值）；
  抽 ≥3 个有台阶 ETF code 跑回测 A/B（跨边界日持有，量化 front 台阶对信号/成交/净值的影响）
  → **回测影响面结论入证据件，不预设**。

## 四、风险描述

| # | 风险 | 规避 |
|---|---|---|
| R1 | T1 收紧致真坏数据漏报 | UNKNOWN 单独计数+日报警；bad 定义不变；黄金行防线 3 不动 |
| R2 | T2 放宽阻断致停摆延迟 | 保留「双批 rate>20%」快通道；skip 计数 + 台账守护 |
| R3 | T5 置 NULL 使下游 front 缺值面扩大 | NULL 行数=未来 fallback 触发数（历史 16 次/713 行，月均个位）；下游既有 NULL 语义复用，不新增处理分支 |
| R4 | T4 依赖错误二先行 | 两案**独立立项独立推送**；执行序：错误二 T2/T5 → 错误一 T4。本案 T1/T2/T3/T5/T6 不阻塞 |
| R5 | 重锚 53,925 trigger 排水期间写锁竞争全量拉取 | 既有水位门控/短事务语义不变；排水限额（默认每轮 N code，参数化） |
| R6 | 告警 outbox 写入热路径开销 | 仅 `(code, max_time)` 值变化时触发（月均每 code 0-2 次），SQLite 行级 upsert，可忽略 |

## 五、验收要点

1. **误报归零**：9-06 窗口重放（隔离库 + 当轮 map）——UNKNOWN 吸收 R3 路径误报，streak 不再被
   锚不一致批推高；真 fallback 批（9-06 冷启动）仍记 bad（**不许借 UNKNOWN 洗白**：重放对照表）。
   **1b（审核增补·吞异常后门守卫）**：正常批 `unknown_rate=0`；注入 map 缺失故障 →
   `unknown_rate>0` 且 `mass_unknown` WARNING 可见；重放 9-06 真 fallback 批 → 该行须落
   **bad 非 UNKNOWN**（锚 map 在、值错 ≠ 锚 map 缺失，两类不得混）。UNKNOWN 占比入台账可 grep。
2. **阻断生效可证**：构造连续 3 bad 批 → 必现「本轮跳过」日志 + 水位不推进（当前 0 证据的补全）。
3. **告警闭环**：构造一次因子注入（新除权模拟）→ revision_alert 出现该 code 行 → 下轮
   `_discover` 消费产生 trigger。**双空表 → 非空**为硬指标。
4. **收敛恢复**：orch 周期连续 3 轮不崩（FIXTEST 已清）；trigger pending 数单调下降有斜率。
5. **次项**：快照缺 code → front=NULL + 计数；黄金 A/B（8 年 1151s 同款口径）无回归。
6. **读窗复验**：主库 bad 现值 + 台阶清单 + 3 code 回测 A/B 影响面结论入 `docs/evidence/`。

## 六、质量判据

- 回滚：T1/T2 参数（streak 阈值、UNKNOWN 开关）config 化可秒级回退；T5 单 commit revert；
  T4 排水限额调 0 = 停；T3 告警旁路（异常吞掉不影响主路径）。
- 写前快照（stash create+store）、共享层提交纪律、矩阵哈希纪律（本件不触 wrapper 模板，预期
  不触发，`--check` 实测）、三方核对、同步门（daemon/pipeline 属共享层，**强制无豁免**）。
- 黄金行 + `tests/test_qfq_invariant.py`/`test_qfq_global_snapshot.py`/`test_qfq_bootstrap_gates.py`
  全绿；六步②审计通过后方可实施。
