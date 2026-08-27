# 3A 写锁收口 — 微流水线设计（第 3 步前置，自第 5 步前移）

- 状态：**待审计**（设计文档，未实施）
- 依据：DSH A 线 v3+ 终审阻塞 2（循环依赖：快照等锁、锁排在第 5 步、修复等快照）
- 日期：2026-08-17

---

## 1. 问题：原排序的循环依赖

```
snapshot create 要求 MAIN/AUX 全部 locked=true
  ← locked=true 来自代码接入锁协议
    ← 原设计把锁接入排在第 5 步（读写隔离）
      ← 数据修复必须先有快照（写前快照保护）
```

## 2. 重排后的顺序（DSH 裁定）

```
3A 写锁收口（本设计）
→ 3B 快照 CLI 实现与首份修复前快照
→ 唯一写入会话执行修复（B 组重锚 / 同步恢复 / D2-F5 清理）
→ 写后快照 + D2 复检
→ 第 4 步基线
→ 第 5 步：剩余读写隔离治理（写入队列、长任务准入、events.py 纳管等流程件）
```

第 5 步保留内容：写入任务队列调度、长任务影响面准入、并行度收敛的**流程治理**；锁协议的**代码接入**全部前移到 3A。

## 3. 3A 改动设计

### 3.1 共享锁模块（新增，零框架行为改动）

`quantstudio/pipeline/snapshot_lock.py`：
- `acquire_write_lock(task_id, timeout_s=30) -> WriteLock` / `WriteLock.release()` / `heartbeat()`；
- 锁文件 `data/snapshots/.write_lock`（内容：PID、task_id、心跳时间戳）；获取失败/超时抛 `WriteLockHeld`（含持有者信息）；
- 装饰器/上下文管理器 `with_write_lock(task_id)`；
- **语义保证**：文件锁为协作锁，权威性由三层防御补齐（锁协议 + registry 准入 + 快照三重 hash 事后检测，见快照设计 §1/§2）。

### 3.2 写入口接入（registry v4 产物：MAIN 14 + AUX 32 个连接点，行级证据 + sqlite 目标嗅探；数字以 data/snapshots/write_path_registry.json stats 为准，由扫描器自动计算）

接入方式按模块分三类：

| 类 | 模块 | 接入方式 |
|---|---|---|
| **A 类：常驻/主入口（必须持锁）** | writers（daemon 同步）、qfq_calendar、qfq_maintenance/revision/observation（aux 族）、mcp_adapter、resident_orchestrator | 连接建立处包 `with_write_lock`（daemon 在任务批次粒度持锁，非连接粒度） |
| **B 类：一次性工具/CLI（入口守卫）** | qfq_formal_cutover(_cli)、schema_migration、reanchor_schema、formal_canary、orchestrator_cli、cutover_activation、events.py、GUI task_tab/db_helper、修复脚本族 | main() 入口处 `acquire_write_lock`，无锁即拒绝执行（fail-closed） |
| **C 类：测试/诊断脚本** | tests、_dbg_* | 不接入（不触生产库；registry 中标注 test-scope 排除） |

**覆盖边界声明（DSH 终审要求）**：registry 准入范围 = `quantstudio/**`（76 连接点）；**scripts/ 修复脚本族不在 registry 内**——其治理 = ①CLI 包裹器强制持锁（行为约束，见下），②快照 B2 三重 hash 事后检测（最终防线，锁外写入必然使 source_hash_pre ≠ post → 快照失败）。**不得将"registry 未含 scripts/" 误读为"无 scripts/ 写者"**。

- **locked=true 的翻转规则（DSH：禁止手工改）**：每完成一个模块接入，由 3A 实施产出"接入证据"（模块名 + 连接点行号 + 接入 commit + 测试证明无锁时拒绝运行），证据文件 `data/snapshots/lock_adoption_log.json`；`governance_write_conn_scan.py` 校验证据文件与 registry 一致后才置 `locked: true`（扫描器重新生成 registry 时自动执行）。

### 3.3 守卫强化（防绕过）

- B 类入口在锁获取失败时 `SystemExit(2)`；
- snapshot create 侧准入不变（registry 校验 + 退出码 5）；
- 可选（实施期评估）：A 类在 debug 模式打印未持锁警告。

## 4. 行为等价性边界（铁律）

- 锁模块为**新增并发协调层**，不改任何写入内容、顺序、语义；
- 接入后单线程运行行为与接入前完全一致（锁在无竞争时立即获得）；
- 验收：接入前后对代表性写路径（daemon 单任务、events.py 导入）的库内容 diff = 0；全量相关测试通过。

## 5. 测试与验收

1. 单测：锁互斥（两进程/线程竞争，后者获锁失败）、心跳、陈锁检测、release 后可再获取；
2. 守卫测试：B 类入口无锁时拒绝（exit 2）；
3. 翻转测试：lock_adoption_log 缺项时 registry 不置 true；伪造手工 true 会被扫描器重置；
4. 等价性验收（§4）；
5. 全部 MAIN/AUX 接入完成 → registry 自动翻 locked=true → **3B 解除 create 拒绝**。

## 6. 影响面与回退

- 新增 snapshot_lock.py + 各模块入口 1-3 行包装 + 扫描器校验逻辑；
- 回退：移除包装（单点）；锁文件与日志删除；
- 风险：A 类 daemon 持锁粒度过粗（批次级）可能延长锁窗口 → 粒度在实施时按任务边界核定，写入日志可观测。

## 7. 三项裁定（DSH 终审已裁定，固化）

1. **daemon 持锁粒度 = 任务批次级**。补充要求：批次边界日志可观测（锁获取/释放时间 + 任务 ID 落日志）；批次内即唯一写会话，无外部写者预期。
2. **GUI 入口 = GUI 操作即持锁**（短事务、持锁窗口短）。补充要求：锁获取失败时**弹窗提示持有者信息**（PID/task_id/心跳时间），禁止静默失败。
3. **修复脚本族 = CLI 包裹**（`python -m quantstudio.pipeline.snapshot_lock run <cmd>`）。补充要求：包裹器**透传退出码/stdout/stderr/环境变量**；文档化限制——**脚本 fork 出的子进程不继承锁**（全部写操作必须在包裹器进程内完成）。
