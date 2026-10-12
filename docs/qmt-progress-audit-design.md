# D-24 设计：QMT 漏斗取数层进度审计行

> 六步流水线环节：**①方案（修订稿 v1.2）**｜状态：候②**定点复核**
> 关联：`docs/ISSUES.md` D-24（P2 可观测性）｜姊妹项：D-25（P3 性能冗余，另起一轮）

## 版本变更日志

| 时间 | 发起人 | 变更内容 | 影响范围 | 评审结论 | 对应里程碑 |
| --- | --- | --- | --- | --- | --- |
| 2026-10-12 | 本会话（架构师） | v1.0 初稿 | `source_import_qmt.py` 模板面 | ②审计退回（sess_b7574342：2×P1/2×P2/1×P3） | M5.2 件8 微批 |
| 2026-10-12 | 本会话（架构师） | v1.1 修订：闭环上述 5 项（落点 3→4） | 同上 | ②复审退回（sess_fed0bb41：2×P1 确认闭环，新出 3×P2/4×P3） | M5.2 件8 微批 |
| 2026-10-12 | 本会话（架构师） | **v1.2 定点修订**：闭环复审 7 项（见 §0，修订面封闭、可定点 diff 核对） | §2/§3/§4/§6 | 候②定点复核 | M5.2 件8 微批 |

---

## 0. 审计项闭环台账（两轮合并）

**审计方**：zcode（两轮均只读静态审计，已声明无机检执行权）。
**本会话纪律**：对审计的每一项 P1/P2 均**独立机检复核**（框架源码直读 / 命令输出实证），不盲信、不照抄。

| 轮 | # | 审计项 | 本会话独立复核 | 处置 |
| --- | --- | --- | --- | --- |
| r1 | P1-a | `time` 依赖缺陷（产物头部仅 import pandas） | ✅ 成立：框架 `:1612` 唯一条 `import pandas as pd`；`:1166` 既有「局部 import 不依赖产物头部」纪律 | v1.1 §4.2 helper 内局部 import + `_qs_progress_start()`；§4.3 逐行定稿形态 |
| r1 | P1-b | valuation per-code `_qs_last_close` 覆盖缺口 | ✅ 成立：框架 `:1381 for code in codes:` + `:1398-1400` valuation 分支；`_qs_last_close`（:1128）内调 `get_market_data_ex` | v1.1 §1.3 表修正 + §3.1 落点 5 + §4.3(4) + §8 边界 4 |
| r1 | P2-c | 行号锚点混用（产物行号挂框架文件名） | ✅ 成立 | v1.1 §3.1 改框架行号为主 + 产物对照列 |
| r1 | P2-d | 哨兵行契约不自洽 | ✅ 成立 | v1.1 §4.1 七字段统一定稿 |
| r1 | P3-e | 清单缺 ISSUES 归属；缺 elapsed 判据 | ✅ 采纳 | v1.1 §3.1-7 + §6h |
| **r2** | **P2-①** | §4.3(3) 哨兵形态：异常路径 end 跳过（孤儿 begin）+ `t0=None` 致 elapsed 恒 0.0 | ✅ **成立**：`get_financial_data` 在 try 内（:1306-1317），异常走 `except Exception: return frame`（:1318-1319，**该 except 静默无打印**）→ end 被跳过；批失败与死锁不可区分，**恰击穿立项目标** | **v1.2 §4.3(3) 改 `finally` 配对 + `_t0` 真计时 + `err=` 尾键**；§4.1/§4.5/§6a 同步 |
| **r2** | **P2-②** | §3.1 条目 6 漏 `fundamentals-contract-matrix.md` | ✅ **成立**：本会话上轮 `--check` 实测输出「OK: 契约矩阵门禁通过（哈希一致 + **MD 一致**）」——reverify 连带写 md 且 `--check` 校验其一致性 | **v1.2 §3.1 条目 6 补 md** |
| **r2** | **P2-③** | §4.3 插入位次约定不自洽（(2) 注文与代码矛盾；(1)/(4) 两套约定） | ✅ 成立（作者笔误） | **v1.2 §4.3 钉死单一约定「重语句之后、分支语句之前」并逐点改注** |
| r2 | P3-④ | §3.2「纯新增、零删除」与 for 行 enumerate 改写矛盾 | ✅ 成立 | v1.2 §3.2 改措辞 |
| r2 | P3-⑤ | §4.3(4) 注「该循环对所有表执行」失实 | ✅ 成立：框架 `:1331-1379` hist 三表（eps/growth_ability/profit_ability）提前 `return`，`:1381` 循环仅非 hist 路径可达 | v1.2 §4.3(4) 改述 |
| r2 | P3-⑥ | §6i 须钉死 AST-only；§6h 须明确 elapsed 解析契约 | ✅ 采纳 | v1.2 §6 两格 |
| r2 | P3-⑦ | §2 知识反哺条目 2/3 格式不合机检契约 | ✅ 采纳 | v1.2 §2 三行统一为 `（<域>/<文件名>.md，YYYY-MM-DD 检索）` |

