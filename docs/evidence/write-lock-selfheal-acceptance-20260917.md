# 批一（写锁死亡自愈 + D1）· T7 验收报告

- 验收日期：2026-09-17（执行窗口 21:27 起，daemon 暂停窗内，未干扰两常驻进程）
- 验收对象：批一**未提交工作区改动**（HEAD = `65ec433cbd5d60e089eae47975b2a81373b716de`，本批不在 HEAD 内）
- 交接包：`docs/evidence/write-lock-selfheal-implementation-20260917.md` §7
- 设计：`docs/write-lock-selfheal-design.md`（含 §4.6 两条边界、§6 隔离机制、§7 口径）
- 运行日志：`agent_workspace/_t7_accept_20260917_2127.log`
- **结论：GREEN（通过）**——两道硬门全过、全量契约 32 passed、回归 134 passed / 1 预存、
  回退双分支 PASS、生产快照目录零差异；**放行建议：提交 → 用户确认 → 双推**

**独立性与签署说明（诚实边界）**：本次由**实施会话**按交接包执行并签署，属「同会话实施 + 验收」，
独立性受限。为便于**完全独立复核**，§1 冻结了对象指纹（7 文件逐 SHA + 路径限定 diff 哈希），
独立验收方只需复跑 §2–§7 命令并与 §1 对表即可完成独立签署，无需重新协商范围。

---

## 1. 验收对象指纹（T7-0 现场对表）

```
HEAD   = 65ec433cbd5d60e089eae47975b2a81373b716de   branch = main
artifacts_sha256 = 4f24730211a8ce6db20db9983cecdda06119a1e19e9b991cd176fc975f44c303
                   （HEAD 无关规范指纹 = SHA256(逐行 "<相对路径>\0<文件SHA>\n" 拼接，7 文件）
                    复算命令：见 §2 附录脚本；本指纹不依赖 HEAD、不依赖 diff 工具链）
diff_sha256 = ee83bd80a7711ef4478a027536fb9cbed1539386f428c9a9074c6433ae96d066
              （-- quantstudio/pipeline/{snapshot_lock,writers}.py 与 sources/mcp_adapter.py；3 files, +495/-35）
```

**口径说明（总调度 2026-09-17 复核项，已修正）**：`diff_sha256` 是**口径依赖量**——它取决于
① `git diff` 的 pathspec 参数、② **HEAD 内容**（diff 是 HEAD↔工作区）、③ 生成文本时的换行/尾随空白归一化；
因此**跨 HEAD 或跨工具链不可比**，**不是盘面真值**，仅作「同一 HEAD、同口径下」的复核锚
（本值绑定 `HEAD=65ec433`）。**验收对象的盘面真值 = 上面 7 个文件 SHA-256**，
另有 HEAD 无关的 `artifacts_sha256` 供任意时点、任意工具链复算对表。

```
53DA1FA579AC875CDBBBCE1069A12928222EADB8224F85957E3B603B01C9CE54  quantstudio/pipeline/snapshot_lock.py
AD02951EAB0CE9E411AC31F612DF0D242351B1780BE7B3E273B182373F6EC920  quantstudio/pipeline/writers.py
392D9D2DB1B72189295FC7CDD6E453776D0479F103B006975305D18184FE30F3  quantstudio/pipeline/sources/mcp_adapter.py
75B30525E4F2BD6D3F66ADB9D5A65208545AEFC0DCAD11F3A4B48C844940C31A  tests/test_snapshot_lock.py
8F303EB129D56E34A11756B03B896F7E893D1CA0B18C1E4FFF203A874A502D55  tests/test_mcp_wide_text_routing.py
CBF91A10AAE6CC4CFD7333683EA2C257A2B8375C9A66DF10FED6B8665CDE54B3  tests/conftest.py
2DEEE054DBE68A92991E4CD799F8B3CFAE1440877CC4A0C2AAEC2D0308414BF7  tests/test_qfq_reanchor_batch1.py
```

- 与 §预验收运行记录（`write-lock-selfheal-t7run-20260917_2034.md`）冻结的指纹**逐位一致**；
- **跑测后重算**：7 文件 SHA-256 与 `diff_sha256` **均未变化** → 验收期间无并行改动、验收对象稳定。

## 2. T7-1 影子隔离复核（源码亲读 + 运行期事实）

**（a）锁目录重定向**（`quantstudio/pipeline/snapshot_lock.py:69-82`，原文）：

