# QuantStudio 客户反馈包（2026-09-23）四问题修复方案

- **日期**：2026-09-23（六步流水线 步骤2 送审件）
- **来源**：客户技术支持反馈包（2026-09-23，客户实测证据齐全）
- **客户环境**：Windows 11 / 16GB / Python 3.12.10 / 代码版本 `9aa462b` / duckdb 1.4.5；部署 `D:\QuantStudioNew`（2026-09-16 全新克隆）
- **本仓基线**：`D:\miniQMT策略实盘\QuantStudio`，HEAD `b50fe43`
- **用户裁定（2026-09-23）**：裁定① 方案过审（本审核即六步第 2 步）；裁定② T3 采用**方案 a（判据归一）**；裁定③ 分钟窗口默认 2→1 天同意，`minute_export_window_days` 可配置回退护栏保留，`async_mode` 默认关 + 灰度同意。

---

## 〇、步骤1 溯源归因结论（分层：现象 / 直接原因 / 代码缺陷 / 业务边界 / 隐性风险）

### 问题 1 — stock_minutes 两次被服务端拒单

| 分层 | 结论 | 证据 |
|---|---|---|
| 现象 | 2 天窗/89 批，死于批 44/87 与 70/89，`export_exceeds_time_budget` | 客户反馈 |
| 直接原因 | 服务端**单作业 60s 软时间预算**触顶；窗口已由安全公式收缩到 **2 天**（每日 ~1.98M 行 × 1.2 余量 → `5_000_000/(1.98M×1.2)=2`） | `mcp_adapter.py:799-806`；`docs/mcp-pull-performance-design.md:15,36` |
| 代码缺陷 | ① 窗口只由**行数**驱动，未纳入**时间预算**约束；② `row_limit` 只限返回行数、不缩短服务端扫描时间；③ 客户端未启用服务端已支持的 `async_mode` | `mcp_adapter.py:773-842`；`mcp/client.py:691-734`（无 async 参数） |
| 云端实测 | 同步：2 天窗 2,514,353 行 ✅ / 8 天窗 ✅ / 50 天窗 ✅（后两者被静默截到 5,000,000 行）；异步 `async_mode=true` → 立即返回 `{"job_id":…,"status":"running"}`，45s 后 `status=ready`，100 分片 SHA 完整 | 2026-09-23 实测 4 次 `create_export_job` |
| 业务边界 | **单作业行数硬上限 = 5,000,000**（请求 50,000,000 仍返回 5,000,000）⇒ `row_limit=5_000_000` 是必需护栏；超限时服务端「取最老」静默截断 | 实测 + `mcp_adapter.py:753-759` |
| 隐性风险 | 客户两次失败属**瞬时性预算触顶**（同参数 2 天窗本轮成功）→ 需重试/降窗双保险 | 实测对照 |

**对客户两问的直接答复**：单作业时间预算 = **60s 软超时**（超时即 `export_exceeds_time_budget`，hint 即「缩小窗口/过滤代码/降低 row_limit」）；客户端**没有**比「窗口天数」更细的分批参数——`row_limit` 仅截断返回行数、不省时间，唯一有效杠杆是缩小 `time_start/time_end`。

### 问题 2 — stock_float_share 三次 OOM

