# R27 取证回执 · 因子真实消费路径映射 + census batch02 段清单

> 节点：㉑ 复权管线修复专属会话 · 2026-11-13 11:3x
> R27 依据项：① 9 表统计 ≠ 段清单 ≠ L2 输入；⑤ 股票因子在列、ETF 独表**分别映射真实消费路径**；⑥ batch02 按只读/可续/资源前置推进；⑦ 云端证书**不默认 `-k` 绕过**

---

## 1. 资源前置（R27 第⑥点）

| 项 | 实测（11:32） | 判定 |
|---|---|---|
| D: 空闲 | **141.4 GB / 1073.7 GB** | 充裕 |
| 物理内存空闲 | **5.0 GB / 34.2 GB（load 85%）** | **偏紧** |

⇒ 处置：batch02 采用**单次聚合查询**（`SAMPLE BY 1d`，一条 SQL 出全部段），**不做逐段 N 次查询**；实测 `stock_minutes` 6.13 s、`etf_minutes` 3.71 s 完成。内存压力下仍安全。

---

## 2. 因子真实消费路径映射（R27 第⑤点）

### 2.1 落点实测：`data/qfq_aux.db`（SQLite 辅助库）

`size = 13,415.0 MB`，`mtime = 2026-11-13 10:44:39`，共 11 张表。

| 表 | 资产 | 行数 | 码数 | 时间范围 |
|---|---|---|---|---|
| `adj_factor` | **股票** | **32,874,481** | 5,857 | 2000-01-21 00:00 ~ 2026-11-12 15:30 |
| `fund_adj` | **ETF** | **12,800,548** | 2,331 | 2010-01-04 00:00 ~ 2026-11-12 15:00 |
| `adj_factor_snapshot` | 快照 | 5,780 | 5,780 | — |

其余表：`qfq_d1_isolation_log`、`qfq_deep_audit_cursor`、`qfq_deep_audit_item`、`qfq_factor_observation`、`qfq_factor_revision_alert`、`qfq_jump_audit`、`qfq_obs_incremental_cursor`、`sqlite_sequence`。

### 2.2 读侧/写侧代码证据（谁读谁）

| 位置 | 角色 | 证据（成文） |
|---|---|---|
| `quantstudio/pipeline/qfq_snapshot.py:56` | 读 | `aux_table = "fund_adj" if table.startswith("etf_") else "adj_factor"` |
| `quantstudio/pipeline/qfq_invariant.py:117` | 读 | `return "fund_adj" if table in ETF_TABLES else "adj_factor"` |
| `quantstudio/pipeline/qfq_maintenance.py:141-143` | 写 | 「股票写 adj_factor；ETF 写 fund_adj。两张表彻底隔离，不得再把 ETF 因子写入 adj_factor」 |
| `quantstudio/pipeline/qfq_event_discovery.py:60-61` | 观察 | `ADJ_FACTOR_TABLE = "adj_factor"`；ETF 读 `fund_adj` |
| `quantstudio/pipeline/qfq_reanchor_schema.py:602-610` | DDL | `adj_factor` 与 `fund_adj` **同结构两表**（code/time/adj_factor），资产隔离 |
| `quantstudio/pipeline/daemon.py:1330-1334` | 写前 | **主动 drop** `raw_df["adj_factor"]`，避免与 `adj_factor_df` merge 产生 `adj_factor_x/_y` 冲突 |

### 2.3 结论（并纠正 v3.6 一处错误）

- 股票与 ETF 因子**不是同一张表的过滤条件**，而是**两张独立表 + 两套写路径 + 一处分流函数**。
- **`QuestDB.stock_daily.adj_factor` 列不是消费路径**：它是 MCP 随行返回的输入残留，写前即被 `daemon.py` drop。v3.6/v3.7-1 R-4 已按此纠正。
- `QuestDB.etf_adj_factor`（1,701,538 行）与 `qfq_aux.db.fund_adj`（12,800,548 行）是**两份不同落点**，不可互换引用。

