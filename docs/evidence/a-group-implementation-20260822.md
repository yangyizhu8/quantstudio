# A 组实施与验收证据 — PTrade 平台对齐（A1 拆单 / A2 统一链 / A3 日期归一）2026-08-22

- 关联方案：`docs/source_import-ptrade-order-split-design.md` + 治理总方案（PTrade 平台对齐 v4）
- 前置门：费用实证 `docs/evidence/a1-commission-probe-20260822.md`（PASS：平台最低佣金≈5 元/笔，与本地同构）
- 审计状态：方案 v4 复审通过（R1-R5 + 三条补充并入），本证据为 A 组实施验收

## 1. 实施文件与改动

| 文件 | 改动 | 内容 |
|---|---|---|
| `quantstudio/strategy_compiler/source_import.py` | +注入模板 | `_QS_ORDER_SPLIT_EXT`（A1 拆单 + A2 统一链 + current_price 包装）、`_QS_DATE_NORM_EXT`（A3 get_trade_days/listed_date 归一）、门控 `_source_uses_order_api`/`_source_uses_date_api`、`_inject_all` 挂接 |
| `quantstudio/backtest/ptrade_api.py` | +本地同构 | `_qs_split_order`/`_qs_last_close_lookup`/`_QSLastCloseState`（PIT 缓存）/`_qs_history_record_wrapper`（get_history 链记录）/`current_price` 统一链绑定（原 API 直调语义不变） |
| `quantstudio/backtest/strategies/vol_regime_mom_rev_quantstudio.py` | 移除策略层兜底 | 删 `_normalize_date_str`/`_current_raw_price`/`g.last_close` 自维护；listed_date/trade_days 直接消费框架统一格式；预筛改调注入 `current_price` |
| `tests/test_ptrade_contract_compliance.py` | +25 用例 | A1 门控/算法/同构 15 + A2 缓存/PIT 4 + A3 归一/门控 6 |

## 2. 平台差异吸收矩阵（上收后）

| 差异 | 吸收层 | 策略层残留 |
|---|---|---|
| 市价单 50,000 股上限（创业板/科创板） | 转换管线拆单（>49,000 拆多笔） | 零 |
| `current_price` 不可用（PTrade 返回 0） | 统一链 ①前收→②原API→③get_history | 零 |
| `get_trade_days` 全量日历/格式混用 | 转换侧归一 + 未来过滤 | 零 |
| `get_stock_info.list_date` 格式 | 转换侧归一 | 零 |
| 退市强平（D4-S3）/ 资金不足三态（D4-S4） | 引擎层（B 组）/ 登记不建模 | 零（另案） |

## 3. 验收结果

| 项 | 结果 |
|---|---|
| 单元测试全绿 | ✅ `test_ptrade_contract_compliance.py` + `test_source_import.py` 100 PASS（含 25 新增） |
| 策略层零平台知识静态扫描 | ✅ `def _normalize_date_str`/`def _current_raw_price`/`g.last_close`/`_qs_split_order` 函数定义级 ZERO HITS |
| 双端同构 | ✅ `_qs_split_order` 模板 vs 本地 AST 逐语句一致（test_split_ptrade_vs_local_homology） |
| 重转产物编译 | ✅ `output/ptrade_export/vol_regime_mom_rev/vol_regime_mom_rev_ptrade.py` py_compile 通过，注入 15 helper 齐全 |
| PIT 纪律（跨日缓存失效） | ✅ test_a2_cross_day_cache_invalidated |
| 费用前置门 | ✅ 平台佣金 ≥5 元/笔（探针实证），拆单双端对称 |

## 4. 下阶段

- C 组（skill/validator BLOCK + 全库误伤扫描）：待 A 组基线回测确认后执行
- D 组文档同步：README/toolbox/prompt/interface-contract 随推
- B 组（退市强平引擎）：数据前提已 PASS，独立流水线排期