| 分层 | 结论 | 证据 |
|---|---|---|
| 现象 | `Unable to allocate 109 MiB`（shape 14,299,109）→ 1.28 GiB；进程峰值 ~6.3GB | 客户反馈 |
| 直接原因 | 该表未进 `_EXPORT_TABLES` → `fetch_table` 落 `_fetch_small_table` → `_fetch_all_pages` **全量累积 JSON dict 列表**再 `pd.DataFrame(rows)` | `mcp_adapter.py:482-486`、`499-528`、`607-612` |
| 代码缺陷 | `_STREAMING_TABLES` 仅 5 张行情大表，未含 `stock_float_share`；daemon 流式分支不命中 | `mcp_adapter.py:1238-1241`、`daemon.py:873-881` |
| 业务边界 | 表实际 14,321,322 行 / 5,919 码（云端 `stock_daily_basic` 全史 2010-01-04 起；任务 `start_date=2024-01-01`） | 云端 `get_coverage`；`collector_tasks.json:2691-2716` |
| 隐性风险 | 同构表 `stock_daily_valuation`（同源）**已**在 export+streaming 白名单 ⇒ 两表口径分裂；另 5 张表在 `_EXPORT_TABLES` 但不在 `_STREAMING_TABLES` | `mcp_adapter.py:172-192` vs `1238-1241` |

### 问题 3 — etf_minutes 质量门禁判负（本轮新定谳）

| 分层 | 结论 | 证据 |
|---|---|---|
| 现象 | 62.2M 行中 932,382 行被 UnitCheck 拦（1.4985% > 1%）→ 整任务判负；隔离区 500,809 行顶 500K 硬上限 | 客户反馈 |
| 直接原因 | `validator.py:210-229` UnitCheck 判据 `amount/(close×volume) ∈ [0.5,2.0]`，但 `close` 是**客户端已还原的 raw**（`raw = qfq × adj_latest_global / adj_i`），而 `amount`/`volume` **不参与复权** | `validator.py:216-229`；`mcp_adapter.py:1730`、`1805-1808`、`1850-1852` |
| 机理（代数等价） | `amount/(raw_close×vol) ≡ adj_latest_global / adj_i`（云端 `qfq_close = amount/vol` 已实测自洽，比值恒 1.0）⇒ 当 `adj_latest/adj_i ∉ [0.5,2.0]` 时**系统性误拒** | 本轮实测 |
| 代码缺陷 | UnitCheck 的不变量前提（amount/volume/close 同为 raw 或同为 adj）在 qfq→raw 还原后被破坏；`docs/strategy_toolbox.md:105` 明确「必须用 raw close、不得用 close_front」，但**未考虑 amount/vol 不复权** | `validator.py:216-229`、`docs/strategy_toolbox.md:105` |
| 实测证据（云端 5 时段 × 30 万行） | 2025-01-06 窗 **3.5143%** / 2025-07-07 窗 **3.0717%** / 2026-01-05 窗 **2.8745%** / 2026-08-03 窗 **0.2898%** / 2026-09-21 窗 **0.0000%**；与客户 2026 全年 **1.4985%** 同量级 | `worktable/_probe_etf_multi.py` |
| 触发集合 | ETF 因子库 **132 只**历史因子未回填（=1.0）vs 最新 3.0~6.0 → 覆盖 343,135 行 | `qfq_aux.db` fund_adj 聚合 |
| 锚源核对 | 客户端 `fund_adj` 与云端 `etf_adj_factor` 每码最新因子 **2165/2165 完全一致** ⇒ **非锚源分歧**，是判据口径缺陷 | 本轮交叉核对 |
| 业务边界 | 「云端 ETF 因子历史未回填」属数据侧边界（客户不可控）；但**框架侧不应把它表现为质量判负** | — |
| 隐性风险 | 同一缺陷同时解释客户「顺带观察」：13 只股票 / 159220 的「复权锚漂移 >0.5%」（`quality_audit.py:558 _ANCHOR_DRIFT_FAIL=0.005`）——**不是独立问题**；etf_daily/stock_daily/stock_minutes 同源同患（`docs/handover/test-evidence/phase6-quarantine-disposition.md:9`：etf_minutes 373,346 + stock_minutes 130,827 + etf_daily 1,453） | `quality_audit.py:558` |

