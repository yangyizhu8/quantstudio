# QFQ 常驻编排器修复报告（2026-07-29）

> **状态**：常驻 QFQ 编排器核心正确性修复全部落地并通过审核（PR #6/#7/#8）。
> staging 演练首轮通过 + front 修正语义验证通过。
> **生产配置仍关闭**：`qfq_orchestrator.enabled=false`、`factor_refresh_enabled=false`。
> 全量回归 **1709 passed**（PR #6 时），后续 PR #7/#8 无新增失败。

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

## 6. 已知风险

| 风险 | 状态 | 说明 |
|------|------|------|
| 部分码失败不 degraded | 保持现状（风险2） | 失败码可能用旧快照；是否升级另立审核 |
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

生产启用必须满足（详见 `docs/qfq-resident-runbook.md` 第 2 节）：
1. 全量回归 0 failed
2. staging 演练通过
3. front 修正验证通过
4. miniQMT 可用 + trade_calendar 完整
5. **取得用户明确部署确认**

当前 `enabled=false`，未启用生产闭环。
