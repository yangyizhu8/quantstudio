# M5 批2 探针证据摘录与逐项判定（2026-10-10）

> 来源：用户域 QMT 客户端实跑 `m5_probe_qmt_ascii.py`（回测模式，testS 占位；日志经用户粘贴回传）。
> 本件 = 证据包**完整性核对 + 逐项判定**（M5 计划 §5 判据①②）；批3 将补 P-4 归因脚本与 P-5 契约回填。
> **日志规模**：首根 bar 证据 13 行 + `M5-4/M5-10` 逐 bar 行 n=1..505（截断于 505，节拍项已足够）。

## 一、首根 bar 证据（原样摘录，未改写）

```
QS_M5_PROBE: init_enter {"account": null, "barpos": 0}
QS_M5_PROBE: M5-3_trading_dates_in_init {"ok": true, "type": "list", "len": 0, "sample": "[]"}
QS_M5_PROBE: M5-10_marker_seeded {"marker": "set_in_init"}
QS_M5_PROBE: M5-4_handlebar_tick {"n": 1, "barpos": 0}
QS_M5_PROBE: M5-13_bar_timetag {"barpos": 0, "raw": "1725292800000", "type": "int", "ms_scale_guess": "milliseconds"}
QS_M5_PROBE: M5-2_market_data_tail {"type": "dict", "index_tail": ["20260930", "20261008", "20261009"], "closes": [11.57, 11.78, 11.59], "bar_timetag": "1725292800000"}
QS_M5_PROBE: M5-1_financial_shape {"type": "NoneType", "outer_keys": null, "inner_keys": null, "repr_head": "None"}
QS_M5_PROBE: M5-8_instrument_detail {"ok": false, "err": "TypeError('get_instrument_detail() takes 2 positional arguments but 3 were given',)"}
QS_M5_PROBE: M5-9_sector_list {"sector": "A", "type": "list", "len": 0, "sample": []}
QS_M5_PROBE: M5-3_trading_dates_in_handlebar {"ok": true, "type": "list", "len": 5, "sample": "['20260928', '20260929', '20260930', '20261008', '20261009']"}
QS_M5_PROBE: M5-6_account {"ok": false, "err": "AttributeError(\"'__PyContext' object has no attribute 'get_trade_detail_data'\",)"}
QS_M5_PROBE: M5-11_positions {"ok": false, "err": "AttributeError(\"'__PyContext' object has no attribute 'get_trade_detail_data'\",)"}
QS_M5_PROBE: probe_done {"note": " bar  bar "}
```

**节拍项**：`M5-4_handlebar_tick` n=1..505 连续、`barpos` 逐根 +1（0..504）；
`M5-10_marker_survived` 每根 bar 均报 `marker="set_in_init"`（存活）。

## 二、逐项判定（M5 计划 §2 的 11 项）

| # | 项 | 判定 | 证据 |
|---|---|---|---|
| **M5-1** | `get_financial_data` 返回形态 | **未钉死（待补）** | 返回 `None` → 财务数据未补充或该标的无数据；**M3.1 段二继续挂起** |
| **M5-2** | `get_market_data_ex` 末根 bar 语义 | **钉死：count 形态返回「全局最新 N 根」** | barpos=0（bar_timetag=2024-09-03）时 `count=3` 返回 **20260930/20261008/20261009** —— 非截至当前 bar |
| **M5-3** | 交易日历形态 + init 内可用性 | **钉死** | init 内**不抛异常、返回空 list**（len=0）；handlebar 内返回 `list[str]` 形态 `'YYYYMMDD'`（len=5） |
| **M5-4** | run_daily 节拍 | **钉死：handlebar 每根日 bar 一次** | n=1..505 连续递增、barpos 逐根 +1 |
| **M5-5** | PERSHAREINDEX 字段枚举 | **未取证**（随 M5-1 的 None 一同阻断） | 同上 |
| **M5-6** | accountID / ACCOUNT 资金字段 | **钉死：`C.get_trade_detail_data` 不存在** | `AttributeError: '__PyContext' object has no attribute 'get_trade_detail_data'`；且 init 时 `accountID=null` |
| **M5-8** | `get_instrument_detail` 枚举 | **钉死：签名与文档不符** | `TypeError: get_instrument_detail() takes 2 positional arguments but 3 were given` |
| **M5-9** | 板块名「沪深A股」等价性 | **未取证（工具侧缺陷）** | 日志 `"sector": "A"` —— **ASCII 版把中文 sector 名替换掉了**（见 §三 D-8） |
| **M5-10** | ContextInfo 跨 bar 重置 | **钉死：本环境自定义属性跨 bar 存活** | 每根 bar 均报 `marker="set_in_init"`（与 `06:8` 表述不符） |
| **M5-11** | 持仓面 | **随 M5-6 阻断**（同一 API 缺失） | 同上 |
| **M5-13** | bar 时间语义 | **钉死：毫秒 int** | `1725292800000`（= 北京时间 2024-09-03 00:00:00（= UTC 2024-09-02 16:00；原注记误标 UTC，2026-10-10 M5.1a 审计 R5 更正）），`type=int` |

