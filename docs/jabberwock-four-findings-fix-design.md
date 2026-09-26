# jabberwock 四项新发现修复设计（一份方案·四项耦合）— 六步第 1 步

- 状态：**方案（呈审）**｜日期：2026-09-25｜归属：客户运维会话 1
- 客户：**jabberwock**（`D:\hasym\PycharmProjects\QuantStudio`）
- 合并理由（采纳派单）：四项互相耦合——**P1-2b 是 P1-2a 的上游**（空/退化 range → epoch 窗口 → 空 fresh → 引擎崩），
  **P1-4** 使数据源不可用（放大空窗），**P2-1** 则是对「已知空窗」的重复重取浪费。拆分提审会增加往返，故合并一份。
- 铁律：本方案遵循「修复前置纯增益审计（三型判定）」；每项给出**改动范围/影响面/验收/回退**。

## 0. 耦合关系（本案主线）

```
[P1-4 TLS 环境耦合] ──► etf_basic 等源不可用 ─┐
                                              ├─► 窗口数据为空/残缺
[_security_range 空 / 日期不可解析] ───────────┘        │
                                                       ▼
                              [P1-2b] ckey 窗口退化（1970-01-01|1970-01-02）
                                                       │
                                                       ▼
                              [P1-2a] 空 fresh df 直送引擎 → 引擎守卫抛错 → **崩整轮（重锚环停摆）**
                                                       │
                                                       ▼
                              [P2-1] 每轮重复重取同一空窗（空窗每 2 周规律出现）
```
**共同根因**：**空/退化输入未在链路上被前置拦截与留痕**——空落到引擎、退化落到缓存键、不可用落到 DEBUG 级日志。

---

## P1-2a 引擎层空值崩溃 → 前置跳过（不传引擎）

### 取证（已定位）
- 落点：`qfq_resident_orchestrator._reanchor_security`（`:458`），流程 = `_security_range`（`:482`）→
  `FreshCapture.capture(...)`（`:490-494`，**返回 `record, fresh_daily, fresh_minute`**）→ 引擎 apply
  `apply_reanchor_for_security(...)`（`:511`）；
- 调用点两处：`:1244`、`:1406`（均在同一轮内**逐证券**循环——**单证券空值即崩整轮**，与「重锚环停摆」现象一致）。

### 改动
| # | 内容 |
|---|---|
| ① | **前置检查**：`capture` 返回后、调用引擎前，判 `fresh_daily` 与 `fresh_minute` **均为空**（或该资产类型对应窗口为空）→ **不调用引擎**，返回 `ReanchorOutcome(status="skipped", reason="empty_fresh_capture")` |
| ② | **告警 + 计数**：`logger.warning`（含 asset_type/code/窗口区间/trigger 数）+ 计入 cycle summary（新增 `skipped_empty` 计数，或复用既有 skipped 语义） |
| ③ | **trigger 处置**：本轮**不改 trigger 状态**（不进 dead letter、不计 attempt 失败），保持 `pending` 由后续轮次自然重试（避免把"源暂时无数据"判成永久失败） |

### 类型判定
**纯恢复型**（正常路径零变化：非空输入逐行等价）＋ **新增检测型**（新增「空 fresh」判定与可见化）。

### 验收
- 单测：构造 `capture` 返回空 daily/minute → 断言**引擎未被调用**、outcome.status=skipped、warning 含 code/区间、trigger 仍 pending；
- 单测：非空路径 → 与改前逐项一致（引擎入参、event 写入、返回 outcome 不变）；
- 集成：**空窗证券存在时整轮不再崩**（cycle 正常 finalized）。

---

## P1-2b ckey 窗口退化 bug（epoch 退化）→ 定位 + 最小复现 + 修复

### 取证（已定位入口，**根因待最小复现坐实**）
- 落点：`mcp_adapter._export_batches`（`:801`）——`s = _parse_flexible_date(start[:10])`、`e = _parse_flexible_date(end[:10])`（`:822-823`）；
  缓存键 `_cache_key(table, bs, be)`（`:1099`）；