> 修订不触及用户⑤三项裁定（粒度 500 / `get_history` 族不覆盖 / D-24 单独立项）。

---

## 1. 问题定义

### 1.1 现象（用户实测，2026-10-12 16:34:05）

QMT 端 `QMT_WEEKLY_SMALLCAP` 回测启动后日志**仅两行**后长时间无输出：

```
【2026-10-12 16:34:05.081】  start back test mode
【2026-10-12 16:34:05.081】  QS_QMT_DENY_REMOVED: set_benchmark
```

用户判为「卡住」，无法区分引擎**在计算**还是**已死锁**。

### 1.2 根因（结构取证）

| 事实 | 位置（产物） |
| --- | --- |
| `init(C)` 打印后**仅剩 `g.initialized = True`**，无 I/O → `init` 必已走完 | :1621-1629 |
| 耗时全部落在 `handlebar` 首日 `_select_weekly_targets` 漏斗 | :1635-1657 |
| `QS_FUNNEL_AUDIT` 仅在**整个漏斗返回后**打印一行 | :1655 |
| 漏斗内**全程无中间输出** | :1514-1619（策略主体） |

→ 首个 bar 期间完全静默；D-23 修复后 L1 由 0 变 5227，下游首次全部激活，静默由「秒级」跃升「分钟级」。

### 1.3 首日真实工作量

| 层 | 调用形态 | 量级 | 框架位置 | 产物位置 |
| --- | --- | --- | --- | --- |
| L1 `get_stock_info` | **逐只** `get_instrument_detail` | 5227 | :1567-1574（循环 :1571） | :1323-1332 |
| L3 `filter_stock_by_status` | **逐只** `get_instrument_detail`（重复） | ≈4500 | :1059-1088（循环 :1077） | :814-838 |
| L0补全(a) | `get_financial_data` **单次批量** ×3（valuation/growth/eps） | 3 次 | :1291-1330（调用 :1315-1317） | :1040-1068 |
| L0补全(b) | **逐只** `_qs_last_close` → `get_market_data_ex`（valuation 分支） | **≈4500** | **:1381 循环 / :1398-1400 分支** | :1130-1156 |

> (b) 为第二长静默段，且是 r1 审计发现的覆盖缺口。

### 1.4 需求（可验收表述）

回测运行期间，取数层须周期性输出**可机读**的进度审计行，使「在计算」与「已死锁」可由日志**客观区分**；不得引入刷屏、不得改变任何既有返回值与行为。

---

## 2. 知识反哺检索留痕

**检索时间**：2026-10-12｜**主库**：`D:\miniQMT策略实盘\knowleage_base`｜**入域**：`07-经验库`、`01-策略`

