# DAT 11 维度枚举清单（2026-10-22 · 拆分案照准后首轮）

- **触发**：总调度照准「DAT 枚举 11 维度拆分案（口径维度拆分合理）——一轮出枚举清单并回填」
- **原则**：**逐维度按本地实况判状态**，无证据者记「未验证」并写明待验点；**与既有条目重叠者归并**（避免双计）

## 一、11 条枚举与判定

| 编号 | 维度 | 语义（本地口径） | 状态 | 本地证据 / 待验点 |
| --- | --- | --- | --- | --- |
| **DAT-05** | 日期边界（首/末日含否、`count` 语义） | `get_history` 取数窗口边界与命中不足时的返回策略 | **未验证** | 待验点：`get_history` 首末日是否含、`count` 不足时返回实际行还是报错（探针 v-batch 已验部分：窗口取得数 < count 时**如实返回不凑数**，`tests/test_source_import_qmt.py:438` 为 QMT 侧同类证据，**本地侧未取**） |
| **DAT-06** | 停牌日 K 线 | 停牌日是否有 bar、成交量语义 | **已排查** | 停牌判定三处：`backtest_engine.py:750`（`suspendFlag==1` 或 `volume==0`）、`ptrade_api.py:1094`（`filter_stock_by_status` 四型）、`providers/duckdb_data_access.py:518`（数据含 `suspendFlag` 列）；**停牌日的 K 线是否补齐未逐项对表** |
| **DAT-07** | fallback 链 | 数据源缺失时的降级顺序与静默边界 | **已排查** | 原则在位：`backtest_engine.py:40`（configured/env roots 作为 fallback）、`fidelity_config.py:11`（**禁止静默单端 fallback**，须显性报错或降级+WARNING）；**具体链路顺序未逐级列出**（待列） |
| **DAT-08** | 复权口径 | `fq`/`dividend_type` 语义 | **不适用（归并 DAT-04）** | 与 DAT-04「前复权 fq='pre'」同源 ⇒ **不重复计数** |
| **DAT-09** | PIT 时点 | 取数时点锚定（D-1） | **已锚定（复用 DAT-03）** | DAT-03 已锚定（E1 铁律＋四格实测）；实现锚点 `ptrade_api.py:598`（`attach_bar` 直写 `_prev_date`）、引擎 `:572` `get_trading_day(day,-1)` |
| **DAT-10** | 指数日线专门 API | `get_index_day_bar` 的契约与边界 | **已锚定** | `ptrade_api.py:2130` 实现 ＋ 设计件 `docs/get-index-day-bar-design.md` ＋ `duckdb_data_access.py:679` `query_index_day_bars`；**契约边界**见 AGENTS.md E1 铁律第 7 条（daily-bar-v1 含当日 D，消费方须显式取 D-1） |
| **DAT-11** | ETF 池 PIT | `get_etf_list_local` 动态池的时点语义 | **已锚定** | 铁律级约定（AGENTS.md「ETF 策略默认用 `get_etf_list_local` 做 PIT 动态池」）＋ 实现 `ptrade_import.py:36` 导出 ＋ `backtest_engine.py:1124`（与 T0 分类同一数据源、失败→空缓存+warning） |
| **DAT-12** | 分钟/日线一致性 | 同一标的分钟聚合与日线是否一致 | **已排查** | 等价性测试 `tests/test_day_code_index_equivalence.py`；缓存一致性设计 `duckdb_data_access.py:543`（"行/列/顺序与单日查询"）；**全市场规模对照未做** |
| **DAT-13** | 字段缺失表达（None / 空串 / 0） | 缺字段与缺值三态区分 | **未验证** | 待验点：本地 API 缺字段时返回 `None`／空串／0 的**统一约定未取证**；参考坑族 QD-2026-0008 ②条（QMT 侧「空串≠None」）+ POS-17（`m_strOpenDate` 空串）——**QMT 侧已固件，本地侧待系统化** |
| **DAT-14** | 单位与精度 | 价格小数位、百分比×100、浮点容差 | **已排查** | `ptrade_api.py:286` `round(preclose*(1+limit_pct), 2)`（价格 2 位）；`libs/shared_cost_model.py:74` `round(price+slip, price_decimals)`；指标侧 `ptrade_metrics.py` 出参统一 ×100；**浮点容差无统一约定**（ACOD 面已实测须 1e-9） |
| **DAT-15** | 跨源一致性 | 本地库 vs 平台导出 vs MCP 源 | **已排查** | 对照工具在案：`calibration.py:10`（`PtradeExportProvider`）、`fidelity_compare.py:4-5`（差异明细 + 自动归因：涨跌停／数据源边界／阈值微调／正常成交）；**MCP 源对照未纳入** |

## 二、汇总（不双计）

| 状态 | 条数 | 编号 |
| --- | --- | --- |
| 已锚定 | **3** | DAT-09（复用 DAT-03）／DAT-10／DAT-11 |
| 已排查 | **5** | DAT-06／DAT-07／DAT-12／DAT-14／DAT-15 |
| 不适用 | **1** | DAT-08（归并 DAT-04） |
| **未验证** | **2** | DAT-05（日期边界）／DAT-13（字段缺失表达） |

## 三、待验点（下轮可离线推进）

1. **DAT-05**：`get_history` 首末日含否 + `count` 不足返回行为——纯本地可验（读实现 + 单测）。
2. **DAT-13**：本地 API 缺字段三态约定——纯本地可验（枚举 `ptrade_api` 返回 None/空串/0 的位置）。
3. **DAT-07 链路顺序**：把 fallback 顺序逐级列出（读 provider 选路）。
4. **DAT-12 全市场规模对照**：需跑一次全市场分钟/日线对照（本地可跑，耗时）。
5. **DAT-15 MCP 源**：跨源对照把 MCP 纳入（属数据线，非本会话主责）。

## 四、附带登记：端测清单新增一项

**印花税切换点核对（FEE-03 衍生）**：本地 `_stamp_tax_rate(date)`（`backtest_engine.py:885-889`）在特定日期后封顶 0.0005；
**平台侧切换点未核对** ⇒ 已列入端测清单（与 QMCH 回测探针同窗口或其后），判据＝同一卖出样本在切换日前后两端费率一致。
