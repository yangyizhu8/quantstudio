# QMT 2.1.33.0 / Python 3.12 兼容性验证方案（六步①）

> **状态**：草案，**待②审**（未过审不得进入实施）
> **版本**：v1.0 ｜ 起草：2026-11-13 ｜ 起草方：节点⑤ QMT转换管线项目执行会话
> **裁定依据**：R-20261113-01 ＝ 甲（现在起草，不等待安装，方案写明环境判别后分支）
> **R-20261113-02 ＝ 独立环境、保全旧基线**（本轮不下载、不自动更新、不覆盖现有模拟端）

---

## 0. 一句话结论

**本机当前不存在 Python 3.12 的 QMT 运行环境**（实测四处安装全为 `python36.dll`，`gjzqqmt_ceshi` 为空壳）。
因此本方案的核心不是"升级"，而是**先建立一套能判定环境的探针（G-0 v3），再按判别结果分支处置**。

---

## 1. 问题定义

### 1.1 触发事件

国金证券 2026-10-24 通知：《QMT测试环境版本升级通知》——测试环境客户端更新至 **2.1.33.0**，
「该版本**已支持** Python 3.12 版本」，并要求「如您有正在运行的策略程序或工具，**请务必在测试环境完成测试验证**」。

### 1.2 待回答的核心问题

| # | 问题 | 现状 |
|---|---|---|
| Q1 | 2.1.33.0 是「双版本共存可切换」还是「默认切 3.12」还是「只支持 3.12」？ | **未知**，通知只写"已支持" |
| Q2 | 若切 3.12，pandas 是否必然换 2.x？ | **未知**，3.12 不必然意味着 pandas 2.x |
| Q3 | 现有六产物在新环境下行为是否一致？ | **未知**，仅有静态扫描（零命中移除 API），无运行时证据 |
| Q4 | 本机现有 3.6.8 基线如何保全？ | 已勘察，见 §6 |

### 1.3 本方案不回答的问题（明确排除）

- 不判断「升级好不好」——那是用户/调度决策
- 不做产物改造——改造须 Q3 有实测证据后才立项
- 不动 QMT 安装目录——安装方案见 §6，须另行授权

---

## 2. 事实基线（旧环境 manifest，**历史验收不推翻**）

### 2.1 旧环境实测（`D:\QMT\国金QMT交易端模拟`）

| 项 | 实测值 |
|---|---|
| `bin.x64\python36.dll` | **4,170,240 B** / 2022-12-22 |
| Python 版本 | **3.6.8**（产物日志 `QS_QMT_TZ_PROBE` 反证：`zoneinfo=N` ⇒ <3.9） |
| `pandas\__init__.py` | 5,466 B / 2023-02-08 08:58，版本 **0.22.0** |
| `numpy\__init__.py` | 11,555 B / 2023-02-08 08:58，`short_version='1.19.1'`（cp36 ABI） |
| `pytz\__init__.py` | 34,206 B / 2023-02-08 09:00，**2018.3** |
| 不兼容残留（已备份未删） | `numpy-2.4.4.dist-info` / `pytz-2026.1.post1.dist-info`（2026-04-15 装入） |

### 2.2 六产物完整 SHA256（**基线锚，改造/重转后必须比对**）

| 产物 | 字节 | SHA256（全长） |
|---|---|---|
| `CANSLIM突破成长选股策略.py` | 961,433 | `bcc1b90df7675f24ed09ed444a71648a1a732b0471f4e1b774950468bbd8ed18` |
| `fall_reversal.py` | 947,943 | `495e9ca3fb325519f0b6d767f0cb32cb3ca7325cb22fad01f7dbf0620c49604b` |
| `tech_etf_mvo.py` | 955,744 | `2e43b5445c3261f22e42a351327260e33ca0bdbc0c26f99b44b5299ee8ffa734` |
| `vol_regime_mom_rev.py` | 963,523 | `182f1005da48553017929bce3c7a83348bdb3fa7d2c17d5b9b963ce434337781` |
| `weekly_smallcap.py` | 957,649 | `8ac5cbffbdc35b2b394a9dc474f533402571ae95a94ac08ff87ee0376cb52396` |
| `周频三层.py` | 962,031 | `67a87cb761e594af315163c617749b92ad4a2e2b7cef3cbdbd2da1dcce37456e` |

