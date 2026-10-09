# 架构设计：本地策略 → 大 QMT（迅投内置 Python）转换管线

> 状态：**方案稿 v0.1（待审计）** —— 与 docs/qmt-conversion-prd.md 配套；六步流水线第①步。
> 原则：**镜像 PTrade 管线既有架构**，一切「双端对齐」机制对齐 PTrade 先例（单一规则源 / 注入 wrapper / fail-closed 校验器 / 矩阵哈希 CI / 保真开关默认关）。
> 文档依据标注约定：〔QS-qmt：<文件>:<行>〕= 本仓 docs/qmt/inner-api/ 转写文档；〔C7〕= Context7 /websites/dict_thinktrader_net。

---

## 1. 参照系：PTrade 管线现状（as-is）

```
策略源码(PTrade契约形) ──qs-compile import──▶ source_import.convert_source
     │  AST 定位 + 行级改写（禁纯正则）          │
     │  别名归一 / REMOVE 三档 / SHIM 注入        ├─ portability_rules.py（规则单一来源）
     ▼                                          ▼
转换产物 + ConversionAction 留痕 ──▶ validate_ptrade_portability（校验器）
                                        │
              check_fund_matrix（wrapper 模板哈希）──▶ CI contract-gate（push main）
本地对齐：ptrade_api.py(本地引擎) vs 产物 → FidelityComparator + ptrade_baseline.py 黄金对照
```

要点（直接继承）：①规则清单转换器/校验器**共用单一文件**防漂移；②每动作留痕 ConversionAction；③幂等 INJECTED_MARKER；④SHIM_CONTRACT_REGISTRY 未登记即 BLOCK；⑤契约矩阵哈希与代码同 commit（铁律）。

## 2. 目标架构（to-be）

新增 QMT 平行管线，**不动 PTrade 任何一行**（纯增益）：

```
                       ┌────────────────────────────────────────────┐
策略源码(本地,PTrade形) ─┤ qs-compile qmt / GUI「转 QMT」tab           │
                       └──────────────┬─────────────────────────────┘
                                      ▼
                  quantstudio/strategy_compiler/qmt_source_import.py
                  （结构层转译：生命周期/定时任务/入口重排，AST+行级）
                                      │ 注入
                                      ▼
                  qmt_wrappers（产物内嵌 helper 区，GBK）
                   ├─ 行情：_qs_get_history / _qs_current_price …
                   ├─ 宇宙：_qs_get_astocks / _qs_get_etf_list（固化池）
                   ├─ 财务：_qs_get_fundamentals / _qs_get_fundamentals_batch
                   ├─ 持仓：_qs_get_positions（get_trade_detail_data 归一）
                   └─ 下单：_qs_order_target_value …（→ passorder）
                                      │
              ┌───────────────────────┴───────────────────────┐
              ▼                                               ▼
 qmt_portability_rules.py（规则单一来源）      validate_qmt_portability.py
 （PASS/SHIM/REMOVE/BLOCK/GAP 登记册）         （fail-closed：未登记即 BLOCK）
              │
              ▼
 qmt-contract-matrix.yaml/md（wrapper 模板哈希）→ run_contract_gate 扩展 → CI
                                      │
 本地对齐验收：quantstudio/backtest/qmt_sim/（ContextInfo 模拟器 + handlebar 驱动）
   └─ 与本地回测引擎同数据窗口黄金对照（信号/订单/持仓/净值逐位）
```

### 2.1 单源三目标渲染（难点1 裁定：不生成两份）

