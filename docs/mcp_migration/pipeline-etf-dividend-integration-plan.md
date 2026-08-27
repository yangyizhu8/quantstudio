# QuantStudio 侧管线接入方案：etf_dividend + stock_dividend_full（供 zcode 审核）

> 前置：Trae 已完成本地 QuestDB 回填（etf_dividend / stock_dividend_full）并同步推送云端（见 `questdb-etf-dividend-supplement-task.md`）。
> 本方案：QuantStudio 管线接入（拉取→标准化→入库→质检），**不涉及回测引擎改动**。
> 审核通过后执行；完成后**再启动** ETF/股票回测除权补正任务（引擎侧）。
> 性质：数据适配层改动（mcp_adapter/writers/配置），适用 AGENTS.md 铁律（本地修复 → 用户确认 → GitHub 双仓库同步 + README/docs）。

---

## 〇、总体结论

管线为配置驱动，核心调度/水位/质检主链路**无需修改**。改动集中在：**2 张表的注册（mcp_adapter 映射 + writers DDL + config_lint + source_capabilities + GUI 清单）+ 2 份配置（collector_tasks / alignment_rules，各含 mcp_only 副本）+ 1 处 stock_dividend 源切换 + 1 处通用小扩展（日期列过滤）+ 1 次水位重置**。

**背景确认（2026-08，用户口径 + 实测）**：现 DuckDB `stock_dividend` 2181 行来自**历史多源版本遗留**（westock `ws_exdiv` 链路），实测含**单位缺陷**（601628 每股 0.618 元被存为 61.8，×100）；旧源（akshare/baostock/tushare 直连/westock）已废弃不维护、不做兼容考虑。切换 `stock_dividend_full`（tushare 每股口径）后旧数据被覆盖、单位修正为正确每股值——**这是预期结果而非回归**。

**关键设计决策（需 zcode 确认）**：

- **D1：stock_dividend canonical 源切换**：`_CANONICAL_TO_QUESTDB["stock_dividend"]` 由 `"ws_exdiv"` 改为 `"stock_dividend_full"`（tushare 全字段）。不新增 stock_dividend_full 独立 canonical 表（DuckDB `stock_dividend` schema 已含全部字段）。ws_exdiv 云端表保留不删，QuantStudio 侧不再消费。
- **D2：etf_dividend 作为独立 canonical 表**（不复用 stock_dividend）：回测引擎的 ETF 现金入账在后续回测任务中读取该表（stock_dividend 保持纯股票，`_apply_corporate_actions` 语义不变；ETF 折算/分红检测逻辑在引擎任务中统一）。
- **D3：adapter 侧归一化**（替代现有 ws_exdiv div_proc 派生 hack）：stock_dividend 拉取后统一字段名（cash_div_tax→cash_div_before_tax 等）+ 过滤 + 去重，保证 `_inject_dividend`（§7.2-A QFQ 注入）与 aligner 看到的都是 canonical 列名。

**两方案分工（zcode 审核 R-2 确认，回写）**：

| 问题 | 解法 | 依赖 |
|---|---|---|
| ETF/股票分红数据缺失（stock_dividend 无 ETF、仅 2026 现金） | **管线方案**：stock_dividend_full（全历史含送转）+ etf_dividend（ETF 每份派息） | 本方案 |
| ETF 份额拆分补正缺失（159995 2026-07-07 10送10，fund_div 无解——fund_div 只管分红不管拆分） | **引擎方案**：preClose/factor 反推送股（`etf-split-factor-derived-fix-plan.md`） | 数据源仅需 etf_adj_factor/preClose（本地已存在） |
| ETF 纯现金分红入账 | 引擎读 etf_dividend（本方案产出）+ 引擎消费逻辑（后续回测任务） | 两方案联合 |

> **并行推进（R-5）**：引擎方案（preClose 反推）**不依赖 etf_dividend 表**，可先行实施；管线方案落地后做联合验收（ETF 动量净值对齐）。两份方案文档互相引用，边界清晰可审计。

---

## 一、代码改动（4 个文件）

