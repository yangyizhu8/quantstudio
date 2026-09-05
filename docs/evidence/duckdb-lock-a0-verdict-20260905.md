# A0 定谳报告：F-DUCKDB-LOCK 登记症状真形态（2026-09-05）

> 送审对象：ZCode 复核（六步流水线主线 A 第 1 步前置定谳）。定稿依据：三线方案 v3（已批准）修正①「A0 先定症状真形态（挂死 vs 静默空跑），落点依结论划定，结论前禁固化修复点」。

## 1. 登记症状（被定谳对象）
- v10-delta-notes-20260903.md L93：F-DUCKDB-LOCK（引擎 `_ensure_bars_in_cache` **写锁无超时**）。
- v10 线 E2E 实录（delta-notes L52 / pctchg-portability L276）：「duckdb 写锁竞争依旧（挂起栈与 v9 对照一致，环境性 BLOCKED 维持）」。
- 09-03 16:58 挂起栈定位（会话 turn 181，seq 611173 原文摘录）：挂点 = `duckdb_data_access.py:502 in _ensure_bars_in_cache` ← `query_bars_by_count_batch` ← `get_history`（ptrade_api L1330）← 产物 `_screen_market` 的 get_history_batch **日线调用**；当时假设「ensure=可能写 → 被锁阻塞 → 无限等待」。

## 2. 代码事实（本会话与审核方独立复测一致）
- `_ensure_bars_in_cache`（duckdb_data_access.py L487-510）：**无任何等待循环**；`_get_conn()` 返 None 即 return；函数体仅经连接执行 SELECT 并填充进程内 dict——**无 duckdb 写操作**，「写缓存/ensure=可能写」假设不成立。
- `_get_conn`（L124-140）：connect 失败 except:pass 静默返 None（fail-soft 家族）。
- `snapshot_lock.acquire_write_lock`（pipeline/snapshot_lock.py L105-132）：文件锁轮询含 **30s 超时上限**后 fail-closed 抛 WriteLockHeld——该锁不存在无限等待。

## 3. duckdb 1.5.5 语义探针（本会话实测，scratch 库零副作用，脚本 agent_workspace/a0_lock_probe*.py）
| 探针 | 场景 | 结果 |
|---|---|---|
| 1 | 进程1 持 RW 连接；进程2 connect(read_only) | **立即成功** 0.026s（无异常、无阻塞） |
| 1b | 进程1 持 RW；进程2 connect(read_write) | **立即成功** 0.018s |
| 2 | 进程1 持未提交写事务（INSERT 200 万行）；进程2 RO execute 聚合查询 | connect 0.019s；**execute 0.004s 返回旧快照**（MVCC），无阻塞 |
| 3 | writer 提交后 CHECKPOINT 场景（时序竞争未覆盖窗口本体，作残留缺口登记） | reader 三连查均 0.001s |

**结论：duckdb 1.5.5 跨进程（1RW+多RO、未提交事务、提交重写）connect 与 execute 均不冲突、不阻塞。**登记假设的「写锁竞争 → connect/execute 无限等待」机制在该版本跨进程场景**证伪**。（writers.py L423-426 所述 read_only/read_write「different configuration」冲突属**同进程**实例缓存行为，触发形态是 connect 立即抛异常（响亮）→ 被吞成 None → 归入静默空家族，同样不是挂起。）

## 4. 定谳
登记症状「写锁无超时/无限等待」**表述不成立**。真实机制为双机制叠加：
1. **慢查询被观测为挂起（主因）**：L502 的批量全历史 SELECT（PR7 冷加载路径，profiling_report_smallcap_phase4_20260812.md 在案：154s/天）在 stock_daily 大表 + 大 missing 集时单条 SQL 可达分钟级——无输出运行被 40min 后台清理器终止 → 形成「E2E 挂起/BLOCKED」表象。v9/v10「挂起栈一致」= 同一慢查询路径，与产物版本无关（与当时「与 v7+ 合成代码无关」的判断互证）。
2. **静默空家族（伴生风险）**：`_get_conn` except:pass 吞异常返 None → 引擎带空数据静默跑（22 轮前那类事故温床，审核附加条针对点）。
3. 残留缺口（诚实登记）：CHECKPOINT 窗口本体未被探针覆盖（时序竞争），以及 09-03 当晚真实并发进程形态未留快照——两者不改变主定谳（慢查询为主因的置信度不依赖此缺口），如复核方要求可补第四阶段探针或生产库只读观测。

## 5. A1 落点重划建议（依 A0 结论，送复核后定稿）
原条件批准设计（connect 锁超时+退避+告警）的落点平移：
- **P1 慢查询治理（主修复）**：L502 批量加载分片/限量 + 进度心跳（profiling 报告既有立项方向的延续），消除「挂起」表象；
- **P2 观测超时 + 诊断显式化**：execute 级超时参数（语义沿用审核建议：总预算秒数，默认 30s，超时即降级并归因）；QS_DUCKDB_SLOW_QUERY 一次性告警 + **写入回测 diagnose 汇总输出**（审核附加条平移至此层）；
- **P3 _get_conn 静默消音**：except:pass 改为一次性显式告警（保留 None 返回契约不变），防静默空跑；
- 原 connect 层 QS_DUCKDB_LOCK_TIMEOUT_S 是否保留轻量重试或删除，依复核裁定（探针示证 connect 层无竞争，倾向删除以免死代码）。
- 策略源码零改动不变；纯增益门槛不变（无锁/无慢查询场景行为字节级不变）。

## 6. 登记表述修正案（复核通过后同步 delta-notes）
F-DUCKDB-LOCK 更名：~~引擎 _ensure_bars_in_cache 写锁无超时~~ → 「引擎数据层批量加载慢查询（PR7 冷启动）+ 连接失败静默空家族（_get_conn except:pass）」。

## 7. 证据清单
- 代码锚点：duckdb_data_access.py L124-140 / L487-510 / L502；pipeline/snapshot_lock.py L105-132；pipeline/writers.py L423-432；backtest/events.py L128。
- 探针脚本：agent_workspace/a0_lock_probe.py（三组合）、a0_lock_probe2.py（未提交事务）、a0_lock_probe3.py（CHECKPOINT，残留缺口）。
- 历史证据：v10-delta-notes-20260903.md L52/L93；pctchg-portability-20260901.md L276；profiling_report_smallcap_phase4_20260812.md L51；backtest-align-golden-20260817.md L65-66（2026-08-17 环境性独占在案先例）。
- duckdb 版本：1.5.5（python -c 实测）。
