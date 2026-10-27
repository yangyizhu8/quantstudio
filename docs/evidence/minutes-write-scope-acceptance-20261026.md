# minute 表写作用域隔离（B1-B7 + A1b）· ④验收结论与 ⑤呈批件

- 件名：写作用域隔离 P1（B1-B7）+ A1b 登记判活 —— ④验收结论 + ⑤呈批材料
- 呈批日期：2026-10-26
- 提交：`c96d09e`（7 文件 +643/-23，本地待推队列 20 笔中 1 笔属本批）
- 方案：`docs/minutes-write-scope-isolation-design.md`（②审通过：三裁 + B4 defer 实质条件）
- 窗口台账：`docs/handoff/minutes-fix-window-runbook-20261022.md`（10-24 ④窗 S0-S4）
- 判型：纯恢复型（并发不丢批）+ 新增检测型（作用域可见/让行/defer 计数——单列）

## 一、④验收结论

### 判定：**绿（附注）**——总调度 2026-10-26 裁定，口径 A

| 判据 | 读数 | 来源 | 结论 |
|---|---|---|---|
| ① 分钟任务零失败（口径 A 实质面） | `etf_minutes` ✅ 07:32:00（3,736,464 行全写入） | daemon.log L1549（轮 `r_ea0cd8f9`） | ✅ |
| ② 零写冲突 | `write_retry={"attempts":0,"exhausted":0,"success":0}`；06:00-08:00 段零冲突行 | run_state（轮 `r_ea0cd8f9`） | ✅ |
| ③ applying ≤60 配额 | 轮 1 未达 applying 段（stock 面中断） | — | 待轮 2 |
| ④ B 判据面 | 轮 1 未达 B 观察期（同上） | — | 待轮 2 |
| ⑤ `write_scope` 生产首现 | `{"entered":0,"conflicts":0,"waits":0,"deferred":0,"timeouts":0,"wait_s":0.0,"active":0}` | run_state（轮 `r_ea0cd8f9`） | ✅ 加分项（非门槛，运维2 已证事件性字段） |
| ⑥ 排水速率 | 轮 1 未达（基线 QfqPending 51,289 / DeadLetter 12,161） | — | 待轮 2 |

**附注 1（stock 面第四态中断非红）**：`stock_minutes` 于 10-26 09:55:23 后**中断**（非 FAILED）——当日轮 06:01 起 67 批通过后，daemon 第四次静默死亡（见附注 3），进程在写盘段消失。**归基础设施死亡，非写作用域缺陷**（无冲突记录、无 defer、作用域零；批次 67 全过 validator）。

### 支撑证据

| 项 | 证据 |
|---|---|
| 单测 42 绿 | `test_minutes_write_conflict.py` 19 + `test_minutes_write_scope.py` 15 + `test_writers_rw_backoff.py` 8（10-24 ④窗 S2 复跑，主树） |
| ④窗 S0-S4 全绿 | 占用声明 → `--gate-only` PASS（新契约，事后追认）→ 优雅停 69036（5s）→ 合入（patch 35,171B + 42 测试）→ 重启 → ⑥提交 `c96d09e` |
| 生产加载两证 | `git_commit=a4fb871`（含 `606e688`）+ 启动行（10-24 21:32） |
| 观测面在产 | `write_scope` 字段在 run_state 连续两轮出现（`r_ea0cd8f9` 全零） |

## 二、附注 2（🔴 风险登记）：第 20 次冲突——**B1-B7 未拦截的新形态**

| 项 | 事实 |
|---|---|
| 时点 | 2026-10-26 14:11:04（补跑轮 `r_d96c8866`） |
| key | `000895, 1792373400000, 1min`（**当前增量窗新数据段**，非既往恒定历史 key `000055@09-07`） |
| 失败槽 | `path=_write_locked serial=True peers_n=0 cross_proc=True` |
| 作用域 | `write_scope` 全零（**无在程作用域 ⇒ 未触发让行/defer**） |
| 重试 | 1/3→2/3→3/3 耗尽（A6 正常工作） |
| 冲突者 | **未定谳**：3A 锁持有者=daemon 自身（task=`event_discovery:292`）；进程清单无跨进程写者；引擎 apply 段无活动 ⇒ `cross_proc=True` 为误报 |
| 定性 | **同进程未登记写路径或连接/事务泄漏**（event_discovery 线连接为首要嫌疑，未证） |

**处置建议**：连接级取证（冲突复现时枚举进程内 duckdb 连接与 open transaction；或 writer 加连接登记钩子入失败槽）——需另立增量（走六步），本轮不扩面。