本地策略**只生成一份**（PTrade 契约形，skill QuantStudio-only 铁律不变）；PTrade 与 QMT 各自的转换管线从同一份源码出发，QMT 是**追加渲染目标**而非第二源。依据：
- 仓库既有先例：G4 CLI 本就是 spec → dual-render（QuantStudio + Strict-PTrade，cli.py 文档串）；QMT = 第三目标；
- 双份本地策略 = 逻辑分叉怪圈：每次策略迭代同步改两份，比平台对齐更早失控；且违背「skill 只产本地代码 + 转换管线承接平台」分工（2026-07-26 历史教训的制度化分工不动摇）；
- 本地 API 面以 PTrade 契约为锚**不动摇**（既有双端对齐根基）；QMT 差异全部在转换器规则 + 注入 wrapper 消化（§4 映射总表）——「修复仅限框架层」铁律的平台延伸；
- 未来可选（不在本期）：spec→IR 路径（build_strategy_ir.py）原生增加 QMT renderer；本期镜像 source_import 源码入口路径。

#### 2.1.1 对齐语义澄清：对齐的是「意图无损」，不是「平台相同」（2026-10-07 用户问询落档）

一份策略代码适配双平台的机理——**策略代码只表达意图；意图→平台原语的翻译全部发生在产物适配层；运行时差异（撮合）交给验收体系**：

| 差异类别 | 例子 | 吸收点 |
|---|---|---|
| 生命周期契约不同 | handle_data vs handlebar(C) | **结构层转译**：转换器重排产物骨架（handlebar + _qs_should_run_daily 门控）；改的是产物，不是策略源码 |
| API 函数不同 | order_target_value vs passorder；get_history vs get_market_data_ex | **注入 wrapper**：同一契约、两平台实现。本地 ptrade_api.py 本就是「DuckDB 上实现 PTrade 契约」的适配器（策略从不是直接跑在 PTrade 上，而是跑在实现该契约的运行时上）；QMT wrapper 是同一模式的第二实例 |
| 撮合/核算机制不同 | 成交价口径、费用、成交时机 | **验收体系分层**：信号/订单意图序列逐位一致 = 硬门（A2，与撮合无关）；核算差异 = 运行时口径，入 registry MCH 条目三态仲裁（可配置对齐则对齐、平台固有则豁免声明）。**此层与代码份数无关——生成两份代码也不会让两个撮合引擎变相同** |

例：`order_target_value('000001.SZ', 10000)` 的三端旅程——本地：ptrade_api 实现差额→引擎撮合；QMT 产物：`_qs_order_target_value`（注入 wrapper）查 get_trade_detail_data 持仓→算差额→passorder 下单→QMT 回测撮合；A2 验收比对两侧**订单意图**（卖 X 股/买 Y 股）逐位一致，成交价差若存在→MCH 条目登记。真出现 QMT 无法等价表达的意图→A10 能力门在生成期拦截（WARN/BLOCK+用户裁定），差异永不被静默吞掉。

#### 2.1.2 QMT 产物解剖：三成分构成，策略逻辑原文照搬（2026-10-07 二轮问询落档）

QMT 转换**不重写策略逻辑**——产物由三部分构成，逻辑原文照搬：

| 成分 | 来源 | 变化频率 |
|---|---|---|
| ① 注入 wrapper 层（文件头） | 框架层统一模板（qmt_wrappers，哈希入矩阵 D7） | 低：所有策略共用一份，改一次全策略生效 |
| ② 策略逻辑（函数体主体） | 本地策略源码**原文照搬**；仅 API 调用点被 AST 行级改写为 _qs_* 并显式传 C（留痕同款） | 随策略自身迭代，与平台无关 |
| ③ 结构骨架（生命周期壳） | 转换器模板：init / handlebar + _qs_should_run_daily 门控 / run_time 注册 | 低：模板固定 |

对照示例（节选，本地 → 产物）：

```python
# 本地（唯一一份，永不改）                          # QMT 产物（转换器生成，GBK）
def handle_data(context, data):                    def handlebar(C):
    hist = get_history('000001.SZ', 20, '1d',           if not _qs_should_run_daily(C):   # ③骨架
                       ['close'], include=False)            return
    if hist['close'].iloc[-1] > hist[0]:                hist = _qs_get_history('000001.SZ', 20, ... C)  # ②调用点改写
        order_target_value('000001.SZ', 10000)          if hist['close'].iloc[-1] > hist[0]:            # ②逻辑原文
                                                            _qs_order_target_value('000001.SZ', 10000, C)
# ①文件头另注入 wrapper 层：
#   _qs_get_history      → C.get_market_data_ex + 剔除当日bar（E1 固化）
#   _qs_order_target_value → get_trade_detail_data 持仓 + 差额 + passorder 下单
```

