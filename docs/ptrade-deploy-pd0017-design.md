# PD-0017 方案：qs-deploy 交易态四缺陷批（data 代理 / 快照取值 / 拆单审计 / 陈旧挂单）（六步①方案）

> 立项依据：2026-11-02 模拟盘 14:50 实跑日志 + PTrade【委托】/【普通持仓】表截图归因（归因报告 m00944）。
> 状态：①方案 ✅ → ②审计 ✅（总调度 2026-11-02 裁定 B 通过：A/B/C/D 进批、E 登记不进批、D 撤单默认 false、兜底「②审延误先发 A+B」照准但本轮②审按期出故走全量）→ ③实施 ✅（轮 17；含 **③实施期新增发现 E′** 幂等台账跨日锁死，比 A 更致命，按铁律 08 当场纳入本批）→ ④验收 ✅（`docs/evidence/ptrade-deploy-pd0017-evidence.md`，七项读数）→ ⑤待用户确认 → ⑥待双仓推送。
> 用户要求（2026-11-02）：总结框架层修复问题 → 给具体修复方案 → 严格按全流程（含知识沉淀与知识反哺）→ 上报总调度审核 → 通过后加速推进，目标 **2026-11-03 换上新策略代码部署**。

---

## 1. 问题定义

### 1.1 触发场景

2026-11-02 模拟盘，14:50 档位同时运行三个策略：**全球资产轮动**（`strat_0c202b`）、**四象限ETF轮动策略**（`ETF_f926a7`）、**F-Score RSRS**（`149655`）。用户贴运行日志 + PTrade 交易端【委托】表 +【普通持仓】表，要求归因。

### 1.2 五现象定性（代码层 + 委托表逐位反算，非猜测）

| # | 现象 | 定性 | 框架缺陷 | 级别 |
|---|---|---|---|---|
| 1 | 全球资产轮动「每标的 2–7 笔相同数量订单」 | **误报**——49000 股/笔申报上限的拆单设计，非重复提交 | 否 | — |
| 2 | 护栏「参考快照最新价=None」 | `get_snapshot` 二层嵌套取值错误 | **是（B）** | P1（不阻断） |
| 3 | 四象限「512890 价格无效，无法计算目标数量」→ **整轮调仓中止** | `run_daily` 回调不传 `data`，策略取价链拿到 `None` | **是（A）** | **P0（阻断建仓）** |
| 4 | 「运行周期错误」告警 | 策略自检误报（挂载 14:50 vs 策略内部自检 15:00） | 非框架阻断 | P2（本批不进） |
| 5 | F-Score 全量正常（RSRS 正确，空仓+卖出信号⇒无单可下） | — | 否 | — |

### 1.3 现象 1 的逐位反算（证伪「重复下单」）

拆单算法（`source_import.py:894 _qs_split_order`）：`k = ceil(目标股数 / 49000)`，每笔 `= floor(目标市值 / k / 委托价 / 100) × 100`。

| 标的 | 目标市值 | 委托价 | 目标股数 n | k | 每笔股数 | 预测合计 | 实际（委托表/持仓表） |
|---|---|---|---|---|---|---|---|
| 511260 十年国债 | 600 万 | 135.489 | 44,287 | 1 | 44,200 | 44,200 | 持仓 44,200 ✓ |
| 518880 黄金ETF | 50 万 | 7.950 | 62,893 | 2 | 31,400 | 62,800 | 持仓 62,800 ✓ |
| 159985 豆粕ETF | 50 万 | 2.182 | 229,147 | 5 | 45,800 | 229,000 | 持仓 229,000 ✓ |
| 501018 南方原油 | 50 万 | 2.250 | 222,222 | 5 | 44,400 | 222,000 | 持仓 222,000 ✓ |
| 159687 亚太精选 | 50 万 | 1.705 | 293,255 | 6 | 48,700 | 292,800 | 持仓 292,800 ✓ |
| 159980 有色ETF | 50 万 | 2.094 | 238,777 | 5 | 47,700 | 238,500 | 持仓 238,500 ✓ |
| 513500 中概互联 | 50 万 | 2.644 | 189,107 | 4 | 47,200 | 188,800 | 4 笔全挂单未成交 |
| 159941 纳指100 | 50 万 | 1.650 | 303,030 | 7 | 43,200 | 302,400 | 5 笔挂单未成交（滚动区外） |
| 513030 德国ETF | 50 万 | 1.775 | 281,690 | 6 | 46,900 | 281,400 | 3 笔挂单未成交（滚动区外） |

