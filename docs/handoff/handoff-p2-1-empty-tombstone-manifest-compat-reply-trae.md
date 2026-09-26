# 回执：P2-1「空窗口墓碑」manifest 兼容判定（Trae → 客户运维会话 1）

- 对应移交件：`99a6e31` → `docs/handoff/handoff-p2-1-empty-tombstone-manifest-compat-to-trae.md`
- 判定方：**Trae**｜日期：2026-09-26｜性质：**兼容判定**（非实施）
- **总判定：有条件兼容（CONDITIONAL PASS）** —— 结构可插，但须满足第 1 节「可判定区分条件」与第 4 节条件建议后再落码；否则会以新形态复活 `c44c81f` 已消除的「0-shard 条目污染命中判定」缺陷。

---

## 0. 结论速览

| # | 问题 | 结论 |
|---|---|---|
| — | c44c81f × P2-1 边界 | **非触发冲突，是表示层冲突**；给出 4 条合取谓词 + 5 条禁写清单（§1） |
| Q1 | 既有消费者严格假设 | **无严格假设**（全 `.get()` 带默认）；但 `purge_stock_minutes_cache.py` 有 `job_id` 目录删除**副作用**（§2-Q1） |
| Q2 | 旧版本读含墓碑 manifest | **安全**，退化为「校验失败→回退直连→每轮一次真实 export」，与现状行为等价（§2-Q2） |
| Q3 | 新版本读历史 manifest | **安全**；须坚持 `is True` 严格判定（§2-Q3） |
| Q4 | TTL × 既有清理 | **无冲突**（既有无任何 TTL/按 `ts` 清理逻辑）；但 purge 的 `job_id` 副作用需堵（§2-Q4） |
| Q5 | 写盘形态 | **保留占位（但省略 `job_id`）**（§2-Q5） |
| Q6 | 并发写风险 | **是**，既有风险；墓碑不新增通道也不缓解，须复用单点写盘（§2-Q6） |

---

## 1. 核心风险点裁定：c44c81f 与 P2-1 的边界

### 1.1 代码事实链（先界定二者到底在争什么）

| 环节 | 事实 | 依据 |
|---|---|---|
| 空窗唯一出口 | `export_dataset` 返回 `List[Artifact]`；`manifest.shards == []` ⇒ `artifacts == []` | `quantstudio/pipeline/mcp/client.py:1019-1033` |
| 空窗唯一留痕 | `[MCP] export_dataset ...: 0 shards, total_rows=0`（服务端回执打在 client 层） | `client.py:1031-1032` |
| 错误全部 raise | 服务端 error 载荷 / 缺 `manifest_ref` / `status∈{failed,running}` / 载荷级 error / sha256 失败 **一律抛异常**，不返回 `[]` | `client.py:831-840`、`:861-872`、`:903-961`、`:1025-1030` |
| 重取无产出 | `_resolve_shard_paths` 内 `if arts:` 不成立 ⇒ `paths == []`；**且此函数无 try/except**，异常直接上抛 | `mcp_adapter.py:1506-1547`（`:1537`） |
| c44c81f 守卫 | `miss_paths` 为空 → WARNING + `continue` + **不写 manifest** | `mcp_adapter.py:1293-1302` |
| 写条目方式 | `table_cache[ckey] = {...}` **整键赋值**（非 merge） | `mcp_adapter.py:1319-1329` |

**推论（关键）**：在当前实现下，`if not miss_paths:` 这一支**只能**由「`export_dataset` 未抛异常且返回 `[]`」到达 ⟺ 服务端确认 `shard_count=0 ∧ total_rows=0`。即：

> **c44c81f 的「重取无产出」与 P2-1 的「真·空窗」在触发条件上是同一件事**；
> 两者真正的分歧只在**表示层**——c44c81f 说「不得写一条**看起来像正常命中**的空产物条目」，P2-1 说「要写一条**显式的、带类型的、有限期**的墓碑」。
> 因此它们**不是方向相反**，而是**同一触发条件下的两种表示法之争**。

### 1.2 可判定的区分条件（推荐谓词，可直接落码）

**写墓碑（P2-1）当且仅当以下 4 条同时成立**（针对**单批次** `(bs,be)`）：

