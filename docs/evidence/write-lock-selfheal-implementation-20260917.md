# 批一（写锁死亡自愈 + D1）· 实施侧证据（2026-09-17）

- 归属：实施会话（数据拉取会话）；设计/审批见 `docs/write-lock-selfheal-design.md`
- 性质：**实施侧证据**，供独立验收会话（T7）复核；**未提交、未推送**（待用户确认）
- 回退点：`git stash@{0}: baseline-write-lock-selfheal-20260917_1522`
  （对象 hash `72cc85d9f84f66ef1506def6337873bfaeaa1c01`，已 `stash store` 持久化）
- 工作区基线：`docs/handoff/baseline_write_lock_selfheal_20260917_1522.md`

---

## 1. 改动清单（路径限定，批一边界）

```
quantstudio/pipeline/snapshot_lock.py       | 524 ++++++++++++++++++++++++++--
quantstudio/pipeline/sources/mcp_adapter.py |   4 +
quantstudio/pipeline/writers.py             |   2 +
3 files changed, 495 insertions(+), 35 deletions(-)
```
新增：`tests/test_mcp_wide_text_routing.py`、`docs/write-lock-selfheal-design.md`、
`docs/handoff/customer-lock-rescue-20260917.md`、`docs/handoff/tracking-qfq_bootstrap_item-manifest-drift-20260917.md`、
基线文档；验收辅助探针 `agent_workspace/snapshot_dir_baseline.py`、`agent_workspace/write_lock_rollback_probe.py`
（与会话脚本同域，均只读/临时目录，不产生生产副作用）。

**批一边界核对（硬约束）**：`writers.py` 仅 +2 行（import + 1 行调用）；`mcp_adapter.py` 仅 +4 行
（D1 注释 + `self._config` 赋值）；**未触及** daemon 预检、CLI `status`/`clear`、`aligner.py`、
因子快照语义 → 批一 diff 不含批二内容（§8.2-6 通过）。

**测试基础设施改动（须审计知悉，二轮新增，仅测试面、零生产行为）**：
- `tests/conftest.py`：新增会话级 autouse fixture `_isolate_write_lock_dir`（`QS_WRITE_LOCK_DIR` 指向会话临时目录）；
- `tests/test_qfq_reanchor_batch1.py`：`_clean_write_lock` 改走 `snapshot_lock.lock_path()`（不再硬编码生产路径）。
- 动机与证据见 §5.7/§5.8（本机有常驻 daemon，测试原会碰生产锁路径）。

**提交纪律提醒**：`git status` 中 `quantstudio/backtest/strategies/*` 等改动属**其他会话**，
提交必须按路径限定（不得 `git add -A`）。

## 2. 硬门验收（AC5 / AC-replay）

命令：`python -m pytest tests/test_snapshot_lock.py tests/test_mcp_wide_text_routing.py -q`
结果：**32 passed**（含参数化：D1 路由 10 例、AC-replay 2 例；另含 2 例锁定「影子隔离」前提）

| 硬门 | 用例 | 结果 |
|---|---|---|
| AC5 八进程并发回收恰一获锁 | `test_reclaim_race_eight_processes` | **PASS**（`ACQUIRED` 恰 1 行 + 审计恰 1 条 `predicate=legacy_weak`） |
| AC-replay 客户A 真实 payload | `test_replay_customer_payloads[客户A]` | **PASS**（pid=26168，年龄 ≥390,000 s，`legacy_weak`） |
| AC-replay 客户B 真实 payload | `test_replay_customer_payloads[客户B]` | **PASS**（pid=19968，年龄 ≥21,000 s，`legacy_weak`） |

其余新增用例：AC1 死亡回收、AC2 存活不回收（红线）、AC3 跨主机不回收、AC4 PID 复用判死回收、
不可解析 payload 不回收、`QS_WRITE_LOCK_SELFHEAL=0` fail-closed、所有权丢失抛 `WriteLockLost`、
锁文件缺失原子重建、`release_all_write_locks` 幂等、payload v2 字段完整性。

## 3. 回归（AC10）

命令：`python -m pytest tests/test_snapshot_lock.py tests/test_mcp_wide_text_routing.py
tests/test_3a_equivalence.py tests/test_writer_channel_contract.py tests/test_writers_rw_backoff.py
tests/test_qfq_reanchor_batch1.py tests/test_qfq_aux_route.py tests/test_mcp_fetch_routing.py
tests/test_mcp_export_cache.py tests/test_f_series_export_fix.py tests/test_pipeline_guardrails.py -q`

结果：**195 passed, 1 failed**；唯一失败项已证**与本次改动无关（预存失败）**：

