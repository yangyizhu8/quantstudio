# B2 · POS 族交易端读数与判据对表（2026-10-19）

- **件**：`docs/probe/qmt_shared_surface_pos_probe-20261017.py`（v1，实际执行）
- **端测**：2026-10-19 20:14:28｜QMT 交易端｜周期＝**日线**｜策略名 `test`
- **语境**：`C1 account='8881409880'` ✅ 交易端语境成立（非回测）
- **性质**：只读读数归档 ＋ registry POS 面状态升级依据

## 一、读数摘要

| 项 | 读数 |
| --- | --- |
| `C1 account` | `'8881409880'`（非空 ⇒ 交易端语境 ✓） |
| `C1 biz=POSITION n` | **15** |
| `C1 biz=ACCOUNT n` | 1（`m_strStatus='登录成功'`） |
| `C1 biz=ORDER n` | 16 |
| `C1 biz=DEAL n` | 9 |
| **`C4 wrapper_path n`** | **15** ⇒ **与 C1 POSITION n=15 一致 ✓（生产路径一致性通过）** |

首条 POSITION 记录（示例）：`510360.SH 沪深300ETF广发`｜`m_nVolume=26900`｜`m_nCanUseVolume=26900`｜
`m_nYesterdayVolume=26900`｜`m_dPositionCost=43803.96`｜`m_dAvgOpenPrice=1.6274`｜`m_dLastPrice=1.626`｜
`m_dFloatProfit=80.70`｜`m_dPositionProfit=-64.56`｜`m_strTradingDay='20261019'`

## 二、POS 对表（17 格）

| 格 | 本地契约字段 | QMT 原生字段 | 实测读数 | 判定 |
| --- | --- | --- | --- | --- |
| POS-02 | `amount` 总持仓量 | **`m_nVolume`** | 26900 | ✅ **已锚定** |
| POS-03 | `enable_amount` 可用量(T+1) | **`m_nCanUseVolume`** | 26900（＝总量，因昨仓已过 T+1） | ✅ **已锚定** |
| POS-04 | `today_long_amount` 今买量 | 无原生字段；**可派生** `m_nVolume − m_nYesterdayVolume` | 26900−26900＝**0** | ⚠️ **派生可用**（无原生字段，登记派生式） |
| POS-05 | `today_short_amount` 今卖量 | 无原生字段；`m_nCloseVolume`＝0（语义疑为"平仓量"） | 0 | ⚠️ **缺口**（`m_nCloseVolume` 语义待验，不冒充锚定） |
| POS-06 | `long_amount` 多头量 | 无（A 股现货无多空分离；`m_nDirection`＝48＝'0' 恒买向） | — | ⚠️ **不适用**（现货无该概念） |
| POS-07 | `short_amount` 空头量 | 无 | — | ⚠️ **不适用** |
| POS-08 | `last_sale_price` 最新价 | **`m_dLastPrice`**（另有 `m_dSettlementPrice` 结算价同名值） | 1.626 | ✅ **已锚定** |
| POS-09 | `sid` 证券代码 | **`m_strInstrumentID`＝'510360'（裸码）＋ `m_strExchangeID`＝'SH'** | ⇒ 需合成 `510360.SH` | ✅ **已锚定**（**关键契约：QMT 返回裸码＋交易所，须拼后缀**） |
| POS-10 | `business_type` 业务类型 | 无直接字段；`m_eFutureTradeType`＝48（期货交易类型，现货恒 '0'） | 48 | ⚠️ **近似但语义不同**（不冒充锚定） |
| POS-11 | `contract_multiplier` 合约乘数 | 无该字段 | — | ⚠️ **平台不提供**（ETF/股票按 1 约定） |
| POS-12 | `margin_rate` 保证金率 | 无"率"字段；`m_dMargin`＝0.0（保证金额） | 0.0 | ⚠️ **不适用**（现货无保证金） |
| POS-13 | `long_pnl` 多头浮盈 | **`m_dFloatProfit`＝80.70**（当日浮动）／`m_dPositionProfit`＝−64.56（持仓累计） | 两字段并存 | ✅ **已锚定**（**须区分二者语义**：Float vs Position） |
| POS-14 | `short_pnl` 空头浮盈 | 无 | — | ⚠️ **不适用** |
| POS-15 | `long_cost_basis` 多头成本 | **`m_dPositionCost`＝43803.96**／`m_dAvgOpenPrice`＝1.6274（均价）／`m_dOpenCost`＝43803.96 | 三字段并存 | ✅ **已锚定**（成本额与均价两口径齐备） |
| POS-16 | `short_cost_basis` 空头成本 | 无 | — | ⚠️ **不适用** |
| POS-17 | `delivery_date` 交割日 | `m_strOpenDate`＝**`''`**／`m_strExpireDate`＝**`''`** | **空串（非 None）** | ⚠️ **已排查**（现货返回**空串**——**重要约定：不可按 None 判空**） |