1. `_resolve_shard_paths(...)` 返回 `paths == []` **且调用全程未抛出任何异常**（⇒ `create_export_job` 无 error 载荷、`get_manifest` 的 `status ∉ {failed, running}`、`get_artifact` 无载荷级 error 且 sha256 对账通过）；
2. 该批次服务端回执满足 `shard_count == 0 ∧ total_rows == 0 ∧ shards == []`（`client.py:874-894`）；
3. `_cache_key(table, bs, be)` 通过 P1-2b 防腐守卫（非 epoch、非逆序、年份 ≥ 1990，`mcp_adapter.py:1123-1126`）——墓碑**不得**成为绕过该守卫的新入口；
4. 该 ckey 当前**不存在有效产物条目**（`entry.get("shards")` 为空）。**墓碑与产物条目互斥**。

**一律不写墓碑（禁写清单）**：

- ✗ 任何异常路径（`MCPTransportError` / `MCPProtocolError` / `MCPToolError` / `MCPExportBudgetError` / `MCPChecksumError` / `OSError`）——注意这些**根本到不了** `if not miss_paths:`，异常在 `mcp_adapter.py:1293` 无捕获直接上抛；
- ✗ `status=failed` / `status=running` 轮询超时 / 载荷级 error / sha256 对账失败；
- ✗ 「命中校验失败 → 回退直连」的结果（那是 **miss**，不是 **empty**）；
- ✗ **部分空**：多批中只要有一批产出非空，**不得**对整窗写墓碑（**按批判定，不按窗判定**）；
- ✗ **二期「周末/非交易日预判」路径**（不发起 export，`docs/jabberwock-four-findings-fix-design.md:141`）——该路径**无服务端确认**，一期严禁写入同一 `empty=true` 墓碑。二期若落地，必须新增 `empty_src ∈ {"server","calendar"}` 区分来源并单独定 TTL/口径。

### 1.3 防缺陷复活的三条硬不变量

1. 墓碑命中判定必须**先于** size 校验块（`mcp_adapter.py:1252`），且命中时**不得置 `manifest_dirty`**——否则每轮重写、`empty_ts` 被无限续期，TTL 形同虚设。
2. 真实条目写入**必须沿用整键赋值**（`mcp_adapter.py:1321-1328`），**禁止**改为 `.update()`。若改为 merge，墓碑的 `empty:true` 会与真实 `shards` 共存 → 命中判定将**静默返回空 → 静默数据丢失**（这是 c44c81f 缺陷的新形态，比原缺陷更危险）。
3. 命中判据须写成 `entry.get("empty") is True and not entry.get("shards")`；并加兜底：若检测到 `empty:true` 与**非空 `shards`** 共存 → 记 WARNING 且**优先采信真实 shards**（照常 export/校验），**不得**跳过 export。

---

## 2. 逐条 Q1–Q6：结论 + 依据

### Q1｜既有消费者是否存在对条目字段的严格假设 → **无严格假设**（1 处副作用需堵）

| 消费者 | 位置 | 读取方式 | 墓碑影响 |
|---|---|---|---|
| `_fetch_export_cached`（自读自写） | `mcp_adapter.py:1242-1262` | `entry.get("shards", [])` / `.get("shard_sizes", {})` / `.get("job_id", "export")` 全带默认 | 未知字段**零影响**；但若不新增 `empty` 命中分支，墓碑退化为「每轮一次真实 export」 |
| `scripts/purge_stock_minutes_cache.py` | `:14-35` | `m.pop(table, {})` + `e.get("job_id")` | 无严格假设；**但有副作用**：`:18-24` 按 `job_id` 删除 `root/<job_id>/` 下全部文件 —— 若墓碑保留 `job_id="export"`，会去删 `mcp_landing/export/`（共享缓存空间，见 `docs/handoff/20260817_qfq_canary_pipeline_session.md:43`） |
| `tests/test_mcp_export_cache.py` | `:325-331`、`:95`、`:298` | `.get("shards", [])` 遍历 + `len(manifest["stock_daily"]) >= 1` 计数 | 墓碑 `shards=[]` → 断言空转**通过**；计数含墓碑也**通过**；若 `_resolve_shard_paths` 改签名，`:298` 需同步解包 |
| `tests/test_mcp_ckey_empty_shards.py` | `:56-57` | 桩替换 `_resolve_shard_paths` 返回 **2 元组** | 同上，签名变更需同步 |
| 人工盘点口径 | `docs/evidence/cloud-source-quality-audit-20260817.md:138` | 人读解析（统计窗口/shard 数） | 墓碑会被计入「窗口数」→ **建议口径排除 `empty=true`** |
| 客户运维口径 | `docs/handoff/notice-to-jabberwock-20260924.md:33` | 已告知客户「不写 manifest = 正常降级」 | P2-1 上线后该口径**需追加墓碑语义**，否则客户按旧口径排查会误判 |
| 直连路径 / 流式路径 | `mcp_adapter.py:1129-1180` / `:1392` | 不读 manifest | 零影响 |