**对客户三问的直接答复**：① 09-22「测试码污染源头拦截」（commit `fa8e64f`）**不覆盖**本问题——该批只改 `code_contract.py`（形式契约）/ `_get_adj_latest_global`（冷启动预过滤）/ `qfq_event_discovery._observe_factors`（脏行剥离），**未触及 `validator.py`**，新代码下重拉**仍会判负**；② 隔离区 500,809 行**不是数据丢失**，是误拒正常累积，修好判据后应先归档腾配额；③ 调高 `max_rows` 只是掩盖。

### 问题 4 — pyarrow 未声明

| 分层 | 结论 | 证据 |
|---|---|---|
| 现象 | 新机 `pip install -e ".[all]"` 后无 pyarrow；`streaming …/daily 第一遍因子同步失败（注入 0 行）` | 客户反馈 |
| 直接原因 | `pyproject.toml` dependencies（:15-26）与 `[all]` extra（:41-49）**均无 pyarrow**；而代码多处依赖它 | `pyproject.toml` 全文 |
| 代码缺陷 | ① 打包声明缺失（`pandas.read_parquet` 需 pyarrow/fastparquet）；② `_parquet_has_column`（`mcp_adapter.py:1429-1437`）`except Exception: return False` **静默吞掉 ImportError**，把「依赖缺失」伪装成「列不存在」 | `mcp_adapter.py:1431-1437`；实测缺 pyarrow 时 `pd.read_parquet` → `ImportError: Unable to find a usable engine` |
| 隐性风险 | 依赖缺失被降级成误导性报错，排障成本高；同类静默 `except` 需一并排查 | — |

---

## 一、背景与动机

客户在 2026-09-16 全新克隆部署上按手册跑 MCP 全量拉取，4 个问题**全部直接阻断交付**：

1. `stock_minutes` 全量拉取两次失败，表无法拉通（最紧急）；
2. `stock_float_share` 三次 OOM，14.3M 行表无法入库；
3. `etf_minutes` 质量门禁判负，62.2M 行任务被判失败，隔离区顶格；
4. 新机部署必踩 pyarrow 缺失坑（客户已自行绕过，但每个新客户都会重演）。

问题 3 经本轮取证**推翻了既往「阈值对分钟表偏严」的定性**：根因是 UnitCheck 判据与 qfq→raw 还原口径冲突导致的**系统性误拒**，而非数据质量问题。

**不修的影响**：客户无法完成首次全量建库 → 回测/策略交付全链路阻塞；隔离区配额被误拒数据占满 → **真实异常样本反而写不进去，防护能力实质失效**；「复权锚漂移」告警长期误报。

---

## 二、范围与边界

### 改动文件（精确清单）

| # | 文件 | 改动性质 |
|---|---|---|
| 1 | `quantstudio/pipeline/sources/mcp_adapter.py` | ①分钟表分批窗口纳入时间预算 + 可配置；②`_EXPORT_TABLES` / `_STREAMING_TABLES` 补 `stock_float_share`；③`_parquet_has_column` 依赖缺失显式失败；④还原时随行附复权因子比（供 UnitCheck 归一） |
| 2 | `quantstudio/pipeline/mcp/client.py` | `create_export_job` / `export_dataset` 增 `async_mode` 可选透传（默认关）；`get_manifest` running 分支可选轮询 |
| 3 | `quantstudio/pipeline/daemon.py` | 流式分支表集合改由适配器常量驱动（消除硬编码耦合，零行为变更） |
| 4 | `quantstudio/pipeline/validator.py` | UnitCheck 判据归一（方案 a） |
| 5 | `pyproject.toml` | dependencies 与 `[all]` 补 `pyarrow` |
| 6-8 | `tests/test_mcp_fetch_routing.py`、`test_mcp_streaming.py`、`test_validator_behavior.py` | 新增回归钉 |
| 9 | `docs/mcp-pull-performance-design.md`、`docs/strategy_toolbox.md`、`README.md` | 六步流水线同步 |
| 10 | 新增 `docs/evidence/*-acceptance-*.md` | 验收证据件 |

### 不改动范围（硬边界）