```
知识反哺：[命中] 验收盲点-skipna因子合成缺列不可区分须审计行直证（07-经验库/验收盲点-skipna因子合成缺列不可区分须审计行直证.md，2026-10-12 检索）
知识反哺：[命中] 平台转换坑-必填参数空串未定义行为返回静默空（07-经验库/平台转换坑-必填参数空串未定义行为返回静默空.md，2026-10-12 检索）
知识反哺：[命中] 行情适配量化策略提示词（01-策略/行情适配量化策略提示词.md，2026-10-12 检索）
```

**采纳要点**：①结构化键值审计行=唯一可机检判据（「推断『有数据』必须有审计行实证」）→ D-24 行含 `done/total/elapsed` 数字直证；②单次大批量调用是静默黑洞 → 起止哨兵行**成对**（§4.3(3) 已按 `finally` 保证配对）;③既有纪律「避免循环高频打印」→ 固定步长 500 节流。

---

## 3. 改动范围

### 3.1 允许写入清单（＝实施轮 diff 范围核对判据；行号为**框架实测值**）

| # | 文件 | 落点（框架行号） | 产物对照 | 改动性质 |
| --- | --- | --- | --- | --- |
| 1 | `quantstudio/strategy_compiler/source_import_qmt.py` | `_QS_QMT_LOG_EXT`（:217-247）新增常量 + `_qs_progress_start()` + `_qs_progress()` | :26-56 | 纯新增 |
| 2 | 同上 | `_qs_filter_stock_by_status` 循环（:1077-1078 之间）插入 tick | :832-838 | 纯新增（for 头等价改写） |
| 3 | 同上 | `get_stock_info` 循环（:1571-1572）插入 tick + 循环前 t0 | :1327-1328 | 纯新增（for 头等价改写） |
| 4 | 同上 | `_qs_get_fundamentals` 批量调用（:1306-1319）前后 begin/end 哨兵（**finally 配对**） | :1055-1068 | 纯新增语句 |
| 5 | 同上 | `_qs_get_fundamentals` per-code 循环（:1381-1407）插入 tick + 循环前 t0 | :1130-1156 | 纯新增（for 头等价改写） |
| 6 | `docs/evidence/fundamentals-contract-matrix.yaml` **+ `docs/evidence/fundamentals-contract-matrix.md`** | `meta.qmt_wrapper_hash` 及其派生内容 | — | 哈希追认（同 commit，**两文件同批**） |
| 7 | `docs/ISSUES.md` | D-24 状态流转登记（open → fixed-pending-retest） | — | 簿记豁免白名单（显式声明归属） |
| 8 | `tests/test_qmt_progress_audit.py` | 新建单测 | — | 纯新增 |
| 9 | `docs/evidence/qmt-progress-audit-evidence.md` | 新建验收证据 | — | 纯新增 |

**不在清单内的任何文件改动即越界**（含策略源码、产物、其他模板常量、PTrade 侧 `source_import.py`）。

### 3.2 不可触碰面与「允许触碰的既有行」白名单（**v1.2 措辞修正**）

- **策略源码零改动**：`_select_weekly_targets` 等漏斗函数属**策略主体**（本地策略与转换产物），禁止编辑。本设计**不产出漏斗分层日志**（§8 能力边界）。
- 不改 `_qs_status_flags`（D-13 定谳逻辑零触碰）；不改任何既有审计行。

**允许触碰的既有行（穷举，仅两类；其余一律「纯新增、零删除、既有语句语义不得改动」）**：

| 类 | 内容 | 等价性 |
| --- | --- | --- |
| A | 三处 `for` 头改 `enumerate` 形态（:1077 / :1571 / :1381） | 迭代序/元素/次数零变（`list(...)` 均在循环前单次物化） |
| B | `_qs_get_fundamentals` 的 `except` 块内**新增** `_err = 1` 一行（:1318 区） | 原有 `return frame` 语句本体与语义零变 |

---

## 4. 设计

### 4.1 审计行契约（七字段；哨兵行亦带 done/total）

