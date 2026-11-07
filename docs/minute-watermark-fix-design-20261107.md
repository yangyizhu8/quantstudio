# 【修复方案 · 待用户审核】分钟数据拉取：水位滞后 + 取数时限 + 无锚点熔断

> 提出：节点⑳ ｜ 日期：2026-11-07 ｜ 状态：**待用户审核**（未过审不动码）
> 依据：`docs/evidence/minute-pull-perf-review-20261107.md`（性能复核 + 根因）
> 关联：链① `docs/wm-freeze-fix-design.md`、`docs/duckdb-art-index-corruption-fix-design-20261107.md`

---

## 0 知识反哺留痕（R1）

```
知识反哺：[命中] docs/wm-freeze-fix-design.md（链①）—— W1-A/W1-B 原设计、W4 不变式、V1-V6 判据
知识反哺：[命中] DB-2026-0024/0019/0018（db 族·水位/intent 域）—— 水位语义与意图提交机制
知识反哺：[命中] docs/evidence/w1-checkpoint-obs1/obs2 —— committed=2/0、held=3、单元失败率
知识反哺：[命中] docs/evidence/d4-deadletter-attribution-input-20261106.md —— 死信/超SLA 存量与因果链
知识反哺：[无命中] 「gate 全局 passed 导致四表死锁」专项条目 —— 本件为新增结论，修复后入册
```

---

## 1 问题总览（按修复优先级）

| # | 问题 | 现象 | 根因（已定位） | 影响 |
|---|---|---|---|---|
| **P0** | **四价格表水位永久冻结** | GUI 五任务「门控保持」；`etf_daily` 09-04 vs 止点 11-05（62 天） | **gate 用单个全局 `passed` 控制全部表**；`passed` 判据含「dead_letter=0 / pending SLA=0 / orphan=0 / 本轮全 committed」，而历史存量 **17,520 / 47,199** 恒不为 0 ⇒ **死锁自我维持** | 吃掉全部性能红利；每轮重拉历史；实盘/模拟盘信号受影响 |
| **P1** | 分钟表取数失败 | 11-07 两分钟表 2/2 `export_exceeds_time_budget` | 服务端单作业 60s 同步预算；**1 天窗（33 万行）仍超** | 当日分钟数据完全取不到 |
| **P2** | 无锚点退化全量 | 读失败 ⇒ 判「无锚点」⇒ 自动 310 天/4.8 亿行全量重建 | 无确认、无限额 | 极端代价降级（本次因 DB invalidated 未实际发生） |
| P3 | 单批固定成本 | 11-06 仅写 87 万行仍耗 13,038s | 取数+align+validate | 追赶速度上限 |

---

## 2 【P0】水位滞后修复（核心，三案分层）

### 2.1 根因代码实证

| 位置 | 现状 |
|---|---|
| `daemon.py:625 _advance_or_defer_watermark` | 四价格表 ⇒ `defer_watermark`（写意图，不推进） |
| `qfq_resident_orchestrator.py:1241 _commit_or_hold_watermarks` | **`if passed:` 一个布尔控制【全部表】**；`else` 全部 `status='held'` |
| `:14` docstring | gate 判据 =「**orphan / pending SLA / dead letter / 本轮全 committed**」四项 |
| `:1119 orphan_sql` | `SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='in_progress' …`（**全表计数**，无本轮限定） |
| `:1942` | `if scope: passed=False`「scoped reconcile: **全局**水位强制 hold」 |
| `:1953` | `if detector_degraded: passed=False` |

### 2.2 方案 P0-A（推荐·主修）：gate 判据区分「历史存量」与「本轮增量」

**改法**：把三项计数的统计窗口从「全表」收窄到「本周期」：

```python
# 现状（全表计数，历史存量永久阻塞）
orphan_sql = "SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='in_progress' AND <gen>"
dl_sql     = "SELECT COUNT(*) FROM qfq_trigger_queue WHERE status='dead_letter' AND <gen>"

# 改后（仅本轮产生）
#  orphan：本周期 claim 且未终态（claim 时间 >= cycle_start）
#  dead_letter：本周期新增（last_event_id 属于本周期 / updated_at >= cycle_start）
#  pending SLA：本周期内 pending 且已超 SLA
```

| 项 | 内容 |
|---|---|
| 核心论点 | **历史存量是"过去的问题"，不应永久阻塞"现在的推进"**。水位语义 = 「该表数据已到位」，与「历史单元是否重锚成功」是两个维度 |
| 预期效果 | 存量 17,520/47,199 不再阻塞；只要**本轮**干净即提交 |
| 风险 | 存量死信从此不再阻塞任何提交（需配 P0-D 治理，避免"眼不见为净"） |
| 回退 | 环境开关 `QS_QFQ_GATE_SCOPE=cycle|all`（默认 cycle，置 all 恢复旧行为） |

### 2.3 方案 P0-B（推荐·并行）：表级隔离——单表问题不冻结其他表

