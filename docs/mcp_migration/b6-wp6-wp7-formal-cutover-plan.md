# B-6 WP6 / WP7 正式切换与最终闭环计划（CodeBuddy 审核稿）

> **权威日期**：2026-08-06（星期四）  
> **状态**：REVISED DRAFT FOR CODEBUDDY G0 RE-REVIEW  
> **性质**：QuantStudio 本地回测框架层 + 正式数据切换高风险工作包  
> **本稿只定义计划**：不执行正式库写入、不切 active pointer、不推进正式水位、不运行正式 canary、不激活正式 `mcp-gen1`、不 stage/commit/push。  
> **审核目标**：CodeBuddy 应一次性给出完整的 P0/P1/P2 审核意见，避免拆成多轮零散反馈。

### G0 首轮审核与本次修订

CodeBuddy 首轮结论：`REVISE / P0=0 / P1=5 / P2=3 / 不允许进入大跨度 A`。

本次修订一次性合并全部意见：

| 编号 | 审核问题 | 修订位置 |
|---|---|---|
| P1-1 | authorization manifest 自身缺少防篡改、离线生成、独立哈希与存放约束 | §3.1 |
| P1-2 | Windows 双锁 owner/PID/stale 处置算法不完整 | §3.3.1 |
| P1-3 | formal file pre-SHA 与 Git 工作树检查口径混淆 | §3.3.2 |
| P1-4 | formal fault matrix 未要求与 staging WP1-WP3 逐项等价 | §3.5.1、§3.7.1、§7.1 |
| P1-5 | WP7-E3 正常水位释放入口不明确，formal runner 退出边界不足 | §5.3 |
| P2-1 | 双 aux 并存期、fallback 和退役条件未定义 | §3.6.1、§7.4 |
| P2-2 | 磁盘保守公式未固化 | §3.3.3、§7.4 |
| P2-3 | 观察期未绑定明确采集/重放成功次数 | §5.5、§7.4 |

本次仍只修订计划；不更新权威进度报告中的“审核通过”状态，不开始实现，不执行正式操作，不 Git stage/commit/push。

---

## 0. 当前冻结基线

### 0.1 已完成

- B-6 WP1-WP5 本地/staging 工作已完成并通过独立复核。
- PyQt 水位显示闭环已同步到 `origin/main`：
  - commit：`06590b6a54c2837d7e53e472ae9519790f3eed8c`
  - local/tracking/remote SHA 已验证一致。
- WP5 formal readiness 只读门通过：`P0=0 / P1=0 / P2=4 documented`。
- staging active identity 已验证：
  - `price_source=mcp`
  - `source_generation=mcp-gen1`
  - `cutover_id=b6_mcp_gen1_20260806`
- staging canary、after-COMMIT recovery、fault matrix、baseline replay、formal-readonly evidence 均已通过。

### 0.2 正式环境当前状态

- 正式主库：`data/quantstudio.db`
  - schema：`COMPLETE_2_0`
  - size：`14996746240`
  - SHA-256：`53e85feda38bc71a2595317092a5d03270b40558a9ea97c375bdf75703d5c677`
- 正式 aux：`data/qfq_aux.db`
  - size：`2641793024`
  - SHA-256：`5966790153c4966a8dfbe61b59f50880c98e4dd49705053fffacdcda9e2158b9`
- 正式 active pointer：未切换。
- 正式 `mcp-gen1`：未激活。
- 正式 source watermark：未切换到 `mcp-gen1` identity。
- 正式 canary：未执行。

### 0.3 现有硬门禁

现有实现故意拒绝正式库：

- `qfq_schema_migration.py` 对正式 main/aux 绝对硬拒绝，`--allow-production` 无效；
- `qfq_cutover_activation.py` / `cutover-activate` 仅允许 staging；
- `qfq_staging_canary.py` / `cutover-canary` 仅允许 staging；
- 不得通过删除/弱化现有 guard 来完成 WP6；
- 必须新建独立的、显式授权的 formal runner，保留所有 staging-only guard 原样生效。

---

## 1. 总体目标与工作包关系

### WP6：Formal Cutover Execution

负责唯一一次正式写窗口：

1. 正式迁移前复核、双锁和 fresh backup；
2. 正式主库 `COMPLETE_2_0 -> COMPLETE_2_1`；
3. 正式 generation-specific aux 初始化与 baseline；
4. legacy 非终态退役；
5. expected-old active pointer CAS；
6. 激活正式 `mcp-gen1` identity；
7. 保持正式价格水位不推进；
8. 发布不可变 handoff evidence。

### WP7：Post-Cutover Observation & Final Closure Audit

拆为两部分：

- **WP7-P（Preparation）**：可与 WP6 本地实现并行，但禁止访问正式库；
- **WP7-E（Execution）**：必须等待 WP6 handoff 后执行。

WP7-E 负责：

1. post-COMMIT 独立只读审计；
2. 正式 bounded canary，强制全局 watermark hold；
3. CodeBuddy 审核 canary；
4. 用户明确授权解除 hold；
5. 通过正常 QFQ gate 推进正式水位；
6. GUI / daemon / 数据浏览 / 黄金回测验证；
7. 1～2 个完整交易日观察；
8. 最终审计、报告与项目闭环判定。

---

## 2. 为减少审核轮次采用的“大跨度三阶段”流程

### 审核轮 G0：本计划一次性架构审核（当前轮）

CodeBuddy 一次性审核：

- WP6/WP7 边界；
- 正式 runner 隔离方式；
- 授权、备份、锁、sidecar、事务与 recovery；
- active pointer / mcp-gen1 / watermark / canary 顺序；
- 大跨度执行与退出条件；
- 文档和 Git 治理。

**G0 PASS 后只授权进入本地实现，不授权正式写入或 Git 同步。**

### 大跨度 A：WP6 实现 + WP7-P + 全量 staging 演练

一次性完成下列本地工作：

- formal runner 代码；
- formal authorization manifest 契约；
- backup/rollback/handoff evidence 契约；
- formal post-cutover auditor；
- formal held-canary runner；
- observation/final-audit 工具；
- 全部单元、集成、hard-crash、别名路径、并发锁测试；
- 在 output 全量副本上完整模拟 WP6 + WP7 immediate canary；
- README、strategy toolbox、prompt engineering、生产 checklist、runbook、设计文档更新；
- 不接触正式库，不 Git 同步。

完成后由 CodeBuddy 做一次集中审核（G1）。

### 审核轮 G1：代码 + staging 全量演练集中审核

CodeBuddy 应一次性核查：

- 所有代码文件；
- 所有文档；
- 全量测试；
- staging 完整执行证据；
- formal path hard refusal 回归；
- formal runner 只能由一次性 authorization manifest 启动；
- rollback / after-COMMIT recovery；
- handoff / canary / watermark release 边界。

G1 PASS 后需要两个独立、明确的用户确认：

1. **框架代码 GitHub 同步确认**；
2. **正式切换批次授权**。

二者不得因 CodeBuddy PASS 自动推断。

### 大跨度 B：WP6 正式执行 + WP7 immediate audit + held canary

在一个维护窗口内按条件链执行：

