# write-integrity R1+R3a+R3b 验收证据（六步④）

实施轮：2026-10-08 03:10-03:55 · 方案：docs/write-integrity-r123-design.md（②审计 PASS + 四裁定）
实施方式：委派 zcode_code（GLM）一次性实施；DSH 会话全程核验（diff 逐段审查 + 验收测试 + 既有红 HEAD 复证）。委派调用本身超时（10min 墙钟）但产物完整落地，已按纪律全部重验。

## 四裁定落实核对

| 裁定 | 落实 |
|---|---|
| ① R3a=甲 | supersede_stale_intents SQL 扩第五可清条件（活跃态 + updated_at < now−N），单点改动 ✓ |
| ② N=4h | _STALE_ACTIVE_CYCLE_S = 4*3600（qfq_resident_orchestrator.py 模块级常量；_stale_active_cutoff 返回 BJ 墙钟 naive datetime，与 _now_ts 同基比较）✓ |
| ③ R1 哨兵 | WriteResult .new/.updated = -1；batch_checkpoint rows_new/rows_updated = NULL（BatchAudit 签名改 Optional[int]，列本就允许 NULL）✓ |
| ④ R2 限大表 | **已补实施（04:0x-05:2x 轮，见文末 R2 补充章）**：两阶段——writer 写段表锁 + 引擎主事务同锁包覆；archify 影响面图随件 |

## 改动清单（git diff --stat）

- quantstudio/pipeline/writers.py +53/-（R1：探测 except→告警+新连接重试（重试连接重新 register _tmp_write，连接级视图隔离已处理）→accounting_unknown→-1 哨兵；正常路径公式逐位不变；汇总行分叉「新增 ? + 更新 ? 记账失效: <异常类型>」）
- quantstudio/pipeline/daemon.py +83/-（R1 消费端：普通路径 degraded 批 write_new/updated 置 NULL+计数；streaming 路径 -1 分片跳过累计防污染合计；两条 ✅ 汇总行 degraded 时显示 ? + accounting-degraded=N 尾注）
- quantstudio/pipeline/qfq_resident_orchestrator.py +79/-（R3a：常量+helper+SQL 扩条件+stale_active 统计+清理数 INFO 日志；R3b：applying 循环 _APPLY_HEARTBEAT_S=600 心跳 + _APPLY_STALL_ALARM_S=4h 一次性 ERROR 告警，fail-closed 不处置）
- tests/test_write_integrity_r123.py（新，340 行：R1 红测/重试/正常回归 ×3 + R3a 三态 ×3 + R3b 心跳/停滞 ×N）

## 验收测试（2026-10-08 03:4x 实跑）

```
python -m pytest tests/test_write_integrity_r123.py tests/test_writer_upsert_skip_identical.py   tests/test_writers_rw_backoff.py tests/test_writer_channel_contract.py   tests/test_qfq_resident_orchestrator.py -q
→ 58 passed, 1 failed（35.89s）
```

- 唯一 FAILED = test_qfq_resident_orchestrator.py::test_require_bootstrap_fail_closed —— **既有红**：以三文件路径级 stash 对照 HEAD 复跑同样失败（warning 行号 1497=HEAD 版），非本次引入。缺陷本体：require_bootstrap fail-closed 分支 status/error 已置但 CycleSummary.bootstrap_required 未置 True。**登记待修**（框架问题立即解决铁律：呈总调度归属立项，本方案范围外）。
- 过程中修正 2 处 ZCode 测试侧数据期望笔误（SEED 3 行 + BATCH 重叠 2 新 1 → 终态 4 行；记账断言全过、实现零改动）——只改测试文件断言与注释，未动实现。

## 观察登记（非本方案产物，未触碰）

- quantstudio/pipeline/sources/consume_whitelist_guard.py（外来未跟踪新文件，他会话产物）
- 工作区既有他人 M 文件若干（快照 fcf27269 已固化基线；后续提交将用精确 add 清单隔离）

## 生效路径（重要）

daemon 41572（06:00 周期持仓中）仍运行旧代码；R1/R3a/R3b/R2 生效需 daemon 重启。建议顺序：06:00 周期跑完 → V4 终报（旧代码行为存证）→ 重启 daemon → 首个 begin_cycle 的 supersede 清障日志（R3a 实战验证：预期清 5 个死周期 11 条 pending，含 ea956d12×3）→ 水位锚恢复可见。**重启时机呈总调度裁定**（daemon 生命周期跨会话占用纪律）。

---

# R2 补充章：per-table 写互斥两阶段实施与验收（2026-10-08 04:0x-05:2x）

## 机制修正（呈总调度复审）

方案 v1 前提「DuckDBWriter 是全部写者公共入口，天然覆盖三路」**被实施核验证伪**：

