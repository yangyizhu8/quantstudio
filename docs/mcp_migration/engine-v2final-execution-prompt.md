# 新会话执行提示词：ETF/股票回测除权补正（引擎方案 v2-final，阶段 1 + 阶段 2）

请严格按以下要求执行，不要省略步骤，不要改动方案外内容。

## 任务
在本地 QuantStudio 回测框架的撮合引擎中实施"ETF 除权日送股/份额折算/合并补正 + ETF 现金分红入账"，修复 raw 撮合下 ETF 除权日送股缺失导致的净值虚假腰斩（-50%）问题。

## 必读文档（按优先级）
1. `D:\miniQMT策略实盘\QuantStudio\docs\mcp_migration\etf-split-factor-derived-fix-plan.v2-final.md` —— **唯一实施依据**（含 §十二 修订索引：P0-1/P0-2/P1-3/P1-4/P1-5 的落点与测试编号）
2. `D:\miniQMT策略实盘\QuantStudio\docs\mcp_migration\etf-split-factor-derived-fix-plan.review.md` —— 背景审核意见（含全宇宙实证数据：1575 次现金分红带误触发、189 次非 0.5 倍数折算、725 次份额合并）
3. `D:\miniQMT策略实盘\QuantStudio\docs\mcp_migration\pipeline-etf-dividend-integration-plan.md` —— 数据管线方案（阶段 2 依赖其产出的 etf_dividend 表；若表尚未落地则阶段 2 no-op）
4. `D:\miniQMT策略实盘\QuantStudio\AGENTS.md` —— **铁律必须遵守**（见下方"流程红线"）

## 工作目录
`D:\miniQMT策略实盘\QuantStudio`

## 实施内容

### 阶段 1（必须完成）：ETF 除权补正（preClose 反推，仅 ETF）
1. 在 `quantstudio/backtest/backtest_engine.py` 新增方法 `_apply_factor_derived_split`，**代码以 v2-final §3.5 为准逐字实施**（含 P0-1 already_handled 裸码统一、P0-2 现金分红带 1.01~1.10 跳过+WARN、P1-3 ≥1.10 吸附未命中按原值+WARN、P1-4 ratio<0.99 份额合并对称处理、P1-5 is_etf 门控）。
2. 主循环调用点：行 527（`curr_data = self._get_daily_data(day)`）之后、`prices`（行 534）之前插入调用。
3. **禁止改动**（v2-final §七 不变项）：`_apply_corporate_actions`、match_prices、prices 估值、pctChg 涨跌停判断、get_history/get_price fq='pre' 路径、align/_apply_qfq 签名。

### 阶段 2（条件实施）：ETF 现金分红入账
- 先检查 DuckDB `etf_dividend` 表是否存在（`data/quantstudio.db`，`SHOW TABLES`）。**存在**则按 v2-final §3.6 实施：`duckdb_data_access.py` 新增 `query_etf_dividends(date_ms)`、provider 透出、引擎 `_apply_etf_cash_dividends`（div_cash × volume **全额入账**，公募基金分红免税口径；与股票 20% 短持税区分）；**不存在**则跳过并在汇报中注明（no-op 设计，不阻塞阶段 1）。

## 测试（必须新增并全部通过）
- 新建 `tests/test_factor_derived_split.py`，覆盖 v2-final §六 的 **10 个用例**（含：QMT 格式 already_handled 不重复送股、现金分红带跳过无幽灵送股、2.0462 原值送股、0.5 合并对称处理、0.9718 WARN+跳过、净值连续性 |净值变化率−pctChg|≤0.1pp、avg_cost 调整、ETF动量回归、股票零回归）。
- 跑既有回归套件（`python -m pytest tests/ -x -q` 或按仓库既有方式），确认无新增失败。

## 验证（必须完成并汇报数据）
1. **159995 精确对齐**：构造/回放 2026-07 场景——07-01 买入 30100 股（close 3.321）→ 07-07 除权（prev_close 3.009 / preClose 1.505 → ratio 1.9993 吸附 2.0）→ 送股 60200、avg_cost 3.321→1.661 → 07-16 卖出 60200×1.317=79,283；除权日净值变化 -0.23%（非 -50%）。
2. **ETF动量策略回测**：`quantstudio/backtest/strategies/ETF动量.py`（13 只 ETF，单持仓轮动），与 PTrade 结果 -23.85% 对比，修复后差异 **<1%**（阶段 1 先验证拆分项）。
3. **股票零回归**：既有股票策略黄金基线逐项一致（ETF-only 门控下股票路径零变化）。

## 流程红线（AGENTS.md 铁律）
- 本任务属**框架层行为变更**：**本地修复完成后必须汇报，未经用户明确确认禁止 git commit/push**（禁止推送 GitHub）。
- GitHub 同步（仅在用户确认后）：双仓库（quantstudio-plus + quantstudio 多 push URL），同步内容必须包含：框架代码、`README.md` 相关章节、`docs/strategy_toolbox.md`、`docs/prompt_engineering.md` 中涉及本修复的表述。
- 禁止以"性能优化"名义混入行为变更；禁止扩大修改范围。

## 汇报要求
完成阶段 1（及阶段 2 条件实施）后输出：
1. 改动文件清单（含行号/新增方法）；
2. 测试结果（10 例 + 回归统计）；
3. 159995 场景验证数据（送股、avg_cost、净值连续性）；
4. ETF动量回测净值对比（修复前/修复后/PTrade -23.85%）；
5. 股票回归结论；
6. 阶段 2 状态（etf_dividend 表是否存在、是否实施）；
7. 待用户确认的 GitHub 同步清单。