```text
WP6 preflight
  -> fresh backup
  -> formal migration
  -> aux/baseline
  -> legacy retirement + pointer CAS + mcp-gen1 active
  -> watermark remains held
  -> publish handoff
  -> WP7 immediate read-only audit
  -> formal bounded held-canary
  -> publish evidence
  -> STOP
```

本阶段**不解除 watermark hold，不启动正常生产增量，不宣布闭环**。

完成后由 CodeBuddy 做一次集中审核（G2）。

### 审核轮 G2：正式迁移 + held canary 证据审核

CodeBuddy 审核：

- 正式迁移前后证据；
- migration report；
- after-COMMIT classification；
- active pointer；
- mcp-gen1 aux；
- legacy retirement；
- canary；
- watermark unchanged；
- rollback 可用性；
- P0/P1/P2。

G2 PASS 后，用户再明确授权：

> 允许解除正式 watermark hold，通过正常 QFQ gate 推进水位并进入观察期。

### 大跨度 C：WP7 水位释放 + 观察期 + 最终审计

一次性完成：

- 正常正式采集周期；
- QFQ gate 正常 committed；
- 四价格表 watermark 正常推进；
- 下一轮增量幂等；
- GUI 单任务、Run All、fallback 显示；
- daemon 启停/恢复；
- MCP API；
- 黄金回测一致性；
- 1～2 个完整交易日观察；
- 监控告警验证；
- 最终 evidence、Run Card、权威报告。

完成后 CodeBuddy 做最终集中审核（G3）。

### 审核轮 G3：最终闭环审核

只有 G3 返回：

```text
PASS
P0=0
P1=0
P2 全部解决或由用户明确接受
```

才允许宣布 B-6 cutover 闭环。

整个 MCP 全数据源替代项目是否闭环，还需另外核对域名/SSL、客户外网、凭据、合规和项目级技术债。

---

## 3. WP6 详细实施范围

## 3.1 新建独立 formal runner，不修改 staging guard

建议新增：

- `quantstudio/pipeline/qfq_formal_cutover.py`
- `quantstudio/pipeline/qfq_formal_cutover_cli.py`
- `quantstudio/pipeline/qfq_formal_authorization.py`
- `tests/test_qfq_formal_cutover.py`
- `tests/test_qfq_formal_cutover_hard_crash.py`
- `docs/mcp_migration/b6-formal-cutover-runbook.md`

核心要求：

1. staging runner 保持原有正式库硬拒绝；
2. formal runner 不接受通用 `--allow-production`；
3. formal runner 必须消费一次性、不可覆盖、可独立校验完整性的 authorization manifest；
4. runner 不负责生成真实 authorization manifest，真实 manifest 只能由用户/项目负责人在正式执行授权时离线生成；
5. authorization manifest 必须绑定：
   - schema/version；
   - exact Git commit SHA 与正式执行 checkout canonical root；
   - exact formal main/aux canonical path；
   - exact pre-cutover main/aux SHA/size/mtime；
   - exact config files SHA；
   - exact cutover_id / source_generation / aux_db_path；
   - 允许操作集合；
   - `watermark_release_authorized=false`；
   - 分操作 grant：`wp6_formal_cutover` 与可选 `wp7_held_canary`；
   - 每个 grant 使用独立 nonce，禁止 WP6 与 canary 共用 nonce；
   - 维护窗口 ID；
   - authorization issuer/approved_by；
6. manifest 原始文件字节计算 SHA-256，不对 parse 后 JSON 重排再计算；
7. 用户授权消息必须通过独立通道同时下发：
   - authorization manifest 文件路径；
   - manifest 原始字节的 exact SHA-256；
   - 明确授权操作范围；
8. runner CLI 必须同时要求 `--authorization <path>` 与 `--authorization-sha256 <64hex>`；expected hash 不能从 manifest 本身读取，也不能从同目录 companion 文件自动读取；
9. runner 在解析 JSON 前先读取原始字节并校验 SHA-256；hash 不一致、文件变化或二次读取内容变化立即 fail-closed；
10. manifest 存放在项目仓库、`data/`、正式 DB 目录和 run evidence 目录之外的独立授权根目录，例如：

   `D:\miniQMT策略实盘\私募工作文件\QuantStudio-MCP全数据源替代任务文件\formal_authorizations\<cutover_id>\authorization.json`

11. 授权根目录必须 canonical 校验，manifest 不得位于 symlink/junction 指向的正式 DB/evidence/repo 目录；
12. runner 不修改 manifest；成功读取后在 WP6 run-dir 通过 `O_CREAT|O_EXCL` 创建 consumption record，记录 manifest SHA、nonce、授权范围、执行 commit 和消费时间；
13. nonce ledger 按 grant 拒绝重复 nonce，即使换 manifest 路径、文件名或 run-dir 也不得重放；WP6 消费只标记 `wp6_formal_cutover`，held-canary 另行消费 `wp7_held_canary`；
14. nonce ledger 必须位于跨 run-dir 持久化的独立授权状态根，例如 `<authorization_root>\consumed\<grant>\<nonce>.json`，不得放在本次 `output/` run-dir；authorization root、`consumed` 父目录和 ledger 文件都要做 canonical/symlink/junction 校验；
15. runner 在打开正式 read-write 连接前，以 `O_CREAT|O_EXCL` 原子创建 nonce ledger marker，记录 grant、nonce、manifest raw SHA、cutover_id、commit SHA、runner PID/create_time 和 reserved_at；marker 一旦创建即烧毁该 nonce，后续即使前次因 preflight/backup 失败退出也必须使用新授权，禁止删除 marker 后重试；
16. 若 ledger marker 已存在，无论其内容与当前请求相同或不同，均按 replay 立即 BLOCK；run-dir consumption record 只作为本次执行证据并绑定 ledger marker SHA，不能替代跨 run-dir ledger；
17. consumption record 和 nonce ledger 只证明消费/防重放，不能替代 manifest SHA 防篡改；
18. manifest 文件和 authorization state root 在正式执行前由用户设置只读/受限 ACL；ACL 仅作附加防护，不能替代 SHA 校验和 O_EXCL；
19. 任何授权字段、文件 hash、路径、commit 或权限范围不一致，必须在打开正式 read-write 连接前退出；
20. manifest 可同时携带 WP6 与 held-canary 两个独立 grant，但 formal runner 只能消费 WP6 grant；WP7 canary runner 只能在有效 handoff/exit evidence 后消费 held-canary grant；manifest 绝不能包含 WP7-E3 watermark release grant。

### 3.1.1 Authorization trust root 与审核证据

G1/G2 证据必须分别记录：

- 用户原始授权消息或其不可变引用；
- manifest path；
- expected SHA-256；
- computed SHA-256；
- raw byte size；
- nonce；
- authorization scope；
- consumption record path/hash；
- runner 拒绝从 manifest 内部自我声明 hash 的测试结果；
- manifest 单字节篡改、权限范围篡改、watermark flag 篡改、pre-SHA 篡改均被拒绝的测试。

## 3.2 不得直接给现有 migration API 增加生产绕过开关

禁止方案：

