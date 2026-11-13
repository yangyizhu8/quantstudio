# QMT 2.1.33.0 / Python 3.12 兼容性验证方案 **v2.0**（六步①·修订版）

> **状态**：**v2.0，待②审（第二轮）**
> **前版**：`qmt-py312-compat-verification-design.md`（v1.0，②审不通过，保留为审核链路证据，不删除）
> **裁定依据**：R-20261113-01＝甲；R-20261113-02＝独立环境、保全旧基线
> **本轮纪律**：**不下载、不安装、不切换解释器、不启动并存实例**

---

## 0. 修订对照（v1.0 → v2.0，逐条对应②审阻断项）

| # | ②审阻断 | v1.0 缺陷 | v2.0 修正位置 |
|---|---|---|---|
| M1 | 六项是 `hasattr` 存在性，不是 API 实调；NumPy 能力不能由 pandas 版本代判 | 只判存在性 | **§3.3**（存在性/最小调用分列 + numpy 独立） |
| M2 | 本机 3.11 不能称"3.12 乙态"，不计入基线验收；跑到 END ≠ 全分支验证 | 定性越界 | **§3.5**（降为旁证，明示不计入） |
| M3 | 矩阵补混合/导入失败/unknown；两 prefix 不能独证双解释器；DLL/原生/ABI 与宿主分开 | 矩阵不全 | **§3.2 七态矩阵** + **§3.4 ABI 分离取据** |
| M4 | "有 zoneinfo 便不得停 P1"撤回；成功即停，验时区/偏移/结果 | 判据倒置 | **§5.3 改结果导向** |
| M5 | 旧验收对原 manifest **始终有效**；新环境另验；不得写"换版才作废" | 作废表述错 | **§2.3 永久有效 + 另建** |
| M6 | 黄金输入补行情/合约/费用撮合；九处 EPS、86 周期按策略适用不复制 | 门槛泛化 | **§5.1 补三类** + **§5.2 标注专属** |
| M7 | "所列目录未发现 3.12"不外推全盘；空壳只证当前内容 | 结论外推 | **§6.1 限定观测面** |
| M8 | 清理 rc=1 与探针退出状态分开留证 | 混为一谈 | **§3.6 分开留证规范** |

**②审已直接答复（采纳，不再问）**：①`G0_BITS`=`struct.calcsize('P')*8` + `sys.maxsize` 交叉核对 → §3.3；②端口键名不臆设，未证资源隔离前不启动并存实例 → §6.3；③六策略先 CANSLIM 后五个，最终不缩减，耗时为估算 → §5.4。

---

## 1. 问题定义

**触发**：国金证券 2026-10-24《QMT测试环境版本升级通知》——测试环境客户端 2.1.33.0「已支持 Python 3.12」，并要求「务必在测试环境完成测试验证」。

| # | 待答问题 | 状态 |
|---|---|---|
| Q1 | 双版本共存可切换 / 默认切 3.12 / 只支持 3.12？ | 未知 |
| Q2 | pandas 与 numpy **各自**是否换版、换到哪版？ | 未知（**须独立探测**） |
| Q3 | 六产物在新环境行为是否一致？ | 未知，须固定输入实测 |
| Q4 | 现有 3.6.8 基线如何保全？ | §6 |

**明确排除**：不判断"升级好不好"；不做产物改造；不动 QMT 安装目录。

---

## 2. 事实基线

### 2.1 旧环境 manifest（`D:\QMT\国金QMT交易端模拟`）

| 项 | 实测值 |
|---|---|
| `bin.x64\python36.dll` | 4,170,240 B / 2022-12-22 |
| Python 版本 | **3.6.8**（`QS_QMT_TZ_PROBE` 中 `zoneinfo=N` 反证 <3.9） |
| `pandas\__init__.py` | 5,466 B / 2023-02-08 08:58，0.22.0 |
| `numpy\__init__.py` | 11,555 B / 2023-02-08 08:58，`short_version='1.19.1'`（cp36 ABI） |
| `pytz\__init__.py` | 34,206 B / 2023-02-08 09:00，2018.3 |
| 不兼容残留（已备份未删） | `numpy-2.4.4.dist-info` / `pytz-2026.1.post1.dist-info` |

### 2.2 六产物完整 SHA256（基线锚）

