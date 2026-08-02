# 任务书：MCP 行情 is_qfq 还原 raw 方案（adapter 侧还原）

> **状态**：经 codex 审核（2026-08-02），方向拍板通过，附硬条件 P0×2 / P1×4 / P2×2。
> **交付方**：CodeBuddy
> **基线 Git HEAD**：`07c3817`（main）
> **属性**：**框架层变更**（MCPAdapter 复权行为变更 + QFQ 闭环语义），完成后必须走完整流程：
> 本地验证 → 文档同步（README + docs/prompt_engineering + docs/strategy_toolbox）→
> 汇报用户确认 → 才推 GitHub；并按铁律#4 更新实时进度报告（含"P3 复权验证结论重审"如实记录）。

---

## 0. 背景与决定性证据（codex 实测，不得修改）

- **数据现状**（ZCode 描述基本属实，2 处需更正）：
  - `stock_daily`：99.4% 为 qfq（1415 万行，覆盖 2000→2026 全部历史），raw 仅 8 万行（2010-2017 残留）。
  - `stock_minutes`：qfq 3.77 亿行（2024-05 起）、raw 1.03 亿行（2025-01 起）——
    **时间段是重叠的**，按代码分：qfq 覆盖 5224 只、raw 覆盖 2063 只（同一票同一时刻不存在重复）。
- **决定性交叉验证（300750 宁德时代，除权日 2025-04-22）**：
  - QuestDB qfq close（4-21）= 222.519，当日因子 1.875，云端最新因子 1.9495
  - 还原：`222.519 × 1.9495 / 1.875 = 231.36`
  - DuckDB（传统管线 xtquant/tushare 生产）同日 raw close = **231.36** —— 精确一致
  - **结论**：QuestDB 的 qfq = `raw × adj_i/adj_latest`（同一因子系列），且历史行已按最新因子重锚。
- **两套因子系列不同**：
  - 云端系列最新 = 1.9495
  - DuckDB（传统管线 front 锚定的 tushare 系列）最新 = 1.9816（差 1.6%）
  - **还原必须用云端自己的 adj_factor 系列的 per-code 全局最新值**，绝不能混用 tushare 因子。
- **is_qfq=False 的行未必是真 raw**：
  - 抽样 300492.SZ 2025-01-02 09:30 close=50.989（三位小数，非合法 A 股 tick 价格）
  - 这批 2063 只代码的"raw"行真实语义待抽样核实（可能是其他复权口径）。

---

## 1. 方案（已拍板）

**adapter 侧还原 → 管线永远吃 raw + adj_factor**：

```
云端 is_qfq=True 行：
  raw = qfq × adj_latest_global / adj_factor_i
  （adj_latest_global = 该证券完整因子历史的全局最新值，与拉取窗口无关）

云端 is_qfq=False 行：
  暂不直通，先抽样验证语义（P1 硬条件 ③）
```

还原后管线（aligner / qfq_fresh_capture / orchestrator）按既有逻辑消费 raw + adj_factor，
走 aligner tushare 计算路径：`front = raw × adj_i / adj_latest`。

**备选路径否决理由**（codex 确认）：
- ❌ qfq 直接当 front 写、跳过管线复权 → 破坏 12 模块 QFQ 闭环前提（raw_unchanged 门控、事件驱动重锚）
- ❌ 生产端补写 raw 分钟表（4.8 亿行重写）→ 根治但不现实

---

## 2. codex 硬条件清单（必须全部满足，逐条对照验收）

### 【P0-必做】① adj_latest 取法（方案最大技术漏洞）

**问题**：全量回填是按日期分段导出的，**段内最新 ≠ 该证券的全局最新**。
回填 2024 年的分片，段内最后一行的因子是 2024 年的，不是今天的 1.9495。
ZCode 之前建议"用每组最后一行"仍错误。