- 给 `qfq_schema_migration.py` 增加可公开传入的 `allow_production=True`；
- 修改 `_assert_not_production()` 使正式库可绕过；
- 让现有 staging CLI 接受正式路径；
- 通过 monkeypatch / 环境变量关闭 guard；
- 复制正式库到别名路径后利用 samefile 漏洞。

允许方案：

- 抽取只接收已验证连接/上下文的内部 migration core；
- staging wrapper 始终执行 `_assert_not_production()`；
- formal runner 只有在 authorization、双锁、fresh backup、sidecar 和 pre-hash 全通过后才能调用内部 core；
- 正式入口和 staging 入口具有不同类型/能力对象，测试保证不能互换。

## 3.3 WP6 正式 preflight

正式写入前必须同时满足：

- formal runner 当前 checkout 的 HEAD 与 authorization 中的 commit SHA 一致；
- 本地 tracking `origin/main` 与 HEAD 一致，并在维护窗口前固化 `git ls-remote` remote SHA evidence；
- tracked 受管代码/配置文件无 staged 或 unstaged diff；
- 正式 main/aux canonical path 与授权一致；
- 正式 main 为 `COMPLETE_2_0`；
- 正式 main/aux 文件 SHA、size、mtime 与授权一致；
- 正式 active pointer 未切换；
- 正式 mcp-gen1 未激活；
- `.daemon.lock`、`.collector_run.lock` 按 §3.3.1 算法安全独占；
- daemon status 按进程身份五验分类；
- 无存活 writer/collector/GUI 手动采集；
- WAL/journal/SHM 按锁与 owner 证据判定为安全；
- 可用磁盘满足 §3.3.3 固化公式；
- report、backup、handoff、rollback manifest 路径均为新路径且不可覆盖；
- authorization manifest 原始字节 SHA 与用户独立下发 hash 一致。

### 3.3.1 Windows 双锁与 stale 处置算法

锁文件存在性和 mtime **不能单独**作为 owner 存活或 stale 的判断依据。

正式算法按以下固定顺序执行：

1. 解析 canonical 路径：
   - `data/.daemon.lock`
   - `data/.collector_run.lock`
   - `data/daemon_status.json`
2. 读取 `daemon_status.json`，使用现有 `verify_daemon_identity()` 五验：
   - PID 存活；
   - process create_time 与记录值在既有容差内；
   - exe canonical path 一致；
   - cmdline 包含 `quantstudio.pipeline.daemon`；
   - instance_token 按现有规则匹配；
3. identity=`alive`：立即 BLOCK；formal runner 不发送 stop、不 kill、不清 status；
4. identity=`denied`：立即 BLOCK；权限不足不得推断 stale；
5. identity=`stale`：只表示 status 中记录的进程身份不再可信，**不代表锁可删除或 DB 可写**；
6. 按固定顺序非阻塞获取 OS/filelock：
   - 先 `.daemon.lock`；
   - 再 `.collector_run.lock`；
   任一锁获取失败即 BLOCK，不参考 mtime 放行；
7. 两锁获取后重新执行一次 daemon identity 五验和进程扫描，防止 TOCTOU；
8. 扫描当前机器进程 cmdline，若存在 QuantStudio daemon、collector、GUI manual worker 或已知 writer 命令，但无法证明属于本 runner，则 BLOCK；
9. stale status 只有在以下全部满足时才能清理：
   - identity 精确为 `stale`，不是 `denied`；
   - 双锁已由本 runner 持有；
   - 二次进程扫描未发现 owner；
   - status 原文件先复制到本次 WP6 evidence，记录 SHA/size/mtime；
   - authorization scope 明确允许 `archive_stale_daemon_status`；
   - 通过 canonical path 删除原 status，不删除 lock 文件；
10. lock 文件 mtime 仅写入 evidence，绝不作为自动 stale 阈值；
11. formal runner 禁止自动强制 kill；
12. 如发现 alive daemon，需要用户另行执行正常 graceful stop；若 graceful stop 失败，只有新的明确指令同时指定 PID、create_time、instance_token 才允许进入 force-stop 流程；force-stop 不是 WP6 runner 的子功能；
13. 双锁持有后，在打开正式 read-write 前再次检查 sidecar 和正式文件 evidence；
14. WP6 所有正式写步骤结束、连接关闭、handoff durable 后，按相反顺序释放 collector lock、daemon lock；
15. 任一异常路径都必须 finally 释放本 runner 自己持有的锁，但不得删除他人 lock/status。

### 3.3.2 Git 身份、tracked 工作树与 formal file pre-SHA 的独立口径

必须区分三个互不替代的证据域：

#### A. 代码身份

- `git rev-parse HEAD == authorization.git_commit_sha`；
- 当前 repo canonical root 写入 evidence；
- `git rev-parse origin/main == HEAD`；
- 维护窗口前执行 `git ls-remote origin refs/heads/main` 并固化 remote SHA；
- authorization manifest 可以由另一离线环境生成，但 formal runner 只信任 manifest 中批准的 commit SHA，并在实际执行 checkout 本地验证。

#### B. tracked 工作树干净

只检查 Git tracked 文件：

```text
git diff --quiet --
git diff --cached --quiet --
git status --porcelain --untracked-files=no
```

必须无 staged/unstaged tracked diff。

以下内容不参与工作树干净判定，也不得因此误拒：

- `.gitignore` 已忽略文件；
- `output/` evidence；
- `data/*.db`、zip、backup；
- `.reasonix/`；
- `scripts/_probe*`、临时日志等 untracked 文件。

但 runner 仍必须确保这些 untracked/ignored 路径不能被当作 authorization、正式 runner 代码、正式配置或 report 输入。

#### C. 正式文件 pre-evidence

`pre-SHA` 只指正式 main/aux 文件本身：

- canonical path；
- SHA-256；
- size；
- mtime_ns。

配置文件另算 config SHA；代码身份另用 Git SHA。三者不得混写为一个 `pre-SHA`。

正式 main/aux evidence 必须：

1. 在授权生成时冻结；
2. 在 WP6 preflight 首次复算；
3. 双锁获取后再次复算或核对稳定证据；
4. 打开 read-write 前最后一次确认；
5. 任一次不一致立即 BLOCK，不能用新 hash 自动改写 authorization。

### 3.3.3 磁盘保守公式

固化公式：

```text
reserve_bytes = max(10 GiB, ceil(0.20 * (formal_main_size + formal_aux_size)))
required_free_bytes =
    5 * formal_main_size
  + 2 * formal_aux_size
  + reserve_bytes
```

覆盖：

- fresh main/aux backup；
- migration shadow/temporary objects；
- recovery branch/evidence；
- generation-specific new aux；
- WAL/journal/SHM 和 report 余量。

按当前冻结 size 计算：

```text
reserve_bytes       = 10737418240
required_free_bytes = 91004735488
```

要求：

- runner 和 runbook 使用同一个常量/函数，不允许文档与代码公式漂移；
- 使用运行时实际 size 重新计算，不把上述基线数值写死为执行值；
- `free_bytes < required_free_bytes` 立即 BLOCK；
- 仅能提高保守系数，降低系数属于设计变更，需重新审核。
## 3.4 Fresh backup 与恢复证明