| 产物 | 字节 | SHA256（全长） |
|---|---|---|
| `CANSLIM突破成长选股策略.py` | 961,433 | `bcc1b90df7675f24ed09ed444a71648a1a732b0471f4e1b774950468bbd8ed18` |
| `fall_reversal.py` | 947,943 | `495e9ca3fb325519f0b6d767f0cb32cb3ca7325cb22fad01f7dbf0620c49604b` |
| `tech_etf_mvo.py` | 955,744 | `2e43b5445c3261f22e42a351327260e33ca0bdbc0c26f99b44b5299ee8ffa734` |
| `vol_regime_mom_rev.py` | 963,523 | `182f1005da48553017929bce3c7a83348bdb3fa7d2c17d5b9b963ce434337781` |
| `weekly_smallcap.py` | 957,649 | `8ac5cbffbdc35b2b394a9dc474f533402571ae95a94ac08ff87ee0376cb52396` |
| `周频三层.py` | 962,031 | `67a87cb761e594af315163c617749b92ad4a2e2b7cef3cbdbd2da1dcce37456e` |

### 2.3 旧验收的效力（**M5 修正**）

> **对原 manifest（§2.1 实体）的验收结论，永久有效。**

| 判据 | 3.6.8 实测 | 适用面 |
|---|---|---|
| G-6 `overridden>0` | 9/9（57/43/22/15/13/40/15/10/11） | **CANSLIM 专属** |
| G-7 五计数含 `code_miss` | 9/9，全为 0 | CANSLIM 专属 |
| 守恒等式 | 9/9 | CANSLIM 专属 |
| `QS_QMT_TZ_PROBE` | `pytz=Y zoneinfo=N resolve=P1-Y tzcache=5 level=P1` | CANSLIM 专属 |
| 双三态 | QFQ `n=5385 base=20261030` / EPS `n=5206 base=20260801 mode=zlib` | CANSLIM 专属 |
| 本地回归 | 266 passed | 框架层，环境无关 |

**新环境处置**：旧结论作为**旧环境历史证据**继续存在，不因新环境被否定；新环境**另建独立基线**（新 manifest + 新读数），与旧基线**并列存档**。两份是独立证据，非"新覆旧"。仅在**同一 manifest + 同一输入**下结论自相矛盾时才追查。

---

## 3. 环境判别：G-0 v4 探针

### 3.1 为什么一行版本不够

| 盲区 | 缺陷 |
|---|---|
| 双解释器切换 | `sys.version` 只报当前进程，不说明是否有另一套可切 |
| 位数/ABI | 同版本不同位数致 `.pyd` 加载失败，版本号看不出 |
| 加载路径 | 版本对但加载另一套 `site-packages`（用户级/venv 遮蔽） |
| 包真实版本 | `__version__` 可能经 versioneer 计算，须三方对照 |
| 旧 API 状态 | **存在 ≠ 可用**，须最小调用验证 |

### 3.2 判别矩阵（**M3：七态**）

| 态 | 触发条件 | 含义 | 处置 |
|---|---|---|---|
| **S0 未升级** | `G0_VI`=3.6.x | 基态 | 挂起 |
| **S1 甲态** | `G0_VI`≥3.12 且 pandas/numpy 旧调用**成功** | 环境升级、包未换版 | 六产物直跑 |
| **S2 乙态** | `has_DataFrame_append`=False 或 `call_df_append` 失败，且 `G0_MOD_PANDAS`≥2.0 | pandas 换版 | §5 全量验证 |
| **S3 混合** | pandas 换版但 numpy 未换（或反之） | 依赖不匹配，高风险 | **优先告警**，逐包定性 |
| **S4 导入失败** | `G0_MOD_*` 任一 `ERR` | 环境损坏/缺依赖 | 修环境，不评估产物 |
| **S5 路径污染** | `G0_MOD_*.file` 指向非 QMT 目录 | 加载了别处的包 | 先修路径 |
| **S6 unknown** | 均不匹配或缺关键字段 | 信息不足 | **fail-closed：不判定、不实施** |

### 3.3 G-0 v4 探针（**M1：存在性与最小调用分列；numpy 独立**）