## 三、发现清单（按严重度）

| # | 发现 | 级别 | 影响 |
|---|---|---|---|
| **D-1** | **`get_market_data_ex(count=N)` 返回全局最新 N 根**（非截至 `barpos`） | **P0 严重** | 产物 `_qs_get_history` 用 `count+1` 取 `[-2]` → 回测中取到**回测结束日附近数据** = **未来数据泄漏**。必须改用 `end_time`（`C.get_bar_timetag(barpos)`）形态 |
| **D-2** | **`C.get_trade_detail_data` 不存在**（应为**全局函数**，见 `08:583` 签名） | **P0 严重** | 产物 `_qs_get_positions` / `get_position` / `_qs_order_target_value` 运行期必 `AttributeError` |
| **D-3** | `C.get_instrument_detail(code, iscomplete)` **位置传参报错**（takes 2 positional but 3 given） | **P0 严重** | 产物 `_qs_get_stock_info` / `_qs_data_view`（涨跌停/preclose）运行期必 `TypeError` |
| **D-4** | init 内 `get_trading_dates` **返回空 list 而非抛异常** | P1 | 产物 `_qs_get_trade_days` 的「异常捕获 + 安全回退」策略失配——空结果**静默通过**，需显式判空 |
| **D-5** | `accountID` 在 init 时 **null** | P1 | 产物账号占位/`get_trade_detail_data` 调用面需容错 |
| **D-6** | ContextInfo 自定义属性**跨 bar 存活**（`06:8` 称随 bar 重置） | P1（**利好**） | 产物 `g=C` 运行态假设**成立**，无需跨 bar 重绑定 |
| **D-7** | `get_financial_data` 返回 **None** | 待补 | M5-1/M5-5 未钉死；需完成财务数据补充后重测 |
| **D-8** | **ASCII 版把中文 `SECTOR='沪深A股'` 替换为 `'A'`** → M5-9 未取证 | 工具侧 | 我方核对疏漏（只核对了**标签集合**，未核对**中文常量**）；需 GBK 版重跑该步 |

## 四、结论与下一步

**完整性核对**：11 项中 **7 项已钉死**（M5-2/3/4/6/8/10/13）、**3 项待补**（M5-1/5/9）、**1 项随 M5-6 阻断**（M5-11）。

**下一步（用户域，两项补测）**：
1. **补财务数据**后重跑探针 → 钉死 M5-1/M5-5；
2. **用 GBK 版**（`m5_probe_qmt_gbk.py`）重跑 → 钉死 M5-9（`'沪深A股'` 需 GBK 才能表达）。

**产物侧 P0 缺陷（D-1/D-2/D-3）**：三项均为**产物 wrapper 与真实 QMT API 不符**，直接导致六策略在 QMT 端**运行期崩溃或取数错误**。
→ **建议**：并入 M5 后续修复批次（走六步流水线）；**M5-6/M5-8 的六策略回测大概率会报同类错**，其报错原文将构成交叉证据。

---

## 五、追加轮次（同日 16:50 / 17:18）：M5-1 完整钉死 + D-9 完整形态

### 5.1 单码形态（第三轮，16:50）

```
QS_M5_PROBE: M5-1_financial_shape {"type": "DataFrame",
  "outer_keys": ["20200102","20200103","20200106","20200107","20200108"],
  "inner_keys": ["s_fa_eps_basic","inc_revenue_rate","m_anntime","m_timetag","circulating_capital"],
  "repr_head": "          s_fa_eps_basic  inc_revenue_rate     m_anntime     m_timetag
20200102            1.32           18.8013  1.571674e+12  1.569773e+12"}
```

- **单码** → `DataFrame(index=日期字符串, columns=请求字段名)`；**index 是日期、不是代码**；字段名**不带表名前缀**（前缀被剥掉）；
- **同时钉死 M5-5**：`m_anntime`/`m_timetag` **存在**且为**毫秒**（`1.571674e+12`）→ 原「字段表未显式列」疑点消除。

### 5.2 多码形态（第四轮，17:18）—— **六策略实际路径**

```
QS_M5_PROBE: M5-1b_financial_shape_multi {"type": "Panel",
  "repr_head": "<class 'pandas.core.panel.Panel'>
Dimensions: 2 (items) x 1638 (major_axis) x 5 (minor_axis)
Items axis: 000001.SZ to 000002.SZ
Major_axis axis: 20200102 to 20261009
Minor_axis axis: s_fa_eps_basic to circulating_capital"}
```

- **多码** → **`pandas.Panel`（三维）**：`items = 证券代码` × `major_axis = 日期` × `minor_axis = 字段`；
- **注意**：`Panel` 为 pandas 旧版类型（新版已移除）→ **QMT 内置 pandas 为旧版**，产物若依赖新版 pandas API 会失败。

### 5.3 D-9 完整形态与对产物的后果（**P0**）

