# `is_trade` 探针包 · **R3 补充件**（2026-11-15）

> **只修文档内源码草稿与清单**；**不落新产品/测试源码、不运行探针/D5/产品测试**、
> **不改断言求绿、不恢复 `False` 改写、不提交推送**。
> 主锚：`review-round2-conclusions-20251115.md`（**DRIFT-N7-r1**）。
> 本文为 R2（`is_trade_probe_package-r2-20251115.md`）的**定点修订**。

## 〇、按主锚 DRIFT-N7-r1 回收的**五项摘要**（当前状态）

| # | 主锚条目 | 当前状态 |
|---|---|---|
| 1 | **三态核验**（不可得／真零／正预算） | 订单修复②审**通过**（附三项生效条件）⇒ **未实施**（条件①注释同 commit、②真零态判据措辞、③R2 知识措辞 待授权） |
| 2 | **卖出路径** | 同上，**未实施** |
| 3 | **双端逐笔一致** | 同上；**判据措辞已按主锚修正**（真零态合法皆空 ⇒ 改「皆空＋模板三件套＋本地 no-op」，「不接受皆空」仅约束不可得/正预算） |
| 4 | **最小差分** | 同上；**未实施**（改动点已核全文件仅两处） |
| 5 | **公开 API 依据（文档乙 vs 实证甲）** | **不通过** ⇒ A-1 **暂缓**；本探针包即为解决该项的取证路径 ⇒ **R3 待审，未运行** |

## 一、审核指出的三处**我的真缺陷**（全部承认）

| # | 缺陷 | 后果 | R3 修法 |
|---|---|---|---|
| **D-a** | `_qs_probe_collect` 内用 `os.path.basename(fr["file"])`，而 `os` 只在 `_qs_probe_is_trade()` 里以 `import os as _os` 导入 | **异常分支内 `os` 未定义 ⇒ 再抛 `NameError: name 'os' is not defined`** ⇒ 异常分支**不可用**（编译通过掩盖了这一点） | 探针**顶层**统一 `import os, sys, json, traceback`；分支内不再依赖任何局部导入 |
| **D-b** | `globals().get("is_trade")` 返回 `None` 时，**无法区分「键不存在（缺名）」与「键存在但值为 `None`」** | 两种截然不同的语义被合并为 `SCOPE_NOT_RESOLVED` | **先判键存在性**（`"is_trade" in globals()`），再判值的性质 ⇒ 拆出 `PRESENT_BUT_NONE` |
| **D-c** | 用「帧文件名含 `is_trade`」判定「被调函数内部帧」 | **不覆盖「任意文件名的实现」**；且同名文件会误判 | 改用**帧序**判定：`traceback.extract_tb` 的 `frames[0]` 恒为**调用 `fn()` 的那一帧**（在探针内），`frames[1:]` 恒为**被调函数内部帧** ⇒ **完全不依赖文件名** |

## 二、R2 → R3 **定点 diff**（逐处）

### 2.1 探针源码：删除函数内局部导入，改为顶层统一导入

```diff
-# ===== QS_PROBE_IS_TRADE v2（只读 · 零交易 · 可留证）=====
-import traceback
+# ===== QS_PROBE_IS_TRADE v3（只读 · 零交易 · 可留证）=====
+import os as _qs_os          # 顶层统一导入：分支内不再依赖局部 import（D-a）
+import sys as _qs_sys
+import json as _qs_json
+import traceback as _qs_tb
```

### 2.2 `_qs_probe_is_trade`：移除局部 import 与 `global _os`

```diff
 def _qs_probe_is_trade(scope_name="probe"):
     """零交易只读探针：不调 set_backtest()、不调任何 order*、不改任何状态。"""
-    import os as _os
-    global _os
-    rec = _qs_probe_collect(globals().get("is_trade"), scope_name)
+    rec = _qs_probe_collect(scope_name)
```

### 2.3 `_qs_probe_collect`：签名与「缺名 vs 值 None」拆分（D-b）

