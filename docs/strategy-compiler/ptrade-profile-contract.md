# PTrade Profile 契约

## 1. Profile 决定的内容

- Profile ID 和版本；
- 策略代码后缀；
- import/API 注入方式；
- 生命周期与回调签名；
- 公共 API 白名单；
- 行情/财务字段权限；
- 定时任务能力；
- 支持的频率与撮合能力；
- 严格公共 API 模式或本地优化模式。

## 2. 默认 Profile：`ptrade-default-v1`

- 使用 QuantStudio 当前 PTrade 兼容层与目标平台公共 API 子集。
- 生命周期：`initialize`、可选 `before_trading_start`、`handle_data`、`after_trading_end`。
- PTrade 公共 API 仍由平台提供，但计算模块不视为注入对象；源码使用 NumPy/pandas 时必须显式 import。Renderer 不写死 `from ptrade_api import *`，且禁止数据库/框架内部 import。
- 生成代码不得访问 DuckDB、Provider 内部模块或本地文件。
- 未提供券商与版本时，只声明符合默认 Profile，不承诺适配全部 PTrade 部署。
- 分钟回调/精确定时在 Engine/Profile 未验证前标为 BLOCKED 或 PLATFORM_DEPENDENT。

## 3. 可移植性检查

PTrade Renderer 输出必须通过：语法、生命周期、API 白名单、禁止本地扩展 API、禁止文件/数据库访问、字段权限、代码后缀、双版本参数一致性检查。

## 4. Profile 演进

不同券商差异通过显式 Profile ID/版本表达，不使用隐藏条件。任何 API 白名单、回调、字段权限或撮合含义变化必须提升 `ptrade_profile_version`。

## 2026-07-25 ETF universe capability split

- `get_etf_list()` is trading-context only in the strict PTrade profile and is blocked in PTrade backtest source.
- `get_etf_list_local()` and `get_history_batch()` are registered QuantStudio-only APIs and are blocked whenever `targets` contains `ptrade`.
- Dual ETF backtests use a customer-confirmed static whitelist. Local-only ETF backtests may use the PIT local universe API.

## Candidate boundary

A QuantStudio `__candidate` file is never a PTrade artifact. PTrade formal output is generated only in R6 after hash-bound R5 PASS and is revalidated against the public profile.

## 2026-07-26 PTrade Profile 1.8.0 get_history return-shape closure

- `get_history(..., is_dict=True)` 登记显式 `return_contract`：mapping item 可能是 pandas DataFrame / NumPy structured array / recarray；`item[field]` 可能是 Series 或 ndarray。
- 可移植规则：提取字段必须先经 `np.asarray(...)` 归一化再参与数值计算；对 history item/字段的无保护 pandas 专属访问（`.values`/`.iloc`/`.loc`/`.to_numpy()`/`.columns`/`.index`/`.empty`）由 agent-first Validator 一律 BLOCK（`PTRADE-HISTORY-SHAPE-UNSAFE` / `PTRADE-HISTORY-PANDAS-ONLY` / `PTRADE-HISTORY-NORMALIZATION-MISSING`）。
- fixture 分层明确区分：legacy renderer runtime fixture（仅覆盖旧 Jinja 产出）与 agent-first source runtime-shape fixture（`scripts/validate_runtime_shapes.py`，对策略自带 helper 执行 DataFrame/Series/structured/recarray/空/缺字段/NaN-inf 真实形状）。未实际运行 agent-first fixture 不得声称"structured-array fixture 已覆盖"。
- 静态验证术语：R4 报告区分 `profile_validation_status`（静态契约）与 `runtime_validation_status=NOT_VERIFIED`、`deployment_status=NOT_DEPLOYABLE`；静态 PASS 不得表述为"PTrade 可上线/已验证/部署通过"。真实券商运行失败后由 `scripts/retire_ptrade_runtime_evidence.py` 将旧 R4 PASS、candidate、staging 与哈希一并置为 STALE/RETIRED。

## 2026-07-26 PTrade Profile 1.7.0 stock-core signature closure

- Registered exact signatures and return-shape notes for `set_benchmark`, `run_daily`, `get_Ashares`, `get_index_stocks`, `get_stock_status`, `get_positions`, `get_position`, `get_trade_days`, and `get_fundamentals`.
- Dual/PTrade validation is fail-closed: every `components.required_apis` entry and every external top-level source call must be profiled. Missing entries are `MISSING_REUSABLE_API` at R1 and `BLOCK` at R4; they cannot be waived as execution approximations.
- Portable `get_stock_status` accepts `ST`, `HALT`, or `DELISTING`. `DELISTING_SORTING` is a `filter_stock_by_status` filter type and a local backward-compatible alias only.
- Static Profile PASS proves conformance to the registered default subset, not successful execution on every broker/IQEngine deployment.

## 2026-07-26 Agent-first execution-price contract（2026-08-14 修订：raw 口径）

- The selected QuantStudio backtest engine profile uses `raw_trade_price` for matching, fills, cash, valuation, `data[code].price`, and BarData OHLC — revised 2026-08-14 by real-PTrade match-price audit (daily fill = T-day raw close 5/5; minute fill = bar raw close 6/6; valuation last_price = raw close).
- Agent-first designs must declare `signal_price_adjustment=pre` (front-adjusted signal OHLC) and `execution_price_basis=raw_trade_price`; `pre_adjusted_price` is rejected (superseded 2026-08-14).
- Signal history calls keep literal `fq='pre'`; source_import injects `fq='pre'` on PTrade conversion because PTrade's own default fq is unadjusted.
- PTrade public-API validation remains a portability gate and does not redefine the broker runtime's internal valuation basis.