| 调用面 | 真实返回 |
|---|---|
| 单码 | `DataFrame(index=日期, columns=字段)` |
| **多码（六策略实际）** | **`Panel(items=代码, major_axis=日期, minor_axis=字段)`** |

**产物后果**：
- `_qs_fin_to_rows` 的 `hasattr(raw,'to_dict')` 分支对 `Panel` 调 `to_dict('index')` → 形态不匹配（Panel 的 `to_dict` 语义与 DataFrame 不同）→ **取不到 `{code: {field: v}}`** → `rows.get(code)` 全 miss → **全 NaN**；
- **即：M3.1 的双轨（显式声明 code_major/field_major）对真实形态完全不适配**——真实形态是**三维 Panel**，既非 code_major 也非 field_major。

### 5.4 M3.1 段二结论（**需重设计**）

原口径「M5-1 钉死后以轨 A 显式声明接入」**不成立**。正确修复方向应为：
**按 Panel/DataFrame 真实形态解析** —— `Panel[code] → DataFrame(index=日期, columns=字段)` → 按 `date` 选行（或取 as-of 末行）→ 按字段映射投影 → 组装 `{code: {local_field: value}}`。
→ 该修复属**产物 wrapper 重写**，应与 D-1/D-2/D-3 一并立 **M5.1 产物修复批**（走六步流水线）。

### 5.5 M5 探针完成度（截至第四轮）

| 状态 | 项 |
|---|---|
| **已钉死（11 项）** | M5-1（单码 + **多码**）· M5-2 · M5-3 · M5-4 · M5-5 · M5-6 · M5-8 · M5-9 · M5-10 · M5-13 |
| 阻断 | M5-11（随 D-2） |
| 待六策略回测 | M5-7（orderType/prType 实跑）· M5-11（持仓面，需 D-2 修复后） |
| 待操作确认 | M5-12（停牌/退市正例）· M5-14（财务数据补充，已确认可返回数据 → 视为已补） |

**P0 产物缺陷累计 4 项**：D-1（未来数据泄漏）· D-2（`C.get_trade_detail_data` 不存在）· D-3（`get_instrument_detail` 签名）· **D-9（fundamentals 三维 Panel 形态不适配）**。



## §6 M5.1a 修复批：D-10 实测落锤 + 修复实施（2026-10-10 晚）

### D-10 新增（用户域回测首跑实测，原样）

\File "<string>", line 1133, in handlebar
File "<string>", line 1141, in daily_logic
AttributeError: 'NoneType' object has no attribute 'strftime'
\
- 根因：\_QmtContextView.current_dt\（源 :346）\getattr(C,'bar_date',None)\——**实测 ContextInfo 无 bar_date 属性**（M5 实测项落锤）；\pre_bar_date\ 同根因。
- 伴生证据（同跑）：wrapper 审计行正常输出——\QS_QMT_DENY_REMOVED: set_benchmark\、\QS_QMT_SEMANTIC_DIFF: run_daily time collapsed\、\initialize done: target=5, stop=7.5%/20.0%\（GBK 文件级覆写部署通道成立）。

### M5.1a 修复（六步流水线：方案 v1.1 → ZCode 审计 sess_a1c58d74 → 实施 → 验收）

- 改动（仅框架层 \source_import_qmt.py\，33+/9-，策略源零改动，经重转生效）：
  1. D-10：current_dt / previous_date 改由 \get_bar_timetag(barpos / barpos-1)\ 推导 datetime（M5-13 毫秒 int）；except 补 \QS_QMT_CONTEXT_DT_UNAVAILABLE\ 审计行；首 bar previous_date=None（QMT 侧边界语义差，登记）。
  2. D-3：3 处 \C.get_instrument_detail(code, False)\ → 单参（M5-8 实测 TypeError 落锤）。
- 验收证据（2026-10-10）：
  - pytest \	ests/test_source_import_qmt.py tests/test_qmt_stub_smoke.py\：**27 passed**（18 既有 + 3 新增 + 6 冒烟）；新增含时区前置校验（UTC+8 fail-fast）与产物单实参正则回归闸（堵 stub 保真度分叉下 D-3 回归）。
  - 矩阵门禁：\check_fund_matrix.py --check\ → OK（哈希一致 + MD 一致，零漂移）。
  - 六策略重转：全部 GBK=OK / 首行 #coding:gbk / AST=parsed；**六产物 diff 形状逐字节同构**（changed=42 行/产物），全部归因于方案 §2.1/§2.2 列明内容模式行集（机械归因唯一残留 \if bp is None or bp <= 0:\ 与方案代码逐字一致——关键词表缺口，人工终审 PASS）。
  - 部署：新建策略文件7.py ← 新 CANSLIM 产物（64142B，与 staging 逐位一致）。