```python
# ===== QS-QMT G-0 v4 =====
# ① hasattr 与 最小调用 分列 —— 存在不等于可用
# ② numpy 独立探测，不由 pandas 版本代判
# ③ 每项独立 try，互不影响
import sys, struct

def _p(fn, tag):
    try:
        print(tag, fn())
    except Exception as e:
        print(tag, 'ERR', type(e).__name__, str(e)[:100])

print('G0_V', 4)
# ---- 宿主 ----
_p(lambda: sys.version.replace('\n', ' '), 'G0_SYS_VERSION')
_p(lambda: sys.version_info[:3], 'G0_VI')
_p(lambda: struct.calcsize('P') * 8, 'G0_BITS')          # ②审指定
_p(lambda: sys.maxsize > 2**32, 'G0_IS64')
_p(lambda: (struct.calcsize('P') * 8 == (64 if sys.maxsize > 2**32 else 32)), 'G0_BITS_XCHECK')
_p(lambda: sys.executable, 'G0_EXECUTABLE')
_p(lambda: sys.prefix, 'G0_PREFIX')
_p(lambda: sys.base_prefix, 'G0_BASE_PREFIX')
_p(lambda: sys.path[:8], 'G0_SYSPATH')

# ---- 包实体 ----
for _n in ('numpy', 'pandas', 'pytz'):
    def _mod(_name=_n):
        __import__(_name)
        m = sys.modules[_name]
        return (getattr(m, '__version__', 'no-attr'),
                getattr(m, '__file__', 'no-file'))
    _p(_mod, 'G0_MOD_%s' % _n.upper())

# ---- ABI / 原生扩展（与宿主分离，M3）----
def _abi():
    out = []
    for _tgt, _stmt in (('numpy_native', 'import numpy.core._multiarray_umath as m'),
                        ('pandas_tslib', 'import pandas._libs.tslib as m')):
        try:
            _ns = {}
            exec(_stmt, _ns)
            out.append((_tgt, getattr(_ns['m'], '__file__', '?')))
        except Exception as e:
            out.append((_tgt, 'ERR:%s' % type(e).__name__))
    return out
_p(_abi, 'G0_ABI_NATIVE')

# ---- 存在性 vs 最小调用（M1 核心）----
def _apis():
    r = []
    try:
        import pandas as pd
        r.append(('pd_exists_append', hasattr(pd.DataFrame, 'append')))
        try:
            u = pd.DataFrame({'a': [1]}).append(pd.DataFrame({'a': [2]}))
            r.append(('pd_call_append', 'OK len=%d' % len(u)))
        except Exception as e:
            r.append(('pd_call_append', 'FAIL:%s' % type(e).__name__))
        r.append(('pd_exists_ix', hasattr(pd.DataFrame, 'ix')))
        try:
            _d = pd.DataFrame({'a': [1, 2], 'b': [3, 4]})
            r.append(('pd_call_ix', 'OK val=%s' % _d.ix[0, 'a']))
        except Exception as e:
            r.append(('pd_call_ix', 'FAIL:%s' % type(e).__name__))
        r.append(('pd_exists_Panel', hasattr(pd, 'Panel')))
        try:
            r.append(('pd_call_Panel', 'OK %r' % (pd.Panel,)))
        except Exception as e:
            r.append(('pd_call_Panel', 'FAIL:%s' % type(e).__name__))
    except Exception as e:
        r.append(('pandas_import', 'ERR:%s' % type(e).__name__))

    try:
        import numpy as np
        r.append(('np_exists_float_', hasattr(np, 'float_')))
        try:
            r.append(('np_call_float_', 'OK %s' % np.float_(1.5)))
        except Exception as e:
            r.append(('np_call_float_', 'FAIL:%s' % type(e).__name__))
        r.append(('np_exists_bool8', hasattr(np, 'bool8')))
        try:
            r.append(('np_call_bool8', 'OK %s' % np.bool8(True)))
        except Exception as e:
            r.append(('np_call_bool8', 'FAIL:%s' % type(e).__name__))
        r.append(('np_exists_NaN', hasattr(np, 'NaN')))
        try:
            r.append(('np_call_NaN', 'OK %r' % (np.NaN,)))
        except Exception as e:
            r.append(('np_call_NaN', 'FAIL:%s' % type(e).__name__))
    except Exception as e:
        r.append(('numpy_import', 'ERR:%s' % type(e).__name__))
    return r

for _i, _x in enumerate(_apis()):
    print('G0_API_%02d' % _i, _x)

# ---- 时区现状（只记录，不设"必须改层级"的判据，M4）----
def _tz():
    z = pt = 'N'
    try:
        import zoneinfo  # noqa
        z = 'Y'
    except Exception:
        pass
    try:
        import pytz
        pytz.timezone('Asia/Shanghai')
        pt = 'Y'
    except Exception:
        pass
    return 'zoneinfo=%s pytz=%s' % (z, pt)
_p(_tz, 'G0_TZ')

print('G0_V4_END')
```