```
QS_QMT_PROGRESS func=<name> done=<n>/<total> pct=<p> elapsed=<s> phase=<tick|begin|end>[ extra=<k=v,...>]
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `func` | str | `get_stock_info` / `filter_stock_by_status` / `get_financial_data` / `query_last_close` |
| `done/total` | int/int | **复合字段**，渲染为 `done=<n>/<total>`（`total` 为分母；行内**无独立 `total=` 键**）：`begin` 恒 `0/N`，`end` 恒 `N/N` |
| `pct` | float(1) | `100.0*done/total` |
| `elapsed` | float(1) | 批次起算秒数，`t0` 有效时**恒非负且应收敛于真实耗时** |
| `phase` | enum | `tick` / `begin` / `end` |
| `extra` | str? | 可选尾键：`table=<t>`，哨兵行含 `err=0|1`（1=该次批量调用抛异常）；无则整段省略 |

### 4.2 helper（落 `_QS_QMT_LOG_EXT`，组装序首位 → 全模板可见）

```python
_QS_QMT_PROGRESS_EVERY = 500


def _qs_progress_start():
    """批次起算点。局部 import：产物头部仅 import pandas（框架 :1612），
    不得依赖模块级 time（既有纪律 :1166 同款）。失败返回 None。"""
    try:
        import time as _qs_t
        return _qs_t.time()
    except Exception:
        return None


def _qs_progress(func, done, total, t0=None, phase='tick', extra=None):
    """批次进度审计行（D-24）：仅 total >= 阈值时按固定步长打印，防刷屏。

    零行为影响：只 print，不改返回值/计数/顺序（判型＝新增检测型）。
    """
    if not total or total < _QS_QMT_PROGRESS_EVERY:
        return
    if phase == 'tick' and done % _QS_QMT_PROGRESS_EVERY and done != total:
        return
    elapsed = 0.0
    try:
        import time as _qs_t
        if t0 is not None:
            elapsed = _qs_t.time() - t0
    except Exception:
        elapsed = 0.0
    line = ('QS_QMT_PROGRESS func=%s done=%d/%d pct=%.1f elapsed=%.1fs phase=%s'
            % (func, done, total, 100.0 * done / float(total), elapsed, phase))
    if extra:
        line += ' extra=%s' % (extra,)
    print(line)
```

### 4.3 四个调用点（**逐行定稿形态**；统一插入位次约定＝「**重语句之后、分支语句之前**」）

**(1) `get_stock_info`（框架 :1567-1574）** — 重语句＝`out[s] = ...`（无分支）
```python
    lst = [stocks] if isinstance(stocks, str) else list(stocks or [])
    out = {}
    _t0 = _qs_progress_start()
    for _i, s in enumerate(lst):
        out[s] = _qs_get_stock_info(s, C=C)
        _qs_progress('get_stock_info', _i + 1, len(lst), _t0)
```

**(2) `_qs_filter_stock_by_status`（框架 :1077）** — 重语句＝`_qs_status_flags(...)`，其后即 `if not ok:` 分支
```python
    out = []
    _src = list(stocks)
    _t0 = _qs_progress_start()
    for _i, s in enumerate(_src):
        flags, ok = _qs_status_flags(s, C)
        _qs_progress('filter_stock_by_status', _i + 1, len(_src), _t0)
        if not ok:
            ...
```

**(3) `_qs_get_fundamentals` 批量调用起止哨兵（框架 :1306-1319）** — **v1.2 改为 `finally` 配对 + 真计时**
```python
    _n = len(codes)
    _t0 = _qs_progress_start()
    _qs_progress('get_financial_data', 0, _n, _t0, 'begin', 'table=%s' % (table,))
    _err = 0
    try:
        raw = C.get_financial_data(native_fields, codes, '19900101',
                                   str(date) if date else '20991231',
                                   report_type='announce_time')
    except Exception:
        _err = 1
        return frame                      # ← 原有语句，语义零变
    finally:
        _qs_progress('get_financial_data', _n, _n, _t0, 'end',
                     'table=%s err=%d' % (table, _err))