- `tests/test_qfq_reanchor_batch1.py::TestSchemaDDL::test_duckdb_column_order_matches_manifest`
- 现象：本地 DB 的 `qfq_bootstrap_item` 比代码 manifest 多 3 列（`approved`/`approved_reason`/`approved_at`）
- 取证：将本次 3 个源文件临时 `git checkout` 回 HEAD 后重跑该用例 → **同样失败**（1 failed）；
  随后按字节备份还原，SHA-256 逐文件比对一致（`True`）
- 结论：schema manifest 与本地库漂移，属既有技术债；不在批一范围，**已另立追单**
  `docs/handoff/tracking-qfq_bootstrap_item-manifest-drift-20260917.md`（归因已完成：2026-08-15 提交
  `463570a` 加了三列 DDL 但同批漏更新 `DUCKDB_COLS`；只读探针实测 15 张 manifest 表**仅此 1 张漂移**）

## 4. 实施期设计 refine（须审计知悉）

**S3 所有权校验语义收紧为「他人持锁才失败」**（原方案字面为「锁文件被外部删除/回收 → 失败」）。

- 触发：首轮回归中出现 `WriteLockLost: task=observation:own_conn；当前锁文件内容=None`。
  根因：`tests/test_qfq_reanchor_batch1.py` 的锁卫生 fixture（autouse）**直接 unlink 锁文件**，
  而同进程仍存在未释放的 depth=1 句柄 → 该进程后续写路径（`ensure_write_lock` 走同进程浅句柄，
  不再创建文件）触发字面语义 → 误判为所有权丢失。
- refine 后判定顺序：① 文件存在且 token == 自己 → 通过；② 文件存在但 token 非自己（**他人持锁**）
  → 抛 `WriteLockLost`；③ 文件缺失 → 以 `O_CREAT|O_EXCL` **原子重建**并写回自己 payload
  （成功则记 `[write-lock]` WARNING 后继续；失败→重读仍非自己则抛错）。
- 安全性论证：线性化点仍是文件创建的 `O_EXCL`，任一时刻至多一个持有者；
  「文件缺失」本身不构成双写事实，**他人持锁**才构成 → 收紧到②不放宽红线。
- 覆盖用例：`test_lock_taken_by_another_owner_raises`（他人 token → 抛错且不覆盖他人内容）、
  `test_missing_lock_file_atomically_restored`（缺失 → 恢复持有）。

## 5. 测试脚手架修正（含一次现场污染与清理，如实记录）

1. 首轮运行时子进程输出为 UTF-8 而父进程按 GBK 解码 → `subprocess` reader 线程 `UnicodeDecodeError`。
   修正：子进程统一 `PYTHONIOENCODING=utf-8` + `encoding="utf-8", errors="replace"`。
2. 修正 1 的实现一度把子进程环境固化为**模块级常量**（import 时快照 `os.environ`）→ 丢失
   `QS_WRITE_LOCK_AUDIT_LOG` 重定向，AC5 的 8 个子进程把 1 条回收审计写进了真实
   `data/snapshots/write_lock_reclaim.log`。**该文件已确认仅含该 1 条测试行并已删除**；
   随后改为调用时构造子进程环境（`_child_env()`），复跑核验真实审计文件不存在、锁目录无残留。
3. **T7-1 前提加固（二轮）**：加固前 `lock_path()` 硬编码仓根 → 用例会瞬时创建/删除**生产**
   `data/snapshots/.write_lock`（审计虽已重定向，锁文件没有）。本轮新增 `QS_WRITE_LOCK_DIR`
   （默认不设 → 与接入前逐位一致），autouse fixture 指向 `tmp_path/lockdir`
   → **验收全程零碰生产快照目录**；`test_lock_dir_override_isolates_from_production` 锁定该前提。
   刻意**不**耦合 `QUANTSTUDIO_DATA_ROOT`（混版感知差异会各自解析出不同锁文件、静默打破互斥）。
4. **一处假红与修正（如实记录）**：新增的 `test_default_lock_dir_matches_repo_convention` 首轮断言
   「生产目录无锁文件」→ 组合运行中假红：生产锁是**活资源**，本机 daemon 正常写入期间会瞬时创建该文件。
   已修正为「仅断言路径解析」；「无残留」判定移交前/后基线 diff（T7-2 采纳项）。
5. **回退验证升为必做（裁定②）**：`agent_workspace/write_lock_rollback_probe.py` →
   **A PASS**（`QS_WRITE_LOCK_SELFHEAL=0`：陈锁不回收、fail-closed、审计 0 条）
   / **B PASS**（默认：同一陈锁安全回收 + 审计恰 1 条）→ **RESULT: PASS**。
6. **前/后基线（裁定①，T7-2）**：`agent_workspace/snapshot_dir_baseline.py` 跑测前/后各一次 →
   SHA-256 **逐字节一致**（`B21322F2…`；14 个顶层条目的 name/size/mtime + 小文件哈希全等）。
