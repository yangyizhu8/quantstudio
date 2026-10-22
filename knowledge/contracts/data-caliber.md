# 契约档案 · 数据口径（DAT 类）· 骨架 v1

> 对应注册表 [../registry.md](../registry.md) §DAT。逐条建档模板同 contracts/api-semantics.md。

## 已锚定

### DAT-01 pctChg 合成口径（除权日涨跌幅）
- 证据：`docs/evidence/ptrade-pctchg-synth-evidence.md`（指针不复制）。

### DAT-03 日线 include 语义（D-1 锚定，E1）
- 平台口径：`get_history(..., include=False)` 锚定前一交易日；`include=True` 在执行日语境含当日全日 bar；
- 实证：2026-09-22 四格实测（15:00/09:31 × True/False）；锚定机制 `ptrade_api.py:598`（attach_bar
  直写 `_prev_date`，与 profile 无关）；
- 纪律：生成策略一律 include=False（校验器 NO-LOOKAHEAD-INCLUDE BLOCK）——AGENTS.md E1 铁律全文。

## 已排查

- DAT-02 基准收益（000300）：四象限案例双端 36.85% 逐位一致；
- DAT-04 前复权 fq='pre'：本案例价格序列逐笔一致（ETF 无分红除权日覆盖——**覆盖不完整，
  探针须补除权日场景**，升级排查结论前按未验证对待）。

## 待枚举验证（P2 细化编号）

日期边界（首日/末日/half-day）、停牌日 K 线、指数日线专门 API（get_index_day_bar 契约边界：
match open 策略须取 D-1 行）、ETF 池 PIT、fallback 数据源优先级、港股通/北交所代码域、
分钟线（如接入）、除权除息日复权处理、新股上市首日、退市处理。
## F-2′ 复权字段口径禁令（2026-10-18 登记）

- `get_history(..., fq='pre')` 返回的 **OHLC 为前复权价**，而 **`preclose` 为原始价**（字段级口径差异）；
- ⇒ **禁止跨口径比较**：任何「复权价 vs 原始价/限价」的比较（如涨停判定、涨跌停保护）都会在复权因子 ≠ 1 时系统性失败；
- 正确做法：执行层涨跌停判定统一走 `check_limit(query_date=...)`（见 `api-semantics.md` API-01），
  或显式取原始价（`fq=None` 的专用入口，非信号价路径）；
- 实证：2026-10-15 首板案——前复权 close 6.665458 vs 原始涨停价 6.68 ⇒ 判非涨停 ⇒ 池欠填（28 vs 77）；
- 本条即 F-2 的落地（F-2 由件 2 承接，落地即闭）。

## DAT-13 字段缺失三态契约（2026-10-22 登记，总调度照准）

> **用途**：统一「查不到 / 缺字段」时的返回形态，避免消费方按错形态判空（`is None` vs `not v` vs `len()==0`）。
> **来源**：`docs/evidence/dat-05-07-13-closure-20261022.md` §三（本地公开面实勘）。

| 返回形态 | 公开 API 例 | 缺数据时返回 | 正确判空写法 |
| --- | --- | --- | --- |
| **集合型** | `get_trades()`／`get_open_orders()` | 空列表 `[]` | `if not xs:` |
| " | `get_history(security=None)` | 空 DataFrame | `if df is None or df.empty:` |
| **单值对象（持仓）** | `get_position(code)` 未命中 | **零视图**（`amount=0` 的 Position，非 None） | `if pos.amount == 0:`（**禁用 truthiness**，QD-2026-0009 §④） |
| **单值对象（委托）** | `get_order(order_id)` | **`None`** | `if o is None:` |
| **字符串型** | 日期/名称类字段（如 QMT `m_strOpenDate`） | 空串 `''` | `if not v:`（**不得用 `is None`**，POS-17） |

### 已知不对称（有意设计，非缺陷，须按形态分别处理）

同为「查不到」，**`get_position` 给零视图、`get_order` 给 `None`**：
- `get_position` 零视图是**有意契约**——策略惯写 `get_position(c).amount == 0` 判空仓（`ptrade_api.py:1832-1835` 引据，QD-2026-0009 §④）；
- `get_order` 的 `None` 复刻平台 close/open 分支语义（`ptrade_api.py:2645` 前 `return None`）。
- ⇒ **消费方禁止把两者统一成一种判空写法**；新增 API 时须显式声明属哪一形态。

### 配套（随 DEAL-07 批次同批落地）

`portability_rules.py` 增提示：策略对上述 API 使用**错误判空形态**时给出 WARN（与 POS 7 字段 WARN、DEAL-07 四条费用规则同一 commit，避免二次动该文件）。
