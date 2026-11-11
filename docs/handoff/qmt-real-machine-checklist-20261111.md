# QMT 六产物 v1.8 真机复跑交接单（三件套）

> 文档 ID：`docs/handoff/qmt-real-machine-checklist-20261111.md`
> 版本：v1.0（2026-11-11）
> 用途：交持有**大 QMT 客户端**的执行方，按本单逐项复跑并回填读数
> 裁定依据：R370 三归属裁定——真机复跑＝**交接制**（本线出三件套，经用户在客户端执行）
> 对应验收：`docs/evidence/qfq-compress-v1-v7-acceptance-20261111.md`（④验收 7/7 全绿）

---

## 零、前置认知（防误操作）

| 项 | 事实 |
|---|---|
| 运行环境 | **大 QMT 客户端**（平台内置 Python + 内置数据/接口） |
| **不需要** | **xtquant / miniQMT**（xtquant 是 miniQMT 外部对接接口，已永久停用，与本链路无关） |
| 产物特征 | 首行 `#coding:gbk` ⇒ 供大 QMT 编辑器载入；纯 ASCII 主体 + gbk 头 |
| 依赖 API | `ContextInfo` + `C.get_market_data_ex` / `C.get_financial_data` / `C.get_instrument_detail` / `C.get_stock_list_in_sector` / `C.get_trading_dates` / `C.barpos` / `C.capital` / `C.bar_date` / `C.accountID` / `C.get_bar_timetag` + `passorder` |
| 注入形态 | **EPS 与 QFQ 双压缩（zlib+base64）嵌入**，运行期一次性解压，态缓存后不再解压 |

## 一、件①：六产物 sha16 清单（交付基线）

