# 验收证据：get_fundamentals 三大报表取数接线（D4 修复）

- 日期：2026-08-28
- 方案：docs/fundamentals-statement-wiring-design.md（v2，ZCode 必改①/② + 随实施 3 项已并入）
- 改动：duckdb_data_access.py（+query_statement_table）/ duckdb_provider.py（+3+1 报表方法）/ ptrade_api.py（else 分支接线）/ tests（+矩阵+验收）/ skill inspect_capabilities（+fundamentals_contract_wiring 维度）

## 1. 根因与修复

- **根因**：get_fundamentals 对 income/balance/cashflow 恒返回空（else 分支空表）；provider 缺报表查询。DuckDB 表/契约/采集均在，缺接线层。R1 盘点遗漏（表存在≠API 可取）。
- **修复**（v2）：
  - 必改①：报表查询返回 **PIT 窗口全部报告期行**（start_year/end_year/report_types 生效，默认合并报表）——支撑 F-Score D3 同比两期取数
  - 必改②：契约接线测试矩阵（遍历 FUND_TABLES 全表 API 层抽查非空，未接线即红）+ skill R1 探查升级（表存在≠API 可取固化为 capability 维度）
  - 随实施：返回列名按契约归一（publ_date 数值时间戳）/ report_types 默认合并 / 缺列 log.warning 不静默

## 2. 数据补齐（连带）

- **balance_statement 全字段重采**：历史按旧映射只采了 total_assets——按当前 column_map（18 项）全量重采至 167,486 行；total_current_assets 163,546 非空（原 0）→ F-Score 流动比率/负债率指标数据齐。
- 过程：staging（prepare→run-task→audit→promote）→ 备份 quantstudio.db.bak.balance_refill → 生产复检 PASS。

## 3. 验收结果

| 项 | 结果 |
|---|---|
| 同比双期（income 2024+2025，600519） | ✅ 7 行多期，非仅最新 |
| balance 流动比率字段（tca/tcl/tl） | ✅ 全非空 |
| PIT 双边界 | ✅ ann_date==查询日可见 / 2019-01-05 as-of 无 2019 年报（无未来函数）|
| 缺列告警 | ✅ log.warning + 缺列返回 |
| **契约接线矩阵**（必改②）| ✅ 9/9：wired 7 表 / unwired 0 / unsourced 2（debt_paying/operating 数据源整体缺失，豁免留痕）|
| **capability 新维度**（必改②）| ✅ fundamentals_contract_wiring = LOCAL_DATA_READY（wired 7 / unwired 0 / unsourced 2）|
| 全量回归 | ✅ 158 passed（149 既有 + 9 矩阵）；验收 5/5 另批通过 |

## 4. 已核验事实

- ptrade_api else 分支空表（L824-825）✅ → 已接线三大报表
- FIN_TABLES 契约含三表字段；DuckDB 三表 12.8万+ 行；adapter 采集在 ✅
- fin_indicator 无 debt_paying/operating 所需字段（current_ratio/turnover）→ 2 表无源豁免留痕 ✅

## 5. skill R1 探查规则升级（必改②第二道防线）

inspect_capabilities 新增 `fundamentals_contract_wiring` capability：
- 遍历 FUND_TABLES 全表经 get_fundamentals API 层实测非空（wired/unwired/unsourced 三态）
- 未接线表 → 该 capability DATA_BLOCKED（而非等某策略 R3 炸）
- 证据详情含完整 wired/unwired/unsourced 清单

## 6. 后续登记

- DR-3（get_fundamentals 三大报表未接线）→ closed（修复+验收）
- 数据债：debt_paying_ability / operating_ability 契约声明但无数据源（fin_indicator 缺字段）——豁免留痕，数据源接入时移除豁免并接线
- balance_statement 历史采集字段残缺根因（旧 alignment_rules 缺映射）已由全量重采解决；DR-2 增量链路仍待 daemon 修复