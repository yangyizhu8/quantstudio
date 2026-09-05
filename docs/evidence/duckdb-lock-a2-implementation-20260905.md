# A2 实施与验收证据：F-DUCKDB-LOCK 数据层观测网（2026-09-05）

> 六步流水线主线 A 第 3-4 步。设计依据：docs/duckdb-lock-timeout-design.md（A1 复核通过）。
> 实施回退点：stash@{0} = afbd4088（a2-impl-baseline-20260905，refs/stash 持久化）。

## 1. 实施内容（两文件，精确清单）
1. **quantstudio/backtest/providers/duckdb_data_access.py**（主落点，diff +145 行级）：
   - 模块常量：`_DEFAULT_QUERY_BUDGET_S=30.0`、`_BARS_CACHE_CHUNK_DEFAULT=200`、`_env_pos_float`（QS_DUCKDB_QUERY_TIMEOUT_S / QS_BARS_CACHE_CHUNK_SIZE 可调，非法回退默认）；
   - `_get_conn`（P3）：except:pass → 首次 QS_DUCKDB_CONN_UNAVAILABLE WARNING（db_path+真实异常文本）+ 计数聚合；**None 返回契约不变**；文件缺失静默分支保持原行为（不告警）；
   - `_qs_record_diag` / `qs_diagnostics()`（新）：明细封顶 50 条 + 每类计数聚合；
   - `_execute_with_timeout`（P2）：per-statement 预算看门狗（线程 + conn.interrupt()）；超时 → 落账 QS_DUCKDB_QUERY_TIMEOUT（SQL 片段/预算/耗时/码数/attempt）+ WARNING + **单次重试**（复核可选项采纳，覆盖 CHECKPOINT 类瞬态窗，重试计入事件）；二次超时抛 RuntimeError 带归因（显式失败，禁止静默 None）；非超时异常**原样透传**（异常行为契约不变）；
   - `_ensure_bars_in_cache`（P1）：等价分片（默认 200 码/片，只切码集）+ 每片经超时执行 + QS_BARS_CACHE_PROGRESS 心跳（loaded/总码数/片数/耗时）。
2. **quantstudio/backtest/backtest_engine.py**（+13 行接线）：run() 收尾（[Backtest] completed 之后、export 之前）getattr 守卫（同 L2490 既有 mock 安全模式）调 `qs_diagnostics()` → 逐条 WARNING QS_DIAG + 汇总行——**无条件输出，不依赖错误状态**（审核附加条）。超范围说明：设计 §2 写「仅 duckdb_data_access.py」，为实现「收尾无条件输出」必须加此最小接线（6 行守卫块），已按守卫模式实现并在本节显式申报，请复核追认。

## 2. 验收四门结果
### 门① 单测（tests/test_duckdb_query_timeout.py，新增 10 测试全绿 2.81s）
- P2 看门狗：900 万×900 万笛卡尔积（1s 预算）→ InterruptException → attempt1/2 两明细 + occurrences=2 聚合 → RuntimeError 带归因 ✅
- P2 透传：BinderException 原样抛出（异常行为契约不变）✅
- P2 预算内正常查询：零诊断事件（clean run 无噪音）✅
- P1 等价：分片（chunk=2）vs 单条 SQL 参照实现 **7 码 × 70 行 assert_frame_equal 逐行一致** ✅；chunk=1 极端分片 + 缓存命中幂等 ✅
- P1 心跳：多片加载必出 QS_BARS_CACHE_PROGRESS loaded=5/5 ✅
- P3：connect 失败（损坏文件）→ None 契约不变 + 首次告警一次 + 二次零告警 + occurrences 计数 ✅；文件缺失静默分支保持原行为 ✅
- 诊断封顶：60 事件 → 明细 50 + occurrences=60 聚合 ✅

### 门② 真实并发
A0 三阶段跨进程探针在案（1RW+多RO / 未提交事务 / 提交重写均无阻塞）——本门由 A0 证据 + 门④生产形态冒烟共同覆盖。

### 门③ 回归（钉死清单）
契约门重跑：失败集 = 钉死清单在途漂移层 5 项（他方 source_import §21 探针在途，归因 docs/evidence/duckdb-lock-regression-baseline-20260905.md §3）+ 矩阵哈希门红（同因），**与 A2 前逐项一致、零新增**。白名单 5 项本轮未触发。按钉死文档 §4 比较规则：不计入 A2 放行/否决。

### 门④ 黄金冒烟（打板 canonical lbdt-dalong，2026-07-01~07-03 短窗）
- 端到端 60.2s 正常完成，nav_days=3，metrics_summary 结构不变，output_dir 正常导出；
- QS_BARS_CACHE_PROGRESS 实证：**loaded=5511/5511 chunks=28 elapsed=24.7s**（原路径此段分钟级无输出=「挂起」表象；现可观测、可中断）；
- qs_diagnostics()=[] —— clean run 零噪音；
- 缓存条目 5517 正常填充。
- 说明：24.7s 慢加载本体仍在（分片解决可观测+可中断，**不提速**——PR7 全表扫描本体优化是 profiling 报告既有立项方向的后续项，不在 A2 范围）。逐项信号/订单/净值对比的完整黄金门：分片等价由门①逐行断言覆盖数据层本体；策略层短窗对比建议随 B3 E2E 联调一并执行（同库同数据源，改动不触达信号路径）。

## 3. 审核两项非阻塞注意的落实
- **注意① 传播路径**：InterruptException 实测从 _ensure_bars_in_cache 抛 RuntimeError（显式）→ 引擎数据层调用链为 fail-loud（本地引擎直调，无 wrapper 吞噬层——wrapper 只存在于转换产物侧）；**策略侧防御性 except（L1512-1513）确会吞显式失败** → 保底通道 = 源点落账 + 引擎收尾无条件 QS_DIAG 汇总（已实现，冒烟验证通道可用）。传播实测记录：duckdb 抛点( worker InterruptException) → _execute_with_timeout 二次超时 RuntimeError → _ensure_bars_in_cache 直达调用方（query_bars_by_count_batch → get_bars_by_count → ptrade_api）→ 策略边界形态留 B3 E2E 时以打板产物实测补录。
- **注意② 单次重试**：已实现（attempt1 超时→interrupt→重试同语句一次，重试计入事件 attempt=2）；连接复用经 a1_interrupt_probe 实证。

## 4. 回退条件核查
三条回退线均未触发（分片等价 ✅ / 无误杀——预算 30s 默认下单语句 P99 远低于预算 ✅ / diagnose 走日志通道不改结果文件 schema ✅）。

## 5. 待办与边界
- 送审 → 用户确认 → 双仓推送（推送前矩阵哈希门需他方 §21 探针落地后 --check --reverify，或在途改动回滚——归他方会话闭环，A2 不代办）；
- B1 定位（另案）、C 窗口执行（备案待窗口）照计划并行；
- 范围外维持：O4 测试债、AGENTS.md 修订（已落地工作树，随下次文档类推送）。