```python
def _lock_dir() -> Path:
    """锁目录解析（默认 = 仓库 `data/snapshots`，与接入前逐位一致）。

    `QS_WRITE_LOCK_DIR`：测试/运维显式重定向（验收须「零碰生产快照目录」时使用）。

    **刻意不耦合 `QUANTSTUDIO_DATA_ROOT`**：影子根场景下若新旧版本进程对该变量感知不一致，
    会各自解析出不同的锁文件 → 互斥被静默打破（混版运行期风险）。锁目录仅在**显式设置本
    专用变量**时改变，从而保证生产解析路径对所有版本恒定、互斥不被版本差击穿。
    """
    override = os.environ.get("QS_WRITE_LOCK_DIR")
    if override:
        return Path(override)
    root = Path(__file__).resolve().parent.parent.parent
    return root / "data" / "snapshots"
```

**（b）子进程环境在调用时构造**（`tests/test_snapshot_lock.py:98-105`，原文）：

```python
def _child_env():
    """子进程环境：UTF-8 IO + 继承当前（含 monkeypatch 注入的）环境变量。

    注意：必须在调用时构造——模块级常量会冻结 import 时的 os.environ，
    导致 monkeypatch.setenv（如 QS_WRITE_LOCK_AUDIT_LOG 重定向）传不到子进程，
    子进程便会把回收审计写进真实 data/snapshots/（本用例曾以此污染一次现场）。
    """
    return dict(os.environ, PYTHONIOENCODING="utf-8")
```

**（c）会话级兜底**（`tests/conftest.py::_isolate_write_lock_dir`）：把 `QS_WRITE_LOCK_DIR` 指向
会话临时目录（外部显式设置优先），覆盖**所有**测试文件；`tests/test_qfq_reanchor_batch1.py`
的锁卫生 fixture 已改走 `snapshot_lock.lock_path()`（不再硬编码生产路径）。

**运行期事实核验**：
```
with override    -> C:\Temp\shadow_probe\.write_lock
without override -> D:\miniQMT策略实盘\QuantStudio\data\snapshots\.write_lock
```
⇒ 重定向生效，且**默认路径与接入前逐位一致**。

## 3. 硬门（放行开关）

| 硬门 | 命令 | 结果 |
|---|---|---|
| **AC5** 八进程并发回收同一陈锁 → 恰一获锁 + 审计恰 1 条 | `pytest tests/test_snapshot_lock.py::test_reclaim_race_eight_processes -q` | **1 passed**（7.27 s） |
| **AC-replay** 两客户真实 legacy payload 回放（pid=26168 / pid=19968） | `pytest tests/test_snapshot_lock.py::test_replay_customer_payloads -q` | **2 passed** |

## 4. 全量契约 + 回归

| 项 | 命令 | 结果 |
|---|---|---|
| 锁契约 + 隔离前提 + D1 路由 | `pytest tests/test_snapshot_lock.py tests/test_mcp_wide_text_routing.py -q` | **32 passed**（45.18 s） |
| 回归子集（3a 等价 / 写通道契约 / rw 退避 / qfq_reanchor_batch1 / qfq_aux_route / pipeline_guardrails） | `pytest <6 suites> -q` | **134 passed, 1 failed** |

唯一失败 = **预存追单**：`tests/test_qfq_reanchor_batch1.py::TestSchemaDDL::test_duckdb_column_order_matches_manifest`
（本地库 `qfq_bootstrap_item` 多 `approved`/`approved_reason`/`approved_at` 三列；A/B 回退到 HEAD 同样失败；
归因 = `463570a` 加 DDL 三列漏更新 `DUCKDB_COLS`；追单 `tracking-qfq_bootstrap_item-manifest-drift-20260917.md`）。
**不属批一，按追单独立处置，不阻断本次放行。**

## 5. 一行回退验证（必做项）

```
A. SELFHEAL=0 陈锁不回收 fail-closed : PASS     （陈锁+持有者已死 → 不回收、锁文件保留、审计 0 条）
B. 默认 陈锁安全回收 + 审计恰 1 条   : PASS     （同一陈锁 → 回收成功 + 审计恰 1 条）
RESULT: PASS
```
⇒ `QS_WRITE_LOCK_SELFHEAL=0` 一行回退**真实有效**（止损能力可验证）。

## 6. 生产目录影响面（前/后基线，逐条目）

- 前 15 条 / 后 15 条；**仅 pre 有：无；仅 post 有：无；同名条目 size/sha/mtime 变化：0**。
- 本轮 daemon 处暂停窗，无活动锁抖动 → **单次干净对照**：验收对生产快照目录**零写入、零删除**。
- （预验收轮另有哨兵实验：在生产目录放置既有文件后重跑 4 个锁套件 → 哨兵哈希未变、零 reclaim 残留。）