**9/9 标的逐位吻合**（k 值与每笔股数），且与用户日志「513500×4、159941×7、513030×6、518880×2、511260×1」完全一致。

**账实自洽**：已成交持仓市值 8,477,847.80 + 可见挂单冻结 1,105,369.70 ≈ 账户总资产 10,000,000（余约 416,782）→ 无重复扣款、无资金超买。

> **结论**：现象 1 **不是缺陷**，但其暴露了两个真实缺陷——见 §1.5 的 C 与 D。

### 1.4 根因（逐条带取证行号）

**A（P0）— `run_daily` 回调不传 `data`，策略取价链拿到 `None`**

- 平台事实：Context7 `/kay-ou/ptradeapi` 官方文档载明 `run_daily(context, func, time)` 的 func 签名为 `Callable[[Context], None]`，官方示例一律 `def get_finance(context):` —— **只传 context，不传 data**。
- 框架侧：`quantstudio/ptrade_deploy/transformer.py:556-565` 生成挂载包装 `def _deploy_daily_exec(context, data=None)`（L562 `defaults=[ast.Constant(value=None)]`），函数体为源 `handle_data` 原样内联（L563）。
- 策略侧：`Demo_account_Real_account/四象限ETF轮动策略/ETF_f926a7_trade.py:997-1015` `get_market_price(security, data, position=None)` → L1001 `if security in data:` 抛 `TypeError: argument of type 'NoneType' is not iterable` → L1009 `except` 吞掉 → L1012 `position.last_sale_price`（首日无持仓）→ 返回 `0.0` → 「价格无效」→ L1176 `return None` **整轮调仓中止**。
- 为何只影响部分标的：512890.SS 是 BEAR 象限 `weight_map` 首个 key（L896 `etf_pool`），**首个失败即中止**，与「部分委托未创建、保留标记下一交易日」完全吻合。

**B（P1）— `get_snapshot` 二层嵌套取值错误**

- 平台事实：Context7 `/kay-ou/ptradeapi` 官方示例 `snapshot[stock]['last_px']` → `get_snapshot(security)` 返回 `{code: {last_px: ...}}` **二层嵌套**。
- 框架侧：`quantstudio/ptrade_deploy/templates/guard_template.py:67`
  `_px = _snap.get('last_px') if isinstance(_snap, dict) else _snap` —— 在**外层**找字段名 → 恒 `None`。
- 影响：仅参考价日志 / F8 提示，**不阻断下单**。

**C（P1）— 拆单的 k 笔委托只有末笔被登记进审计台账**

- 框架侧：`quantstudio/strategy_compiler/source_import.py:1120-1123`（`order_target_value` 包装）
  `_ids = []; for _code, _amt2 in orders: _ids.append(order(_code, _amt2)); return _ids[-1]`；
  `order_value` 包装同构（L1156-1159）；`order` 包装直拆路径返回末笔（L1134-1143）。
- 策略侧：`Demo_account_Real_account/全球资产轮动/strat_0c202b_trade.py:1163` `g.pending_order_audit[str(order_id)] = {...}` **只登记末笔**（`submit_target_value_order` 拿到的是 `_ids[-1]`）。
- 后果：`audit_pending_orders`（L1198-1258）遍历 `g.pending_order_audit` 的键调 `get_order(order_id)` → **k−1 笔拆单委托永远不被审计**。
- 可安全代登记的依据：该审计函数对委托字段访问**全部用 `.get()`**（仅 `order_info['audit_count']` 为自写字段）→ 框架代填最小 dict 不会破。

**D（P1）— 未成交挂单无处置，存在跨日双买风险**

- 事实：截图 15:53 仍显示 15 笔「已报」挂单、成交 0、挂单全额；**部署包零 `cancel_order` 调用**。
- 事实：`get_orders()` 契约备注为「**当日**订单列表」→ 跨日未成交委托在次日**未必能被查出**（依赖平台日终撤单行为，未取证）。
- 风险：若平台不自动撤单 → ①次日误成交；②策略次日再下单 = **双买**；③尾盘 14:50 限价单未成交时目标仓位缺失（本次 513500 / 159941 / 513030 三只零持仓）。

**E（P2，本批不进）—「运行周期错误」告警**

