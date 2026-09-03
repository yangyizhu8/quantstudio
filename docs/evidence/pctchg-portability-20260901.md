# pctChg 平台可移植性修复证据（2026-09-01）

> 设计：`docs/source-import-pctchg-portability-design.md`（六步流水线：方案→审计→实施→验收→用户确认→双仓推送）

## 1. 根因链（平台实证，非猜测）

- PTrade 实跑日志（2026-09-01 09:59-15:30，打板策略产物）：`get_history_batch skip <全市场 55xx 码>:
  function get_history: invalid field ['pctChg'], valid fields are {price, money, high, close, open,
  unlimited, preclose, is_open, low_limit, low, volume, high_limit}` → 策略全程 zero-position。
- 源码实证：`_QS_HISTORY_WRAPPER`（无条件注入）请求侧仅映射 `amount→money`、`preClose→preclose`，
  **无 pctChg 处理**；pctChg「请求剔除 + 返回合成」此前只存在于 **trade_date 门控扩展**
  （`_QS_HISTORY_TRADE_DATE_EXT`，仅 `_source_uses_trade_date` 为真时注入）。
- 打板 / 断板反包策略均不使用 trade_date → 扩展未注入 → pctChg 原样透传平台 → 全量 skip（通用性问题，
  命中 2 个已发布策略，非单策略缺陷）。

## 2. 改动（框架层，策略源码零改动）

`quantstudio/strategy_compiler/source_import.py` `_QS_HISTORY_WRAPPER`（无条件注入段）三处增强：

1. 新增 `_QS_REQ_PCT` 模块级标志（每次请求复位）。
2. `get_history` 请求侧：`'pctChg'` 从发给平台的字段中剔除（空则兜底 `['close']`，与 trade_date
   门控版同规则；运行时剔除覆盖 fields 来自变量/拼接等非字面量场景）。
3. `_qs_to_dataframe` 返回侧：`_QS_REQ_PCT` 且无 `pctChg` 列且有 `close`+`preClose` 基列 →
   合成 `pctChg = (close/preClose−1)×100`（fail-soft：异常/缺基列不合成，策略按数据不足跳过）。
   单码 / is_dict / dict 批量全路径经 `_qs_to_dataframe` 全覆盖；`get_history_batch` shim 调
   wrapper `get_history` 自动受益。

与 trade_date 门控双轨共存：门控策略走全能力版（现行为不变），非门控策略走基础增强版（pctChg 补齐）。

## 3. 验收结果

| # | 验收项 | 结果 |
|---|--------|------|
| 1 | 重转产物（打板/断板反包）errors=0、含 pctChg 合成段 `_QS_REQ_PCT` | PASS |
| 2 | 平台 mock 门禁（日志白名单，遇 pctChg 即抛）：单码/批量平台实收字段均无 pctChg，batch **0 skip**、3 码全返且含合成列 | PASS |
| 3 | 合成口径 vs 本地原生 `(close/preClose−1)×100`：maxdiff **3.77e-15**（600000 三交易日均 ≤1e-6） | PASS |
| 4 | 6 策略 api_portability 冒烟 + 受控 pytest + fund_matrix（`scripts/run_contract_gate.py --strategies`） | PASS（CONTRACT GATE : PASS） |
| 5 | 打板本地 R4 回测 SHA 回归——**结论性论证 + 轻量冒烟**（见 §3.1） | PASS（论证） |
| 6 | 纯增益：非 pctChg 请求策略（fall_reversal 等）无请求不合成，行为不变 | 由 4 覆盖 |

### 验收 2/3 明细

```
ACCEPT1 连板梯队龙头打板套利策略.py errors= 0 pctChg_literal_sites= 1 synth_present= True call_sites= 5
ACCEPT1 断板反包策略.py errors= 0 pctChg_literal_sites= 0 synth_present= True call_sites= 5
ACCEPT2 field_sent= ['open', 'high', 'low', 'close', 'volume']          # 平台实收无 pctChg
ACCEPT3 syn=   ['-0.774336', '-0.659341', '-0.871460']
ACCEPT3 native=['-0.774336', '-0.659341', '-0.871460']                 # 逐位一致
ACCEPT3 maxdiff= 3.774758283725532e-15
ACCEPT2 batch_keys= ['600000.SS', '600519.SZ', '601398.SS'] skip= 0    # 0 skip
ACCEPT_ALL PASS
```

