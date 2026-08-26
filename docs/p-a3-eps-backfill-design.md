# P-A3 设计文档：MCP 财务数据管线 eps 列缺口根治（跨表回补 + 管线级免疫）

- 状态：**复审通过（2026-08-25），准予进入实施期 1（今日代码与临时库验证）**
- 流水线：六步第 1 步方案 ✅ → 第 2 步审计 ✅（有条件通过）→ 复审 ✅（通过+三条细化）→ 第 3 步实施期 1 进行中
- 审计方记录：ZCode/审计方 2026-08-25 有条件通过；修订版复审通过。审计意见全文见下方 §10
- 关联登记：issue_registry P-A3（立项，状态链见进度报告）
- 回退点：实施前 `git stash create -u -m "baseline-pa3-<ts>"`（hash 回填本文件与登记表）

> 总原则：纯增益修复——对不存在的缺口零行为变更（无 NULL 库上管线行为逐字节不变）；不改变任何管线既有功能（拉取任务/清洗规则/写入口径/水位机制零语义变更）；不造数据（无源可补的空值保持空，显性登记而非硬填）。

---

## 1. 问题定义（真实库实证，data/quantstudio.db 只读 probe 2026-08-25）

| 指标 | 实证 | 说明 |
|---|---|---|
| fin_indicator 行数 | 135,840（100% data_source='mcp'） | 当前生产 profile 实为 mcp_only |
| eps NULL 总行 | **4,827** | = 3,189 可回补 + 1,638 不可回补 |
| **可回补候选** | **3,189 行** | eps NULL 且 income_statement 同 (code,end_date) basic_eps IS NOT NULL |
| 不可回补 | 1,638 行 | income 无该报告期行或 basic_eps NULL（次新上市前报告期主体） |
| 最新公告行 eps NULL | **218 码**（156 可回补 / 62 不可回补） | 用户口述 ~147 为较早时点口径，以 218 为当前基准 |
| 最新公告行 or_yoy NULL | 48 码（39 与 eps 同 NULL，9 eps 有值） | **no-source → 不回补，显性登记** |
| 交叉对账 fin.eps ↔ inc.basic_eps | 124,078 对中 121,732 相等（98.1%） | 同源复制列成立（用户 98.4% 接近） |
| 回补期分布 | 2018-2026 全历史期（2020:638/2023:611/2025:554/2021:451/2019:418/2026:100…） | **影响所有消费 eps 的历史回测（预期变更，§7 归因）** |
| ann_date 取较大者的 PK 冲突 | **0** | 回补 UPDATE 安全 |
| 000063 镜像 | fin eps=NULL, or_yoy=6.1267；income basic_eps=0.27（同 key） | 根因复现 |
| 次新禁区实证 | 001220/301449 上市前报告期在 income **无行** → 不可回补 | 保持 NULL ✓ |
| diluted_eps | income_statement **无此列** | **同构规则显式排除（无源可补）**，登记 |
| 消费路径 | `get_fundamentals('eps'/'growth_ability')` → `get_financial` → `duckdb_data_access.query_fin_indicator`（SELECT f.eps，不 join income，eps NULL 直出）→ 策略 `_latest_by_code` 按 (end_date,publ_date) 取最新行，value=NaN → `np.isfinite` 剔除 | 回补在数据层生效后消费侧零改动自动透出 |

## 2. 根因（三层，全部代码/数据实证）

1. **MCP 源端回填不同步**：income_statement.basic_eps 已回填（000063 2026Q1=0.27）而 fin_indicator.eps NULL（同表 or_yoy 却有值）——两表回填进度错位。
2. **两列同源**：98.1% 精确相等 → 跨表回补口径安全。
3. **消费口径放大**：`_latest_by_code` 取"最新公告行"，该行恰为 NULL 时整码被 `np.isfinite` 剔除（L3v/L6 漏斗）；次新上市前报告期（ann 2026-04 但 end_date=2025-03-31）属真实无数据，不可回补。

## 3. 解决方案（三道防线）

### 防线 1：管线内跨表回补（治本）

**核心逻辑全部放新文件 `quantstudio/pipeline/eps_backfill.py`（零污染新增）**：

- `EPS_BACKFILL_PAIRS` 注册表：`[(("fin_indicator", "eps"), ("income_statement", "basic_eps"))]`——同源复制列对，**泛化扩展点**：未来 np_yoy↔净利增速等同源列对按同款加一对，门禁自动覆盖。
- `backfill_eps_gap(conn, dry_run=False) -> BackfillResult`：全库幂等回补。
- `check_eps_backfill_gap(conn) -> int`：门禁查询（防线 2 核心，供 quality_audit 与 CLI 复用）。

