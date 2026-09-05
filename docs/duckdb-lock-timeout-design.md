# duckdb 数据层慢查询超时与静默空治理设计（A1 设计文档 v1）

> 六步流水线主线 A 第 1 步（A0 定谳已复核通过：duckdb-lock-a0-verdict-20260905.md）。
> 审核硬性要求①②已吸收：超时 per-statement 语义、interrupt 机制 1.5.5 实测、心跳带进度。

## 1. 问题定义（依 A0 定谳，登记更名已批准）
F-DUCKDB-LOCK 真实机制 = 双机制：
- **主因**：`_ensure_bars_in_cache` L502 批量全历史 SELECT（PR7 冷启动）单语句可达分钟级 → 无输出被 40min 清理器终止 → 「E2E 挂起/BLOCKED」表象；
- **伴生**：`_get_conn` except:pass 吞异常返 None → 引擎带空数据静默跑。
duckdb 1.5.5 跨进程锁竞争（connect/execute 阻塞）已被三阶段探针证伪（a0-verdict §3）。

## 2. 改动范围与影响面
- **仅** `quantstudio/backtest/providers/duckdb_data_access.py`（`_get_conn` / `_ensure_bars_in_cache` / 新增超时执行辅助）。
- 策略源码零改动；引擎取数结果内容/顺序/dtype 零变化（无慢查询场景**行为字节级不变**——纯增益门槛）。
- 不改：connect 层锁超时（**裁定删除**——跨进程无竞争，同进程异构冲突是响亮异常，归 P3 显式告警覆盖；加重试属死代码且掩盖真实配置错误）。

## 3. 设计本体

### P1 分片加载 + 进度心跳（慢查询治理）
- `_ensure_bars_in_cache` 的单条大 IN(...) 全历史 SELECT 改为**等价分片加载**：missing codes 按 CHUNK（建议 200 码/片，实测校准）拆分，逐片 SELECT 后合并填充缓存。
- **等价性约束（铁律）**：分片=等价拆分，仅切 WHERE code IN 的码集，**不改 SELECT 列集、不改 LIMIT 语义（无 LIMIT 依旧无）、不改排序/后处理**——合并结果与单条 SQL 逐行一致（黄金门④抓校验）。
- **每片完成打心跳日志**：`QS_BARS_CACHE_PROGRESS loaded=k/total elapsed=Xs`（审核要求：进度信息=已加载/总码数+耗时），per-day 一次汇总行，不逐码刷屏。
- 分片尺寸校准标准：单语句 P99 耗时 << 超时预算（§P2），保证健康分片永不挨刀。

### P2 execute 级观测超时（per-statement 语义，钉死）

> **B3 实战补注（2026-09-05，faulthandler 现场转储实证）**：conn.interrupt() 对
> 「等待文件锁/IO」状态的语句无效（只能打断计算中查询）→ 超时后 worker 可能滞留，
> fail-loud 上界 = 2×budget + join 窗口（≈2×budget+20s）。worker 为 daemon 线程，
> 滞留不阻塞进程收尾；QS_DUCKDB_QUERY_TIMEOUT 事件照常落账（可观测性不受影响）。

- **语义**：`QS_DUCKDB_QUERY_TIMEOUT_S` = **单语句（per-statement）超时预算**，默认 30s。**绝不能是回测级总预算**——分片加载总耗时超预算被杀 = 新造「静默空数据出回测」事故（审核硬性要求①）。分片后的每条语句独立计时、独立受预算保护。
- **实现机制（duckdb 1.5.5 实测确认，agent_workspace/a1_interrupt_probe.py）**：看门狗线程 + `conn.interrupt()`——3s 预算实测：3.021s 抛 `duckdb.InterruptException`（"INTERRUPT Error: Interrupted!"），线程干净退出，**中断后连接仍可用**（后续 SELECT 正常返回）。
- **超时处置（显式归因，禁止静默 None）**：InterruptException → 抛出带归因的运行时异常（QS_DUCKDB_QUERY_TIMEOUT 事件：SQL 片段前 80 字符 + 预算 + 实际耗时 + missing 码数），**事件写入回测 diagnose 汇总输出**（事后可检，审核附加条）——不降级为空数据继续跑；策略侧由此收到显式失败而非静默空。
- 实现形态：`_execute_with_timeout(conn, sql, params, budget_s)` 辅助函数，`_ensure_bars_in_cache` 及同文件大查询调用点统一接入（本设计期仅接 `_ensure_bars_in_cache`，其余调用点列清单后 eval 是否同批——**不扩面**，防修一送一）。

### P3 _get_conn 静默消音
- except:pass → 捕获后**一次性告警**（进程生命周期一次）：`QS_DUCKDB_CONN_UNAVAILABLE`（含 db_path + 异常文本，复用 L2039 独立探测取文本）+ **写入 diagnose 汇总**；None 返回契约**不变**（上层空数据行为兼容不变，但可在 diagnose 事后归因）。
- 心跳去重：同进程仅首条全量告警，后续静默计数、diagnose 汇总累计条数。

### 验收四门（照 v3，落点平移后细化）
1. **单测**：①分片等价——随机码集分片 vs 单条 SQL 结果逐行一致；②看门狗——mock 慢查询（大笛卡尔积）超预算 → InterruptException → QS_DUCKDB_QUERY_TIMEOUT 进 diagnose、异常显式抛出；③正常路径零变化（既有 149 套件 + 契约套件全绿 + 钉死失败清单不变——A2 开工前置先落盘精确失败清单）；④P3——connect 失败一次性告警 + None 契约不变。
2. **真实并发**：writer 持久 RW 连接在场时引擎回测正常出数（探针示证跨进程无冲突——验证生产形态不回归）。
3. **回归**：钉死清单不变 + 契约门全绿。
4. **黄金对比**：代表性策略（打板短窗 + fall_reversal）修复前后信号/订单/净值逐项一致。

### 回退条件
- 分片合并结果与单条 SQL 任何不一致 → 立即回退（等价性破损）；
- 看门狗误杀健康语句（分片 P99 << 预算仍被杀）→ 立即回退；
- diagnose 汇总输出改变既有回测结果文件 schema → 立即回退重新设计输出通道。
- 实施前 stash create + **store 持久化**（铁律③新细则）+ 精确文件清单 add。

## 4. 与既有裁定的对账
- QS_DUCKDB_LOCK_TIMEOUT_S：**删除**（复核裁定，connect 层无竞争）；
- QS_DUCKDB_QUERY_TIMEOUT_S：**per-statement 总预算语义**（默认 30s），由 QS_DUCKDB_QUERY_TIMEOUT_S 环境变量可调——实现期如命名与语义冲突以本节为准；
- 登记更名：F-DUCKDB-LOCK 描述改为「引擎数据层批量加载慢查询（PR7 冷启动）+ 连接失败静默空家族（_get_conn except:pass）」（复核已批准，A2 验收时同步 delta-notes）；
- 心跳/告警/diagnose 事件命名统一 QS_ 前缀，与既有 QS_MINUTE_SYNTH/QS_SCREEN_AUDIT 家族一致；
- 策略源码零改动、G3.5 双端语义、6 策略横验证不在本线（无产物重转——纯引擎内部观测网）。
