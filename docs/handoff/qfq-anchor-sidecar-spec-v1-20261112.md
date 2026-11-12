# 写时锚 sidecar · 六要素规格件（v1 · 2026-11-12 · 节点21 制定，⑱ 实施）

> **本件性质**：**规格件（消费方定义）**，非实施方案。R17 审核派件：sidecar 六要素由**锚语义消费方（节点21）**定义，
> 不假手他人；⑱ 在等、**L2 存量回放步③ 因此阻塞**。
> **交付面**：落主仓（消费方 ⑱ 与节点1 均在主仓）。
> 物理路径：`D:\miniQMT策略实盘\QuantStudio\docs\handoff\qfq-anchor-sidecar-spec-20261112.md`
> 上游依据：`D:\miniQMT策略实盘\trading-battle-back\docs\design-l2-minute-reanchor-20261111-v2.md` §二·§十（口径定谳）

---

## 〇、一句话：要什么

一句话：**每条写入路径在把行落库之前，把「当时用的 `latest_adj`（即 `a_write`）」按「写入批段」登记下来**，
使消费方可以**直接读出**任意分钟/日线行的锚，而不必像现在这样从价格比值**反推**。

### 为什么必须这么做（我方实测证据，非推断）

| 事实 | 实测证据 |
|---|---|
| 分钟表 `adj_factor` 列存的是**交易日因子 a(t)**，**不是** `a_write` | 两表 `adj_factor` 区间边界逐位一致（`000063.SZ`：16.7756@≤2025-04-15 / 17.1055@2025-04-16~07-28 / 17.3141@≥2026-07-29） |
| **同一因子区间内并存多个 `a_write`** ⇒ 无法用 a(t) 或区间常量替代 | `000063.SZ` 区间 17.1055 内：`2025-04-16` ratio=1.012205（a_write=17.1055）而 `2026-07-28` ratio=**1.000000**（a_write=**17.3141**）；`688808.SH` 同区间内并存 ratio=1.0 与 ratio=1.48 |
| 反推法有抽样误差、且无法分辨「已同锚」与「中间锚」 | L2 步⑥ 加密抽样：同 40 码，`--days-per-interval` 3→12 使可回放段 **47→64（+36%）**、排除回填行 **219→840 天（+284%）** ⇒ 稀疏抽样漏检混批段 |

⇒ 结论：**`a_write` 必须由写入侧记录**；价格比值反推只能做校验，不能做数据源。

---

## 一、字段定义（要素①）

**粒度 = 写入批段（segment）**，不是 per-bar、也不是 per-code 常量。
依据：同一 `(ts_code, freq)` 内，`a_write` 在「一次写入批内恒定、跨批变化」；且实测存在**同日跨批**（`--plan` 的
`intraday_overlap` 已探到「同日跨因子区间」）⇒ 段边界必须能落到**时刻**，不能只落到日。

| # | 字段 | 类型 | 必填 | 语义 | 取值域/约束 |
|---|---|---|---|---|---|
| 1 | `ts_code` | TEXT | ✔ | 证券代码（含后缀） | 与分钟表 `ts_code` 同口径 |
| 2 | `freq` | TEXT | ✔ | 频率 | `1min`/`5min`/…；**日线用 `D`** |
| 3 | `anchor_start_time` | TIMESTAMP | ✔ | 段起点，**含** | 左闭；段内最小 bar 时刻（日线＝当日 00:00） |
| 4 | `anchor_end_time` | TIMESTAMP | ✔ | 段终点，**不含** | 右开；段内最大 bar 时刻的下一个边界 |
| 5 | **`a_write`** | DOUBLE | ✔ | **写入时点的 `latest_adj`**（该段所有行共用的 qfq 分母） | >0 且 finite |
| 6 | `a_day` | DOUBLE | ✔ | 该段的**交易日因子** `a(t)`（段内恒定；供交叉校验与段切分自证） | >0 且 finite；段内不得有两个不同值 |
| 7 | `src` | TEXT | ✔ | 写入路径标识 | `etl_stock`／`etl_etf`／`backfill_gaps`／`etl_daily`／`staging_promote`（枚举，新增须登记） |
| 8 | `write_batch_id` | TEXT | ✔ | 写批标识（溯源用；同日多批靠它区分） | 非空；同批内一致 |
| 9 | `written_at` | TIMESTAMP | ✔ | 该段记录的**生成时刻**（非业务时间） | 用于审计与「谁在何时改了锚」 |
| 10 | `schema_ver` | TEXT | ✔ | 规格版本 | 本期固定 `v1` |

**派生量（消费方计算，不落库）**：
- 行的理论比值 `ratio_row = a_latest / a_write`（`a_latest` 取 `stock_daily`/`etf_daily` 的 `LATEST ON trade_date`）
- 回放修正因子 = `1 / ratio_row`；回放目标值 = 落库值 `÷ ratio_row`