- 待用户域复测（验收 #6）：QMT 重开「新建策略文件7」回测——预期越过 :1141（D-10 消除）、data view 快照无 3 参 TypeError（D-3 消除）；后续报错（若有）按 D-2（get_trade_detail_data 全局形态）/ D-9（fundamentals Panel）收集实测。
- 不修项（分流 M5.1b）：D-1（count 形态未来数据）/ D-9（fundamentals Panel/DataFrame 解析）——数据正确性级，另走六步流水线。

## §7 M5.1b 实施与验收证据（D-1 窗口化 + D-9 Panel 轨，2026-10-10）

- 实施落点：`source_import_qmt.py`（`_qs_fetch_bars` 窗口化 + `_qs_snap_kwargs` 快照锚定 helper + 四处快照调用替换 + `_qs_fin_to_rows` D-9 轨）；`portability_rules.py`（`get_bar_timetag` 登记 `_QMT_CONTEXT_METHODS`，M5-13 依据）。
- D-11 探针证据（23:02 用户域）：front/none 均 nrows=221、cols=['close']、first=20251110 last=20261009、m_cond=False —— D-11 撤销（形态与数值真实），selected=0 定谳为 D-1 口径产物（M 闸门消费全局最新序列）。
- 审计三要点落证：① `test_fetch_bars_honest_when_short`（不足 count 如实返回）；② D-9 轨为第三形态扩展，轨 A/B 与 UNDETERMINED 零改动（`test_fin_to_rows_panel_branch`）；③ D-2b 暂缓登记 ISSUES、E1 端点留复测定谳。
- 验收：pytest 27 passed（21 既有 + 6 新增）；stub smoke 6 passed（stub :623 原生支持窗口参数）；`check_fund_matrix.py --check` 哈希一致零漂移；六产物重转 GBK/AST 全 OK（67811/54321/62122/69901/64027/68409 B）。
- 部署：「新建策略文件7」← CANSLIM 新产物 67811B，byte-identical=True，sha256 前16位 d36ad1436be2c71d，首行 #coding:gbk；旧版备份 `output/backups/m5.1b-20261010/deployed-file7-m51a.py.bak`。
- 回退基线：`output/backups/m5.1b-20261010/`（source_import_qmt.py.bak / test_source_import_qmt.py.bak / qmt_stub.py.bak——注：qmt_stub 本批未改）。

### §7.1 阻断性笔误自查修正（2026-10-10，⑤后等待复测期间）

- 用户授权直达闭环后、等待复测日志期间执行格式自查：文档行级索引 07-行情函数.md:119-120 明确 start_time/end_time 格式为 %Y%m%d（示例 :187 "20230901"）——实施初版误写 '%Y-%m-%d'（带横线），复测必触发 FETCH_FAIL 白跑一轮。
- 修正：source_import_qmt.py 5 处 strftime 格式串 + 测试断言同步；属本批目标文件内阻断性笔误分级豁免（不改对外行为设计），特此申报附注。
- 回归：pytest 33 passed（test_source_import_qmt 27 + stub_smoke 6）；六产物重转 + 文件7 重新部署（byte-identical，首行 #coding:gbk，产物内无 '%Y-%m-%d' 残留）。

## §8 M5.1b 复测判定 + D-12 发现与修复（2026-10-11 00:54 用户域日志）

### 复测判定（M5.1b 修复项）
- 183 交易日全跑通零崩溃；无 FETCH_FAIL / FIN_SHAPE_UNDETERMINED 刷屏——窗口参数（%Y%m%d）被真实 QMT 接受。
- **M 闸门 m=1 全程**（FUN 行恢复打印且 m=1；修复前 m 恒 False、FUN 行不打印）——**D-1 窗口化实证生效** ✓。
- E1 端点未直接观察（FUN 行不含窗口日期字段；防御性剔除逻辑在位，无泄漏迹象）。

### D-12 新缺陷（P0，同轮发现同轮修，直达闭环授权内）
- 症状：FUN 行 A=0 F=0 H=0 q=0 b=0、selected 恒 0——A=len(all_codes)=get_Ashares() 池为空。
- 根因（文档定谳）：07-行情函数.md:3309 `realtime` 参数为**毫秒级时间戳**非布尔；wrapper 原传 `1` = 锚定 1970-01-01 → 空池。
- 修复：source_import_qmt.py get_Ashares 改传 `C.get_bar_timetag(C.barpos)`（as-of 锚定，与 D-1 同族）；timetag 不可得回退单参调用。
- 验收：pytest 33 passed；六产物重转（D-12 旧形态断言无残留）；文件7 重部署 68291B identical sha 5123210dc1e4f7a3；矩阵 --check 零漂移。
- 判型：数据修正型（影响面=全部消费 sector 池的策略产物信号链）。
- 待用户域复测确认 A>0（预期数千只量级，M5-9 先例 5227）。


### §8.1 D-12 PROBE 定谳 + final 修复（2026-10-11 01:45 用户域探针）