- 客户现象：ckey = `etf_minutes|1970-01-01|1970-01-02` ⇒ **窗口已退化为 epoch 附近的 1 天窗**；
- **待坐实的最小复现（实施前必做，禁止未证实归因）**：
  1. 复现条件假设 A：`_security_range` 为空/缺值 → 上游传空串/None/0 → **`_parse_flexible_date` 回落 epoch**；
  2. 假设 B：日期解析失败静默回落（错误被吞）→ 与 A 同症；
  3. 假设 C：`start/end` 为 ms epoch 数字被 `[:10]` 截断误解析；
  - 动作：对 `_parse_flexible_date` 做**边界矩阵**（""/None/"0"/0/"1970-01-01"/合法日期/10 位数字/13 位 ms）→ 记录每格返回；再回溯 `_export_batches` 的两个调用方传入值来源（`_fetch_export_cached` / 直连路径）。

### 改动（待复现结论定形；候选）
| # | 内容 |
|---|---|
| ① | **解析失败/空值显式化**：`_parse_flexible_date` 对不可解析输入**返回 None 或抛专用异常**（不再静默回落 epoch）；调用方按上一步语义处理（空 range → 走 P1-2a 的 skip，而不是造出 epoch 窗） |
| ② | **窗口健全性守卫**：`_export_batches` 出口断言 `s < e` 且 `s >= 合理下界`（如数据源首日）；违反 → 抛/降级为 skip + WARNING（**不产生 1970 窗缓存键**） |
| ③ | **缓存键防腐**：`_cache_key` 对非法窗口（epoch/逆序）拒绝生成，或加显式前缀标记，避免污染 manifest |

### 类型判定
**纯恢复型**（合法输入路径零变化）＋ **新增检测型**（非法窗口由「静默造键」变为「显式拒绝/降级」）。

### 验收
- **最小复现用例**（先红后绿）：以复现矩阵中的触发输入断言「不再产生 1970 缓存键」；
- 合法日期路径 → ckey/批次边界逐位不变（对既有 manifest 兼容）；
- 回归：`test_mcp_export_cache` / `test_mcp_fetch_routing` / `test_qfq_*` 全绿。

---

## P1-4 etf_basic TLS：`REQUESTS_CA_BUNDLE` 覆盖 session.verify

> ### ⚠️ 实测补充（2026-09-25，真连云端 smoke，**请审核裁定归因修订**）
>
> 实施后做真连实测（`agent_workspace/e2e_smoke_cloud.py`）发现：**端点 = `https://124.223.159.234/mcp`
> （IP 直连），其证书与 IP 不匹配** →
> `SSLCertVerificationError(1, "IP address mismatch, certificate is not valid for '124.223.159.234'")`。
> 即：**该端点在任何 CA 配置下都无法通过标准 TLS 校验**（设计如此，故 config 为
> `tls_verify: false` = 开发 IP 模式）。
>
> **由此产生的归因修订建议**：
> 1. 客户 etf_basic 的 TLS 失败，**更可能的直接原因是 `tls_verify=true`**（对 IP 端点必然失败），
>    而非「env `REQUESTS_CA_BUNDLE` 覆盖」——后者在 `verify=False` 时**根本不参与**；
> 2. 故**可行动修复**是：配置 `tls_verify: false`，或使用本项新增的 **`MCP_TLS_VERIFY=0`** 逃生通道
>    （+ INFO 日志一眼可见生效来源与策略）；
> 3. **本项改动无回归**：`verify=False` 路径逐行不变；`verify=True` 路径**前后同样失败**于 IP 不匹配
>    （certifi/系统 CA 皆然），故不存在「修一送一」；新增能力 = 逃生通道 + 可见化。
>
> **待审核裁定**：① 是否按上文修订 P1-4 归因（env CA → `tls_verify=true` 对 IP 端点）；
> ② 是否需要在客户通知中增加「检查/调整 `tls_verify` 配置」一条（大概率即其症结）。

### 取证
- `client.py:207 tls_verify: bool = False`（构造参数）→ `:222 self.tls_verify` → **`:231 self._session.verify = self.tls_verify`**（`:542` 重连后重设）；
- `requests` 语义：`session.verify=True` 时，**仍会读取环境变量 `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE`** 作为 CA 包路径 ⇒
  客户机若设了错误/失效的 `REQUESTS_CA_BUNDLE`，即使 `tls_verify=True` 也会握手失败；
- 本机核查：`certifi` 可用（`…Python311\Lib\site-packages\certifi\cacert.pem`），当前 `REQUESTS_CA_BUNDLE/CURL_CA_BUNDLE` 均为空（**故本机不体现，需按客户环境复现**）；
- 现状日志：不可用原因落在 **DEBUG** 级 → 客户侧不可见（`get_etf_codes` 仅 WARNING「etf_basic 无数据」，`mcp_adapter.py:2031`）。

