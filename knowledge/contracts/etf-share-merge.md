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

平台侧合并取整规则未公开：公告比例推 3792 vs 平台 3796（≈4 份 ≈30 元 ≈0.02%）——回测对账判据（<0.5%）噪声带内，P2 探针矩阵收集多案例后再议是否追赶。