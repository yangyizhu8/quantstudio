# 写时锚 sidecar · 修订规格 v2（2026-11-12 · 节点21 应⑱ 复核修订）

> **v1 → v2 的动因**：⑱ 复核提出五项协议缺口（因子版本及写时值来源／源端到 delivery 的实际变换／批次键集与内容摘要／
> 跨批重写的旧段切分与可见版本／部分失败恢复及历史覆盖）。**v1 的「规格齐全＋三点样本吻合」不构成历史覆盖已闭**；
> **新增表不豁免生产 schema 闸**（v1 此断言撤回）。v1 保留于同目录（文件名含 `-v1-`），以 v2 为唯一有效版本。
> **状态**：修订稿，待**双方对答**（⑱ ↔ ㉑）后**独立②审**；**11-14 仅条件排队，非实施许可**。
> 物理路径：`D:\miniQMT策略实盘\QuantStudio\docs\handoff\qfq-anchor-sidecar-spec-v2-20261112.md`

---

## 〇、v1 撤回与保留部分

| v1 条目 | v2 处置 |
|---|---|
| 字段定义（10 字段） | **保留并扩充**（见 §1，新增 8 个字段） |
| 落点＝QuestDB 同库表 | **保留为推荐**，但**必须走生产 schema 闸**（六件套：契约期望列集＋版本常量＋状态分支＋安全闸放行＋迁移 runner＋测试；铁律 24 全文适用，新表不豁免） |
| 生成时机 fail-closed | **保留**，并细化为**批次级两阶段可见**（见 §4/§5） |
| 验收 8 条 | **保留**，但**新增第 9–12 条**（历史覆盖闭包、跨批重写正确性、部分失败恢复、delivery 对拍） |

**⑭ 明确驳斥的断言**：「新表不改既有表 ⇒ 不触发 schema 指纹契约变更」——**撤回**。理由：生产 schema 闸的适用面由
生产方裁定，不由消费方推导；新表入生产库必须过闸，本规格不再以「新表」为由申请豁免。

---

## 一、五问逐条回答

### 问 1｜因子版本及写时值来源

**v1 缺口**：`a_write` 只有一个数，不知道它从哪条因子记录来、属于哪个因子版本快照。
**v2 补协议**——`a_write` 的**取值必须携带溯源三元组**（字段新增）：

| 新字段 | 类型 | 语义 |
|---|---|---|
| `factor_source` | TEXT 枚举 | `stock_daily_latest`（db 的 `LATEST ON` 覆盖值）／`tushare_increment`（35 天窗口 groupby last）／`tushare_day`（当日 adj_factor 回退）／`etf_daily_latest`／`fund_adj_latest` |
| `factor_asof` | DATE | 该 `a_write` 值对应的**因子快照截至日**（即该因子记录自身的 `trade_date`，非写入日） |
| `factor_version` | TEXT | 因子版本标识（上游若提供修订号则记修订号；无则记 `factor_asof` + 来源表的 `max(updated)` 等可复现指纹） |

**写入口径**（与 TBB ETL 实际逻辑逐字对应）：
- 主 ETL 股票分钟（`get_tushare_data.py` L7574 先 tushare、L7582-7592 db 覆盖）：`factor_source` 按**最终生效的那一路**记
  （db 覆盖成功 ⇒ `stock_daily_latest`，否则 `tushare_increment`）；
- ETF 分钟（L7909/L7915-7924 同构）：`etf_daily_latest`／`fund_adj_latest`；
- 旧补拉（`backfill_minute_gaps.py` L286-291 只查 db）：恒 `stock_daily_latest`；
- 日线（`load_daily` L7337-7342）：同上按最终生效路记。

**不可只增 `factor_asof + state + n_bars` 即宣闭环**：来源枚举、快照指纹、与 ETL 实际取数分支的**逐字对应**是闭环的一部分；
缺任一即验收判据 9 不通过（见 §6）。

### 问 2｜源端到 delivery 的实际变换