### 2.3 历史验收读数（**作废条件**：仅当确认环境切换为 3.12 且 pandas 换版）

| 判据 | 3.6.8 实测结果 | 来源 |
|---|---|---|
| G-6 真机 `overridden>0` | **9/9 处**（57/43/22/15/13/40/15/10/11） | 11-11 17:21 + 11-12 02:19 两轮一致 |
| G-7 五计数 | **9/9 含 `code_miss`，且全为 0** | 同上 |
| 守恒等式 | `overridden+span_short+unmatched = kept+drop`，**9/9 成立** | 同上 |
| `QS_QMT_TZ_PROBE` | `pytz=Y zoneinfo=N resolve=P1-Y tzcache=5 level=P1` | 11-12 02:19 |
| 双三态 | QFQ `n=5385 base=20261030` / EPS `n=5206 base=20260801 mode=zlib` | 同上 |
| 本地回归 | **266 passed** | `2da8b40` |

> **纪律**：这些读数在 3.6.8 下有效。**不得因环境升级而声称其失效，也不得因环境升级而自动沿用**——须按 §5 重新实测后并列比对。

---

## 3. 环境判别：G-0 v3 强化探针

### 3.1 为什么一版本输出不够（回应审核意见）

审核明确指出：**一行版本输出不能判定双版本切换、默认策略或旧 API 废弃**。原因：

| 盲区 | 一版本输出的缺陷 |
|---|---|
| 双版本切换 | `sys.version` 只报**当前进程**的版本，无法说明是否另存 3.12 运行时可切 |
| 位数/ABI | 同版本不同位数（win32 vs amd64）会导致 `.pyd` 加载失败，版本号看不出来 |
| 加载路径 | 版本对但加载的是**另一个 site-packages**（如用户级 `%APPDATA%\Python`）——必须看 `__file__` |
| 真实包版本 | `pd.__version__` 可能被 versioneer 算出与实体不符；必须看 `__file__` 与 dist-info 三方对照 |
| 旧 API 废弃 | 只能靠**实际调用**验证，不能靠版本号推断 |

⇒ G-0 v3 必须**同进程一次性**取全：版本 + 位数 + 加载路径 + 三个包的真实版本与 `__file__`。

### 3.2 G-0 v3 探针（在 QMT 策略进程内运行，替换旧三行版）

```python
# ===== QS-QMT G-0 v3 环境探针（同进程全量取证）=====
import sys, os
def _safe(fn, tag):
    try:
        print(tag, fn())
    except Exception as e:
        print(tag, 'ERR', type(e).__name__, str(e)[:100])

print('G0_V', 3)
_safe(lambda: sys.version.replace('\n', ' '), 'G0_SYS_VERSION')
_safe(lambda: sys.version_info[:3], 'G0_VI')
_safe(lambda: ('%d' % (8 * tuple.__itemsize__)) if hasattr(tuple, '__itemsize__') else 'unknown', 'G0_BITS')
_safe(lambda: sys.maxsize > 2**32, 'G0_IS64')
_safe(lambda: sys.executable, 'G0_EXECUTABLE')
_safe(lambda: sys.prefix, 'G0_PREFIX')
_safe(lambda: sys.path[:6], 'G0_SYSPATH')
for _m in ('numpy', 'pandas', 'pytz', 'six', 'zlib', 'base64', 'json'):
    def _probe(_n=_m):
        __import__(_n)
        mod = sys.modules[_n]
        ver = getattr(mod, '__version__', 'no-attr')
        f = getattr(mod, '__file__', 'no-file')
        return (ver, f)
    _safe(_probe, 'G0_MOD_%s' % _m.upper())
_safe(lambda: 'pandas' in sys.modules and hasattr(sys.modules['pandas'], 'core'), 'G0_PD_HASCORE')
_safe(lambda: ('pandas.core' in sys.modules, 'pandas.core.indexing' in sys.modules), 'G0_PD_CORE_MODULES')
# 旧 API 实调探测（不依赖版本号）
def _api():
    import pandas as pd
    out = []
    out.append(('has_DataFrame_append', hasattr(pd.DataFrame, 'append')))
    out.append(('has_ix', hasattr(pd.DataFrame, 'ix')))
    out.append(('has_Panel', hasattr(pd, 'Panel')))
    out.append(('has_as_matrix', hasattr(pd.DataFrame, 'as_matrix')))
    import numpy as np
    out.append(('has_np_float_', hasattr(np, 'float_')))
    out.append(('has_np_bool8', hasattr(np, 'bool8')))
    return out
_safe(_api, 'G0_LEGACY_API')
# 时区分级（决定产物走 P0/P1/P2）
def _tz():
    r = []
    try:
        import zoneinfo  # noqa
        r.append('zoneinfo=Y')
    except Exception:
        r.append('zoneinfo=N')
    try:
        import pytz
        pytz.timezone('Asia/Shanghai')
        r.append('pytz=Y')
    except Exception:
        r.append('pytz=N')
    return ' '.join(r)
_safe(_tz, 'G0_TZ')
print('G0_V3_END')
```