- 探针（文件8）回测模式实测：单参 len=5227（唯一可用）；realtime 实参任何值（timetag/布尔1）均 len=0；C.stock_list 属性 AttributeError。
- 修复：get_Ashares 直接单参调用，删除 realtime 传参分支（前两轮 timetag 修复因 timetag 可得而恒走双参分支故未生效）。
- 验收：六产物重转（断言单参形态、无双参残留）+ 文件7 重部署 + pytest/矩阵门禁（见当轮输出）。
- 文档口径修正：07:3309 realtime"毫秒级时间戳"参数在回测模式实际不可用——单参=当前板块全名单（与 SEMANTIC_DIFF 声明一致，无 PIT）。

## §8.2 D-13 修复（ExpireDate 误判退市 → F=0，2026-10-11 02:31 定谳 / 02:5x 实施）

- **症状**：01:48 复测 FUN 漏斗 A=5227 F=0——`filter_stock_by_status` 5227 只全剔，且零 QS_QMT_UNDETERMINED 刷屏（失败路径必打印 → 指向判定恒真而非取数失败）。
- **PROBE 定谳（新建策略文件8，02:31 日志）**：4/4 详查 `ExpireDate=99999999 type=int`（YYYYMMDD 永续标记；OpenDate=19910403 同形态佐证）；census 100 只 `IsTrading=None×100`（None is False 不触发）、`InstrumentStatus 0×99+20×1`（haltByStatus=1/100）、ST=5/100——ST/HALT 低命中率与全剔互斥，唯一解释 = DELISTING 恒真：旧代码 `fromtimestamp(99999999/1000)=1970-01-02` → `now()>1970` 恒真。
- **修复（框架层 wrapper，`_QS_QMT_STOCK_INFO_EXT` 模板 `_qs_status_flags`）**：数值形态分流——`>=1e10` 按毫秒时间戳防御解析（实测未现）；否则 int YYYYMMDD 走 `strptime('%Y%m%d')`，非法月日（99999999 的 99 月）ValueError → 不过期（保守保留）。ST/HALT/详情取数路径零改动。
- **判型**：数据修正型——影响面=DELISTING 判定行集；diff 只能源于修正行集（ST/HALT 回归锚测试锁定）。
- **门禁**：`pytest tests/test_source_import_qmt.py tests/test_qmt_stub_smoke.py` → **38 passed**（33 既有含 D-12 后全绿 + 5 新增：perpetual 不过期核心断言 / 过期 YYYYMMDD 判退 / 毫秒防御分支 / ST+HALT 路径零改动回归锚 / filter 端到端保留）；`check_fund_matrix.py --check` → OK 零漂移。中途白名单拦截一次（`_qs_dte(...)` 直接名调用 → 改 strptime 属性调用形态后全绿，校验器判据正确未放宽）。
- **六产物重转**（GBK+AST+D-13 渲染闸 1e10/99999999 在位）：CANSLIM 69067B sha=17af261996c02af6 / fall_reversal 55577B 2820e5daa02721b5 / tech_etf_mvo 63378B 5d1c8cf002f0fa84 / vol_regime 71157B 2536c15860842311 / weekly_smallcap 65283B b4e649061b0d06f4 / 周频三层止损 69665B 8011058b30665bce。
- **部署**：文件7 重部署 69067B byte-identical=True sha=17af261996c02af6（旧版备份 output/backups/m5.1b-20261010/deployed-file7-d12.py.bak）。
- **回退**：文件级 .bak-d13-pre 两件（source_import_qmt / test）+ git stash `baseline-before-d13-expiredate-fix-20261011`（1498d3d9）。
- **待复测**：文件7 用户域回测——预期 FUN 行 F≈5000+（5227 减 ST/停牌/退市少数）→ H/q/b 逐级激活。

## §8.3 D-13 复测通过 + q=0 新阻断（2026-10-11 02:38-02:47 回测）

- **D-13 复测通过**：FUN 漏斗 A=5227 **F=4414**（剔除 813 只 ST/停牌/退市，15.5% 合理）H=4414（全加载）；每 bar ~10s（首 bar 2m41s 懒下载）——D-14 吞吐降级为非阻断（缓存生效）。
- **新阻断：q=0 b=0**——流动性/形态全灭。定位链：:1350 门槛（close/high/volume 三字段 len>=260）+ :1353 流动性（close[-20:]*volume[-20:] >= 3000万）。H=cand_hist 键数（:1362）不含字段级过滤。三假设：A 返回列名错位（volume 列缺失→_extract_history_field 返空数组:1183）B volume 值全 NaN/0 C 窗口行数不足 260。
- **D-15 探针已部署文件8**：实测窗口返回 columns/rows/volume 值/amt20 复现。

## §8.4 D-16a volume 单位修复实施 + QMT 客户端加密改写发现（2026-10-11 03:0x）