在双锁窗口内创建：

- `quantstudio.db` fresh backup；
- `qfq_aux.db` fresh backup；
- 当前配置目录快照；
- Git SHA/配置 hash；
- backup manifest；
- restore plan；
- restore verification report。

必须验证：

- source 文件复制前后证据一致；
- backup SHA 与 source SHA 一致；
- backup main 只读检测为 `COMPLETE_2_0`；
- backup aux `PRAGMA quick_check=ok`；
- 备份恢复到临时路径后可打开；
- 不在正式路径执行恢复演练。

## 3.5 正式主库迁移

迁移阶段只允许 schema 迁移：

- `COMPLETE_2_0 -> COMPLETE_2_1`；
- 不补行情数据；
- 不重算 QFQ；
- 不推进 source watermark；
- 不切 active pointer；
- 不激活 mcp-gen1；
- 不运行 canary。

验收：

- source status=`COMPLETE_2_0`；
- target status=`COMPLETE_2_1`；
- price table row counts/content hashes 不变；
- source watermark 旧值不变；
- migration report durable；
- pre-COMMIT fault 全回滚；
- after-COMMIT interruption 可通过只读 `ALREADY_CURRENT` 路径分类，不盲目重跑。

### 3.5.1 与 staging fault matrix 的逐项等价要求

formal runner 不能只复制一份相似逻辑。大跨度 A 必须采用以下之一：

1. 抽取 staging/formal 共用的事务 core，由 staging/formal guard wrapper 分别调用；或
2. 若因生产授权隔离不能共享入口，则以同一状态机规范和版本化测试向量证明逐项语义等价。

现有 staging guard 继续拒绝 formal；formal wrapper 继续要求 authorization。共享事务 core 不负责路径授权。

必须对照 staging WP1-WP3 的：

- `evidence/fault_matrix.json`；
- `evidence/after_commit_recovery.json`；
- `evidence/final_audit.json`；
- `qfq_cutover_activation.py` 的 fault point 名称和状态后置条件。

formal runner 必须保留完全相同的 activation fault point：

- `after_retirement`；
- `after_pointer_delete`；
- `after_new_status`；
- `after_pointer_insert`；
- `before_commit`；
- `after_commit_before_report`。

此外 schema migration 自身继续保留既有：

- pre-COMMIT failure/rollback；
- migration `after_commit_before_report`；
- `COMPLETE_2_1 -> ALREADY_CURRENT` 只读恢复。

要求区分两个 after-COMMIT 边界：

- schema migration COMMIT 后、migration report 完成前；
- activation COMMIT 后、activation/handoff report 完成前。

两者都必须使用独立 Windows 子进程测试，严格 exit code `92`，串行执行；不得接受 `0xC0000005`，不得用重试掩盖。

独立测试证据必须同时包含：

- fault point；
- child exit code；
- pre/post schema；
- cutover status；
- active pointer；
- legacy trigger/intent/cycle counts；
- committed/dead_letter counts；
- source watermark evidence；
- report/handoff state；
- recovery classification；
- 与 staging 语义向量逐项比较结果。

任何 pre-COMMIT fault 后存在部分退役、部分 pointer、部分 status 或水位变化，均为 P0。

## 3.6 正式 mcp-gen1 aux 与 baseline

目标建议使用 generation-specific 文件：

- `data/qfq_aux_mcp_gen1.db`

要求：

- 不覆盖 `qfq_aux.db`；
- O_EXCL 创建或验证为空；
- SQLite schema 初始化；
- `adj_factor` / `fund_adj` 数据按冻结流程准备；
- integrity check=ok；
- aux path 写入 cutover record 后不可变；
- baseline build 只建立历史 baseline，不产生历史 trigger；
- baseline rows 与 staging 冻结口径一致；
- immediate replay new trigger=0；
- pending slot audit 三项为 0。

### 3.6.1 双 aux 并存治理、fallback 与退役条件

切换后允许以下两个文件并存：

- legacy：`data/qfq_aux.db`；
- dynamic：`data/qfq_aux_mcp_gen1.db`。

路由规则必须硬编码为 identity 驱动，不按“文件是否存在”fallback：

- `source_generation=mcp-gen1` 只能路由到 cutover record 冻结的 `qfq_aux_mcp_gen1.db`；文件缺失、hash 不符、integrity 失败一律 fail-closed，绝不 fallback 到 `qfq_aux.db`；
- `source_generation=xtquant-legacy` 只有在明确 legacy audit/rollback 路径中才能读取 `qfq_aux.db`；
- active pointer 切换后，正常 mcp runtime 禁止打开 legacy aux read-write；
- legacy aux 保持只读回滚证据，不删除、不覆盖、不继续写新 observation；
- 审计必须记录两个 aux 的 open path、identity、SHA、size、mtime、integrity 和 sidecar；
- 监控必须告警 dynamic identity 打开 legacy aux、legacy aux mtime 变化、dynamic aux integrity/sidecar 异常。

旧 aux 的退役触发条件：

1. G3 最终审核 PASS；
2. WP7 观察硬计数全部满足；
3. rollback retention 结束或用户明确接受不再回滚 legacy；
4. 无任何配置、active pointer、cutover record、进程日志或审计证明仍引用 legacy aux；
5. 退役前生成 final SHA/integrity/evidence；
6. 用户另行明确批准归档/删除。

在满足上述条件前，旧 aux 只能标记 `legacy_readonly_retained`，不能标记“已退役”。

## 3.7 Legacy 退役 + active pointer CAS

在一个显式事务内完成：

1. 重新验证新 cutover 为 `baseline_validated`；
2. 验证 expected-old active pointer；
3. 验证 legacy lease 不存在；
4. legacy started cycles -> interrupted；
5. legacy pending intents -> superseded；
6. legacy non-terminal triggers -> superseded；
7. 保留 committed/dead_letter；
8. old active -> superseded（如存在）；
9. new cutover -> active；
10. 插入唯一 active pointer；
11. 验证 source_watermark 未变化；
12. durable COMMIT。

必须覆盖 fault points：

- after_retirement；
- after_pointer_delete；
- after_new_status；
- after_pointer_insert；
- before_commit；
- after_commit_before_report。

### 3.7.1 Activation fault matrix 后置条件

formal activation 的事务 core 与 staging 语义必须逐项等价：

| Fault point | 事务预期 | 必须验证 |
|---|---|---|
| `after_retirement` | ROLLBACK | legacy cycle/intent/trigger 全恢复；new cutover 仍 baseline_validated；pointer 不变；watermark 不变 |
| `after_pointer_delete` | ROLLBACK | old pointer/status 恢复；retirement 全恢复；new cutover 未 active |
| `after_new_status` | ROLLBACK | new cutover 回到 baseline_validated；pointer/legacy 全恢复 |
| `after_pointer_insert` | ROLLBACK | 不留 active pointer；new/old status、legacy 全恢复 |
| `before_commit` | ROLLBACK | 所有可观察状态与 transaction 前逐项一致 |
| `after_commit_before_report` | COMMITTED | new cutover active；唯一 pointer 正确；legacy retirement durable；committed/dead_letter 和 watermark 守恒；只允许 report/handoff 未完成 |

