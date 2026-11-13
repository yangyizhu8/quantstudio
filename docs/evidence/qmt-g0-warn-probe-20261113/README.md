# R387 离线补测原始取证（2026-11-13）

> 范围：R386 ②审授权的**已准离线范围**。不进客户端、不下载、不安装、不切换解释器。
> 环境：本机 Python 3.11.9 / pandas 2.3.3 / numpy 2.4.6（隔离临时副本内运行）。
> 未修改 QMT 宿主、未改宿主 sys.modules / sys.path。

## 文件

| 文件 | 内容 | 指纹 |
|---|---|---|
| `raw_output.txt` | 脚本完整 stdout（37 行 / 1803 B） | sha256 `c748f2c8b256fb5d70887df5c4cadbadbd3f4b7a8ffcff771bbb714d2f6aa689` |
| `fp.json` | 指纹清单（脚本内采集） | 见文件 |

## 三点结论

### ① WARN 注入（四项判据全达成）

```text
WARN_FILTERS_BEFORE len=5
WARN_INJECT OK [WARN:FutureWarning:DEPRECATED_API_X will be removed] val=42
WARN_AFTER_ITEM marker-following-item-still-runs
WARN_AFTER_PROBE OK val='still-alive'
WARN_FILTERS_AFTER  len=5
WARN_FILTERS_RESTORED True
WARN_GLOBAL_LEAK False
WARN_OUTSIDE_CATCH count=1 cat=FutureWarning
```

| 判据 | 结果 |
|---|---|
| 记录**类别 + 消息** | ✅ `FutureWarning:DEPRECATED_API_X will be removed` |
| `OK[WARN:...]` 形态 | ✅ 正确呈现，value 仍可用（`val=42`） |
| **后续项继续** | ✅ 注入项之后两项均正常执行 |
| **warnings 过滤恢复** | ✅ `len` 5→5、`RESTORED True`、`GLOBAL_LEAK False` |
| 附加：未被全局吞掉 | ✅ `catch_warnings` 外仍可观测到 1 条 FutureWarning |

### ② 「属性不存在」vs「调用内部 AttributeError」已可机读区分

```text
CASE_ABSENT_pd_append    ABSENT      attr=append  msg=type object 'DataFrame' has no attribute 'append'
CASE_ABSENT_pd_Panel     ABSENT      attr=Panel   msg=module 'pandas' has no attribute 'Panel'
CASE_ABSENT_np_float_    ABSENT      attr=float_  msg=`np.float_` was removed in the NumPy 2.0 release...
CASE_INTERNAL_fake       CALL_INTERNAL_ATTRIBUTE_ERROR  msg='object' object has no attribute 'no_such_attr_deep'
CASE_OK_pd_head          OK          val=(1, 1)
```

| 形态 | 定性 | 能否判 PASS |
|---|---|---|
| `ABSENT` | 属性层即不存在 ⇒ **能力读数** | ✅ 可按矩阵计入 |
| `CALL_INTERNAL_ATTRIBUTE_ERROR` | 属性存在、**调用内部**抛 ⇒ **不能当"API 不存在"** | ❌ 须单独定性 |
| `OK` / `OK[WARN]` | 可用 | ✅ |
| `FAIL` / `ATTR_ERR` | 意外 | ❌ |

⇒ 三层分类（属性层 / 调用层 / 结果层）已实现，v2.1 中以 `ABSENT:` 单一前缀混装两类 AttributeError 的缺陷已消除。

### ③ 「包装目录无 .pyd」≠「全包无原生扩展」（证实②审判断正确）

```text
NUMPY_ROOT  ...\site-packages\numpy
NUMPY_PYD_TOTAL 19
  _core\_multiarray_umath.cp311-win_amd64.pyd      ← 真正位置在 _core（2.x 改名），不是 core
  _core\_simd.cp311-win_amd64.pyd
  fft\_pocketfft_umath.cp311-win_amd64.pyd
  linalg\_umath_linalg.cp311-win_amd64.pyd
  ...
WRAPPER_DIR_HAS_PYD False        ← numpy/core（旧名目录）确实无 .pyd
```

**结论**：numpy 全包**确有 19 个原生扩展**，全部为 `cp311-win_amd64`；`numpy/core/` 无 `.pyd` 只说明**该目录**是纯 Python 包装（2.x 把二进制移到 `numpy/_core/`）。
⇒ v2.0「`numpy_native_pyd=[]` 故 numpy 无原生扩展」的推断**是错的**，②审批评成立。v2.2 起改为**全包扫描**并单列「包装目录」与「原生扩展目录」两个观测面。

## ★本轮自身缺陷（如实登记）

`NUMPY_PYD_CP_TAGS ['?']` —— 我的 cp 标签解析语句 `p.split('.')[-3]` 对
`_multiarray_umath.cp311-win_amd64.pyd` 取段位置算错，**解析分支有缺陷**（扫描本身正确，19 个已列出）。
⇒ 已在 v2.2 修正为按文件名后缀解析（`rsplit('.', 2)[-2]`），并在下方补正输出中复验。


## 补正（R387 ②审指出的证据缺口，2026-11-13）

上文"自身缺陷"一节称"已改 rsplit 并复验"，但该复验当时仅存在于会话输出、未落文件；
本 README 也提"下方补正"而无对应内容。现补齐既有修正原件与指纹：

| 文件 | 内容 | 指纹（sha256） |
|---|---|---|
| `pyd_scan_fixed.py` | cp 标签修正版扫描脚本（`f.rsplit('.', 2)[-2]`；扫描逻辑与首版相同，仅解析取段修正） | `46e1b36281c53e3053f34950fa10c9860fbbbf4d7dca0fc5e90d5c1415dc8731` |
| `raw_cptag_fixed.txt` | 修正版完整原始输出（可复跑 `python pyd_scan_fixed.py` 对拍） | `18ac19bcf016c9a47b4ff95c66ce543242a1cbf1a48764c0f33c2e6102699962`（1844 B） |

读数：`NUMPY_PYD_TOTAL 19`；`NUMPY_PYD_CP_TAGS ['cp311-win_amd64']`；`UNIQUE_TAGS 1`；
`ALL_SAME_TAG True`；环境 `PY 3.11.9 BITS 64`，`NUMPY 2.4.6 @ ...\site-packages\numpy`。

**范围声明**：本读数属本机 Python 3.11 环境的离线取证，**不构成** QMT 宿主、Python 3.12
或实际 ABI 加载的验收依据；`ABSENT` 是否可接受还取决于目标产物是否实际依赖该 API。