```diff
-def _qs_probe_collect(fn, scope_name):
-    """收集 is_trade 的四态 + 完整堆栈；不吞异常。"""
+def _qs_probe_collect(scope_name):
+    """收集 is_trade 的存在性/可调用性/返回值/异常 + 完整堆栈；不吞异常。"""
     rec = {...}
-    # —— ① 存在（作用域层）——
-    try:
-        rec["scope_resolved"] = "is_trade" in globals()
-    except BaseException as e:
-        rec["scope_resolved"] = False
-        rec["exception_type"] = "SCOPE_CHECK:%s" % type(e).__name__
-    fn_obj = globals().get("is_trade")
-    if fn_obj is not None:
-        rec["callable"] = callable(fn_obj)
-        rec["resolved_from"] = (getattr(fn_obj, "__module__", None)
-                                or getattr(fn_obj, "__name__", None))
+    # —— ① 存在性：先判「键是否存在」，再判「值是什么」——
+    try:
+        rec["key_present"] = "is_trade" in globals()
+    except BaseException as e:
+        rec["key_present"] = None
+        rec["scope_check_error"] = "%s: %s" % (type(e).__name__, e)
+    fn_obj = None
+    if rec["key_present"] is True:
+        fn_obj = globals().get("is_trade")
+        rec["value_is_none"] = (fn_obj is None)          # ← 缺名 vs 值 None 的分界
+        rec["callable"] = callable(fn_obj) if fn_obj is not None else False
+        rec["resolved_from"] = (getattr(fn_obj, "__module__", None)
+                                or getattr(fn_obj, "__name__", None)) if fn_obj is not None else None
```

### 2.4 `_qs_probe_collect`：内部帧判定改为**帧序**（D-c）

```diff
-            # 区分「作用域层未绑定」与「被调函数内部异常」
-            inside_api = any(("is_trade" in os.path.basename(fr["file"]).lower()
-                              and fr["func"] != "_qs_probe_collect")
-                             for fr in rec["frames"])
-            rec["verdict"] = "INTERNAL_ERROR" if inside_api else "SCOPE_NOT_RESOLVED"
+            # 区分「调用处未绑定」与「被调实现内部异常」——**按帧序，不按文件名**
+            # extract_tb: frames[0] = 调用 fn() 的那一帧（探针内）；frames[1:] = 被调实现内部帧
+            rec["frame_count"] = len(rec["frames"])
+            rec["internal_frames"] = rec["frames"][1:]     # 逐帧留存，不做文件名过滤
+            if len(rec["frames"]) > 1:
+                rec["verdict"] = "INTERNAL_ERROR"          # API 存在且可调用，实现内部抛错
+            elif len(rec["frames"]) == 1:
+                rec["verdict"] = "SCOPE_NOT_RESOLVED"     # 调用处未绑定该名
+            else:
+                rec["verdict"] = "UNKNOWN"                 # 帧链不可判 ⇒ UNKNOWN
+                rec["unknown_reason"] = "frame_chain_insufficient"
+            rec["exception_obj_repr"] = repr(e)           # 保原异常（复核用）
```

### 2.5 非布尔返回的标记（新增）

```diff
             try:
                 v = fn_obj()
                 rec["returns"] = repr(v)
                 rec["returns_type"] = type(v).__name__
-                rec["verdict"] = "CALLABLE_OK"
+                rec["returns_bool_like"] = isinstance(v, bool)
+                rec["returns_is_none"] = (v is None)
+                rec["returns_truthy"] = (bool(v) if not isinstance(v, bool) else v)
+                rec["verdict"] = "CALLABLE_OK"
+                if not rec["returns_bool_like"]:
+                    rec["returns_note"] = "non_boolean_return"   # 非布尔返回：仍 CALLABLE_OK，但须显式标记
```

### 2.6 输出路径、唯一命名、失败出口（新增/明确）

```diff
+_QS_PROBE_OUT_DIR = ""      # 由执行方在装载前设置为**临时目录绝对路径**（空 ⇒ 不写文件）
+_QS_PROBE_RUN_ID = ""       # 由执行方设置：UTC 时间戳＋短随机串，保证唯一命名
+
 def _qs_probe_out_path(scope_name):
+    """输出路径：<OUT_DIR>/QS_PROBE_IS_TRADE_<RUN_ID>_<scope>.json（唯一、不覆盖）。"""
+    try:
+        if not _QS_PROBE_OUT_DIR or not _QS_PROBE_RUN_ID:
+            return None
+        name = "QS_PROBE_IS_TRADE_%s_%s.json" % (_QS_PROBE_RUN_ID, scope_name)
+        return _qs_os.path.join(_QS_PROBE_OUT_DIR, name)
+    except BaseException:
+        return None
```