## 2026-07-27 PTrade Profile 1.9.0 industry + PIT contract closure (F1-F6)

- `get_industry(security, date=None)` 正式登记（sw_l1 返回形状 + PIT 契约）：本地严格 as-of 当前回测日期查 SW2021 正式成员表；无有效归属返回 None；legacy `sw_industry` 不再作为正式数据。PTrade 平台行业分类版本按部署核实（`PTRADE_RUNTIME_UNVERIFIED`）。
- `get_index_stocks` 声明严格 as-of PIT/date 契约（F3）：非历史并集、无未来快照、无快照返回空、partial 快照不计完整 PIT；PTrade 平台成分历史深度按部署核实。
- `get_stock_info` 声明股票+ETF 统一元数据（F2）：stock_type/上市/退市日（YYYY-MM-DD）、fallback 显式标记；本地 ETF 元数据支持 ≠ PTrade 真实 ETF 支持（未验证）。
- `callback_basket` 是 QuantStudio 引擎语义模式（`0.4.0-next_open_basket`），仅 daily-bar-v1 + next_open；PTrade 渲染不得输出 basket 专属构造；PyQt 已透出 `rebalance_mode`（默认 legacy）。
- `get_history` 多表路由（股票/ETF/指数含申万行业指数→统一 index_daily）与 `fq='pre'` 复权列缺失回退原始价为引擎侧保证（F5/F6），不改变策略可见契约。

## 2026-07-27 PTrade Profile 1.10.0 get_stock_exrights registration

- `get_stock_exrights(security, date=None)` 正式登记。返回 DataFrame（date 索引，8 列 PTrade 兼容：allotted_ps/rationed_ps/rationed_px/bonus_ps/exer_forward_a/exer_backward_a/bexer_backward_a/b），无数据返回 `None`。
- Contexts: research/backtest/trade。portable usage 必须显式传 `date`；`date=None` 返回 `None`（底层查询需具体日期）。
- 源表 `stock_dividend`（tushare 权威源，`allow_fallback=false`）。schema 兼容旧列 `cash_div`，自动检测 `cash_div_before_tax` 存在性。
- 受 Tushare 接口频率限制（~200 次/分钟），批量调用需间隔。
- PTrade 平台除权除息字段映射与字段准确性按部署核实（`PTRADE_RUNTIME_UNVERIFIED`）。

## 2026-08-11 平台读写格式不对称实证（ETF动量平台对比）

> 来源：`私募工作文件/QuantStudio本地策略转ptrade模块开发/09-ETF动量平台对比报告.md`
> （2026-08-11 真实 PTrade 平台回测实证，对照组=平台原版代码，实验组=source_import 转换产物）
> **草稿状态：待用户确认后随框架层流程同步 GitHub（未 commit/push）。**

- **新事实（有平台实证）**：PTrade 平台**订单/成交显示代码**用 `XSHG/XSHE` 风格
  （日志实证：`股票代码：515880.XSHG`、`159870.XSHE`），但**策略上下文
  （`context.portfolio.positions` 容器 key 匹配、`g.last_traded in positions` 判断）
  需用 `.SS/.SZ` 规范后缀**——读写格式不对称。
- **后果（实测）**：策略代码混用 `.SS` + `.XSHE` 时，`.XSHE` 标的的持仓 key 匹配失效：
  "继续持有/卖出"分支不可达 → 委托数量为 0 取消 → 换仓时资金不足下单失败 →
  死持仓单票 6 个月（平台原版 3 笔成交 vs 转换版 30 笔完整轮动；转换版 0 WARNING）。
- **契约要求**：PTrade 策略源码中代码后缀必须统一 `.SS/.SZ/.BJ`（禁止混用 `.XSHE/.XSHG`）。
  source_import 的 `NORM-CODE-SUFFIX` 规则（`.XSHG/.XSHE/.SH → .SS/.SZ`）为此实证背书。
- 遗留标注：`.SH` 同族后缀在真实平台的直接实证仍无（`PTRADE_RUNTIME_UNVERIFIED`），
  由 .XSHE 间接实证支撑（同族 key 匹配失效机理）。

## 2026-08-24 PTrade Profile 1.11.0 get_fundamentals contract closure (P-D10)

- `get_fundamentals(security, table, fields=None, date=None)` 与 `get_fundamentals_batch(sec_list, table, fields, date)` 统一登记为 **DataFrame 契约**：返回 `pd.DataFrame(index=code, columns=fields)`，空结果返回空 DataFrame（不抛异常）。
- 本地→平台字段名映射由转换器在注入阶段处理（例：`growth_ability.or_yoy → operating_revenue_grow_rate`），策略源码仍按本地字段名消费；平台无等价字段时返回空 DataFrame 并触发 `QS_SHIM_FIELD_MISSING` 显性警报（非静默空）。
- 日期列 `end_date`/`publ_date` 在平台侧为 `'YYYY-MM-DD'` object，转换器归一为数值 YYYYMMDD 以保持与本地 `fin_indicator` 排序语义一致。
- 该契约通过 `SHIM_CONTRACT_REGISTRY` 机器门禁、`test_ptrade_contract_compliance.py` 同构矩阵与运行时 `_qs_shape_check` 三道防线守住。平台实测（week10 2026-07）双端漏斗 L4≈10%×L3v、L5=30、R_selected=10，验证通过。