挂载时点 14:50 ≠ 策略内部自检常量 15:00。属策略自检口径与框架档位时点的**表述不一致**，不影响下单；且框架无法可靠识别「哪个字面量是调度自检时点」，强行改写有误改风险。**登记为遗留项，不进本批**（见 §8）。

### 1.5 本批范围判定

| 子项 | 进本批 | 理由 |
|---|---|---|
| A data 代理 | ✅ **P0** | 阻断四象限建仓；用户 11-03 换新代码部署的硬前提 |
| B 快照取值 | ✅ P1 | 一行修复，风险极低，消除日志假象 |
| C 拆单全量登记 | ✅ P1 | 审计覆盖缺口；本次 15 笔挂单中 14 笔未被审计 |
| D 陈旧挂单巡检 | ✅ P1（**默认只告警，撤单默认关闭**） | 跨日双买风险；但平台日终撤单行为未取证 → 撤单做成默认关闭的开关，不阻塞部署 |
| E 时点自检对齐 | ❌ 登记 | 告警非阻断；框架识别不可靠 → 零越权原则下不擅动 |

---

## 2. 平台文档依据（铁律 16，先查文档再动手）

依据 Context7 `/kay-ou/ptradeapi`（2026-11-02 查证）：

| API | 官方事实 | 本方案用途 |
|---|---|---|
| `run_daily(context, func, time)` | func 签名 `Callable[[Context], None]`，**只传 context** | A 的根因与设计前提 |
| `get_snapshot(security)` | 返回 `{code: {last_px: ...}}` **二层嵌套** | B 的修复口径 |
| `get_history(count, frequency='1d', field='close', security_list=None, fq=None, include=False)` | 双端可用（契约表 L270） | A 代理的取价兜底 |
| `get_orders(security=None)` | 返回 Order 列表（含 `id/dt/priceGear/limit/symbol/amount/created/filled/status/entrust_no`）；备注为**当日**订单列表 | D 的查询面 |
| `get_open_orders(security=None)` | 契约表 L378（PD-0013 补）；语义为未完成委托 | D 的跨日查询面（优先） |
| `get_order(order_id)` | 查单笔委托 | D 的详情读取 / C 的审计读取 |
| `cancel_order(order_id)` | 按单号撤单，**双端可用**（契约表 L414-422） | D 的可选撤单路径 |

**契约表结论**：本批**无需改动 `contract_table.yaml`**——`get_snapshot`(L115)、`get_orders`(L360)、`get_order`(L369)、`get_open_orders`(L378)、`cancel_order`(L414) 全部已有行且 `trading: true`。这是「零契约变更」的证据，也是本批能压缩到明天可交付的关键。

---

## 3. 知识反哺检索留痕（R1 开工留痕 · 铁律 21/22）

```
知识反哺：[命中] PD-2026-0006（PD/D3/knowledge/ptrade-deploy/PD-2026-0006.md，2026-10-11）—— 仅覆盖「run_daily **源形态**识别与重挂」，未覆盖「run_daily 回调不传 data 的取价代理」⇒ 相关不覆盖
知识反哺：[命中] PD-2026-0007（PD/D3/knowledge/ptrade-deploy/PD-2026-0007.md，2026-10-15）—— 混合形态（stub + 单一 run_daily 挂载）识别，与本批 A 无交集 ⇒ 相关不覆盖
知识反哺：[命中] PD-2026-0016（PD/D3/knowledge/ptrade-deploy/PD-2026-0016.md，2026-11-01）—— 护栏四段在位断言仅验证 `get_snapshot` **在位**，未验证快照**取值形态** ⇒ 覆盖空白，本批 B 即该空白的填充
知识反哺：[无命中] PD 族其余 12 条（检索面：run_daily data=None 代理 / get_snapshot 二层嵌套取值 / 拆单 49000 上限与审计登记 / 未成交陈旧挂单处置），2026-11-02
```

**结论**：本批四缺陷在 PD 族**均为首次出现**，不存在「同坑复发」（R_i>0）风险；知识库存在两处**覆盖空白**（PD-2026-0016 的快照取值、PD-0006/0007 的 data 代理），由本轮 PD-2026-0017 填补。

---

## 4. 改动范围（6 文件，全部框架层；策略源码零改动 · 铁律 07）