> 位置：`D:\miniQMT策略实盘\QuantStudio\output\qmt_import\`（gitignored，可再生不迁库）

| # | 产物 | 体积（B） | sha256 前 16 位 | EPS 标记残留 | QFQ 标记残留 |
|---|---|---|---|---|---|
| 1 | `CANSLIM突破成长选股策略.py` | 961,433 | `bcc1b90df7675f24` | 0 | 0 |
| 2 | `fall_reversal.py` | 947,943 | `495e9ca3fb325519` | 0 | 0 |
| 3 | `tech_etf_mvo.py` | 955,744 | `2e43b5445c3261f2` | 0 | 0 |
| 4 | `vol_regime_mom_rev.py` | 963,523 | `182f1005da485530` | 0 | 0 |
| 5 | `weekly_smallcap.py` | 957,649 | `8ac5cbffbdc35b2b` | 0 | 0 |
| 6 | `周频三层.py` | 962,031 | `67a87cb761e594af` | 0 | 0 |

**交付前自检**（在 QuantStudio 侧执行，一行即可）：

```powershell
Get-ChildItem output\qmt_import\*.py |
  Where-Object { $_.Length -gt 900000 -and $_.Length -lt 1000000 } |
  ForEach-Object { '{0,-28} {1,8} {2}' -f $_.Name, $_.Length,
    (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower().Substring(0,16) }
```

**判据**：六行 sha16 与上表**逐位一致**。不一致即交付基线漂移，停手回查（不得以「大小差不多」放行）。

## 二、件②：双三态验证清单（运行期读数）

### 2.1 载入与运行

1. 大 QMT 客户端 → 新建/打开 Python 策略 → 粘贴某个产物全文（或导入文件）；
2. 设定回测区间与标的（按该策略原语义，**不改策略参数**）；
3. 运行一次回测。

### 2.2 必须出现的两行审计（在输出窗口/日志中检索）

| # | 审计行（逐字） | 判据 | 观测锚说明 |
|---|---|---|---|
| A | `QS_QMT_EPS_SNAP embedded n=5206 base=20260801 mode=zlib` | 必须出现，且 `n`/`base`/`mode` 三值**逐字一致** | `n`=标的数 5,206；`base`=快照基准日 20260801；`mode=zlib`=压缩形态 |
| B | `QS_QMT_QFQ_SNAP embedded n=5385 base=20261030` | 必须出现，且 `n`/`base` 两值**逐字一致** | `n`=有跳变标的数 5,385；`base`=库内最新交易日 20261030 |

**不接受的形态**（任一出现即判失败，登记回本线）：

| 现象 | 含义 | 处置 |
|---|---|---|
| `QS_QMT_EPS_SNAP absent` | EPS 层未生效（注入丢失/标记未替换） | 停手，报本线 |
| `QS_QMT_EPS_SNAP broken <原因>` | 常量在位但校验失败 / 解压失败 | **把 `<原因>` 原文回传**（fail-loud 设计如此） |
| `QS_QMT_QFQ_SNAP absent` | QFQ 层未生效 | 停手，报本线 |
| `QS_QMT_QFQ_SNAP broken SNAP_ZB64 decode failed: <ExcType>` | zlib/base64 解压异常 | 回传 `<ExcType>`（预期 `Error`/`zlib.error`） |
| 两行都不出现 | 产物未执行到快照层（可能未进入生命周期） | 回传运行日志尾部 50 行 |

> ⚠️ **absent/broken 必打印**是刻意设计（QD-2026-0022 fail-loud）：静默回退会让下一轮对照误判「差异已消除」。

### 2.3 加载耗时 / 内存读数（需回填）

| 项 | 采集方法 | 本次读数（待填） |
|---|---|---|
| 载入耗时（解析+常量构建） | 客户端载入产物到可运行的时间，秒表/客户端日志时间戳 | ______ 秒 |
| 首次解压耗时 | 首次触发 `_qs_eps_snap_state()` / `_qs_qfq_snap_state()` 的时点差（可用 `time.time()` 包一层临时打印） | ______ 秒 |
| 进程内存峰值 | 任务管理器或客户端内存显示（载入后 vs 空闲基线） | ______ MB |
| 回测总耗时 | 客户端回测完成时间 | ______ 秒 |

> 预期：两压缩常量合计 `325,752 + 450,072 = 775,824` 字符 base64，解压后 EPS 约 3.5 MB、QFQ 约 3.5 MB 结构化数据；**一次性**，态缓存后不再解压。

## 三、件③：内置库兼容核查单

> **这是真机复跑最可能真崩的点**（更正后的准确风险定位）。

产物实际 import 面（实测自产物头部，非推测）：

```text
import pandas as pd      ← 依赖
import numpy as np       ← 依赖
import pytz as _p        ← 依赖（多处以 _pytz/_p 别名重复导入）
import zoneinfo as _z    ← 依赖（Python 3.9+ 标准库；若内置 Python <3.9 需回退 pytz）
import datetime / json / hashlib / time / base64 / zlib / os / re   ← 标准库
```

### 3.1 逐项核查（在客户端 Python 环境执行并回填）

```python
import sys; print('PY', sys.version)
for m in ('pandas', 'numpy', 'pytz', 'zoneinfo'):
    try:
        mod = __import__(m); print(m, getattr(mod, '__version__', 'n/a'))
    except Exception as e:
        print(m, 'MISSING', type(e).__name__, e)
```

| # | 库 | 要求 | 实测版本（待填） | 判据 |
|---|---|---|---|---|
| 1 | `sys.version` | **实测 3.6.8**（`D:\QMT\国金QMT交易端模拟\python36.dll` FileVersion=3.6.8、`pythonw.exe`=3.6.4） | 客户已交付实测 | **已确认**：`zoneinfo`(3.9+) **不存在**，产物内 5 处 `zoneinfo` 引用全部在 `try/except` 保护内（L36←try@L35、L91←try@L89）⇒ 自动回落 pytz 路径；`pytz` 因此**必须有** |
| 2 | `pandas` | 存在即可（产物只用基础 DataFrame 操作） | ______ | **缺失即致命**（`_QS_QMT_FUNDAMENTALS_EXT` 消费） |
| 3 | `numpy` | 存在即可 | ______ | 缺失即致命 |
| 4 | `pytz` | 存在 | ______ | 缺失则产物内 `import pytz` 会失败 ⇒ 致命 |
| 5 | `zoneinfo` | **3.6 不存在（预期内）** | 客户已交付实测 | 非致命：产物已 try 保护，回落 pytz；需确认日志出现 `QS_QMT_TZ_PROBE ... level=` 行 |
| 6 | `pandas._libs.tslibs.timezones` | 存在（产物有 `from pandas._libs.tslibs import timezones as _pdtz`） | ______ | **pandas 2.x 若移除该内部路径即致命** ← 高风险项 |

> ⚠️ 第 6 项是**内部私有路径**（`pandas._libs.tslibs.timezones`），跨 pandas 大版本可能消失——这是比「缺 pytz」更隐蔽的炸点，务必单独确认。

### 3.2 若出现 ImportError 的最小回退（不要在现场改产物）

| 缺失项 | 回退方向 | 归属 |
|---|---|---|
| `pytz` / `zoneinfo` | 框架层时区 shim（`_qs_qmt_tz_shim` 已有相关机制，需扩展覆盖面） | 本线（框架层，走六步） |
| `pandas._libs.tslibs.timezones` | 同上，改为 `try/except` 降级到 `pytz` 或纯 `datetime` | 本线（框架层，走六步） |
| `pandas` / `numpy` 整体缺失 | 环境问题，非产物问题 | 客户端侧 |

> **禁止**在现场手改产物绕过（产物是生成物，改它等于废掉可复现性）；把错误原文回传本线，在**模板层**修。

## 四、回填与回报格式

执行完毕后请按下列格式回报（一项一行，缺项写「未采」）：

```text
[真机复跑回报]
产物：<文件名> ｜sha16：<前台自检实测>
A 行：QS_QMT_EPS_SNAP embedded n=____ base=____ mode=____   （或 absent/broken <原因>）
B 行：QS_QMT_QFQ_SNAP embedded n=____ base=____             （或 absent/broken <原因>）
PY：____ ｜pandas：____ ｜numpy：____ ｜pytz：____ ｜zoneinfo：____ ｜tslibs.timezones：有/无
载入耗时：____ 秒 ｜首次解压：____ 秒 ｜内存峰值：____ MB ｜回测总耗时：____ 秒
异常：<若有，粘贴完整报错，勿节选>
```

## 四点五、真机实测记录（2026-11-11 首轮）

### 交付基线一致性
`output/qmt_snapshot/_diag_T3_both.py` 与 `output/qmt_import/CANSLIM突破成长选股策略.py`
**逐字节相同**：均 960,210 B / sha16 `805219029f30398c`。

### 首轮成功日志（T3 双注入件 · 大 QMT 3.6.8）
```text
start back test mode
QS_QMT_DENY_REMOVED: set_benchmark
QS_QMT_SEMANTIC_DIFF: run_daily time collapsed to per-daily-bar
[QS-QMT log.info] [canslim_breakthrough] initialize done: target=5, stop=7.5%/20.0
QS_QMT_QFQ_SNAP embedded n=5385 base=20261030        ← G-3 达成
QS_QMT_PROGRESS func=filter_stock_by_status done=5225/5225 pct=100.0 elapsed=0.2s
```

### 判据达成情况
| 门 | 状态 | 证据 |
|---|---|---|
| G-1 交付基线 | ✅ | T3 与正式件 sha16 同为 `805219029f30398c` |
| G-2 EPS 层 | ⏳ **待惰性触发** | `_qs_eps_snap_state()` 惰性：首次取 EPS 基本面时才打印；本轮日志止于 `filter_stock_by_status` 完成，尚未进入 EPS 取数点 |
| G-3 QFQ 层 | ✅ | `QS_QMT_QFQ_SNAP embedded n=5385 base=20261030` |
| G-4 环境 | ✅ 部分 | Python **3.6.8** 确认；pandas/numpy 可用（initialize 已过）；zoneinfo 缺失但已保护 |
| G-5 无异常 | ✅ 本轮无 Traceback | — |

### ⚠️ 历史误报澄清（同一内容两次结果不同）
首次尝试报 `SyntaxError: EOL while scanning string literal (<string>, line 671)` 的**不是产物缺陷**：
- 产物在 3.6/3.7/3.8/3.9/3.11 五种语法规则下 `ast.parse` **全部通过**；
- 4 种编码（gbk / utf-8-replace / latin-1）解析**全部通过**；gbk 汉字次字节不含 0x5C/0x27/0x22（无经典 GBK 转义陷阱）；
- ZB64 块逐行严格匹配 `^    '[A-Za-z0-9+/=]{116}'$`，L671 内容正常、无不可见字符；
- L671 字符偏移 26,809，**不落在** 32K/64K/96K/128K 任何截断边界。
- **同一份字节内容（T3）改以文件方式载入后完整跑通** ⇒ 结论：`<string>` 报错发生在**载入/粘贴环节的内容损坏**，非产物问题。
- **操作纪律**：960 KB / 9,734 行的产物**禁止粘贴进编辑器**，必须用「打开文件/导入」方式载入。


## 四点六、EPS 代码归一化修复批（2026-11-11 第二轮）

> 本文档 §一 的六产物 sha16 已更新为**本批**（含 EPS code 归一化修复 `2da8b40`）。
> §四点五 记录的是**上一批**（`805219029f30398c`）的真实实测读数，保留作为历史证据，不篡改。

### 本批交付基线（`output/qmt_import/`，0.917–0.919 MB）

| # | 产物 | 体积（B） | sha256 前 16 位 |
|---|---|---|---|
| 1 | `CANSLIM突破成长选股策略.py` | 961,433 | `bcc1b90df7675f24` |
| 2 | `fall_reversal.py` | 947,943 | `495e9ca3fb325519` |
| 3 | `tech_etf_mvo.py` | 955,744 | `2e43b5445c3261f2` |
| 4 | `vol_regime_mom_rev.py` | 963,523 | `182f1005da485530` |
| 5 | `weekly_smallcap.py` | 957,649 | `8ac5cbffbdc35b2b` |
| 6 | `周频三层.py` | 962,031 | `67a87cb761e594af` |

### 本批新增必验项（G-6 / G-7，缺一即判未通过）

| 门 | 判据 | 通过条件 |
|---|---|---|
| **G-6** | EPS 对齐**真的生效** | 日志出现 `QS_QMT_EPS_ALIGN overridden=<正整数>`（修复前恒 0）；且 `span_short` / `code_miss` 数值合理（非全量落进其一） |
| **G-7** | 五计数审计行格式 | 必须为 `QS_QMT_EPS_ALIGN overridden=.. mismatch=.. span_short=.. code_miss=.. unmatched=..`（含 `code_miss` 字段）|

### A-5b 真机数值对拍（审核补强项·必做）

从日志中取 **≥1 个** `QS_QMT_EPS_MISMATCH` 样例（格式 `code@end_d8:panel=X,local=Y`），回报该样例原文；
本线将用同一份快照独立核实：该 `(code归一化后, end_d8)` 在 `_QS_QMT_EPS_SNAP_ZB64` 解压结果中的 eps 值，必须与日志中的 `local=Y` **逐位一致**（堵住「归一化修了但取错期次」的残余风险）。

> 本地段已用真实快照做等价对拍通过（`002568`：`20251231→0.62`、`20260331→0.21`，repr 逐位一致）；真机段待本批回填。

### 判据汇总（本批）

| 门 | 状态 |
|---|---|
| G-1 交付基线 | 待客户按上表核六 sha16 |
| G-2 EPS 层 embedded | 待核 `QS_QMT_EPS_SNAP embedded n=5206 base=20260801 mode=zlib` |
| G-3 QFQ 层 embedded | 待核 `QS_QMT_QFQ_SNAP embedded n=5385 base=20261030` |
| G-4 环境 | 已确认 Python **3.6.8**（`python36.dll`），pandas/numpy 可用，zoneinfo 已 try 保护 |
| G-5 无异常 | 待核无 Traceback |
| **G-6 overridden>0** | 待核（本批修复目标） |
| **G-7 五计数审计行** | 待核 |


## 四点七、EPS 归一化修复批 · 真机验证结果（2026-11-11 17:21–17:30）

### G-6 / G-7 判定：**全部达成 ✅**

九处 EPS 取数审计行（本批产物 `CANSLIM突破成长选股策略.py` 961,433 B / `bcc1b90df7675f24`）：

```text
QS_QMT_EPS_ALIGN overridden=57 mismatch=0 span_short=139 code_miss=0 unmatched=6   (kept=196 drop=6)
QS_QMT_EPS_ALIGN overridden=43 mismatch=0 span_short=183 code_miss=0 unmatched=3   (kept=224 drop=5)
QS_QMT_EPS_ALIGN overridden=22 mismatch=0 span_short=41  code_miss=0 unmatched=2   (kept=63  drop=2)
QS_QMT_EPS_ALIGN overridden=15 mismatch=0 span_short=23  code_miss=0 unmatched=1   (kept=38  drop=1)
QS_QMT_EPS_ALIGN overridden=13 mismatch=0 span_short=53  code_miss=0 unmatched=1   (kept=66  drop=1)
QS_QMT_EPS_ALIGN overridden=40 mismatch=0 span_short=89  code_miss=0 unmatched=5   (kept=129 drop=5)
QS_QMT_EPS_ALIGN overridden=15 mismatch=0 span_short=4   code_miss=0 unmatched=1   (kept=19  drop=1)
QS_QMT_EPS_ALIGN overridden=10 mismatch=0 span_short=1   code_miss=0 unmatched=1   (kept=11  drop=1)
QS_QMT_EPS_ALIGN overridden=11 mismatch=0 span_short=22  code_miss=0 unmatched=1   (kept=33  drop=1)
```

| 门 | 判据 | 实测 | 判定 |
|---|---|---|---|
| **G-6** | `overridden > 0`（修复前恒 0） | **9/9 处均 > 0**（57/43/22/15/13/40/15/10/11） | ✅ |
| **G-7** | 五计数审计行含 `code_miss` | 9/9 处均含 `code_miss=0` | ✅ |
| G-2 | EPS embedded | `embedded n=5206 base=20260801 mode=zlib` | ✅ |
| G-3 | QFQ embedded | `embedded n=5385 base=20261030` | ✅ |
| G-5 | 无异常 | 2026-06-01 → 09-30 完整跑完 86 个调仓周期，无 Traceback | ✅ |

### 守恒自证（本轮最强证据）

**每一处都满足 `overridden + span_short + unmatched = kept + drop`**：

| # | overridden | span_short | unmatched | 小计 | kept + drop | 守恒 |
|---|---|---|---|---|---|---|
| 1 | 57 | 139 | 6 | 202 | 196 + 6 = 202 | ✅ |
| 2 | 43 | 183 | 3 | 229 | 224 + 5 = 229 | ✅ |
| 3 | 22 | 41 | 2 | 65 | 63 + 2 = 65 | ✅ |
| 4 | 15 | 23 | 1 | 39 | 38 + 1 = 39 | ✅ |
| 5 | 13 | 53 | 1 | 67 | 66 + 1 = 67 | ✅ |
| 6 | 40 | 89 | 5 | 134 | 129 + 5 = 134 | ✅ |
| 7 | 15 | 4 | 1 | 20 | 19 + 1 = 20 | ✅ |
| 8 | 10 | 1 | 1 | 12 | 11 + 1 = 12 | ✅ |
| 9 | 11 | 22 | 1 | 34 | 33 + 1 = 34 | ✅ |

**修复前后对撞（第 1 处，同一 code 集）**：
```text
修复前：overridden=0   span_short=196  （全部误判为"期次缺失"）
修复后：overridden=57  span_short=139  code_miss=0
        ⇒ 196 = 57 + 139  完全守恒
```
即：修复前那 196 行**全部**因 code miss 落进 `span_short`；修复后**精确拆分**为「57 行成功覆盖 + 139 行真实期次缺失」，
**无遗漏、无重复、无新增** ⇒ 归一化只修正匹配、未改变任何行数语义。

### 关于 `mismatch=0`：A-5b 真机段的路径说明

九处 `mismatch` **全部为 0**，即不存在 `QS_QMT_EPS_MISMATCH` 行 ⇒ 审核补强项原定的「取 ≥1 个 MISMATCH 样例对拍」**无样本可依**。

**原因**：`mismatch` 统计的是「同 `(code, end_d8)` 下面板值与快照值差异 > 1e-6」的行数。`mismatch=0` 说明
**对能命中同一报告期的行，QMT 面板的 `s_fa_eps_basic` 与本地库逐值一致** —— 这是好消息（面板与本地库同源同值），
但也意味着「差异样例」这条路本身不存在。

**替代验证（等价强度，建议以此判 A-5b 真机段通过）**：
1. `code_miss=0`（9/9 处）⇒ 归一化对**全部真实面板 code 形态**均命中，无残余 miss；
2. 守恒等式 9/9 成立 ⇒ 处理行数无遗漏/重复；
3. 本地段已用**真实快照**做逐位对拍（`002568`：`20251231→0.62`、`20260331→0.21`，`repr` 逐位一致）；
4. `overridden>0` 且 `mismatch=0` 组合 ⇒ 覆盖确实发生，且覆盖值与面板值恰好相等（无风险掩盖）。

### 附带发现（数据层面，非代码缺陷，已登记 ISSUE）

第 8 处 `span_short=1 sample=001331.SZ@20260630` —— **最新一期**却缺快照：
本地 EPS 库该标的**最新期只到 20260331**，而 QMT 面板已有 20260630 期（半年报）。
⇒ 本地库快照相对 QMT 面板**滞后约一个季度**。这是**数据新鲜度**问题（快照 `base=20260801` 导出时的库内状态），
不是归一化缺陷；修复后 `span_short` 已能精准指向「快照缺该期」，正是 G-7 五计数分列的价值体现。

## 五、判据汇总（过/不过）

| 门 | 通过条件 |
|---|---|
| G-1 交付基线 | 六产物 sha16 与件①表**逐位一致** |
| G-2 EPS 层 | A 行出现且 `n=5206 base=20260801 mode=zlib` 逐字一致 |
| G-3 QFQ 层 | B 行出现且 `n=5385 base=20261030` 逐字一致 |
| G-4 环境 | 件③ 六项全「存在」（第 6 项 `tslibs.timezones` 必须有） |
| G-5 无异常 | 无 Traceback；若有，**回传完整原文** |

四项全过 ⇒ 本线判「真机复跑通过」，闭环；任一不过 ⇒ 按 §二.2 表登记，回本线走框架层修复（六步）。