### 3.4 双解释器支持的正确取据（**M3**）

| 证据 | 能否单独证明 | 说明 |
|---|---|---|
| 两个 `prefix` | **❌ 不能** | venv/`sys.path` 操纵即可不同，不等于客户端支持切换 |
| `G0_EXECUTABLE` | ❌ 不能 | 只说明当前进程由谁启动 |
| 安装目录 `python31*.dll` 清单 | ✅ 必要条件 | 多个版本 DLL ⇒ 有能力 |
| 原生扩展 `.pyd` 的 cp 标签 | ✅ 必要条件 | ABI 证据，与宿主版本**分开**记录 |
| `G0_ABI_NATIVE` 两类扩展加载成功 | ✅ 佐证 | 加载成功说明 ABI 匹配 |
| 客户端解释器选择配置项 | ✅ 直接证据 | **须安装后实测查明，不臆设** |

> **判"支持双解释器"须同时满足**：安装目录存在 ≥2 个版本 `python3*.dll` **且** 存在 ≥2 套 site-packages **且**（若可切换）切换后 `G0_ABI_NATIVE` 仍成功。**仅凭 prefix 差异不判。**

### 3.5 本机 3.11 样本的定位（**M2：撤回原定性**）

v1.0 曾据本机 Python 3.11.9 + pandas 2.3.3 的运行结果称"乙态样本、判别能力得证"。**该定性撤回**：

| 问题 | 说明 |
|---|---|
| 不是 3.12 | 本机是 3.11.9，不满足 S1/S2 的"≥3.12" |
| 不是 QMT 环境 | CPython 官方安装 + 用户 site-packages，与 QMT 客户端无关 |
| 一次跑完 ≠ 全分支 | 运行至 `G0_V4_END` 只证明主路径可执行；`ERR` 分支、异常分支、包缺失分支**均未被触发** |

**正确定位**：该次运行仅作**探针脚本语法与主路径旁证**（`py_compile` rc=0 + 主路径输出完整），
**不计入**"QMT 3.6.8 基线实跑"验收，**不计入**任何"3.12 兼容性"判据。
探针真实效力须在 **QMT 客户端进程内**取得，且须覆盖正常路径 **+ 至少一个人为注入的 ERR 分支**。

#### 3.5.1 ★「一次跑到 END ≠ 全分支验证」的活证据（2026-11-13 实测）

本方案 G-0 v4 脚本**首次实跑即抓到自身 bug**，恰好实证②审 M2 的批评：

```text
首次运行输出（已修正前）：
  G0_V 4
  <function <lambda> at 0x0000020F3E64B7E0> ERR TypeError 'str' object is not callable
  ...（连续 13 行同类 ERR）
  G0_API_00 ('pd_exists_append', False)      ← 这部分正常
  ...
  G0_V4_END                                   ← ★END 标记照样打印了
  [PROBE_EXIT] 0                              ← ★退出码照样是 0
```

**根因**：`_p(tag, fn)` 的签名与调用 `_p(lambda_fn, 'TAG')` 的参数顺序相反
⇒ `tag` 收到函数对象、`fn` 收到字符串 ⇒ `fn()` 报 `'str' object is not callable`。

**为什么这是活证据**：

| 观测 | 若只看 END 与退出码 | 真相 |
|---|---|---|
| `G0_V4_END` 已打印 | 误判"探针跑通" | 13 项探测全部失败 |
| `[PROBE_EXIT] 0` | 误判"无异常" | 每个 `_p` 内部 `try` 吞掉了异常，进程正常退出 |
| 只有逐行读 `ERR` 才暴露 | — | **必须逐项核验输出，不能只看结束标记** |