- 不改复权公式、不改 `adj_latest_global` 语义、不改 PIT/日期边界、不改列名/字段/单位契约；
- 不改 `_EXPORT_ROW_LIMIT_BIG = 5_000_000`（服务端硬上限）；
- **不调低质量门禁阈值**（`max_reject_rate_mcp = 1%` 保持）——**修判据而非放宽门禁**；
- 不删除、不清空隔离区数据（只提供归档操作指引）；
- 不动 `stock_float_share` 的 `column_map` / `copy_columns` / schema；不动引擎、策略、回测、注入 API。

### 兼容边界

- `stock_float_share` 换路径后：**列集、行数、dtype、upsert 主键、metadata 契约不变**（`fetch_mode` 由 `fetch_page` 变 `export_streaming`，属追溯字段，验收显式声明）；
- 分钟窗口默认值变更会改变批次数（87→~174），**产物终态必须逐位一致**；
- `async_mode` 默认关闭 → 现有路径零影响；
- UnitCheck 归一仅在行内含复权因子比列时启用，**无该列时逐行等同旧行为**。

---

## 三、任务拆解

### T1 分钟表分批窗口：纳入时间预算 + 可配置 + 异步可选
- `_export_batches` 分钟安全窗口除行数约束外，增加**每作业目标行数**约束（按 60s 预算 × 保守速率反算），默认由 2 天收紧到 **1 天**；新增配置键 `minute_export_window_days` / `minute_export_target_rows`（`sources_config.json` 的 `mcp` 段，缺省即用新默认）。
- `client.py`：`create_export_job` / `export_dataset` 增 `async_mode` 透传；`get_manifest` 的 `status=running` 分支改为可选轮询等待。
- 适配器侧新增 `export_async` 开关（默认 `false`，灰度）。
- **防回归**：窗口公式在行数/时间双约束下的取值断言 + 既有 `_export_batches` 边界测试回归钉。

### T2 stock_float_share 改走流式导出路径
- `_EXPORT_TABLES` 增 `("stock_float_share","daily")`；`_STREAMING_TABLES` 增 `"stock_float_share"`；`_EXPORT_ROW_ESTIMATE` 增 `"stock_float_share": 14_300_000`。
- `daemon.py` 表集合硬编码耦合改为引用适配器常量（纯整理，零行为变更）。
- **防回归**：三断言（在 `_EXPORT_TABLES` ∧ 在 `_STREAMING_TABLES` ∧ 不在 `_QFQ_ADJFACTOR_TABLES`）+ 流式 vs 直连 `assert_frame_equal` 等价测试。

### T3 UnitCheck 判据归一（方案 a，用户裁定）
- **问题本质**：UnitCheck 要求 `amount`/`volume`/`close` 同一价格基准；`amount`/`volume` 不复权、`close` 已还原 raw。
- **落点**：适配器在 `_restore_to_raw` 中随行附复权因子比列（`adj_i / adj_latest`，缺失行填 1.0，与价格还原同一 `valid` 掩码）；`validator.py` UnitCheck 在**行内含该列**时按 `amount/(close×volume) × 因子比` 判定，无该列时**逐行等同旧行为**。
- **能力保留**：真阳性拦截能力不减（xtquant 手单位 ×100 仍被拦）；不使用 `close_front` 替代（已由既往定谳否决，`docs/strategy_toolbox.md:105`）。
- **防回归三件**：① 真阳性反测（xtquant 手单位数据仍被 UnitCheck 拦）；② 客户 932,382 行样本前后对照；③ `tests/test_validator_behavior.py` / `test_xtquant_volume_unit.py` 全绿。

### T4 pyarrow 声明 + 静默吞异常消除
- `pyproject.toml` 增 `"pyarrow>=14"`（dependencies + `[all]` 同步）。
- `_parquet_has_column` 区分 `ImportError`（显式抛出 + 安装指引）与「列不存在」（返回 False）；`client.py` `_HAS_PARQUET` 兜底分支同样显式化。
- **防回归**：monkeypatch `__import__` 断言「缺 pyarrow 给明确错误而非 `注入 0 行`」。

