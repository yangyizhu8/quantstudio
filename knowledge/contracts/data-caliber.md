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