### 1. `quantstudio/pipeline/sources/mcp_adapter.py`

**1a. `_MCP_SUPPORTED`（line 54 字典）新增：**
```python
("etf_dividend", "daily"): "etf_dividend",
```
（`stock_dividend_full` 不需要新增——它只是 canonical `stock_dividend` 的新云端源表。）

**1b. `_CANONICAL_TO_QUESTDB`（line 195 字典）：**
```python
"stock_dividend": "stock_dividend_full",   # 原 "ws_exdiv" → 切换（D1）
"etf_dividend": "etf_dividend",            # 新增
```

**1c. `_fetch_small_table` 中 stock_dividend 归一化块（替换 line 578-583 的 ws_exdiv div_proc hack）：**
```python
if table == "stock_dividend":
    # 新源 stock_dividend_full（tushare dividend 字段）。
    # 【R-3】以下 ws_exdiv 兼容分支（dividend_plan 派生 div_proc）为防御性代码：
    # 源切换后预期永不命中（新源自带 div_proc），保留仅为回退期兼容；单测断言其不命中。
    if "cash_div_tax" in df.columns and "cash_div_before_tax" not in df.columns:
        df = df.rename(columns={"cash_div_tax": "cash_div_before_tax"})
    if "cash_div" in df.columns and "cash_div_after_tax" not in df.columns:
        df = df.rename(columns={"cash_div": "cash_div_after_tax"})
    if "div_proc" not in df.columns and "dividend_plan" in df.columns:
        df["div_proc"] = df["dividend_plan"].apply(
            lambda v: "实施" if (v is not None and str(v).strip() != "") else None)
    # 过滤：仅保留 实施 + ex_date 非空（与 tushare adapter _fetch_dividend 同语义）
    if "div_proc" in df.columns:
        df = df[df["div_proc"].astype(str) == "实施"]
    if "ex_date" in df.columns:
        df = df[df["ex_date"].notna()].reset_index(drop=True)
    # 去重：同 (ts_code, ex_date) 保留 div_cash_before_tax 非空行（fund_div 重复行防御）
    if "ts_code" in df.columns and "ex_date" in df.columns:
        df = df.sort_values("cash_div_before_tax", na_position="last") \
               .drop_duplicates(subset=["ts_code", "ex_date"], keep="first") \
               .reset_index(drop=True)
```

**1d. `_fetch_small_table` 日期列候选（line 556）加 `"ex_date"`：**
```python
date_col = next((c for c in ("date", "trade_date", "cal_date", "ex_date") if c in df.columns), None)
```
并在 `_norm_date` 支持毫秒时间戳（防御：若云端 ex_date 返回 epoch-ms 数值）：
```python
def _norm_date(v):
    s = str(v).strip()
    if s.isdigit() and len(s) == 13:          # epoch 毫秒
        s = datetime.datetime.fromtimestamp(int(s)/1000).strftime("%Y%m%d")
        return s
    if len(s) >= 10 and s[4] == "-":
        s = s[:10].replace("-", "")
    return s[:8]
```
> 注：1c/1d 执行前先用 `scripts/mcp_probe_tables.py` 实测云端 stock_dividend_full 真实列名/日期格式，若列名已是 canonical 则归一化块自动 no-op（兼容）。

**1e. `_inject_dividend`（line 1839）**：不改。1c 归一化后 df 已含 `cash_div_before_tax` 等 canonical 列，`needed` 集直接命中。

### 2. `quantstudio/pipeline/writers.py`

**2a. 表 DDL 字典新增（对齐 stock_dividend 条目，line 298 附近）：**
```sql
"etf_dividend": """
    CREATE TABLE IF NOT EXISTS etf_dividend (
        code VARCHAR, ex_date BIGINT, record_date BIGINT,
        ann_date BIGINT, imp_anndate BIGINT, base_date BIGINT,
        div_proc VARCHAR, pay_date BIGINT, earpay_date BIGINT,
        net_ex_date BIGINT, div_cash DOUBLE, base_unit DOUBLE,
        ear_distr DOUBLE, ear_amount DOUBLE, account_date BIGINT,
        base_year VARCHAR, update_time VARCHAR,
        PRIMARY KEY(code, ex_date)
    )""",
```
> 日期统一 BIGINT 毫秒（aligner time_to_ms）；`base_year` VARCHAR（如 "2026"）；空日期落 NULL（**禁止 1970-01-01**，见 §四验证 4）。