## 7. 审计重点逐条应答

| # | 重点（用户裁定） | 应答 |
|---|---|---|
| 1 | **S3 两条边界**：他人持锁 vs 仅文件缺失 | `test_lock_taken_by_another_owner_raises`（他人 token → `WriteLockLost` 且**不覆盖**他人 payload）+ `test_missing_lock_file_atomically_restored`（缺失 → `O_EXCL` 原子重建恢复持有）**均通过**；线性化点仍 = 文件创建 `O_EXCL`，互斥未放宽；红线（存活不回收）由 `test_stale_heartbeat_live_holder_not_reclaimed` 锁定 |
| 2 | AC5 / AC-replay 硬门 | 均 PASS（§3） |
| 3 | 批一边界（diff 不得含批二内容） | 生产代码仅 3 文件；`writers.py` +2 行、`mcp_adapter.py` +4 行；无 daemon 预检 / CLI / `aligner.py` / 因子快照改动 |
| 4 | 现场零污染 | §6：基线零差异；主库与生产目录零接触（隔离由 §2 双证） |
| 5 | 回退验证 | §5 双分支 PASS |
| 6 | 口径收窄（禁写「残锁根治」） | §8 结论三句严格限定 |

## 8. 结论与放行建议

**结论（严格限定，不得外扩）**：

1. 本批证明 **「进程已终止后自愈成立」**：持有者进程已不存在时，陈锁由 acquire 侧安全回收，写路径恢复
   （两客户现场形态均以真实 payload 回放验证）；
2. **不覆盖「终止瞬间的立即释放」**：`SIGKILL`/断电/强制重启下锁文件必然残留，由第 1 条在心跳过期
   （>600 s）后回收，最坏表现为**有界等待 ≤10 分钟**，**不是**永不恢复；
3. 本批**不构成「残锁根治」**；要覆盖第 2 条属 P1 另立项（`filelock` 原语迁移，触发条件见设计 §7）。

**放行建议**：GREEN → 进入「提交（路径限定）→ 用户确认 → 双仓库推送 + QuantStudio-trading 同步门」。

## 9. 残留风险与未覆盖（如实声明）

| # | 项 | 状态 |
|---|---|---|
| 1 | 本机两个常驻 daemon 为**旧码**（无自愈），批一推送后需**重启**才生效 | 不可逆动作，**待用户裁**（随批一确认一并呈） |
| 2 | `observation:own_conn` 路径锁生命周期（观察用锁走生产目录、不随连接释放） | 已登记 **pending**，独立复核 |
| 3 | `SIGKILL`/断电即时释放 | 设计为不覆盖（§8-2），P1 立项 |
| 4 | 预存 manifest 漂移 | 追单处理，不属批一 |
| 5 | 验收独立性 | 同会话实施+验收；**总调度已实测独采替代独立会话签署**：验收报告在位 + `test_snapshot_lock.py` 22 passed（45.18 s 独立复跑）；另有 §1 三重保障（指纹冻结 + 命令给全 + 硬门客观可复现） |
| 6 | **客户指南 Q1 未修订（本批 S7 未完成，如实报告）** | `docs/customer-user-guide.md` 仍写「系统会在次日自动补齐（T+1 自愈）」——该承诺在两客户事故中不成立。该文件为**其他会话的未跟踪在制品**，本会话未擅自改动；建议补丁（待派单或授权后应用）见 §9.1 |

### 9.1 客户指南 Q1 建议补丁（待授权/派单）

```diff
- **A**：先贴「提示词 1」让智能体检查。常见原因：网络中断、MCP 服务临时不可用、数据源晚发布。系统会在次日自动补齐（T+1 自愈）。
+ **A**：先贴「提示词 1」让智能体检查。常见原因：网络中断、MCP 服务临时不可用、数据源晚发布；
+ 少数情况是**采集进程非正常结束留下了锁文件**（表现为日志反复出现「写锁被持有」，此时不会自动补齐）。
+ 该类情况按《客户使用说明》「锁残留处置」四步处理：先留证据 → 确认进程号已不存在 → 把锁文件**改名**（不删除）
+ → 重启采集程序。新版本已具备「残留锁自愈」，升级后该情形无需人工介入。
```

## 10. 签署

- 执行：实施会话（数据拉取会话）｜日期：2026-09-17｜证据：本报告 + `_t7_accept_20260917_2127.log` + §1 指纹
- 状态：**验收 GREEN，未提交、未推送**；回退点 `stash@{0}: baseline-write-lock-selfheal-20260917_1522`；
  一行回退 `QS_WRITE_LOCK_SELFHEAL=0`
