# PD-2026-0020 期1 · 冻结与六策略重生登记

> 六策略**逐个重生**，产物 hash 取重生后值；**不继承历史产物 PASS**。

## 代码冻结

| 文件 | sha256 |
|---|---|
| `quantstudio/strategy_compiler/source_import.py` | `191e9f1e1ad860e5d6b397ce5e65938db332ff6890c810447592fc61563b8497` |
| `quantstudio/strategy_compiler/portability_rules.py` | `84f591ee6f74f9fba990df6b72c1340645b1af5526a9c4adc563a55aa360f397` |
| `quantstudio/strategy_compiler/validators/validate_ptrade_portability.py` | `ce6f8b7caadd7237add9b1178fd011ba255d6123d8a9415243c6ff64d1b087dd` |
| `quantstudio/ptrade_deploy/transformer.py` | `a0c13e8b0171f0af4702cc8351388c4b6d447ef7b2a309510eacd259518ef443` |
| `quantstudio/ptrade_deploy/templates/isolation_template.py` | `72dc820e9d0a67832c18f8cc2b717b0e6ad409bc45cb778c6130dfb8a0267fb2` |
| `quantstudio/ptrade_deploy/templates/persistence_template.py` | `e7dd2929952c1fc108b2fb7c60d4b219a28cc3a0e76eb95b6c4c95f4d608d0b9` |

## 输入冻结（策略源）

| 策略 | sha256 |
|---|---|
| CANSLIM突破成长选股策略 | `53262acd2db3d47e3b37e9036c5b1a32e47f9dc347c6a9d674923f2df11891bf` |
| fall_reversal | `190d68800d0d305c9a5f01b2a1b8fbacbd7d18809af29c1650d47ee4133e5c82` |
| tech_etf_mvo_rotation | `2c1deaedf5b7d583edac67ebfc594a744366170d71a995af634c9c8014a7a147` |
| vol_regime_mom_rev | `bb639da05625b8cb43e55dd92c3baaaae6cc78980da72b0f8a89e1e28861d18b` |
| weekly_smallcap_growth_momentum_10 | `3342a490b0abf958d57a172e44d9275175e16ee393864000bf2b15b666882166` |
| 周频小市值成长动量（三层止损） | `3e3bbdedc1a9bf6c0d239f52a5a61ede7529ce06fd696b05be990ea4ebe00b08` |

## 依赖

`python 3.11.9` ｜ {'duckdb': '1.5.6', 'pandas': '2.3.3', 'numpy': '2.4.6', 'pytest': '8.4.2'}

## 产物登记（重生后）

| 策略 | 字节 | sha256 | api_portability | rc |
|---|---|---|---|---|
| CANSLIM突破成长选股策略 | 186755 | `f56f891a9fae72c4e91a3e7fe436d7d0ea2ab2db7b9671503d16256468dd5346` | **PASS** | 1 |
| fall_reversal | 91276 | `d99f34f4075d216554ca27724ee369c24239a923a4302dc547ae8ed647eb83b1` | **PASS** | 1 |
| tech_etf_mvo_rotation | 96541 | `291389b72bb7d11e2f387a0d32531271bc2384823a77080749bbfebbf1fd0e4e` | **PASS** | 1 |
| vol_regime_mom_rev | 121920 | `6e4550c8ba9a7daf06c2319bd3ea72a5d988577c13b76a4f2acf6fe52bcfbe0e` | **PASS** | 1 |
| weekly_smallcap_growth_momentum_10 | 174425 | `d72555571b8d2c48618f224f0254b4753b81b98469b413ee12e55a499124eb80` | **PASS** | 1 |
| 周频小市值成长动量（三层止损） | 185585 | `53558eadccf5d3f43da0378376d05774700565b4a4f565b7f17a009c305e44b9` | **PASS** | 1 |

## 结论

六策略 api_portability 全 PASS：YES