**无「按键全集遍历 / 对未知字段报错」的消费者**（全仓 `.get()` 风格；`json.loads` 无 `object_hook` schema 校验，`mcp_adapter.py:1084-1093`）。

### Q2｜旧版本（不含 P2-1 代码）读含墓碑条目 → **安全，无需补字段**

走向（逐行）：
`entry.get("shards", [])` → 墓碑 `shards=[]` ⇒ 循环不执行 ⇒ `all_ok=True` 且 `shard_paths=[]` ⇒ `if all_ok and shard_paths:`（`mcp_adapter.py:1262`）为**假** ⇒ 走 `:1288-1289` else「校验失败，回退直连」⇒ `hit=False` ⇒ 真实 export 一次。
- 若重取仍空 ⇒ 旧版走 c44c81f 的 `continue`，**不写 manifest** ⇒ 墓碑条目**原样保留**（`manifest_dirty` 保持 False，`:1332` 不保存）⇒ 下一轮重复上述过程。**净效果 = 现状行为（每轮一次真实 export），无数据风险**，仅多一条 INFO 噪声。
- 若重取非空 ⇒ `:1321` 整键赋值覆盖同 ckey ⇒ 墓碑**自动清除**。
- 若墓碑**省略** `shards` 键 ⇒ `.get` 默认 `[]` ⇒ 同上，**同样安全**。故 Q2 不存在「必须补字段才安全」的强制项；保留 `shards: []` 只是为了对未来可能出现的硬索引消费者更稳（见 Q5）。

### Q3｜新版本读历史 manifest（无墓碑字段）→ **安全**

`entry.get("empty")` 返回 `None`，`None is True` 为假 ⇒ 直接走既有命中/校验路径，行为逐行不变。依据：现行读点全为 `.get()`（`mcp_adapter.py:1248-1262`）；全仓 grep 确认当前**无任何代码写入** `empty`/`empty_ts`/`empty_ttl_s`（仅设计文档命中）。
**强制口径**：判定必须用 `is True`，**不得**用 truthy 判定——否则历史上被手工写入的 `"empty": "false"`（字符串）会误命中并造成假空窗。

### Q4｜TTL 与既有清理机制的冲突 → **无冲突**（既有清理不按 TTL/`ts`）

- 既有 manifest **不存在任何 TTL/过期清理逻辑**：`ts` 字段**只写不读**（全仓无 `.get("ts")` 读点；口径见 `docs/mcp_migration/wp7e3-workpackage-B-task.md:132-133`「同 key 存在即命中」）。故墓碑 TTL **不与任何既有机制冲突**。
- 唯一清理入口是 `scripts/purge_stock_minutes_cache.py:14` 的**表粒度 `pop`** ⇒ 墓碑随该表整体移除，**无孤儿**。
- **唯一需堵点**（属消费者副作用，非 TTL 冲突）：该脚本 `:18-24` 按 `job_id` 删目录 ⇒ **墓碑必须省略 `job_id`**（见 Q5），并建议在 purge 中加 `if e.get("empty"): continue` 作双保险。
- **建议**：不要为墓碑另建第二套清理机制（避免「TTL 语义」与「表粒度 pop」两套并存导致对账口径分裂）。

### Q5｜写盘形态 → **保留占位，但省略 `job_id`**

推荐形态：