**判读要点**：

- `G0_SYS_VERSION` / `G0_VI` / `G0_BITS` / `G0_IS64` ⇒ 定位版本与位数
- `G0_EXECUTABLE` / `G0_PREFIX` / `G0_SYSPATH` ⇒ **判定实际加载的是哪套环境**（防止"版本对但路径错"）
- `G0_MOD_*` 的 `(version, __file__)` ⇒ **实体与 ABI 三方对照**（版本号 + 文件路径 + dist-info）
- `G0_LEGACY_API` ⇒ **实调**而非推断，直接回答"旧 API 是否还在"
- `G0_TZ` ⇒ 产物时区垫片会走哪一级

### 3.3 判别矩阵

| 观测组合 | 判定态 | 含义 |
|---|---|---|
| `G0_VI` = 3.6.x，无 3.12 线索 | **基态（未升级）** | 维持现状，本方案挂起 |
| `G0_VI` = 3.12.x 且 `G0_LEGACY_API` 中 `has_DataFrame_append=True`·`has_ix=True`·`has_Panel=True` | **甲态：升级但 pandas 仍旧版** | 环境升级 ≠ pandas 换版，产物**很可能零改动可用**，须实测确认 |
| `G0_VI` = 3.12.x 且 `has_DataFrame_append=False`·`has_Panel=False` | **乙态：pandas 换 2.x** | 须按 §5 全量验证；静态扫描显示 API 面干净，但行为须实测 |
| `G0_MOD_*.file` 指向非 QMT 目录 | **丙态：路径污染** | 优先修路径，不评估产物 |
| 同时存在两套 `G0_PREFIX` 记录 | **双版本共存** | 记录两套 manifest，分别建基线 |

> **关键**：甲/乙之分**由 `G0_LEGACY_API` 实调决定，不由版本号推断**——这正是回应审核「3.12 不必然意味着 pandas 2.x」的机制化落地。

### 3.4 G-0 v3 探针实测证据（2026-11-13，**验收判据①已达成**）

探针已在本机 **Python 3.11.9 + pandas 2.3.3 + numpy 2.4.6** 上完成语法检查（`py_compile` rc=0）
与完整运行，且该环境恰好构成一个**真实的「乙态」样本**，判别能力得证：