pre-COMMIT rollback 比较必须覆盖：

- qfq_source_cutover；
- qfq_active_cutover；
- qfq_trigger_queue；
- qfq_cycle_run；
- qfq_watermark_intent；
- source_watermark；
- committed/dead_letter counts；
- pending slot audit。

after-COMMIT recovery 必须：

1. 新进程以 read-only 重新打开；
2. 识别 activation 已 durable；
3. 不再次执行 retirement/pointer CAS；
4. 生成新的 recovery evidence，状态为 `ALREADY_ACTIVE` 或冻结的等价枚举；
5. 补发 handoff/report 时使用新 O_EXCL 路径，不覆盖 interrupted report；
6. 与 staging `after_commit_recovery.json` 的 active pointer、legacy counts、watermark、committed/dead_letter 语义逐项一致。

## 3.8 WP6 handoff evidence

WP6 成功后必须发布不可变 `formal_cutover_handoff.json`，至少包含：

- formal main/aux before/after evidence；
- backup/rollback manifest；
- migration report status；
- source/target schema status；
- cutover_id；
- price_source/source_generation；
- active pointer；
- aux_db_path + aux SHA/integrity；
- legacy retirement counts；
- committed/dead_letter preservation；
- watermark evidence unchanged；
- `connections_closed=true`；
- `locks_release_pending=true`，明确 handoff 发布时双锁仍由 child 持有，不能虚假声明已经释放；
- formal runner child PID/create_time；
- expected exit code；
- after-COMMIT classification；
- `formal_canary_authorized=true/false`；
- `watermark_release_authorized=false`。

handoff 文件不得把“自身 SHA-256”写入自身内容。child 必须以 `O_CREAT|O_EXCL` 发布并对文件和父目录执行 durable flush；handoff 发布后，supervisor 等待 child 退出，再从 handoff 原始字节独立计算 SHA-256，并发布 `formal_runner_exit_evidence.json`，证明 child 已退出、无 descendants、双锁可重新获取且已由 supervisor 释放，并绑定 handoff raw SHA。handoff 不能自行声明已退出或双锁已经释放。

WP7-E1/E2/E3 均必须同时验证 handoff 与 exit evidence，并独立复算 handoff raw SHA；缺一或 hash 绑定不一致立即 BLOCK。

WP6 出口必须明确：

```text
formal migration complete
mcp-gen1 active
active pointer correct
watermark still held
normal production collection not started
```

---

## 4. WP7-P 详细准备范围（可与大跨度 A 并行）

建议新增：

- `quantstudio/pipeline/qfq_formal_postcutover_audit.py`
- `quantstudio/pipeline/qfq_formal_canary.py`
- `quantstudio/pipeline/qfq_formal_observation.py`
- `tests/test_qfq_formal_postcutover_audit.py`
- `tests/test_qfq_formal_canary.py`
- `tests/test_qfq_formal_observation.py`
- `docs/mcp_migration/b6-post-cutover-observation-runbook.md`

WP7-P 只能在 hermetic/staging 副本上开发和测试。

## 4.1 Immediate audit 工具

只读检查：

- schema=`COMPLETE_2_1`；
- active pointer 唯一；
- cutover status=active；
- config/runtime identity 一致；
- aux path/hash/integrity；
- legacy nonterminal=0；
- pending intent=0；
- started cycle=0；
- committed/dead_letter 守恒；
- pending slot audit=0；
- source watermark 仍为切换前证据；
- 无异常 sidecar；
- WP6 handoff hash 有效；
- `formal_runner_exit_evidence.json` 有效并绑定 handoff SHA；
- formal runner PID/descendants 不存在、双锁可重新获取。

## 4.2 Formal held-canary runner

要求：

- 必须消费 WP6 handoff；
- 必须验证 `watermark_release_authorized=false`；
- 仅允许固定/显式 canary codes；
- 默认使用已验证代码集合，可由授权 manifest 覆盖；
- scoped gate 强制 global watermark hold；
- cycle、trigger、intent、baseline、prices 前后 evidence；
- abort recovery；
- SQLite quick_check/WAL checkpoint；
- 超时 kill 只针对 canary 子进程，不影响 daemon/其他研究进程；
- canary 成功终态必须为 held 语义；
- 正式 source watermark 前后完全一致。

## 4.3 黄金回测基线

切换前冻结至少四类策略：

- 股票策略；
- ETF 策略；
- 指数/fallback 策略；
- 使用 QFQ 价格字段的策略。

冻结：

- signal；
- selection；
- rebalance dates；
- orders/fills/costs；
- positions/cash；
- daily NAV；
- return/drawdown/trade count；
- data query evidence。

## 4.4 监控和观察模板

准备：

- active identity drift；
- watermark stale；
- pending intent backlog；
- dead letter increase；
- cycle stuck；
- daemon/service availability；
- MCP API availability；
- aux integrity/sidecar；
- schema status drift；
- GUI manual/Run All warning；
- daily observation Run Card。

---

## 5. WP7-E 正式执行范围

## 5.1 WP7-E1：Immediate post-COMMIT audit

只有 WP6 handoff 满足全部条件才启动。

退出条件：

- P0=0；
- P1=0；
- schema/identity/aux/legacy/watermark 全部 PASS；
- formal write=0；
- 允许进入 held-canary。

## 5.2 WP7-E2：正式 held-canary

执行后必须证明：

- runtime identity=`mcp/mcp-gen1/<formal-cutover-id>`；
- baseline unchanged；
- replay new triggers=0；
- unexpected mcp triggers/intents=0；
- price summaries unchanged；
- source watermark unchanged；
- cycle finalized as held；
- canary abort recovery PASS；
- sidecars safe；
- evidence immutable。

本阶段结束后 STOP，等待 G2/用户授权，不自动释放水位。

## 5.3 WP7-E3：通过现有生产采集链路推进水位

禁止手工 UPDATE `source_watermark`，禁止直接调用 `writer.advance_watermark()`，禁止 formal runner 调用采集入口。

### 5.3.1 Formal runner 退出硬边界

WP6 formal runner 必须作为受控 supervisor 的一次性 child process 运行。child 在退出前：

1. 关闭正式 main/aux 全部连接；
2. 释放 collector/daemon 双锁；
3. 在仍持有双锁时发布 handoff，记录 child PID、`connections_closed=true`、`locks_release_pending=true`、`watermark_release_authorized=false` 和 `expected_exit_code=0`；handoff 不得自包含自身 hash；
4. handoff durable 后，child 在 `finally` 中按 collector lock -> daemon lock 的顺序释放自己持有的双锁；
5. 不保留 callable session、后台 helper、socket 或孙进程；
6. formal runner 模块不得导入或调用 `ResidentCollector.run_once()`、`execute_task()`、`qfq_run_post_ingest()`；
7. child 进程随后退出。

child 无法在自己的 handoff 中可信声明“已经退出”或“双锁已经释放”。因此 supervisor 必须：

