# 批一 T7 预验收运行记录（实施侧按包自跑 · 非独立验收）

- 时间：2026-09-17 20:34–20:5x（日志 `agent_workspace/_t7_run_20260917_2034.log`）
- 验收对象：批一未提交改动（写锁死亡自愈 + D1）
- **独立性声明（诚实边界）**：本记录由**实施会话**按 T7 交接包自跑，属**预验收**，
  **不替代独立验收会话**——§3 的结论须由独立验收会话自行复跑并签署后方可放行。
  为消除「验收的是不是同一批字节」的疑问，§1 固定了对象指纹。

---

## 1. 验收对象指纹（T7-1 要求）

- 分支/HEAD：`main` `65ec433cbd5d60e089eae47975b2a81373b716de`
  （注：本轮期间 HEAD 已由其他会话推进到 `65ec433`；本批未提交，验收对象 = 工作区改动）
- 逐文件 SHA-256：

```
53DA1FA579AC875CDBBBCE1069A12928222EADB8224F85957E3B603B01C9CE54  quantstudio/pipeline/snapshot_lock.py
AD02951EAB0CE9E411AC31F612DF0D242351B1780BE7B3E273B182373F6EC920  quantstudio/pipeline/writers.py
392D9D2DB1B72189295FC7CDD6E453776D0479F103B006975305D18184FE30F3  quantstudio/pipeline/sources/mcp_adapter.py
75B30525E4F2BD6D3F66ADB9D5A65208545AEFC0DCAD11F3A4B48C844940C31A  tests/test_snapshot_lock.py
8F303EB129D56E34A11756B03B896F7E893D1CA0B18C1E4FFF203A874A502D55  tests/test_mcp_wide_text_routing.py
CBF91A10AAE6CC4CFD7333683EA2C257A2B8375C9A66DF10FED6B8665CDE54B3  tests/conftest.py
2DEEE054DBE68A92991E4CD799F8B3CFAE1440877CC4A0C2AAEC2D0308414BF7  tests/test_qfq_reanchor_batch1.py
```

- 路径限定 `git diff` 指纹（生产代码三件）：`diff_sha256 = ee83bd80a7711ef4478a027536fb9cbed1539386f428c9a9074c6433ae96d066`
  （`3 files changed, 495 insertions(+), 35 deletions(-)`）
- **完整性复核**：跑测后重算 5 个关键文件 SHA-256 → 与上表**逐位一致**（验收期间无并行改动）。

## 2. 按包执行结果

| 步骤 | 命令 | 结果 |
|---|---|---|
| ① 硬门 AC5 | `pytest tests/test_snapshot_lock.py::test_reclaim_race_eight_processes -q` | **1 passed**（7.94s；八进程并发回收 → 恰一获锁 + 审计恰 1 条） |
| ① 硬门 AC-replay | `pytest tests/test_snapshot_lock.py::test_replay_customer_payloads -q` | **2 passed**（客户A pid=26168 / 客户B pid=19968 真实 legacy payload 均 `legacy_weak` 回收） |
| ② 全量契约 + 隔离前提 + D1 | `pytest tests/test_snapshot_lock.py tests/test_mcp_wide_text_routing.py -q` | **32 passed**（45.86s） |
| ③ 回归子集 | 6 个套件（3a 等价 / 写通道契约 / rw 退避 / qfq_reanchor_batch1 / qfq_aux_route / pipeline_guardrails） | **134 passed, 1 failed**（唯一失败 = **预存 manifest 漂移**，已另立追单，见 §4） |
| ④ 一行回退（必做） | `python agent_workspace/write_lock_rollback_probe.py` | **A PASS / B PASS → RESULT: PASS**（A：`QS_WRITE_LOCK_SELFHEAL=0` 陈锁不回收、fail-closed、审计 0 条；B：默认回收 + 审计恰 1 条） |
| ⑤ 前/后基线 | `python agent_workspace/snapshot_dir_baseline.py <pre/post>.json` | 见 §3 归因（**唯一差异 = 活动 daemon 释放其锁文件**） |
| ⑥ 哨兵证明（定向） | 在生产目录放置 `.write_lock.probe_sentinel` 后重跑 4 个锁相关套件（123 passed / 1 预存失败） | **哨兵存活 + 哈希未变 + 目录条目数 before=16 after=16 + reclaim 类新增件 = 0** |

## 3. 生产目录影响面归因（T7-2 前基线的用途）

pre 条目 16 / post 条目 15，逐条目对表：

- **仅 pre 有**：`.write_lock`（235 B，mtime `2026-09-17T20:33:11`，sha `fb881033…`）
  —— 创建时刻**早于**本轮跑测（20:34 起）；消失原因 = **活动 daemon 完成写入后自行释放**
  （本机两个常驻 daemon 均在跑，锁文件是活资源）。
- **仅 post 有**：**无**（无 `.write_lock.reclaim`、无 `write_lock_reclaim.log`、无任何新增件）。
- **同名条目变化**：**0**（size/sha/mtime 全等）。

⇒ **测试对生产快照目录零写入、零删除**；唯一差异属 daemon 自身写周期的正常抖动。
§2-⑥ 哨兵实验进一步排除「测试删除生产目录既有文件」的可能（哨兵哈希未变）。

## 4. 预存失败（不属批一，禁静默携带）

`tests/test_qfq_reanchor_batch1.py::TestSchemaDDL::test_duckdb_column_order_matches_manifest`
—— 本地库 `qfq_bootstrap_item` 比 manifest 多 `approved`/`approved_reason`/`approved_at` 三列。
取证：A/B 回退到 HEAD 重跑同样失败；只读探针实测 15 张 manifest 表**仅此 1 张漂移**；
归因 = 2026-08-15 提交 `463570a` 加了 DDL 三列但同批漏更新 `DUCKDB_COLS`。
追单：`docs/handoff/tracking-qfq_bootstrap_item-manifest-drift-20260917.md`。

## 5. 预验收结论（实施侧，待独立签署）

- **硬门 AC5 / AC-replay：PASS**；全量契约 32 passed；回归 134 passed / 1 预存失败；
  回退验证双分支 PASS；生产目录零接触（基线归因 + 哨兵双证）。
- **口径（裁定③，不得外扩）**：本批只证**「进程已终止后自愈成立」**，**不构成「残锁根治」**；
  `SIGKILL`/断电/强制重启的**即时**释放不覆盖（最坏 = 心跳过期前有界等待 ≤10 分钟），
  该覆盖属 P1 另立项。
- **未提交、未推送**；回退点 `stash@{0}: baseline-write-lock-selfheal-20260917_1522`；
  一行回退开关 `QS_WRITE_LOCK_SELFHEAL=0`。
- **未覆盖/待裁事项**：① 本机两个常驻 daemon 为旧码（无自愈），批一推送后需**重启**才生效
  （不可逆动作，待用户裁时机）；② `observation:own_conn` 路径的锁生命周期（为何观察用锁走生产
  目录且不随连接释放）已登记 pending，独立复核；③ 提交须按路径限定（工作区含其他会话改动，
  且 HEAD 已推进至 `65ec433`）。