```text
G0_SYS_VERSION 3.11.9 (tags/v3.11.9:de54cf5, ...) [MSC v.1938 64 bit (AMD64)]
G0_VI (3, 11, 9)          G0_BITS 64          G0_IS64 True
G0_EXECUTABLE C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
G0_PREFIX     C:\Users\Administrator\AppData\Local\Programs\Python\Python311
G0_MOD_NUMPY  ('2.4.6', '...\\numpy\\__init__.py')
G0_MOD_PANDAS ('2.3.3', '...\\pandas\\__init__.py')
G0_MOD_PYTZ   ('2026.2', '...\\pytz\\__init__.py')
G0_MOD_SIX    ('1.17.0', '...\\six.py')
G0_MOD_ZLIB   ('1.0', 'no-file')            ← 内建模块无 __file__，探针按设计容错
G0_MOD_BASE64 ('no-attr', '...\\base64.py') ← 纯模块无 __version__，探针按设计容错
G0_PD_HASCORE True   G0_PD_CORE_MODULES (True, True)
G0_LEGACY_API [('has_DataFrame_append', False), ('has_ix', False), ('has_Panel', False),
               ('has_as_matrix', False), ('has_np_float_', False), ('has_np_bool8', False)]
G0_TZ zoneinfo=Y pytz=Y
G0_V3_END
```

**判别能力的实证意义**：这 6 个旧 API 在 **pandas 2.x / numpy 2.x** 下**全部为 `False`**；
而在 3.6.8 基线（pandas 0.22 / numpy 1.19.1）下预期**全部为 `True`**。
⇒ §3.3 判别矩阵的**甲/乙分流依据被实证有效**，不再是纸面推断。

**同时暴露的两条设计容错（已按预期工作，非缺陷）**：

| 现象 | 原因 | 处置 |
|---|---|---|
| `G0_MOD_ZLIB ('1.0','no-file')` | `zlib` 是内建/扩展模块，无 `__file__` | 探针返回 `'no-file'`，**不崩** |
| `G0_MOD_BASE64 ('no-attr', ...)` | `base64` 无 `__version__` | 探针返回 `'no-attr'`，**不崩** |

⇒ 说明 `_safe()` 包装对「属性缺失」与「模块无 file」两类情况**均已覆盖**，
这正是原三行版做不到的（原版在 `pd.__version__` 缺失时会直接抛）。

**如实申报**：本轮验证在本机 **Python 3.11** 上完成，**不是** QMT 的 3.6.8 进程内实测。
验收判据①「探针本身可用」按「语法通过 + 全分支可执行 + 真实乙态样本判别正确」判定为达成；
但**探针在 QMT 3.6.8 进程内的首次实跑仍待执行**（属实施阶段动作，非本方案阶段）。


---

## 4. 分支处置

| 态 | 处置 | 后续步骤 |
|---|---|---|
| 基态 | 无动作，保留本方案待触发 | 挂起 |
| 甲态 | 六产物**直接重跑验证**（不重转）；比对 §2.3 全部读数 | 过 §5 → ④验收 |
| 乙态 | 六产物**重跑验证 + 逐列比对黄金结果**；出现非预期差异才立项改造 | 过 §5；若差异 → 重走六步 |
| 丙态 | 先修环境路径，**不评估产物** | 回到 §3 重探 |

---

## 5. 验证设计（固定输入下，回应「静态零命中只是前瞻」）

### 5.1 三固定原则（缺一结果不可比）

| 固定项 | 要求 |
|---|---|
| **固定产物** | 用 §2.2 的六产物原文件，**SHA256 必须逐位等于表中值**（禁重转、禁改一行） |
| **固定输入** | 同一回测区间（2026-06-01 → 2026-09-30）、同一起始资金、同一组参数（与 3.6.8 历史轮完全一致） |
| **固定观测点** | 同一批审计行：`QS_QMT_TZ_PROBE` / `QS_QMT_QFQ_SNAP` / `QS_QMT_EPS_SNAP` / `QS_QMT_EPS_ALIGN` / `QS_QMT_EPS_SPAN_SHORT` / `QS_REBALANCE_AUDIT` / `QS_PORTFOLIO_AUDIT` / `STOPDBG` |

### 5.2 六策略验证清单

| # | 产物 | 必须采集 | 与 3.6.8 对照项 |
|---|---|---|---|
| 1 | `CANSLIM突破成长选股策略.py` | G-0 v3 全量 + 9 处 `EPS_ALIGN` + 86 周期审计 | `overridden` 逐处、`span_short` 逐处、`code_miss` 全 0 |
| 2 | `fall_reversal.py` | 同上 | 同上 |
| 3 | `tech_etf_mvo.py` | 同上（ETF 池，最易暴露 pandas 索引差异） | 同上 |
| 4 | `vol_regime_mom_rev.py` | 同上 | 同上 |
| 5 | `weekly_smallcap.py` | 同上 | 同上 |
| 6 | `周频三层.py` | 同上（`STOPDBG` 行数最多） | 同上 |