### T5 客户侧处置指引（不涉代码）
- 隔离区 500,809 行：先 `archive_expired`（转 `archived`，**不删除**）腾配额；`max_rows` 暂不上调（修好 T3 后写入量自然回落）。
- 13 只股票 / 159220 锚漂移：判定为 T3 同源，**T3 修复后重拉复验**，不单独立项。

---

## 四、风险描述

| # | 风险 | 等级 | 规避手段 |
|---|---|---|---|
| R1 | 分钟窗口 2→1 天，批次数翻倍（87→~174），全量拉取总耗时增加 | 中 | 接受总时长增加换取「不再中途判负」；`minute_export_window_days` 可配置回退 |
| R2 | `async_mode` 启用后 `get_manifest` 轮询语义变化 | 中 | 默认**关闭**；开启时保留超时上限与失败抛错；先灰度再放默认 |
| R3 | T3 改判据可能**放松**对真实异常行的拦截 | **高** | 采用方案 a（归一而非跳过）；守护 `test_xtquant_volume_unit.py` 反测「手单位数据仍被拦」；对客户 932,382 行做前后对照，确认真阳性未减少 |
| R4 | `stock_float_share` 换路径后 metadata `fetch_mode` 变化 | 中 | 验收中 `grep` 全仓 `fetch_mode` 消费点，确认仅追溯用途；补断言 |
| R5 | 换路径后行数/列序变化 → upsert 主键或 DDL 冲突 | 高 | 影子库协议：旧路径 vs 新路径产出**表指纹逐位一致**；`config_lint` 主键校验通过 |
| R6 | 上游服务端对 `async_mode` 语义变更 | 低 | 开关化 + 失败回退同步路径 |
| R7 | 大表全量拉取耗时以「自然日」计，验收窗口不足 | 中 | 验收用**分段抽样**（2025-01 / 2026-01 / 2026-09 三窗）而非全量 |
| R8 | 共享核心文件 `mcp_adapter.py` 与客户运维 1 的错误一 T3（outbox）冲突 | 中 | 用户已裁定其暂缓至本批落地后；本线先行；提交用精确文件清单，发现他人未提交改动则叠加事实入 commit message |

---

## 五、验收要点

### 功能验收
1. `stock_minutes` 2026-01-01→09-22 全量拉取**一次跑通零拒单**（含触顶重试降窗）；
2. `stock_float_share` 全量拉取完成，**进程峰值内存 < 1.5GB**（对照当前 ~6.3GB），无 `Unable to allocate`；
3. `etf_minutes` 2026 全年窗口质量门禁**判正**，UnitCheck 拒绝率由 1.4985% 降至 **< 0.1%**；
4. 新机 `pip install -e ".[all]"` 后 `import pyarrow` 成功，且缺依赖时报错**明确可读**。

### 场景验收
- 分钟表 1min/5min/15min/30min/60min 五频段分批窗口公式正确；
- ETF/股票/指数三类复权场景：`fq`、`include`、`count`、`fields` 参数组合行为不变；
- `stock_float_share` 与 `stock_daily_valuation` 两表路径一致；
- 隔离区写入/归档路径正常（含归档后配额释放）。

### 边界验收
- 复权因子缺失（fail-fast）路径不变；
- 非标准测试码过滤路径不变；
- `row_limit` 截断护栏仍生效（5M 上限）；
- 还原表无复权因子比列时 UnitCheck 行为与旧版逐位一致。