**硬要求**：
- `adj_latest` 必须从**该证券的完整因子历史**取**全局最新**值，与拉取窗口无关。
- 来源（二选一，实现方决定，但必须文档化）：
  - (a) 云端因子查询：拉一次该证券全历史 adj_factor 取 max(time) 对应值；
  - (b) qfq_aux.db 已注入的快照（MCPAdapter._inject_adjfactor 写入的 adj_factor/fund_adj 表）。
- **禁止**用本次 export 分片内的"最后一行"。

**集成点（精确）**：
- 还原逻辑在 `quantstudio/pipeline/sources/mcp_adapter.py` 的 `_fetch_export`
  和 `_fetch_small_table`（覆盖 stock_daily / etf_daily / stock_minutes / etf_minutes）。
- adj_latest_global 的取数路径：
  - 优先复用 `qfq_aux.db` 已注入的快照（`_inject_adjfactor` 已写 adj_factor/fund_adj 表，
    裸码口径 (code,time) PK）。取 `SELECT adj_factor FROM adj_factor WHERE code=? ORDER BY time DESC LIMIT 1`。
  - 若 qfq_aux.db 无该 code 快照（首次拉取/冷启动）→ 触发一次"仅 adj_factor 列全历史导出"
    （export_dataset 全时间范围，仅取 adj_factor + ts_code + date/trade_date 列），
    注入 qfq_aux.db 后再取全局最新。**不得用本次行情分片内的最后一行兜底**。

**配套测试用例（必须实现）**：
- 拉取一个**不含最新日期**的历史窗口（如 2024-06-01~2024-06-30，最新日期是 2026-08-02）。
- 验证还原后的 raw close 与 DuckDB（传统管线生产）同窗口 raw close 精确一致。
- 断言：还原用的 adj_latest_global ≠ 分片内最后一行的 adj_factor。

---

### 【P0-必做】② P3-2/P3-7 的"QFQ 验证通过"结论需重审

**问题**：codex 查实 300750 在云端 2025-04 的行就是 is_qfq=True。
P3 当时管线拿到的是 qfq 价，`front = qfq × adj_i/adj_latest` 已经是**双重复权**在跑。
当时能通过验证，大概率因为检查的是除权日跳变比率（双重复权后跳变比率仍"符合复权数学"）
或自洽性比对，没对绝对锚定基准做外部对照。

**硬要求**：
- 实现还原后，**必须用 codex 提供的黄金数字复验**（绝对锚定基准外部对照）：

| code | 日期 | 字段 | 黄金值 | 来源 |
|---|---|---|---|---|
| 300750 | 2025-04-21 | raw close | 231.36 | DuckDB xtquant/tushare |
| 300750 | 2025-04-22 | raw close | 230.69 | DuckDB xtquant/tushare |
| 300750 | 2025-04-21 | tushare 锚 front | 218.843 | DuckDB 传统管线 |
| 300750 | 2025-04-22 | tushare 锚 front | 222.726 | DuckDB 传统管线 |

- 注意：表中的 "tushare 锚 front" 用的是 **tushare 因子系列**（最新 1.9816），
  与云端系列（最新 1.9495）不同。MCP 还原后的 front 锚定基准 = 云端系列全局最新，
  **front 值不会等于 tushare 锚 front 值**（这是设计内行为，见 P2-①）。
  因此**复验重点是 raw close 精确一致**（4-21=231.36 / 4-22=230.69），
  front 值另作记录对照差异（预期差 ~1.6%）。
- P3 时代的 staging 库已废弃，无需修复其语义，但在进度报告中**如实记录**这一重审结论。

**集成点（精确）**：
- 复验脚本放 `scripts/verify_mcp_qfq_restore.py`，断言 MCP 还原 raw 与 DuckDB raw 一致。
- 验收报告输出到 `docs/evidence/mcp_qfq_restore_verify_<date>.md`。

---

### 【P1-必做】③ is_qfq=False 行直通前先抽样验证

**问题**：50.989 这种三位小数价格说明 2063 只代码的 is_qfq=False 行未必是真 raw。
若是其他复权口径，直通就是另一种污染。