**v1 缺口**：v1 只覆盖源端（QuestDB/TBB ETL）写入；delivery（主仓 `aligner._apply_qfq` → DuckDB `*_front`）用的是
**另一套锚语义**（全局快照 `adj_latest_map`/`adj_earliest_map`，`front = raw × adj_i / adj_latest`）。
两者不是同一变换，sidecar 若只记源端，**消费方在 delivery 侧无法对拍**。

**v2 补协议**：
- 新增字段 `stage`，TEXT 枚举：`source`（QuestDB 写入）／`delivery`（DuckDB front 写入）。**默认 `source`**；
  delivery 侧由 `aligner` 落自己的段记录（`stage='delivery'`），**不与源端混表语义**，仅共表共键。
- 新增字段 `xform`，TEXT：该段应用的**实际变换式**（如 `qfq=raw*adj_day/a_write`（source）或
  `front=raw*adj_i/adj_latest`（delivery））。消费方可据 `stage+xform` 判断两侧是否同锚；
  **对拍判据**：同一 `(ts_code,freq,bar)` 上 `source.a_write` 与 `delivery.adj_latest` 若不等，
  即两侧锚不同（回放与对账的分叉点），须显式报告而非静默。
- **明确边界**：本规格只**定义两侧记录协议**；delivery 侧的登记由主仓六步流水线实施（节点1/⑱ 域），
  本线（㉑）只实施 `stage='source'` 侧（TBB 五条写入路径），不越界。

### 问 3｜批次键集与内容摘要

**v1 缺口**：v1 只有 `write_batch_id`，无键集范围与内容指纹 ⇒ 无法验证「本批实际写了哪些键、内容是否一致」
（幂等只靠 upsert，缺内容级证据）。

**v2 补协议**——**批次清单表** `qfq_anchor_sidecar_batches`（与段表同库同闸）：

| 字段 | 类型 | 语义 |
|---|---|---|
| `write_batch_id` | TEXT PK | 同段表 |
| `ts_code` | TEXT | 该批涉及的码（一码一批为最小粒度；跨码批须拆） |
| `freq` | TEXT | 同段表 |
| `key_lo` / `key_hi` | TIMESTAMP | 本批**实际落库键区间**（首/末 bar 时刻，左闭右闭——批内真实写入范围） |
| `n_bars` | BIGINT | 本批实际写入 bar 数 |
| `content_sha256` | TEXT | 本批**内容摘要**：键序化后的行串（与段表同列序、同 round 口径）之 sha256 |
| `stage` / `src` | TEXT | 同段表 |
| `state` | TEXT 枚举 | `pending`／`committed`／`aborted`（两阶段，见问 5） |
| `written_at` | TIMESTAMP | 批记录创建时刻 |

**用途**：①重放验证（同批重跑 ⇒ `content_sha256` 逐位一致，比 v1 的「全表 sha」更快定位）；②覆盖审计
（批次键集并集 vs 段表并集 vs 分钟表实际行，三方对账）；③部分失败恢复的回放单元。

### 问 4｜跨批重写的旧段切分、可见版本

**v1 缺口**：v1 说「主键冲突 upsert 覆盖」——但**跨批重写重叠区间**（回填批覆盖旧批的一部分）不是简单覆盖：
旧段须**切分**（被覆盖部分作废、未覆盖部分保留），且消费方须能看到「当前可见版本」与历史。
**v1 的 upsert 语义在重叠场景下会丢信息。**

**v2 补协议**——**段级 MVCC（追加式，不物理覆盖）**：
- 段表新增字段：`seg_version`（BIGINT，同键区间内单调递增）、`superseded_by`（TEXT，后继段 id，可空）、
  `seg_state`（TEXT 枚举：`active`／`superseded`／`tombstone`）。
- **写入规则**：新批 B 的键区间 `[lo,hi)` 与既有 active 段 S 相交时——
  1. S 被完全覆盖 ⇒ `S.seg_state='superseded'`，`S.superseded_by=B.seg_id`；
  2. S 被部分覆盖 ⇒ S **切成两段**：保留侧 `seg_state='active'`（原 a_write 不变、区间裁剪）、
     被覆盖侧 `seg_state='superseded'`（`superseded_by=B`）；**切分是追加新行，不改旧行的 a_write**；
  3. B 以新 `seg_version = max(同键区间旧 version)+1` 落段，`seg_state='active'`。