**回补 SQL（确定性，防多匹配）**：

```sql
UPDATE fin_indicator fi
SET eps = ic.basic_eps,
    ann_date = CASE WHEN fi.ann_date >= ic.ann_date THEN fi.ann_date ELSE ic.ann_date END,
    backfill_eps_source = 'income_statement.basic_eps'
FROM (
  SELECT code, end_date, ann_date, basic_eps,
         ROW_NUMBER() OVER (PARTITION BY code, end_date ORDER BY ann_date DESC) rn
  FROM income_statement WHERE basic_eps IS NOT NULL
) ic
WHERE fi.eps IS NULL AND ic.rn = 1
  AND ic.code = fi.code AND ic.end_date = fi.end_date
```

- **幂等**：二次执行 eps IS NOT NULL 不命中；**无缺口库 0 行命中 → 逐字节不变**（纯增益核心）。
- **PIT 保守**：ann_date 取 max(fi_ann, ic_ann)——回补值可见日不早于任一表公告日；实证 PK 冲突 0。
- **确定性**：rn=1 限定 income 每 (code,end_date) 最新公告版 basic_eps（DuckDB UPDATE..FROM 多匹配行为不定，必须钉死）。
- **禁区**：只做同报告期跨表复制，零推导、零外推；不可回补 1,638 行保持 NULL。

**打标（可审计核心决策）**：新增列 **`backfill_eps_source VARCHAR`**（NULL=原生；回补行='income_statement.basic_eps'）。**不动 data_source**——理由：主 profile fin_indicator 权威源 tushare + allow_fallback=false，改 data_source 即 AuthoritySourceViolation error；且当前库数据源 mcp。新列经 DDL_DUCKDB + COLS 声明 → 新库 CREATE 含列、存量库 `_migrate_add_columns` ALTER 幂等补列（既有迁移机制，test_financial_dividend_schema_migration 同款）。

**挂接（写后回补）**：`DuckDBWriter._write_locked` 的 fin_indicator 分支，upsert 后、conn 关闭前调用 `eps_backfill.backfill_eps_gap(conn)`（同一 conn 同一 _conn_lock；**try/except log-error 不阻断 write 成功路径**——回补失败由防线 2 门禁 error 兜底）。回补审计日志（rows_updated/affected_codes）输出 logger + CLI 报表。**水位机制零触碰**（回补是 UPDATE 非拉取，不推进 source_watermark）。

### 防线 2：质量门禁（防再发）

`quantstudio/pipeline/quality_audit.py` run() fin_indicator 分支（GrowthFieldAllNull 检查旁）新增：

```python
if table == "fin_indicator" and "eps" in columns and "income_statement" in tables:
    gap = eps_backfill.check_eps_backfill_gap(conn)
    if gap > 0:
        self._add(report, "EpsBackfillGap", table, gap, "error",
                  "eps NULL 但 income_statement 同 key basic_eps 非空（回补未生效或源 schema 变化）")
```

- 语义：每日同步后 gap>0 ⇒ 回补规则失效/漏跑/源端 schema 变化 → error 告警。
- **泛化登记**：检查类别「同源复制列跨表一致性」，由 `EPS_BACKFILL_PAIRS` 驱动（当前 1 对）。
- income_statement 表缺失时跳过（不引用不存在表）。

### 防线 3：消费侧兜底（双保险，双端同源）

**`_latest_by_code` 改「最新非 NULL 行」**（策略内函数，本地与 PTrade 产物同源，改一次双端一致）：

```python
val = np.asarray(df[value_field], dtype=float)
mask = ~np.isnan(val)
if not mask.any():
    return {}
frame = frame[mask]   # 之后原有 sort/dedup 不变
```

- 防线 1 已回补码：最新行即非 NULL，行为不变；次新/无源码：自动回退上市后有值期（对齐平台"最新有值行"语义）。
- **纯增益边界**：仅"最新行 NULL"的码行为变化（剔除→回退），无缺口库最新行全有值 → 零行为。
- 改动文件（全部 untracked/agent_workspace 源，**零 git 冲突**）：
  - `quantstudio/backtest/strategies/weekly_smallcap_growth_momentum_10_quantstudio.py`
  - `quantstudio/backtest/strategies/周频小市值成长动量（三层止损）.py`
  - `agent_workspace/wsgm10/strategy.py`
  - `agent_workspace/wsgm10v2/strategy.py`
  - `agent_workspace/wsgm10v2/周频小市值成长动量（三层止损）.py`
  - （grep 确认全仓仅这 5 处定义 `_latest_by_code`）