### 回归验收（铁律要求）
- 影子库协议：`stock_float_share` / `etf_minutes` / `stock_minutes` 三表**改动前后表指纹逐位一致**；
- 代表性策略（6 横验证成员取一）回测信号/成交/净值**逐位一致**；
- 套件全绿：`test_mcp_fetch_routing` / `test_mcp_streaming` / `test_validator_behavior` / `test_xtquant_volume_unit` / `test_pit_filter` / `test_derive_market_value` / `test_mcp_etf_latest_anchor` / `test_full_quality_audit_repair`。

### 用户追加验收项（2026-09-23 裁定）
- **隔离区防护能力复验**：500,809 行归档腾配额后，真实异常样本写入路径复验通过（防护能力恢复的证据）。

---

## 六、质量判据

| 判据 | 门槛 | 证据形式 |
|---|---|---|
| 问题 1 彻底修复 | 全量拉取零 `export_exceeds_time_budget`；窗口公式测试通过 | 拉取日志 + 单测 |
| 问题 2 彻底修复 | 峰值内存 < 1.5GB；行数与旧路径一致 | 内存实测 + 表指纹对比 |
| 问题 3 彻底修复 | UnitCheck 拒绝率 < 0.1% 且**真阳性拦截能力未减** | 前后对照表 + 反测用例 |
| 问题 4 彻底修复 | 新机可复现安装即用；缺依赖报错明确 | 全新 venv 实测 |
| 无回归 | 既有功能零衰减（套件全绿 + 黄金结果逐位一致） | pytest 输出 + 影子库 diff |
| 防回归 | 每问题 ≥1 条结构性防线（测试/校验器/契约断言） | 新增测试清单 |
| 工程标准 | 改动面 = 本清单；无顺手重构；每项独立 commit 可单项回退 | `git diff --stat` + 精确文件清单 |

---

## 七、回退条件

- 任一验收项不达标 → **立即回退该项**，不议「差异很小」；
- 每任务独立 commit；实施前 `git stash create -u` + `git stash store` 建立零副作用回退点；
- **T3 若真阳性拦截能力下降 → 无条件回退并重新归因**；
- 推送前保留用户确认闸门（六步流水线步骤 5）。

---

## 八、实施记录（实施期回填）

- 回退点：`9c2e0f7a857b32c9f4e9bcf328b99c7312d22a6c`（`baseline-four-issues-20260923`，已 `git stash store` 持久化）
- 共享核心文件叠加事实：实施前 `mcp_adapter.py` / `daemon.py` / `validator.py` / `client.py` 四文件 `git status --porcelain` 均为空（无他人未提交改动）
- 客户运维 1 的错误一 T3（outbox，同文件 `mcp_adapter.py`）经用户裁定暂缓至本批落地后，本线先行

### 实施与提交

| 任务 | commit | 状态 |
|---|---|---|
| T1 分钟表窗口纳入时间预算 + async_mode 透传 | `478ed0e` | 已提交 |
| T2 stock_float_share 改走 export 分片 + 流式 | `6ceeff9` | 已提交 |
| T3 UnitCheck 判据归一（方案 a） | `996ced2` | **已回退**（见 §九） |
| T4 pyarrow 声明 + 消除依赖缺失静默降级 | `151e984` | 已提交 |

### 并行会话叠加说明

实施期间同工作区并行会话（GUI/运维线）于 T1 之后追加了 7 个提交
（`7ca1d6a` / `22d7c5b` / `ea4bca1` / `aef6b9d` / `28eaf41` / `b429f98` / `5ee3149`）。
本线提交均为精确文件清单 `git add`，未卷入他人改动；T1/T2/T4 改动在 HEAD 中逐项复核存活。

---

## 九、T3 回退记录与问题 3 根因修正（2026-09-23 步骤 4 反证）

### 回退事实

T3（`996ced2`）按方案 a 落地「UnitCheck 乘复权因子归一」后，步骤 4 验收
**反证不成立**，已按回退条件整体回退（`validator.py` / `mcp_adapter.py` 回到
`6ceeff9` 状态），并保留回归钉 `tests/test_validator_behavior.py`
（`test_unitchk_rejection_on_cloud_bar_is_true_positive_not_anchor_artifact` /
`test_unitchk_must_not_be_normalized_by_adjustment_factor`）。