- **D-15 探针**（02:51）：列名 ['close','high','volume'] 全对、rows=365 满行、值无 NaN/0——A/B/C 三假设全排除；暴露平安银行 amt20=1123万 vs 真实日成交 ~15亿（差 100 倍）。
- **文档定谳**：docs/qmt/inner-api/04-数据结构.md:24「volume | int | 成交总量（手）」；pvolume=原始股数（不推荐）。本地/PTrade 语义=股。
- **D-16 探针**（02:56）：batch500 nonempty=500/500（D-16b 竞态证伪关闭）；passRaw=23 vs passFixX100=491（of 500）——单位修正效果量化 +21 倍过线率。
- **修复实施**（数据修正型，影响面=volume 消费路径）：
  - source_import_qmt.py 历史层 :581-592（_qs_fetch_bars 后处理：fields 含 volume 且列存在 → df.copy() 后 volume×100，失败打 QS_QMT_WRAPPER_VOLUME_CONV_FAIL 不静默）；
  - 快照层 :435-437（_QmtDataBar.__init__ vals[k] 对 volume ×100.0，与历史层一致口径）。
- **验收**：pytest 36 passed（4 新增：test_fetch_bars_volume_converted_to_shares / close_high_unchanged_regression / volume_not_requested_untouched / snap_bar_volume_converted；修复过程披露：首版测试 _MktStubC 同名覆盖既有 D-1 stub 类 + _mkt_df 缺 open/low 列 → 4 failed，改名复用既有 stub + 补列后全绿——测试缺陷非产品缺陷）。
- **六产品重转**：CANSLIM 70009B cf2feefe0f906550 / fall_reversal 56519B 9218f9ccca5b96a4 / tech_etf_mvo 64320B 12842f2eed2027d3 / vol_regime 72099B 34c5e1dc05597ff3 / weekly_smallcap 66225B 64c35d441a9cf93c / 周频三层止损 70607B 8e04650147c9155c——每产物恰 +942B（双换算块+注释机械同构增量）。
- **QMT 客户端加密改写发现**：file7 部署时防覆盖断言拦截（92094B sha=36422757e0e8133f ≠ D-13 部署 69067B 17af261996c02af6）——file7 现为单行 token 形态（非 Python 源）；file8 同样（4162B token vs 部署 3118B）。时间相关定谳：file7 在 02:38 复测后被改写、file8 在 02:56 探针后被改写——**QMT 客户端在回测/探针运行后自动把策略文件改写为内部加密存储**。D-13 部署版无损（本地产物在位 + deployed-file7-d12.py.bak 备份链完整）。部署落点呈报用户裁定。

## §8.5 D-16a 复测 q=0 恒定 → D-17 field= 路由洞定谳与修复（2026-10-11 03:12-03:4x）

- **03:12 复测**（QMT转换测试.py = D-16a 版 70009B cf2feefe）：F=4414 H=4414 稳定，但 **q=0 恒定**——每 bar ~6s（缓存热），确认在跑新部署。
- **矛盾定位**：D-16 探针 passFixX100=491/500（直调 get_market_data_ex 显式传字段）vs 产物 q=0。读产物 get_history 适配层（:1009-1046）：**纯关键字形态**（_load_history :1445-1449 全关键字调用）不进 isinstance(security,int) 重映射分支 → field=[...] 丢弃 → fields=None → _qs_norm_history_fields(None) → **['close']** → high/volume 未请求 → _extract_history_field KeyError 空数组 → :1350 门槛全灭。H=4414（close 列在）自洽；D-16a ×100 未触发（'volume' not in ['close']——列都没有，换算逻辑本身正确）。
- **修复**：source_import_qmt.py get_history 适配层 :1284+ 无条件归一 `if field is not None: fields = field`（与重映射分支优先级一致，幂等）。
- **验收**：pytest 39 passed（D-17 新增 3：test_get_history_kw_field_alias_routed 核心 / 签名A位置锚 / 签名B count-first 锚——两锚首版误断言裸 df 为 dict 系测试笔误非产品缺陷，已修正）；stub 冒烟 6 passed。
- **六产品重转 + 部署**：每产物 +475B（D-17 归一+注释同构）；CANSLIM 70484B sha=673cb230564a24be 部署 QMT转换测试.py（readback 70484B 首行 #coding:gbk 确认明文态）。
- **回滚链**：D-16a 版产物 sha 链在 §8.4；stash 1498d3d9 + bak-d13-pre ×2 + deployed-file7-d12.py.bak。

## §8.6 D-17 复测通过（漏斗全通）+ 执行层新缺陷定性（2026-10-11 03:24-03:5x）

- **03:24 复测**（QMT转换测试.py = D-17 版 70484B 673cb230）：**FUN q=4135→4216 逐日递增（93.7%~95.5% of H，量级吻合 D-16 探针 98.2% 预估）、b=5→14 非零、selected=5（target=5 达标）、tradable 打开**——D-13（状态过滤）+ D-16a（volume 单位）+ D-17（field= 路由）三层修复链实证闭环，M5 批2 漏斗验收目标达成。
- **新暴露 D-18（P0）**：PORTFOLIO_AUDIT cash_ratio=0.0000 / gross_exposure=0.0000 / buy_submitted=0 恒定——_QmtPortfolioView.cash（产物 :90-100）依赖 C.accountID + get_trade_detail_data(...,'ACCOUNT').m_dCash（docstring 自标【M5 实测项】兑现），回测语境资金源形态待探针定谳（候选：C.capital 回测资金属性 / accountID 不存在 / ACCOUNT 表字段名不同）。
- **新暴露 D-19（P1）**：每 bar `get_fundamentals failed: invalid literal for int() with base 10: 'NaT'`——财报 m_timetag/m_anntime 含 NaT 行（该期无报告），wrapper int() 转换无防御；策略降级继续（EPS 因子缺失，selected 为纯价量选出）。
- 处置：两项均已登记 ISSUES；修复需先探针取证（D-18）/框架层防御（D-19），呈报用户授权后推进。

