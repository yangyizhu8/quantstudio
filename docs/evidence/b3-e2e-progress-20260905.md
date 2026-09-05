# B3 E2E 现场记录与登记（2026-09-05，进行中）

## 1. 已完成部分
- 产物本体加载与引擎装配全通（isolation guard 校验、生命周期函数齐全）；
- 门③反断言（数据层直调 Timestamp 形态非空返回）已覆盖「B2 修复的分钟链真驱动」核心断言；
- A2 观测网实战首秀：心跳 QS_BARS_CACHE_PROGRESS 正常（loaded=1/1 chunks=1 elapsed=1.0s）。

## 2. 新发现（活体证据，登记 F-DUCKDB-LOCK 家族）
运行现场（faulthandler 全线程栈转储，agent_workspace/b3_diag.log）：
- 场景：本机 16 个 python 进程残留（09-03/09-04 起的长跑 daemon/回测/他方会话工作），重度竞争 quantstudio.db；
- 现象：产物 _screen_market 的日线预载分片查询 conn.execute **>30s 预算** → A2 看门狗 interrupt 调用 → **worker 线程仍存活**（join(10) 超时）——duckdb interrupt 对「等待文件锁/IO」状态的语句无效（只能打断计算中的查询）；
- 全局栈：_screen_market → get_history_batch（L1206）→ wrapper 三层 → ptrade_api L1330 → provider L84 → _ensure_bars_in_cache → _execute_with_timeout L233 join(10) → worker L224 conn.execute（不可中断态）。
- **设计缺口确认**：interrupt 对 IO/锁等待无效 → 超时后 worker 可能滞留 → 当前实现 join(10) 后 fail-loud 抛 RuntimeError（正确不静默），但 worker 线程泄漏（daemon 线程不阻塞进程退出，回测进程仍可收尾——兜底成立）。
- **登记**：B2 实现的「单次重试」在 interrupt 无效场景会二次耗预算（2×budget 后 fail-loud，不会无限等）——语义正确但耗时上限=2×budget+20s，设计文档应补注此上界。

## 3. B3 收尾条件（守护登记，不销项）
- 产物 _try_play_window 真驱动断言：待主库竞争窗口解除（16 残留进程退出 / 他方会话收口）后复跑 b3_e2e_v2.py；
- 备选（需用户裁定）：复制主库快照至 scratch 跑 E2E（避免竞争，快照大小待评估）。

## 4. A2 验收影响评估
- A2 已验收功能（心跳/超时/消音/诊断采集）在本现场全部按设计触发或就绪——**观测网实战有效**；
- 新发现（interrupt 局限）不推翻 A2 验收：fail-loud 语义保持、无静默空数据；登记为 F-DUCKDB-LOCK 家族新知识，随下轮设计文档/登记表同步。
