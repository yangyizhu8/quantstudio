# ETF 动量 PTrade 对齐回归排查与修复报告

> 日期：2026-07-20  
> 影响策略：`quantstudio/backtest/strategies/ETF动量.py`  
> 影响入口：PyQt 与 CLI（共用同一 BacktestEngine）  
> 状态：已修复并通过真实 PTrade 样本对照

## 现象

同一参数配置下，ETF 动量策略从已对齐结果漂移为：

- 最终资金：49,064.37
- 总收益率：-50.94%
- 最大回撤：62.18%
- 成交笔数：31

异常结果目录：

- `output/backtest_results/20260721_024759_ETF动量`
- `output/backtest_results/20260721_025131_ETF动量`

两次异常结果完全一致，且配置与之前对齐运行一致：10万元、ETF费率、无滑点、close 撮合。

## 根因

回归来自持仓代码后缀和容器 membership 语义变化，不是 PyQt 参数、策略文件或成本参数变化。

此前为了让策略侧所有代码后缀“互通”，运行时把：

- `context.portfolio.positions`
- `get_positions()`

改成了以 `.XSHG/.XSHE` 为 key 的 alias-aware `CodeDict`。

真实 PTrade 样本显示：

- 策略/持仓/CSV 容器使用 `.SS/.SZ`；
- 内部订单日志可能显示 `.XSHG/.XSHE`；
- Python 的 `code in context.portfolio.positions` 是普通 dict 精确 membership；
- 跨后缀查询兼容应由 `get_position()`、`DataDict`、`CodeDict` 等 API 提供，不能改变持仓容器本身的 membership。

ETF 动量策略把 `g.last_traded` 保存为股票池中的 `.XSHE/.SS` 混合代码，并直接检查：

```python
if g.last_traded in context.portfolio.positions:
```

真实 PTrade 中，深市持仓 key 为 `.SZ`，`159870.XSHE in positions` 为 False。策略因而在后续日子反复尝试下单，但没有成功换出已持仓的 159870，最终保持到回测结束。该行为正是平台真实导出样本所记录的结果。

把持仓容器改成 alias-aware 后，上述判断变为 True，策略开始实际轮动 15 次，最终在 2026-07-07 附近遭遇数据价格变化，产生约 50% 单日净值下降，结果漂移至 -50.94%。

## 修复

1. `Portfolio.positions` 恢复普通 dict，不再使用 alias-aware CodeDict；
2. `_get_ptrade_positions()` 恢复 `.SS/.SZ` 持仓 key；
3. `PtradeAPI._to_ptrade_code()` 和 DuckDB 历史返回 key 恢复 `.SS/.SZ`；
4. `get_position()`、行情 `DataDict` 和历史 `CodeDict` 继续支持跨后缀查询；
5. 建立 `security_code_rules.py` 权威代码分类/后缀模块，统一北交所和其他证券类型；
6. 新增 ETF 动量精确 membership 回归测试；
7. 更新接口契约，明确“API 后缀互通”和“持仓容器精确 membership”的边界。

## 修复后结果

结果目录：`output/backtest_results/20260721_034135_ETF动量`

- 最终资金：87,752.56
- 总收益率：-12.2474%
- 最大回撤：23.3294%
- 成交笔数：3
- 持仓末态：159870

真实 PTrade 对照报告：

`output/compare_ETF_momentum_final_regression_fix.json`

```text
Verdict: PASS
L1 信号一致率: 100%
末态净值偏差: 0.0044%
最大回撤偏差: 0.000000045%
夏普偏差: 0.00000001
L3 持仓重叠率: 100%
L4 成本偏差: 0.0064%
```

## 其他策略回归

小市值真实 PTrade 样本重新对照：

`output/compare_smallcap_after_etf_suffix_regression_fix.json`

结果保持原有水平：

```text
Verdict: CLOSE
L1: 72.31%
末态净值偏差: 0.2635%
最大回撤偏差: 1.7128%
L3 持仓重叠率: 95.24%
L4 成本偏差: 0.2496%
```

没有出现因本次修复造成的新漂移。

## 自动测试

```text
证券代码/ETF/持仓专项：48 passed
ETF 精确 membership 回归专项：26 passed
最终全量：243 passed in 12.53s
```

## 永久回归约束

- 禁止把 `context.portfolio.positions` 改成 alias-aware 容器；
- 禁止用“统一后缀体验”改变真实 PTrade 策略控制流；
- API 查询兼容和原生容器语义必须分别建模；
- 每次修改代码后缀、Position 或 Portfolio 时，必须运行 ETF 动量真实样本 Fidelity 对照。