### 改动
| # | 内容 |
|---|---|
| ① | **显式隔离 CA**：会话初始化与重连时显式设置 `session.verify = certifi.where()`（或配置的显式 CA 路径），**使环境变量不再影响**（保留代理 env：不动 `trust_env`，避免破坏 `HTTP(S)_PROXY` 用户） |
| ② | **逃生通道**：`MCP_TLS_VERIFY=1` 强制开启校验（覆盖 `tls_verify=False`）；`MCP_TLS_VERIFY=0` 显式关闭（配合白名单 IP 的开发模式） |
| ③ | **不可用原因升级可见**：TLS/连接类不可用由 **DEBUG → INFO**（含**已生效的 CA 来源**与是否被 env 影响），便于客户一眼归因 |
| ④ | 文档：`.env.example`/README 增补 `REQUESTS_CA_BUNDLE` 注意事项（若客户另有内网代理 CA 需求，走显式配置而非 env 隐式覆盖） |

### 类型判定
**纯恢复型**（校验开启时行为等价安全语义；关闭时逐行不变）＋ **新增检测型**（新增可见化与显式开关）。

### 验收
- 单测：设 `REQUESTS_CA_BUNDLE=/nonexistent` → 断言**会话不采用该值**（仍可用 certifi）；`MCP_TLS_VERIFY=1/0` 覆盖生效；
- 单测：日志级别断言（不可用原因 ≥ INFO，含 CA 来源）；
- 客户侧复现验证（端到端）：`etf_basic` 拉取成功或给出**明确可归因**的信息级原因。

---

## P2-1 空窗口墓碑（轻量负缓存，TTL 内跳过重取）

> ### ✅ 实施定稿（2026-09-25，依 Trae 兼容判定回执 + 审核实施令）
>
> **落点**：`quantstudio/pipeline/sources/mcp_adapter.py`
> （`_resolve_shard_paths` 记录服务端事实；`_fetch_export_cached` 命中/写入；新增 3 个助手）
>
> **写墓碑 4 条谓词（合取）**：
> ① 无异常（仅在顺手路径调用，异常已在上游抛出）；
> ② **服务端零分片**：`shards=[] ∧ total_rows=0 ∧ shard_count=0`
>    —— 以 `_last_export_meta["shards"]==0`（按其 key 精确匹配本次批次）为准（零分片必零行），
>    并要求零落盘（`miss_paths` 为空）；
> ③ ckey 已过 **P1-2b 防腐守卫**（调用方 `_cache_key` 未抛即已通过）；
> ④ 该 ckey **无既有条目**（严格互斥：条目存在——无论真实或失效——**一律不写墓碑**）。
>
> **2 硬不变量**：
> ① **整体替换写入**（`table_cache[ckey] = {...}`，**禁 `.update()`/merge**）⇒ 防 `empty:true` 与 `shards` 共存；
> ② **命中判定先于 size 校验**，且命中即 `continue`、**不置 `manifest_dirty`**（不刷新 `empty_ts`）
>    ⇒ 防 TTL 无限续期。
>
> **口径（采信）**：字段名 `empty` / `empty_ts` / `empty_ttl_s`（同 ckey 命名空间，不新增顶层键）；
> TTL 默认 **7 天**（604800s），env **`QS_EXPORT_EMPTY_TTL_S`** 可配；
> **`export_artifacts` → `export_dataset` 修正**（本项实际调用链为 `client.export_dataset`，见 `_resolve_shard_paths`）；
> 失败路径清理：**`purge_non_authoritative` 仅针对主库行/水位，与 export manifest 无关**（已核），
> 故「purge 跳过 empty」在本实现中体现为：**墓碑条目不含 `shards`** ⇒ 即使走到既有 size 校验路径，
> 也是 `shards_info=[]` → `all_ok=True` 但 `shard_paths=[]` → 走既有「回退直连」分支（安全），
> **不会**被误当作有效产物；TTL 过期后正是经此路径重取 ✓。
>
> **消费者处置 4 项**：① 清理/purge：如上（无 manifest purge 消费点，语义已核）；② **测试桩同步**：
> 存量桩（`test_mcp_ckey_empty_shards` / `test_mcp_export_cache` 等）**47 passed 未受影响**；
> ③ **盘点排除**：凡按条目计数/体积盘点 manifest 的脚本应排除 `empty` 条目（本轮无此类在跑消费点，
> 已登记约束）；④ **客户通知追加**：随下批通知档增补「已知空窗在 TTL 内不再重复重取」。
>
> **周末预判**：二期（本批不做）。
>
> **验收**：`tests/test_mcp_empty_tombstone.py` **8 passed**
> （T1 真·空窗写墓碑 / T2 命中跳过重取且不续期 / T3 过期回落重取 / T4 谓词②拒绝 /
> T5 谓词④拒绝 / T6 真实条目整体替换（旧 `empty` 消失）/ T7 TTL env 与默认 / T8 `_tombstone_active` 边界）。

