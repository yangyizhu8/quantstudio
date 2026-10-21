# POS 7 字段消费方普查（零消费定谳 · 2026-10-22）

- **触发**：总调度裁定「POS 7 条缺口先 grep 消费方再定——零消费 ⇒ 接受缺口+登记已知差异（不立项）；有消费 ⇒ 立六步补」
- **性质**：只读普查（零写入代码），结论即裁定依据

## 一、结论（一句话）

**7 个字段全部零消费**（全仓 Python 属性访问形态 **0 命中**；指定 13 件策略/产物 **0 命中**；`skills` 的 2 处为 `get_business_type()` **API 签名参考**，非持仓字段消费）
⇒ 按既定规则：**接受缺口 ＋ 登记已知差异（不立项）**。

## 二、普查范围与证据（逐层加严，防假阴性）

| 层 | 范围 | 文件数 | 命中 |
| --- | --- | --- | --- |
| ① | `quantstudio/backtest/strategies`（本地策略库） | **38** | **0** |
| ② | `output/generated_strategies`（生成产物） | **472** | **0** |
| ③ | `ptrade`（PTrade 翻译产物） | **38** | **0** |
| ④ | `tests`（测试夹具，补充） | **327** | **0** |
| ⑤ | `skills`（SKILL 模板，补充） | **76** | 2（**误命中**：`get_business_type()` API 参考，非 `position.business_type`） |
| ⑥ | **指定 13 件**：6 本地策略 ＋ 6 QMT 产物 ＋ 1 PTrade 产物 | **13** | **0** |
| ⑦ | **全仓扫描**（所有 `.py`，排除 `.venv/venv/node_modules/__pycache__/.git/data`，仅「属性访问形态」`.字段名`） | — | **0** |

### ⑥ 指定 13 件清单（逐件零命中）

本地策略（`quantstudio/backtest/strategies/`）：
`CANSLIM突破成长选股策略.py`／`fall_reversal_quantstudio.py`／`tech_etf_mvo_rotation_quantstudio.py`／`vol_regime_mom_rev_quantstudio.py`／`weekly_smallcap_growth_momentum_10_quantstudio.py`／`周频小市值成长动量（三层止损）.py` —— **各 0 命中**

QMT 产物（`output/backups/m5.1a-20261010/products-pre-rerun/`）：
`qmt_import__CANSLIM…_qmt.py`／`qmt_import__fall_reversal_qmt.py`／`qmt_import__tech_etf_mvo_rotation_qmt.py`／`qmt_import__vol_regime_mom_rev_qmt.py`／`qmt_import__weekly_smallcap_growth_momentum_10_qmt.py`／`qmt_import__周频小市值成长动量（三层止损）_qmt.py` —— **各 0 命中**

PTrade 产物：`ptrade/tech_etf_mvo_rotation_ptrade.py` —— **0 命中**

### ⑤ 的「误命中」说明（不掩盖）
`skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json:681/:684` 出现 `business_type`，但形态是 `"get_business_type": {…}` 与 `"examples": ["get_business_type()"]` ⇒ **平台 API 签名参考**，与「持仓对象属性 `position.business_type`」无关，**不计为消费**。

## 三、逐字段判定

| 字段 | 消费情况 | 判定 |
| --- | --- | --- |
| `today_long_amount` | 0 | **接受缺口 ＋ 登记已知差异**（不立项） |
| `today_short_amount` | 0 | 同上 |
| `business_type` | 0（2 处为 API 参考，非属性消费） | 同上 |
| `contract_multiplier` | 0 | 同上 |
| `long_pnl` | 0 | 同上 |
| `long_cost_basis` | 0 | 同上 |
| `delivery_date` | 0 | 同上 |

## 四、已知差异登记（随附条件）

接受缺口≠无风险，以下三条须随账本留存：
1. **平台差异客观存在**：PTrade `Position` 契约提供这 7 个字段，本地 `Position`（6 字段）不提供 ⇒ **策略从 PTrade 迁入本地时会 `AttributeError`**。
2. **当前 6 策略不受影响**：本次普查覆盖现有全部策略与产物，均未消费 ⇒ 现网零影响。
3. **未来风险**：若新生成策略引用这 7 个字段，将在本地回测时报错 ⇒ **建议在策略生成/转换校验侧加一条检测**（见 §五）。

## 五、建议（可选，不阻断）

在 portability/校验器侧增加一条 **WARN 级**检测：策略源码出现 `position.<7 字段之一>` 时提示「该字段本地未实现（PTrade 有）」。理由：本次零消费是**时点性**结论，加了这条即可把"未来新策略踩坑"变成可机检，而不是靠人记得。**待总调度另批**（涉及校验器改动，属框架层）。