| 文件 | 子项 | 改动 | 性质 |
|---|---|---|---|
| `quantstudio/ptrade_deploy/templates/data_proxy_template.py`（新建） | A | 产出 `_qs_data_proxy()` 模块级无状态代理（`__contains__`/`__getitem__`/`get`/`keys`），返回支持 `['close']` 与 `.close` 的 dict 子类 | 新增文件 |
| `quantstudio/ptrade_deploy/transformer.py` | A/D | ①挂载包装默认值 `data=None` → `data=_qs_data_proxy()`（L562）；②`run_daily` 注册目标由 `_deploy_daily_exec` 改为 `_deploy_daily_entry`（L601-608）；③新增 `DATA_PROXY` / `DAILY_HOOK` 两个标记块装配 | 新增能力 |
| `quantstudio/ptrade_deploy/equiv.py` | A/D | 新增两个标记种类的归一化 handler（`DATA_PROXY`/`DAILY_HOOK` 整体剔除，与 `GUARD`/`PERSIST` 同级）→ **P4 等价性零影响** | 新增 handler |
| `quantstudio/ptrade_deploy/templates/guard_template.py` | B/D | ①L67 快照取值改二层兼容；②新增 `GUARD_STALE_ORDER_BLOCK` 分段（每日一次的陈旧挂单巡检，默认只告警） | 修复 + 新增分段 |
| `quantstudio/strategy_compiler/source_import.py` ⚠ | C | 拆单三处路径（`order_target_value` L1120-1123 / `order_value` L1156-1159 / `order` L1134-1143）改为**登记全部委托号**到 `g.pending_order_audit`（受 guard 保护）+ 框架自持注册表 | **共享核心文件 · 铁律 10** |
| `quantstudio/ptrade_deploy/gates.py` | A/D | P5 新增断言：产物必须含 `_qs_data_proxy` 且挂载默认值已代理化（防回退） | 新增门禁 |

**不动**：`contract_table.yaml`（**零契约变更**，§2 已证）、`profiler.py`、`config.py`、`runner.py`、`deliver.py`、本地回测引擎 `quantstudio/backtest/*`、任何策略源码与产物。

---

## 5. 设计决策

### D1（A）代理取价与下单换算价**同源** ★关键决策

代理的 `close` 必须与 `order_target_value` 的股数换算价**同源**，否则「目标数量」与「下单金额」自相矛盾。

取价优先级（逐级回退，全部 fail-safe）：
1. `_qs_px_exec(code)` —— 框架 shim 既有函数（`source_import.py:989-1011`），语义为「当日撮合价优先（`get_history(include=True)` → `include=False`）→ 前收缓存 → 0.0」，**正是 `order_target_value` 的换算价**；
2. `_qs_last_close_lookup(code)` —— 前收缓存（`source_import.py:1174-1189`）；
3. `get_history(count=1, frequency='1d', field=['close'], security_list=code, fq='pre', include=False)` —— 直接兜底；
4. `0.0` —— 取不到价。

与铁律 17（E1）的关系：日线信号源一律读 D-1；本代理取的是「平台当前可得的最新收盘」，与 `_qs_px_exec` 同链，与本地回测 D4-S7 #8 换算价**同口径**，不引入新的时点语义。

### D2（A）代理必须**无状态**（默认值在 def 时求值一次）

Python 的默认参数在函数定义时求值一次并绑定 ⇒ `_qs_data_proxy()` 的返回值会被**所有调用共享**。因此代理对象**不得缓存价格**，每次 `__getitem__` 重新解析 → 隔日/隔 tick 不会读到陈价。

### D3（A）降级语义与今日行为**同构**

取不到价 ⇒ `close = 0.0` ⇒ 策略的 `if price > 0` 判定不通过 ⇒ 自然回落到 `position.last_sale_price` 分支 ⇒ **与今日（data=None → 抛异常 → except → 同一回落路径）行为一致**，不产生新的行为分叉。

### D4（A）P4 等价性**结构性零影响**（已核实，非推断）

`equiv.py:110-134 _handle_mount` 形态一把包装函数**签名重写**为 `(context, data)` 且 `defaults=[]` ⇒ 产物侧默认值改写在校验时被归一化抹除；
`run_daily` 注册语句（MOUNT 标记，`transformer.py:606-607`）在 P4 归一时**整条剔除**（形态二 `_droppable`）⇒ 注册目标改名不影响等价性；
`_qs_data_proxy` / `_deploy_daily_entry` 所在的 `DATA_PROXY` / `DAILY_HOOK` 标记块**整体剔除**（与 `GUARD`/`PERSIST` 同级）。