8. wait child 结束；
9. 验证实际 PID 已不存在、exit code 精确为预期值；
10. 从 handoff 原始字节独立计算 SHA-256；
11. 按 daemon lock -> collector lock 顺序非阻塞复取双锁，完成二次 identity/descendant/sidecar 检查，再按 collector lock -> daemon lock 顺序释放；任一复取或释放验证失败即 BLOCK；
12. 通过 O_EXCL 发布独立 `formal_runner_exit_evidence.json`，包含 PID、create_time、exit code、handoff raw SHA、`locks_released_verified=true`、locks reacquire/release evidence、descendant scan；
13. WP7-E3 必须同时消费 handoff 和 exit evidence，并独立复算 handoff raw SHA；缺一或 hash 不一致即 BLOCK；
14. WP7-E3 由新的独立生产采集进程启动，不能在 formal runner/supervisor 同一执行会话内续跑。

### 5.3.2 唯一允许的首次水位释放入口

首次释放采用现有 production CLI：

```powershell
python -m quantstudio.pipeline.daemon --mode once `
  --config-dir config/profiles/mcp_only `
  --task <task-name> `
  --pull-mode incremental `
  --quality-audit full
```

固定任务名：

- `mcp_etf_daily`
- `mcp_etf_minutes`
- `mcp_stock_daily`
- `mcp_stock_minutes`

该 CLI 的真实调用链必须保持为：

```text
daemon.py --mode once
  -> CollectorRunLock
  -> ResidentCollector.from_configs(mcp_only)
  -> ResidentCollector.run_once(task_name, incremental, full)
  -> ResidentCollector.execute_task(...)
  -> _needs_manual_qfq_cycle()
  -> qfq_begin_cycle()
  -> existing fetch/align/validate/write
  -> _advance_or_defer_watermark()
  -> qfq_run_post_ingest()
  -> normal gate commit/hold
  -> full quality audit
  -> collector.close()
```

这与 WP1/PyQt 单任务修复复用同一个 `execute_task()` 和 QFQ one-task coordination cycle，不新增 production watermark API。

### 5.3.3 串行释放纪律

1. 用户明确授权解除 hold；
2. 验证 WP6 runner 已退出、双锁空闲、handoff/G2 PASS；
3. 四个任务逐个启动独立 `--mode once --task ...` 进程，禁止并行；
4. 每个任务必须满足：
   - runtime identity=`mcp/mcp-gen1/<formal-cutover-id>`；
   - candidate intent 已生成；
   - QFQ gate status=`finalized`；
   - exactly one matching intent committed；
   - watermark identity/last_batch_id 正确；
   - quality audit 满足既定门；
5. 任一任务 failed/held/无 terminal intent：立即停止后续任务，不手工修水位；
6. 四个任务全部成功后，才进入 WP7-E4；
7. 后续正常 daemon 观察必须走现有 `DaemonLifecycle.run_one_cycle()` 全队列生产链路，证明常驻模式也能形成协调周期并幂等推进；
8. immediate replay 继续使用相同 CLI/daemon 入口，不得调用 formal runner。

### 5.3.4 防 bypass 测试

必须验证：

- formal runner 的代码路径无法调用生产采集入口；
- authorization manifest 即使被篡改为 watermark release 也因 hash/scope 被拒绝；
- WP7-E3 前 formal runner PID 不存在、双锁已释放；
- 手工 SQL UPDATE source_watermark 不属于任何 runbook 命令；
- monkeypatch `writer.advance_watermark` 直调绕过时测试必须失败；
- 只有 `run_once -> execute_task -> qfq cycle -> post_ingest` 产生 committed intent 才算释放成功。
## 5.4 WP7-E4：生产功能验证

- GUI 单任务 full/incremental；
- GUI Run All；
- fallback-source tooltip；
- daemon 启停和恢复；
- 数据浏览；
- MCP API；
- source watermark；
- trigger/cycle/intent audit；
- 黄金回测逐项一致。

## 5.5 WP7-E5：观察期（按成功周期计数，不按日历日）

观察期硬出口不是“经过 1～2 个日历日”，而是同时完成：

```text
complete_post_close_cycles_success >= 2
incremental_replay_cycles_success  >= 2
```

### 5.5.1 完整盘后采集周期定义

一次 `complete_post_close_cycle_success` 必须满足：

- `trade_calendar` 将该日期标记为有效交易日；
- 到达配置的盘后调度窗口；
- production daemon/`DaemonLifecycle.run_one_cycle()` 遍历全部 eligible incremental tasks；
- traversal_completed=true；
- 四价格表 QFQ cycle 有明确终态；
- 无未解释 failed/held/pending；
- full quality audit 满足既定门；
- run state、cycle、watermark、alerts evidence 完整。

非交易日不计数。周末和节假日只做服务/监控观察，不计入两个完整周期。

半日市只有在以下全部满足时才可计为一个完整周期：

- 官方 `trade_calendar` 标记为交易日；
- 本项目配置的盘后任务全部完成；
- 数据源已发布该交易日完整数据；
- 四价格表 watermark 与该交易日实际最大业务时间一致；
- CodeBuddy/G3 审核未将其判为不完整样本。

否则半日市只记录，不计数。

### 5.5.2 增量重放周期定义

一次 `incremental_replay_cycle_success` 必须在某次成功盘后周期之后，使用同一正式 identity 再运行增量链路，并满足：

- 从已提交 watermark 继续；
- 无非预期全量重拉；
- 无历史 trigger replay；
- 无重复写入/重复 intent；
- next-cycle baseline/pending-slot audit=0；
- watermark 保持幂等或仅按新业务数据单调推进；
- GUI/daemon 状态与 evidence 完整。

同一进程内的简单函数重调不计数；必须是独立正式采集周期。

### 5.5.3 观察期限

- 建议目标仍为 1～2 个完整交易日；
- 若遇非交易日、数据源延迟、半日市不满足完整条件或任一周期失败，观察期自动延长，直到 `2 + 2` 硬计数满足；
- 不设置“时间到了自动通过”；
- 每个计数必须绑定 run_id、scheduled_date、cycle_id、watermark before/after 和 evidence hash。

每个周期固化：

- active identity；
- four-table watermark；
- cycle/trigger/intent；
- dead letter；
- aux integrity/sidecars；
- daemon/service；
- MCP API；
- GUI 状态；
- 回测抽样；
- 告警结果；
- `complete_post_close_cycles_success` / `incremental_replay_cycles_success` 累计值。
## 5.6 WP7-E6：最终闭环审计

B-6 cutover 闭环条件：

```text
formal COMPLETE_2_1
active pointer unique and correct
formal mcp-gen1 aux valid
legacy nonterminal=0
held canary PASS
normal watermark gate PASS
next-cycle idempotency PASS
GUI/daemon/MCP PASS
golden backtest identical
observation PASS
rollback retained and tested
P0=0
P1=0
P2 resolved or explicitly accepted
authoritative report updated
```

整个 MCP 项目闭环还需另行核查：

- domain/SSL；
- 客户外网；
- credential rotation；
- compliance；
- project-wide technical debt；
- customer delivery acceptance。

---

## 6. 事务、锁和并行规则