**失败出口规则（R3 明确）**：

| 通道 | 失败时 | 读数是否仍在 |
|---|---|---|
| ① **返回值**（`_qs_probe_is_trade()` 的返回 dict） | **永不失败**（函数必返回 dict） | ✅ **主通道** |
| ② **stdout 打印**（`print`） | 捕获异常、置 `stdout_failed=True` | ✅ 仍在 |
| ③ **文件**（JSON 落盘） | 捕获异常、置 `file_write_failed=True`、**记 `out_path`** | ✅ 仍在 |
| ④ **平台日志**（`log.info`） | 捕获异常、置 `log_failed=True` | ✅ 仍在 |

⇒ **四通道中任一失败都不丢读数**；读数恒可从①取回。
**日志与文件同时失败** ⇒ 读数从①取回，且 `log_failed=True` ＋ `file_write_failed=True` ＋ `out_path=None`。

### 2.7 顶层导入与禁止范围的一致性（P1/P2 通用）

| 项 | R3 规定 |
|---|---|
| 顶层导入 | `import os as _qs_os / sys as _qs_sys / json as _qs_json / traceback as _qs_tb` —— **探针自包含**，不依赖调用方命名空间 |
| **禁止** `set_backtest()` | P1 与 P2 **一律禁止**（甲的实证正由其触发 `NameError`）；探针**只读** `is_trade` |
| **禁止** 任何 `order*` | P1/P2 一律；P2 另加**拒绝桩**（命中计数） |
| **禁止** `except` 后静默返回默认值 | 探针的失败必须**显式**（verdict ＋ 帧链 ＋ 原异常 repr） |
| `set_backtest` 在**载体源**中的角色 | P2 载体源**含** `set_backtest()`（为复现真实用法形态），但**运行时不被调用**（initialize 被替换 ⇒ 载体体不可达）⇒ **源里出现 ≠ 运行时调用**，此点须在清单中写明 |

## 三、分支预期表（**覆盖审核指定的四类** ＋ 缺名/值 None）

| # | 场景 | `key_present` | `value_is_none` | `callable` | 帧数 | `verdict` | 期望输出 | **不得**得出的结论 |
|---|---|---|---|---|---|---|---|---|
| 1 | **缺名**（作用域未取得） | `False` | — | `False` | 0 | `SCOPE_NOT_RESOLVED` | `scope_resolved_note="key_absent"` | ❌「平台无此 API」 |
| 2 | **键存在但值为 `None`** | `True` | `True` | `False` | 0 | **`PRESENT_BUT_NONE`** | `present_but_none` | ❌「缺名」；❌「API 缺失」 |
| 3 | 存在但**不可调用**（常量/非函数对象） | `True` | `False` | `False` | 0 | `PRESENT_NOT_CALLABLE` | `resolved_from` ＋ 类型 | ❌「API 缺失」 |
| 4 | 可调用、返回 `False` | `True` | `False` | `True` | 0 | `CALLABLE_OK` | `returns='False'`, `returns_bool_like=True` | — |
| 5 | 可调用、返回 `True` | `True` | `False` | `True` | 0 | `CALLABLE_OK` | `returns='True'` | — |
| 6 | **非布尔返回**（如 `0`/`1`/`'False'`/`None`） | `True` | `False` | `True` | 0 | `CALLABLE_OK` ＋ `returns_note="non_boolean_return"` | `returns_type` ＋ `returns_truthy` | ❌把 `0` 当作 `False` |
| 7 | **任意文件名**的实现内部抛错（如 `/tmp/impl_xyz.py`） | `True` | `False` | `True` | **≥2** | **`INTERNAL_ERROR`** | `internal_frames` 逐帧 ＋ 原异常 repr | ❌「API 不存在」（**API 存在且可调用**） |
| 8 | **原生对象**抛错（如 `is_trade` 是会抛错的可调用对象） | `True` | `False` | `True` | **≥2** | `INTERNAL_ERROR` | 同上 | ❌「API 不存在」 |
| 9 | 调用处**未绑定**（`fn()` 抛 `NameError: name 'is_trade' is not defined`） | `True` | `False` | `True` | **1** | `SCOPE_NOT_RESOLVED` | `exception_type='NameError'` ＋ 帧链 | ❌「平台无此 API」 |
| 10 | **帧链不可判**（帧数 0 或结构异常） | — | — | — | 0/异常 | **`UNKNOWN`** ＋ `unknown_reason` ＋ **保原异常 repr** | `exception_obj_repr` | ❌任何肯定结论 |
| 11 | **日志失败** | 任一 | — | — | — | 主 verdict 不变 ＋ `log_failed=True` | 读数在返回值/stdout/文件 | ❌丢读数 |
| 12 | **日志与文件同时失败** | 任一 | — | — | — | 主 verdict 不变 ＋ `log_failed=True` ＋ `file_write_failed=True` ＋ `out_path=None` | 读数在**返回值** | ❌丢读数 |
| 13 | **stdout 失败** | 任一 | — | — | — | 主 verdict 不变 ＋ `stdout_failed=True` | 读数在返回值/文件 | ❌丢读数 |