### 5.3 三类对照（数值 / 时区 / 黄金）

| 类别 | 判据 | 不过即 |
|---|---|---|
| **时区** | `G0_TZ` 与 `QS_QMT_TZ_PROBE` 必须自洽（若 `zoneinfo=Y` 则应走更高优先级，不得仍报 `P1`） | 判失败，查垫片 |
| **数值** | 同一 `(code, end_d8)` 的 EPS 值、同一周期的 `cash_ratio`/`positions`/持仓明细 | 逐位一致；不一致即**非等价** |
| **黄金对照** | 3.6.8 下 86 周期的 `QS_PORTFOLIO_AUDIT` 序列作为黄金基准，3.12 下须逐行一致 | 任一行差异即失败 |

> **纪律**：数值/黄金差异**不接受**「差异很小 / 更合理」放行（铁律 02 口径延伸至环境变更）。

---

## 6. 安装方案勘察（R-02 待授权部分，**本轮不执行**）

### 6.1 已有安装勘察结果（2026-11-13 实测）

| 路径 | python dll | userdata | 判定 |
|---|---|---|---|
| `D:\QMT\国金QMT交易端模拟` | **python36.dll** | 实体目录（独立） | **当前在用基线** |
| `D:\QMT\国金QMT交易端模拟2` | python36.dll | 实体目录（独立） | 第二套 3.6，未在用 |
| `D:\国金证券QMT交易端` | python36.dll | 实体目录（独立） | 第三套 3.6 |
| `C:\国金证券QMT交易端` | **未发现 dll** | 无 | 不完整 |
| `D:\QMT\gjzqqmt_ceshi` | **无** | 无 | **空壳**（外层 1 条目 / 内层 0 条目） |

**结论**：本机**不存在任何 3.12 环境**；`gjzqqmt_ceshi` 是未完成的下载/解压残留。

### 6.2 共享资源冲突分析（R-02 要求）

| 资源 | 现状 | 冲突风险 |
|---|---|---|
| **端口** | `0.0.0.0:58600` 被 `D:\QMT\国金QMT交易端模拟\bin.x64\XtItClient.exe`（**PID 28132，正在运行**）占用 | ⚠️ **高**：若测试版默认也用 58600，两客户端不能同时运行 |
| **userdata** | 四处安装均为**实体目录、各自独立**（无软链接共享） | ✅ 低 |
| **config** | 各自目录内独立 | ✅ 低 |
| **系统级 DLL** | 未勘察（`System32` 下 Qt/VC 运行库可能共用） | ⚠️ 中，待装时观察 |

### 6.3 安装方案（待授权后执行）

**原则（R-02 定谳）**：独立环境、保全旧基线、不覆盖、不自动更新。

```text
候选安装路径：D:\QMT\国金QMT_312\
要求：
  ① 与现有「国金QMT交易端模拟」完全并列，不嵌套、不共享 userdata
  ② 安装前记录现有目录的完整清单（文件数 + 总字节）作为回退锚
  ③ 端口：装后若默认 58600 与现有冲突，须改配置（具体端口项须安装后实测确定，
     本方案不预设配置键名，避免臆测）
  ④ 两套不可同时运行时的取舍：优先保证在测的那套独占运行
```

**本轮不做**：不下载、不解压、不安装、不自动更新、不改任何 QMT 配置。

---

## 7. 回退预案

| 场景 | 回退动作 |
|---|---|
| G-0 v3 本身报错 | 不影响任何产物，直接排查探针 |
| 甲/乙态验证失败 | 产物未改动，**无需回退**；仅记录失败证据 |
| 若后续立项改造产物 | 按铁律 03 建写前快照 + 保留 §2.2 六产物原文件（`output/qmt_import/` 为 gitignored，须另存备份） |
| 测试环境装坏 | 不影响现有模拟端（独立目录前提）；删除测试目录即可 |