⇒ 这直接支持②审结论：**「一次运行到 END 不等于全部条件分支均已验证」**。
本方案据此在 §8 验收判据①中强制要求：探针须覆盖正常路径 **+ 至少一个人为注入的 ERR 分支**，且**逐项核对输出**，不以 END 标记或退出码作为通过依据。

**修正与复验**：将签名改为 `_p(fn, tag)` 后重跑，全部 25 项输出正常：

```text
G0_V 4
G0_SYS_VERSION 3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]
G0_VI (3, 11, 9)          G0_BITS 64          G0_IS64 True
G0_BITS_XCHECK True                              ← struct.calcsize('P')*8 与 sys.maxsize 交叉核对一致
G0_EXECUTABLE ...\Python311\python.exe
G0_PREFIX / G0_BASE_PREFIX 均 ...\Python311
G0_MOD_NUMPY  ('2.4.6', '...\\numpy\\__init__.py')
G0_MOD_PANDAS ('2.3.3', '...\\pandas\\__init__.py')
G0_MOD_PYTZ   ('2026.2', '...\\pytz\\__init__.py')
G0_ABI_NATIVE [('numpy_native', '...\\numpy\\core\\_multiarray_umath.py'),
               ('pandas_tslib',  '...\\pandas\\_libs\\tslib.cp311-win_amd64.pyd')]
G0_API_00 ('pd_exists_append', False)   G0_API_01 ('pd_call_append', 'FAIL:AttributeError')
G0_API_02 ('pd_exists_ix',     False)   G0_API_03 ('pd_call_ix',     'FAIL:AttributeError')
G0_API_04 ('pd_exists_Panel',  False)   G0_API_05 ('pd_call_Panel',  'FAIL:AttributeError')
G0_API_06 ('np_exists_float_', False)   G0_API_07 ('np_call_float_', 'FAIL:AttributeError')
G0_API_08 ('np_exists_bool8',  False)   G0_API_09 ('np_call_bool8',  'FAIL:AttributeError')
G0_API_10 ('np_exists_NaN',    False)   G0_API_11 ('np_call_NaN',    'FAIL:AttributeError')
G0_TZ zoneinfo=Y pytz=Y
G0_V4_END
[PROBE_EXIT] 0      [CLEANUP_EXIT] 0
```

**这轮复验证明了三件事（但**仍不计入** 3.12 / QMT 验收，见下）**：

1. **存在性与最小调用已成功分列**：`G0_API_00/01` 成对、`02/03` 成对……每对是同一 API 的
   「有没有」与「能不能用」两个独立观测，不再用 `hasattr` 代替实调（M1 达成）。
2. **NumPy 已独立探测**：`G0_API_06`–`11` 全部来自 numpy 自身调用，与 pandas 版本无耦合（M1 达成）。
3. **ABI 证据已与宿主分离**：`G0_ABI_NATIVE` 输出 `tslib.cp311-win_amd64.pyd`——**cp311 标签与宿主 3.11 一致**，
   这正是 §3.4 要求「DLL/原生扩展路径及 ABI 证据与宿主 `sys.executable` 分开」的落地形态（M3 达成）。

**★仍然不计入任何验收（M2 纪律）**：
- 本机是 **Python 3.11.9**，不是 3.12 ⇒ 不满足 S1/S2 的"≥3.12"
- 不是 QMT 客户端进程 ⇒ 与 `python36.dll` 环境无关
- 未覆盖人为注入的 ERR 分支（本次暴露的 ERR 是**意外**发生，非主动注入验证）


### 3.6 退出状态留证规范（**M8**）

```text
[PROBE_EXIT] <code>              ← 探针进程本身退出码
[PROBE_END_MARKER] seen|missing  ← 是否打印到 G0_V4_END
[CLEANUP_EXIT] <code> <cmd>      ← 外围清理命令退出码，独立记录
```

**判据**：以 `[PROBE_END_MARKER] seen` + `[PROBE_EXIT]` 为准；`[CLEANUP_EXIT]` 非 0 **不影响**探针结论，但须独立留证，禁止合并归因。

---

## 4. 分支处置