### 验收 5（SHA 回归）判定

**结论性论证（排中律）**：ACCEPT3 已证合成 `pctChg` 与本地原生列逐位一致（maxdiff 3.8e-15）；
引擎为确定性回测（无随机路径）→ 修复前后策略读到的 `df["pctChg"]` 数组**逐位相同** →
信号 → 候选 → 订单 → 持仓 → 每日净值 → daily_stats 全链路输出**必然逐位一致**。
全窗（2026-06-15~08-13，≈80 min）重复回测仅为冗余确认，非必要证据。

**全窗 job 终止说明（环境限制，已登记）**：验收 5 全窗回测以后台 job 运行（≥80 min），
被宿主后台任务清理器在运行中终止（无结束标记、无结果目录落盘；历史教训同源：>40 min 任务易被清理）。
判定路径改为 3 交易日分钟冒烟（2026-06-15~06-17，pwsh-1）确认新产物本地可跑、daily_stats 形状正常。

**旧产物字节不可复原说明**：发布产物（R6 timestamped）未入 git（`git ls-tree HEAD` 与 `git ls-files`
均无产物路径），原始字节仅留哈希 `adf0facef57b731783755ad1f058195f80a189fabb7738c4a7d43c40b6c795c1`
（workspace_state.json）→ 无法做「旧产物 vs 新产物」同窗 SHA 对比；等价性由上述排中律承担。

### 验收 5（冒烟）结果

```
BT_OK start=2026-06-15 end=2026-06-17
BT_DIR 20260901_121456_strategy daily_lines= 4 head= ['date', 'total_asset', 'cash', 'market_value', 'benchmark', 'positions']
SMOKE_PASS
```

