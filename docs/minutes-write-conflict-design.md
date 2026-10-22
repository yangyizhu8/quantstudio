# minutes 表写写冲突治理 · 六步①方案（修订版 v2 · 2026-10-22）

- **件名**：minute 表写写冲突治理——写作用域隔离/序列化 + 冲突重试语义（含 W-4 归因修复）
- **立项依据**：总调度 2026-10-22 照准「minute 表写写冲突六步立项」；范围＝`etf_minutes` + `stock_minutes` 双表；根因框架（**候选·未证实**）＝任务写者与 QFQ 编排器并发写同段（**同进程主假设 + 跨进程先验上调**——22:31 案为 CLI once 进程，W-3 已实证 once 与常驻 daemon **分进程共用库**；冲突 key `159512, 1789090200000, 1min`＝补跑窗首根 bar，与 QFQ 重锚重写段重叠——W-1 同 key 族）
- **六步**：本文件第 ① 步（方案）；**②审已出：有条件通过（M1-M4 已按审修订），修订后进③实施**
- **②审独立发现（已并入本版）**：① `writers.py:938-1074` 主 upsert 路径**缺 `record_write_failure`**——22:31 失败点（writers.py:1033）正在该路径内，即 W-4「失败槽为空」的直接原因；② 静态复核三条 writer 路径（938/1107/1196）与引擎侧（2418-2419）**均已持锁**、进程内未见漏锁实锤 ⇒ **跨进程写者（候选④）先验上调**；③ 登记表须含 **PID** 才能区分进程内/跨进程。
- **知识反哺留痕**（2026-10-10 铁律）：
  ```
  知识反哺：[命中] 已知错误-MCP-export-cache完整性-snappy损坏（06-数据与工具/已知错误-MCP-export-cache完整性-snappy损坏.md，2026-10-22）→ 与 14:47 崩点同族（导出分片损坏），本方案不处理该族，仅登记交叉引用
  知识反哺：[命中] W-1 写-写冲突可否重试（docs/pipeline-tech-debt.md §W-1，2026-10-22）→ 复发率 12 次/7 天（10-19 口径）→ 补录后 13 次/9 天，本案 22:31 为第 14 次；key 恒定；水位推进⇒key 出窗（自然实验）
  知识反哺：[命中] W-4 写冲突归因字段在失败路径上不生效（docs/pipeline-tech-debt.md §W-4，2026-10-22）→ 归因不可靠，须先修取证再定罪
  知识反哺：[无命中] 12-客户运维/已知错误（写写冲突/W-1 主题），2026-10-22
  （代码佐证·非主库条目：取证② 同进程并发写 23 次冲突，quantstudio/pipeline/writers.py L90-104，2026-10-22）
  ```

---

## 一、背景与动机

### 1.1 现象（2026-10-21 实弹）

- 22:31:14 `mcp_etf_minutes` once 补跑：`TransactionException: Failed to commit: write-write conflict on key: "159512, 1789090200000, 1min"`（writers.py:1033 `_write_locked` 主 upsert 路径）；数据面已通（validator `passed=49,992 / rejected=8`），**败在 commit**。**W-1 第 14 次**。
- 同轮 `mcp_stock_minutes` 亦 failed（今日轮次 72/74，2 失败均为分钟表）。
- 14:47 另案：`QFQ post-ingest` `OSError: Corrupt snappy compressed data`（导出分片损坏，见 §7 交叉引用，本方案不处理）。

### 1.2 既有事实（代码取证）

| 事实 | 证据 |
|---|---|
| 表写锁已存在且**已覆盖两张分钟表** | `writers.py:95 _SERIAL_WRITE_TABLES = {"stock_minutes","etf_minutes"}`；`table_write_lock()` 返回模块级 RLock（L157-169） |
| writer 三条写路径均持锁 | 主 upsert `writers.py:938`；passthrough `L1107`；分片 `L1196`——均 `with self._table_write_lock(table)` |
| 引擎自持事务已持同一把锁（BEGIN 前取、COMMIT/ROLLBACK 后放） | `qfq_reanchor_engine.py:2418-2419`（minute 先、daily 后）→ `2431 BEGIN`；注释明确「完整覆盖事务」 |
| **但冲突仍发生** | 22:31 实弹（第 14 次）；W-1 复发率 12 次/7 天、key 恒定 `159512 @ 2026-09-11 09:30` |
| **归因不可靠（W-4）+ 空槽根因收窄** | W-4：`record_write_failure` 仅 2 处调用（`writers.py:1123/1257`，均在 passthrough 变体 except）；**主 upsert 路径（938-1074）无此调用**——22:31 失败点（1033）在该路径内 ⇒ 失败槽必空、回退 `ctx_src=last` 取到误导值（`ctx_table=sw_weight`）；并发写者身份至今未指认 |
| 水位推进⇒key 出窗（自然实验已验证） | W-1 补录 2026-10-21：水位 1788451200000→1789023600000，增量窗 09-05→09-11，恒定 key 正压 09-11 09:30；**再进一日即出窗** |