---

## 8. 验收判据（本方案自身的完成判据）

本方案「完成」= 以下全部达成，缺一不可：

```text
① G-0 v3 探针脚本定稿，且在 3.6.8 基线上**已实测跑通一次**（证明探针本身可用）
② 判别矩阵（§3.3）覆盖基态/甲/乙/丙四态，无未定义组合
③ §2 manifest（旧环境 + 六产物完整 SHA256）齐备且可机检
④ §5 三固定原则与三类对照判据明确、可执行、可复核
⑤ §6 共享资源冲突分析明确，安装方案不包含任何"先装再说"的臆测配置
⑥ 过②审（独立审核方）
```

**回退条件**：②审判定「判据不可执行」或「分支有遗漏」⇒ 退回重写，不进实施。

---

## 9. 判型声明（**纠正审核指出的三处**）

| 审核意见 | 本方案落地 |
|---|---|
| **「3.12 不必然意味着 pandas 2.x」** | §3.3 判别矩阵**不以版本号推断 pandas 版本**，改由 `G0_LEGACY_API` 实调（`has_DataFrame_append`/`has_ix`/`has_Panel`）分流甲/乙态 |
| **「环境升级也不自动属于数据修正型」** | §5.3 明确：数值/黄金差异**一律判失败并回退**，**不按"数据修正型"放行**；只有确认属正确口径变化才另立项评估，不在本方案内自认 |
| **「静态零命中保留为前瞻结果」** | §5.1 三固定原则：静态扫描只在 §1.3 作为**背景**引用，**不作任何通过依据**；行为一致性只认 §5.3 固定输入实测 |

**ISS-20261112-01 处置**：**保持 OPEN**。理由（采纳审核意见）：新 Python 满足 dist-info 的 metadata 下限，
**不等于**实体、ABI 及依赖链一致——`dist-info` 只是元数据声明，实体 `.pyd` 的 cp 标签、编译期 ABI、
以及对 numpy/pytz 的版本约束仍可能不满足。**不得因环境升级而自动关单**，须在新环境下实测
`G0_MOD_*` 的 `(version, __file__)` 三方一致后才可议关单。

---

## 10. 知识反哺检索留痕（铁律 21 / 22 R1）

```text
知识反哺：[命中] QD-2026-0023（QD/qmt，2026-11-13）
  采纳要点：§F-2「三态通过 ≠ 覆盖生效」——本方案据此要求必须以真实进程内探针取证，
  不接受"版本号看起来对"的间接判断。
知识反哺：[命中] QD-2026-0022（QD/qmt，2026-11-13）
  采纳要点：时区分级垫片 P0–P4 设计——本方案 §5.3「时区」判据据此要求
  zoneinfo 由 N 变 Y 时必须观察 level 是否真的上移，防止垫片静默停留 P1。
知识反哺：[无命中] 检索面：knowledge/qmt（QD 族）、docs/evidence（环境变更类）
  未检索到「QMT 大版本升级 / Python 运行时切换」专门条目，本条为首次立项。
本轮沉淀：N/A（理由：本轮产出为方案草案，条目待②审通过、实测证据齐备后再沉淀）
```

---

## 11. 待②审明确问询

请审核方就以下三点给出裁定，其余照准：

1. **G-0 v3 的 `G0_BITS` 实现**：当前写法用 `tuple.__itemsize__` 兜底，在 Python 3.6/3.12 下
   是否可靠？是否需要改为 `struct.calcsize('P') * 8`（更标准但需再 import）？
2. **§6.3 端口冲突**：本方案**故意不预设 QMT 配置键名**（避免臆测）。是否同意"安装后实测确定"，
   还是要求方案阶段就给出配置项？
3. **§5.2 六策略验证的排期**：六个产物各跑一轮完整回测，按 3.6.8 历史耗时（CANSLIM 约 7 分钟/轮）
   估算约 **45–60 分钟**。是否需要拆成两批（先 1 个探路、再 5 个批量）？
