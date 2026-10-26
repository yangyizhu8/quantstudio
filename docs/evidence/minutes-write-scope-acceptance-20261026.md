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

## 四、轮 2 计划（10-27）

| 项 | 内容 |
|---|---|
| 前提 | daemon 存活至 06:00（巡检兜底；五死背景下为最大不确定项） |
| 目标 | 补 `stock_minutes` **完整面**：✅ 或 FAILED（非中断）+ applying ≤60 + B 观察期 + 排水 |
| 取数载体 | 10-27 08:00 一次性守护（按《守护接力》纪律重挂） |
| 判据 | 口径 A 不变：分钟任务零失败 + 零写冲突；minutes 面任一红即停并归因 |

## 五、回退开关

| 开关 | 作用 |
|---|---|
| `QS_PRICE_WRITE_SCOPE=0` | 关作用域检测/让行/defer ⇒ 逐位旧行为 |
| `QS_PRICE_WRITE_SCOPE_WAIT_S` | 让行上限（默认 120s） |
| `QS_PRICE_WRITE_ASSERT=0` | 关锁覆盖断言（A3） |
| `QS_WRITE_RETRY_MAX=0` | 关有界重试（A6） |

## 六、遗留

1. 附注 2 的未登记冲突者（连接级取证，另立增量）；
2. daemon 五死根因（dev 排查中，直接威胁轮 2 前提）；
3. 轮 2 完成后补 applying/B 观察期/排水三读数；
4. ⑥推送：本批 `c96d09e` 待随统推（当前待推队列 20 笔，含他线）；
5. N7 沉淀：待④闭环后按客户运维域沉淀（案例卷宗 + KEDB 条目，含五死族与写作用域族）。