**硬要求**：
- **先抽样 10-20 只** is_qfq=False 的代码（覆盖 stock_minutes 的 2063 只），
  与 DuckDB/xtquant 真值比对价格。
- 抽样通过（确认是真 raw）→ 这些代码直通；抽样不通过 → 这些代码也走还原路径或标记为可疑跳过。
- **抽样结果写进验收报告**，含每只代码的对照表（云端 close vs DuckDB close vs 差异）。
- **默认安全策略**：抽样结论未出前，is_qfq=False 行**不得直通**，按 is_qfq=True 同样还原
  （还原真 raw 时 `adj_factor_i == adj_latest_global` ⇒ raw = qfq，数学等价，安全）。
  即：**统一全部走还原公式**，把"是否直通"作为优化项延后，不在本次任务实现。

---

### 【P1-必做】④ 还原精度与容差

**问题**：qfq 存三位小数，还原误差在半 tick 内，绝大多数还原到正确 tick（231.36 实证），
但不排除个别行差 1 tick。

**硬要求**：
- 此前任务书的跨源幂等验收（"验收#14 逐列一致"）改为**价格字段容忍 1 tick**或单独说明。
- 1 tick 定义：A 股最小价格变动单位（股票 ≥1 元为 0.01 元；ETF 同）。
- 验收脚本断言：`abs(restored_raw - duckdb_raw) <= 0.01`（价格字段）。
- 非 tick 误差（如 >0.01）的行单独列表输出，供人工复核。

---

### 【P1-必做】⑤ etf_minutes 同样处理（走 fund_adj 系列）

**硬要求**：
- etf_minutes（97% qfq）同样走还原路径，但用 **fund_adj 系列**，与 stock 的 adj_factor 系列分开。
- 集成点：`mcp_adapter.py` 的 `_inject_adjfactor` 已按 `table.startswith("etf")` 分流
  到 fund_adj 表；还原逻辑必须对称：ETF 表的 adj_latest_global 从 `qfq_aux.db.fund_adj` 取，
  不得误用 adj_factor 表。
- 还原公式不变：`raw = qfq × fund_adj_latest_global / fund_adj_i`。

---

### 【P1-必做】⑥ 还原后 is_qfq 标记改写与追溯

**硬要求**：
- 还原后，写入管线的 raw 行 `is_qfq` 列改写为 `False`（语义已是 raw）。
- 原始 is_qfq 标记**进 metadata/batch_audit 便于追溯**：
  - metadata 新增 `original_is_qfq_ratio`（本次拉取中 is_qfq=True 行占比，如 0.994）。
  - metadata 新增 `restored_rows`（实际执行还原的行数）。
  - batch_audit 在 record 时附带 `rows_fixed` = 还原行数（现有列已支持）。

---

### 【P2-文档】⑦ 两模式 front 锚不同（已知限制，写进文档）

**事实**：统一库下两模式 front 锚不同（mcp 锚云端系列 1.9495，传统锚 tushare 系列 1.9816）。
模式切换会触发 QFQ 重锚。

**硬要求**：
- 写进 `docs/mcp_migration/mcp_protocol_probe.md` §7 或 README"已知限制"章节：
  > "MCP 模式与传统模式的 front 复权锚定基准不同（分别用云端因子系列、tushare 因子系列）。
  > 在同一统一库下切换数据源模式会触发 QFQ 重锚事件（qfq_reanchor_event），
  > 这是设计内行为，非 bug。生产建议：同一回测周期内不混用两种模式。"

---

### 【P2-文档】⑧ 原始 is_qfq 标记进 metadata 追溯

**硬要求**：见 P1-⑥，metadata 字段定义需同步进 `docs/mcp_migration/mcp_contract_v1_draft.md`
的 metadata 契约章节。

---

## 3. 改动范围（精确到文件/函数）

### 3.1 `quantstudio/pipeline/sources/mcp_adapter.py`（核心改动）

