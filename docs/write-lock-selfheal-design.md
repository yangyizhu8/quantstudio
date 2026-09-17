# 写锁死亡自愈 — 批一设计（P0 锁自愈 + D1）

- 状态：**已两轮审计放行 → 实施中**（2026-09-17）
- 依据：客户A（`D:\project\quantitative investment\QuantStudio`）与客户B（`D:\quantstudio`）写锁残锁事故
- 关联：`docs/governance-3a-write-lock-design.md`（3A 写锁收口，本批修订其陈锁语义）、
  `docs/governance-snapshot-design.md`（快照 create 共用同一把锁）、
  `docs/handoff/customer-lock-rescue-20260917.md`（D+0 止血件）、
  `docs/duckdb-crossproc-lock-plan.md`（另一族锁：DuckDB 原生锁 / `collector_run.lock`，**本批不涉及**）

---

## 0. 审批记录

| 轮次 | 结论 | 要点 |
|---|---|---|
| v2 → v3 | 有条件通过 | ① 拆批（P0 止血不得等 aligner 语义变更）；② D2 质疑「改引擎」→ 配置级优先；③ D3 属写入语义变更 → 独立轨；④ 心跳自动维持线程降级/砍掉；⑤ 取证先于改名 + 补真实日志回放 + 会话称谓统一 |
| v3 → v3-final | **复核放行，批准实施** | 五点全部落实、无保留；三条实施期提醒已硬约束化：D+0 止血与实施并行、T3 写前快照+协调窗口、AC5/AC-replay 为放行硬门 |

## 1. 问题定义（事故根因，逐条取证）

两客户同一缺陷各中一次：`.write_lock` 被**已终止进程**残留持有，此后全部写任务在 30 s
超时后失败，采集全线停更（客户A 陈锁年龄 390,915 s ≈ 4.52 天；客户B 21,295 s ≈ 5.92 h）。

- **L1 陈锁无自愈**：互斥原语 `os.open(O_CREAT|O_EXCL)`，释放仅 `release()` 内 `unlink`；
  `STALE_SECONDS=600` 只用于错误文案；无 `atexit`/信号兜底 → 进程非正常结束必留残锁。
  原裁定「陈锁不自动清除、人工确认」以「有人值守」为前提，无人值守 7×24 客户不成立。
- **L2 心跳不可作存活判据**：`.heartbeat()` 全仓零生产调用；长任务（分钟表分片写、
  `VACUUM INTO` 快照）持锁远超 10 分钟 → 「心跳超时」≠「持有者已死」。
- **L3 错误原因被吞**（`daemon.py` `last_err` 恒 None）与 **L4 无运维工具**（CLI 仅 `run`）、
  **L5 客户指南 T+1 自愈承诺失实**：归批二或同批文档修订。
- **D1（客户B 独立缺陷）**：`mcp_adapter.py:464` 读 `self._config`，`__init__` 从未赋值 →
  `_WIDE_TEXT_PASSTHROUGH` 集合 7/7 表 100% AttributeError（日志 7/7 对应）。

## 2. 改动范围（批一）

| 项 | 文件 | 说明 |
|---|---|---|
| S1 payload v2 | `quantstudio/pipeline/snapshot_lock.py` | 增 `v/host/pid_create_time/token/acquired_at`；`read_holder()` 只增键 |
| S2 死亡自愈 | 同上 | 六判据 + reclaim 互斥 + CAS + 审计 + 告警 |
| S3 所有权校验 | 同上 + `pipeline/writers.py`（+1 行调用） | `assert_still_owner()` / `assert_lock_owner()` → `WriteLockLost` |
| S4 优雅释放 | 同上 | `atexit` + `SIGINT`/`SIGTERM`（仅主线程；链式交还原处理器） |
| S5 文案升级 | 同上 | 按判定结论给出结论 + 处置路径（不引用批二命令） |
| S6 D1 | `pipeline/sources/mcp_adapter.py`（+1 行） | `self._config = dict(config or {})` |
| S7 文档 | 本文件 + 3A/快照设计 + 客户指南 | 陈锁语义修订、Q1 修订、锁残留处置章节 |
| S8 验收隔离 | 同上（`snapshot_lock.py`） | `QS_WRITE_LOCK_DIR` 锁目录重定向（**默认不设 → 与接入前逐位一致**）：测试/验收零碰生产 `data/snapshots/` |

**不做**：心跳自动维持线程（判据以存活为准，心跳仅辅助）、CLI `status`/`clear`、
任务级 fail-fast 预检、aligner 冲突列策略、因子快照失败语义（均归批二）、
DuckDB 原生锁族改动、任何性能优化与写入/水位/复权/回测语义改动。

## 3. 回收判据（六条全部满足才回收）

