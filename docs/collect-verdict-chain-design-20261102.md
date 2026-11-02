# 方案 v2 · 水位线和全部执行增量拉取修复（D1–D6）

- **图节点名称**：`水位线和全部执行增量拉取修复执行会话`
- 版本：**v2（客户取证回执已并入）** · 状态：①方案，**待 ②审 / 总调度批准；零改码**
- v2 相对 v1 的变更：
  1. 判定由"66 全是误判"修正为 **形态 C（混合）**；
  2. 新增 **D5 辅助库路径跨平台畸形（3 张核心表零取数）** —— **P0，优先级最高**；
  3. 新增 **D6 skip 文案变体漏计**（`no new data (streaming), skip`）；
  4. D2 追加硬前提：**manifest 当前无审计判定字段**，须 daemon 先写入才能做结构化判据。

---

## 0 客户证据并入（v1 §3 判据执行结果）

| 项 | 客户回执读数（20261102 批次，73 份 once 日志） |
|---|---|
| 批次对齐 | 73 份 ↔ 界面分母 73 **吻合** |
| M1 `once 完成` | **0/73** |
| M2 `no new data, skip`（精确串） | **7/73** ↔ 界面"成功 7" **完全一致** |
| M3 `quality audit failed` | 69/73 |
| M4 `written=` | **60/73** |
| M5 全局段 4 条 error | 73/73（每份日志都有） |
| 库内核对 | `ws_ipo` 3427 / `ths_hot` 347549 / `top_list` 49171 —— 与日志 `written=` **逐位相等** |
| manifest | **仅运行时定位字段（task/pid/nonce/路径），无审计判定字段** |

**判定：形态 C（混合）** ⇒ 依判据表同时执行 A 与 B 处置，D4 提前。

### 66 个"失败"的真实构成（客户逐份核对）

| 类别 | 数量 | 归属 |
|---|---|---|
| 数据已写入、被全局段审计判失败 | **60** | D1 + D2 + D3 |
| 取数前 `AuxRouteError` 崩溃（辅助库路径畸形 `data/D:\miniQMT…\data\qfq_aux_mcp_gen1.db`）——`stock_daily` / `etf_daily` / `stock_minutes` | **3** | **D5（P0）** |
| 候选源全部失败（`llm_text_events_enriched`：`所有候选源均失败: ['mcp']`） | 1 | D5-b（源可用性，独立议题） |
| 无新数据但文案为变体 `no new data (streaming), skip`（`stock_daily_valuation` / `stock_float_share`） | 2 | **D6** |

> **关键更正（我方 v1 估计有误，特此申报）**：客户机 3 张最核心表（日线/ETF 日线/分钟）本批次**根本没取到数**，这是真实数据缺口，不是显示问题。

---

## 1 根因（六处 owner）

| # | 缺陷 | owner | 优先级 |
|---|---|---|---|
| **D5** | 辅助库路径**跨平台畸形拼接**：POSIX 上 `Path("D:\\…").is_absolute()` 为 `False` ⇒ 被当相对路径与 base 拼成 `data/D:\…`；崩溃在 `qfq_begin_cycle`（早于任何 fetch）⇒ 3 表零取数、水位不推进 | `qfq_aux_router.py:70-77`（`_resolve_path`）+ 传入值来源待取件 | **P0** |
| **D1** | task 范围审计仍无条件跑全局段（`quality_audit.py:82-85` docstring 明写"全局段照跑"、`:220`），全局段 4 条 error ⇒ 每个任务审计必败 | `quality_audit.py` | P1 |
| **D2** | GUI 用人类日志短语判成败（`workers.py:264`；`once 完成` 仅"task+audit 全过"时由 `daemon.py:4329` 打印）；**manifest 无判定字段** | `workers.py` + `daemon.py` manifest 写入 | P1 |
| **D3** | `quality_audit_ok = all(任务 ok)`（`workers.py:282/286`）+ 汇总文案无失败数、标题写"完成"（`task_tab.py:1172-1205`） | `workers.py` / `task_tab.py` | P1 |
| **D6** | skip 文案存在变体，精确子串判据静默漏计 | 同 D2（同一根因第二表现） | P1（并入 D2 修） |
| **D4** | 全局段 4 条真实 error：`QfqDeadLetter 14,302`、`QfqPendingSla 50,771`、`stock_minutes 漂移 149`、`etf_minutes 漂移 66` | 数据质量债 | P1（建议单立） |