## 三、附注 3（🔴 前置风险）：daemon 五死事件

| # | 时点 | 场景 | WAL |
|---|---|---|---|
| 1 | 10-22 01:09 | event_discovery 后（tick 间隙） | — |
| 2 | 10-25 00:53 | tick 间隙 | 0 |
| 3 | 10-25 深夜 | tick 间隙 | 0 |
| 4 | 10-26 09:55 | stock_minutes **写盘段** | **1.00 GB**（备份 `bak.20261026_115507`，已收敛） |
| 5 | 10-26 15:28 | stock_minutes **大批量拉取段** | 已收敛（7,951B） |

**共性**：无 traceback、无 Windows 事件日志、无 stop.request、非他线接管；**全部发生在高内存/IO 活动期间**（拉取/写盘/解码）⇒ **OOM 或 DuckDB native crash 嫌疑为主**（dev 已收 OOM 排查令 + faulthandler 补项）。

## 四、轮 2 实际读数（2026-10-27，④附注收口轮）— 计划已执行

| 项 | 读数 | 来源 |
|---|---|---|
| daemon | **35700 存活**（06:03 轮正常起跑） | daemon_status.json |
| run | `r_c2ce634c` **completed 73/1**，finished 11:58:40 | daemon_run_state.json |
| etf_minutes | ✅ 08:36:39 raw=**4,153,876**（new 417,412 + upd 3,736,464） | daemon.log L1536 |
| stock_minutes | ❌ FAILED 08:59:24 —— **第 21 次冲突**，key **与第 20 次完全相同** `000895, 1792373400000, 1min` | daemon.log L1868-1871 |
| write_retry | `attempts=3, exhausted=1, success=0` | run_state |
| write_scope | `entered=120, conflicts=0, waits=0, deferred=0, timeouts=0` | run_state |
| applying | **56/60 ≤60 配额内 ✓**，committed=**5**、retryable_failed=32、dead_letter=19 | daemon.log L13135 |
| B 观察期 | factor_equivalent=1 / blocked=51 / units=4599 / **放行率 1.9%**（首次非零） | daemon.log L13831 |
| 排水 | QfqPending **51,199**（基线 51,289，**-90**）/ DeadLetter **13,320**（+1,159） | 只读探测 |
| WAL | 无（已收敛 ✓） | data/quantstudio.db.wal |

> 计划对照：原定「补 stock 完整面 ✅/FAILED + applying ≤60 + B 观察期 + 排水」——三项固定读数**全部取得**（applying 56/60 ✓ / B 观察期 1.9% + committed 5 / 排水 -90），stock 面得 FAILED（冲突）而非 ✅，按裁定归入单独立项。

## 五、回退开关

| 开关 | 作用 |
|---|---|
| `QS_PRICE_WRITE_SCOPE=0` | 关作用域检测/让行/defer ⇒ 逐位旧行为 |
| `QS_PRICE_WRITE_SCOPE_WAIT_S` | 让行上限（默认 120s） |
| `QS_PRICE_WRITE_ASSERT=0` | 关锁覆盖断言（A3） |
| `QS_WRITE_RETRY_MAX=0` | 关有界重试（A6） |

## 五之二、④结论落账（总调度 2026-10-27 裁定：绿 + 双附注 · 台账拆两行）

| 台账行 | 状态 | 判据 / 附注 |
|---|---|---|
| **B1-B7 写作用域隔离（本项）** | **④绿 + 附注** | 绿前提五项已满足：etf 两轮成功 + 42 测试 + `write_scope` 生产在产 + applying 56/60 + B 面 committed 首次非零 5（firefly 案专项 B 收口判据首次满足） |
| **stock 面（单独立项）** | **待证 · 另窗裁定** | 附注①：第 21 次冲突完整证据（08:59:24 / 重试 3/3 耗尽 / **与第 20 次同 key**）；附注②：「B1-B7 对该型无效」**两轮实证**（非推测）；附注③：未登记写者假说当前状态 = **待 dev 连接级取证**（不写成结论） |

> 台账口径：stock 面收口**不随 B1-B7 ⑤ 走**，待 dev 取证结论后另窗裁定。

## 六、遗留

1. stock 面单独立项：未登记冲突者连接级取证（dev 增量，低优先）；
2. daemon 五死根因（dev 排查中）；
3. ⑥推送：`c96d09e` 已随 #7 出仓；④归档笔 `3a2ee6b` 候 #8；
4. N7 沉淀：待按客户运维域沉淀（案例卷宗 + KEDB 条目，含五死族与写作用域族）。