### 1.3 根因候选与不确定性（必须写明）

- **候选①（同进程漏口）**：某条价格表写入口未取 `table_write_lock`——**静态复核未找到实锤**（三条 writer 路径 + 引擎均持锁），先验下调。
- **候选②（锁/事务边界错位）**：事务在取锁前 BEGIN / COMMIT 后仍持锁窗口——引擎已「锁在 BEGIN 前、释放于 COMMIT/ROLLBACK 后」，静态未见错位；保留为低先验。
- **候选③（writer 实例/连接路径绕过）**：低先验（模块级锁跨实例共享）。
- **候选④（跨进程写者）**：**先验上调**——22:31 案本身是 CLI once 进程；W-3 已实证 once 与常驻 daemon 分进程共用同一库；且进程内静态复核无漏锁实锤。**若成立则进程内锁天然无效，须跨进程互斥（另案）**。
- **纪律**：**先取证、后定罪**（A1/A2 为第一功能点）；不得以未证实归因直接实施锁改造；**禁止**在取证前宣称候选①成立。

### 1.4 与 B 方案（factor equivalence）的联动

- B 落地 → gate 放行 → 水位推进 → 重拉窗收缩 → **冲突窗自然收缩**（W-1 量化关系：冲突率 ∝ 重拉段与重锚段的重叠长度；`159512 @ 09-11 09:30` 再进一日即出窗 ⇒ 冲突清零）。
- 本方案的串行化/重试**不替代** B 的收敛作用：B 消除"确定性同 key"，本方案兜住**残余并发窗口**（任意 key，含跨进程候选④若被证实则另行方案）。
- 观测期每轮并列留痕：B 放行率、conflict_count、retry_success_count、补跑结果（W-1 量化面板）。

---

## 二、范围与边界

### 2.1 改动文件（预计）

| # | 文件 | 改动 |
|---|---|---|
| F1 | `quantstudio/pipeline/writers.py` | 写者登记表（**pid**/thread/conn/table/phase/key 段）；`record_write_failure` 补到**主 upsert 路径（938-1074）**及全部写入口；`TransactionException` 有界重试（主 upsert；passthrough 变体复用 ledger resume） |
| F2 | `quantstudio/pipeline/qfq_reanchor_engine.py` | 自持事务外层重试包装（BEGIN..COMMIT 整体重放）；锁内登记/锁外摘除 |
| F3 | `quantstudio/pipeline/daemon.py` | `_stamp_and_write` 链失败槽覆盖验证；run_state 记录 `retry_count/conflict_count` |
| F4 | `tests/test_minutes_write_conflict.py`（新增） | 合成并发复现 + 负向对照（强制重叠）+ 重试幂等 + 归因断言（含跨进程判别规则） |
| F5 | `docs/evidence/minutes-write-conflict-20261022.md`（新增） | 复现/对照/回归证据件 |

### 2.2 不改动

- 不改 B 方案判定逻辑（`QS_QFQ_FACTOR_EQUIVALENCE` 默认值/语义）；
- 不改 gate 判定域（FP-6）、不改配额（FP-7）、不改水位提交语义；
- 不改 `snapshot_lock` 跨进程锁；**不改 R2 表写锁本体**（2026-10-08 落地，硬编码无开关）——本方案只**补齐取证/重试**，不属回退范围；
- 不改表结构/契约（schema 指纹零变更）；
- 不修 14:47 snappy 导出分片损坏（§7 交叉引用，另族处理）。

### 2.3 开关与回退（按②审 M1 拆分）

| 开关 | 语义 | 归属 |
|---|---|---|
| `QS_WRITE_RETRY_MAX`（默认 3；0=关重试） | **生产回退开关**：仅回退本方案新增的重试 | 逐位回到「无重试」旧行为 |
| `QS_PRICE_WRITE_ASSERT`（默认 1；0=关 fail-fast 断言） | 本方案新增断言开关（A3 检测行为） | 关后断言静默（登记仍开） |
| `QS_PRICE_WRITE_SERIAL`（**仅测试进程**：0=将 `table_write_lock` 降级为 nullcontext） | **负向对照专用**（A5），生产不设/=1；**不属生产回退面** | A5 用；明示 R2 锁不在回退范围 |

