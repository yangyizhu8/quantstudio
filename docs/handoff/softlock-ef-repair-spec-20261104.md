# 软写锁自锁死修复实施规格（A+C+D+E+F 终版）

> **来源**：客户运维会话2（运维2）R92 乙节 + R102 E/F 扩围｜**批准**：总调度 2026-11-04（A+C+D 原批 + E+F 扩围照准）｜**实施**：会话节点1（主仓dev会话）｜**规格性质**：本文即运维2 R102 原件的完整汇编（客户事故链高风险面——凭本件实施，不凭摘要）
> **背景（三形态定谳）**：形态1/2=错序释放泄漏（S2/S4 隔离复现）；**形态3=0 字节空锁**（os.open 成功后非 OSError 异常⇒文件空+depth=0+不可解析⇒永久卡死——与客户 10:00:48 日志逐字吻合）；触发路径=`qfq_orchestrator:864 → reanchor_engine:2702 → 1770 run_postchecks → qfq_calendar:299 is_trading_day → 165 _connect → ensure_write_lock`；**自然异常源待证**（我方版本 _pid_create_time/_hostname 已 try/except 兜底不抛；客户 60 秒静默现场无日志）——故修复按「消除使能条件+自愈兜底」双保险设计，不依赖复现具体异常源。

## 一、改动文件（精确清单，2 个）

| # | 路径 | 改动 |
|---|---|---|
| 1 | `quantstudio/pipeline/snapshot_lock.py` | A+C+D+E+F 五处内核改动 |
| 2 | `tests/test_snapshot_lock_lifecycle.py`（新建） | 生命周期用例（S1/S2/S3/S4+交错100+并发4×200+自愈+审计留证+V6/V7） |

**不改动**：writers.py / qfq_calendar.py / qfq_resident_orchestrator.py / daemon.py 调用点（A 使两套释放机制语义统一，调用方无需改）；lock_path() 路径解析；任何 schema/数据面。

## 二、五处改动设计

### A — shallow 不参与计数（_depth 语义收敛 0/1）

```python
def acquire_write_lock(task_id="unnamed", timeout_s=30.0, poll_s=0.5):
    ...
    if _depth > 0:
        return WriteLock(task_id=task_id, _shallow=True)  # 不再 +1
    ...

def release(self):
    ...
    if self._shallow:
        return                    # 浅句柄不碰 _depth、不 unlink（由 owner 负责）
    _depth = 0                    # owner 释放即归零
    _unregister_handle(self)
    if _current_lock is self: _current_lock = None
    self._path.unlink(missing_ok=True)
    _restore_graceful_handlers()
```
- 语义：11 处调用方混用直接 `.release()` 与 `release_write_lock()` 在 A 后等价安全。
- `lock_held_locally()` 公开语义不变（`_depth > 0` 仍等价「本进程持锁」）。

### C — 同进程自愈（三重条件齐备才回收，防误删）

```python
except FileExistsError:
    holder = read_holder(); diag = diagnose_holder(holder)
    if (holder and holder.get("pid") == os.getpid()
            and holder.get("pid_create_time") == _self_create_time()
            and _depth == 0 and _current_lock is None):
        _audit("self_pid_lost_handle", holder, task_id)   # 写 write_lock_reclaim.log
        _force_unlink_lock_file()                          # 本进程已丢句柄 ⇒ 安全回收
        continue                                            # 立即重试
    ...既有 reclaim / 超时逻辑不变...
```
- 跨进程持有者一律不碰（fail-closed 既有逻辑保留）。

### D — 状态线程锁

`_STATE_LOCK = threading.RLock()`（可重入，acquire 内部调用不互斥自身）——覆盖 `_depth` / `_current_lock` / `_ensure_handles` 全部读写。

### E — 初始化段全异常兜底（新增·T5 已验证有效）

os.open 成功后，payload 写入段包裹 `try/except BaseException: os.close(fd)（若未关）+ unlink(missing_ok=True) + raise` ——消除 0 字节残留的根因（对 KeyboardInterrupt/MemoryError 等非 OSError 同样生效）。

### F — 空/不可解析锁回收判据（新增）

`diagnose_holder` 对空 payload / 不可解析返回 `readable=False` 时：若 **mtime 年龄 > 阈值（120s 荐值）** 且本机（host 不可判，保守默认）⇒ 标记 `reclaimable=True`（走**既有 CAS 与审计日志**通道回收）；**负向**：未超龄 ⇒ 不回收（防误删正在初始化的锁）。至少输出「空锁×年龄」明确判定语。

## 三、任务拆解 T1-T5（逐点门控·一点绿进下一点）

| 点 | 任务 | 验收判据（可机检+负向对照） |
|---|---|---|
| T1 | 写前快照+调用点审计 | **已完成**（节点1 R14：四符号无生产外部读写点；**约束 A 已消解**=build/lib 未入库不参与 import；**约束 B 在**=`tests/test_snapshot_lock.py` 直接访问三私有符号⇒须同步更新该测试） |
| T2 | 实施 A | owner+ensure×N+release×N+owner.release() ⇒ 文件消失；_depth 恒 0/1；**负向**：临时回退 A ⇒ 文件残留（证明修在 A） |
| T3 | 实施 C | 构造 S4 泄漏态 ⇒ acquire 自愈成功 + `write_lock_reclaim.log` 有 `self_pid_lost_handle`；**负向**：pid≠self 或 create_time 不匹配 ⇒ 不得回收 |
| T4 | 实施 D | 4 线程×200 acquire/release+混用直接句柄 ⇒ 终态无锁文件、零 WriteLockHeld、_depth==0；无死锁（60s 看门狗） |
| T5 | 回归+实跑 | pytest `tests/ -q` 零新增失败；隔离 `QS_WRITE_LOCK_DIR` 下 daemon 一轮采集+qfq apply 零 WriteLockHeld；git diff 仅 2 文件 |

**追加判据**：V6=T2 场景（非 OSError 注入）⇒ 锁文件不残留（E 生效）；V7=手工 0 字节锁+mtime 超龄 ⇒ acquire 可回收+审计日志；未超龄 ⇒ 不回收。

## 四、纪律与边界

- 判型：**纯恢复型（主）+ 新增检测型（自愈/回收——单列不混办）**。
- 测试一律 `QS_WRITE_LOCK_DIR=<临时目录>` 隔离（`QUANTSTUDIO_DATA_ROOT` 对锁路径无效——R91 已证）。
- 回退：单 commit revert；提交用精确 2 路径 add+uuid message+核整个 index+提交后三核（反向串车防护）。
- 复验方：运维2 五判据+V6/V7（dev 回执后跑）；节点1 在无锁窗补跑验收。