**新增函数 `_restore_to_raw()`**：
```python
def _restore_to_raw(self, df: pd.DataFrame, table: str, freq: str) -> Tuple[pd.DataFrame, Dict]:
    """把云端 qfq 行情还原为 raw（价格字段），返回 (restored_df, restore_meta)。

    - adj_latest_global 来源：qfq_aux.db 已注入快照（adj_factor/fund_adj 表），
      冷启动时触发一次全历史 adj_factor 导出注入。
    - 还原公式：raw = qfq × adj_latest_global / adj_factor_i
    - 价格字段：open/high/low/close（如存在 pre_close/change 也还原）。
    - is_qfq=False 行：统一同样还原（数学等价，安全），不单独直通（见 P1-③）。
    - 还原后 is_qfq 列改写为 False。
    """
```

**修改 `_fetch_export` 和 `_fetch_small_table`**：
- 在返回 raw_df 前，若 `is_qfq` 列存在且含 True → 调用 `_restore_to_raw()`。
- 还原后的 metadata 增加 `original_is_qfq_ratio` / `restored_rows`。

**新增辅助 `_get_adj_latest_global(code, asset_type)`**：
- 从 qfq_aux.db 的 adj_factor/fund_adj 表取 `SELECT adj_factor ... ORDER BY time DESC LIMIT 1`。
- 若无该 code → 触发全历史 adj_factor 导出注入 qfq_aux.db 后重取。

> 【实现提示·ZCode 审核 2026-08-03】冷启动导出注意两点（已核实 client.py:656）：
> 1. **`export_dataset` 不支持列选择**（只有 `query_snapshot`/`fetch_page` 带 `columns`）。
>    冷启动只能**全列导出**，再用 `normalize_mcp_adj_factor_df` 只提取 adj_factor 列注入。
>    不得为实现"仅 adj 列导出"而修改 `client.export_dataset` 签名（属公共 API 变更，越界）。
> 2. **`data/qfq_aux.db` 已有 2.3GB 快照**（含注入的 adj_factor/fund_adj），冷启动分支仅对 aux.db
>    未覆盖的新 code 触发，不会频繁执行。注入路径复用现成的 `_inject_adjfactor` 即可，**无需新增
>    写库代码**。

### 3.2 `quantstudio/pipeline/daemon.py`（小改，两处）

**改动 ①：batch_audit.record 调用处传 rows_fixed（mcp 行情任务路径）**
- 普通路径 batch_audit.record（约 L694/L733）传入 `rows_fixed = metadata.get("restored_rows", 0)`。
- per_stock 路径 batch_audit.record 同样传 `rows_fixed`（按本批次还原行数聚合）。

**改动 ②：L584 MCP adj_factor 分支表集合补 etf_minutes（用户确认 2026-08-03）**
- **背景**（ZCode 审核发现）：普通路径 `daemon.py:584` 的 MCP adj_factor 提取分支表集合
  为 `("stock_daily", "stock_minutes", "etf_daily")`，**漏了 etf_minutes**；而 per_stock 路径
  `daemon.py:1252-1253` 的同一逻辑表集合含 etf_minutes。两路径不对称。
- **影响**：etf_minutes 若走普通模式（非 per_stock），即便 adapter 已还原 raw，daemon 也不会
  提取 adj_factor 传 aligner → 复权字段留 NULL，验收#5（etf_minutes fund_adj）无法验证。
- **改动**：把 L584 表集合扩为 `("stock_daily", "stock_minutes", "etf_daily", "etf_minutes")`，
  与 per_stock 路径 L1252 对称。**属还原任务的必要配套**（否则还原在 etf_minutes 普通路径失效），
  不视为超范围改动。
- 现有 MCP adj_factor 分支的其余逻辑（normalize_mcp_adj_factor_df 提取、列名重命名、
  raw_df drop 原始 adj_factor 列）保持不变：还原后的 raw + adj_factor 走 aligner tushare 路径。

### 3.3 `quantstudio/pipeline/aligner.py`（**不改**）

- `_apply_qfq` 不动：还原后的 raw 走 front = `raw × adj_i/adj_latest`，与既有逻辑一致。
- adj_latest_map 快照来源仍是 qfq_aux.db（云端系列），与传统模式的 tushare 快照隔离。

