# DEAL-07 方案骨架：QMT 侧交易面四符同名遮蔽（v0.1 骨架 · 待②审）

- **性质**：**方案骨架**（①方案 阶段，非完整方案；实施排 **QMCH 六步之后**——同 `source_import_qmt.py` 串行）
- **版本**：v0.1（2026-10-21）
- **关联**：DEAL-07（registry ACOD 节，**已知分歧（高）**）／F1（消费面缺位）／ACOD 30 条锚点／QD-0009（同族：消费面契约）
- **判域**：**D2**（改动落在 `_QS_*_EXT` 模板串 ⇒ 矩阵 `qmt_wrapper_hash` 须同 commit 追认）

## 0 知识反哺检索留痕

```
知识反哺：[命中] QD-2026-0009（QMT/qmt/QD-2026-0009.md，2026-10-21）——同族「消费面契约」四条硬规则（钩子时机/显式 C/零视图判 amount>0/码一律 universe 形态）直接适用
知识反哺：[命中] 平台转换铁律-文档签名不可信须建运行实测形态库（07-经验库/平台转换铁律-文档签名不可信须建运行实测形态库.md，2026-10-21）——四符语义须运行实测标定，不以文档为准
知识反哺：[命中] 平台转换坑-必填参数空串未定义行为返回静默空（07-经验库/平台转换坑-必填参数空串未定义行为返回静默空.md，2026-10-21）——"返回空"与"未实现"须显式区分
```

## 1 问题定义

**F1 实测**（`source_import_qmt.py` grep `^def get_orders|^def get_trades` **零命中**）：

| 本地 PTrade 契约（`ptrade_api.py`） | QMT wrapper 现状 | 后果 |
| --- | --- | --- |
| `get_order(order_id)` :2645 前 | **缺位** | 策略调用 `NameError` |
| `get_orders(security=None)` :2645 | **缺位** | 同上 |
| `get_trades()` :2654 | **缺位** | 同上 |
| `get_open_orders(security=None)` :2660 | **缺位** | 同上 |

⇒ 归入「消费面缺位」族（与 QD-0009 同族），非纯字段映射。

## 2 🔴 骨架阶段的关键实证（决定方案走向，须先裁定）

| 函数 | 本地真实语义（只读核对结论） | 直接后果 |
| --- | --- | --- |
| `get_orders(security=None)` | 返回 `engine._today_orders`（**已实现**；元素为 `Order` 对象，13 字段，经 `_po_to_order` 构造） | ✅ 可 1:1 映射 QMT **ORDER** 面 |
| `get_trades()` | `if not hasattr(engine,'_today_trades'): return []`，而**引擎全仓无 `today_trades` 记录点**（grep `today_trades` 于 `backtest_engine.py` 命中 **0**）⇒ **本地恒返回空列表** | ⚠️ **严格 1:1 复刻＝QMT 侧也恒空**（与"映射 DEAL 面"直觉冲突） |
| `get_open_orders(security=None)` | `match_price_mode != 'next_open'` ⇒ **始终 `[]`**；仅 `next_open` 返回 pending 队列 | QMT 无 pending 队列 ⇒ **复刻 close/open 分支＝恒空**（与用户裁定一致）；**`next_open` 分支不适用，须显式登记为边界，不得静默压平** |
| `get_order(order_id)` | 同上：`!= 'next_open'` ⇒ **`return None`** | QMT ⇒ **恒 None**（与用户裁定一致） |

### 2.1 由此产生的三选一（**方案核心裁定项，须用户定**）