- **skill 侧**（SKILL.md 他人 M → **不改 SKILL.md**）：
  - `skills/quantstudio-strategy-compiler/references/component-catalog.json`（git 干净）加 `_latest_by_code` 组件条目（最新有值行语义）。
  - `skills/quantstudio-strategy-compiler/scripts/validate_agent_strategy.py`（git 干净）加规则 **`FUNDAMENTAL-LATEST-VALUE`**（AST 检测 `_latest_by_code` 定义缺 value NaN 过滤 → BLOCK，防新策略用旧口径；沿用既有 `_issue(ID,"BLOCK",msg,lineno)` 机制）。

## 4. 挂接冲突处置（多会话污染核对，AGENTS 铁律）

| 目标文件 | git 状态 | 处置 |
|---|---|---|
| `quantstudio/pipeline/eps_backfill.py`（新增） | — | 核心逻辑零污染 |
| `scripts/backfill_eps_gap.py`（新增） | — | CLI：`--check`（门禁）/ `--backfill`（默认 dry-run 预览，`--apply` 落库，输出回补报表） |
| `quantstudio/pipeline/writers.py` | **他人 M**（+39 行：3A 写锁重构 `_write_locked` + etf_dividend 表） | 挂接在 `_write_locked` 内（3A 重构后主体），hunk 与 etf_dividend 区域**零交集**；实施前 `git diff writers.py` 复核。DDL/COLS 两处 hunk 同理（只加 backfill_eps_source 列定义） |
| `quantstudio/pipeline/quality_audit.py` | **他人 M**（+241 行 D2-F5 等） | run() fin_indicator 分支 +4 行调用；实施前 diff 复核区域 |
| `quantstudio/pipeline/daemon.py` | 他人 M | **零改动**（回补在 writer 内部，无需 daemon 挂接） |
| `skills/.../SKILL.md` | 他人 M | **不改**；skill 更新走 component-catalog.json + validate_agent_strategy.py（均干净） |

**审计确认点（fallback）**：若审计裁定「绝不触碰他人 dirty 文件」，防线 1/2 降级为**独立调度**（CLI --check/--backfill 计划任务定期触发，弱化为定时免疫）。主方案以最小 hunk 叠加（有 ptrade_api 9 hunk 零交集先例）为准。**复审采纳主方案；任何 hunk 复核发现与他人改动重叠时，该处即降级并记录。**

## 5. 实施期 1（今日，零主库触碰）

1. 方案落盘（本文件）。
2. `git stash create -u -m "baseline-pa3-<ts>"` 回退点。
3. 代码实施（§3/§4 全部文件）。
4. 新文件单测（`tests/test_eps_backfill.py` ≥12 例，**全部临时库** `_make_temp_db` 模式）即刻跑绿。
5. **grep 既有测试真实库直连路径 → 命中者临时 skip/改临时库**（硬约束 A 覆盖全部测试执行），确认后于 protect 完成（~12:30）后跑全量回归。
6. M 文件 hunk diff 复核（writers/quality_audit）。
7. 报一期验收（审计方审）：≥12 例全绿 + 既有测试零回归 + hunk 复核通过 + 默认产物哈希不变。

## 6. 验收分两期

**一期（今日，临时库）**：
- `tests/test_eps_backfill.py` ≥12 例全绿：回补命中（值=basic_eps）/ 幂等（二次 0 行）/ PIT（ann=max，窗口早于 ic_ann 不可见）/ 打标（值正确 + data_source 原值不变）/ 无缺口零行为（无 NULL 夹具回补前后逐字节 sha256 一致）/ 禁区（income 无行保持 NULL）/ 多匹配确定性（rn=1 取最新版）/ PK 冲突 0 / 门禁 gap 计数 + audit error / diluted_eps 显式排除 / CLI dry-run 不落库 / 防线 3 回退 + 无缺口不变。
- 既有测试零回归（重点：test_financial_dividend_schema_migration 兼容新增列、quality_audit、writer、test_ptrade_fidelity_config 44）。
- M 文件 hunk diff 复核通过（零交集）。
- 默认转换产物哈希不变（防线 3 不涉默认产物注入路径；week10 产物重转验证）。

**二期（周三推送后，真实库数据验收）**：
- 与用户确认 daemon 状态（运行中下次 fin_indicator 拉取自动触发 vs 停止时 CLI --apply）。
- 真实库回补执行：回补 3,189 行、gap→0、000063/000858 抽检、PIT 实证、打标可查。
- week10 保真重跑：先复现基线（当前缺口库 ≈ 用户 4313/15）→ 回补后 L6_eps 15→≈20、L3v_complete 4313→向 4373 收敛（上限 = 恢复 156 码中 or_yoy 有值者）；**残差逐项归因**：or_yoy 无源 48 码（最新行口径）、平台池/口径差异、次新 62 码不可回补。
- 黄金基线按协议重验（数据变更 3,189 行 → 相关 golden 对比重跑归因）。
- 证据落盘 `docs/evidence/p-a3-eps-backfill-<date>.md`。

