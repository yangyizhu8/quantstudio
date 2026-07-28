# 财务成长指标与除权分红 API 框架正确性修复 — 报告

> **会话基准日期**：2026-07-27 | **工作站文件时间**：2026-07-28 | session date: 2026-07-27 / file timestamp: 2026-07-28
> **状态**：W2-0.5 安全补正完成，等待 W2 staging 全量回填

## 变更摘要

为支持 A 股多因子策略（股息率/PEG/ROE/营收增长/净利增长），修复了以下框架能力：

### 1. fin_indicator 增长字段

| 字段 | 类型 | 单位 | 说明 |
|------|------|:--:|------|
| `or_yoy` | DOUBLE | % | 营收同比增长率（Tushare `or_yoy`） |
| `tr_yoy` | DOUBLE | % | 营业总收入同比增长率（Tushare `tr_yoy`） |
| `update_flag` | INTEGER | — | 0=初版公告，1=修订版（PIT 去重） |
| `diluted_eps` | DOUBLE | — | 稀释 EPS（Tushare `dt_eps`，与 eps 独立列） |

### 2. stock_dividend 标准化

| 字段 | 说明 |
|------|------|
| `cash_div_before_tax` | 税前每股现金分红（Tushare `cash_div_tax`） |
| `cash_div_after_tax` | 税后每股现金分红（Tushare `cash_div`） |
| `stk_bo_rate` | 送股比例 |
| `stk_co_rate` | 转增比例 |
| `div_proc` | 分红进度（仅入库"实施"记录） |

### 3. get_stock_exrights API

| 参数 | 说明 |
|------|------|
| Signature | `get_stock_exrights(security, date=None)` |
| Contexts | research/backtest/trade |
| Return | DataFrame (date 索引, 8 列) 或 None |
| date 要求 | **portable usage 必须显式传 date** |
| bonus_ps | 税前现金分红 (cash_div_before_tax) |
| allotted_ps | 股票分红 (stk_div)，不含送股/转增 |

### 4. 公司行为引擎

- **税务政策**：`pre_tax × 0.80`（税前金额 × 20% 税率）
- **数据源**：优先 `cash_div_before_tax`，回退 legacy `cash_div`
- **事件结构**：保留 `cash_div_per_share`，新增 `cash_div_before_tax`/`cash_div_after_tax`/`tax_policy`

### 5. PIT 修复

- 不同 `ann_date` 全部保留（不再只保留最大）
- 相同 `(code, end_date, ann_date)` 时 `update_flag=1` 优先
- `get_fundamentals(date=T)` 只读 `ann_date <= T`

### 6. 权威源锁定

| 表 | 权威源 | allow_fallback |
|------|------|:--:|
| `fin_indicator` | tushare | false |
| `stock_dividend` | tushare | false |

### 7. Schema 兼容

所有 DataAccess 查询支持迁移前/后双 schema，自动检测列存在性并安全回退。

## 修改文件（14 tracked）

```
config/alignment_rules.json
config/collector_tasks.json
quantstudio/backtest/backtest_engine.py
quantstudio/backtest/providers/duckdb_data_access.py
quantstudio/backtest/providers/duckdb_provider.py
quantstudio/backtest/ptrade_api.py
quantstudio/pipeline/aligner.py
quantstudio/pipeline/config_lint.py
quantstudio/pipeline/daemon.py
quantstudio/pipeline/quality_audit.py
quantstudio/pipeline/sources/tushare_adapter.py
quantstudio/pipeline/validator.py
quantstudio/pipeline/writers.py
skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json
```

## 测试

56 项测试全部 PASS（不含 staging 和 failure gate 新测试）。

## 数据状态

- **正式库**：旧 schema（fin_indicator 11 列，167,028 行），未迁移
- **增长字段**：np_yoy/or_yoy 全 NULL（等待 W2 回填）
- **分红标准化**：旧 cash_div 列存在，口径不明（等待 W2 重建）

## W2 待执行

1. Schema 迁移（`_migrate_add_columns` 幂等）
2. Staging prepare + fin_indicator 回填
3. Staging stock_dividend 回填
4. 质量审计（authority/growth/dividend）
5. Promotion（原子替换）