---

## 3. 设计级缺口：锚表落点与因子落点跨库错配（呈调度裁定）

| 侧 | 存储 | 事务域 |
|---|---|---|
| sidecar 锚表（原设计） | **QuestDB** `qfq_anchor_sidecar` | QuestDB WAL / applied 语义 |
| 因子真实落点（实测） | **SQLite** `data/qfq_aux.db` 的 `adj_factor`/`fund_adj` | SQLite 事务 / `synchronous` 语义 |

⇒ **两侧不同库、不同事务域**。v3.6 的 WAL 可见性结论（commit ≠ 可见、commit ≠ 持久）**只对"锚表与消费方同读 QuestDB"这一形态成立**；若消费方读 SQLite，须另核 SQLite 侧语义，**本文不预设**。

**待裁定（本轮不实施、不预设）**：锚表落点 (甲) QuestDB 同库 / (乙) 随因子入 `qfq_aux.db` 同库 / (丙) 双写。

---

## 4. census batch02 段清单（R27 第①⑥点）

**段规则**：单日区间 `[day 00:00, day+1 00:00)` 左闭右开；`SAMPLE BY 1d` 取段行数。
**时间列勘误**：分钟表指定时间列为 **`trade_time`**（非 `ts`；按 `ts` 查询返回 `400 Invalid column: ts`），已改动态探测。

| 表 | 时间列 | 段数 | 段行数之和 | batch01 表 count | 对账 |
|---|---|---|---|---|---|
| `stock_minutes` | `trade_time` | **614** | **581,303,979** | 581,303,979 | **逐位一致** |
| `etf_minutes` | `trade_time` | **450** | **149,814,519** | 149,814,519 | **逐位一致** |

**段边界抽样验证（每表首/中/末 3 段，`rows_match` 均 True）**：

| 表 | 段 | min | max | 行数对账 |
|---|---|---|---|---|
| stock_minutes | 2024-05-06 | 09:30 | 15:00 | ✓ |
| stock_minutes | 2025-08-06 | 09:30 | 15:30 | ✓ |
| stock_minutes | 2026-11-12 | 09:30 | 15:30 | ✓ |
| etf_minutes | 2025-01-02 | 09:30 | 15:00 | ✓ |
| etf_minutes | 2025-12-08 | 09:30 | 15:00 | ✓ |
| etf_minutes | 2026-11-12 | 09:30 | 15:00 | ✓ |

**明确未提供（不冒充）**：每段首末键（ordered by sym 首末）、可回放判定、L2 执行可用性。
**可续**：manifest 按 `表+day` 幂等，中断重跑同批不重复记。

产出：`docs/evidence/census-manifest-lia-batch02-20261113.json`。

---

## 5. 云端取证口径（R27 第⑦点）

- **不使用** `curl -k` / `--insecure`。
- 依据＝仓库内**已批准信任配置**：`config/profiles/mcp_only/sources_config.json` → `"tls_verify": false`（MCP 通道既有配置）。
- 如实标注：该配置语义为**通道不校验服务端证书**，属**既存安全事实**；本轮**未修改**该配置，也**未据此上升为「云端已连通」**。
- 本轮**仍未完成**云端端点探活（请求未发出）；环境侧 `CURL_CA_BUNDLE`、`SSL_CERT_FILE` 均为空，`curl.exe` 在位（`C:\Windows\system32\curl.exe`）。

---

## 6. 本轮零变更声明

- **生产零 DDL / 零强杀 / 零掉电实验**；未停/未启/未接管任何 daemon。
- **v2 五条写入路径未改一行**；本轮全部产物为文档 / manifest / 只读探针。
- `qfq_aux.db` 以 `mode=ro` 只读打开，未写。
- 未改动 `sources_config.json` 的 `tls_verify`。