### 反证证据（云端只读实测，三项独立取样）

| 判据 | 2025-06 窗 | 2026-01 窗 | 2026-09 窗 |
|---|---|---|---|
| `close/(amount/vol) ≈ 1` 行占比（close 已是可成交价） | **84.40%** | **92.03%** | **99.94%** |
| 旧行为拒绝率 | 3.1738% | 3.3273% | 0.0000% |
| 归一后拒绝率 | 4.9760% | 3.6890% | 0.0000% |
| A 类：归一消除的拒绝 | 1,276 | 2,151 | 0 |
| D 类：归一**新增**的拒绝 | 3,874 | 2,889 | 0 |

- 单码日内实证（`159388.SZ` 2025-06-03）：`close / (amount/vol)` 恒为
  `0.3998 = adj_i/adj_latest(1/2.5011)` ⇒ close 是**已复权到最新锚的可成交价**，
  不是需要再乘倍数的 qfq；
- 交叉表结论：A（消除）3,427 行 < D（新增）6,763 行 ⇒ **净增拒**，修复方向证伪；
- 其他证据：D 类样例（`159381` / `159388` / `159596` / `159663` / `512480` 等，
  共 27~49 只）的 `r_raw ≈ 1` 而 `f = 2~4` ⇒ 归一必然把正确行推离 [0.5,2.0]；
- 数据画像：`amount/(close×vol)` 的 99% 分位在 2025-06 为 **9.0046**、2026-01 为
  **9.0028**、2026-09 为 **1.0021** ⇒ 2026-01 前某些码存在 `amount ≈ 9×close×vol`
  的真实数据异常（倍率不可由「手/股 ×100」「千元/元 ×1000」解释）。

### 问题 3 根因修正（替代原「判据口径缺陷」定性）

**修正定性**：`UnitCheck` 在 etf_minutes 上的拦截**绝大部分是真阳性**——
云端 `close` 与 `amount/vol` 同基准（可成交价口径），凡该比值越界的行，
其**数据自身或因子自身内部矛盾**（如 `amount ≈ 9×close×vol`，或码内因子与价格不一致）。
**不是** validator 判据与复权还原口径冲突导致的系统性误拒。

**真正的框架层问题**（与本批 T1/T2/T4 同类，可直接修复）：
门禁阈值 `max_reject_rate_mcp = 1%` 是**日线口径校准**，对分钟表偏严——
分钟表真实异常率天然更高（3.2%~5.0%），触发率超限即整任务判负并停止推进水位，
导致「真实异常被正确拦截但任务被整体判负」。既有文档已多次记录该定性
（`docs/handover/pre-handover-test-report.md:50`、`docs/pipeline-tech-debt.md:93`
「UnitCheck 误判指数（硬编码跳过）」），本轮实测确认成立。

**待用户裁定的处置方向**（三选一，均不涉及放宽数据质量判定）：
1. **门禁分表型校准**：分钟表使用独立阈值（如 6%），日线表保持 1%——不影响任何行的
   判拒结果，只影响「批次是否判负」；
2. **判负语义调整为「拦行不拦批」**：拒绝率超限时照常入库合格行、推进水位，
   仅记录告警并登记异常清单（需评估对下游口径的影响，属行为变更）；
3. **云端数据侧治理**：定位 `amount ≈ 9×close×vol` 的码与日期区间并要求云端修复
   （属数据侧、跨团队，非本仓可闭环）。

**隔离区 500,809 行处置**：仍建议先 `archive_expired`（转 `archived`，不删除）
腾配额——这些是真阳性异常样本，保留归档供数据侧治理使用；**不建议**调高
`max_rows` 掩盖。