**2b. PK 注册（两处字典，line 542 与 line 624）：**
```python
"etf_dividend": ["code", "ex_date"],   # pk_for_dedup
"etf_dividend": "(code, ex_date)",     # pk_cols
```

### 3. `quantstudio/pipeline/config_lint.py`

`_WRITER_PK_REFERENCE`（line 36）新增：`"etf_dividend": ["code", "ex_date"],`

### 4. `quantstudio/pipeline/source_capabilities.py`

line 33 元组列表新增：`("etf_dividend", "daily"),`

---

## 二、配置改动（2 个文件 × 主配置 + mcp_only profile 副本）

### 5. `config/collector_tasks.json` 与 `config/profiles/mcp_only/collector_tasks.json`

**新增任务（mcp_only 以 `mcp_` 前缀命名，与现有风格一致）：**
```json
{
  "name": "mcp_etf_dividend",
  "enabled": true,
  "source": "mcp",
  "table": "etf_dividend",
  "freq": "daily",
  "start_date": "2005-01-01",
  "codes": ["ALL"],
  "max_workers": 8,
  "retry": {"max": 5, "backoff_sec": [60, 120, 240, 480, 960]},
  "rate_limit": {"calls_per_min": 30, "wait_on_429": true},
  "source_priority": ["mcp"],
  "_note": "类别A 映射表：ETF 基金分红（fund_div），align→validate→write"
}
```
- **stock_dividend 任务不动**（mcp_only 的 `mcp_stock_dividend`）：源切换发生在 mcp_adapter 映射层（1b），同一任务自动改拉 stock_dividend_full。
- 默认 profile 的 `stock_dividend`（source=tushare）保持原样（tushare 直连模式保留，不参与 MCP 运行）。

### 6. `config/alignment_rules.json` 与 `config/profiles/mcp_only/alignment_rules.json`

**6a. `schemas` 段新增 `etf_dividend` canonical schema**（仿 stock_dividend，line 1429 风格）：
```json
"etf_dividend": {
  "_note": "ETF 基金分红（tushare fund_div）。div_cash=每股派息（元），ex_date=除息日（毫秒）。",
  "schema_version": "2.1",
  "primary_key": ["code", "ex_date"],
  "time_key": "ex_date",
  "columns": {
    "code": {"type": "str", "required": true},
    "ex_date": {"type": "int", "required": true},
    "record_date": {"type": "int", "required": false},
    "ann_date": {"type": "int", "required": false},
    "imp_anndate": {"type": "int", "required": false},
    "base_date": {"type": "int", "required": false},
    "div_proc": {"type": "str", "required": false},
    "pay_date": {"type": "int", "required": false},
    "earpay_date": {"type": "int", "required": false},
    "net_ex_date": {"type": "int", "required": false},
    "div_cash": {"type": "float", "required": false, "unit": "元/份"},
    "base_unit": {"type": "float", "required": false},
    "ear_distr": {"type": "float", "required": false},
    "ear_amount": {"type": "float", "required": false},
    "account_date": {"type": "int", "required": false},
    "base_year": {"type": "str", "required": false},
    "update_time": {"type": "str", "required": false}
  }
}
```

