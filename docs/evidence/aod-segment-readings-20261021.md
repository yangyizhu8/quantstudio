# 段A 读数与映射（ACCOUNT／ORDER／DEAL · 2026-10-21）

- **件**：`docs/probe/qmt_acct_order_deal_probe-20261021.py`（97,000B／GBK／compile OK）＋ `docs/probe/qmt_shared_surface_pos_probe_v7-20261021.py`（96,920B，含 S1 诊断行）
- **采样**：段A 16:02:55 ＋ 16:28:31（间隔 25.7 分钟）｜v7 16:30:41｜周期「日线」｜策略 `test`｜account `8881409880`｜`m_strStatus='登录成功'`
- **上下文**：他线四策略 16:13:08 初始化、16:13:14 停止（与本线无交互；探针只读）

## 一、ACCOUNT 面（本次 65 标量属性；上次 64 ⇒ **+1，差集待逐字段核**）

### 1.1 资金面映射（本地目标列＝`Portfolio.cash` / `market_value` / `total_value`）

| QMT 字段 | 16:02 | 16:28 | 本地契约对应 | 判定 |
| --- | --- | --- | --- | --- |
| `m_dAvailable` | 9567383.63 | 9567383.63 | `Portfolio.cash` | **锚定**（可用资金＝本地现金口径） |
| `m_dInstrumentValue` | 409232.39999999997 | 409232.4 | `market_value`（持仓市值合计） | **锚定**（＝`m_dStockValue` 353998.0 ＋ `m_dFundValue` 55234.4） |
| `m_dAssetBalance` | 9977262.03 | 9977262.03 | `Portfolio.total_value` | ⚠️ **口径差 646.0**（详见 §1.2） |
| `m_dCurrMargin` | 409878.4 | 409878.4 | 无本地对应（占用类） | ＝`m_dInstrumentValue` ＋ **646.0** |
| `m_dFetchBalance` | 9541692.1 | 9541692.1 | 无（可取余额） | ＜`m_dAvailable` 差 25691.53 |
| `m_dPositionProfit` | −8868.899999999996 | −8868.899999999994 | 持仓盈亏 | 浮点尾差 ≤2e-12（**判等须容差 1e-9**） |
| `m_dCommission` | 19.75365 | 19.75365 | 手续费累计 | 稳定（本日） |
| `m_dStockValue` / `m_dFundValue` | 353998.0 / 55234.4 | 同 | 股票/基金市值分项 | 新增细粒度（本地无对应，**可选透出**） |
| `m_dInitCloseMoney` | 2634.146350000008 | 2634.1463500000077 | 无 | 尾差 3e-13 |
| `m_dPreBalance` | 9977262.030000001 | 同 | 无 | 昨结算余额（本日＝现值，说明未跨日结算） |
| `m_strTradingDate` | 20261021 | 20261021 | — | 交易日 |
| `m_strStatus` | 登录成功 | 登录成功 | — | 账户状态 |

### 1.2 🔴 恒等式与口径差（两条新确证事实）

**恒等式（本样本成立）**：`m_dAssetBalance = m_dAvailable + m_dCurrMargin`
- 10-21 16:02/16:28：9567383.63 ＋ 409878.4 = **9977262.03** ✓ 精确
- 10-20 02:29：9503483.57 ＋ 463949.3 = **9967432.87** ✓ 精确
- 10-20 20:14：9503481.07 ＋ 463949.3 = 9967430.37 ≠ 9967432.87，**差 2.50**

⇒ **ACCOUNT 字段组非原子更新**：20:14 那次 `AssetBalance` 已是后值而 `Available` 仍是前值（差值 2.50 恰等于两次采样间 `Available` 增量）。
⇒ **判据**：跨字段恒等式校验**须留容差或单次读取**，否则会把「更新时差」误判为「口径不一致」。

**口径差 646.0（可解释但未归因）**：
`m_dCurrMargin`(409878.4) − `m_dInstrumentValue`(409232.4) = **646.0**；本地 `Portfolio.total_value = cash + market_value` = 9976616.03，**比 QMT `m_dAssetBalance` 少 646.0**（他线策略自算 `total_asset=9976616.03` 同样口径）。
⇒ 结论：**本地「总资产」口径 ≠ QMT「总资产」口径，恒差 646.0**（该差额落在 `CurrMargin` 而非 `InstrumentValue`）。**差额来源未确证**（可能为在途/冻结/应计类占用）⇒ **登记为口径差，不擅自归因**；对齐时须显式选择口径。

### 1.3 时变划分（双采样的价值）

| 类别 | 字段 | 结论 |
| --- | --- | --- |
| **日内稳定** | `m_dAvailable`／`m_dAssetBalance`／`m_dCurrMargin`／`m_dFetchBalance`／`m_dStockValue`／`m_dFundValue`／`m_dCommission`／`m_dPositionProfit` | 25.7 分钟内**完全不变**（仅浮点尾差 ≤3e-11） |
| **跨日变动** | 同上各字段 | 10-20 → 10-21：Available 9503483.57→9567383.63（+64000.06）、AssetBalance 9967432.87→9977262.03（+9829.16）、CurrMargin 463949.3→409878.4、Commission 44.9365→19.75365 |
| ⇒ **规则** | — | **变动驱动＝交易日结算，非日内漂移**；**同日内可当常量用，跨日必须重采** |