两管线的机制**同构**，差别只在契约距离：本地=PTrade 契约 → PTrade 转换=删（本地专用 API）+垫（批量 shim）+归一（参数/复权），wrapper 薄；QMT 契约距离大 → wrapper 厚（生命周期+数据+下单全垫）。**撮合机制不在转换器范围，但在对齐工程范围（D10/§5.2）**：产物以 passorder 表达订单意图后，成交由 QMT 回测引擎撮合；转换器不复刻撮合，但撮合口径的对齐由三层机制承担——可配置口径钉「回测配置清单」、引擎固有口径探针标定后由引擎 qmt 档复刻（match_profile 开关，D11）、残余差异走 registry MCH 条目三态仲裁。

### 2.2 生成期双平台能力门（防怪圈入口，A10）

skill R1/R3 能力检查新增产出：**策略 API 消耗清单**（manifest）。逐项对照双平台登记册：

```
策略 API 消耗清单 → PTrade 登记册? → QMT 登记册?
  ├─ 双绿 → 放行（两条转换管线各取所需）
  ├─ QMT 侧缺映射（未登记/在 GAP） → WARN/BLOCK（用户裁定：单平台策略显式放行，仅转 PTrade）
  └─ 双侧缺 → BLOCK（本就不应出现）
```

映射状态唯一来源：qmt_portability_rules.py（机器面）↔ knowledge/registry.md QMT 维度（账面），**稳定 ID 互链，禁双源漂移**（同 portability_rules.py 转换器/校验器共用先例的哲学）。此门把「生成后才发现对不齐」前移为「生成时锁定可移植面」。

## 3. 生命周期映射（结构层转译核心）

| 本地（PTrade 形） | 大 QMT 内置 | 依据 |
|---|---|---|
| `initialize(context)` | `def init(C)` + 池/参数初始化 | 〔QS-qmt:06-系统函数.md:436〕 |
| `handle_data(context, data)` | `def handlebar(C)` 内 **barpos/period 门控**（每日末 bar 一次语义） | 〔QS-qmt:01-快速开始.md:215〕 |
| `run_daily(time, func)` | `C.run_time(func, time)`（注册于 init） | 〔QS-qmt:01-快速开始.md:333; 06-系统函数.md:222〕 |
| `before_trading_start` / `after_trading_end` | run_time 映射（盘前/盘后时点）；无可映射原生生命周期 → wrapper 固化触发时序并单测锁定 | 〔QS-qmt:06-系统函数.md:222〕 |
| 订单/成交回调（本地无） | 不生成（保持策略可移植面最小） | — |

R1 门控模板（唯一入口，禁止策略手写判断）：

```python
# coding:gbk
def handlebar(C):
    if not _qs_should_run_daily(C):   # barpos+period+末bar 判定，模板注入
        return
    ...  # 原 handle_data 主体（经规则改写后的调用面）
```

## 4. API 映射总表（行为层 wrapper）