## §8.7 D-19 二层修复（2026-10-11 16:3x，微批授权延续）

**复测定谳（16:05-16:16 用户回测日志）**：D-18 四判据全过（buy=5 首批 / cash_ratio 0.0165 动态 / positions 0→5→…→0 / 卖出止损多次触发）；D-19 NaT 仍报。

**二层根因**：本地 ptrade_api.py:1022 eps/growth_ability/profit_ability 走 get_financial 返回 **PIT 窗口全报告期多行**（index=code 重复）；wrapper 单行（_qs_fin_to_rows iloc[-1]）+ NaN 契约行双重错位——①CANSLIM:406 groupby 需跨季度序列拆单季算 C/A，单行使 EPS 因子静默退化纯价量；②NaN end_date 行令策略 :391-392 to_datetime→NaT→strftime('NaT')→astype('Int64') 即 int('NaT') 崩。

**修复落点（框架层 source_import_qmt.py）**：
- 新增 `_qs_fin_to_rows_hist`（实测形态 Panel/单 DataFrame 全行收集；dict 形态委托 _qs_fin_to_rows 包装，零新增猜测路径）；
- `_qs_get_fundamentals` 历史表分支：全历史行收集 + 完整行过滤（任一请求字段 float 不可转/NaN → 整行剔除，对齐本地只回完整行）；valuation 快照路径零改动。

**门禁证据**：pytest tests/test_source_import_qmt.py 49 passed（46 基线 −1 契约演进改写 +4 新增：多行 Panel/NaN 行剔除/valuation 回归锚/缺失行剔除）；六策略重转 portability 全 PASS + hist_fn/hist_branch 渲染确认；CANSLIM 部署 83372B sha16=522db759c2b97442 逐位一致。基线 stash e40e3ba1。

**判型**：数据修正型（影响面=历史表 get_fundamentals 消费方：CANSLIM/weekly/周频三层 EPS·growth 因子由缺失转可用；diff 只能源于完整行集）。

## §8.8 D-21 产物编码契约违规修复（2026-10-11 16:5x）

**现象（16:43 用户复测）**：SyntaxError 'gbk' codec can't decode byte 0xa1 in position 151——QMT 客户端按 ANSI(GBK) 解码策略文件；16:4x 部署的产物为 UTF-8 无 BOM（GBK 解码 92 位即炸，探针实证）。

**根因（D-21 定谳）**：临时重转脚本 tmp_reconv_d19l2.py 以 encoding="utf-8" 落盘，违反产物物理编码契约——converter 契约首行恒 `#coding:gbk` 且落盘 GBK（source_import_qmt.py:39/:63/:1538 QS_QMT_PRODUCT_PHYSICAL_ENCODING="gbk"，tests:103/:120 断言）。M5.1a 备份（QMT 跑通版）实证 gbk=OK、utf8=FAIL。生产侧此前无 canonical 写盘点（仅测试引用常量）——临时脚本钻过的洞。

**修复**：新增 scripts/qmt_reconvert_six.py（六策略 canonical 重转门）：portability 门禁 + QS_QMT_PRODUCT_PHYSICAL_ENCODING 契约落盘 + 四自检（首行 #coding:gbk / 无 BOM / GBK 往返逐位一致 / 声明与物理编码一致）。禁止再手搓临时重转脚本。

**证据**：六策略 ALL 6 PASS (enc=gbk)；CANSLIM 76540B sha16=23f79b1a 部署，deployed==product 哈希逐位一致，GBK 全文 69700 字符解码 OK，D-19 二层代码（_qs_fin_to_rows_hist + 历史表分支）在位。

**判型**：纯恢复型（恢复产物编码契约；converted_code 文本内容与 16:4x 版本逐字符一致，仅物理落盘编码恢复 GBK，无行为漂移）。

## §8.9 17:00 复测验收 + QSFUNDHIST 审计行（2026-10-11 17:1x）

**复测结果（16:59-17:11 全程日志）**：①编码 PASS（正常起跑，全程无 SyntaxError）；②NaT warning 全程零出现 PASS（16:05 版每 FUN 行后必现，对照显著）；④漏斗维持 PASS（q 4135-4351 与基线同模式）；回测 0105→1009 全区间 183 rebalance 轮跑完无崩溃（终点 20261009 positions=1）。