- **可见版本**：消费方默认只读 `seg_state='active'`；对拍/审计读全量（含 superseded）以获得完整历史。
- **段 id**：`seg_id = sha256(ts_code|freq|anchor_start|write_batch_id)[:16]`，全局唯一可复现。

### 问 5｜部分失败恢复及历史覆盖

**v1 缺口**：v1 只有「登记失败则该批不落库」一句，未定义：批次部分成功怎么办、重启后如何恢复、
历史（已存在但 sidecar 未覆盖的旧行）怎么补。

**v2 补协议**——**两阶段提交 + 断点恢复 + 历史回补**：
- **两阶段**：写入路径先在批次表落 `state='pending'`（含键集与 content_sha256）→ 业务行落库成功 →
  段表落段、批次改 `state='committed'`。**段表只对 `committed` 批可见**（消费方 join 批次表过滤）。
- **部分失败**：进程在 pending 与 committed 之间崩溃 ⇒ 重启时扫 `pending` 批：
  ①业务行已落（按 `key_lo/hi+n_bars` 反查分钟表对账）⇒ 补段并改 committed（**前滚**）；
  ②业务行未落全 ⇒ 该批 `state='aborted'`，段不落（**作废**），由上游重试；两者以**内容对账**裁决
  （分钟表区间行数与 n_bars 逐位一致才前滚）。
- **历史覆盖**：sidecar 上线**不回改历史**；历史行以**回补批**覆盖——按「键区间无 active 段」缺口清单倒推，
  以 `src='backfill_gaps'`＋`stage='source'` 补登记，补录批同样走两阶段；**缺口清单清零**是验收判据 9 的
  「历史覆盖已闭」的**唯一**证据（三点样本吻合不算）。
- **失败恢复不得动 a_write 语义**：恢复只补/废登记，不重算锚值。

---

## 二、v2 汇总字段表（v1 的 10 字段 ＋ 新增 8 字段）

`ts_code / freq / anchor_start_time / anchor_end_time / a_write / a_day / src / write_batch_id / written_at / schema_ver`
（v1 保留）＋
`seg_id / seg_version / seg_state / superseded_by / factor_source / factor_asof / factor_version / stage`
（v2 新增；`xform` 并入批次表）。`schema_ver = v2`。

## 三、验收判据（v1 的 8 条 ＋ 新增 4 条）

9. **历史覆盖闭包**：`stage='source'` 侧 active 段并集 ∪ 回补批缺口清单清零 ⇔ 分钟表全键覆盖；
   「三点样本吻合」不作为覆盖证据。
10. **跨批重写正确性**：构造「批 B 部分覆盖批 A」夹具 ⇒ A 正确切分、保留侧 a_write 不变、
    B 段 version 递增、`superseded_by` 闭环（无悬空指针）。
11. **部分失败恢复**：构造 pending 崩溃夹具（业务行已落/未落两向）⇒ 前滚/作废与内容对账一致；
    恢复后 active 段与分钟表逐位对账。
12. **delivery 对拍**（`stage='delivery'` 侧落地后）：同键 `source.a_write` vs `delivery` 锚不等时
    显式报告清单（不为零即报告，不设容忍阈值）。

## 四、流程与状态

```
㉑ 出 v2（本件） → ⑱ 逐条对答（双方就五问口径互认，可多轮） → 独立②审 →
[闸] 生产 schema 闸（六件套，⑭/主仓） → 11-14 条件排队（非实施许可）→ ⑤用户确认 → 实施
```

**本线边界重申**：v2 只定义协议；`stage='source'` 侧实施归㉑（TBB 五路径），`stage='delivery'` 侧归主仓节点1/⑱；
表结构入生产库过 schema 闸；**11-14 是条件排队不是实施令**。
