# 契约档案 · ETF 份额合并处理（CORP-01，2026-10-07）

> 关联：`docs/corp-action-etf-merge-design.md`（六步①方案）/ `docs/evidence/corp-action-etf-merge-2025-09-22.md`（破案证据）
> / `tests/test_corp_action_merge.py` + `tests/test_factor_derived_split.py`（契约测试）

## 合并带规则（`_apply_factor_derived_split`，ratio = prev_close / preClose）

| 带区 | 规则 | 依据 |
|---|---|---|
| `0.50 ≤ ratio < 0.99` | **任意比直接采用反推 ratio**（不吸附）+ **按份取整** `int(round(vol×ratio))` + 成本守恒 `avg_cost×old/new` + 审计 `factor_derived_merge` | 除权参考价=交易所按公告精确计算（159934 反推 0.948127 vs 公告 0.948126035 六位吻合） |
| `ratio < 0.50` | WARN + 跳过（脏数据保护） | 合并比例物理下界 |
| `0.99~1.01` | 非除权跳过 | 不变 |
| `1.01~1.10` | 现金分红带跳过+WARN（阶段 2 精确入账） | 不变 |
| `≥1.10` | 送股带 0.5 吸附 | 不变（整手语义保留） |

## 退役规则（黄金重算登记）

- 旧「0.5 倍数吸附 + 0.5% 容差，未吸附 WARN 跳过」——**任意比合并是常态**，该规则把 0.948 类真实合并拒于门外（四象限残差 3.88pp 根因）；
- 旧「整手取整 `int(n/100)*100`」——ETF 合并份额精确到份（平台 159934 合并后 3796 非 100 倍数）。

## 已知精度边界

平台侧合并取整规则未公开：反推比例推 3793 vs 平台 3796（≈3 份 ≈25 元 ≈0.02%）——回测对账判据（<0.5%）噪声带内，P2 探针矩阵收集多案例后再议是否追赶。

## CORP-02 · 合并→零股→全清链闭环（2026-10-07 同批）

CORP-01 修复使合并后持仓可含零股（如 3293）——**卖出链必须支持零股全清**，否则零股滞留（平台 2026-01-29 清仓单 3296 原量成交 vs 本地旧 `round_to_lot` 截 3200）：

| 交易分支 | 语义 | 依据 |
|---|---|---|
| `order(-N)` 数量式卖出 | **委托量即成交量**（can_sell 钳制），不整手截断 | PTrade order 无整手声明+平台实证+A 股零股卖出规则 |
| 买入（`_execute_buy`） | 整手强制（round_to_lot 保留） | A 股买入必须整手 |
| `sell_value` 金额式卖出 | 暂保留整手 | 无平台对照证据（登记待验证） |
| `sell_all` 清仓 | 全量（既有） | — |

契约测试：`tests/test_odd_lot_sell.py`（六项）；方案 `docs/corp02-odd-lot-sell-design.md`。
策略层整手纪律范本：四象限 `submit_target_amount`（非清仓自整手 L406-408 / 清仓含零股全清 L400-402）。