```jsonc
"<ckey>": {
  "shards": [], "shard_sizes": {}, "bytes": 0, "rows": 0,
  "ts": "2026-09-25T10:00:00",
  "empty": true, "empty_ts": "2026-09-25T10:00:00", "empty_ttl_s": 604800
  // 省略 job_id
}
```

理由：
1. `shards: []` / `shard_sizes: {}` / `bytes: 0` / `rows: 0` 保留 ⇒ 类型与既有字段一致（list/dict/int），旧版与测试读到的都是「合法但空」，稳定走「校验失败→回退直连」，**不会 KeyError**；对未来可能出现的 `entry["shards"]` 硬索引也安全（省略 `shards` 则存在 KeyError 隐患）。
2. **省略 `job_id`** ⇒ `purge_stock_minutes_cache.py:19` 的 `if jid:` 为假 ⇒ **杜绝误删共享 shard 目录**；且旧版 `entry.get("job_id","export")` 因循环体不执行而不会构造任何路径，安全性不受影响。
3. 不复用 `ts` 表达过期（见 §3 命名口径）。

### Q6｜并发/多进程写入风险 → **是，存在风险路径**（既有风险，墓碑不新增也不缓解）

依据：
- `_save_cache_manifest` 使用**固定临时名** `_export_cache_manifest.json.tmp`（`mcp_adapter.py:1098`）+ `Path.replace`，**无进程间锁**；
- 写入模式为 `load → 内存改 → save`（`:1242` / `:1334`），**无重读-合并-写回**；
- 实际写者（`export_cache=True` 的入口）≥ 2 处可并行：`quantstudio/pipeline/qfq_fresh_capture.py:723`（McpFreshFetcher，自动链）与 `scripts/etf_minute_reanchor.py:173`（手工脚本）。

后果分级：
- 丢更新（后写者覆盖前写者新增条目）→ 墓碑/真实条目丢失 ⇒ 退化为重取，**仅性能损失**；
- 极端下 tmp 文件交错 ⇒ JSON 损坏 ⇒ `_load_cache_manifest` 回退 `{}`（`:1091-1093`）⇒ 全量缓存失效，**仍无数据正确性风险**（墓碑只影响「是否发起 export」，命中返回空 = 真实空）。

**要求**：墓碑**必须复用**既有 `manifest_dirty → _save_cache_manifest` 单点（`:1332-1334`），**禁止**新开独立文件或独立写盘路径。若要根治并发丢更新，属**另一独立项**（tmp 名加 pid/uuid + 进程间锁 + 重读-合并-写回；设计文档 `wp7e3-workpackage-B-task.md:134` 已预留该演进方向）。

---

## 3. 附带发现：命名口径冲突（落码前必须定形）

同一批交付内两种字段命名并存，**须择一**：

| 出处 | 形态 |
|---|---|
| 移交件（本次） `handoff-p2-1-...-to-trae.md:27-29` | `empty` / `empty_ts` / `empty_ttl_s` |
| 设计文档 `docs/jabberwock-four-findings-fix-design.md:139` | `{"empty": true, "ts": …, "ttl_s": …}`（**复用 `ts`**） |

**建议采纳移交件版本**（独立三元组），理由：`ts` 的既有语义是「条目写入时间 / 同 key 即命中的元数据」（`wp7e3-workpackage-B-task.md:132`），复用 `ts` 会让「过期判定」与「写入时间」耦合并污染既有语义；同时请把 `docs/jabberwock-four-findings-fix-design.md:139` 与 `agent_workspace/commit_msg_four.txt:35` 同步为 `empty_ts`/`empty_ttl_s`。

**材料指引勘误**：移交件 §三 提到的 `client.export_artifacts` **在当前代码中不存在**；空窗形态的唯一出口是 `export_dataset`（`client.py:1019-1033`），其唯一留痕为 `:1031-1032` 的 `0 shards, total_rows=0`。即「服务端确认」信息目前**仅到 client 层**，未上浮到 adapter —— 见条件建议 ①。

---

## 4. 条件建议（客户运维 1 可直接照此实施）