⇒ **P4 等价 diff 预期零回退**。此结论须在③实施后以实跑 P4 复核为准。

### D5（B）快照取值**双形态兼容**（一层/二层都支持）

```python
_snap = get_snapshot(security)
_px = None
if isinstance(_snap, dict):
    _inner = _snap.get(security)
    if isinstance(_inner, dict):        # 二层嵌套（PTrade 实际形态）
        _px = _inner.get('last_px')
    else:                                # 一层/异常形态兜底
        _px = _snap.get('last_px')
        if _px is None and isinstance(_snap.get(security), (int, float)):
            _px = _snap.get(security)
if _px is None and not isinstance(_snap, dict):
    _px = _snap
```
兼容而非替换：若某平台版本返回一层，行为不退化。

### D6（C）登记**全部**委托号，但**不改订单序列**

拆单三处路径改为：`for ...: _ids.append(order(...)); _qs_register_split_order(_code, _oid)`；返回值仍为**末笔**（`_ids[-1]`）⇒ **策略侧拿到的 order_id 语义完全不变**（P4 无关、行为无关），只新增审计登记。
登记目标：
- 策略台账 `g.pending_order_audit[str(oid)] = {'security': code, 'asset_name': '', 'stage': 'split', 'action': 'auto'}`（受 `isinstance(getattr(g,'pending_order_audit',None), dict)` 保护，无台账时静默跳过）；
- 框架自持注册表 `_QSOrderRefState.split_orders`（供 D 用）。
策略自身在 `submit_target_value_order` 的登记（末笔）仍照旧执行，会**覆盖**同键的框架最小字段为更完整字段（值更全、键相同）⇒ 无冲突。

### D7（D）默认**只告警不撤单**，撤单为默认关闭的显式开关

新增配置 `guards.stale_order_check`（默认 `true`，即巡检告警默认开启）与 `guards.cancel_stale_orders`（默认 `false`）。
- 默认行为 = 巡检 + `log.warning` 告警，**不撤单** ⇒ 对既有行为零影响（纯增益）；
- 撤单需用户在 deploy.yaml 显式开启（`cancel_stale_orders: true`）⇒ 不可逆动作留用户闸门（铁律：不可逆动作保留用户确认闸门）。
- 日戳来源优先级：`context` 上的策略日期属性 → `g` 上的策略日期 → **框架自持「上次 exec 日戳」**；三者皆不可得 ⇒ **跳过陈旧判定**（宁可不告警，绝不误撤）。
- 撤单前置硬门：仅当「该委托号由本框架提交」（在 `_QSOrderRefState.split_orders` 或策略台账内）且「日期可确证跨日」且「状态非终态」三条同时成立才撤。

### D8（E）不擅动策略层时点自检

「运行周期错误」是策略自检常量与框架档位时点的表述差异。框架侧无可靠识别手段（策略里可能有多个时间字面量）⇒ 按零越权原则不进本批，登记为遗留项，留待专门轮次以「档位时点 → 暴露为模块级常量」的方式消歧。

---

## 6. 影响面（修复前置纯增益审计 · 铁律 18 三型判定）

| 子项 | 判定 | 理由 | 零影响面证据 |
|---|---|---|---|
| A data 代理 | **新增能力型** | 新增能力；既有路径（`data` 非 `None`）行为完全不变 | 代理只在 `data` 为 `None` 时生效；默认参数改写在 P4 归一时被抹除 |
| B 快照取值 | **纯恢复型** | 恢复到平台真实取值形态；不改变任何下单判定 | 仅影响一条 INFO 参考价日志；不改 `_px` 的下游用途（当前仅打印） |
| C 拆单登记 | **纯恢复型** | 恢复「拆单委托全部纳入审计」；**不改订单序列、不改股数、不改返回值** | 订单序列逐位不变（`_qs_split_order` 未动）；返回值仍 `_ids[-1]` |
| D 陈旧巡检 | **新增能力型**（默认关闭撤单） | 新增巡检告警；默认路径不改任何下单/撤单行为 | 默认 `cancel_stale_orders=false` ⇒ 不调用 `cancel_order`；日戳不可得 ⇒ 跳过 |