| 本地 API | QMT 实现（wrapper 内） | 依据 |
|---|---|---|
| `get_history/count/include=False`（E1） | `C.get_market_data_ex(period='1d', count=N, dividend_type=…)` + **D-1 锚定过滤**（实盘盘中 count=1 含当日未完成 bar → wrapper 显式剔除今日 bar） | 〔QS-qmt:07-行情函数.md:185-195〕〔C7: fields/stock_code/period/start_time/end_time/count/dividend_type/fill_data/subscribe〕 |
| `get_fundamentals` / `_batch` | `get_financial_data`（字段映射 + 数值归一，M0 逐字段登记） | 〔QS-qmt:07-行情函数.md:1998〕 |
| `get_Ashares` | `C.get_stock_list_in_sector('沪深A股')` | 〔QS-qmt:01-快速开始.md:328; 07:3326〕 |
| ETF 池（PIT） | 复用 PTrade 侧固化机制（ETF_POOL_STATIC 同规则） | PRD§7-2 |
| `get_stock_info` | `get_instrument_detail` | 〔QS-qmt:07-行情函数.md:2459〕 |
| `get_positions`/`get_position` | `get_trade_detail_data(accid,'stock','position')` → 持仓视图归一（同 PTrade _QSPositionView 契约） | 〔QS-qmt:12-完整示例.md:1374〕 |
| `order`/`order_value`/`order_target_value`/`order_target_percent` | wrapper 计算目标差额 → `passorder(opType, orderType, accountid, code, prType, price, volume, strategyName, quickTrade, userOrderId, C)`；**不用回测专用同形函数**（PRD§7-1，消除回测/实盘分叉） | 〔QS-qmt:08-交易函数.md:8-46〕〔C7 同签名核验〕 |
| `current_price` | `get_market_data_ex(count=1)`['close'] / 盘中 `get_full_tick` | 〔QS-qmt:07-行情函数.md:542〕 |
| `get_trade_days` | **GAP-1**：innerApi 无交易日历 API（get_trading_day/get_holiday 均未收录）→ wrapper 以基准指数日 bar 序列推导，单测锁定 | 〔QS-qmt 全集检索 0 命中〕 |
| ST/停牌过滤 | **GAP-2**：无 get_stock_status → 板块法（get_sector_list/get_stock_list_in_sector）+ `Bar.suspendFlag` 双路径 | 〔QS-qmt:06:498; 04-数据结构.md:79; 07:212〕 |
| `get_divid_factors`（如需） | 平台原生存在 | 〔QS-qmt:07-行情函数.md:3225〕 |
| DENY_REMOVE 同款（set_backtest/is_trade/get_etf_list_local/…） | 沿用三档删除/字面量/BLOCK | portability_rules.py 先例 |
| MyTT/A股规则函数 | 同 PTrade：用到才注入 + 前缀 + 非 1:1 标记 | source_import.py 先例 |