| 选项 | 做法 | 分型 | 评价 |
| --- | --- | --- | --- |
| **A（推荐·严格 1:1）** | 四符全部按本地语义复刻：`get_orders` 映射 ORDER 面（**功能**）；`get_trades` **恒空**；`get_open_orders` **恒空**；`get_order` **恒 None** | 纯增益（补缺位，语义与本地一致） | 双端一致、零分叉；**代价**：DEAL 面 8 条锚点暂不被消费（留待 B 立项） |
| B（双端同批真实化） | 本地引擎补 `_today_trades` 记录 ＋ QMT 侧映射 DEAL 面 | **框架行为变更**（本地新增成交记录能力） | 双端一致且都真实；**须单独立项**（改本地引擎属另一域），不在本方案内捆绑 |
| C（仅 QMT 真实化） | QMT `get_trades` 返回 DEAL 映射，本地不动（恒空） | **双端语义分叉** | ❌ **禁止**：同一策略两端行为不同（本地恒空 / QMT 有数据），违反双端对齐原则 |

> **本骨架按 A 起草**；若裁定改 B，则本方案拆为「A 先行（补缺位）＋ B 另立项（补本地成交记录）」两段。

## 3 四符设计（按选项 A）

### 3.1 `get_orders(security=None)` — 映射 QMT ORDER 面

```
返回 list[Order]（本地 Order 对象，13 字段）；security 非空时按 code 精确过滤
数据源：_qs_get_trade_detail(C, 'ORDER')（实测 n=3）
过滤：m_bEnable 为 True？—— 待实施轮定（本地无对应概念，建议不额外过滤，保持"当日全部委托"）
```

**字段映射（13 字段；QMT 源字段均已 ACOD 锚定）**：

| 本地 `Order` 字段 | QMT 源 | 判据/备注 |
| --- | --- | --- |
| `order_id` | `m_strOrderRef`（主）／`m_strOrderSysID`（辅） | **待实施轮定**：本地 `order_id` 形态须核（`PendingOrder.order_id` 生成规则），优先与本地一致 |
| `security` | `m_strInstrumentID` ＋ `'.'` ＋ `m_strExchangeID` | **裸码须拼后缀**（同 POS 口径；QD-0008 ①条） |
| `direction` | **`m_nOffsetFlag`（48=买/开、49=卖/平）** | 🔴 **禁用 `m_nDirection`（恒 48）**（ACOD ORD-08）；本地 `direction` 取值域待核（'buy'/'sell'？） |
| `target` | 无对应 | ⚠️ 本地「目标金额/目标量」概念 QMT 无 ⇒ 建议 `0.0` 并登记已知差异 |
| `filled` | `m_nVolumeTraded > 0` 且 `== m_nVolumeTotalOriginal` | 布尔派生（ACOD ORD-06 推断：56↔已成交） |
| `target_amount` | `m_nVolumeTotalOriginal` | 原始委托量 |
| `filled_amount` | `m_nVolumeTraded` | 已成交量 |
| `price` | `m_dLimitPrice` | 委托价（`m_nOrderPriceType=50` 限价） |
| `status` | `m_nOrderStatus`（54/56…） | ⚠️ **枚举映射表须实测标定**（54=未成交/56=已成交为样本推断，各 1 笔）；本地 `status` 取值域待核 |
| `reason` | `m_strErrorMsg` / `m_strCancelInfo`（均可能空串） | **空串≠None**（QD-0008 ②条） |
| `created_dt` | `m_strInsertDate` ＋ `m_strInsertTime` | **时间须补位至 6 位**（F2 修正） |
| `filled_dt` | 无直接字段 | 经 `m_strOrderRef` join DEAL 反查 `m_strTradeTime`；缺则 `None` |
| `basket_id` | 无对应 | `None` |

### 3.2 `get_open_orders(security=None)` — **恒 `[]`**

复刻本地 close/open 分支；**边界登记**：`next_open` 分支（pending 队列）在 QMT 无对应，显式注记不适用，**不得静默压平**（防未来误判为"已实现全部语义"）。

### 3.3 `get_order(order_id)` — **恒 `None`**

复刻本地 close/open 分支（`return None`）。**注**：本地即使 `next_open` 也只查 pending/basket，**已成交委托亦查不到**——QMT 侧同此，不得私自扩展为"查全部委托"（那会改变本地语义）。