### 6.1 单写者规则

- WP6 是唯一正式写者；
- WP6 写窗口内，WP7、GUI、daemon、回测、数据浏览禁止访问正式 DB；
- WP7 只能在 WP6 释放锁、关闭连接、发布 handoff 后启动；
- 不并行 hash 正式大表和执行正式写事务。

### 6.2 WP7-P 可并行范围

WP7-P 可以与 WP6 本地开发并行：

- 代码开发；
- 测试；
- staging 演练；
- 模板；
- 黄金基线；
- 监控规则；
- 文档审查。

禁止：

- 打开正式库 read-write；
- 正式 canary；
- 正式 hash 扫描；
- 正式 GUI；
- 正式 daemon；
- 正式 watermark 操作。

### 6.3 Evidence 路径隔离

建议：

```text
output/mcp_migration/b6_<date>_wp6_formal/
output/mcp_migration/b6_<date>_wp7_canary/
output/mcp_migration/b6_<date>_wp7_observation/
```

每个 evidence 文件 O_EXCL，不覆盖、不复用旧 run-dir。

---

## 7. 测试与验收矩阵

### 7.1 Formal runner 测试

#### Authorization 完整性

- 无 authorization manifest 拒绝；
- 缺少独立 `--authorization-sha256` 拒绝；
- expected SHA 与 raw manifest bytes 不一致拒绝；
- manifest 单字节篡改拒绝；
- `watermark_release_authorized` 篡改拒绝；
- pre-main/pre-aux evidence 篡改拒绝；
- authorization scope 扩大拒绝；
- manifest 内自我声明 hash 不能替代外部 expected hash；
- manifest 位于 repo/data/output/evidence/formal DB 目录拒绝；
- manifest symlink/junction 指向禁止目录拒绝；
- nonce 重放、改文件名重放、改路径重放均拒绝；
- consumption record 路径碰撞拒绝；

#### Git 与 formal file evidence

- commit SHA 不一致拒绝；
- tracking/remote SHA 不一致拒绝；
- tracked staged/unstaged diff 拒绝；
- 仅存在 ignored/untracked 临时文件时不误拒；
- formal main/aux SHA/size/mtime 不一致拒绝；
- config SHA 不一致拒绝；
- formal file evidence 与 Git SHA/config SHA 字段混淆时 schema 校验拒绝；
- canonical path/alias/symlink/junction/hardlink 测试；

#### Windows lock/status/sidecar

- daemon lock busy 拒绝；
- collector lock busy 拒绝；
- live daemon identity 拒绝；
- access denied identity 拒绝；
- stale status + 任一锁不可获取仍拒绝；
- stale status 仅在双锁、二次扫描、evidence archive、授权 scope 全满足时可清理；
- mtime 很旧但 OS lock busy 仍拒绝；
- mtime 很新但锁可获取仅作为 evidence，不自动判 owner；
- formal runner 不自动 kill；
- WAL/journal/SHM owner 不明拒绝；
- 双锁后 sidecar/evidence TOCTOU 变化拒绝；

#### Disk/backup/report

- 固化公式单元测试；
- 运行时 size 变化后重新计算；
- 磁盘不足拒绝；
- backup SHA mismatch 拒绝；
- backup restore-open/integrity failure 拒绝；
- report/backup/handoff 路径碰撞拒绝；

#### Migration/activation fault matrix

- schema migration pre-COMMIT fault rollback；
- schema migration after-COMMIT exact exit 92 + `ALREADY_CURRENT` recovery；
- activation `after_retirement` rollback；
- activation `after_pointer_delete` rollback；
- activation `after_new_status` rollback；
- activation `after_pointer_insert` rollback；
- activation `before_commit` rollback；
- activation `after_commit_before_report` exact exit 92 + `ALREADY_ACTIVE` recovery；
- 两类 hard-crash 均严格串行，拒绝 `0xC0000005`；
- pre-COMMIT 每个 fault 的 cutover/pointer/trigger/cycle/intent/watermark 全量快照与前值一致；
- after-COMMIT committed/dead-letter 守恒、watermark unchanged；
- 与 staging WP1-WP3 fault matrix/versioned semantic vector 逐项比较 PASS；
- staging-only API 仍拒绝 formal；
- formal authorization wrapper 不能被 staging capability 调用，反向也不能互换。
### 7.2 WP7 测试

- handoff 不完整拒绝；
- handoff hash mismatch 拒绝；
- active identity mismatch 拒绝；
- aux mismatch/integrity failure 拒绝；
- canary codes 空/越界拒绝；
- scoped canary 强制 hold；
- canary timeout recovery；
- source watermark changed -> P0；
- unexpected triggers/intents -> P0；
- normal release 前手工水位操作拒绝；
- WP6 handoff 后 formal runner PID 已退出、连接关闭、双锁释放；
- formal runner 模块无 `run_once/execute_task/qfq_run_post_ingest` 调用路径；
- WP7-E3 仅允许现有 `daemon.py --mode once --task ... --pull-mode incremental --quality-audit full`；
- 四个固定 QFQ 任务串行运行，任一 held/failed 立即停止；
- `run_once -> execute_task -> qfq cycle -> post_ingest` 调用链证据；
- normal gate commit；
- candidate without committed/held terminal intent 拒绝进入下一任务；
- next-cycle idempotency；
- 正常 daemon `DaemonLifecycle.run_one_cycle()` 观察链路；
- GUI single/Run All；
- golden backtest exact comparison；
- observation report completeness；
- 非交易日不计 observation hard count；
- 半日市不满足完整条件时不计数；
- `complete_post_close_cycles_success >= 2`；
- `incremental_replay_cycles_success >= 2`；
- 任何失败后计数不增加且观察期自动延长。

### 7.3 变更等价性

正式 schema migration 及 runner 工具不得改变：

- 行情表内容；
- QFQ 价格语义；
- PIT/复权/字段契约；
- 撮合和回测引擎；
- 策略信号与回测结果。

任何差异必须重新分类为正确性/行为变更，不能作为 cutover 工具优化接受。

### 7.4 G0 P2 处置、负责人和期限

| P2 | 处置 | 实施负责人 | 审核/接受 | 期限 |
|---|---|---|---|---|
| 双 aux 并存治理 | §3.6.1 路由、监控、legacy 只读保留和退役条件写入代码测试/runbook；G3 前给出保留或退役结论 | ZCode/Codex | CodeBuddy 审核；用户接受最终保留/删除决定 | 设计/测试在 G1 前完成；退役结论在 G3 前完成 |
| 磁盘保守公式 | §3.3.3 同一函数/常量用于 runner、测试和 runbook，禁止漂移 | ZCode/Codex | CodeBuddy G1 审核 | G1 前完成 |
| 观察期硬计数 | §5.5 固化 `2` 个完整盘后周期 + `2` 个增量重放周期，非交易日不计数、半日市条件计数 | ZCode/Codex | CodeBuddy G1 审设计、G3 审实证；用户接受最终观察结论 | 规则在 G1 前完成；实证在 G3 前完成 |