### 3.4 `scripts/verify_mcp_qfq_restore.py`（新增）

- 拉取 300750 在 2025-04-21~2025-04-22 的云端数据，还原后断言：
  - raw close 4-21 == 231.36（容差 1 tick = 0.01）
  - raw close 4-22 == 230.69
- 拉取一个不含最新日期的历史窗口，断言 adj_latest_global ≠ 分片内最后一行。
- 抽样 10-20 只 is_qfq=False 代码，与 DuckDB 比对，输出对照表。

### 3.5 文档同步（铁律#1）

- `docs/mcp_migration/mcp_protocol_probe.md` §7：补充还原方案 + 已知限制（front 锚不同）。
- `docs/mcp_migration/mcp_contract_v1_draft.md`：metadata 契约补 original_is_qfq_ratio / restored_rows。
- `README.md`：数据源章节补 MCP 还原说明（简述，技术细节引用 docs）。
- `docs/strategy_toolbox.md` / `docs/prompt_engineering.md`：如涉及复权口径表述，同步更新。

---

## 4. 验收清单（逐条对照 codex 硬条件）

| # | 验收项 | 通过标准 | 对应硬条件 |
|---|---|---|---|
| 1 | adj_latest_global 取数 | 拉历史窗口（不含最新日期），还原 raw 与 DuckDB 一致；断言 adj_latest_global ≠ 分片末行 | P0-① |
| 2 | 300750 黄金数字复验 | raw close 4-21=231.36 / 4-22=230.69（容差 0.01）；front 值记录差异（预期~1.6%） | P0-② |
| 3 | is_qfq=False 抽样 | 10-20 只代码对照表输出；未出结论前不直通 | P1-③ |
| 4 | 价格容差 | 断言 `abs(diff) <= 0.01`；超差行单独列表 | P1-④ |
| 5 | etf_minutes fund_adj 系列 | ETF 还原用 fund_adj 表，不误用 adj_factor；普通路径 daemon L584 表集合含 etf_minutes（与 per_stock 对称） | P1-⑤ |
| 6 | is_qfq 标记追溯 | 还原后 is_qfq=False；metadata/batch_audit 记原始占比 + 还原行数 | P1-⑥ |
| 7 | 两模式 front 锚文档 | mcp_protocol_probe.md / README 写进已知限制 | P2-⑦ |
| 8 | metadata 契约文档 | mcp_contract_v1_draft.md 补字段 | P2-⑧ |
| 9 | 框架层变更流程 | 本地验证 → 文档同步 → 汇报确认 → 才推 GitHub | 铁律#1 |
| 10 | 进度报告更新 | 含"P3 复权验证结论重审"如实记录 | 铁律#4 |

---

## 5. 不得改动的范围（铁律#2 性能/行为隔离）

- ❌ 不改 aligner._apply_qfq 的公式、列名、adj_latest_map 逻辑。
- ❌ 不改 qfq_fresh_capture / qfq_resident_orchestrator / qfq_event_discovery 任何行为。
- ❌ 不改 daemon 的生命周期、PIT、撮合、交易规则。
- ❌ 不改 writer 的 upsert / CREATE OR REPLACE 语义。
- ❌ 不改 MCP server 端任何代码（还原在客户端，server 不动）。
- ❌ 不改传统模式（xtquant/tushare/baostock）的任何取数/复权行为。
- ❌ 不为"性能优化"扩大改动范围；还原公式是语义变更，已分类为框架行为变更。

---

## 6. 给 CodeBuddy 的执行顺序