**明确不做（避免第二套语义）**：不在 sidecar 里存价格、不存 `ratio`、不存回放结果；`ratio` 必须由消费方用
`a_latest/a_write` 现算（否则 `a_latest` 一变，sidecar 里的 `ratio` 就过期）。

## 二、落点（要素②）

**推荐：甲（同库表）**——`QuestDB` 表 `qfq_anchor_sidecar`，与 `stock_minutes` / `etf_minutes` 同库。

| 案 | 落点 | 优点 | 缺点 | 我方倾向 |
|---|---|---|---|---|
| **甲** | QuestDB 表 `qfq_anchor_sidecar`（本地 `http://127.0.0.1:9000`） | 与分钟表**同库可直接 join**；时间口径天然一致（无跨源搬运）；可按 `ts_code` 分区 | 需 DDL（新表，不改既有表 ⇒ 不触发 schema 指纹契约变更） | **✅ 推荐** |
| 乙 | 按日 Parquet `D:\miniQMT策略实盘\QuantStudio\data\qfq_anchor_sidecar\YYYYMMDD.parquet` | 无 DDL、易归档 | 消费方须自建 join（时间口径/类型易错配）；跨源搬运风险 | 备选 |
| 丙 | DuckDB delivery 库表 | 与 delivery 侧同库 | 与 QuestDB 侧分钟表跨库，L2 主消费面在 QuestDB 侧 ⇒ 反而多一跳 | 不推荐 |

**若 ⑱ 选乙**：必须同时给出**期号清单文件**（`_index.json`：期号→文件绝对路径→行数→sha256），否则消费方无法保证「无空洞」。

## 三、命名（要素③）

| 对象 | 约定 | 例 |
|---|---|---|
| 表/根目录 | `qfq_anchor_sidecar`（固定，小写下划线） | `qfq_anchor_sidecar` |
| 字段 | snake_case，无语义冗余前缀 | `a_write`（不写 `latest_adj_at_write`） |
| Parquet 分片（若选乙） | `qfq_anchor_sidecar_YYYYMMDD.parquet`（按 `written_at` 的日切） | `qfq_anchor_sidecar_20261112.parquet` |
| 期号索引（若选乙） | `qfq_anchor_sidecar_index.json` | 同目录 |
| 版本 | `schema_ver = v1`；不兼容变更升 `v2` 并双写过渡 | — |

## 四、生成时机（要素④）

**硬规则：`a_write` 必须在写入侧「落库前」取一次并随行登记——禁止事后反推。**

| 写入路径 | 落点（TBB 仓） | 登记点 | 对应 `src` |
|---|---|---|---|
| 主 ETL · 股票分钟 | `D:\miniQMT策略实盘\trading-battle-back\data\get_tushare_data.py` `_process_single_stock_minutes`（复权调用 L7640） | 取得 `latest_adj`（L7591 用 db 覆盖 tushare 之后）→ 复权前 | `etl_stock` |
| 主 ETL · ETF 分钟 | 同上 `_process_single_etf_minutes`（L7968） | 同上（L7924 之后） | `etl_etf` |
| 旧补拉 | `D:\miniQMT策略实盘\trading-battle-back\data\backfill_minute_gaps.py`（L291 取 `latest_adj` 后） | 复权前 | `backfill_gaps` |
| 日线路径 | `get_tushare_data.py` `load_daily`（L7346 附近） | 日线 qfq 前 | `etl_daily` |
| staging 转正 | `minutes_qfq_freeze.stage_if_frozen` 转正路径 | 转正时继承原批 `a_write` | `staging_promote` |

**幂等**：主键 `(ts_code, freq, anchor_start_time, write_batch_id)` ⇒ 同批重跑**覆盖写**，两次运行结果逐位一致。
**与冻结的关系**：staging 期间**不登记**；转正时按原批 `a_write` 登记（`src=staging_promote`）。
**失败语义**：登记失败 ⇒ **该批不落库**（fail-closed，与 R1 甲同族）；已登记但落库失败 ⇒ 段成为孤儿，由验收判据 7 捕获。

## 五、接口（要素⑤）

**消费方契约（⑱ → 节点21）**，三条必给：

1. **点查（bar → 锚）**
   ```sql
   SELECT a_write, a_day FROM qfq_anchor_sidecar
   WHERE ts_code = ? AND freq = ?
     AND anchor_start_time <= ? AND ? < anchor_end_time;
   ```
   期望：**恰好 1 行**（段不相交且覆盖完整）；0 行＝覆盖缺口（违约）；≥2 行＝段重叠（违约）。
