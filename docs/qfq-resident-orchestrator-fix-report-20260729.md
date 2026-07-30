# QFQ 常驻编排器修复报告（2026-07-29）

> **状态**：常驻 QFQ 编排器（PR #6/#7/#8）+ authoritative rebase 全链路（R1/R2/阶段4/阶段6A）
> + R3 真实 staging 验收全部完成。阶段6A trigger 粒度修复已落地。**生产配置仍关闭**：
> `qfq_orchestrator.enabled=false`、`factor_refresh_enabled=false`。功能进入"待生产启用"状态。
> 全量 qfq 回归 **214 passed**（authoritative 29 + batch1 104 + batch2 83 + orchestrator 13，含重叠去重）。

## 1. 修复目标

把已完成的 QFQ 单证券重锚引擎（`apply_reanchor_for_security`）串成**生产常驻闭环**：
daemon 每轮采集后自动发现股票/ETF 除权除息事件，用 fresh xtquant 数据重锚前复权字段，
失败自动登记恢复，质量门控不通过不推进水位。

修复前主体框架已完成，但真实生产闭环有若干阻断（详见各阶段）。

## 2. 修复阶段与内容

### 阶段一：预演练阻断修复（PR #6）

| 阻断 | 修复 | 模块 |
|------|------|------|
| 股票/ETF 因子未分库 | adj_factor（股票）/ fund_adj（ETF）独立表 + staging 迁移工具 | qfq_maintenance |
| ETF 新 factor date 无法触发 | 相邻 factor_time 值变化检测 → factor_new trigger | qfq_observation / qfq_event_discovery |
| dividend payload 不完整 | 13 字段完整 hash + 旧/新 schema 兼容 + _norm_div_val 规范化 | qfq_event_discovery |
| xtquant 未先 download | download_history_data 分窗下载再 get，失败抛 FreshCaptureDownloadError | qfq_fresh_capture |
| bootstrap 误解锁 | fail-closed（版本校验 + blocked/failed 不解锁）+ stale-only 分类 | qfq_resident_orchestrator |
| daemon 未接因子刷新 | QFQFactorRefresher + detector_degraded → 水位 hold | daemon / qfq_factor_refresh |

### 阶段二：自动测试门控（PR #6）

6 个新测试文件（41 用例）+ 双环境验证（CP936 / PYTHONUTF8=1）：
- test_qfq_factor_split_storage / test_qfq_factor_refresh / test_qfq_factor_new_date_trigger
- test_qfq_dividend_payload_full / test_qfq_fresh_capture_download / test_qfq_bootstrap_gates
- 既有 test_qfq_event_discovery（payload 13 字段对齐）/ test_daemon_qfq_integration（degraded hold）

### 阶段三：C3 裸码→ts_code 转换（PR #6）

**冲突点**：QFQFactorRefresher 传裸码给 Tushare（需 ts_code）；`market_of_code` 对 ETF 5/1 开头误判 BJ。

**修复**：
- `aligner.raw_to_tushare_ts_code(code, asset_type)`：纯前缀规则（STOCK 6→SH/0,3→SZ/4,8→BJ；ETF 5→SH/1→SZ），幂等，.SS→.SH，未知后缀抛错。
- `qfq_maintenance.resolve_ts_codes`：元数据优先（stock_basic/etf_basic），miss 前缀 fallback。
  - **已带合法后缀幂等保留**（不被元数据覆盖）
  - 未知前缀防御性 fallback 到 .BJ + 聚合 WARNING
- `qfq_factor_refresh.refresh`：各资产类别 try 内调 resolve_ts_codes（股票异常不影响 ETF）
- daemon `_fetch_adj_factor`（PR #8）：同样用 resolve_ts_codes 替换 market_of_code

**关键修复（多轮审核）**：
- import json 缺失（fresh_capture 生产 NameError）
- fetch_adj_factor 静默吞异常（全失败 → FactorRefreshError → degraded）
- RateLimiter 接口不一致（生产无 `__call__`，删 refresh 外层调用）
- 显式后缀被元数据覆盖（已带后缀幂等保留）

### 阶段四：staging 演练（PR #7）

`scripts/qfq_staging_rehearsal.py`：核心守恒闭环验证 ✅ 通过
- raw OHLC / *_back / 行数 4 张表演练前后逐行一致
- 正式库 SHA 不变（未污染）
- ETF 因子分库行数守恒
- 重锚被正确 BLOCK（fresh 基准不一致时不覆写）

### 阶段五：front 修正语义验证（PR #8）

`scripts/qfq_front_fix_verification.py`：fresh_staged 完整路径 ✅ 通过
- status=committed（precheck + postcheck 全通过）
- raw/back/行数守恒 ✓
- front 修正回真值 ✓