7. **全套隔离（二轮收口，重要）**：发现**本机有两个常驻采集 daemon 在跑**（`--mode forever`，
   旧版代码、无死亡自愈），而回归集中 `tests/test_qfq_reanchor_batch1.py` 的锁卫生 fixture
   **硬编码生产锁路径并直接 unlink** → 测试可能删掉 daemon 正持有的锁（双写风险）。处理：
   - `tests/conftest.py` 增**会话级** autouse fixture `_isolate_write_lock_dir`：把
     `QS_WRITE_LOCK_DIR`（+审计路径）指向会话临时目录，覆盖**所有**测试文件（外部显式设置优先）；
   - `tests/test_qfq_reanchor_batch1.py` 的 `_clean_write_lock` 改走 `snapshot_lock.lock_path()`
     （尊重重定向），不再硬编码生产路径。
   复核：`pytest tests/test_snapshot_lock.py tests/test_mcp_wide_text_routing.py
   tests/test_qfq_reanchor_batch1.py tests/test_3a_equivalence.py -q` → **123 passed / 1 failed（预存追单）**；
   同轮 **跑测前/后生产快照目录基线逐字节一致**、生产 `.write_lock` 不存在 → **全套测试对生产锁零接触**。
8. **一次生产影响的事故与处置（如实记录）**：隔离加固**之前**的回归运行，经
   `test_qfq_reanchor_batch1` 的 `observation:own_conn` 路径在**生产**目录留下死持有者残锁
   （`{"v":2,"pid":40196,"task_id":"observation:own_conn",...}`；`psutil` 实测 pid 已不存在）。
   本机 daemon 为旧版代码（不会自愈），该残锁会使其写任务在心跳过期前失败 30 s。
   处置：**确认持有者已死**后按止血纪律**改名**（非删除）为
   `data/snapshots/.write_lock.stale.bak_20260917T1715`（236 B，保留为证据）；随后落实第 7 条隔离。

## 6. 批二并行取证（本次已完成 D2 第一步，**未实施任何改动**）

D2（`stock_float_share/mcp` 重复列）根因证据（MCP 只读接口）：

- `qdb.stock_daily_basic` 原始列**同时包含** `float_share` 与 `free_share`（两列均为 DOUBLE）；
- 取值语义**不同**且 `free_share` 常为 NULL（`000001.SZ`：09-09/09-10 `free_share=816056.5553`，
  09-11~09-16 为 `null`；`float_share` 恒为 `1940568.4991`）；
- 交叉验算：`float_share × close = 1940568.4991 万股 × 11.70 元 = 22704651.44 万元`
  ≈ 上游 `circ_mv=22704651.45` → **本管线 target `free_share` 语义 = 流通股本**（用于 derive circ_mv），
  与上游 `free_share`（自由流通股本）**不是同一量**；
- 机理：`aligner._map_columns` 保留未映射原始列 → `float_share→free_share` 映射后与原始
  `free_share` 同名 → `aligner.py:316-320` 抛 `Duplicate column names after mapping`。

配置级修法候选（批二，待审计）：给 mcp/questdb 的 `stock_float_share` 条目补一条
`"free_share": "_raw_free_share"`（把上游自由流通股本改名让位），**零引擎改动**、保持
target 语义与 `circ_mv` 推导口径不变；`alignment_rules.json` 现有键集中**无** drop/exclude 键，
故「改名让位」是唯一无需动引擎的配置路径。若最终判定 target `free_share` 应承载自由流通股本，
则属契约/语义变更，须独立立项（黄金等价 + 历史回灌影响面），不与本批混同。

D3（因子快照失败语义）：仍需客户侧/最小复现取证，未实施改动。

## 7. T7 独立验收交接包（命令与判据）

> **T7-1 前提（已核实并已加固，2026-09-17 二轮）**：
> ① **审计重定向：成立**——`QS_WRITE_LOCK_AUDIT_LOG` 在模块内**调用时**读取；子进程经
>    `_child_env()`（调用时构造 `dict(os.environ, ...)`）继承，故进程内与子进程的回收审计都落临时目录
>    （亲读源码可核：`tests/test_snapshot_lock.py::_child_env`）。
> ② **锁目录影子隔离：原不成立，已加固后成立**——加固前 `lock_path()` 硬编码仓根
>    （`<repo>/data/snapshots`），测试确会瞬时创建/删除**生产**锁文件；本轮新增
>    `QS_WRITE_LOCK_DIR`（默认不设 → 与接入前逐位一致），autouse fixture 已将其指向 `tmp_path/lockdir`
>    → **验收全程零碰生产 `data/snapshots/`**。
>    刻意**不**耦合 `QUANTSTUDIO_DATA_ROOT`：混版运行期新旧进程若对该变量感知不同，会各自解析出不同锁文件、
>    静默打破互斥（该耦合已被明确拒绝，理由见设计 §2/S8）。
> ③ **单测不得断言「生产目录无锁文件」**：生产锁是活资源（本机 daemon 正常写入期间会瞬时创建），
>    该类断言会假红（本批首轮即以此暴露，已改为仅断言路径解析）。「无残留」一律由前/后基线 diff 判定。