### 3.4 `get_trades()` — **恒 `[]`**

复刻本地未实现状态（§2 实证）。**头部注释须写明**：「本地引擎无成交记录（`_today_trades` 未实现）⇒ 本函数恒空；成交面真实化须双端同批（选项 B 另立项）」。

## 4 portability 分级调整 ＋ 负向用例

**现状**（`portability_rules.py`）：`validate_qmt_portability` 五规则 —— `QMT-GBK-HEADER`／`QMT-SYNTAX`／`QMT-LIFECYCLE-SHAPE`／`QMT-DENY-API`／`QMT-API-WHITELIST`（另有 `QMT-CONTEXT-METHOD`）；`denylist()` 为「API 名 → rule_id」映射（如 `get_market_data`→`QMT-NONEX-MARKET-DATA`）。

**调整内容**：
1. **白名单放行**：四符加入 `QMT-API-WHITELIST` 允许集（否则策略调用会被 BLOCK）。
2. **denylist 复核**：确认四符**不在** `denylist()`（若在，须移除并登记原因）。
3. **分级**：四符放行建议为 **WARN 级不阻断**？——**待②审裁定**；默认按「已实现即放行（不告警）」。

**负向用例（必须成对，防"放行动作不可验证"）**：

| # | 用例 | 期望 |
| --- | --- | --- |
| N1 | 四符**未放行**时，策略调用 `get_orders()` | `QMT-API-WHITELIST` **BLOCK**（证明放行是有效开关，非恒真） |
| N2 | 四符放行后，策略调用四符 | **不 BLOCK**（正例对照） |
| N3 | 调用 `get_market_data()` 等 denylist 项 | 仍 **BLOCK**（证明未误伤 denylist） |
| N4 | 裸名 `get_position` / `get_positions` 等既有遮蔽 | 行为不变（回归） |

## 4.1 同批追加：POS 7 缺口 WARN ＋ 三处费用结构差异（2026-10-22 总调度正式令）

**立项依据**：总调度令「portability 校验器加 WARN 级规则——策略源码出现 `position.<七字段之一>` 时提示『本地未实现（PTrade 有，已裁接受缺口）』；**随 DEAL-07 批次同文件落地**（避免三次动 `source_import_qmt`/`portability_rules`）；三处费用结构差异的 portability 规则同批。」

| 新增规则（建议 ID） | 级别 | 触发 | 提示文案要点 |
| --- | --- | --- | --- |
| `QMT-POS-FIELD-UNIMPL` | **WARN** | 策略源码出现 `position.<today_long_amount｜today_short_amount｜business_type｜contract_multiplier｜long_pnl｜long_cost_basis｜delivery_date>` | 本地 `Position` 未实现该字段（PTrade 有）；已裁「接受缺口」，策略须改用替代口径 |
| `QMT-FEE-COMMISSION-TRI` | WARN | 转换产物/配置涉及佣金费率 | QMT 佣金**三项分设**（买入/平昨/平今）⇒ 本地单一费率须**三项同值映射** |
| `QMT-FEE-STAMP-BUY` | WARN | 配置含买入印花税 > 0 | 本地仅卖出印花税（A 股实际）⇒ QMT 侧**买入印花税须为 0** |
| `QMT-FEE-TRANSFER-ALIAS` | INFO/WARN | 配置含过户费 | QMT **无「过户费」专名** ⇒ 须并入「手续费类型+比例」并在文档明示 |

**落地约束（与 DEAL-07 同批）**：
- 四条规则与 DEAL-07 的四符遮蔽**同一 commit**（避免二次动 `portability_rules.py`）；
- 负向用例成对（命中即 WARN 且不阻断；未命中不误报）；
- 矩阵哈希：若仅动 `portability_rules.py`（非 wrapper 模板串）则**不需** `qmt_wrapper_hash`；若同批改 wrapper 则须同 commit 追认。