**6b. `sources.mcp` 段新增 `etf_dividend` 映射：**
```json
"etf_dividend": {
  "column_map": {
    "ts_code": "code", "ex_date": "ex_date",
    "ann_date": "ann_date", "imp_anndate": "imp_anndate", "base_date": "base_date",
    "div_proc": "div_proc", "record_date": "record_date", "pay_date": "pay_date",
    "earpay_date": "earpay_date", "net_ex_date": "net_ex_date",
    "div_cash": "div_cash", "base_unit": "base_unit",
    "ear_distr": "ear_distr", "ear_amount": "ear_amount",
    "account_date": "account_date", "base_year": "base_year",
    "update_time": "update_time"
  },
  "code_format": "tushare_to_raw",
  "time_to_ms": true,
  "unit_conversions": {},
  "_note": "ETF 基金分红（fund_div）；adapter 侧已过滤 div_proc=实施 + ex_date 非空 + (code,ex_date) 去重"
}
```

**6c. `sources.mcp.stock_dividend` column_map 更新（line 2781 起）**——源列名改为 stock_dividend_full（tushare 字段）：
```json
"stock_dividend": {
  "column_map": {
    "ts_code": "code", "ex_date": "ex_date",
    "record_date": "record_date", "ann_date": "ann_date", "end_date": "end_date",
    "cash_div_before_tax": "cash_div_before_tax",
    "cash_div_after_tax": "cash_div_after_tax",
    "cash_div": "cash_div",
    "stk_div": "stk_div", "stk_bo_rate": "stk_bo_rate", "stk_co_rate": "stk_co_rate",
    "div_proc": "div_proc", "update_time": "update_time"
  },
  "code_format": "tushare_to_raw",
  "time_to_ms": true,
  "unit_conversions": {},
  "_note": "股票除权除息（tushare dividend 全字段，源表 stock_dividend_full）；adapter 侧已归一化 cash_div_tax→cash_div_before_tax 并过滤实施+去重。"
}
```
> 移除原 `_ws_*` 伪列与 `data_source` 映射（新源无这些列）；`pay_date/div_listdate/imp_ann_date` 不入 DuckDB schema（引擎不需要，保持 schema 稳定）。

### 7. `config/sources_config.json`

grep 确认无 stock_dividend/etf_adj_factor 表级条目（源注册为源级）→ **无需修改**；若执行时发现表级声明则补 `etf_dividend: mcp`。

### 8. `docs/mcp_migration/full_table_inventory.json`

登记两条（云端侧事实记录）：
- `etf_dividend`（questdb_table=etf_dividend, canonical=etf_dividend, category=A_mapped, 来源 fund_div）
- `stock_dividend_full`（questdb_table=stock_dividend_full, canonical=stock_dividend, category=A_mapped, 来源 tushare dividend）

---

## 三、GUI 改动（1 个文件）

### 9. `quantstudio/gui/tabs/config_editor_tab.py`

- `DEFAULT_SOURCE_MAP`（line 60）新增：`"etf_dividend": "mcp",`
- `CODES_ALL_HINT` 新增：`"etf_dividend": "ALL = 全部 ETF",`
- `TABLE_DESCRIPTION` 新增：`"etf_dividend": "ETF基金分红记录（每份派息）",`
- `TABLE_CATEGORIES`（line 123）：`"除权行业"` 组追加 `"etf_dividend"`

---

## 四、执行步骤（含验证，审核通过后按序执行）

1. **前置探测**：Trae 推送云端后，`python scripts/mcp_probe_tables.py etf_dividend stock_dividend_full` 实测云端列名/日期格式（决定 1c 归一化块与 1d 日期过滤的实际分支；列名已是 canonical 则归一化自动 no-op）。
2. **落地代码+配置**：按 §一/§二/§三 修改；`ConfigLint` 双 profile 通过（`python -m quantstudio.pipeline.config_lint` 或既有入口，default + mcp_only 各 0 error）。
3. **重置 stock_dividend 水位**（否则增量不会拉历史）：
   ```sql
   DELETE FROM source_watermark WHERE source='mcp' AND table_name='stock_dividend' AND freq='daily';
   ```
   （或首拉用 `--mode full_range`，二选一，推荐删水位 + 正常增量首拉。）