**改法**：`_commit_or_hold_watermarks` 逐 intent 判定，替代全局 `passed`：

```python
for source, table, freq, cand in intents:
    table_passed, why = self._table_gate(conn, table, cycle_id)   # 新函数
    if table_passed:
        advance + status='committed'
    else:
        status='held', hold_reason=f"{table}: {why}"
```

| 项 | 内容 |
|---|---|
| 核心论点 | 即使 P0-A 后仍有失败，也应**只 hold 出问题的表**，不该四表连坐 |
| 表级判据（建议） | ①该表本轮无新增死信 ②该表无 orphan ③该表 candidate ≤ 表内 MAX（数据确已到位） |
| 预期效果 | 四表可独立解冻；`etf_daily` 的问题不再拖累 `stock_minutes` |
| 风险 | 逐表判定增加一次查询/表（4 次，可忽略） |
| 回退 | `QS_QFQ_GATE_TABLE_SCOPE=0` 恢复全局开关 |

### 2.4 方案 P0-C（兜底）：W1-A 安全补提交（链①原案）

```python
# 终结周期遗留 pending：当且仅当 candidate <= 该表当前实际 MAX(time) 时提交
```

| 项 | 内容 |
|---|---|
| 定位 | 链① W1-A 原设计（不改本件） |
| 适用 | P0-A/P0-B 实施后仍有残留冻结时启用 |
| 风险 | 触碰 W4 不变式 2「意图只经 gate 提交」⇒ 须显式呈裁（链①已列） |
| 关系 | **本件不重复设计，仅列为兜底**；待 P0-A/B 观察后再定是否启用 |

### 2.5 方案 P0-D（治理·必配）：历史存量一次性清理

**为什么必配**：P0-A 让存量不再阻塞，但**存量本身仍是健康度黑洞**（17,520 死信 + 47,199 超 SLA），且每日 QA 报 ERROR。

| 项 | 内容 |
|---|---|
| 动作 | ①`dead_letter` 按 code/表 分类归档（保留最近 N 天 + 统计快照）②超 SLA pending 批量 supersede（附原因）③治理前后计数留痕 |
| 前置 | **须在无锁窗**执行；执行前备份 `qfq_trigger_queue` 表 |
| 风险 | 不可逆（删/改状态）⇒ 需显式确认；建议先 dry-run 出清单 |
| 预期 | 存量归零后，即使旧判据（全表计数）也不再恒超 ⇒ **双保险** |

### 2.6 P0 实施顺序（推荐）

```
P0-A（本轮判据收窄）→ 观察 1 轮 → P0-B（表级隔离）→ 观察 1 轮
   ↓ 若仍冻
P0-C（W1-A 兜底，须链①呈裁）
   ↓ 并行
P0-D（存量治理，须无锁窗 + 显式确认）
```

**验收（P0）**：

| # | 判据 |
|---|---|
| **G1** | 构造「历史有死信 + 本轮全成功」⇒ 断言 `passed=True`（A 生效） |
| **G2** | 构造「本轮有新死信（仅 A 表）」⇒ 断言 A 表 `held`、B/C/D 表 `committed`（B 生效） |
| **G3** | 构造「candidate > 表内 MAX」⇒ 断言该表 `held`（安全边界不破） |
| **G4** | **现场**：下一轮后 GUI 四表水位**推进**（`etf_daily` 09-04 → ≥11-01），状态从「门控保持」转「正常」 |
| **G5** | 回归：QFQ 既有测试全绿 + 水位不回退（`GREATEST` 守卫）不变 |
| **G6** | 死信/超 SLA 存量**不再增长**（重拉减少的直接结果） |

---

## 3 【P1】服务端取数时限（`export_exceeds_time_budget`）

### 3.1 现状（代码已查）

| 项 | 现状 |
|---|---|
| 客户端窗口机制 | **已实现**：`minute_export_window_days=1` + `minute_export_target_rows=2,500,000` + 行数反算 `safe_window`（`mcp_adapter.py:907-934`），三者取 min |
| 11-07 实测 | 07:06 多次**成功**（14 shards/66.9 万行、7 shards/33.5 万行）⇒ 1 天窗多数情况可行 |
| 07:42 失败 | `create_export_job 服务端错误: export_exceeds_time_budget hint=split the request: narrow time_start/time_end` |

⇒ **1 天窗（33 万行）仍可能瞬时触顶 60s 同步预算**。

### 3.2 方案 P1-A（推荐·配置级零代码）：开启异步导出

代码**已实现但默认关闭**（`mcp_adapter.py:396-398`）：

```python
# 异步导出（服务端已支持 async_mode：立即返回 job_id + status=running）。
self.export_async = bool(config.get("export_async", False))
```