**关键技术点**：减法复权模型（front=raw-D）满足 front_chain 加法豁免；
窗口选除权后无除权连续段；tushare trade_cal 填完整日历。

### 阶段六：authoritative rebase 全链路 + 阶段6A 粒度修复

在 orchestrator 核心之上，新增 `fresh_authoritative_rebase` 模型并完成生产闭环：

- **R1（模型注册 + precheck）**：`MODELS` 增加 `fresh_authoritative_rebase`；`apply_reanchor_for_security`
  增加 rebase 分支（显式模型选择 + 防呆）；`stage_fresh_authoritative`：基本校验 + 完整覆盖 +
  raw 逐 bar 对齐（删除理想化乘法/加法比例校验）。
- **R2（postcheck）**：`run_postchecks` 增加 rebase 分支，移除 `scale_consistency`（乘法）和
  `front_chain`（乘法/加法收益），保留 daily_staged_match / kline_relation / row_conservation /
  cross_table_overlap / minute_* 四项 + 事务回滚 + capture 不可变契约（冲突检测 + 崩溃恢复幂等）。
- **阶段4（编排器接入）**：编排器显式选择 rebase；capture `INSERT OR REPLACE`→plain `INSERT` 清理 +
  端到端 FakeFreshFetcher（10 passed）。
- **阶段6A（trigger 粒度修复）**：`_reanchor_security` 在 rebase 模式下改从 `stock_dividend` +
  `factor_observation` 取该证券**全量** ex_dates，修复增量轮次丢历史除权日的局部重基缺陷。
  仅影响 rebase 模式（`ratio`/`fresh_staged` 逐位不变），`apply_reanchor_for_security` 签名不变。

**R3 真实 staging 验收结论（committed>0 证实功能可用）**：
- 2.1 单证券直接 apply（全 ex_dates 一次性）→ 4/4 committed（000012 多次分红 / 002864 送转 /
  510300 ETF / 600000 银行分红）；front 调整比率与 xtquant 前复权逐日一致（机器精度 ~1e-16）；
  raw/back/行数 SHA 演练前后逐行一致。
- 2.2 编排器 reconcile-once（9 证券全样本）→ `triggers_found=151, committed=6/7 单元`
  （ETF 无 ex_date 不入队），`blocked=0`，正式库 SHA 不变。对比 fresh_staged 演练 `committed=0`
  被乘法校验 BLOCK。
- 证据目录：`output/qfq_rebase_r3_20260730/`（~810MB staging 数据不入库，仅保留结论）。

## 3. 变更框架文件清单

| 文件 | 主要改动 |
|------|---------|
| `qfq_maintenance.py` | 因子分库 + FactorRefreshError + resolve_ts_codes + universe helper + staging 迁移 |
| `aligner.py` | raw_to_tushare_ts_code（纯前缀转换） |
| `qfq_factor_refresh.py` | QFQFactorRefresher（各资产 try 内转换 + degraded 契约） |
| `qfq_event_discovery.py` | factor_new 检测 + dividend 13 字段 payload |
| `qfq_observation.py` | 相邻 factor_time 值变化检测 |
| `qfq_fresh_capture.py` | download-before-get + import json + download_trace |
| `qfq_reanchor_schema.py` | 新表/列 + manifest 同步 |
| `qfq_resident_orchestrator.py` | bootstrap fail-closed + stale-only + detector_degraded |
| `qfq_orchestrator_types.py` | factor_refresh_enabled + download_trace |
| `daemon.py` | 因子刷新接入 + degraded hold + _fetch_adj_factor 用 resolve_ts_codes |

## 4. 测试文件清单（9 个）

test_qfq_factor_refresh / test_qfq_ts_code_resolve / test_qfq_factor_split_storage /
test_qfq_factor_new_date_trigger / test_qfq_dividend_payload_full /
test_qfq_fresh_capture_download / test_qfq_bootstrap_gates /
test_qfq_event_discovery（改动）/ test_daemon_qfq_integration（degraded hold + ETF 后缀）

## 5. 验收证据

| 项 | 结果 |
|----|------|
| 全量回归（PR #6） | 1709 passed, 0 failed |
| daemon ETF 后缀（PR #8） | 2 新测试 passed，无新增回归 |
| staging 守恒 | raw/back/行数一致，正式库 SHA 不变 |
| front 修正 | committed，raw/back 守恒，front 修正回真值 |
| 双环境 | CP936 / PYTHONUTF8=1 均 0 failed |
| **R3 authoritative rebase 验收** | **committed>0**（2.1 单证券 4/4；2.2 编排器 6/7 单元），front~oracle ~1e-16，守恒，正式库 SHA 不变 |
| **全量 qfq 回归（含6A）** | **214 passed**（authoritative 29 + batch1 104 + batch2 83 + orchestrator 13，重叠去重） |