4. **QFQ discovery 洪水预评估与处置（R-1，切换拉数前完成）**：
   1. **SQL 预统计**（只读，切换前）：
      ```sql
      -- 全历史候选 trigger 上界（与 scan_stock_dividend 同口径：div_proc='实施' AND ex_date IS NOT NULL）
      SELECT COUNT(*) AS total FROM stock_dividend
        WHERE div_proc='实施' AND ex_date IS NOT NULL;
      -- 已覆盖行（trigger 队列中同 logical_key 已存在 pending/committed）
      SELECT COUNT(DISTINCT logical_key) FROM qfq_trigger_queue
        WHERE detection_source='stock_dividend' AND status IN ('pending','committed','in_progress','retryable_failed');
      -- 预计新增 trigger ≈ total − 已覆盖
      ```
   2. **干跑计数**：orchestrator CLI 变更类命令默认 dry-run（`qfq_orchestrator_cli.py` 契约：不带 `--execute` 一律 dry-run），用 bootstrap-plan/扫描类命令干跑确认将新增 trigger 数量；
   3. **决策矩阵**：
      - 预计新增 **≤ 1000**：正常切换，discovery 常规处理；
      - 预计新增 **> 1000 且 B-5 baseline 机制已激活**（qfq_active_cutover 存在、baseline 表已建立）：切换后执行 **`baseline-build`**（`establish_discovery_baseline`，把全历史行写入 applied_payload_hash 标记为已观察）→ 首轮 discover 净新增 0（首选，不洪水）；随后 `cutover-canary --expected-baseline-rows <全历史行数>`（原默认 2181 需更新）验证；
      - 预计新增 **> 1000 且 B-5 未激活**（当前生产 pre-B-5）：**受控一次性放行**——允许 discovery 生成全历史 trigger，由 QFQ 修订流水线批量处理（一轮，监控 trigger 队列积压与 factor_revision 重算耗时，完成后重放 0）；或**推迟源切换到 B-6 激活后**再切（可接受则选此）；
      - **禁止裸切后直接跑 QFQ 常驻**（任何分支都必须先完成 4.1-4.3 再启动常驻）。

   **2026-08 实测修订（执行期发现）**：
   - 正式库 **B-6 cutover 已激活**（`qfq_active_cutover = (mcp, b6_formal_20260807_v2)`，qfq_discovery_baseline 2181 行）；
   - 但 `establish_discovery_baseline` 强制 `require_status="baseline_building"`（qfq_discovery_baseline.py:44），而状态机 `active → {superseded, failed}`（qfq_cutover.py:27）**不允许转回 baseline_building** → **baseline-build 首选路径在正式库不可用**；
   - tushare 抽样实测估算全历史 ≈ **11.8 万行**（30 只样本均值 21.8 条/只 × 5400 只）→ 预计新增 trigger ≈ **11.6 万**；
   - **处置决策（推荐）**：**受控一次性放行 + 分批 wave**——首拉后按 `codes_filter` 分批（如 500 只/轮）跑 discovery 并让编排器逐批消化，监控队列积压；trigger 处理对因子源无变化（云端 adj_factor 未变）时多为幂等修订（no-op reanchor），一次性成本为队列排水时长（小时级）；可先用小样本（5~10 只）canary 验证 trigger 生成与处理正确，再全量放行；
   - 备选：推迟源切换（保留单位缺陷数据），或由 MCP 项目另立工作包做新世代 cutover 迁移（重走 B-6 激活流程）——超出本任务范围。

   **zcode 批准（2026-08，受控放行的 3 个硬性前置条件，执行前必须满足）**：   1. **canary 幂等验证（硬性门槛）**：抽 5~10 只股票跑通 discovery → claim → reanchor 全链路，断言：① factor 未变的历史行 → reanchor 为**幂等 no-op**（anchor 不推进、front 不变写）；② 单条耗时 < 1s。**假设不成立（出现大量真实重锚）→ 立即叫停，回到方案讨论**；
   2. **全量耗时估算与分批**：canary 单条耗时 × 11.6 万 = 总时长；若 > 4 小时，必须定义分批 wave（如每批 2000 trigger）+ 批间暂停（消化队列）+ 积压上限（pending > 5000 时暂停注入）；
   3. **回退预案**：若全量放行中异常（真实重锚率 > 10%、队列积压失控、front 数据被改错）→ **立即停止 daemon + 恢复备份**。**`.bak_c4merge` 不存在（2026-08 实测）**，可用备份（`data/`）：`quantstudio.20260807T041035.db`（14.3GB，2026-08-05，B-6 激活前快照，首选回退点）、`quantstudio_backup_stepB_20260731_171119.db`、`quantstudio_backup_20260729_100725.db`。**执行步骤 5 前必须先做当前库备份**（`quantstudio_backup_pre_pipeline_<date>.db`），回退后核对 stock_dividend 2181 行基线 + trigger 队列状态 + baseline 2181 行。