```powershell
cd D:\miniQMT策略实盘\QuantStudio; $env:PYTHONIOENCODING="utf-8"
# ⓪ 跑测前基线（T7-2 采纳项：区分「没有残留」与「本来就没有」）
python agent_workspace\snapshot_dir_baseline.py agent_workspace\_t7_baseline_pre.json
# ① 硬门（AC5 八进程并发回收恰一获锁 + AC-replay 两客户真实 payload 回放）
python -m pytest "tests/test_snapshot_lock.py::test_reclaim_race_eight_processes" -q
python -m pytest "tests/test_snapshot_lock.py::test_replay_customer_payloads" -q
# ② 全量锁契约 + 隔离前提 + D1 路由
python -m pytest tests/test_snapshot_lock.py tests/test_mcp_wide_text_routing.py -q   # 期望 32 passed
# ③ 回归子集（期望 134 passed / 1 failed=预存 manifest 漂移，见 §3 与追单）
python -m pytest tests/test_3a_equivalence.py tests/test_writer_channel_contract.py tests/test_writers_rw_backoff.py tests/test_qfq_reanchor_batch1.py tests/test_qfq_aux_route.py tests/test_pipeline_guardrails.py -q
# ④ 一行回退验证（裁定②：必做）
python agent_workspace\write_lock_rollback_probe.py        # 期望 A/B 双 PASS，RESULT: PASS
# ⑤ 跑测后基线 + 比对（零残留判定）
python agent_workspace\snapshot_dir_baseline.py agent_workspace\_t7_baseline_post.json
```

**实施侧自跑结果（2026-09-17 二轮，供 T7 对照）**：② **32 passed**；③ **134 passed / 1 failed（预存追单）**；
④ **A PASS（SELFHEAL=0 不回收、fail-closed）/ B PASS（默认回收 + 审计恰 1 条）→ RESULT: PASS**；
⑤ 前/后基线 **SHA-256 逐字节一致**（`B21322F2…`，14 个顶层条目全部未变）。

**执行窗口说明（据 T7-1 结论更新，二轮收口后）**：锁目录已**会话级**影子隔离并经基线比对验证
（全套 123 passed 前后生产快照目录逐字节一致）→ 验收**不再触碰生产快照目录**，
**不依赖非交易时段**；仍建议 21:15 后执行，理由仅剩资源占用与观察便利，非正确性要求。
**注意**：本机两个常驻 daemon 为**旧版代码**（无自愈）——批一推送后需重启 daemon 才生效，
重启属不可逆动作，**待用户确认**；验收期间**不要**重启或干扰 daemon。

**T7 审计重点（用户指令 2026-09-17）**：

1. **S3 两条边界用例**（设计 §4.6）：`test_lock_taken_by_another_owner_raises`（他人持锁 → `WriteLockLost`
   且不覆盖他人 payload）vs `test_missing_lock_file_atomically_restored`（仅文件缺失 → O_EXCL 原子重建恢复持有）；
   须复核「线性化点仍是文件创建 O_EXCL、互斥性未被放宽、红线未触」。
2. **AC5 / AC-replay 为放行硬门**：不过即退回，不带过。
3. **批一边界**：`git diff --stat` 路径限定范围内不得出现 daemon 预检 / CLI / `aligner.py` / 因子快照改动。
4. **现场零污染**：跑测前后基线 diff 必须为空（③ 的回归子集含自带锁卫生 fixture 的用例，尤其要看）。
5. **回退验证（已升为必做）**：`write_lock_rollback_probe.py` 双 PASS。
6. **结论口径（裁定③）**：只写「进程已终止后自愈成立」，**不写「残锁根治」**；`SIGKILL`/断电的
   即时释放不覆盖（最坏 = 心跳过期前有界等待 ≤10 分钟），要覆盖属另一立项（P1）。

## 8. 待办（闸门）

1. **独立验收（T7）**：由独立验收会话按 §2/§3 复跑并出具
   `docs/evidence/write-lock-selfheal-acceptance-<date>.md`；
2. **用户确认**：确认后方可提交；
3. **双仓库推送 + QuantStudio-trading 同步门**；
4. **D+0 止血**：`docs/handoff/customer-lock-rescue-20260917.md` 待转发两客户（与实施并行，不等本批验收）。