### 取证
- 空窗形态：`export_dataset` 返回 `0 shards / rows=0`（历史实例：`[MCP] export_dataset index_weight: 0 shards, total_rows=0`）；
- 空窗规律（派单给定）：**每 2 周**出现 → 可由**交易日历/周末规则预判**；
- 现状：无负缓存 → 每轮对同一空窗重复发起 export（叠加 P1-4 不可用时空转更久）。

### 改动
| # | 内容 |
|---|---|
| ① | 在既有 export cache manifest 内新增**负缓存条目**（同 ckey 命名空间）：
  `{"empty": true, "ts": …, "ttl_s": …}`；命中负缓存且未过期 → **直接跳过 export**（返回空结果，走既有空语义） |
| ② | **TTL 有限期**（默认建议 7 天，可配 `QS_EXPORT_EMPTY_TTL_S`）；过期即失效、重取一次 |
| ③ | **预判**（可选增强）：对可判定为**周末/非交易日**的窗口直接标空（依据交易日历），不发起 export；仍写负缓存条目留痕 |
| ④ | **留痕与可观测**：命中负缓存时 INFO（含 ckey/年龄/TTL）；负缓存条目数计入 cycle summary |

### 类型判定
**纯性能型**（结果语义不变：空窗仍返回空；命中与否不改变数据内容）＋ **新增检测型**（新增空窗识别与留痕）。
> 注意：须以「负缓存命中 vs 未命中」两条路径**产物逐值一致**为验收前提（纯性能型铁律）。

### 验收
- 单测：首轮空 → 写负缓存；次轮命中 → **不再调用 export**（mock 计数=0）且返回值与首轮一致；TTL 过期 → 重取；
- 单测：非空窗**不受影响**（负缓存不写、行为不变）；
- 端到端：同一空窗在 TTL 内的 export 次数由「每轮 N 次」降为 **1 次**。

---

## 端到端闭环验证（死循环解除的终极判据）

三项（P1-2a / P1-2b / P1-4）落地 + P2-1 可选后，**在客户等价环境跑一轮完整增量拉取**：

| 判据 | 阈值 |
|---|---|
| **总耗时** | **< 2 h**（当前 12h40m；Q1 已量化重锚环 ≈ 14.26h 为独立议题，本轮先看闭环是否解除） |
| **门禁** | **无门禁否决**（无 `detector_degraded` hold、无 quality gate FAIL） |
| **水位** | **正常提交**（四价格表 `status='committed'`，非 `held`） |
| 附加 | 无 `IndexError`、无 `get_artifact` 载荷级 error 未处理、`skipped_empty` 计数可见且非异常量级 |

> 说明：<2h 目标与 Q1（重锚环逐码开销）**分属两条线**——本案先证「闭环解除 + 水位正常提交」；
> Q1 的 C′/D′（并行/缓存）另行推进（待窗取证）。

## 实施序、纪律与待审

| 项 | 内容 |
|---|---|
| **实施序（建议）** | P1-2a（最小、直接解崩）→ **P1-2b 复现**（先红）→ P1-2b 修复 → P1-4 → P2-1 → 端到端验证 |
| 纪律 | 触及 `qfq_resident_orchestrator.py` / `mcp_adapter.py` / `mcp/client.py`（共享核心）→ edit 后即时 `git diff` 自检 + 精确清单 + 实施前零副作用回退点；**回退禁整文件 checkout**（CASE-009 教训） |
| 待审 | ① 四项合并一份方案的边界是否认可；② **P1-2b 是否要求先出「复现矩阵」再落码**（我建议是）；③ P2-1 的 TTL 默认值（建议 7 天）与「周末预判」是否本批做；④ 端到端验证的**执行环境**（客户机 or 本机等价 profile）与窗口 |
| 待用户 | （另一议题）**Q2b ④ 按表隔离 hold** 与 **Q1 窗口化** 一并呈用户裁定 |