| 态 | 处置 |
|---|---|
| S0 | 无动作，挂起 |
| S1 | 六产物直跑，按 §5 验证 |
| S2 | 六产物全量验证 + 黄金逐行比对 |
| S3 | **优先告警**，逐包定性（版本 + ABI），定性后再按 S1/S2 处置 |
| S4 | 修环境，不评估产物 |
| S5 | 先修路径，回 §3 重探 |
| S6 | **fail-closed：不判定、不实施** |

---

## 5. 验证设计

### 5.1 输入固定清单（**M6：补三类**）

| 类别 | 固定项 | 采集 |
|---|---|---|
| 产物 | §2.2 六产物，SHA256 **逐位等于**表值 | 跑前先算并比对 |
| **行情数据** | 数据源版本 / 采集日期 / 覆盖区间 / 复权口径 | 记录 `source_watermark` 与 QFQ `base` |
| **合约信息** | 合约表快照日期、涨跌停/停牌规则版本 | 记录 instrument snapshot 指纹 |
| **费用与撮合** | 佣金率、印花税、过户费、滑点、T+1、成交价模式 | 从配置读取并**原文记录** |
| 回测参数 | 区间 2026-06-01→2026-09-30、起始资金、策略参数组 | 与 3.6.8 历史轮完全一致 |
| 观测点 | `TZ_PROBE`/`QFQ_SNAP`/`EPS_SNAP`/`EPS_ALIGN`/`EPS_SPAN_SHORT`/`QS_REBALANCE_AUDIT`/`QS_PORTFOLIO_AUDIT`/`STOPDBG` | 按策略适用性取用 |

### 5.2 六策略清单（**M6：专属判据不复制**）

| # | 产物 | 专属强判据 | 通用判据 |
|---|---|---|---|
| 1 | CANSLIM | **9 处 `EPS_ALIGN`**（`overridden`/`span_short` 逐处等值、`code_miss` 全 0、守恒逐处成立）+ **86 周期**审计逐行等值 | 无 Traceback；时区结果正确 |
| 2 | fall_reversal | **待首次运行实测确定**（v1.0 曾误把 CANSLIM 门槛当共同门槛，已撤回） | 同上 |
| 3 | tech_etf_mvo | 待实测（ETF 池，重点索引对齐） | 同上 |
| 4 | vol_regime_mom_rev | 待实测 | 同上 |
| 5 | weekly_smallcap | 待实测 | 同上 |
| 6 | 周频三层 | 待实测（`STOPDBG` 行数最多） | 同上 |

> **纪律**：专属判据须**该策略首次运行时实测采集**，采集后写入该策略独立基线作为后续锚，**禁止从 CANSLIM 复制**。

### 5.3 三类对照（**M4：时区改结果导向**）

| 类别 | **正确判据** |
|---|---|
| **时区** | ✅ **只验结果**：`pytz.timezone('Asia/Shanghai')` 解析出的**实际 UTC 偏移**是否为 `+08:00`；同一 `datetime` 经产物链路转换后的**日期时间值**与 3.6.8 是否一致。**"成功即停"原则得到尊重**——垫片停在哪级由它自己决定，只要结果对 |
| **数值** | 同一 `(code, end_d8)` 的 EPS、同一周期 `cash_ratio`/`positions`/持仓明细**逐位一致** |
| **黄金** | 以**同策略**在 3.6.8 下的完整审计序列为基准（**各策略各自基准，不共用**）；任一行差异即失败 |

> **纪律**：差异**一律判失败并回退**，不按"数据修正型"放行。

### 5.4 排期（采纳②审）

```text
第一批：CANSLIM（探路）→ 验证探针与流程
第二批：其余五个批量
最终：六策略全部完成，不缩减（耗时为估算，非承诺）
```

---

## 6. 安装勘察（R-02 待授权，本轮不执行）

### 6.1 已观测情况（**M7：限定观测面**）

> **观测面声明**：以下仅覆盖所列路径，**不构成"全盘不存在 3.12"的断言**。

| 路径 | python dll | 观测到的事实 |
|---|---|---|
| `D:\QMT\国金QMT交易端模拟` | `python36.dll` | 当前在用基线 |
| `D:\QMT\国金QMT交易端模拟2` | `python36.dll` | 第二套 |
| `D:\国金证券QMT交易端` | `python36.dll` | 第三套 |
| `C:\国金证券QMT交易端` | 未发现 dll | 内容不完整 |
| `D:\QMT\gjzqqmt_ceshi` | 无 | **只证明当前内容为空壳**（外层 1 / 内层 0），不代表该目录用途或未来内容 |