**跨项影响**：
- **本地回测侧零交集**：`_qs_split_order` 在本地为 `quantstudio/backtest/ptrade_api.py:3456` 独立实现；本批 C 只改 PTrade 侧 `source_import.py` shim，且**只加登记不改订单序列** ⇒ 双端订单序列仍逐笔一致（`source_import.py:862` 注释所述同构约束不被破坏）。
- **既有测试零衰减**：新增分支/新增 handler/新增门禁，不改既有判定路径；验收以「deploy 全量 pytest ≥ 基线（当前 134 passed / 1 skipped）」为证。
- **共享核心文件风险**：`source_import.py` 属铁律 10 共享核心文件 ⇒ 提交前建回退点（四步配方）+ **精确文件清单 add**（禁 `git add -A`）+ **wrapper_hash 变化 ⇒ 同 commit 带 `check_fund_matrix.py --check --reverify` 的矩阵 yaml/md 哈希**。
- **纯增量审计（铁律 18 前置三问）**：①是否影响项目其他功能——仅影响 PTrade 部署产物；②是否影响回测性能——无（回测侧零改动）；③是否影响回测精度——无（回测侧零改动，订单序列不变）⇒ 纯增益成立。

---

## 7. 验收标准（七项 · ④验收逐条读数）

| # | 验收项 | 判据 |
|---|---|---|
| 1 | 新增固件与测试 | `tests/test_deploy_pd0017.py`：A（代理 `__contains__`/`__getitem__`/降级/无状态）、B（一层/二层双形态）、C（拆单 k 笔全量登记 + 返回值仍末笔）、D（陈旧巡检只告警不撤单 + 日期不可得跳过）——预计 12–16 用例，全绿 |
| 2 | 既有回归零衰减 | `py -3.11 -m pytest tests/test_deploy_*.py` **≥ 基线 134 passed / 1 skipped**；既有 `tests/` 全量无新增红 |
| 3 | 三策略重转 P0–P6 全 PASS | `qs-compile deploy "output/.qs-deploy-quick/strat_0c202b.yaml"`（全球资产轮动）、`ETF_f926a7.yaml`（四象限）、`F-Score_RSRS_149655.yaml`（F-Score）三条命令 **exit=0 且 P0–P6 全 PASS** |
| 4 | 产物级证据 | 新产物含 `def _deploy_daily_exec(context, data=_qs_data_proxy())` 与 `_qs_data_proxy` 定义；guard 快照段为二层兼容形态；shim 拆单三路径含全量登记；`DATA_PROXY`/`DAILY_HOOK` 标记块可被 P4 完整剔除（**P4 等价 diff 零回退**） |
| 5 | 矩阵哈希追认 | `py -3.11 scripts/check_fund_matrix.py --check --reverify` PASS（因 `source_import.py` wrapper 变更 ⇒ wrapper_hash 变） |
| 6 | 知识沉淀机检 | `py -3.11 scripts/kb_hook.py --files knowledge/ptrade-deploy/PD-*.md --strict` PASS（应 ≥ 13/13，新增 PD-2026-0017） |
| 7 | 模拟盘复跑 | 2026-11-03 14:50（或次日）实跑核对：①四象限**不再出现**「价格无效」，且产出委托；②全球资产轮动审计台账覆盖**全部**拆单委托号（非仅末笔）；③护栏参考价**非 None** |

**负向对照（必做，防止"假通过"）**：构造「读到的条目与本次失败无关」与「非本框架提交的委托」两个负例，验证 D 不告警/不撤单；构造 `data` 已是 dict 的情形，验证代理**不介入**（零行为漂移）。

---

## 8. 回退条件

- **子项粒度**：A/B/C/D 各自独立可回退（建议按子项分 commit），任一项失败即 revert 对应 commit，既有路径天然不受影响。
- **整体回退**：`git revert` 本批全部 commit ⇒ 回到 2026-11-02 状态（四象限 P0 缺陷复现），无数据迁移、无持久化格式变更。
- **回退触发**：任一验收项失败且当轮无法闭环；或②审 /⑤用户确认阶段要求调整方向。
- **不可逆动作闸门**：`cancel_order`（D 的撤单分支）默认关闭；一旦显式开启即产生不可逆的外部动作 ⇒ 该开关的启用必须由用户单独确认，本批**不代用户开启**。

---

## 9. 知识沉淀与反哺全流程（用户明确要求 · 铁律闭环收敛）