1. HEAD 上 writers.py 全部写方法本就包在进程级 `_conn_lock`（threading.Lock，79c4457 时代已存在）——writer.write 之间本无进程内竞态，单包 writer 侧表锁是必要但不足；
2. 真源旁路：qfq_reanchor_engine.py 在**自有连接自持事务**（BEGIN→COMMIT/ROLLBACK，不取 _conn_lock；writers.py *_on_conn 方法族 L1346-1351 注释明示该设计）内直 UPDATE 价格表数据行 front 四列（update_daily_front_from_staged / apply_minute_segments / apply_fresh_minute_staged）——与 streaming 分片 upsert 对同一 (code,time,freq) 行并发写，即取证② TransactionContext write-write 的进程内机理。

修正为两阶段（设计文档已随件修订 v1.1）：
- **一阶段**（原方案）：_SERIAL_WRITE_TABLES={stock_minutes,etf_minutes}（裁定④）；_write_locked/_write_passthrough 写事务段包表锁；
- **二阶段**（补）：锁注册表上提**模块级** table_write_lock(table)（跨模块同一把锁，RLock 防同线程嵌套死锁）；引擎主事务包 apply_reanchor_for_security（唯一价格表写事务：BEGIN→分钟/daily front UPDATE→postcheck→event/anchor→COMMIT/三型 ROLLBACK）以 ExitStack 接入 minute+daily 两表锁——锁先于 BEGIN 获取、COMMIT/ROLLBACK 后 finally 释放；纯控制表短事务（_record_failure_event，仅 qfq_reanchor_event）按裁定④范围外不取锁。

判型不变：**纯恢复型**（两路写入结果与串行执行一致，消除的是并发冲突作废）。代价：apply 主事务持锁期间同表 streaming 分片等待（回退条件 §5 吞吐退化 >30% 呈报重评仍适用）。

实施方式：委派 zcode_code 第二次调用（edit-only，未跑测试）；DSH 会话核验（py_compile ×2 PASS + diff 逐段审查：ExitStack 单释放点横跨 COMMIT/三 ROLLBACK、两表锁获取序、_record_failure_event 豁免正确）。

## R2 改动清单

- quantstudio/pipeline/writers.py：模块级 _TABLE_WRITE_LOCKS + table_write_lock()（serial→RLock setdefault；其余 nullcontext）；实例 _table_write_lock 委托模块函数（原实例登记表移除；调用点零变化）
- quantstudio/pipeline/qfq_reanchor_engine.py：顶层 import table_write_lock（循环导入已核无环）；apply_reanchor_for_security 以 ExitStack 在 BEGIN 前获取 minute+daily 两表锁、finally（COMMIT/ROLLBACK 后、staged 清理前）释放；三个 UPDATE 写点注释指向锁覆盖；_record_failure_event docstring 标注范围界定
- tests/test_write_integrity_r2_serial.py（新，6 测试：注册表同一性 / 实例委托 / 并发同表零异常+数据完整 / 外部持锁阻塞证明（载重断言）/ 非 serial 直通回归 / 引擎同注册表+接入点结构断言）

## R2 验收（2026-10-08 04:5x-05:1x 实跑）

```
大套（9 文件，含重锚 batch1+batch2 全量回归——引擎主事务被包覆，行为回归必跑）：
  245 passed, 3 failed（125.15s）
  败 3 = R2 测试分钟表 schema 笔误 ×2（freq NOT NULL——测试 df 缺 freq 列，修测试不改实现）
            + test_require_bootstrap_fail_closed（既有红，已 HEAD 复证归因，见上）
修正后定点重跑（R2 + r123 + writer 三套）：50 passed（27.44s）
```

重锚 batch1/batch2/empty_fresh_skip + orchestrator 套在大套中全数通过——引擎锁接入零行为回归。

## archify 影响面图（裁定④随件义务）

- 产物：`.archify/architecture-write-integrity-r2-20261008-0450/write-integrity-r2.html`（756KB，交互式）
- 门禁：finalize validate ✓ deliver ✓ check ✓（quality=showcase）；browser-check 门本机不可达（Edge CDP stdout 管道不兼容 + harness 浏览器守护 Job Object 限制）——以无头 Edge 整页截图（render-check.png 148KB）+ 视觉桥读图代证：五组件/边界/全部边标注/三卡片/图例完整呈现
- 内容：daemon 进程内 writer 写路径与重锚引擎主事务经同一把 table_write_lock（stock_minutes/etf_minutes）串行后写入 DuckDB 单文件；冲突真源（取证② 23 次 write-write）/两阶段修复/范围外清单三卡片随图

## 六步状态（R2 并入后）

①方案 ✓（v1 + v1.1 修订段）②审计 PASS（四裁定；机制修正段**呈总调度复审**）③实施 ✓（两阶段，zcode_code ×2 + 核验）④验收 ✓（245+50 pass，唯一败=既有红已隔离）⑤用户确认 **待** ⑥双仓库推送 **待⑤**。

推送清单更新：3 pipeline 文件（writers.py / qfq_reanchor_engine.py / qfq_resident_orchestrator.py / daemon.py 共 4 个）+ tests/test_write_integrity_r123.py + tests/test_write_integrity_r2_serial.py + docs/write-integrity-r123-design.md（含 v1.1）+ 本证据文档；.archify/ 目录不入库（工作产物）。