5. **首拉**：daemon/CLI 运行 `mcp_stock_dividend` 与 `mcp_etf_dividend`（full_range 一次），确认：
   - `stock_dividend` 全历史行数（预期数万级）、`stk_div/stk_bo_rate/stk_co_rate` 出现非零行、`div_proc='实施'`；
   - `etf_dividend` 行数、510500 的 div_cash（0.087/0.091/0.062/0.149）与 etf_daily preClose 差额核对；
   - 159995/518880/159915 在 etf_dividend 无记录（正常/已知缺口），登记缺口清单（TD-ETF-DIV 兜底）。
6. **存量一致性抽查（R-6）**：切换前旧 ws_exdiv 数据与切换后同 PK 行对比——抽查 3~5 只股票（如 600000.SH 2026-07-16、002107.SZ 2026-03-05、300803.SZ 2026-03-06、688230.SH 2026-03-19）的 `cash_div_before_tax` 新旧值。**预期系统性差异**：旧值为遗留多源单位口径（601628 每股 0.618 存为 61.8），切换后为 tushare 每股正确值（0.618）——差异属**单位修正**（§〇 背景确认），抽查记录前后值即可，**不需回滚**；仅当出现非单位类意外差异（同一事件派息金额与 tushare 不符）才需排查。
7. **空日期断言（R-4）**：确认 aligner `time_to_ms` 对 NULL/空字符串日期输出 **NULL（非 0/1970-01-01）**；etf_dividend 空日期列落 NULL、`ex_date` 非空行才入库（防 fin_indicator 同类缺陷）；该断言同时进单测（§六）。
8. **增量续跑**：次日/下一轮增量正常（水位推进、A4 变更检测不误报）。
9. **质检**：quality audit 双表 PASS；按步骤 4 决策矩阵完成 discovery 首轮处置后，确认 trigger 数量符合预期（≤1000 或 baseline 扩展后净新增 0 或受控放行一轮完成）。
10. **回归**：既有测试套件（config_lint / authority / daemon_qfq_integration / staging 工具）全过；**回测黄金基线不重跑**（本任务纯数据层，引擎未动，回测行为不变）。
11. **GitHub 同步**（铁律）：本地完成 + 用户确认后，推送双仓库（quantstudio-plus + quantstudio），同步 README + `docs/strategy_toolbox.md` + `docs/prompt_engineering.md` 中涉及 ETF 分红/除权数据来源的表述；更新 MCP 实时进度报告。

---

## 五、风险与协调

| 风险 | 等级 | 缓解 |
|---|---|---|
| QFQ discovery 历史 trigger 洪水（stock_dividend 全历史 → 全表 hash 扫描新增数万 trigger → 前复权因子全历史修订重放） | **高** | 按 §四步骤 4 前置处置：切换前 SQL 预统计 + orchestrator CLI 干跑计数 → 决策矩阵（≤1000 直接放行 / >1000 走 `baseline-build` 扩展（首选，净新增 0）/ 受控一次性放行 / 推迟到 B-6 激活）；切换后 `cutover-canary --expected-baseline-rows` 更新为全历史行数；**禁止裸切后直接跑 QFQ 常驻** |
| 云端 ex_date 返回格式（TIMESTAMP/YYYYMMDD/epoch-ms）不确定 | 中 | 步骤 1 probe 实测；1d 的 `_norm_date` 毫秒分支防御 |
| fund_div 重复行/覆盖缺口（518880/159915 无记录） | 中 | 1c 去重 + 缺口清单登记；缺口 ETF 现金分红由回测任务兜底（fund_adj/preClose 检测） |
| 空日期 → 1970-01-01 | 中 | 步骤 5 显式验证；必要时 adapter 侧将空日期置 NULL |
| ws_exdiv 残留双写 | 低 | 映射切换后 QuantStudio 不再消费 ws_exdiv；云端保留不删 |
| `_inject_dividend` 与 aligner 列名口径 | 低 | 1c 归一化后统一 canonical 列名；单测覆盖 |