**统计**：✅ 已锚定 **6**（POS-02/03/08/09/13/15）｜⚠️ 派生 1（POS-04）｜⚠️ 已排查/不适用 **10**（POS-05/06/07/10/11/12/14/16/17）

## 三、关键契约发现（3 条，均须回灌）

1. **`sid` 需合成**：`m_strInstrumentID` 为**裸代码**（'510360'），交易所另在 `m_strExchangeID`（'SH'）⇒ 消费方须拼 `code + '.' + exchange`；**不得直接当本地 `sid` 使用**。
2. **空串 ≠ None**：`m_strOpenDate`／`m_strExpireDate` 返回**空串 `''`**（POS-17）⇒ 判空须用 `not value`，不能 `is None`。
3. **哨兵/垃圾值**：`m_dStaticHoldMargin`＝**1.7976931348623157e+308**（DBL_MAX 哨兵＝无效）；`m_nCidRateOfCurrentLine`＝−1618221407、`m_nCidRateOfTotalValue`＝1954115676（**不可解释值**）⇒ **该三字段禁止消费**。

## 四、顺带取得（ACCOUNT／ORDER／DEAL 面，非本轮目标）

| 面 | 关键读数 | 价值 |
| --- | --- | --- |
| ACCOUNT | `m_dAvailable`＝9503481.07、`m_dAssetBalance`＝9967432.87、`m_dInstrumentValue`＝463976.2、`m_dCommission`＝44.9365、`m_strStatus`＝'登录成功' | 资金面锚点候选（POS 外的独立族，**候独立立项**） |
| ORDER | 16 条；`m_nOrderStatus`＝54、`m_strOptName`＝'限价卖出'、`m_strXTTrade`＝'其他终端' | 委托面候选 |
| DEAL | 9 条；`m_dPrice`＝4.92、`m_nVolume`＝10300、`m_dCommission`＝15.2028 | 成交面候选 |

## 五、探针缺陷自查（诚实申报）

1. **`dir()` 噪音**：对 ORDER 类型对象 `dir()` 吐出大量**数字属性名**（'−1','0','1','101'…）、**枚举类对象**
   （`EBrokerPriceType` 等）与**单字母属性**（j/w/x/y/z）⇒ 输出 200+ 行噪音，`C3` 对它们全为 `?`。
   POSITION/ACCOUNT/DEAL 面相对干净。**改进方向**：只取 `m_` 前缀且值类型为标量的属性。
2. **v1 全逻辑在 handlebar**：本次非交易时段（20:14）**handlebar 确实被触发**（印证文档「非交易时段可能被调用」），
   但该行为**不可依赖**；v2 双钩子（`after_init` 优先）仍是更稳设计，**建议后续统一用 v2**。

## 六、状态升级汇总（本轮落 registry）

POS 面 QMT 锚点列：**未验证 16 → 已锚定 6 ＋ 派生 1 ＋ 已排查/不适用 9**（POS-01 待核）；对齐面未验证总数相应下降。