## 5. 关键设计决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | 产物 **GBK** 编码（`# coding:gbk` + 字节级校验 A3） | QMT 文档 106 处示例均 gbk；UTF-8 产物在 QMT 编辑器乱码 |
| D2 | 结构层（生命周期）转译 + 行为层（API）wrapper，两层分离 | 结构无法 wrapper 化；行为集中在 wrapper = 单一契约点，可本地模拟验证 |
| D3 | 下单统一 passorder wrapper，不用回测专用函数 | 回测/实盘同一路径；专用函数实盘不可用（模式分叉） |
| D4 | E1 语义在 `_qs_get_history` 固化（含实盘盘中测试） | 铁律「日线信号取数」平台侧延续；与 PTrade 侧 attach_bar 机制同构 |
| D5 | 规则单一来源 qmt_portability_rules.py + 登记册 md 双写 | PTrade 先例；转换器/校验器共用防漂移 |
| D6 | qmt_sim：ContextInfo mock（capital/barpos/period/accid…）+ handlebar 逐日驱动 + DuckDB 数据 | 本地即可跑转换产物 → 黄金对照，无需 QMT 终端 |
| D7 | 契约矩阵：qmt wrapper 模板哈希纳入同款 CI 门，改动同 commit 追认 | 哈希追认同批纪律铁律 |
| D8 | 保真开关默认关（QMT_FIDELITY_* 配置族） | PTrade 先例；默认行为=最保守对齐 |
| D0 | 单源三目标：本地策略一份，PTrade/QMT 两条管线分别渲染（难点1 裁定） | G4 dual-render 先例；零改动分工；防逻辑分叉怪圈 |
| D9 | 知识库闭环并入 knowledge/ 五设计（registry/探针/仲裁/类例放大/契约测试/平台回灌），QMT 为第二平台维度（难点2 裁定） | 用户 PTrade 对齐经验已固化 alignment-loop-guide.md（2026-10-06 建库裁定）；复用机制，不另起炉灶 |
| D10 | QMT 撮合口径分层对齐：可配置口径（滑点/费率/印花税/佣金/最大成交比例——QS-qmt:15-界面操作.md:94-102）钉「回测配置清单」；引擎固有口径真机探针标定后由引擎 qmt 档复刻（match_profile='qmt'，D11；MCH-QMT 条目）；净值对齐=L3 容差对照不承诺逐位（2026-10-07 用户撮合质疑落档） | 本地撮合=PTrade 口径是探针标定成果（不改）；撮合是运行时属性——策略一份或两份都不改变平台撮合行为，对齐投入必须落在标定+核算层复刻，与代码份数正交 |
| D11 | 撮合口径运行时开关（2026-10-07 用户方案采纳）：本地引擎新增 match_profile 运行级选项（'ptrade' 默认 / 'qmt'）；一份策略 × 两档口径，本地即可复现两平台预期回测；qmt_sim 不自建核算层，**复用引擎核算核心**（profile 传递）；开关为运行级配置，**双入口强制**（2026-10-07 用户裁定）：终端命令 CLI 旗标 `--match-profile`（M4）+ PyQt 回测界面「撮合口径」选择器（M6），两入口同名同默认（ptrade）、缺一不验收；不进策略源码；默认 ptrade 档与现状逐位一致（纯增益硬门） | 比独立核算层更优：单一核算真相源，杜绝两套核算漂移；转换保真定义升级为「同口径下逐位一致」（A2 两档各验）；qmt 档取值由标定闭环修正；QMT 验证环与 PTrade 既有保真对照环（ptrade_baseline 导入+容差）结构对称，可复用整套机制 |

### 5.1 知识库闭环架构（难点2 裁定：复用 knowledge/ 五设计，QMT 维度并入）

```
knowledge/registry.md（QMT 维度）
   ↑ ⑥沉淀：升锚 + 契约测试 + playbook        ↓ ①驱动：能力门禁查登记册（A10）
skill 生成本地策略 ──②双管线转换──③黄金对照 + 探针矩阵双平台 diff
                                        ├─ diff=0 → 放行门禁（新策略零修复通过）
                                        └─ 差异 → ④三态仲裁 → ⑤框架修复·类例放大（六步流水线）
                                              真机 QMT 回测日志回灌（M5+，平台实证黄金）
```

六个扩充点（对齐 alignment-loop-guide.md 五设计，逐条落位）：
1. **registry.md QMT 维度**：共享面（POS/DAT/IDX——本地语义一份、平台锚点两个）加平台锚点列；QMT 专属面（LC handlebar 时机、MCH 撮合、日历合成）立独立条目；收敛度量沿用「未验证数单调下降」。
2. **contracts/ QMT 契约卡**：每条已锚定映射存档（文档依据 + 黄金值）；QMT 初期黄金级 = 平台文档契约（docs/qmt/inner-api），真机回灌后升「平台实证黄金」（黄金三级制沿用：平台实证 > 平台文档 > 本地自产）。
3. **probes/ 探针矩阵 QMT 腿**：探针策略本地引擎 → 双管线转换 → PTrade 路径回归不变 + qmt_sim 黄金对照；**新策略放行门禁前移：双平台探针 diff=0 方可出管线**。
4. **playbooks/diff-triage.md 三态仲裁复用**：可归因→修｜不可归因→深挖｜平台自身问题→豁免登记；QMT 差异同 SOP，禁止修完就忘。
5. **平台日志回灌**：真机 QMT 回测日志归档 + 例行 diff（M5+ 呈批通道，对齐 PTrade 用户人工跑平台回测→日志归档 SOP）。
6. **ID 脊柱互链**：qmt_portability_rules.py 规则 ID ↔ registry QMT 条目 ↔ contracts 契约卡 ↔ 契约测试，四点一线；六步①方案必须引用条目编号（guide 既有条款），⑤汇报附 registry 前后状态 diff。

