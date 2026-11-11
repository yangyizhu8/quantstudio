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
| 1 | `CANSLIM突破成长选股策略.py` | 960,210 | `805219029f30398c` | 0 | 0 |
| 2 | `fall_reversal.py` | 946,720 | `4d29170ff6038d3d` | 0 | 0 |
| 3 | `tech_etf_mvo.py` | 954,521 | `eff68d2b4b0b3cd6` | 0 | 0 |
| 4 | `vol_regime_mom_rev.py` | 962,300 | `53455c7051cf9c0c` | 0 | 0 |
| 5 | `weekly_smallcap.py` | 956,426 | `195fe22db6dd9132` | 0 | 0 |
| 6 | `周频三层.py` | 960,808 | `ee113aa1c5625956` | 0 | 0 |

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

## 五、判据汇总（过/不过）

| 门 | 通过条件 |
|---|---|
| G-1 交付基线 | 六产物 sha16 与件①表**逐位一致** |
| G-2 EPS 层 | A 行出现且 `n=5206 base=20260801 mode=zlib` 逐字一致 |
| G-3 QFQ 层 | B 行出现且 `n=5385 base=20261030` 逐字一致 |
| G-4 环境 | 件③ 六项全「存在」（第 6 项 `tslibs.timezones` 必须有） |
| G-5 无异常 | 无 Traceback；若有，**回传完整原文** |

四项全过 ⇒ 本线判「真机复跑通过」，闭环；任一不过 ⇒ 按 §二.2 表登记，回本线走框架层修复（六步）。