---

## 2 修复设计（按 owner 最小修复，不改审计阈值、不关审计）

### D5 跨平台路径守卫（**先做，P0**）
- `_resolve_path()` 增跨平台形态识别：POSIX 上遇 `X:\` / `X:/`、Windows 上遇 `/abs` ⇒ 抛
  `AuxRouteError("跨平台路径不可解析：<raw>（宿主=<os>）——请改用相对路径或本机绝对路径")`，
  **不再静默拼出畸形路径**；
- 增二次校验：拼接结果含 Windows 专有字符（`\`、盘符冒号）而宿主非 Windows ⇒ 拒绝；
- 上游传入值来源需取件定谳（§5）；若来源是我方模板 ⇒ 我方缺陷，改为按平台生成。

### D1 审计范围归因
- `QualityReport` 增 `table_ok` / `global_ok`；`run_once` 增 `audit_table_ok` / `audit_global_ok`（保留 `audit_ok` 兼容）；
- CLI：**表级失败 ⇒ exit 1**；**仅全局段失败 ⇒ exit 0 + 机读 token `AUDIT_GLOBAL_FAIL`**；
- 回扫全部读 `audit_ok` 的位置并逐个判定（DB-2026-0003 教训）。

### D2 结构化判据 + manifest 判定字段（含 D6）
- `daemon` once 结束时向 `once_*.manifest.json` 写入：`task_ok / audit_table_ok / audit_global_ok / written_rows / skipped_reason`；
- `workers` 优先读 manifest 字段；老 manifest 无字段 ⇒ 回退短语判据**且放宽为"含 `no new data` 且 `skip`"**（覆盖 `(streaming)` 变体）；
- 回退路径必须 WARN 留痕，禁止"静默判成功"。

### D3 汇总四态化
- payload：`counts{ok, skipped, audit_global_warn, table_failed, crashed}`；
- 文案：`✅ 成功 x ｜ ⏭ 跳过 y ｜ ⚠ 完成·全局审计告警 a ｜ ❌ 失败 z（崩溃 c）`，失败附前 3 名 + 原因摘要；
- 纪律：`ok_count < total` 时主词禁用"完成"。

### D4 分流（建议单立）
本节点只保留指针，不要求本次清零。

---

## 3 验收判据

| # | 判据 | 方法 | 相对 v1 |
|---|---|---|---|
| **V0** | POSIX 上配 `D:\…` 辅助库路径 ⇒ **明确报错**、不产生畸形路径；相对路径仍正常 | 跨平台单测（Windows/POSIX 形态双向） | 新增（D5） |
| V1 | 数据写入成功 + 仅全局段失败 ⇒ **成功·全局审计告警**（exit 0） | 场景测试 | 同 |
| V2 | 表级审计失败 ⇒ **失败**（exit 1） | 场景测试 | 同 |
| V3 | 汇总含 成功/跳过/失败/崩溃 四数；`ok<total` 主词不含"完成" | 文案断言 | 扩展（+崩溃） |
| V4 | 老 manifest 仍正确；`no new data (streaming), skip` 计为跳过 | 兼容 + 变体测试 | 扩展（D6） |
| V5 | CLI 退出码两向契约 | CLI 实跑 | 同 |
| V6 | 共享层全量回归 + SC-013 零变化 | pytest 隔离 | 同 |
| **V7** | 客户机重跑：3 张崩溃表 **watermark/max 日期推进**；界面四数与日志判据一致 | 客户复测 | 强化（D5） |

**回滚**：D1 `QS_AUDIT_SCOPE_ATTRIBUTION=legacy`；D2 `QS_ONCE_VERDICT=log`；D5 纯加固（无行为放宽，无需开关）。

---

## 4 知识沉淀与反哺（R1 / R4 · 严格执行）

### R1 开工留痕（v2 增补）
- [命中] **DB-2026-0003**（判据改道须回扫退出码语义）→ D1 回扫清单；
- [命中] **DB-2026-0010**（CLI smoke 只断言 `--help` ⇒ 零执行覆盖）→ 判据必须真跑并断言结构化输出；
- [命中] **DB-2026-0019 / DB-2026-0020**（本节点自身沉淀）→ 实施直接依据；
- [无命中] 「跨平台路径形态判据（POSIX 上的 `X:\`）」⇒ 待 D5 来源定谳后新增沉淀。

### R4 已落盘（本阶段，机检通过）
| 条目 | 主题 | 校验 |
|---|---|---|
| **DB-2026-0019** | 审计范围泄漏：task 审计混入全局段 ⇒ 60 任务误判失败 | `kb_hook.validate_record` 通过（族 db / D4） |
| **DB-2026-0020** | 成败判据建立在人类日志精确串 ⇒ 文案一变静默失准 | 通过（族 db / D4） |

已落盘命令与回执：`python scripts/kb_draft.py --kb knowledge/db --domain D4 --layer artifact_rule …` →
`编号：DB-2026-0019 / DB-2026-0020 → 已写入；校验：kb_hook.validate_record 通过`。

### R4 待落（实施/验收时）
- **DB-2026-00xx**「跨平台路径字符串在 POSIX 上不被判为绝对路径 ⇒ 静默拼出畸形路径」，含客户两文件作复现固件指针（D5）。

### 回哺（可机检，不许散文）
| 回哺面 | 落点 |
|---|---|
| 契约表 | `knowledge/contracts/audit-verdict-contract.md`：定义 `task_ok / audit_table_ok / audit_global_ok` 语义与"谁决定退出码" |
| 测试固件 | `tests/fixtures/audit_scope/`（数据成功+全局失败 / 表级失败）、`tests/fixtures/once_log_variants/`（三种 skip 文案 + 无标记）、`tests/fixtures/aux_path_crossplatform/`（POSIX↔Windows 形态） |
| 条目回哺 | 三条 DB 的 `fix_target` 已指文件函数，验收时补 commit SHA |

### 收敛判据
同族缺陷连续 3 轮复发数 R=0；复发则先归因"知识库缺失 or 回哺链断裂"，先修管线再修漏洞。

---

## 5 待取件（定谳 D5 来源，客户侧只读）

1. `config/profiles/<实际 profile>/qfq_aux_paths.json`；
2. `data/qfq_aux_route.json`（若存在）；
3. `data/logs/once_mcp_stock_daily_incremental_20261102_*.log` **头部 20 行**（`qfq_begin_cycle` 前后路径打印）。

> 客户机上该串是 `D:\miniQMT策略实盘\…`（我方开发机路径形态），极可能沿用了含我方绝对路径的配置/运行态文件；
> 须取件确认，**不得先入为主**。

---

## 6 六步流水线状态

| 步 | 状态 |
|---|---|
| ①方案 | **本文件 v2** |
| ②审计 | 待（总调度派审） |
| ③实施 | **未开始，零改码** |
| ④验收 | §3（V0–V7） |
| ⑤用户确认 | 待 |
| ⑥双推+同步门 | 待（铁律 03/10/14） |

---

## 7 ②审预研补充（2026-11-02 夜，全程只读）

### 7.1 关键发现：A6 已有部分缓解 ⇒ D1 必须整合，不能另起

`daemon.execute_task` 末尾（`daemon.py:799-814`）已实现"数据成败与审计完整性分离（D1 采纳）"：

- 新语义 `return task_ok`（数据成功即 True），"审计未完整"由 `_last_quality_audit['incomplete']` 承载；
- `QS_AUDIT_STRICT_FAIL=1` 可逐位还原旧语义 `task_ok and audit_ok`。

**缺口**：`run_once` 聚合里 `audit_ok` 仍独立存在，CLI 出口（`daemon.py:4326-4329`）执行
`if result["audit_run"] and not result["audit_ok"]: sys.exit(1)`——A6 只覆盖 **incomplete**，
未覆盖"审计真跑完且报了 error"。全局段 4 条 error 正落在后者 ⇒ 本批 60 任务 exit 1。

**D1 修正口径（v2.1，请 ②审 定谳）**
1. `audit_ok` 保留为真值（审计跑完且无 error 级问题）；
2. 新增 `audit_table_ok` / `audit_global_ok`，**归因**落细分位；
3. CLI：`task_ok=False` 或 `audit_table_ok=False` ⇒ exit 1；仅 `audit_global_ok=False` ⇒ **exit 0 + token `AUDIT_GLOBAL_FAIL`**；
4. 与 A6 开关合并为单一语义族：`QS_AUDIT_STRICT_FAIL`（旧）+ `QS_AUDIT_SCOPE_ATTRIBUTION=legacy`（新），
   两者优先级与互斥关系**实现前写死**并由测试固定。

### 7.2 D1 退出码消费面枚举（②审要点① · 本机只读核出）

| 消费方 | 位置 | 是否读退出码 | 处置 |
|---|---|---|---|
| GUI 批量 worker（once 子进程） | `workers.py:237-283` | **只看 `poll()` 结束，当前不看 `returncode`** | 改为消费结构化判定（D2） |
| GUI 单任务 worker | `workers.py:130-177` | 记录 `returncode`（仅入日志/结果） | 语义不变，仅确认 |
| 守护/launcher 脚本 | `scripts/launch_daemon.py`、`scripts/qfq_staging_rehearsal.py` | 读 daemon 启动/锁，不读 once 退出码 | 不受影响 |
| 计划任务注册件（schtasks） | 仓外 | 未读退出码 | 实现时二次核并留痕 |
| CI / 钩子 | `.githooks/pre-push`、`.github/workflows/*` | 不调用 once | 不受影响 |

> 结论：once 退出码当前**只有一个真实语义消费方**（GUI 批量 worker），而它本就要在 D2 改为结构化；
> 影响面收敛。回哺契约需固定"谁可依赖 exit code"，防未来新增消费方踩同一坑。

### 7.3 D5 守卫语义（②审要点② · 实现约定）

- **拒绝并显式报错，绝不静默回退**：跨平台形态 ⇒ `AuxRouteError`，消息含 `raw` 值、宿主平台、
  "请改用相对路径或本机绝对路径"的可执行指引；
- 不做"猜测修正"（如把 `D:\…` 硬映射到当前盘）——那会掩盖配置错误；
- 单测双向：POSIX 宿主遇 `D:\…`/`C:/…` ⇒ 抛错；Windows 宿主遇 `/abs/path` ⇒ 抛错；
  正常相对路径与本机绝对路径 ⇒ 通过。

### 7.4 D5 取件（仍缺，经铁律 12 通道待客户回）

1. `config/profiles/<profile>/qfq_aux_paths.json`
2. `data/qfq_aux_route.json`（若存在）
3. `data/logs/once_mcp_stock_daily_incremental_20261102_*.log` 头部 20 行

> 取件到达前 D5 只实现"守卫"（拒绝畸形路径），**不改来源**；来源定谳后再决定是否修我方模板。

---

## 8 客户取件回执并入 · D5 根因改判（2026-11-02 23:3x，只读）

### 8.1 取件读数（客户原样回执）

| 项 | 读数 | 推论 |
|---|---|---|
| `config/profiles/mcp_only/qfq_aux_paths.json` | **存在**，内容与我们仓内**完全一致**（全是相对路径 `../../../data/qfq_aux_mcp_gen1.db`） | **排除**"配置文件写死 Windows 路径" |
| `data/qfq_aux_mcp_gen1.db` | **存在**，53,248 B（Nov 2 14:28） | 目标库存在 ⇒ 报错"不存在"指的是**畸形拼接后的路径**，不是真缺库 |
| `data/qfq_aux_route.json` | **不存在** | **排除**该运行态文件 |
| 全 `config/` 搜 `miniQMT` | 仅 3 处（fidelity gate 提示文案 / alignment `_note` / help.json 示例），**均非辅助库路径字段** | **排除**配置来源 |
| `quantstudio/pipeline/**` 源码搜 `D:\miniQMT策略实盘\QuantStudio` | **未检索到** | **排除**源码硬编码 |
| macOS 版本 | 27.0.1（POSIX 宿主） | 跨平台判定成立 |

### 8.2 定谳：路径来自**数据库不可变记录**（不是我方模板、不是客户配置）

代码链路（本机只读核出）：

1. `qfq_cutover.py:139-151`：`SELECT … aux_db_path … FROM qfq_source_cutover WHERE cutover_id=?`
   —— 这是**不可变 cutover 路由记录**（activation 时校验一致性，见 `qfq_cutover_activation.py:198-200`）；
2. `daemon.py:435`：`orch._resolve_dynamic_identity(conn)` → `resolve_runtime_aux_path(...)` 使用该记录值；
3. `qfq_resident_orchestrator.py:216-219`：`self._explicit_aux_db = aux_db or aux_db_path(main_db)`，
   随后 `AuxDbRouter(main_db=main_db, explicit_default=self._explicit_aux_db)`；
4. `qfq_aux_router.py:70-77` `_resolve_path()`：`Path("D:\miniQMT…")` 在 POSIX 上 `is_absolute()` 返回 **False**
   ⇒ 走 `base / p` 分支；此处 `config_path=None`、`base = main_db.parent = …/data`
   ⇒ 得到 **`…/data/D:\miniQMT策略实盘\QuantStudio\data\qfq_aux_mcp_gen1.db`**
   —— 与客户报错串**逐字符吻合**（含重复的 `data/` 与 `data\`）。

**结论**：`qfq_source_cutover` 表里那行记录的 `aux_db_path` 存的是**我方 Windows 绝对路径**
（很可能是从 Windows 机器迁移/复制数据库时带入的历史记录）。
⇒ **D5 根因由"配置/模板缺陷"改判为「运行态不可变记录中的跨平台绝对路径 + 解析器静默拼接」双因素**；
v2 里"D5 来源未定谳、不排除我方模板缺陷"的保留，**现予撤销：不是我方配置模板**，但**仍是我方缺陷**——
解析器把跨平台形态静默当相对路径，且 cutover 记录缺"路径可迁移性"校验。

### 8.3 修复设计更新（D5 扩为三处）

| 子项 | 内容 | 优先级 |
|---|---|---|
| **D5-a** 解析器守卫（保留 v2 设计） | `_resolve_path()` 遇跨平台形态 ⇒ `AuxRouteError`（不静默拼接），消息含 raw/宿主/指引 | P0 |
| **D5-b** 记录读取守卫（**新增**） | 从 `qfq_source_cutover.aux_db_path` 读出的值同样过 D5-a 守卫；错误消息点名"记录 cutover_id=X 的 aux_db_path 与当前平台不符" | P0 |
| **D5-c** 可迁移性校验（**新增，建议**） | activation/校验时若记录路径的**根与 main_db 根不同设备/不同平台形态** ⇒ 拒绝激活并提示重建记录；纯新增校验，不改不可变语义 | P1（可与 D4 同批） |

**明确不做**：不自动改写 `qfq_source_cutover`（不可变记录，裸改会破坏 activation 一致性）；
如需修复客户侧记录，走**正式修复工具**（单独议题，需 ②审 + 客户窗口）。

### 8.4 验收判据更新

| # | 判据 | 方法 |
|---|---|---|
| **V0-b（新增）** | 构造 `qfq_source_cutover` 记录含 `D:\…` 形态 ⇒ run_once 报**明确错误**（点名 cutover_id 与平台不符），不产生畸形路径、不崩在 traceback | 隔离环境构造记录 + 单测 |
| V0（原） | 配置文件层跨平台形态 ⇒ 报错 | 单测 |
| 其余 V1–V7 | 不变 | — |

### 8.5 仍需客户补一项只读（最小集）

`SELECT cutover_id, price_source, source_generation, aux_db_path FROM qfq_source_cutover;`
（只读；用于**证实**该记录值确为我方 Windows 路径，并把读数作为复现固件归档）

另需 once 日志 **tail 20 行**（`head 20` 已被审计错误占满，traceback 被截断）。

### 8.6 客户侧临时规避（不改库、不冒险）

- **不建议**客户手改 `qfq_source_cutover`（不可变记录，改坏会触发 activation 校验失败）；
- 可行规避：在正式修复下发前，**暂停 stock_daily / etf_daily / stock_minutes 三个任务的增量执行**
  （它们共用该 aux 路由），其余 70 个任务不受影响；
- 我方尽快以 D5-a + D5-b 先行下发（守卫化后错误会变成一句可读的人话 + 明确处置指引）。

---

## 9 N-子8 · cutover 记录校正修复工具（2026-11-03 总调度裁定纳入本节点）

**裁定**：纳入本节点；允许"客户自助只读诊断 + 我方执行校正"两段式（**客户侧零写入**）。
**背景**：D5-a/b 只拒绝；客户 `qfq_source_cutover.aux_db_path` 仍是 Windows 路径 ⇒
`stock_daily` / `etf_daily` / `stock_minutes` 永久不可用。守卫只把错误说清楚，不修记录。

### 9.1 语义红线：不原地改写不可变记录

`qfq_source_cutover` 是**不可变路由记录**（activation 会校验 `aux_db_path` 一致性）。
故校正 = **换代**，不是改行：

1. 新增一条记录：新 `cutover_id`（如 `<旧id>_rehost_<YYYYMMDD>`），`aux_db_path` = 本机可迁移路径；
2. 旧记录置 `status='superseded'`（保留全部字段与时间戳，审计链完整）；
3. 两条记录均可被追溯（"谁在何时因何原因换代"）。

### 9.2 两段式（客户侧零写入）

| 段 | 执行方 | 动作 | 写入 |
|---|---|---|---|
| **S1 只读诊断** | 客户 | `qs-cutover-diag`（CLI）/ GUI「QFQ 路由诊断」按钮：读 `qfq_source_cutover` + `qfq_aux_paths.json` + 目标库存在性，输出 JSON 报告（含不可迁移项、建议新值与依据） | **零写入**（不建文件、不改库） |
| **S2 执行校正** | 我方（或客户授权后在窗口内） | `qs-cutover-rehost --plan <S1 报告> --apply`：备份 → CAS 校验 → 插新记录 + 旧记录 superseded → 复验 → 写审计行 | 仅此段写入，需显式 `--apply` |

### 9.3 硬性约束（与既有铁律一致）

- `--dry-run` 为默认；`--apply` 必须显式，且需 `--expect-aux-db-path "<旧值>"`（CAS：原值不符即拒）；
- 执行前**备份**：按 §10.2 三档口径（b 主 / a 兜底可配置 / c 必做，**c 不得作为唯一回滚手段**）；任一必备档缺失即拒跑；
- 全过程写审计行（`data/logs/qfq_cutover_repair_audit.log`：时间/操作者/旧值/新值/cutover_id/结果）；
- 客户侧 S1 永不写盘；S2 由我方在客户窗口内执行（或客户按说明书自行执行，仍受 CAS+备份约束）；
- 禁止实现"猜路径自动修正"（例如把 `D:\…` 映射到当前盘）——与 D5-a 守卫同理。

### 9.4 验收判据

| # | 判据 |
|---|---|
| V8-1 | S1 在含异平台记录的库上运行 ⇒ 输出"不可迁移 + 建议新值 + 依据"，且**工作区无任何新增/修改文件**（用 git status + 文件 mtime 双向核） |
| V8-2 | S2 缺备份 ⇒ 拒跑（非零退出 + 明确提示） |
| V8-3 | S2 CAS 不符（旧值已变）⇒ 拒跑，且**库零变化** |
| V8-4 | S2 正常执行 ⇒ 新记录 `aux_db_path` 为本机路径、旧记录 `status='superseded'`、审计行落盘；随后 `stock_daily` 增量可跑通（隔离环境 fixture 验证） |
| V8-5 | 回滚路径：备份还原后记录状态与执行前逐位一致 |

### 9.5 归属与排期

- 节点：**N-子8**（本节点内），与 N-子2..5 并行；
- 依赖：D5-a/b（已实施）+ D5-c（可迁移性校验，待 ②审）；
- 工期：≈1 人日；客户窗口由总调度排期；
- 知识沉淀：新增条目「不可变路由记录的换代校正协议（勿原地改）」，回哺落点 = 本条 §9.1 + V8 五条固件。

---

## 10 S2 执行归属裁定 · 备份口径 · 排期分组（2026-11-03 总调度）

### 10.1 裁定

| 项 | 裁定 |
|---|---|
| **S2 执行归属** | **选项 C**：我方执行优先 + 客户自助能力保留（两段式不变，客户侧零写入仅限 S1） |
| **备份口径** | 三档定稿见 §10.2：**b（`VACUUM INTO`）为主 + a（整体复制）余量允许时兜底可配置 + c（记录快照/`COPY SELECT` 双份）必做**；c **不得作为唯一回滚手段** |
| **排期分组** | 组①（不依赖客户，可先推进）：**N-子8（候 ②审）+ D5-c（候 ②审）**；组②（候客户）：S2 窗口/取件；组③（随包）：S1 诊断件 + 操作说明 |
| **更正（2026-11-03）** | 前版误植的 **L2-b 系 ⑪ 切隧道件术语**，已由总调度撤回；**非本线任务**，本节点不再引用 |

### 10.2 备份口径（2026-11-03 ②审倾向定稿 · 三档 + 前置余量检查）

**三档，职责不同，不可互相替代：**

| 档 | 手段 | 角色 | 是否可省 |
|---|---|---|---|
| **b（主）** | `VACUUM INTO '<备份目录>/quantstudio.vacuum.db'` 快照 | **主备份**：与 WAL 合并、体积略小、可独立打开校验（`PRAGMA integrity_check` 等价物 + 关键表行数比对） | 否（磁盘不足时才降级，见下） |
| **a（兜底，可配置）** | 整体复制 `quantstudio.db`（+`-wal`） | 磁盘余量允许时兜底；`--backup-mode=auto\|vacuum\|copy` 可强制 | 可（auto 模式下按余量决定是否追加） |
| **c（必做）** | 记录快照 JSON + `COPY (SELECT * FROM qfq_source_cutover) TO ... (FORMAT JSON)` **双份** | 记录级快速回滚；**但记录级还原能力弱于完整库** ⇒ **不得作为唯一回滚手段** | **否** |

**前置余量检查（S2 第一步，不满足即拒跑）**

```
db_size   = quantstudio.db 大小
wal_size  = quantstudio.db.wal 大小（存在则计）
need_b    = int(db_size * 1.02) + 512 MiB        # VACUUM INTO 产物 + 余量
need_a    = db_size + wal_size + 512 MiB         # 整体复制所需
free      = disk_usage(data_root).free

if free < need_b:                       → 拒跑（退出非零），打印"还差 X GiB"
elif free >= need_b and free < need_b*2 → 仅做 b + c（不追加 a）
else (free >= need_b*2)                 → b + c +（auto 且 --backup-mode!=vacuum 时）a
```

- 磁盘不足时的提示复用交付侧 `probe.space_report()` 的口径（还差多少 + 可清理位置，**只读扫描**）；

**两小裁（2026-11-03 总调度照准，随明晨 ②审 正式落档）**

| # | 裁定 | 落档口径 |
|---|---|---|
| ① | **a 档 auto 阈值：`free ≥ 2×need_b`** | 照准；低于该值只做 b + c，不追加 a（避免与 b 争空间导致半截备份） |
| ② | **不提供 `--force-insufficient-space`** | 照准且**永不实现**：空间不足强跑 = 半截备份风险；"无 force 通道"与"缺备份即拒跑"同语义（**fail-closed 一致**） |

- 所有备份路径、sha256、大小、实际方式、是否含 WAL **写入审计行**；
- 客户机实况（2026-11-02 取证）：`quantstudio.db` **62,766,198,784 B（62.7 GB）**、`data/` 已占约 **357 GB**
  ⇒ 若 `free < need_b` 则 S2 直接拒跑并给出清理建议，**不得先动库再补备份**。

### 10.3 组①中 N-子8 的开工前置（不可跳）

②审 认 §9（含本节备份口径）→ 方可实现；实现顺序：
`S1 只读诊断（qs-cutover-diag + GUI 面板）` → `S2 校正（qs-cutover-rehost，默认 dry-run）` → V8-1..V8-5 固件。

### 10.4 待明示

- **L2-b** 未在本节点任务清单中出现，语义与归属不明 ⇒ 已在回执中请调度明示，**未擅自行动**。