## 7. 预期变更显式归因（非 BUG，但要明示）

- 数据面：3,189 行 eps NULL→值、少数行 ann_date 调整、backfill_eps_source 打标。
- 回测面：所有消费 get_fundamentals('eps'/'growth_ability') 的策略（不止 week10）选股池与历史回测变化——修复目标。
- 保真模式：week10 双端对账 L3v/L6/选股重合度变化逐项归因入证据。
- 黄金基线：按协议重验（方案 §6 二期）。

## 8. 回退条件

- 任一单测回归 → `git reset --hard <回退点>`。
- 回补 SQL 意外影响非 eps 行（diff 校验）→ 立即停止并回退。
- 无缺口库行为漂移（一期 sha256 红）→ 回退。
- week10 对账未收敛且归因显示回补引入新偏差 → 回退防线 1。
- **回补可逆**：`UPDATE fin_indicator SET eps=NULL, backfill_eps_source=NULL WHERE backfill_eps_source='income_statement.basic_eps'`。

## 9. 文档同步（六步第 6 步推送前置，周三）

- `README.md`：财务数据管线章节补 eps 跨表回补说明。
- `docs/strategy_toolbox.md`：get_fundamentals eps 数据说明（跨表回补 + backfill 打标）。
- `docs/prompt_engineering.md`：字段规范（eps 缺失自动回补语义）。
- 进度报告（MCP 全数据源替代实时进度报告）+ issue_registry P-A3 立项→closed 状态链（ZCode 审核通过后更新）。

## 10. 审计与复审记录

### 步骤 2 审计（2026-08-25，ZCode/审计方）：有条件通过

- ✅ 采纳：三层防线 / SQL rn=1 确定性 / ann_date=max PIT / 幂等 / backfill_eps_source 打标不动 data_source（AuthoritySourceViolation 规避成立）/ diluted_eps、or_yoy 无源排除登记 / 次新禁区保持 NULL / 防线 3 五文件+validator+component-catalog（避开他人 M 的 SKILL.md）/ writers.py、quality_audit.py 最小 hunk+diff 复核（保留降级 fallback 审计确认点）/ 无缺口库 sha256 逐字节实证。
- 🔴 硬约束 A：今日 ~12:30（SNAP_003 create+verify+protect）前禁止任何触碰 quantstudio.db 的动作（含测试真实库部分）——DuckDB 写冲突 + 快照撕裂。
- 🔴 硬约束 B：真实库 --apply + week10 重跑 + 黄金基线重验整体排周三推送之后（今天只做方案落盘/代码实施/临时库单测/M 文件 diff 复核）。
- 六步照走：本审计 = 步骤 2；实施完成临时库全绿后报验收（步骤 4 审计方审）；推送（步骤 6）与今日 len/test 修复同款纪律（严格文件清单）。

### 复审（2026-08-25）：通过，准予进入实施期 1

三条细化（非否决项）：
1. **全量回归排今日下午 protect 完成后**跑（代码编辑/新文件临时库单测可即刻；不与 10:35-12:30 verify 分片 hash 内存敏感窗口并发）。
2. **既有测试零真实库路径确认**：回归执行前 grep 直连 data/quantstudio.db 的用例，命中临时 skip 或改临时库——硬约束 A 覆盖全部测试执行。
3. **周三推送排序显式化**：交付推送（黄金基线链）先行 → P-A3 代码推送随后独立 commit（严格文件清单），两者不混批；今日黄金基线证据标注「P-A3 数据回补 pending（周三后执行+基线重验）」。

### 一期验收（2026-08-25，审计方独立复跑确认通过）

- **通过**：15P+1S（tests/test_eps_backfill.py）一致、迁移套件全绿、4 新文件实存、writers/quality_audit hunk 抽查零交集、回退点 fa98d905 在案。
- skip 用例定性正确（写锁防呆 `data/snapshots/.write_lock` pid=40276 SNAP_003 create，12:30 自动解除）。
- **一期补全条件**：全量回归（含真实库直连用例：test_qfq_range_r1a/test_security_metadata_api/test_qfq_schema_migration/test_batch_apis/test_quality_audit_anchor/test_empty_trade_days_diagnosis）排今日 ~12:30 protect 完成后跑，全绿才关闭一期。
- **二期**（周三推送后）：真实库 --apply + week10 重跑 + 基线重验，daemon 时序与用户确认。
- **周三推送**：交付先行 → P-A3 独立 commit（4 新增+4 M 最小 hunk+5 策略+3 文档），步骤 5 需用户确认。