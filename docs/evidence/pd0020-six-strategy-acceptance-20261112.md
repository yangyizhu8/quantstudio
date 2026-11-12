# 六策略 api_portability 验收（PD-2026-0020 §12 期1 · 2026-11-12）

> **判据**：铁律 07 验收门槛「六策略重转 api_portability 全 PASS」。
> **纪律（D5-B）**：`source_import.py` 是共享模板，改动后**旧产物 PASS 是假绿** ⇒
> 本件全部为**本次修复后重转**的产物，`--no-smoke`（跳过回测冒烟，故 status=PARTIAL，
> 非失败：schema/timing/hard_filters=NOT_RUN 属未运行而非未通过）。

## 一、六策略读数（全部重转）

| # | 策略 | run_card status | **api_portability** | source_import |
|---|---|---|---|---|
| 1 | CANSLIM突破成长选股策略 | PARTIAL | **PASS** | PASS |
| 2 | fall_reversal | PARTIAL | **PASS** | PASS |
| 3 | tech_etf_mvo_rotation | PARTIAL | **PASS** | PASS |
| 4 | vol_regime_mom_rev | PARTIAL | **PASS** | PASS |
| 5 | weekly_smallcap_growth_momentum_10 | PARTIAL | **PASS** | PASS |
| 6 | 周频小市值成长动量（三层止损） | PARTIAL | **PASS** | PASS |

## 二、关键发现：**命名错位**（R-20261112-08 裁乙先行的直接成果）

轮5 曾据「`strategies/tech_etf_mvo_rotation.py` 不存在」判定两策略**无源**、验收面只能 2/6。
裁乙要求「先排除路径/命名错位」后实测：**四策略源全部在库**，仅文件名带 `_quantstudio` 后缀
（或中文全角括号）：

| 我查的错名 | 实际在库名 | 大小 |
|---|---|---|
| `tech_etf_mvo_rotation.py` | `tech_etf_mvo_rotation_quantstudio.py` | 16,302 B |
| `vol_regime_mom_rev.py` | `vol_regime_mom_rev_quantstudio.py` | 23,617 B |
| `weekly_smallcap_growth.py` | `weekly_smallcap_growth_momentum_10_quantstudio.py` | 18,015 B |
| `周频小市值成长动量三层止损.py` | `周频小市值成长动量（三层止损）.py` | 22,089 B |

⇒ **验收面由「2/6 上限」更正为 6/6**。教训：**报「源缺失」前必须穷尽命名变体**（后缀/中文括号/
分隔符差异），否则会把「命名错位」误判为「环境缺失」——后者会污染铁律 07 门槛登记。

## 三、本轮修复链（进入本验收的全部改动）

| SHA | 内容 |
|---|---|
| `6a98974` | A 项：三公有 API 放宽 ＋ FILE-DB-ACCESS 检测点迁 open() |
| `7cba12b` | a-2 零预算显式拒单 ＋ b/B3 卖出回写账本 ＋ B4 非负不变量 |
| `0658911` | 甲-order：`_qs_budget_clamp` 买入折算（卖出不钳） |
| `e2bde2c` | RECONCILE 四计数 ＋ B5 金额版调用面检测 |
| `0e99bdb` | BUILD 唯一化（git 短 sha）＋ 五项固件 |
| `fb14d41` | **`_QSClampHooks` class 属性承载**（修 LOCAL-API-WHITELIST 回归） |

## 四、结论

**六策略 api_portability = 6/6 PASS** ⇒ 铁律 07 验收门槛**达成**（在 `--no-smoke` 静态面口径下）。
注：`status=PARTIAL` 系 `--no-smoke` 跳过回测冒烟，不构成 BLOCK；
若需 PASS 状态须跑带 smoke 的完整 import（耗时较长，且与 api_portability 判据无关）。