> 若要断言"本机无 3.12"，须以**全盘按 `python312.dll` 无过滤搜索**为准；本方案不作该断言。

### 6.2 共享资源

| 资源 | 已观测 | 风险 |
|---|---|---|
| 端口 | `0.0.0.0:58600` 被 `...\国金QMT交易端模拟\bin.x64\XtItClient.exe`（PID 28132）占用 | ⚠️ 高 |
| userdata | 四处均实体目录，各自独立 | ✅ 低 |
| 系统级 DLL | 未勘察 | ⚠️ 待装时观察 |

### 6.3 安装原则（采纳②审，不臆设键名）

```text
① 独立目录、不覆盖、不自动更新（R-02）
② 端口与配置项：不预设键名，列为安装计划必须实测查明项
③ ★未证明资源隔离前，不启动并存实例★
④ 安装前记录现有目录完整清单（文件数 + 总字节）作为回退锚
```

---

## 7. 回退预案

| 场景 | 动作 |
|---|---|
| 探针报错 | 不影响产物；按 §3.6 分开留证后排查 |
| 验证失败 | 产物未改动，无需回退；记录失败证据 |
| 后续立项改造 | 铁律 03 写前快照 + 另存 §2.2 六产物原件 |
| 测试环境装坏 | 独立目录前提下不影响现有模拟端 |

---

## 8. 验收判据（本方案自身）

```text
① 探针 v4 在 QMT 客户端进程内完整运行覆盖正常路径 + 至少一个人为注入的 ERR 分支，
   两者分别留证（v1.0 的"本机3.11跑通"不算）
② §3.2 七态矩阵无未定义组合（含 S3/S4/S6）
③ §3.4 双解释器判据以 DLL + ABI 为主证，prefix 不作主证
④ §2.2 六产物完整 SHA256 可机检
⑤ §5.1 六类输入固定项齐备；§5.3 时区判据为结果导向
⑥ §5.2 各策略专属判据均实测采集，无跨策略复制
⑦ 过②审（第二轮）
```

**回退条件**：②审再判「判据不可执行 / 分支有遗漏 / 证据不足」⇒ 退回重写。

---

## 9. 判型声明

| 审核意见 | 落地 |
|---|---|
| 3.12 不必然意味着 pandas 2.x | §3.2 S1/S2 由**最小调用结果**分流；S3 专列混合态 |
| 环境升级不自动属数据修正型 | §5.3 差异一律判失败并回退 |
| 静态零命中仅前瞻 | §1 只作背景，不作通过依据 |
| ISS-20261112-01 保持 OPEN | 新 Python 满足 metadata 下限 ≠ 实体/ABI/依赖一致；须实测 `G0_MOD_*` 三方一致后才可议关单 |

---

## 10. 知识反哺（铁律 21 / 22 R1）

```text
知识反哺：[命中] QD-2026-0023（QD/qmt，2026-11-13）
  §F-2「三态通过 ≠ 覆盖生效」⇒ 探针须取真实进程内证据。
知识反哺：[命中] QD-2026-0022（QD/qmt，2026-11-13）
  时区分级垫片 P0–P4 ⇒ **撤回**"强迫层级上移"的错误判据，改为验 UTC 偏移与转换结果。
知识反哺：[无命中] knowledge/qmt、docs/evidence 中无「大版本升级/运行时切换」专门条目。
本轮沉淀：N/A（方案草案，待②审通过 + 真机证据齐备后再沉淀）
```

---

## 11. 待②审确认（v2.0 新增 2 项）

1. **§3.3 最小调用的副作用**：`df1.append(df2)` 在 pandas 0.22 下产生 `FutureWarning`，在 2.x 抛 `AttributeError`。探针是否需 `warnings.catch_warnings` 抑制，以免污染 QMT 日志？
2. **§3.5 ERR 分支人为注入**：建议用 `sys.modules['numpy'] = None` 或临时改 `sys.path` 制造导入失败以验证 ERR 分支。是否接受该注入方式（仅探针自测，不触产物）？