| # | 条件 | 判据名 | 不满足时 |
|---|---|---|---|
| 1 | `now - heartbeat > 600s` | 前置 | 继续等待（死锁者心跳仍新则最多等 ≤10 min） |
| 2 | `v=2` 且 `host == 本机` | 跨主机保护 | 不回收（verdict=`cross_host`） |
| 3 | `psutil.pid_exists(pid)` 为假 | 存活红线 | 存活 → 不回收（verdict=`stale_alive`） |
| 4 | `pid_create_time` 与本机实测不符 → PID 复用 | 复用排除 | 相符 → 视为存活 → 不回收 |
| 5 | legacy（无 host/token）：条件 1+3 成立即可回收 | `legacy_weak` | pid 缺失/不可解析 → 不回收 |
| 6 | `QS_WRITE_LOCK_SELFHEAL`（默认 1） | 回退开关 | `0` → 退回旧「仅告警」 |

**Windows 陷阱**：判活一律走 `psutil`，**禁用 `os.kill(pid, 0)`**（CPython 在 Windows 上
对任意信号都会真正终止目标进程）。

## 4. 回收协议（原子）

取回收互斥 `.write_lock.reclaim`（`O_CREAT|O_EXCL`）→ 重读 payload 与判定快照做
**CAS 四字段比对**（`pid/heartbeat/task_id/token`）→ 复核判定 → `unlink` → 释放互斥。
任一步不成立即放弃（退回等待），绝不强删。审计追加 `data/snapshots/write_lock_reclaim.log`
（单行文本，含持有者全字段 + 判据名 + 回收者 + 时间），同时 `WARNING` 结构化日志
（固定 `[write-lock]` 前缀，含 `持有者`/`pid=`/`lock_path` 锚点）。

### 4.6 S3 所有权校验（批一两条边界用例 — **T7 审计重点**）

接入点：`writers._write_locked` 入口 1 行 `assert_lock_owner()`（无竞争 = 一次文件读）。
判定对象 = 本进程**当前外层持锁句柄**（`_current_lock`），不是「所有历史句柄」。

| 情形 | 判定 | 行为 | 用例 |
|---|---|---|---|
| A. 锁文件存在，token == 自己 | 持有成立 | 通过（零副作用） | `test_owner_check_passes_while_holding` |
| **B. 锁文件存在，token != 自己（他人持锁）** | **已被他人取得** | **抛 `WriteLockLost`（fail-closed），且不覆盖他人 payload** | `test_lock_taken_by_another_owner_raises` |
| **C. 锁文件缺失（仅被删除/人工清理）** | 不构成双写事实 | **`O_CREAT\|O_EXCL` 原子重建并写回自己 payload**，记 `[write-lock]` WARNING 后继续 | `test_missing_lock_file_atomically_restored` |

**为什么 B/C 必须分开（审计要点）**：线性化点是**文件创建的 O_EXCL**——任一时刻至多一个持有者；
「文件缺失」本身不构成并发写入事实，而「他人持锁」才构成。若把 C 也判为丢失，会出现两类误伤：
① 进程内仍有未释放句柄而外部清理了锁文件（既有回归 `tests/test_qfq_reanchor_batch1.py` 的锁卫生
fixture 直接 unlink，首轮回归即以此暴露）；
② 运维按止血流程清理残锁而持锁进程仍在运行——字面语义会让该进程此后所有写入硬失败。
C 的原子重建使这两种情形恢复正确持有，同时**不放宽红线**（他人持锁仍立即失败）。

> 与回收判据（§3）正交：§3 管「别的进程死了、它的残锁怎么被安全接管」；§4.6 管「我自己
> 名义持有期间，锁文件的归属是否仍属于我」。两者共用同一线性化点，互不替代。

## 5. 行为等价与回退

- 无竞争时立即获得锁，单线程行为与接入前逐位一致；`acquire/ensure/release/read_holder`
  签名、异常类型、退出码、重入语义不变（`read_holder()` 仅增键）；
- 新增唯一语义：**持锁期间锁被他人取得 → `WriteLockLost`（fail-closed，可观测失败）**；
  仅「锁文件被删除」时**原子重建恢复持有**（不判丢失）——两条边界的完整论证与用例见 §4.6；
- 回退：`QS_WRITE_LOCK_SELFHEAL=0` 一行退回旧行为；代码回退按文件面定向（单文件为主）。

## 6. 验收隔离与证据机制（T7 用）

| 机制 | 用途 | 命令 |
|---|---|---|
| `QS_WRITE_LOCK_DIR` | 锁目录重定向：单测/验收把 `.write_lock`、`.write_lock.reclaim` 全部落在临时目录，**零碰生产快照目录**（`tests/test_snapshot_lock.py` 的 autouse fixture 已设） | — |
| `QS_WRITE_LOCK_AUDIT_LOG` | 回收审计重定向（进程内与子进程均生效——测试用 `_child_env()` 在**调用时**构造子进程环境，不冻结 import 期 `os.environ`） | — |
| 前/后基线比对（T7-2） | 区分「没有残留」与「本来就没有」 | `python agent_workspace/snapshot_dir_baseline.py <out.json>`（跑测前、跑测后各一次，SHA-256 比对） |
| 一行回退验证（裁定②，必做） | 证「`QS_WRITE_LOCK_SELFHEAL=0` 真实有效」（唯一止损能力证据） | `python agent_workspace/write_lock_rollback_probe.py`（A 不回收 / B 回收，全程 tmp 隔离） |