---

## 六、测试补充（本任务新增）

- `tests/test_mcp_dividend_normalize.py`：1c 归一化块单测（cash_div_tax 重命名、实施过滤、去重、ws_exdiv 兼容分支**断言不命中**（R-3））。
- `tests/test_etf_dividend_aligner.py`：aligner `time_to_ms` 对 NULL/空字符串日期输出 **NULL（非 0/1970-01-01）** 的断言（R-4），覆盖 ex_date 非空过滤与空日期列落 NULL。
- `tests/test_config_lint.py` 扩展：etf_dividend 任务/映射/PK 一致性。
- `tests/test_etf_dividend_pipeline.py`（可选）：端到端——fake cloud df → adapter → align → writer → DuckDB etf_dividend 内容断言。

---

## 七、与本任务无关（后续任务，不在本方案范围）

- 回测引擎除权补正（`_apply_factor_derived_split` 等）：按 `etf-split-factor-derived-fix-plan.md` + 审核意见（P0-1/P0-2、P1-3/4/5）实施，并读取本任务产出的 `etf_dividend`（ETF 现金入账，TD-ETF-DIV）与全历史 `stock_dividend`（股票精确路径真正生效）。
- 执行顺序：本方案审核通过并落地验证 → 用户确认 → 再启动回测除权补正任务 → ETF 动量回测净值对齐验收。

---

## 八、执行期技术债登记（2026-08-16 实测）

| TD | 内容 | 状态 |
|---|---|---|
| TD-QFQ-FRESH-CACHE | export 缓存无失效机制——fetcher 与 daemon 共用 export_cache，fetcher 路径无 A4 变更检测，云端数据推进后缓存陈旧 → fresh 拉取止于旧数据 → rebase BLOCK。**已实证处置**（清 stock_daily 缓存后 fresh 恢复 08-13）；建议缓存加 TTL 或 fetcher 路径接入 A4 变更检测 | 登记（harness 侧） |
| TD-CLOUD-MINUTE-FRONT-STALE | ~~云端分钟 front 陈旧~~ → **撤销**（Trae 核实 + 实证：云端分钟 adj_factor 与本地/tushare 逐股逐日一致） | 撤销 |
| **TD-CLOUD-MINUTE-15MBAR（已修复）** | stock_minutes 口径分裂：load_minutes 自 8/6 起对分钟价格做前复权（只影响新写入行），8/14 回退重写使 8 月上旬除息股全历史分钟被 qfq 化（99.2% raw / 0.6% qfq），600519 06-15 15:00 bar 出现一字后复权错误值 1301.18（vs 日线 1271.1），06-15 全市场 26.5%（1,382 只）15:00 bar 与日线不一致。**已修复（Trae）**：load_minutes + backfill_stock_minutes.py 源头根治（去掉分钟价格 qfq、保留 adj_factor 列）+ 云端 32 只全历史还原（~300 万行），tushare 直连三方一致、全市场扫描 0 污染。**QuantStudio 侧需重拉分钟表**（重置 stock_minutes/etf_minutes 水位 + full_range，2026-06-14 起窗口） | 已修复（数据侧），QuantStudio 重拉执行中 |
| TD-BASELINE-EXT | discovery baseline 状态机缺"active 后扩展"路径（active → baseline_building 被禁止，re-baseline 不可用）——受控放行期间以 canary + 分批 wave 代替 | 登记（zcode） |