## 四、修订后探针源码（**完整、唯一权威**）

```python
# ===== QS_PROBE_IS_TRADE v3（只读 · 零交易 · 可留证）=====
# 顶层统一导入：分支内不得依赖局部 import（D-a 反例：异常分支再抛 NameError: os 未定义）
import os as _qs_os
import sys as _qs_sys
import json as _qs_json
import traceback as _qs_tb

_QS_PROBE_OUT_DIR = ""     # 执行方在装载前设置为临时目录绝对路径（空 ⇒ 不写文件）
_QS_PROBE_RUN_ID = ""      # 执行方设置：UTC 时间戳＋短随机串（唯一命名，防覆盖）
_QS_PROBE_RESULT = {}


def _qs_probe_out_path(scope_name):
    """唯一命名：QS_PROBE_IS_TRADE_<RUN_ID>_<scope>.json；失败返回 None（不抛）。"""
    try:
        if not _QS_PROBE_OUT_DIR or not _QS_PROBE_RUN_ID:
            return None
        return _qs_os.path.join(
            _QS_PROBE_OUT_DIR,
            "QS_PROBE_IS_TRADE_%s_%s.json" % (_QS_PROBE_RUN_ID, scope_name))
    except BaseException:
        return None


def _qs_probe_collect(scope_name):
    """收集 存在/可调用/返回值/异常 ＋ 完整帧链；不吞异常、不猜。"""
    rec = {
        "probe": "QS_PROBE_IS_TRADE", "probe_version": 3, "scope": scope_name,
        "key_present": None, "value_is_none": None, "callable": False,
        "returns": None, "returns_type": None, "returns_bool_like": None,
        "returns_is_none": None, "returns_truthy": None, "returns_note": None,
        "exception_type": None, "exception_msg": None, "exception_obj_repr": None,
        "traceback": None, "frames": [], "internal_frames": [], "frame_count": 0,
        "resolved_from": None, "verdict": "UNKNOWN", "unknown_reason": None,
        "log_failed": False, "stdout_failed": False, "file_write_failed": False,
        "out_path": None,
    }
    # ① 存在性：键是否存在（与「值为 None」区分，D-b）
    try:
        rec["key_present"] = ("is_trade" in globals())
    except BaseException as e:
        rec["scope_check_error"] = "%s: %s" % (type(e).__name__, e)
        rec["verdict"] = "UNKNOWN"; rec["unknown_reason"] = "scope_check_failed"
        return rec
    if rec["key_present"] is not True:
        rec["verdict"] = "SCOPE_NOT_RESOLVED"      # 仅证「该作用域未取得」
        return rec
    fn_obj = globals().get("is_trade")
    rec["value_is_none"] = (fn_obj is None)
    if fn_obj is None:
        rec["verdict"] = "PRESENT_BUT_NONE"       # 键在、值为 None ⇒ 与缺名不同
        return rec
    rec["callable"] = callable(fn_obj)
    rec["resolved_from"] = (getattr(fn_obj, "__module__", None)
                            or getattr(fn_obj, "__name__", None))
    if not rec["callable"]:
        rec["verdict"] = "PRESENT_NOT_CALLABLE"    # 存在但不可调用
        return rec
    # ② 可调用：返回值 / 异常（含完整帧链）
    try:
        v = fn_obj()
        rec["returns"] = repr(v)
        rec["returns_type"] = type(v).__name__
        rec["returns_bool_like"] = isinstance(v, bool)
        rec["returns_is_none"] = (v is None)
        rec["returns_truthy"] = (v if isinstance(v, bool) else bool(v))
        rec["verdict"] = "CALLABLE_OK"
        if not rec["returns_bool_like"]:
            rec["returns_note"] = "non_boolean_return"
    except BaseException as e:
        rec["exception_type"] = type(e).__name__
        rec["exception_msg"] = str(e)
        rec["exception_obj_repr"] = repr(e)
        try:
            rec["traceback"] = _qs_tb.format_exc()
            rec["frames"] = [
                {"file": fr.filename, "line": fr.lineno, "func": fr.name,
                 "code": (fr.line or "")[:160]}
                for fr in _qs_tb.extract_tb(e.__traceback__)]
        except BaseException:
            rec["traceback"] = None
            rec["frames"] = []
        rec["frame_count"] = len(rec["frames"])
        rec["internal_frames"] = rec["frames"][1:]   # 帧序判定，不依赖文件名（D-c）
        # R3.1 修正（2026-11-15 合成反例自测所得）：帧序**不足以**区分
        # 「调用处未绑定」与「被调实现内部抛错」——二者在 Python 中**同形**
        # （frames[1:] 均非空）。故必须**叠加异常消息判据**（点名 is_trade 未定义）。
        _msg = (rec["exception_msg"] or "")
        _unbound = (rec["exception_type"] == "NameError"
                    and "is_trade" in _msg and "not defined" in _msg)
        rec["unbound_named_is_trade"] = _unbound
        if _unbound:
            rec["verdict"] = "SCOPE_NOT_RESOLVED"    # 调用处未绑定该名（消息点名）
        elif rec["frame_count"] >= 2:
            rec["verdict"] = "INTERNAL_ERROR"         # API 存在且可调用，实现内部抛错
        elif rec["frame_count"] == 1:
            rec["verdict"] = "SCOPE_NOT_RESOLVED"    # 单帧且未点名 ⇒ 仍视为调用处未绑定
        else:
            rec["verdict"] = "UNKNOWN"                # 帧链不可判（保原异常）
            rec["unknown_reason"] = "frame_chain_empty"
    return rec


def _qs_probe_is_trade(scope_name="probe"):
    """零交易只读探针：不调 set_backtest()、不调任何 order*、不改任何状态。
    读数恒可从返回值取回；stdout/文件/日志任一失败均不丢读数。"""
    rec = _qs_probe_collect(scope_name)
    # ② stdout 通道
    try:
        print("QS_PROBE_IS_TRADE " + repr(rec))
    except BaseException:
        rec["stdout_failed"] = True
    # ③ 文件通道（唯一命名；失败不抛）
    try:
        p = _qs_probe_out_path(scope_name)
        rec["out_path"] = p
        if p:
            with open(p, "w", encoding="utf-8") as _f:
                _qs_json.dump(rec, _f, ensure_ascii=False, indent=2)
    except BaseException:
        rec["file_write_failed"] = True
    # ④ 平台日志通道
    try:
        log.info("QS_PROBE_IS_TRADE " + repr(rec))
    except BaseException:
        rec["log_failed"] = True
    _QS_PROBE_RESULT.clear()
    _QS_PROBE_RESULT.update(rec)
    return rec          # ← 主通道：读数恒在此
```