**注**：单测**不得**断言「生产目录里没有锁文件」——生产锁是活资源，本机 daemon 正常写入期间会
瞬时创建该文件，此类断言会假红（本批首轮即以此暴露）。「无残留」一律由前/后基线 diff 判定。

## 7. 验收（AC5 / AC-replay 为放行硬门）

| ID | 内容 | 位置 |
|---|---|---|
| AC1 | 陈锁 + 持有者不存在 → ≤1 s 回收 + 审计 1 条 + 取得锁 | `tests/test_snapshot_lock.py::test_stale_lock_dead_holder_reclaimed` |
| **AC-replay** | 两客户**真实 payload 回放**（legacy，年龄 390,915 s / 21,295 s）→ `legacy_weak` 回收 | `::test_replay_customer_payloads[客户A/客户B]` |
| **AC5** | 八进程并发回收同一陈锁 → **恰一获锁** + 审计恰 1 条 | `::test_reclaim_race_eight_processes` |
| AC2 | 存活持有者 + 心跳停更 → 不回收、锁文件保留（红线） | `::test_stale_heartbeat_live_holder_not_reclaimed` |
| AC3/AC4 | 跨主机不回收；PID 复用判死回收 | `::test_cross_host_lock_not_reclaimed` / `::test_pid_reuse_treated_as_dead_and_reclaimed` |
| AC7 | 外部删除锁文件 → `WriteLockLost` | `::test_lock_ownership_lost_raises` |
| AC-D1 | 7 张宽文本表逐一走 export 路由 + 覆写回落 | `tests/test_mcp_wide_text_routing.py`（5 用例 × 参数化 = 10） |
| AC10 | 既有契约不退化（互斥/重入/心跳/CLI 透传与退出码 2） | `tests/test_snapshot_lock.py` A 组 |
| AC12 | 客户侧：升级后一周期内全部任务成功，审计 0/1 条 | 客户验证清单 |

**结论口径（裁定③，收窄，不得外扩）**——本次验收只证以下三句，均不得写成「残锁根治」：

1. **「进程已终止后自愈成立」**：持有者进程已不存在（同主机；或 PID 复用已排除）时，陈锁由
   acquire 侧安全回收，写路径恢复——这正是两客户事故场景（客户A pid 26168 / 客户B pid 19968）；
2. **不覆盖「终止瞬间的立即释放」**：`SIGKILL` / 断电 / 强制重启下 `atexit` 与信号处理器
   都无法执行，锁文件必然残留——该残留由第 1 句的自愈在**心跳过期（>STALE_SECONDS=600 s）后**
   被回收，故最坏表现为**有界等待 ≤10 分钟**，而**不是**永不恢复；
3. **要覆盖「终止瞬间立即释放」属另一立项**（P1：写锁原语迁移既有 `filelock` 家族——进程死亡由
   OS 释放；触发条件：① 批一上线后再现非「进程死亡」型残留；② 客户 `data/` 落网络共享盘；
   ③ POSIX/macOS 客户部署；④ 年度架构评审）。

**红线不可动**：持有者**进程存活**时（哪怕心跳停更）一律不回收——误回收会直接制造双写者。


## 8. 批二（各自独立六步，不卡客户止血）

顺序：CLI `status`/`clear` → 任务级 fail-fast 预检（客户B 26 分钟白拉证据）→
D2 `stock_float_share` 重复列（**配置级优先**：先取证 `stock_daily_basic` 原始列 →
改 `alignment_rules.json` 映射，不动 aligner；确需引擎变更则独立立项 + 黄金等价 + 回灌影响面）→
D3 因子快照失败语义（先取证确证因果 → 独立正确性变更轨，未确证前保持现状）。

> **D2 取证已完成（2026-09-17，只读 MCP 接口，未实施改动）**：`qdb.stock_daily_basic` 同时含
> `float_share` 与 `free_share`，且两者语义不同、`free_share` 常为 NULL；交叉验算确认本管线
> target `free_share`（由 `float_share` 映射而来）= 流通股本口径（用于 derive `circ_mv`）。
> 配置级修法候选：给该条目补 `"free_share": "_raw_free_share"`（改名让位，零引擎改动）。
> 详见 `docs/evidence/write-lock-selfheal-implementation-20260917.md` §6。

## 9. 实施记录（本轮）

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/snapshot_lock.py` | payload v2 + 六判据回收 + CAS + 审计 + 所有权校验 + 优雅释放 + 文案升级 |
| `quantstudio/pipeline/writers.py` | import `assert_lock_owner` + `_write_locked` 入口 +1 行校验 |
| `quantstudio/pipeline/sources/mcp_adapter.py` | `__init__` 补 `self._config`（D1） |
| `tests/test_snapshot_lock.py` | 原 7 用例保留（陈锁用例改为「死亡回收」+ 新增「存活不回收」）+ 13 新用例 |
| `tests/test_mcp_wide_text_routing.py` | 新增（D1 路由回归，10 用例） |

测试现场隔离：锁文件与回收互斥位于 `data/snapshots/`（用例前后清理）；回收审计经
`QS_WRITE_LOCK_AUDIT_LOG` 重定向到临时目录，**不污染生产审计文件**。
