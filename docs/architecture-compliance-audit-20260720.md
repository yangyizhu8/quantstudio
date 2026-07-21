# QuantStudio 架构合规审计（2026-07-20）

本报告按项目目标逐项核验数据管线、PyQt GUI、数据适配层、PTrade API 兼容层与本地回测框架。结论基于源码审计、配置 lint、198+ 自动化测试、真实 5GB Canonical DuckDB 审计和 PTrade 导出样本对照。

## 1. 统一数据管线与三种运行模式

所有运行入口最终进入 `ResidentCollector.execute_task()`，并固定执行：

```text
SourceAdapter.fetch_table
  -> FieldAligner.align
  -> PreIngestValidator.validate
  -> DuckDBWriter.write (idempotent upsert)
  -> source watermark
  -> DataQualityAuditor
```

运行模式只改变日期范围，不改变处理链：

- `full_range`：配置起止日期，全量幂等 upsert；
- `incremental`：从安全水位下一日到当前日期；
- 常驻增量：定时调用同一个 `execute_task(..., mode="incremental")`。

任务级 `rate_limit`、`retry` 和 `call_timeout` 已统一应用到缓存 adapter；特殊的 Tushare 按交易日路径也走同一限流/重试实现。数据源回退链只接受 `adapter.supports_task(table, freq)` 明确声明支持的实现。

## 2. GUI 与质量检查

- GUI 的全量、增量、常驻按钮复用同一 collector 公共入口；
- GUI 数据源能力展示由 adapter 运行时能力矩阵生成，不再维护重复硬编码矩阵；
- GUI 回测页显式提供 `close/open/next_open` 撮合口径，并传入同一 `BacktestEngine`；
- 统一质量报告覆盖：schema/必填/regex/enum/数值约束、OHLC、单位、复权、频率网格、PIT、未来时间、主键、数据源溯源、水位、批次阶段守恒、写入守恒、失败批次、隔离区完整性与积压。

## 3. 策略、数据和交易实现解耦

策略只实现 PTrade 生命周期并调用注入 API：

- `initialize`
- `before_trading_start`
- `handle_data`
- `after_trading_end`（可选）

`StrategyRunner -> PtradeAPI -> DataProviderRegistry -> provider` 隔离了策略与 DuckDB/数据源。`StrategyIsolationGuard` 会拒绝策略直接导入数据库驱动、pipeline/provider 内部模块或直接读取 CSV/Parquet/SQL 文件。

证券代码字典统一支持 `.SS/.SZ/.XSHG/.XSHE/裸码` 别名；portfolio 持仓以 PTrade 标准后缀暴露，避免策略写市场后缀转换逻辑。交易成本、比例/固定滑点、T+1、涨跌停、整手、资金校验和订单状态均在引擎/API 层处理。

## 4. 修复边界

数据、复权、PIT、状态字段问题应修复在：

- adapter / alignment rules / `FieldAligner`；
- `PreIngestValidator` / writer / quality auditor；
- provider / `PtradeAPI` / `BacktestEngine`。

策略隔离守卫和 provider contract 测试用于阻止底层修复向策略文件泄漏。新增数据源时只需实现四类 provider 或 source adapter contract，现有策略不修改。

## 5. PTrade 对齐实测

### ETF 动量（真实 PTrade 导出）

报告：`output/compare_ETF_momentum_post_metric_fix.json`

- verdict: **PASS**
- L1 信号一致率：100%
- L2 末态净值/回撤/夏普偏差：接近 0
- L3 持仓重叠率：100%
- L4 有效费率偏差：0.01%

### 小市值（真实 PTrade 导出）

报告：`output/compare_smallcap_post_metric_fix.json`

- verdict: **CLOSE**
- L1 信号一致率：72.3%
- L2 末态净值偏差：0.26%；回撤偏差：1.71%；夏普偏差：0.02
- L3 持仓重叠率：95.2%
- L4 有效费率偏差：0.25%

小市值剩余信号差异集中在跨源流通市值排名边界和少数交易日的候选切换；本地与 PTrade 的最终表现及持仓高度接近，但不能宣称所有策略、所有数据源达到逐笔完全一致。

## 验证结果

- 全量测试：201 passed
- GUI offscreen smoke：8 个 Tab 全部成功实例化
- ConfigLint：0 errors / 0 warnings
- 真实 Canonical 库：264 项检查，error=0；存在历史 warning（旧数据缺 `data_source`、复权连续性、隔离区积压）
- PTrade 样本导入：交易、持仓、日志三件套测试通过

## 尚需运维处理的历史数据债务

这些不是策略代码问题，后续应在底层维护：

1. 旧批次部分表的 `data_source` 为空；新写入已自动 stamp，历史行可离线回填。
2. 隔离区仍有历史 `pending_repair` 积压，应按规则 review/replay/archive。
3. 部分复权锚点和收益连续性为 warning，需要结合供应商官方复权口径做跨源复核。
4. 小市值跨源排名边界尚未达到 L1 85% 硬门槛；若要求 PASS，需要同源 PTrade 数据或对流通市值口径继续校准，修复位置应在 provider/aligner/calibration，而不是策略文件。