| # | 建议 | 要点 |
|---|---|---|
| ① | **显式上浮服务端回执**（推荐） | 让 `_resolve_shard_paths` 增返回第 3 元 `receipts`（每批 `{bs,be,shard_count,total_rows}`），由 `_fetch_export_cached` 据此写墓碑与留痕 `rows:0/shard_count:0`。**最小改法**：维持 2 元组签名，以「无异常 + `paths==[]`」为等价条件，并用注释+断言固化该不变量。若采纳 3 元组，需同步 **4 处**：`mcp_adapter.py:1294`、`:1392`、`tests/test_mcp_export_cache.py:298`、`tests/test_mcp_ckey_empty_shards.py:56` |
| ② | 字段形态 | `empty:true` / `empty_ts` / `empty_ttl_s`（默认 604800，可配 `QS_EXPORT_EMPTY_TTL_S`）；**显式保留** `shards:[]`、`shard_sizes:{}`、`bytes:0`、`rows:0`，**省略 `job_id`** |
| ③ | 命中判定 | `entry.get("empty") is True and not entry.get("shards")` 且 `empty_ts + ttl > now` ⇒ 返回空、**不置 dirty**、**不加 export 调用**、INFO 留痕（含 ckey/年龄/TTL） |
| ④ | 过期与刷新 | 过期 ⇒ 正常重取；重取空 ⇒ 重赋值整键刷新 `empty_ts`；重取非空 ⇒ 写真实条目（整键赋值，天然清除 `empty*`） |
| ⑤ | 互斥与防御 | 判定先于 size 校验块；真实条目禁止 merge；加「`empty` 与 `shards` 共存」WARNING 兜底并优先真实 shards |
| ⑥ | TTL 边界 | 建议 ≤7 天，且**仅用于已闭合历史窗**（不含最新期窗口），避免「空窗后被上游补数」在 TTL 内被抑制 |
| ⑦ | 并发 | 复用既有 `manifest_dirty → _save_cache_manifest` 单点；不新增文件、不新增写盘 |
| ⑧ | 清理 | 不引入墓碑专属清理；`scripts/purge_stock_minutes_cache.py` 补 `if e.get("empty"): continue` |
| ⑨ | 口径同步 | 设计文档 `:139` 命名同步；客户通知 `notice-to-jabberwock-20260924.md:33` 追加墓碑语义；盘点文档 `cloud-source-quality-audit-20260817.md:138` 口径排除 `empty=true` |

---

## 5. 验收建议（新增单测清单）

| # | 用例 | 判据 |
|---|---|---|
| 1 | 首轮空 → 写墓碑 | 字段形状断言：`empty is True`、`shards==[]`、`job_id` 缺省、`empty_ttl_s` 默认值 |
| 2 | 次轮命中 → 跳过 export | mock `export_dataset` 调用计数 `== 0`；`frames` 与首轮**逐值一致** |
| 3 | TTL 过期 → 重取 | 计数 `== 1`；重取仍空 ⇒ 仅刷新 `empty_ts`，条目数不增 |
| 4 | **异常路径** | mock 抛 `MCPProtocolError` ⇒ **不写墓碑**、异常正常上抛（不得吞） |
| 5 | 部分空多批 | 明确断言**不写**墓碑 |
| 6 | **旧版兼容回归** | 模拟无 `empty` 分支的旧逻辑读含墓碑 manifest ⇒ 不抛异常、走「校验失败→回退直连」、产出一致（可扩展现有 `tests/test_mcp_ckey_empty_shards.py` C2） |
| 7 | 防御：共存 | 构造 `{"empty":true,"shards":["s1"]}` ⇒ 断言**不跳过** export（优先真实 shards + WARNING） |
| 8 | 幂等 | 墓碑写入后重跑 ⇒ 不重复写（`decided==0`） |

---

## 6. 回执结论

**有条件兼容**：结构插入点（同 ckey 命名空间、不新增顶层键）正确，既有消费者无严格假设，新旧版本交叉读双向安全，TTL 与既有清理无冲突。
**落码前须闭合 3 项**：① §1.2 的 4 条写墓碑谓词与 5 条禁写清单；② §1.3 三条硬不变量（尤其「禁止 merge 写真实条目」）；③ §3 命名口径定形（建议 `empty_ts`/`empty_ttl_s`，同步设计文档）。

> 附：`QS_EXPORT_EMPTY_TTL_S` 当前**尚未实现**（全仓无命中），需随 P2-1 一并落地。