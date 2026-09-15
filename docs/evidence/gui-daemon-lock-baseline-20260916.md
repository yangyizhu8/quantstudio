# GUI×daemon 锁冲突 · P0 真实 GUI 基线轮（A2）— 冻结版

> **排期勘误（2026-09-16，总调度修正）**：dev EOD 进度检查点 = **9/16**；本方 A6 全量验收 = **9/17–18**。此前文档中"9/18 EOD / 9/19–20 验收"等表述为勘误前旧排期。基线轮实际执行于 **9/15 深夜–9/16 凌晨**，文件名与日期标注已同步更正为 0916（内容与数字**未变**，冻结口径不受影响）。

- 日期：2026-09-16｜执行：策略研发专属会话
- 依据：施工清单 A2 + 总调度裁定①（手动拉取态改为**影子库真实执行**；**全轮影子化**）与裁定②（**本文件一经产出即冻结为验收对照**）
- 结论：**三态基线全部取得；跨进程锁冲突在真实生产代码路径上复现**（采集期 100%）

---

## 1. 影子化环境声明（生产零接触）

| 项 | 值 |
|---|---|
| 影子根 | `D:\miniQMT策略实盘\QuantStudio\agent_workspace\shadow_lockprobe\` |
| 注入方式 | `QUANTSTUDIO_DATA_ROOT` **进程启动前**注入（`quantstudio/_paths.py:49-50` 模块加载即解析一次，进程内修改无效） |
| 路径校验（实测） | `DATA_ROOT` / `db_path` / `quarantine_db_path` / `collector_run_lock_path` / `daemon_lock_path` / `daemon_status_path` **六条全部落在影子根**；无一条指向生产（校验脚本输出见 §5 证据清单） |
| 生产主库 | `D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db`（**全程零接触**；本轮无任何写入） |
| 影子配置 | `shadow_lockprobe/config/`（复制自 `config/profiles/mcp_only/`，仅 `data_config.json` 的 `path`/`quarantine.path` 改为影子绝对路径，附 `_note: SHADOW PROFILE`） |
| 生产 daemon | 未启动、未受影响；影子锁路径独立（`.collector_run.lock` 位于影子根） |

**三方影子化**：GUI（真实 `main_gui.py`，offscreen）、daemon（真实 `python -m quantstudio.pipeline.daemon --mode once`）、探测进程（RW 打开探测）——三者均注入同一影子根。

---

## 2. 三态基线数字（冻结值）

探测方法：独立进程按 **0.4s** 间隔尝试以**读写模式打开影子库**（打开即关，**不执行任何写语句**）；失败即计数并采样报错。

| 态 | 场景构造（真实生产代码路径） | 采样 | 失败率 | holder 构造对照（生产主库） |
|---|---|---|---|---|
| **A 空闲** | 真实 GUI 进程在场（offscreen，pid 23244），静置不操作 | 24 | **0.0%**（24 成功） | 0.0% |
| **B 浏览** | 真实 GUI 在场 + 真实 `DbHelper` 代码路径周期查询（`query_duckdb`/`list_tables`/`table_rowcount`，间隔 0.9s，13 轮） | 24 | **4.2%**（1 失败） | 53.8% |
| **C 采集期** | 真实 `daemon --mode once --task mcp_etf_basic`（MCP handshake OK；INCREMENTAL 2024-01-01→2026-09-15）影子库真实拉取中 | 22 | **100.0%**（0 成功） | 100.0% |
| **D 采集结束后** | 采集终止/完成后恢复 | 19 | **0.0%**（19 成功） | — |

**反向验证（源③，裁定①要求）**：真实采集窗口内（`collector_run.lock` 被占 = **True**）以真实 `DbHelper` 连续只读查询 12 次 → **7 次返回空（显式降级）**、5 次成功（采集于第 7 次前结束）；采集结束后恢复正常。→ **daemon 持 RW 期间 GUI 只读确实失败**（现行为=静默降级为空结果 + 告警日志，正是 T4 待改善点）。

---

## 3. 与 holder 构造值的差异说明

| 态 | 差异 | 归因 |
|---|---|---|
| A / C | 完全一致（0.0% / 100.0%） | 无需归因——空闲不持锁、采集长持锁，与库规模无关 |
| **B** | holder 53.8% → 真实 **4.2%** | ①**库规模差异**：holder 用生产主库（36 GB），DuckDB 连接建立需读元数据/WAL，**持锁窗口显著更长**；影子库为小库（首次 536 KB，拉取后仍远小于主库），连接极快 → 命中窗口概率大幅下降。②**驱动差异**：holder 脚本为裸 `connect→execute→close`；真实 `DbHelper` 经 `_safe_query` 包装（含异常捕获与返回路径），且三处查询串行。③探测相位随机性（24 采样）。 |

> 冻结口径：**A/C/D 为验收对照基线**；B 以影子真实值 **4.2%** 为对照（holder 53.8% 自此仅作参照，不作为验收判据——裁定②）。

---

## 4. 工程障碍与处置记录（实施留痕）

| # | 障碍 | 处置 |
|---|---|---|
| 1 | daemon once 主库强校验（`daemon.py:3341-3363`）将 `data_config.json` 的相对 path 解析为**生产路径**并拒绝（exit 2） | **禁用 `--allow-non-main-target`**（该参数仅跳过拒绝，写入目标仍是生产路径 → 会导致写生产库）；改为建**影子 profile**（path 指向影子库）后运行 → 校验通过且目标=影子 ✓ |
| 2 | writer init 被 QFQ schema 安全闸拦下（`_WriterSchemaMigrationRequired`：手工建表使 schema 判定为 `partial_or_mixed`，仅 `EMPTY_OR_NEW`/`COMPLETE_2_1` 放行） | 影子库重置为**纯空库**（0 表）→ 闸门放行 → daemon 自行建表并完成真实拉取 ✓ |
| 3 | 后台作业显示 `exit code 1` | 判定为 **pwsh 对原生命令 stderr 重定向的包装假阳性**（NativeCommandError）；daemon 自身日志明确 "once 完成（task + audit 全部通过）"、水位已推进、`etf_basic` 已写入影子库 → **非真实失败**（记录以防误判） |
| 4 | 首次重跑 "水位已追平，无需拉取" 瞬时结束、未形成采集窗 | 清空影子库 `source_watermark` 触发真实全量重拉 → 取得真实采集窗（用于反向验证）✓ |

---

## 5. 证据清单（原始文件）

| 文件 | 内容 |
|---|---|
| `agent_workspace/probe_state_A.json` | 态A 空闲（24 采样，0 失败） |
| `agent_workspace/probe_state_B.json` | 态B 浏览（24 采样，1 失败，含报错文本） |
| `agent_workspace/probe_state_C.json` | 态C 采集期（22 采样，**22 失败**，含 `File is already open in ...` 报错） |
| `agent_workspace/probe_state_C2.json` | 采集结束态复核（水位追平瞬时完成，未形成采集窗） |
| `agent_workspace/probe_state_D_after.json` | 态D 恢复（19 采样，0 失败） |
| `agent_workspace/probe_rw.py` | RW 打开探测脚本（可复用） |
| `agent_workspace/browse_sim.py` | 态B 真实 `DbHelper` 驱动脚本 |
| 影子根 `shadow_lockprobe/` | 影子库 / 影子配置 / `.collector_run.lock` / daemon 日志 |

---

## 6. 冻结声明与后续

1. **本文件产出即冻结**为验收轮（A6）对照基线（裁定②）；此后 holder 构造值仅作参照。
2. 验收判据（A6）：N=10 双轮（修复前=本轮基线 vs 修复后终轮）+ 3×2 矩阵每格四项 + 06:00 轮延迟实测 + 锁生命周期行为变更单列；**全轮影子化**照本轮配置执行。
3. 本基线已直接支撑两项结论：① 源①（短查询干扰）真实存在但幅度依存于库规模（4.2%）；② **源③与源②（采集期/写路径）为 100% 决定性冲突**，与 A/D/A′ 三项修复的靶点一致。