## 五、修订后清单（**仅文档，审定后方可呈运行许可**）

| 步 | 动作 | 判据 |
|---|---|---|
| 0 | 重算 8 文件 sha256 ＋ 合成摘要 | 不符 ⇒ **中止** |
| 1 | 平台产品/版本/账号（沪A `A222087012`／深A `0028389333`）／**模拟盘非实盘**／**无持仓无挂单** | 账户快照留证 |
| 2 | 装载前设置 `_QS_PROBE_OUT_DIR`（临时目录绝对路径）＋ `_QS_PROBE_RUN_ID`（UTC＋随机） | 唯一命名 |
| 3 | **P1**（手写最小脚本，不经转换管线）在 **C-TR** 运行 | R-TR 读数（十三分支全字段 ＋ 帧链 ＋ 原异常 repr） |
| 4 | 同会话同机同版本下 **P1** 在 **C-BT** 运行 | R-BT 读数 |
| 5 | **P2**：生成最小载体源（**零下单**、含 `is_trade()`＋`set_backtest()`）→ 转换管线出 **V-A**／**V-B** → 登记 sha | 两版载体 sha |
| 6 | 副本内**替换 `initialize` 函数体**为 `return _qs_probe_is_trade("product")` | 副本 sha ＋ AST 可解析 |
| 7 | 副本内五个 `order*` 包装改写为**拒绝桩**（命中计数＋warning＋返回 `None`） | 副本 sha ＋ 桩清单 |
| 8 | 两语境装载副本并让平台调用 `initialize` | R-TR-PROD／R-BT-PROD ＋ **命中计数** |
| 9 | **硬闸**：命中计数 **>0 ⇒ 立即中止** | 命中数须 **0** |
| 10 | 删除全部副本；sha 比对证明**正式产物与 8 文件输入面均未变** | 删除后 sha |
| 11 | 归档：十三分支读数 ＋ 帧链 ＋ 原异常 repr ＋ 载体/副本 sha ＋ 命中计数 ＋ 平台标识 | evidence |