1. **先写测试骨架** `scripts/verify_mcp_qfq_restore.py`（含黄金数字断言，先红）。
2. 实现 `_get_adj_latest_global` + 全历史 adj_factor 冷启动注入。
3. 实现 `_restore_to_raw` + 在 `_fetch_export`/`_fetch_small_table` 接入。
4. 跑 verify 脚本 → 绿（raw close 231.36/230.69）。
5. 跑历史窗口 adj_latest_global 断言 → 绿。
6. 抽样 is_qfq=False 代码 → 输出对照表。
7. metadata/batch_audit 追溯字段接入。
8. 文档同步（4 个文档）。
9. 输出验收报告 `docs/evidence/mcp_qfq_restore_verify_<date>.md`。
10. 汇报用户，等待确认后才推 GitHub（铁律#1）。

---

## 附录 A：还原公式推导（codex 实证）

云端存储语义（实测 300750）：
```
qfq_i = raw_i × adj_factor_i / adj_factor_latest_global
```

反推 raw：
```
raw_i = qfq_i × adj_factor_latest_global / adj_factor_i
```

代入实证（4-21）：
```
raw = 222.519 × 1.9495 / 1.875 = 231.36  ✓ 与 DuckDB raw 一致
```

**关键**：adj_factor_latest_global 必须是**云端因子系列的全局最新**（1.9495），
不是分片内最后一行的因子，也不是 tushare 系列的最新（1.9816）。

---

## 附录 C：日线(stock_daily)实测验证（2026-08-02 ZCode 实测，非推断）

**问题背景**：codex 审核聚焦分钟线，需确认日线是否同样需还原。

**实测结论：日线同样必须走还原方案，证据确凿。**

### C.1 stock_daily is_qfq 分布
- query_snapshot 抽样 5000 行：is_qfq=True **100%**（codex 报 99.4%，实测更高）
- 列结构含 adj_factor + is_qfq 两列，与分钟线一致

### C.2 日线黄金数字验证（300750 宁德时代）

| 日期 | qfq_close | adj_i | 还原raw | codex黄金 | 偏差 | 判定 |
|---|---|---|---|---|---|---|
| 2025-04-21 | 222.519 | 1.8750 | 231.3604 | 231.36 | 0.0004 | ✓ PASS |
| 2025-04-22 | 226.312 | 1.9125 | 230.6903 | 230.69 | 0.0003 | ✓ PASS |

- 全局最新 adj_factor = 1.9495（2026-07-13，与 codex 一致）
- 容差 1 tick = 0.01，实测偏差 < 0.001，远优于容差

### C.3 P0-① 全局 latest 证明（日线早期日期）

2024-06-03 的 300750：
- 正确（全局 latest=1.9495）→ 还原 raw = **202.5001**
- 错误（2024-06 分片末行=1.8660）→ 还原 raw = **193.8267**
- **差异 8.67 元** → 证实 codex P0-①：日线也必须用全局 latest，不能用分片末行

### C.4 双重复权确认（日线）

4-22 数据：
- 正确还原 raw = 230.69 → 正确 front = `230.69 × 1.9125/1.9495` = **226.312**
- 当前错误（直接用 qfq 当 raw）：front = `226.312 × 1.9125/1.9495` = **222.017** ← 双重复权
- codex 给的 tushare 锚 front = 222.726（tushare 系列，与云端系列差 1.6%，设计内）

### C.5 任务书覆盖确认

任务书 §3.1 的 `_restore_to_raw` 作用于 `_fetch_export` 和 `_fetch_small_table`，
已覆盖四张表：**stock_daily / etf_daily / stock_minutes / etf_minutes**。
日线无需额外修改任务书，本附录仅为补充实测证据。

---

## 附录 B：为什么不能用"分片内最后一行"兜底

全量回填按日期分段导出。假设回填 2024 年分片：
- 分片内最后一行的 adj_factor = 2024-12-31 的因子（如 1.85）
- 但该证券全局最新因子 = 2026-08-02 的 1.9495
- 用分片末行 1.85 兜底 → 还原 raw = `qfq × 1.85 / adj_i` → **错误**（少乘了 1.9495/1.85 的比率）

只有用全局最新 1.9495 还原，才能数学等价于"云端 qfq = raw × adj_i/adj_latest"的逆运算。

---

*本任务书由 codex 审核 + ZCode 整合，硬条件源自 codex 实测证据，不得删减。*