| 项 | 内容 |
|---|---|
| 改法 | 配置 `export_async: true`（profile 级）；**零代码改动** |
| 原理 | 异步作业不受 60s **同步**预算约束，客户端 `get_manifest` 轮询结果 |
| 预期 | 彻底避开 `export_exceeds_time_budget`（今天 2/2 失败的类） |
| 风险 | ①轮询超时需配（作业可能跑几分钟）②服务端 async 支持度需实测确认 |
| 验收 | 分钟表任务不再出现该错误；shards/行数正常 |

### 3.3 方案 P1-B（补充）：失败自动拆窗重试

| 项 | 内容 |
|---|---|
| 改法 | 捕获 `export_exceeds_time_budget` ⇒ 把该窗对半拆（1 天 → 12h → 6h）重试，最多 2 次 |
| 位置 | `mcp_adapter.py` export 调用处（L608-627 附近） |
| 风险 | 需支持**小时级窗口**（现配置是整数天）⇒ 小改 |
| 关系 | P1-A 生效后可降为备选 |

---

## 4 【P2】无锚点熔断（丙，方案件 §6 原案）

| 项 | 内容 |
|---|---|
| 现状 | `get_last_date` 返回 None ⇒ 判「无锚点」⇒ **自动**进入 310 天/4.8 亿行全量（无确认无限额） |
| 方案 | **丙-2（默认）**：只拉最近 **7 天**建最小锚点，后续轮次自然推进；**丙-3（逃生阀）**：`QS_ALLOW_FULL_REBUILD=1` 才允许全量 |
| 改法位置 | `daemon.py`「无锚点（首次/升级后）」分支 |
| 验收 | **V7** 构造 None+表内有数据 ⇒ 断言不走全量、走 7 天；**V8** 置开关 ⇒ 恢复全量 |
| 独立性 | 与 P0/P1 完全解耦 |

---

## 5 影响面与风险（铁律 18）

| 子件 | ①其他功能 | ②性能 | ③精度 | 判型 |
|---|---|---|---|---|
| **P0-A**（判据收窄） | 仅 gate 判定窗口 | 无 | 无（水位语义不变） | **行为修正型**（须显式声明：存量不再阻塞） |
| **P0-B**（表级隔离） | 仅 hold 粒度 | 无 | 无 | **行为修正型**（hold 粒度变细） |
| **P0-C**（W1-A） | 提交路径 | 收益 | 无（幂等 upsert） | **行为修正型**（改不变式，须链①呈裁） |
| **P0-D**（存量治理） | trigger 队列状态 | 收益（QA 不再 ERROR） | 无 | **数据修正型**（须 dry-run + 备份） |
| **P1-A**（异步导出） | 仅分钟表 export 通道 | 可能更快 | 无 | **行为修正型**（同步→异步） |
| **P2**（丙） | 仅无锚点分支 | 收益（避免数十小时无效拉取） | 首次水位语义改「近 7 天」 | **行为修正型**（须显式声明） |

**共同风险**：P0 三案都触及 QFQ 红线域（`qfq_resident_orchestrator.py`）⇒ 按铁律 19/六步：**该文件改动触发 QFQ 既有测试失败即回退**；V1–V3 任一失败回退该子件。

---

## 6 建议的推进顺序（给审核）

| 批次 | 内容 | 前置 | 预估 |
|---|---|---|---|
| **批 1** | **P1-A 异步导出**（配置级）+ **P2 丙-2/丙-3** | 无（P2 独立） | 0.5–1 天 |
| **批 2** | **P0-A 判据收窄** + 环境开关 + G1/G3 单测 | 批 1 观察 1 轮 | 1 天 |
| **批 3** | **P0-B 表级隔离** + G2 单测 | 批 2 观察 1 轮 | 1 天 |
| **批 4** | **P0-D 存量治理**（dry-run → 确认 → 执行） | 无锁窗 + 备份 | 0.5 天 |
| 可选 | P0-C（W1-A） | 前三批仍不收敛 + 链①呈裁 | — |

**为什么先做 P1-A 而非 P0-A**：P1 是今天 100% 失败的**硬阻塞**（取不到数什么都谈不上），且是配置级零代码风险最低；P0 需要观察轮次才能验证。

---

## 7 需你裁定的四点

| # | 事项 | 我方倾向 |
|---|---|---|
| 1 | **P0 主修选 A（判据收窄）/ B（表级隔离）/ 两者都做** | **A+B 都做**（A 解死锁、B 防连坐） |
| 2 | **P0-D 存量治理是否授权**（删/改 17,520 死信 + 47,199 超 SLA，不可逆） | 授权，但**先 dry-run 出清单**给你看 |
| 3 | **P1-A 异步导出**是否直接开（配置级） | 建议开，先在隔离环境验证一次 |
| 4 | **P2 丙-2 的 7 天**是否合适 | 7 天（可一行改为 3 天） |

---

## 8 R4 沉淀

- `本轮沉淀：N/A`（理由：方案件轮，缺陷结论待修复落地并观察后按族入册）
