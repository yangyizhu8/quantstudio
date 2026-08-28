# 框架修复方案 v2（终稿）：get_fundamentals 三大报表取数接线

- 作者：dsh（fscore_rsrs R3 前置，2026-08-28）
- 状态：方案 v2（ZCode 必改①/② + 随实施 3 项已并入）→ 实施
- 关联：R1 能力盘点遗漏、DR-3、issue_registry

## 0. ZCode 必改/随实施修入

| 项 | 裁定 | 方案 v2 落实 |
|---|---|---|
| 必改① | 只返最新一行无法支撑 F-Score 同比（D3）+ 6 项两期比较 | §2.1 报表查询返回 **PIT 窗口内全部报告期行**（start_year/end_year/report_types 生效），策略层用两期取数范式取本期（最新披露）/去年同期 —— 与 get_financial 既有模式同构 |
| 必改② | 防再发：契约声明≠接线无机器检查 | §2.5 新增**契约表接线测试矩阵**（遍历 FUND_TABLES 全部表名经 API 层抽查非空，未接线即红）+ **skill R1 探查升级**（表存在≠API 可取，API 层实测非空入清单） |
| 随实施1 | 返回列名按契约归一（publ_date 数值时间戳） | §2.2 输出列 = base.py FUND_TABLES 契约列名，日期统一数值毫秒时间戳（与 eps/growth 同构，P-D10 先例） |
| 随实施2 | report_types 默认合并报表 | §2.1 report_types 默认 `["合并"]`（F-Score 语义依赖） |
| 随实施3 | 字段缺列不静默 | §2.3 缺列 → 返回缺列 + `log.warning`（表名+缺失字段清单），不静默 |

## 1. 问题定义

`get_fundamentals(income/balance/cashflow)` 恒空（ptrade_api else 分支）；provider 缺三大报表查询；DuckDB 表/契约/采集均在。F-Score 无法实现。R1 盘点遗漏（表存在≠API 可取）。

## 2. 方案

### 2.1 provider 报表查询（PIT 窗口全行返回）
```python
def _query_statement_table(self, table, bare_codes, query_ms, fields,
                           start_year=None, end_year=None, report_types=None):
    """PIT 窗口内全部报告期行（非仅最新——支撑同比两期取数）。
    SELECT code, end_date, publ_date, {fields} FROM {table}
    WHERE code IN (...) AND ann_date <= query_ms
      AND end_date 在 [start_year,end_year] 报告期内    # 报告期过滤（非披露日）
    order by code, end_date
    report_types 默认 ['合并']；不存在的 report_types 仅 log.warning 不阻断
    """
```

**关键**：
- **返回窗口内全部报告期**（`ann_date <= query_ms` 的每个报告期一行），策略层自行取本期（最新 ann_date）/去年同期（D3 同比）
- `start_year/end_year` 作用于 **end_date 报告期**（如 F-Score 需要 2025 年报与 2024 年报 → start/end 覆盖两年）
- `report_types` 默认合并报表，过滤报告期类型（若表有该列）

### 2.2 列名契约归一（随实施1）
- 返回列名 = `base.py FUND_TABLES[table]` 契约名（`publ_date`）
- DuckDB 表 `ann_date` → 契约 `publ_date`（**数值毫秒时间戳**，与 eps/growth 路径同构）
- 策略层只见一套列名 + 数值时间戳，不需处理两套命名

### 2.3 字段缺列（随实施3）
- 请求字段不在表列 → 返回缺列（NaN）+ `log.warning("get_fundamentals %s 表缺列: %s" % (table, missing))`
- fail-open 但显式告警（不静默吞错）

### 2.4 ptrade_api else 分支接线
```python
elif table == "balance_statement":
    df = self._fundamental.get_balance_statement(bare_codes, qd, fields,
                                                 start_year, end_year, report_types)
elif table == "income_statement": ...
elif table == "cashflow_statement": ...
else: df = pd.DataFrame(columns=self._FUND_TABLES[table])
# 返回列按 FUND_TABLES[table] 过滤 + index=ptrade 码（与 valuation 同款）
```

### 2.5 防再发两道防线（必改②）
**第一道 — 契约接线测试矩阵**（新参数化单测）：
```python
@pytest.mark.parametrize("table", FUND_TABLES.keys())
def test_fund_contract_wired(table):
    # 每张契约表经 get_fundamentals API 层抽查非空（数据在库前提下）
    # 未接线 → 返回空 → 本测试红（而非等某策略 R3 炸）
```
**第二道 — skill R1 探查升级**：inspect_capabilities 增加"每张契约表经 API 层实测非空"维度；写进 R1 检查清单（表存在 ≠ API 可取）。本次 fscore_rsrs R1 状态更正为 DATA_BLOCKED→READY（修复后）。

## 3. 改动范围
- providers（fundamental provider）：+`_query_statement_table` + 3 个表方法
- ptrade_api.py：else 分支接线
- tests：+契约接线测试矩阵
- skill（inspect_capabilities + R1 清单）：API 层实测维度
- 引擎零改动；策略零改动

## 4. 验收标准（含 ZCode 补强）
1. **同比双期可取**：get_fundamentals(income_statement, start_year=2024, end_year=2025, report_types=合并) 返回 2025 年报 + 2024 年报两期（600519 抽查）
2. balance/cashflow 同验证（total_assets / net_operate_cash_flow）
3. **PIT 双边界**：ann_date == 查询日（23:59:59 口径）可见；ann_date > 查询日不可见——两边界都测
4. **契约矩阵全绿**（必改②）：FUND_TABLES 全部表名经 API 抽查非空
5. 返回列名=契约（publ_date 数值时间戳）；report_types 默认合并；缺列 log.warning
6. 149 套件零回归
7. capability 复验 + 契约矩阵维度

## 5. 回退条件
验收失败 → git 回退 provider/ptrade_api/tests/skill 改动（提交前基线）。

## 6. 实施后登记
- DR-3 → closed（修复+验收）；capability R1 补验；skill R1 清单永久升级。

## 7. 已核验事实（含 ZCode 复核）
- ptrade_api L824-825 else 分支空表（ZCode 本机复验）✅
- base.py FUND_TABLES 含三表契约（total_assets/np_parent_company_owners/net_operate_cash_flow/publ_date）✅
- DuckDB 三表 12.8万+ 行 ✅；adapter 采集在 ✅
- get_financial 现有模式（start_year/end_year/report_types + 列名归一先例）可参照 ✅