**判据③盲点（诚实披露）**：_composite_score 为 NaN 按剩余权重归一（CANSLIM :489-494 score[valid]+=w; wsum[valid]+=w）——C/A 全 NaN 与有值在选股结果上不可区分，「无 warning+selected 正常」只证不走异常分支，不证 C/A 参与评分。

**补证手段**：wrapper 历史表分支新增 QSFUNDHIST 审计行（新增检测型·纯增益，print 不改数据流）：`QSFUNDHIST t=<table> kept=<完整行数> drop=<剔除数> cc=<有行代码数> n=<请求数>`；kept>0 即多行契约生效。写前快照 e4be74ff。

**门禁拦截实录（D-21 知识反馈有效证据）**：审计注释首版含 ⟺（U+27FA，GBK 不可编码）——test_positive_minimal_ptrade_source_converts 的 code.encode(''gbk'') fail-closed 断言当场拦截（1 failed），替换为「即」后 49/49 全绿。写盘点门禁在 D-21 修复后首次实战拦截同类缺陷。

**部署**：六产物 GBK 重转全 PASS；CANSLIM 77262B sha16=fc8e76d1 部署 QMT转换测试.py，哈希逐位一致，审计行在位。待复测：QSFUNDHIST kept>0（eps 表至少一次）即判据③关闭。

## §8.10 D-22 endDate 空串修复（2026-10-11 17:4x，17:31 复测定谳后续）

**17:31 复测审计行定谳**：QSFUNDHIST t=eps kept=0 drop=0 cc=0（n=6/11/4/9/20）——收集阶段即空，非过滤剔除。

**根因反转（修正前轮推断）**：16:05 的 NaT warning 不能证明 raw 有数据——空->NaN 契约行（pd.DataFrame(index=codes, columns=fields) 全 NaN）本身即足以令策略侧 to_datetime->NaT->astype 崩。16:05（NaT warning）与 17:31（cc=0）统一指向：get_financial_data 对该组参数从未返回有效数据。D-19 二层修复实际消灭的是 NaN 契约行进策略（崩溃消失为真），数据源为空是独立缺陷（新登记 D-22）。

**文档定谳（Context7 /websites/dict_thinktrader_net·innerApi/data_function.html + dictionary/stock.html）**：①startDate/endDate 均为必填字符串（示例 ''20171209''->''20231204''）——wrapper 的 str(date) if date else ''''' 在 date 缺省时传空串，未定义行为高概率返回空；②report_type=''announce_time'' 合法（默认值且防未来数据：''announce_time'' ensures data is not from the future）——排除该嫌疑。

**修复（数据修正型，影响面=历史表消费方 C/A 因子由缺转可用）**：endDate 缺省补安全终点 ''20991231''（announce_time 自身保证不取未来数据，窗口右界拉大不引入泄漏；PIT 过滤由消费方按 publ_date/end_date 自滤，与本地 get_financial 窗口全行契约同构）；审计行增强 raw 形态打印（type + len/items，下次复测可直接看到数据到达形态）。写前快照 9f349c05。

**门禁与部署**：pytest 49/49 绿；六产物 GBK 重转全 PASS；CANSLIM 78292B sha16=330e16e5 部署哈希逐位一致，endparam 与 raw 审计均在位。待复测：QSFUNDHIST kept>0 且 raw=<有数据形态> 即 D-22 关闭 + 判据③（EPS 因子恢复）同步关闭。

## §8.11 D-22 复测全过：EPS 因子链路贯通（2026-10-11 17:5x）

**17:45 复测（sha16=330e16e5）QSFUNDHIST 审计**：kept=12015/cc=6、46015/cc=11、11828/cc=4、28474/cc=9、63698/cc=20、29138/cc=9——kept>0 恒成立，cc=n 全覆盖（每个请求代码均有完整行），raw=Panel items=N 形态确认（D-9 判定正确），drop 数万=1990 全历史窗口 NaN 行被完整行过滤剔除（合理）。

**判据③（EPS 因子恢复）关闭**：C/A 列真有数据参与 _composite_score 六因子合成（skipna 归一不再触发——kept>0 直接证明）。

**选股轨迹 diff 归因（数据修正型验收核心）**：首批 0105 由退化版 002908/301042/301160/301380/600969 变为完整版 301042/301160/301380/600969/603055——退化版选入的 002908 被六因子版剔除；后续 0130/0205/0226/0305 等调仓点大部分一致。diff 集中于因子权重敏感处，与「C/A 由缺转可用」影响面吻合，无不可归因 diff。

**耗时观察**：FUN 轮 get_fundamentals 增至 3.4-10.9s（n=20 时 10.9s，全历史窗口数据量大）；每 FUN 轮一次，可接受。若后续要求可优化窗口起点（本地契约需 3 年年报，1990 远超必要）——登记为可选优化项非缺陷。

**四案全关**：D-18（07-行情函数对齐）/ D-19（历史表多行契约）/ D-21（GBK 物理编码）/ D-22（endDate 空串）全部修复并经复测验证关闭。