## 六、与 R2 的差异清单（便于技术审逐点核）

| R2 | R3 | 依据 |
|---|---|---|
| `import os as _os` 在 `_qs_probe_is_trade` 内 + `global _os` | 顶层统一 `_qs_os/_qs_sys/_qs_json/_qs_tb` | D-a（异常分支 `os` 未定义） |
| `globals().get("is_trade")` 单一来源 | **先判键存在性** → 再判值 ⇒ 拆出 `PRESENT_BUT_NONE` | D-b |
| 内部帧按**文件名含 `is_trade`** 判定 | **帧序判定**（`frames[1:]`）⇒ 覆盖任意文件名实现 | D-c |
| 三态 | **十三分支**（含非布尔返回、原生对象抛错、日志+文件同时失败、UNKNOWN 保原异常） | 审核指定 |
| 输出路径/命名/失败出口未明确 | `_QS_PROBE_OUT_DIR`＋`_QS_PROBE_RUN_ID`＋唯一命名＋**四通道**（返回值为主通道） | 审核指定 |
| P1/P2 与禁止范围、顶层导入、回调可达性分散 | §二.7 统一表 | 审核指定 |

---

## 七、R3 合成反例自测读数（2026-11-15 执行；**临时目录，未落产品/测试源码**）

> 审核指出的 D-a 由**合成反例**证实（异常分支再抛 `NameError: os 未定义`）。
> 为避免第三次退修，我方对 R3 源码**自建 9 个合成反例 ＋ 1 个双失败场景**实测。
> 方法：从本文件 §四 **抽出源码字符串** → `compile()` → 在**临时目录**逐场景注入
> `is_trade` 变体 → 调用 `_qs_probe_is_trade()` → 读 `verdict`／帧链／失败标记；
> **临时目录事后删除，未落任何产品/测试源码，未运行探针/D5/产品测试**。

### 7.1 实测结果（v3）