## 6. 已知风险

| 风险 | 状态 | 说明 |
|------|------|------|
| 部分码失败不 degraded | 保持现状（风险2） | 失败码可能用旧快照；是否升级另立审核 |
| 源端语义故障不可检测 | 信任边界核心（R3 确认） | fresh front 同步偏移污染无法被确定性条件检测；以 xtquant front 为权威 oracle 接受 |
| trigger 粒度（增量重基） | **已修复（阶段6A）** | rebase 永远传全量 ex_dates，增量轮次不再丢历史除权日 |
| raw 全市场覆盖率未验证 | 生产启用前必做 | 预检仅抽样 000012/510300；全市场 5202+1605 差异率待扩 |
| daemon 其它 Tushare 因子路径 | 已修复（PR #8） | _fetch_adj_factor 已用 resolve_ts_codes |
| rate_limiter 参数残余 | 保留不用 | refresh 的 rate_limiter 参数保留但不再调用 |
| front 修正需基准一致 | 已记录 | 真实数据下重锚常 BLOCK，front 修正需合成/基准一致场景 |

## 7. 文档同步

- `README.md`：QFQ 章节新增"主动因子刷新与 detector degraded"
- `docs/strategy_toolbox.md`：第 4.7 节因子刷新契约
- `docs/prompt_engineering.md`：5.x QFQ 编排铁律
- `docs/qfq-resident-runbook.md`：运维手册（本文档同期）
- `docs/qfq-resident-orchestrator-fix-report-20260729.md`：本报告

## 8. Git 历史

- PR #6：常驻 QFQ 编排器核心正确性收口 + 因子刷新 ts_code 转换（22 文件）
- PR #7：QFQ staging 演练脚本（核心守恒闭环验证通过）
- PR #8：front 修正验证 + daemon ETF 后缀推导修复

## 9. 生产启用前置

生产启用必须满足（详见 `docs/qfq-resident-runbook.md` 第 2 节「部署门控」）：
1. 全量回归 0 failed
2. R3 staging 验收 committed>0 + 守恒通过
3. trigger 粒度修复测试通过（阶段6A）
4. miniQMT 可用 + trade_calendar 完整
5. 全市场 raw 准入预检（5202 股票 + 1605 ETF）
6. dead_letter 清零 + pending_backfill 无超期
7. 至少一个真实除权事件 committed
8. 多日常驻运行稳定
9. **取得用户明确部署确认**

当前 `enabled=false`，未启用生产闭环。

## 10. 拟同步文件清单（阶段6，待用户确认后 commit/push）

> 铁律：框架层代码 + 文档须同 PR 同步。以下为 `feat/qfq-authoritative-rebase` 分支拟同步范围。
> **未 push，enabled=false**。

**6A 代码 + 测试**
- `quantstudio/pipeline/qfq_resident_orchestrator.py`（阶段6A：rebase 传全量 ex_dates）
- `tests/test_qfq_resident_orchestrator.py`（+3 用例：增量全量 / 多 trigger 合并 / rebase BLOCK）

**6B 文档（本轮更新）**
- `docs/qfq-resident-runbook.md`（§1.5 三模型 + §2 部署门控）
- `docs/qfq-resident-orchestrator-fix-report-20260729.md`（阶段六 + R3 + §5/§6 更新）
- `docs/superpowers/specs/2026-07-29-fresh-authoritative-rebase-design.md`（§5 R1-R4 完成 + §7 风险）

**QFQ rebase 线程既有文件（同分支一并同步，详见 `docs/qfq-rebase-branch-separation.md`）**
- `quantstudio/pipeline/qfq_reanchor_engine.py`、`qfq_fresh_capture.py`（R1/R2/阶段4）
- `docs/qfq-rebase-precision-validation-20260729.md`、`docs/qfq-raw-admission-preflight-20260729.md`
- `tests/test_qfq_authoritative_rebase.py`、`test_qfq_reanchor_batch2.py`
- `scripts/preflight_raw_admission.py`、`scripts/validate_qfq_rebase_precision.py`
- `tests/fixtures/qfq_raw_admission/`、`tests/fixtures/qfq_rebase_precision/`、`docs/evidence/`
- `docs/qfq-rebase-branch-separation.md`

**R3 验收脚本与证据（可选入库）**
- `scripts/qfq_rebase_r3_staging.py`（staging 验收脚本）
- `output/qfq_rebase_r3_20260730/*.json` / `*.txt`（结论证据；~810MB staging 数据不入库）

**明确排除（不入库）**：`data/staging_qfq_rebase_r3_20260730/`（~810MB 运行时 DB）、
`bench_artifacts/` 临时文件、`data/quantstudio.zip` 等大文件（铁律）。