## 二、ORDER 面（本次 3 笔 × 60 标量）

| QMT 字段 | 读数（ORDER[0]／[1]／[2]） | 判定 |
| --- | --- | --- |
| `m_strInstrumentID` / `m_strExchangeID` | 002516/SZ、002516/SZ、001696/SZ | 裸码＋交易所（同 POS 口径） |
| `m_nVolumeTotal` / `m_nVolumeTotalOriginal` / `m_nVolumeTraded` | 200/200/**0**｜0/300/**300**｜0/1800/**1800** | 委托量/原始量/**已成交量**（键语义区分明确） |
| `m_dLimitPrice` | 5.4 / 5.41 / 15.19 | 限价 |
| `m_dTradedPrice` / `m_dTradeAmount` | 0/0｜5.41/1623.0｜15.19/27342.0 | 成交价/成交额（未成交时为 0） |
| `m_dCancelAmount` | **200.0** / 0 / 0 | 撤单量（仅未成交那笔有值） |
| **`m_nOrderStatus`** | **54** / **56** / **56** | **54＝未成交（VolumeTraded=0）**；**56＝已成交**（VolumeTraded>0）——**样本推断，待更多状态样本确证** |
| `m_nOrderSubmitStatus` | 51 / 51 / 51 | 单一取值，语义待验（疑＝已报） |
| `m_nOrderPriceType` | 50 | 限价（与 `m_strOptName` 吻合） |
| `m_eEntrustType` / `m_eCashgroupProp` / `m_eFutureTradeType` | 48 / 48 / 48 | 占位常量，**无语义** |
| **`m_nDirection`** | 48 / 48 / 48 | 🔴 **恒为 48（买、卖皆同）⇒ 不可作买卖方向** |
| **`m_nOffsetFlag`** | 48 / 48 / **49** | ✅ **48＝买/开、49＝卖/平**（买单 48、卖单 49；与 DEAL 方向一致）⇒ **方向真源** |
| `m_nHedgeFlag` | 49 / 49 / 49 | 恒定占位 |
| `m_eCoveredFlag` | 0 / 0 / 0 | 与 DEAL 的 48 不同 ⇒ 占位 |
| `m_strOptName` | 限价买入 / 限价买入 / **限价卖出** | ✅ **中文操作名＝方向交叉校验源** |
| `m_strInsertDate` / `m_strInsertTime` | 20261021 / 143442·144943·145448 | 下单时点（HHMMSS 6 位） |
| `m_strOrderRef` / `m_strOrderSysID` | 1098910157·1098910471·1098910873 / 16·18·19 | ✅ **委托↔成交关联键**（见 §四） |
| `m_nRef` | 1098910157 等 | 与 `m_strOrderRef` 同值（平台引用） |
| `m_bEnable` | True | 委托有效标志 |
| `m_strXTTrade` | 其他终端 | 委托来源 |
| `m_strInstrumentName` | 旷达科技 / 旷达科技 / 宗申动力 | 标的名称 |

**🆕 新发现（会话级随机，不可作业务字段）**：`m_nOrderStrategyType` 两次采样**不同**（1329631880 → 374759360）；DEAL 侧 `m_nOrderStrategyType` 亦变（2066481152 → 1165623296）⇒ **每次运行随机**。
**对照**：`m_nStrategyID` 两次采样**恒为 −1567873368281261866**（跨会话稳定的策略标识）⇒ 两者语义不同，**不可混用**。

## 三、DEAL 面（本次 2 笔 × 47 标量）

| QMT 字段 | 读数 | 判定 |
| --- | --- | --- |
| `m_dPrice` / `m_nVolume` / `m_dTradeAmount` | 15.19 / 1800 / 27342.0；5.41 / 300 / 1623.0 | 成交价/量/额（价×量＝额 ✓） |
| `m_dCommission` | 19.41282 / 0.34083 | 手续费 |
| **`m_dComssion`** | 19.41282 / 0.34083 | ✅ **F3 确证：拼写变体并存且同值**（探针 `same=True`）⇒ 定 `m_dCommission` 为准，`m_dComssion` 登记别名 |
| `m_nDirection` | 48 / 48 | 🔴 同 ORDER，**恒 48 不可用** |
| **`m_nOffsetFlag`** | **49**（卖）/ **48**（买） | ✅ 方向真源（与 ORDER 互证） |
| `m_nHedgeFlag` | 49 / 49 | 恒定占位 |
| `m_eCoveredFlag` / `m_eEntrustType` / `m_eFutureTradeType` | 48 / 48 / 48 | 占位常量 |
| `m_nOrderPriceType` | 50 | 限价 |
| `m_strTradeDate` / `m_strTradeTime` | 20261021 / **145506**·**145001** | ✅ **本次两笔均 6 位（PADDED）**；10-20 样本 `'94005'`（5 位）⇒ **F2 修正：不保证定长，解析须补位至 6 位** |
| `m_strTradeID` | 1010000032485873 / 1010000032482204 | 平台成交编号（长数字串） |
| `m_strOrderRef` / `m_strOrderSysID` | 1098910873 / 19；1098910471 / 18 | ✅ **与 ORDER[2]/[1] 逐一对应** |
| `m_dCloseProfit` / `m_nCloseTodayVolume` | 0.0 / 0 | 平仓盈亏/今平量（本次无平仓） |

## 四、哨兵与黑名单（实测 9 项 ＋ 1 类随机）

| 类别 | 字段 | 值 |
| --- | --- | --- |
| INT32_MAX | ORDER `m_nErrorID`／`m_nGroupId`／`m_nOpType`；DEAL `m_nGroupId` | 2147483647 |
| MINUS1 | ORDER `m_nFrontID`／`m_nSessionID`／`m_nTaskId`；DEAL `m_nTaskId`／`m_nRealOffsetFlag` | −1 |
| 🆕 **会话级随机** | ORDER／DEAL `m_nOrderStrategyType` | 两次采样不同 ⇒ **禁作业务字段** |
| 恒零占位 | ACCOUNT `m_dInitBalance`／`m_dNav`／`m_dNetValue`／`m_dPurchasingPower`／`m_dGoldValue`／`m_dLoanValue`／`m_dLongValue`／`m_dShortValue`／`m_dMortgage`／`m_dCredit` 等 20+ 项 | 0.0 |

## 五、委托↔成交关联（实测可连）

| ORDER | ↔ | DEAL | 关联键 |
| --- | --- | --- | --- |
| ORDER[1] 002516 买 300 @5.41（`m_strOrderRef=1098910471`，`m_strOrderSysID=18`） | ↔ | DEAL[1] 002516 买 300 @5.41（`m_strOrderRef=1098910471`，`m_strOrderSysID=18`） | **`m_strOrderRef` ＋ `m_strOrderSysID` 双键一致** |
| ORDER[2] 001696 卖 1800 @15.19（`…=1098910873`，`=19`） | ↔ | DEAL[0] 001696 卖 1800 @15.19（`…=1098910873`，`=19`） | 同上 |
| ORDER[0] 002516 买 200 @5.4（未成交，撤单量 200） | — | 无对应成交 | 未成交单 |

⇒ **关联键确证：`m_strOrderRef`（主）＋ `m_strOrderSysID`（辅）**。

## 六、S1 端上复核（v7 · 16:30:41）——**④第 4 条闭**

```
A1_positions total=16  keys=['510360.SH','515380.SH','600059.SH','600699.SH','601777.SH']
A2_universe total=5226  uni_counts={'SZ':2908,'SH':2318}        ← 与 10-20 定谳完全一致
A3 get_position('510360.SH') amount=26900.0 -> HIT                ← 命中且【无诊断行】
[QS_QMT_CODE_FORM_MISS] code='510360.SS' hit_forms=['510360.SH']
A3 get_position('510360.SS') amount=0.0 -> MISS(zeroview)        ← 形态不匹配：静默 miss 变可审计
[QS_QMT_CODE_FORM_MISS] code='510360.SZ' hit_forms=['510360.SH']
A3 get_position('510360.SZ') amount=0.0 -> MISS(zeroview)
[QS_QMT_CODE_FORM_MISS] code='510360' hit_forms=['510360.SH']
A3 get_position('510360') amount=0.0 -> MISS(zeroview)
A4 VERDICT=端内自洽且单标精确（仅 ['510360.SH'] 命中）=> C1 结案
uni_has_SH=True  uni_has_SZ=True
```

| 判据 | 结果 |
| --- | --- |
| 四形态命中/未命中判定与 10-20 定谳**完全一致** | ✅ **wrapper 改动后行为未变 ⇒ 纯增益成立** |
| 诊断行在三种形态不匹配下各打 1 行（含 `hit_forms`） | ✅ **S1 端上实证生效** |
| 命中路径无诊断行 | ✅ 无误报 |
| ⚠️ 观察 | A4 判定段自身又调 `get_position` 三次 ⇒ 诊断行**多打 2 行**（共 5 行）——探针内部重复，**非缺陷**；但实盘若反复查错形态会刷屏 ⇒ 候 **S1.1 优化（按 code 去重）**，**非必需、不阻断** |

## 七、本轮结转事项

1. **registry 回填**（立项步骤 3）：新增 ACCT／ORD／DEAL 族格位——**须先核 `scripts/check_dimension_alignment.py` 是否需同步登记新族**（若需 ⇒ 属脚本改动，须走框架流程）
2. **口径差 646.0** 未归因（`CurrMargin` − `InstrumentValue`）——待解释
3. **ACCOUNT 字段组非原子** ⇒ 恒等式校验须容差/单次读
4. **m_nOrderStatus 54/56 语义** 仍为样本推断（各 1 笔）——待补状态样本确证
5. **ACCOUNT 标量 64→65 差集**、**F2 时间不保证定长** —— 均已登记