- 纯增益三型：**纯恢复型**（任一批不因并发丢——恢复原语义）+ **新增检测型**（登记/断言只报告/检测；断言 fail-closed 属新增检测型，单列）——**单写者（无并发）场景重试零触发、结果逐位不变**（等价性验收组成）。

---

## 三、任务拆解（含功能点分解表）

### 3.1 功能点分解表（每点独立可验证）

| # | 功能点 | 改动 | 验收判据（可机检） |
|---|---|---|---|
| A1 | **W-4 归因修复**：失败槽补到主 upsert 路径 + 全部写入口；锁内登记表（**pid**/thread/conn/table/phase/key 段） | writers.py 938-1074 等各写入口加 `record_write_failure`；登记表 | 合成冲突时日志 `ctx_src=self`（非 last）、失败写 path/table/batch/serial 齐全；登记表可按 key 段反查在程写者；**推断规则**：冲突 + 登记表无同 key 段在程写者 ⇒ 跨进程成立 ⇒ 触发 R6 上报 |
| A2 | **插桩复现**：双线程并发写同 key → 指认另一写者 | 测试构造（writers upsert × 引擎事务） | 冲突时日志指名并发写者（thread/phase/table）；**同时验证跨进程判别规则**（无在程写者时能判"跨进程"） |
| A3 | **写作用域隔离**：价格表写入口全覆盖，锁包住 BEGIN..COMMIT；**断言域仅 `table ∈ _SERIAL_WRITE_TABLES`**（daily 的 nullcontext 不误伤） | writers.py / engine.py 全入口断言锁持有（`QS_PRICE_WRITE_ASSERT`） | 单测：mock 一条**分钟表**写入口未持锁 → 断言触发（fail-fast）；daily 写**不**触发；正常路径零触发 |
| A4 | **串行化验证**：同场景新代码无冲突 | 锁覆盖后重放 | 双线程同 key 写 100 次 → 0 次 TransactionException（旧版必现 ≥1） |
| A5 | **负向对照**：`QS_PRICE_WRITE_SERIAL=0`（测试进程）→ 必现冲突 | 测试内强制重叠构造（屏障 + 事务内暂停） | 开关关闭 + 强制重叠 → **确定性**复现 write-write conflict（证明锁为有效因子） |
| A6 | **冲突重试语义**：有界重试（默认 3，退避 0.5/1/2s+jitter），**整事务重放**；分片变体**复用 ledger resume 不盲重放** | writers.py / engine.py | 注入一次外部冲突 → 重试成功且日志 `retry=1`；耗尽 → 失败并记 `retry=3`；重试不无限；分片路径按 ledger 断点续写 |
| A7 | **幂等**：重试不产生重复行、**不重复累计审计账本** | 重试重放同一 batch（batch_id 不变）；审计短事务在锁内或幂等键 | 重试后行数/值正确、PK 无重复；**batch_audit 无重复累计**（审计账本幂等断言） |
| A8 | **补跑验收**：once 补跑 etf_minutes 成功、止点至当日；**允许「多轮 until 追平并记录轮数」**（当前水位 09-10 15:00，跨度约 28 交易日） | 修复后端到端 | `mcp_etf_minutes` 补跑成功且止点至当日（单轮或记录轮数直至追平）；stock_minutes 同验 |
| A9 | **B 联动留痕**：每轮 conflict/retry/放行率并列 | daemon.py run_state + 日志 | run_state 含 `conflict_count/retry_count`；B 放行率行与冲突计数同轮出现 |
| A10 | **证据件 + 回退**：证据文档 + 开关回退演练 | docs/evidence/… | 证据件含复现/对照/回归三段；`QS_WRITE_RETRY_MAX=0` 下逐位旧行为（对照测试）；单写者场景逐位不变 |

### 3.2 实施顺序（逐点门控）

A1（取证：主路径失败槽 + 登记表含 pid）→ A2（复现指认 + 跨进程判别规则）→ A3（锁覆盖断言，域限定分钟表）→ A4（无冲突）→ A5（负向对照，强制重叠）→ A6（重试，分片复用 ledger）→ A7（幂等含审计账本）→ A8（补跑，多轮追平）→ A9（观测）→ A10（证据/回退）。**任一红停在该点**，修复/回退后重过门禁。

---

## 四、风险描述

