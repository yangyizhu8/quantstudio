# R6 发布证据账：股息防守小市值五日轮动（客户风险接受型发布）

- 发布时刻：2026-09-15
- 发布形态：**CUSTOMER_RISK_ACCEPTED_RELEASE（客户风险接受型发布）**
- verbatim 证据：**「客户意见A，直接发布」**（来源：用户转客户裁定，2026-09-15）
- 发布产物：`quantstudio/backtest/strategies/股息防守小市值五日轮动.py`
- canonical sha256：`415d1b9bd2e31b1deae5b7d2f7c3b0811320698ea92433b09ad234080a6f14f2`

## 一、⚠ G2 未通过（如实留痕，不豁免为 PASS）

| 门 | 含义 | 结果 | 实测 | 阈值 |
|---|---|---|---|---|
| G1 | 绝对收益 | PASS | 0.3212 | > 0.0 |
| **G2** | **最大回撤** | **❌ FAIL** | **-0.2723（-27.23%）** | **≥ -0.25（-25.00%）** |
| G3 | 夏普 | PASS | 1.1503 | > 0.5 |
| G4 | 交易层胜率 | PASS | 0.5811 | > 0.4（561 笔） |
| G5 | — | PASS | — | — |
| G6 | — | PASS | — | — |

- R5.5 overall = **FAILED** ／ failed_gates = `['G2']` ／ insufficient = `none`
- **差距 2.23 个百分点**；WF 5 折 4 正（0.80）；MC n=1000 p=0.006（显著）
- R5.5 报告原样入证据包：`robustness/robustness_report_iter0.json`（不作任何修改）

## 二、豁免的合法性依据

1. 客户 verbatim 裁定「客户意见A，直接发布」——**知悉** G2 未通过（呈报包已列明），**接受**该回撤特征并放行；
2. 本线**未为通过门控做任何调整**：策略源码全程零改动（回撤 -27.23% 是「5 只等权小市值 + 无止损 + 每 5 日轮动」的固有特征，样本内=全窗同值、样本外更浅）；
3. design 记录：`validation_contract.robustness_gates.enabled=false` + `confirmation_evidence.robustness_exemption`（verbatim）；
4. workspace_state 记录：`robustness.exempted=true`，**同时保留** `stage=ROBUSTNESS_FAILED` 与 `failed_gates=['G2']`。

> **豁免 ≠ PASS**：G2 的 FAIL 状态在任何证据文件中均未被改写。

## 三、R5 主跑绑定（G3.5 复现门 PASS）

| 文件 | SHA-256（G351 == G352 逐位一致） |
|---|---|
| config.csv | `9f0ebe9ab273e25e7e6b793dbe4e898ee724dcdd77d2a9738127a9183bbc4333` |
| daily_stats.csv | `3f7b986f1426a03378649cc656e72db4bb98ff5e399667ed50713e66d590cb76` |
| trades.csv | `965c5bdde78ef57439ec0b84eca5ccac6da63c0258a2d0110bdb8d3f38a0d773` |
| round_trips.csv | `c389e93f4b2ffa068bcfaee99d7f1fc66e17d85cab6f71fe9328cad0ed4d5df4` |

- 审计行 `QS_REBALANCE_AUDIT : QS_PORTFOLIO_AUDIT = 319 : 319`（1:1）✅
- 运行日志 `[handle_data 错误] = 0` ✅
- 数据源：`data/staging/r5_maincopy_20260913/quantstudio.db`（provenance 在档）

## 四、分段报告

样本内年化 31.56% / 夏普 1.13 / 回撤 -27.23%；样本外年化 30.78% / 夏普 1.19 / 回撤 -23.30%；
全窗 +482.47%，期末 581,744 元（期初 99,876）。详见 `R5_segment_report.md`。

## 五、先例对照（publish 门合法路径）

照 **`agent_workspace/canslim_breakthrough`**（已发布 PASS）复刻：
`backtest_execution_owner='agent_managed'`（**非** USER_MODE）、`candidate_status='NOT_GENERATED'`（无需候选件）、
`backtest_data_source='duckdb_provider'`、`formal_publish_allowed=true`、`backtest_evidence_status='PASS'`。

> 修正记录：我先前据错误信息误判门要求 `USER_MODE` 与候选件生成，**先例读证伪**；未按误判伪造任何字段。