**证据**：POS 零消费普查 `docs/evidence/pos-gap-consumer-scan-20261022.md`；费用结构差异 `docs/evidence/qmt-backtest-panel-fee-qmch-20261022.md` §六。

## 5 改动范围（实施轮细化；判域 D2）

| 落点 | 改动 | 判域 |
| --- | --- | --- |
| `_QS_QMT_POSITION_EXT` 或新 `_QS_QMT_TRADE_EXT`（模板串） | 新增四符遮蔽 | **D2**（`_QS_*_EXT` 模板串） |
| `_QS_QMT_MODULE_HEADER`／`_QS_QMT_ALIAS_ZONE` | 若需导出四符 | D2 |
| `portability_rules.py` | 白名单放行 ＋ 负向用例 | D3（宿主侧；亦属框架层） |
| **矩阵** | `check_fund_matrix.py --check --reverify` → `qmt_wrapper_hash` **同 commit 追认** | 强制 |

**放位选择待实施轮定**：并入既有 `_QS_QMT_POSITION_EXT`（位置语义相近）vs 新建 `_QS_QMT_TRADE_EXT`（职责清晰，但**新增 `_QMT_HASH_SCOPE` 常量须同步登记**）。

## 6 验收判据（骨架版，实施轮细化）

1. **签名一致**：四符签名与本地逐字一致（`get_order(order_id)`／`get_orders(security=None)`／`get_trades()`／`get_open_orders(security=None)`）
2. **语义 1:1**：`get_trades()` 恒 `[]`；`get_open_orders()` 恒 `[]`；`get_order(x)` 恒 `None`；`get_orders()` 返回 13 字段 `Order` 对象，字段非空率与 ACOD 锚点一致
3. **负向用例 N1–N4 全过**（N1 必 BLOCK）
4. **端测**：QMT 端调用四符不抛 `NameError`；`get_orders()` 条数＝`_qs_get_trade_detail(C,'ORDER')` 条数
5. **矩阵门**：`--check` 通过（哈希同 commit）
6. **既有回归**：六策略契约门 `CONTRACT GATE : PASS`；相关 pytest 全绿
7. **oracle 会红**：注入「移除白名单放行」变异体 ⇒ N1 用例变红

## 7 回退条件

- 触发：矩阵门红／负向用例不过／六策略门红／端测 `NameError` 未消除
- 手段：单 commit `git revert`（含矩阵哈希）；写前快照四步配方（hash 记台账）
- 备选：四符实现体加 `_QS_QMT_TRADE_SURFACE_ENABLE` 开关（默认开，置 False 恢复缺位状态）——**待②审裁定是否加**

## 8 排期与依赖

| 前置 | 说明 |
| --- | --- |
| **QMCH 六步完成后** | 同 `source_import_qmt.py` 文件串行，禁并行编辑 |
| 统一推送批次 | 与当期在途批次合并推送（⑥另裁） |
| 选项裁定 | §2.1 A/B/C 须先生效 |

## 9 待裁定（呈②审）

1. **§2.1 选项 A/B/C**（建议 A；B 另立项；C 禁止）
2. `get_orders` 的 `order_id` 取值（`m_strOrderRef` vs `m_strOrderSysID`）
3. `target` 字段（QMT 无对应 ⇒ `0.0` ＋ 已知差异登记，是否接受）
4. 四符放行的 portability 分级（放行不告警 vs WARN）
5. 放位：并入 `_QS_QMT_POSITION_EXT` vs 新建 `_QS_QMT_TRADE_EXT`
6. 是否加 `_QS_QMT_TRADE_SURFACE_ENABLE` 开关

## 10 版本变更日志

| 时间 | 发起人 | 变更 | 影响 | 评审 | 里程碑 |
| --- | --- | --- | --- | --- | --- |
| 2026-10-21 | QMT 转换管线项目执行会话 | v0.1 骨架（四符语义实证＋选项三选一＋映射表＋负向用例） | 待定 | 待②审（排 QMCH 之后） | DEAL-07 |