| # | 风险 | 规避/说明 |
|---|---|---|
| R1 | 串行化降低分钟拉取吞吐（写者持锁期间引擎等待） | 锁粒度按表、临界区仅写事务段（现状）；压测判据：50k 行 upsert 耗时增量 ≤10%；若超阈，评估"引擎事务短化"替代 |
| R2 | 重试掩盖真实缺陷 | 重试计数强制留痕；重试成功也算 W-1 计数（放行率面板可见）；不得静默 |
| R3 | 归因字段再次误导（W-4 教训） | 失败槽语义：`ctx_src=self`；不可用则输出 `ctx_src=unavailable`，**禁止**回退 last 槽（误导比缺失更坏） |
| R4 | 锁顺序死锁（引擎取 minute+daily 两锁） | 现状 daily 表为 nullcontext；若未来扩面，须固定「minute→daily」序；RLock 同线程重入安全；单测含双锁场景 |
| R5 | 重试重放与已部分提交事务冲突 | 只重放整个 BEGIN..COMMIT（先 ROLLBACK/关闭连接重建）；不重放已部分写入的语句流；分片变体走 ledger resume |
| R6 | 跨进程写者未被覆盖（先验上调） | A1 登记表含 pid + 推断规则；实弹冲突若判跨进程 → **立即上报**，另行方案（跨进程锁）；本方案不宣称已覆盖 |
| R7 | B 联动被误读为"冲突已自然消失无需修" | 本方案不替代 B：B 消除确定性同 key，本方案兜残余并发窗口；二者同轮验收 |
| R8 | 断言 fail-fast 把可成功写变失败 | 断言域限分钟表、默认开但可 `QS_PRICE_WRITE_ASSERT=0` 关；单列新增检测型；单写者场景零触发（等价性验收） |

---

## 五、验收要点

1. **功能验收**：A1-A10 逐点绿；冲突时日志能指认并发写者（A2，含跨进程判别）；重试成功/耗尽语义正确（A6）。
2. **场景验收**：
   - 双线程同 key 并发写 100 次 → 0 冲突（新版）；`QS_PRICE_WRITE_SERIAL=0`（测试进程）+ 强制重叠 → 确定性复现（负向对照）；
   - 注入外部冲突 → `retry=1` 成功、幂等无重复行、审计账本无重复累计；
   - once 补跑 `mcp_etf_minutes` 成功（单轮或记录轮数直至止点至当日）；`mcp_stock_minutes` 同验。
3. **边界验收**：空批/单批/无锁路径（分钟表 fail-fast 触发、daily 不触发）；`QS_WRITE_RETRY_MAX=0` 逐位旧行为；WAL 收敛后库可读。
4. **回归验收**：精确选择器 `tests/test_writers_rw_backoff.py`、`tests/test_writers*.py`、`tests/test_qfq_*.py`、`tests/test_daemon_*.py` 全绿 + 黄金行冒烟 1/1；不引入 schema/指纹变更。
5. **性能验收**：50k 行 upsert 耗时增量 ≤10%（基准同机同数据）。

---

## 六、质量判据

1. 冲突可复现、修复可对照（开关负向对照齐备；负向对照强制重叠非 flaky）；
2. 并发写者身份**首次可指认**（W-4 关单证据），且能区分进程内/跨进程；
3. 重试有界、计数可见、幂等可验（行数 + 审计账本）；
4. 补跑端到端成功（etf_minutes/stock_minutes 止点至当日，或记录多轮轮数直至追平）；
5. 回归零衰减（既有单写者路径零行为变化；单写者场景重试零触发、结果逐位不变）；
6. 回退开关逐位还原旧行为（对照测试为证；R2 锁不在回退范围）；
7. 与 B 方案联动留痕（放行率/冲突计数同轮记录）。

---

## 七、交叉引用（非本方案范围）

- 14:47 `OSError: Corrupt snappy compressed data`（QFQ post-ingest 导出分片损坏）——与主库已知错误「MCP-export-cache完整性-snappy损坏」同族，另行处置；
- W-1 复发率统计与「水位推进⇒key 出窗」自然实验——`docs/pipeline-tech-debt.md` §W-1（含 24h 窗口口径与 12 次/7 天实测；本方案 22:31 为第 14 次，已按双向同步纪律回写登记件）；
- W-4 归因缺陷——`docs/pipeline-tech-debt.md` §W-4（本方案 A1 即其关单修复）；
- FP-6/FP-7/B 方案——上批已出仓（ec8323d），本方案只联动不修改。