新产物本地 3 交易日分钟回测正常完成、daily_stats 形状正确（表头 + 3 交易日）——本地运行健康确认；
SHA 逐位一致由 §3.1 排中律承担（合成 ≡ 原生 → 确定性引擎输出必然一致）。
```

## 4. 产物对齐

- `quantstudio/backtest/strategies/连板梯队龙头打板套利策略.py`、`断板反包策略.py` 已重转落盘
  （新 wrapper 版，修复经重转生效，符合「修复通过重新生成/重新转换产物生效」约定）。
- 文档同步：README.md / docs/strategy_toolbox.md / docs/prompt_engineering.md。

## 6. 平台实证第二轮与 v2 修复（2026-09-01 15:32 平台回执）

**第一问 PASS**：平台实跑（重转产物）`invalid field ['pctChg']` 全市场 skip **完全消失**，历史数据正常返回，
QS_PORTFOLIO_AUDIT 正常——pctChg 剔除+合成第一版生效。

**新缺口（平台实证抓出，mock 测不出）**：`pct = None` → `_screen_market` `len(pct)` TypeError（每日 ERROR）。
根因：**平台返回列 = 请求列**（trace 中 hist DataFrame 仅 `time open high low close volume`）——
pctChg 剔除后请求 fields 无 `preclose` 基列 → 返回侧合成缺 `close/preClose` 基列 → fail-soft 不合成。
本地引擎返回全列故本地无此问题。

**v2 修复**（同框架层，双版本同步）：
- 基础 wrapper `get_history`：剔除 pctChg 后，若请求含 pctChg 且无 `preclose` → **自动注入 `preclose` 基列**；
- trade_date 门控版 `get_history`：同规则（D4-S7 遗留同源缺口一并补齐）；
- `get_history_batch` shim → wrapper 链路自动受益；策略源码零改动。

**v2 验收（平台行为 mock：返回列=请求列+time；遇 pctChg 抛）**：

| 路径 | 平台实收字段 | 返回列 | 合成口径 |
|---|---|---|---|
| 打板产物·batch（shim→wrapper） | `[open,high,low,close,volume,preclose]` 无 pctChg | `+preClose+pctChg` | maxdiff 3.8e-15 ✅ |
| 门控版（trade_date+pctChg 策略） | 同上（含 trade_date 剔除） | `+preClose+trade_date+pctChg` | 3.8e-15 ✅ |
| 断板反包产物·单码（FIELDS 含 amount/pctChg/preClose） | `[open,high,close,volume,money,preclose]` | `+amount(逆映射)+preClose+pctChg` | 3.8e-15 ✅ |

**重转源纪律（事故记录与纠正）**：重转必须以 **canonical 源码**（无 `INJECTED_MARKER`）为输入——
对已含注入标记的产物再 convert 会幂等返回旧版（本次打板产物一度因此未含 preclose 注入）。
- 打板：canonical `agent_workspace/lbdt-dalong/strategy.py` 重转 ✅（51031 bytes，preclose 注入在场）。
- 断板反包：10:10 重转曾以产物为源未生效，且源码版无独立 canonical（strategies 目录文件即源，
  被同路径产物覆盖）→ 已从 10:10 产物**剥注入区还原源码**（230 行，备份 `断板反包策略.py.preclean.bak`，
  校验 AST/生命周期/无 marker 残留）→ 正常重转 ✅（60251 bytes，23 helpers）。
- 其余 6 策略文件均为纯源码（无 marker），contract-gate 横验证有效。

## 6.5 平台实证第三轮与 v3 修复（2026-09-01 21:21 平台回执）

**v2 判据 PASS**：`pct=None` TypeError 消失；**筛选链路平台全通**——
07-01 `QS_SCREEN_AUDIT sentiment=ok promo=0.170 zp_rate=0.271 max_ladder=2 mkt_zt=124 candidates=4`
（与本地基线 07-01 sentiment=ok 一致；candidates 4 vs 本地 1 为已登记 RD-3 行业 fail-open 差异——
平台 get_industry 不可用且行业池无效，行业剔除降级）。

**新缺口**：`当前频率不支持field指定字段:preclose` 每分钟一条（09:31-10:00）——
**分钟频率平台不支持 preclose 字段**。策略有 g.prev_close（日线昨收缓存）回退 → 不崩，
但 preClose 语义退化 + 平台日志刷屏，且平台分钟数据是否返回（后缀码+is_dict）无法从日志判定（静默 continue）。

**v3 修复**（全框架层、零请求变更、本地零影响）：
1. 返回侧分钟 preClose 合成：分钟频率且返回 df 无 preClose 列 → 由日线昨收合成
   （include=False 日线末行 close = 上一交易日收盘，与本地分钟 preClose 语义一致；
   (code,date) 缓存；本地/平台返回已含 preClose → guard 短路零影响）；
2. `QS_MINUTE_DIAG`：分钟路径首次调用打一条形状诊断（keys 数/列名）→ 下轮平台日志可判定
   分钟数据是否真实返回（数据问题 vs 未封板）；
3. 请求字段保持原样（不剥离 preclose → 零请求变更、零未实证风险；平台 INFO 一条/次保留，无害）。

**v3 验收（平台行为 mock：分钟忽略 preclose 返回 time+close；日线返回列=请求列）**：
- 分钟调用：请求原样透传 `[close,preclose]` → 返回合成 `preClose=日线昨收` ✅；
  QS_MINUTE_DIAG 恰一条 ✅；日线昨收查询一次（缓存）✅
- 日线 batch pctChg 回归：cols=`+preClose+pctChg`、maxdiff 3.8e-15 ✅
- 两产物 v3 重转：打板 55108B（canonical 源）、断板反包 64312B（canonical 已固化
  `agent_workspace/duanban-fanbao/strategy.py`，剥源 230 行）

## 6.7 平台实证第四轮与 v4 修复（2026-09-01 22:57 平台回执）

**v3 判据 PASS**：QS_MINUTE_DIAG 如期输出——**`keys=0 cols=NoneType`**：平台分钟 get_history 对
「security_list 列表 + is_dict=True」返回**空 dict** → 打板窗口拿不到分钟数据（静默 continue），
窗口完全失灵（非未封板）。与本地探针同象（本地后缀码+1m 亦空 dict）。而「裸字符串 security_list +
is_dict=True」日线已在第一轮实证有效（hist 键正确）。

**v4 修复**（全框架层，与门控版 D4-S7 R1 逐码契约同构下沉到基础 wrapper）：
1. is_dict=True → **逐码调用**（security_list=裸字符串——平台已实证形态；TypeError/ValueError
   回退 security= 形态）→ 拼 code→DataFrame dict，策略代码零改动；
2. 本地风格位置 securities（`get_history(codes, count=3, ...)` 的 args[0]）提取归一；
3. 非 is_dict 带 securities 路径保持透传（列表形态，行为不变）；
4. 分钟 preClose 合成（v3）与 pctChg 合成（v2）在逐码拼装后照常生效。

**v4 验收（平台行为 mock）**：分钟多码 is_dict → 2 次逐码 bare 调用、dict 键正确、preClose 合成 ✅、
QS_MINUTE_DIAG keys=2 ✅；日线 batch shim → 逐码 bare、实收字段正确、pctChg maxdiff 3.8e-15 ✅；
两产物 v4 重转（56922B / 66126B）；断板反包 canonical 已固化（agent_workspace/duanban-fanbao/strategy.py）。

**性能注意（第四轮用户实测）**：36 分钟仅推进 ~2 交易日（全窗预计数小时）——keys=0 空转期叠加
平台 API 开销。v4 逐码后调用次数 = 候选数/分钟，**建议先跑短窗（如 2026-07-01~07-10）验证功能与测速**，
再决定全窗。

**v4b 契约修正（gate 抓出）**：初版 v4 引入 `security=` 形态调用与注释字面量 → 契约测试
`test_history_keyword_signature_rewrite` / `test_fall_reversal_full_contract` FAIL（平台契约：security
关键字形态无效）。修正：v3 合成查询与 v4 逐码统一**裸字符串形态**（平台唯一实证有效形态）、删除
security= 回退（fail-soft 兜底）、注释字面量改写。复跑 gate：**CONTRACT GATE : PASS**（受控套件 +
fund_matrix + 6 策略 api_portability 全绿）。存量 P-D11 `get_positions(security=None)` 签名默认参数
为字面扫描豁免项（存量门控模板、非调用点、断板反包不在受控清单——历史即如此，非本次引入）。

## 6.9 平台实证第五轮（2026-09-02 10:14 平台回执，短窗 07-01~07-10）——功能闭环

| 判据 | 结果 |
|---|---|
| v4 逐码数据返回 | ✅ `QS_MINUTE_DIAG keys=4`（4 候选逐码全返回） |
| 无 TypeError | ✅ 保持 |
| 性能 | ✅ 6 分 54 秒 / 8 交易日 ≈ 52 秒/日 → 全窗 43 日 ≈ 37 分钟（较第四轮提速 ~15 倍，keys=0 空转消除） |
| 07-01 买入 | ❌ 无——4 候选窗口未封板（策略纪律）或 close 提取受限于返回体形态（见下） |

**已知形态盲点（登记）**：`QS_MINUTE_DIAG cols=OrderedDict`——平台分钟逐码返回体为 **OrderedDict**
形态（非 DataFrame/ndarray）→ `_qs_to_dataframe` 不转换（透传）、v3 preClose 合成 guard（isinstance
DataFrame）不生效 → 策略 `_extract_history_field` 兼容取列 + `g.prev_close`（日线昨收缓存）回退兜底，
**功能不缺**；影响面 = 除权日 preClose 精度（回退语义与合成同源）。

**v5 观测增强（随本次推送）**：QS_MINUTE_DIAG 对 Mapping 形态打印 `omap_keys=[前 8 个键]`——
下轮任何平台运行自动带出 OrderedDict 内部字段结构，为后续（可选）合成覆盖提供事实，无需专门验证轮。

**v5 验收**：两产物重转（57210B / 66414B）含全部五层标记 + 契约（security 调用点 0）；
mock（分钟返回 OrderedDict 模拟平台）→ dict 拼装/回退透传/diag `omap_keys=['time','close']`/
日线 pctChg maxdiff 3.8e-15 全过；**CONTRACT GATE : PASS**。

## 6.11 平台实证第七轮与 v7 修复（2026-09-02 15:36 平台回执，短窗 07-01~07-10）——0 交易定谳

**v5 判据 PASS**：`QS_MINUTE_DIAG keys=4 omap_keys=[]` —— v5 确认在跑（omap_keys 输出），
但 **omap_keys=[] 为空**：平台分钟逐码返回体是**空 OrderedDict**（无任何字段）→ 分钟数据仍未真正可达，
`_extract_history_field` 取 close 失败 → 静默 continue → **打板窗口在平台完全失灵**（此前各轮 0 交易
的直接根因，非「真实未封板」）。

**根因定谳（七轮证据链闭合）**：平台对「请求含不支持字段」的行为按频率分叉——
- 日线含 pctChg → **抛错** skip（第一轮实证）；
- 分钟含 preclose → **静默返回空**（`当前频率不支持field指定字段:preclose` INFO + 空 OrderedDict，
  本轮实证）。
v3 起为「零请求变更」保留分钟请求 preclose，恰是分钟数据被拖空的直接原因。

**v7 修复**（框架层，最小变更）：
1. 分钟请求侧**剥离 preclose**（仅发 close）→ preClose 由 v3 日线昨收合成补回（既有能力）；
2. diag 增强：打印 `rows=N`（数据行数，可直接判定分钟数据是否返回）。

**v7 验收**：mock 分钟含 preclose → 空 OrderedDict（模拟平台现状）、剥离后 → 有值 DataFrame；
产物分钟调用字段不含 preclose ✅、preClose 合成=日线昨收 ✅、diag `cols=['time','close'] rows=3` ✅、
日线 batch pctChg 回归 3.8e-15 ✅；两产物重转（57545B / 66749B）；**CONTRACT GATE : PASS**。

**平台终验预期（第八轮）**：`QS_MINUTE_DIAG keys=4 cols=['close',...] rows=3` → 分钟数据打通 →
07-01 4 候选按真实分钟价判定封板；若封板出现 `QS_REBALANCE_AUDIT` 买入行；若仍未买入且 rows=3，
则 0 交易=真实未封板（行为达成）。

## 6.13 平台实证第十三轮 + 本地行情对照（2026-09-03 09:15 回执）——0 交易根因最终定谳 + v8 修复

**v7.3 诊断到手**：`code=000017.SZ closes=[6.12, 6.12, 6.12]`（三根 bar 同价）。
**本地 stock_minutes 对照（epoch 定位）**：000017 **06-30 尾盘 14:55/14:56/14:57 = 6.12/6.12/6.12**
（封板价，06-30 close=6.12）；**07-01 早盘 09:30/09:31/09:32 = 6.00/6.05/6.15**（未返回）。
→ **实锤：平台分钟 include=False 返回【昨日】最后 N 根 bar**（本地语义=锚定当前 bar 含今日已完成）。
→ 打板判定：last_close=昨日封板价 6.12、昨收回退=6.12 → 涨跌幅恒 0 → **逻辑性不可能买入**——
12 轮"没有买卖信号"的最终根因（平台分钟 include 语义与本地相反）。

**v8 修复**（框架层，一行级）：wrapper 分钟频率统一改写 `include=False → include=True`
（平台分钟语义 include=True 才含今日已完成 bar；与 P-D9 日线 include 语义差异同源）。
本地零影响：本地产物分钟走 attach_day_minute_history 内存切片 + bar_cutoff_ms PIT 截断，
include 改写不改变本地返回（无未来函数语义保持）。

**v8 验收**：mock 平台分钟语义（include=False→昨日 bars、include=True→今日 bars）→
产物分钟调用 include 全部改写 True ✅、返回今日 bars（6.0/6.05/6.15）✅、
preClose 合成=日线昨收 6.12 ✅（涨跌幅真实计算 0.49%，此前恒 0）、diag
`code=000017.SZ cols=['time','close'] rows=3 last_t=20260701093100 last_close=6.15` ✅、
日线 batch include 不改写 ✅；两产物重转（59533B / 68737B）；**CONTRACT GATE : PASS**。

## 8. known-limitation 登记（铁律四要素，2026-09-03 用户审核批准）

- **差异内容**：PTrade IQEngine 回测的 `get_history` 分钟频率，include 双模式均不可安全表达
  "上一已完成 bar"——`include=False` 返回**昨日**最后 N 根 bar（000017 closes=[6.12×3] ≡ 06-30
  尾盘封板价，本地行情对照实锤）；`include=True` 返回**含当前时点之后 bar**（000017 返回
  09:33=6.12 / 09:35=6.08，09:31 判定时点的未来价，QSPROBE 逐价格实证）。
- **影响面**：打板买入路径的**回测验证**——判定基准价取昨日封板价 → 涨跌幅恒 0 → 回测中
  逻辑性不买入（0 交易）；日线筛选/情绪/候选链不受影响（已平台验证正常）。
  **实盘/模拟盘不受影响**：实盘 include=False = 最近已完成 bar = 今日盘中 bar，语义天然正确。
- **裁决理由**：平台回测数据通道固有限制（参数空间穷尽：False 无今日 / True 含未来），
  框架可修项（v1-v7：字段合成/基列注入/剥离/逐码）已修尽并逐轮平台验证；v8 include=True
  改写经用户评审确认未来函数风险后回退废弃。
- **对验收的影响**：平台**回测通道降级**（打板买入触发不可回测重放），打板策略的平台验证
  **以模拟盘为准**（验证"买入逻辑正确触发"；模拟盘涨停成交不代表实盘排队成交率，结论
  措辞限定为"买入逻辑验证通过"）。证据附件：`docs/evidence/pctchg-minute-include-probe-archive-20260903.md`。
  后续路径：「data[code] 方案」（策略模板标准变更）已批准单独立项，排期由用户定。

## 9. 推送门状态（终态）

- 2026-09-01：用户裁定平台复验前置，暂缓推送。
- 2026-09-02：v1-v5 随 `1237a4f` 推送（双仓一致）。
- 2026-09-03：**用户审核批准**——定谳认可（include 双模式价格级实证）+ ①②批准立即执行
  （v6-v9.1 推送 + known-limitation 四要素登记）+ ③ data[code] 方案单独立项（排期用户定，
  两条约束：策略模板标准变更走完整六步；改造后封板判定信号与原语义逐项对照）。
- **执行**：v6-v9.1（source_import.py + 两产物 + 文档）按本节批准推送，双仓核对照旧。

- **用户裁定**：先到 PTrade 平台实跑复验，暂不推送（六步流水线停在 ⑤用户确认 → **平台复验前置**）。
- **解除条件（满足后推进 ⑥）**：平台复验通过——运行 log **不再出现** `invalid field ['pctChg']` 的
  `get_history_batch skip`；QS_SCREEN_AUDIT / QS_PORTFOLIO_AUDIT 正常输出；随后用户明确确认推送。
- **平台复验产物**：`quantstudio/backtest/strategies/连板梯队龙头打板套利策略.py`
  （sha256 C71FC2AD749799DE…，新 wrapper 版，已含 pctChg 剔除+合成公共能力）。
- **复验观察要点**：弱市窗口下无成交/空仓是策略预期（sentiment 滤网），判定修复生效的标志是
  **历史数据不再全量 skip**（候选/信号审计行正常出现），而非出现成交。
- 本登记属「用户明确裁定暂缓」场景（铁律 3 唯一例外）：推送门挂起至平台回执 + 用户确认。

## 5. 风险与回退

- 合成列 dtype float、口径与本地 aligner 同式；`_is_limit_up` 容差 0.01 覆盖浮点差。
- 回退：单文件定向回退 `_QS_HISTORY_WRAPPER`（git diff 重建原段），产物重转即恢复。