2. **段列举（区间 → 段清单）**
   ```sql
   SELECT ts_code, freq, anchor_start_time, anchor_end_time, a_write, a_day, src, write_batch_id
   FROM qfq_anchor_sidecar
   WHERE ts_code = ? AND freq = ? AND anchor_end_time > ? AND anchor_start_time < ?
   ORDER BY anchor_start_time;
   ```
   用途：L2 按段回放（与 `apply_minute_segments` 的 `(code,freq,t_start,t_end)` 直接对齐）。
3. **当日覆盖自证（新鲜度）**
   ```sql
   SELECT max(written_at), count() FROM qfq_anchor_sidecar WHERE written_at >= ?;
   ```
   用途：门禁判「sidecar 是否跟得上最新写入批」。

**接口纪律**：
- 段语义**左闭右开**，与 `apply_minute_segments` 一致；
- 时间口径必须是**仓内既有日界口径**（`day_start_ms` 同源），禁自造时区换算；
- 若选乙（Parquet），须提供等价 SQL 视图或等价 API（否则消费方无法 join）。

## 六、验收判据（要素⑥，可机检）

| # | 判据 | 机检形态 | 失败处置 |
|---|---|---|---|
| 1 | **覆盖完整**（无空洞） | 对每个 `(ts_code,freq)`：sidecar 段并集 ⊇ 分钟表该码该 freq 的 `[min(bar), max(bar)]`；空洞数 = 0 | 阻断投产 |
| 2 | **段不相交** | 同 `(ts_code,freq)` 内按 `anchor_start_time` 排序，后段 `anchor_start_time` ≥ 前段 `anchor_end_time` | 阻断投产 |
| 3 | **段内 `a_day` 恒定** | 每段内用 `stock_daily`/`etf_daily` 的 `a(t)` 校验：段内不同取值数 = 1 | 阻断投产 |
| 4 | **与实测比值对拍**（强判据） | 抽样 N 码 × M 日：`|(a_latest/a_write) ÷ (分钟末收/日线收) − 1| ≤ 0.1%`。依据：我方已实测 ratio **精确等于** `a_latest/a_write`（`000063.SZ 2026-06-15` 实测 1.012183 vs `latest/adj_D` 1.012182） | 超差即违约 |
| 5 | **回填行可识别** | 已知回填行 `000063.SZ 2026-07-28`（ratio=1.0）⇒ 其段 `a_write` 必须等于 `a_latest`，且**与同区间旧锚段（a_write=17.1055）分属不同段** | 未分出即违约（G1 的 authoritative 证据依赖此项） |
| 6 | **幂等** | 同批重跑两次，导出全表 sha256 逐位一致 | 不一致即违约 |
| 7 | **零孤儿** | sidecar 每条段至少能在分钟表找到 1 行 | 有孤儿须列出并说明 |
| 8 | **可追溯** | 抽样 10 段：`write_batch_id` 能在写入日志中找到对应批次 | 不可追溯即违约 |

**验收抽样口径（与 L2 步⑥ 对齐）**：N ≥ 40 码（无偏随机、seed 固定）、M ≥ 12 日/码；并**必须包含**
`000063.SZ`、`688808.SH`、`300109.SZ` 三个已知形态的码（同区间混批 / 因子跳变 / 中间锚）。

## 七、对 L2 的解除关系

- **本件落地 ⇒ L2 步③ 解除**（`a_write` 从「实测反推」升级为「直接读取」）；
- **G1 的 authoritative 证据路径同时可用**（回填批边界可由 sidecar 直接证明，无需 degraded 路径）；
- 步⑤（delivery 侧复算覆盖对账）与步①②④的解除条件不受本件影响（分别依 节点1 P2/A4、⑱ 快照追平、节点1 G1）。

## 八、边界（⑱ 实施时须守）

1. **不改既有表结构**（新表/新文件即可）⇒ 不触发 schema 指纹契约变更（铁律 24）；
2. **不改复权算式**（本件只登记，不改价）；
3. **不落 `ratio` / 不落价格 / 不落回放结果**（避免 a_latest 变动导致二次过期）；
4. 与 `QS_QFQ_FACTOR_EQUIVALENCE` 回退门**正交**：本件不影响该门禁语义。

## 九、本件自身可核对锚

- 物理路径：`D:\miniQMT策略实盘\QuantStudio\docs\handoff\qfq-anchor-sidecar-spec-20261112.md`
- 制定方：节点21（㉑，锚语义消费方）｜实施方：⑱｜阻塞解除项：L2 步③
- 上游依据：`D:\miniQMT策略实盘\trading-battle-back\docs\design-l2-minute-reanchor-20261111-v2.md`
- 判据方向定谳：`D:\miniQMT策略实盘\QuantStudio\knowledge\db\DB-2026-0043.md`（ratio 与 ff 互为倒数）