| 阶段 | 动作 | 载体 |
|---|---|---|
| ①方案 | R1 留痕（本文件 §3）+ 立项登记 | 本文件 + 台账 §4.13 |
| ②审计 | 审核四条：修复口径 / 零契约变更论证 / P4 零影响论证 / 反哺落点可机检性 | 审核意见 |
| ③实施 | 起草 KB 条目 `PD-2026-0017`（`scripts/kb_draft.py`，`status: open`，`fix_layer: transformer+template+kernel`） | `knowledge/ptrade-deploy/PD-2026-0017.md` |
| ④验收 | **R4 沉淀**：条目 `status → closed`，回填 `fix_target`（文件+行）/ `evidence`（证据文档路径）/ `fixture`（测试路径） | 同上 + `docs/evidence/ptrade-deploy-pd0017-evidence.md` |
| 反哺 | **知识库驱动回哺**三载体：①**模板**（`data_proxy_template.py` + `guard_template.py` 修复本体）②**校验规则**（`gates.py` 新增产物断言：`_qs_data_proxy` 必须在位且默认值已代理化 ⇒ 未来转换自动受检）③**巡检脚本**（`scripts/check_deploy_closedloop.py` 增设产物级巡检项：data 代理在位 / 拆单全量登记在位） | 代码 + 脚本 |
| ⑤/⑥ | 台账 §4.13 登记各阶段读数；双仓推送后同步门 | 台账 + Git |

**可机读回哺判据（铁律：条目须可机读回哺，禁止散文式笔记）**：
- 症状：检索串 = 「价格无效」「参考快照最新价=None」「拆单未审计」「陈旧挂单」
- 根因 → 修复落点 → 验证证据 → 复现固件 → 回哺方式（模板改动 + 门禁断言 + 巡检项）五要素齐备
- **下一次转换的自动回灌**：新产物若缺 `_qs_data_proxy` ⇒ P5 门禁 FAIL ⇒ 知识库条目被真实触发（不是"写完就躺"的笔记）

---

## 10. 排期（加速版 · 目标 2026-11-03 换新代码部署）

| 时段 | 阶段 | 交付 |
|---|---|---|
| 11-02 夜 | ①方案 + 上报总调度 | 本文件 + 台账 §4.13 + KB 条目 open |
| 11-02 夜 – 11-03 01:00 | ②审计通过 → ③实施（**委派 zcode_code**，GLM） | 4 个子项 commit + 回归 |
| 11-03 01:00 – 04:00 | ④验收（七项读数 + 负向对照 + 证据件落盘） | `docs/evidence/ptrade-deploy-pd0017-evidence.md` + KB 条目 closed |
| 11-03 04:00 – 06:00 | ⑤用户确认 | 用户确认回执 |
| 11-03 06:00 – 08:00 | ⑥双仓推送 + 同步门（trading 副本） | 双远程逐位一致 |
| 11-03 08:00 – 11:00 | **三策略重转部署包** → 交付新代码到 `Demo_account_Real_account/` | 新版 `strat_0c202b_trade.py` / `ETF_f926a7_trade.py` / F-Score 包 |
| 11-03 14:50 | 模拟盘复跑验证（验收项 7） | 实跑日志 + 委托表 |

**风险与预案**：
- 若②审在 11-03 02:00 前未回 ⇒ **兜底方案**：先只发 **A+B 两项**（A 是四象限阻断的最小充分修复、B 是一行）先出仓保住 11-03 部署窗口，C/D 随下一批。
- 若 `source_import.py` 改动触发矩阵哈希连锁 ⇒ 矩阵追认随同 commit 一并完成（铁律 10 要求，不接受"CI 再补"）。
- 四象限在 A 修复前**次日 14:50 会再次同样失败** ⇒ 建议用户在修复落地前**暂停四象限**或接受该轮空仓。

---

## 11. 请示裁定（呈总调度 / 用户）

1. **是否照准本方案**（含范围收敛：A/B/C/D 四项进批、E 登记不进批；D 默认只告警不撤单）？
2. **是否照准兜底方案**（②审延误则先发 A+B 保部署窗口，C/D 次批）？
3. **D 的撤单开关**：默认 `false` 保持不变？是否需要在本批内就为四象限/全球资产轮动**显式开启**？（开启即产生不可逆外部动作，需用户单独确认）
4. **PTrade 日终是否自动撤单**（用户侧可查证）：若平台自动撤单 ⇒ D 的告警为冗余保险；若不撤 ⇒ D 的「只告警」不足以覆盖跨日双买风险，需在 11-03 前升级为「显式撤单」。
5. **四象限在修复落地前的处置**：暂停 / 接受空仓一轮 / 其他。