```
> 语义核验：`finally` 在 `return frame` 之前执行 → **配对恒成立**（无孤儿 begin）；
> `_err = 1` 为 except 块内新增语句，`return frame` 本体未改（异常仍静默返回空 frame，行为零漂移）；
> `_t0` 由 `_qs_progress_start()` 真实捕获 → end 行 elapsed 非 0（解 r2-P2-① 第二个缺陷）。

**(4) per-code 循环（框架 :1381-1407）** — 重语句＝`row = rows.get(code)`，其后即 `if not row:` 分支
```python
    rows = _qs_fin_to_rows(raw, shape=None, codes=codes)
    _t0 = _qs_progress_start()
    for _i, code in enumerate(codes):
        row = rows.get(code)
        _qs_progress('query_last_close', _i + 1, len(codes), _t0)
        if not row:
            continue  # 缺数保持 NaN 契约行
        ...
```
> **（v1.2 改述）** 该循环位于**非 hist 路径**——hist 三表（eps/growth_ability/profit_ability）
> 在 :1331-1379 提前 `return`，故当前映射表下**仅 valuation 路径可达**；进度行按 code 计数
> （工作推进指示，非 API 调用计数——见 §8 边界 4）。

### 4.4 节流设计（用户⑤裁定①）

固定步长 **500**；`total < 500` 一律不打印（`get_history` 族 ≤30 只天然静默——⑤裁定②边界）；收尾 `done == total` 恒打印。

### 4.5 预期日志形态（字段与 §4.1 契约一致）

```
QS_QMT_PROGRESS func=get_stock_info done=500/5227 pct=9.6 elapsed=2.1s phase=tick
QS_QMT_PROGRESS func=get_stock_info done=5227/5227 pct=100.0 elapsed=22.6s phase=tick
QS_QMT_PROGRESS func=filter_stock_by_status done=500/4503 pct=11.1 elapsed=1.9s phase=tick
QS_QMT_PROGRESS func=get_financial_data done=0/4503 pct=0.0 elapsed=0.0s phase=begin extra=table=valuation
QS_QMT_PROGRESS func=get_financial_data done=4503/4503 pct=100.0 elapsed=31.0s phase=end extra=table=valuation err=0
QS_QMT_PROGRESS func=query_last_close done=500/4503 pct=11.1 elapsed=4.4s phase=tick
...
QS_FUNNEL_AUDIT rebalance_id=... L0_all=5227 L1_listed=... R_selected=...
```
> `begin` 行 elapsed 恒 0.0（起点语义）；`end` 行为真实耗时。**配对判据**：每次 `begin` 必有同 `table` 的 `end`。

---

## 5. 影响面

| 维度 | 评估 |
| --- | --- |
| **返回值/计数** | **零影响**（§6b 黄金对照验证） |
| **调用顺序/次数** | **零影响**——不新增/不减少任何 QMT API 调用 |
| **分支/异常语义** | **零影响**——`finally` 只增打印；`except: return frame` 本体未改 |
| **产物** | 六策略产物字节数统一增加，增量应恒定可归因 |
| **性能** | 首日新增 ≈(5227+4503+4503)/500 ≈ 29 行 + 6 行哨兵；`time` 同频次，相对 ≈14000 次 API 调用可忽略 |
| **消费方** | 全部 6 策略（通用框架层修复） |
| **PTrade 面** | **零影响**——落点全在 `source_import_qmt.py` |

**影响面图**：本轮存在组件边界与变更影响面（4 落点 × 6 策略 × 取数链）→ 归 **实施轮 Plan 第⑧项子项**，按 `archify-gate` 执行（环境预检 → `workflow` 型 → finalize → visual-check）随该轮 Plan 送审。图须与**生效的**写入清单一致，故不在方案轮出图。

---

## 6. 验收标准

| # | 判据 | 复核方式 |
| --- | --- | --- |
| a | 契约成立：`tick` 在 500/1000/… 与 `done==total` 触发；非步长不触发；`total<500` 全静默；哨兵行七字段齐全且 `begin/end` 配对（含异常路径） | `pytest tests/test_qmt_progress_audit.py -q` |
| b | **黄金对照（零行为漂移）**：同 mock ContextInfo 下三函数返回对象与改动前**逐位一致**（含异常路径仍返回空 frame） | 单测内嵌对照断言 |
| c | 既有回归零衰减 | `pytest tests/test_source_import_qmt.py -q`（49 用例） |
| d | 六策略横验证全 PASS | `python scripts/qmt_reconvert_six.py` → `ALL 6 PASS (enc=gbk)` |
| e | 产物增量可归因（统一 Δ 字节） | 六份产物字节数对照表 |
| f | 哈希同批追认（yaml + md 两文件） | `python scripts/check_fund_matrix.py --check --reverify` → `--check` 绿 |
| g | 端测（用户执行）：首日出现 `QS_QMT_PROGRESS` 行且最终 `QS_FUNNEL_AUDIT` 正常输出 | QMT 日志 |
| h | **elapsed 有效性**：monkeypatch `_qs_progress_start` 返回过去时点 → 断言 `elapsed > 0`；**解析契约**为剥除尾缀 `s` 后 `float()`（格式 `%.1fs`），空/异常时回落 0.0 | 单测用例 |
| i | **无 time 依赖泄漏**：**AST-only** 机检——模块级无 `import time`，`time` 仅出现于两个 helper 函数体内（不用文本 grep，避免误伤注释/docstring 中的 "time" 字样） | 单测静态 AST 断言 |

---

## 7. 回退条件

| 层 | 手段 |
| --- | --- |
| ① 写前快照 | 四步配方（`git add -A` → `stash create` → `stash store` → `reset`），hash 记入实施轮申报 |
| ② 文件级备份 | `docs/evidence/_bak/source_import_qmt.py.bak-<ts>`（SHA256 双向核对） |
| ③ 触发条件 | ①任一既有测试转红；②返回值出现任何 diff；③六策略任一 portability FAIL；④产物增量不可归因；⑤§6h/§6i 失败 → **立即回退并停止** |

---

## 8. 判型声明与能力边界

**判型＝新增检测型**（新增检测能力开始报告真相，非既有行为漂移）：不改任何既有返回值、计数、调用顺序、分支语义与产物业务行为。

**能力边界（如实声明）**：

1. 只提供**取数层**进度，**不提供漏斗分层进度**（L1/L2/L3…）——漏斗逻辑在策略主体，受「策略源码零改动」铁律约束。
2. `get_history` 族（≤30 只）**不覆盖**（用户⑤裁定②）。
3. 本设计**不改善**性能（D-25 另起一轮，按纯性能型全套验收走，须附黄金对照）。
4. per-code `query_last_close` 进度行是**循环工作推进**指示，**非** API 调用计数指示（`rows.get` 为纯内存操作；真实 API 调用仅发生于 valuation 分支）。

> 端测判读：`QS_QMT_PROGRESS` 行指示取数推进 → 其后 `QS_FUNNEL_AUDIT` 的 `L1_listed/L3_status/...` 分层计数即等价于漏斗各层真实读数（数据侧直证）。

---

## 9. 非目标

- 不做漏斗内部分层日志（策略源码零改动）；
- 不做 D-25 重复取数合并（⑤裁定③：单独立项）；
- 不引入日志级别开关/环境变量（零配置，与既有 `QS_QMT_*` 审计行同族）；
- 不改任何既有审计行（`QS_QMT_SEMANTIC_DIFF`／`QS_QMT_UNDETERMINED`／`QSFUNDHIST`／`QS_FUNNEL_AUDIT` 零触碰）。