P2 不允许因“非阻断”而从后续报告删除。每轮权威报告必须保留状态：`open / implemented_pending_evidence / verified / user_accepted`。

---

## 8. 文档与 Git 治理

大跨度 A 完成时必须同时更新：

- `README.md`；
- `docs/strategy_toolbox.md`；
- `docs/prompt_engineering.md`；
- `docs/qfq-production-enablement-checklist.md`；
- `docs/mcp_migration/mcp-cutover-design-v2.md`；
- formal cutover runbook；
- post-cutover observation runbook；
- 相关 schemas/examples/tests。

流程：

1. 本地实现；
2. 本地/staging 测试；
3. CodeBuddy G1 审核；
4. ZCode 汇报文件/行为/文档/测试/风险/提交范围；
5. 用户明确 post-repair GitHub 同步确认；
6. 才能 stage/commit/push；
7. 远端 SHA 验证；
8. 用户另行明确正式切换授权；
9. 才能执行 WP6。

Git 同步确认不能替代正式迁移授权；正式迁移授权也不能替代 Git 同步确认。

### 8.1 权威进度报告更新纪律

唯一权威报告：

`D:\miniQMT策略实盘\私募工作文件\QuantStudio-MCP全数据源替代任务文件\实时进度报告.md`

必须遵守：

- G0/G1/G2/G3 每轮只有在 CodeBuddy 审核 PASS 后才能更新报告；
- 审核未通过时不得提前登记“完成”或“审核通过”；
- 每次 PASS 后，在进入下一阶段前立即更新：已完成、待需完成、P0/P1/P2、测试/证据/SHA、阶段总览和变更记录；
- 代码、配置或正式运行证据完成后不得遗漏报告更新；
- 技术债只能标记解决/接受，不得静默删除；
- 最终闭环必须逐项对照报告，而不是只看 runner exit code。

---

## 9. 停止、回滚和 BLOCK 条件

任一条件触发立即停止，不自动继续：

### P0 / 必须回滚或保持阻断

- 正式 pre-evidence 与授权不一致；
- backup 不完整或不可恢复；
- schema 进入 PARTIAL/MIXED/UNKNOWN；
- price content hash/row count 改变；
- active pointer 不唯一；
- source watermark 在 held-canary 前变化；
- committed/dead-letter 历史改变；
- mcp-gen1 aux fallback 到 legacy aux；
- canary 产生非预期 trigger/intent；
- after-COMMIT 状态无法分类；
- rollback manifest 缺失；
- 发现未授权正式写连接。

### P1 / 不进入下一阶段

- 监控未准备；
- GUI/daemon/MCP 任一关键路径失败；
- next-cycle 不幂等；
- 黄金回测不一致；
- evidence 不完整；
- CodeBuddy 未给出 PASS。

### P2

必须完整记录处置、接受人和期限，不能静默删除。

---

## 10. CodeBuddy G0 复审任务（可直接发送）

请对修订后的《B-6 WP6 / WP7 正式切换与最终闭环计划》进行 G0 一次性集中复审。首轮结论为 `REVISE / P0=0 / P1=5 / P2=3`，本次请先逐项确认 5 个 P1 是否关闭，再审核整体计划。

### 首轮 P1/P2 关闭核查

请逐项返回 `CLOSED / OPEN` 和依据：

1. P1-1：§3.1 是否已建立离线用户授权、独立通道 expected SHA、raw bytes 先验 hash、独立 authorization root、nonce consumption，且 watermark scope 篡改可拒绝；
2. P1-2：§3.3.1 是否已给出 Windows PID/create_time/exe/cmdline/token、双锁、二次扫描、stale archive、禁止自动 kill、mtime 仅诊断的完整算法；
3. P1-3：§3.3.2 是否已明确 formal main/aux pre-evidence、Git commit/config SHA、tracked worktree 三个独立口径，并允许 ignored/untracked 临时文件不误拒；
4. P1-4：§3.5.1/§3.7.1/§7.1 是否已要求 formal 与 staging fault matrix 六点逐项等价，pre-COMMIT 全回滚、两类 after-COMMIT exact exit 92 可恢复；
5. P1-5：§5.3 是否已明确 WP6 child/supervisor 完全退出，WP7-E3 只允许现有 `daemon.py --mode once -> run_once -> execute_task -> qfq cycle -> post_ingest` 生产路径；
6. P2-1：§3.6.1/§7.4 双 aux 并存、fallback、监控、退役条件、负责人和期限是否完整；
7. P2-2：§3.3.3/§7.4 磁盘公式、当前基线、统一常量和期限是否完整；
8. P2-3：§5.5/§7.4 是否已改为至少 2 个完整盘后周期 + 2 个独立增量重放周期，并定义非交易日/半日市规则。

### 整体审核目标

判断该计划是否能够在减少审核轮次的同时，严格保障：

1. 现有 staging-only/production-hard-refusal guard 不被削弱；
2. formal runner 只能在一次性 authorization manifest、exact commit/pre-SHA、双锁、fresh backup、sidecar、disk 全通过后启动；
3. schema migration、aux/baseline、legacy retirement、active pointer CAS、mcp-gen1 active、watermark hold 的事务/顺序正确；
4. WP6 与 WP7 通过 immutable handoff 串行衔接；
5. formal held-canary 在 watermark release 前执行；
6. source watermark 只能由 canary PASS 后的正常 QFQ gate 推进；
7. after-COMMIT recovery 和 rollback 证据充分；
8. 观察期、GUI、daemon、MCP、黄金回测和最终闭环标准充分；
9. Git 同步确认与正式迁移授权严格分离；
10. 大跨度 A/B/C 是否足以减少往返审核且不牺牲安全性。

### 请按以下格式一次性返回

```text
结论：PASS / REVISE / BLOCK
首轮 P1 关闭：P1-1 CLOSED/OPEN；P1-2 CLOSED/OPEN；P1-3 CLOSED/OPEN；P1-4 CLOSED/OPEN；P1-5 CLOSED/OPEN
首轮 P2 处置充分：P2-1 YES/NO；P2-2 YES/NO；P2-3 YES/NO
P0：数量 + 完整清单
P1：数量 + 完整清单
P2：数量 + 完整清单

必须修改：
1. ...
2. ...

允许保持：
1. ...

建议的大跨度 A 实施任务：
- 明确文件范围
- 明确测试范围
- 明确 evidence
- 明确禁止事项
- 明确出口条件

是否允许进入大跨度 A：YES / NO
```

### 审核纪律

- 请一次性列出全部阻断项，不要拆成多轮零散意见；
- 本轮是 G0 计划复审，不授权正式库写入，不授权 Git stage/commit/push；
- 若 PASS，请直接给出可执行的“大跨度 A”任务安排；
- 若 REVISE，请给出可直接合并进计划的完整修订文本或精确条款。

---

## 11. 本计划当前出口

本计划修订后立即停止，等待 CodeBuddy G0 复审。

在 G0 PASS 前禁止：

- 开始 formal runner 实现；
- 打开正式库 read-write；
- 生成正式 authorization manifest；
- 执行正式迁移/active pointer/mcp-gen1/canary/watermark；
- stage/commit/push 本计划或后续代码。