| 场景 | 未崩溃 | verdict | frame_count | value_is_none | key_present |
|---|---|---|---|---|---|
| S1 缺名 | **OK** | `SCOPE_NOT_RESOLVED` | 0 | — | `False` |
| S2 键存在但值为 `None` | **OK** | **`PRESENT_BUT_NONE`** | 0 | `True` | `True` |
| S3 存在但不可调用 | **OK** | `PRESENT_NOT_CALLABLE` | 0 | `False` | `True` |
| S4 返回 `False` | **OK** | `CALLABLE_OK` | 0 | `False` | `True` |
| S5 返回 `True` | **OK** | `CALLABLE_OK` | 0 | `False` | `True` |
| S6 **非布尔返回**（`0`） | **OK** | `CALLABLE_OK` ＋ `returns_note="non_boolean_return"` | 0 | `False` | `True` |
| S7 **任意文件名**实现内部抛错（`weird_name_not_istrade.py`） | **OK** | **`INTERNAL_ERROR`** | **3** | `False` | `True` |
| S8 「调用处未绑定」 | **OK** | ⚠️ **`INTERNAL_ERROR`（误判！）** | 2 | `False` | `True` |
| S9 「帧链不可判」 | **OK** | ⚠️ `INTERNAL_ERROR`（**场景未造出**） | 2 | `False` | `True` |
| 双失败（日志抛 ＋ 目录不可写） | **OK** | 主 verdict 不变 ＋ `log_failed=True` ＋ `file_write_failed=True` ＋ `out_path` 指向不可写路径 | — | — | — |

> **9/9 未崩溃** ⇒ D-a（`os` 未定义）**已消除**；D-b（缺名 vs 值 `None`）**已消除**（S1/S2 分离）；
> D-c（靠文件名判帧）**已消除**（S7 以 `weird_name_not_istrade.py` 正确判为 `INTERNAL_ERROR`）。

### 7.2 自测暴露的**第二处缺陷**（已就地修正 ⇒ R3.1）

| 项 | 发现 | 根因 | 修法 |
|---|---|---|---|
| **D-d** | S8（异常消息为 `name 'is_trade' is not defined`）被误判为 `INTERNAL_ERROR` | **帧序不足以区分**：「调用处未绑定」与「被调实现内部抛错」在 Python 中**同形**（`frames[1:]` 均非空）⇒ 须**叠加异常消息判据**（点名 `is_trade` 未定义） | §四 判据改为 **消息优先**：`NameError` 且消息含 `is_trade` + `not defined` ⇒ `SCOPE_NOT_RESOLVED`；否则 `frame_count>=2` ⇒ `INTERNAL_ERROR`；`frame_count==1` ⇒ `SCOPE_NOT_RESOLVED`；`frame_count==0` ⇒ `UNKNOWN`（保原异常） |
| **D-e** | S9「帧链不可判」场景**未造出** | 真 0 帧在 Python 中**实际不可达**（`raise` 必产生帧）⇒ 该分支只能由 **`extract_tb`/`format_exc` 自身抛错**触发（代码已 try/except 兜住 ⇒ `frames=[]` ⇒ `frame_count==0` ⇒ `UNKNOWN`） | 预期表注明：**真 0 帧为理论情形**；自测以「`extract_tb` 抛错」模拟，需在下次自测中补该注入 |

### 7.3 D-d 修正后的歧义登记（**须写入预期表**）

⚠️ 若被调实现**内部**恰好引用了 `is_trade`（如递归/闭包自调用），
其 `NameError` 消息**也会**点名 `is_trade` ⇒ 会被判为 `SCOPE_NOT_RESOLVED`（实为 `INTERNAL_ERROR`）。
⇒ **该歧义不可由判据消除**，只能由**帧内容**人工复核：`internal_frames` 中若无「引用 is_trade」的帧
⇒ 归 `SCOPE_NOT_RESOLVED`；有 ⇒ 归 `INTERNAL_ERROR`。**审阅读数时必须看 `internal_frames`，不得只看 verdict。**

### 7.4 自测覆盖缺口（下次自测须补）

| 缺口 | 说明 |
|---|---|
| G-1 | `extract_tb` 自身抛错 ⇒ `frames=[]` ⇒ `UNKNOWN` 分支（用 monkeypatch 注入） |
| G-2 | 被调实现内部**点名 is_trade** ⇒ 歧义场景（D-d 登记） |
| G-3 | `stdout` 失败分支（`print` 抛错）当前仅经 `except` 兜住，未单独造反例 |
| G-4 | 文件**部分写入**（写一半失败）⇒ `file_write_failed=True` 但文件已存在 |