**终局收敛判据**（用户「最终收敛」的操作化）：**新策略零修复通过率**——新策略从生成到双平台转换 + 黄金对照通过，框架层零新增修复；连续 N 个新策略零修复 ⇒ 对齐基线宣布收敛 v1.0，闭环转入守护态（只处理平台版本漂移/新 API 采纳产生的新面）。N 与统计口径 M5 呈裁。

**收敛三防线**（2026-10-07 用户「拆东墙补西墙」之问落档——怪圈在机器层面被拦截的三道闸）：
1. **洞不增**：A10 生成期能力门——策略 API 消耗清单对照双平台登记册，未登记面 WARN/BLOCK，新策略无法引入未锚定面（单平台放行须用户显式裁定并登记在案）；
2. **洞不换**：修复前置纯增益审计（三型判定）+ 6 策略横验证 + wrapper 模板矩阵哈希 CI——任何修复若使既有已锚定面退化即验收失败回退，「拆东墙补西墙」被回归门禁机器拦截；
3. **洞不复发**：类例放大三步纪律（修一洞·排一类·立一墙）——每个洞修复时同类面一并排查并以契约测试固化，同类洞一生只允许出现一次。
诚实边界：收敛是渐近过程——前 N 个策略仍会踩到「未验证」面并升锚，判据是零修复通过率单调上升而非首日即零；基线收敛后转守护态（平台版本漂移例行回灌 + 新 API 采纳走登记流程），非「永久无洞」；L3 真机对照依赖用户人工跑平台回测回灌（PTrade 侧同款人机分工）。

强制性来源：落档是六步④验收的组成部分（不做不算验收完成）+ knowledge/ 随代码同 commit 双仓推送——闭环由 git 与验收门强制，Obsidian 仅为阅读层（README.md 既有定性）。

### 5.2 撮合口径三层对齐（难点2 追问：净值层对齐的真正解法，D10）

用户正确指出的硬约束：本地引擎撮合口径=PTrade 探针标定成果（信号前复权 + 撮合不复权原始价等）；QMT 引擎用自己的撮合 → 本地默认净值 ≠ 真机 QMT 净值。**代码份数与该问题正交**（两份策略也不会改变 QMT 引擎撮合），解法是把 PTrade 侧已验证的标定方法论平移到 QMT，并按可配置性拆两半：

| 层 | 内容 | 验证方式 | PTrade 侧等价物 |
|---|---|---|---|
| L1 订单意图层 | 信号日/标的/方向/数量/目标值逐位一致 | A2 硬门（qmt_sim 驱动产物 vs 本地引擎；与撮合无关） | api_portability + 信号黄金 |
| L2 本地 QMT 语义净值 | **本地引擎 match_profile='qmt' 档**（D11）：同一核算核心换 qmt 口径参数；qmt_sim 复用该核心驱动转换产物 | A2 同口径逐位：qmt_sim(产物) ≡ local(策略)；local(strategy, profile=qmt) 即「QMT 预期净值」 | 本地引擎（PTrade 撮合标定版）净值 |
| L3 真机 QMT 净值 | 真机 QMT 回测净值 vs L2 | 容差对照 + 差异归因台账（三态仲裁），阈值 M5 呈裁 | 本地 vs 真机 PTrade（跨源容差先例：tushare/juyuan） |

撮合口径两半拆分（文档依据 QS-qmt:15-界面操作.md:86-102 回测参数面板 + 08-交易函数 prType/回测专用 style）：
1. **可配置半**：滑点、手续费类型（成交额比例/固定值）、买/卖印花税、最低佣金、买入佣金、平昨/平今佣金、最大成交比例 → 转换产物附**「QMT 回测配置清单」**（字段↔本地约定逐项映射表），运行 QMT 回测前按清单设置，口径即对齐——无需探针。
2. **固有半**：成交时点/价格取值、复权与分红处理、T+1 细节等 → **真机探针标定**（复刻 PTrade 撮合探针方法论：探针策略 → 双端跑 → 逐笔 diff → MCH-QMT registry 条目升锚「平台实证黄金」）→ 标定值写回引擎 qmt 档（match_profile 取值；首轮可文档推断值上线，真机 diff 逐项修正）→ L2/L3 收敛。

诚实结论（D11 用户方案采纳后）：QMT 管线相对 PTrade 管线的两大真实增量成本 = ①转译厚度（结构+全 API wrapper）②撮合标定投入（M4 启动、M5 首版，落点 = 引擎 qmt 档取值）；架构由 match_profile 开关承载——本地 local(strategy, profile) 直接产出对应平台的预期回测，验证环与 PTrade 侧结构对称。若标定投入不足，交付降级为 L1+L2（意图保真 + 本地 qmt 档净值），L3 差异如实呈报——呈裁项（PRD 边界9/R9）。**默认 ptrade 档与现状逐位一致为纯增益硬门**（既有黄金全绿）。

## 6. 模块落位（文件清单，实施期产出）

```
quantstudio/strategy_compiler/
  qmt_portability_rules.py     # 规则单一来源 + GAP 登记册（M0）
  qmt_source_import.py         # 结构层转译 + 注入编排（M1）
  qmt_wrappers.py              # wrapper 模板串（哈希入矩阵）（M2/M3）
  validate_qmt_portability.py  # 校验器（fail-closed）（M0 起）
quantstudio/backtest/backtest_engine.py    # match_profile 参数化（D11：默认 ptrade 档逐位不变；共享核心文件纪律适用）
quantstudio/backtest/qmt_sim/
  context_sim.py               # ContextInfo 模拟器（D6）
  driver.py                    # handlebar 逐日驱动（核算复用引擎核心，D11）
scripts/check_qmt_matrix.py    # 或并入 check_fund_matrix（M5）
tests/test_qmt_*.py            # 单测 + 横验证（M2-M5）
main_gui.py                    # 「转 QMT」tab + 回测「撮合口径」选择器（M6，A12 双入口之二）
docs/strategy-compiler/qmt-portability-contract.md   # 契约登记册（M0）
knowledge/registry.md + contracts/qmt-*.md           # KB QMT 维度（M0 起，六步④同步落档）
skills/quantstudio-strategy-compiler                 # R1/R3 API 消耗清单 + 双平台能力门（A10）
```

## 7. 验收与回退

- 验收：PRD A1–A11（A10 生成期能力门 / A11 知识库闭环为 2026-10-07 两难点裁定新增）；黄金对照脚本产出 docs/evidence/qmt-conversion-*.md（证据文档）+ knowledge/ 同步落档。
- 回退条件：任一 A 项失败且不可当轮归因修复 → 整体回退（纯新增模块，git revert 即净；不触 PTrade 面）；A8（既有零衰减）失败立即停。

## 8. 会话/预设模式（用户「新开专属 Agent 预设」诉求落位）

- **推荐**：实施会话直接以现有 `project-dev-optimizer` 预设开启（双模式=存量优化 + Plan-Mode 计划审批 + 验收五步串行 + ZCode 编码委派编排，与本 PRD 六步/五步法完全匹配）；其首轮输入 = 本 PRD + 本设计 + docs/qmt/inner-api/ 文档集路径。
- 可选（M6 后再评估）：建专用 preset `qmt-conversion-pipeline`（~/.dsh/.agent-presets/：preset.yml + agents/*.md，persona 注入 QMT 契约文档路径与铁律摘要），避免过早固化。

## 9. 与铁律的协同清单

六步流水线（本文档=①方案）／策略全链路修复仅框架层／纯增益三型判定（本管线=新增能力，纯新增模块）／平台代码先查文档（本文档全量标注依据）／矩阵哈希同批纪律／主仓推送后 trading 同步门／Rust 三端保温态（映射登记册备查）。
