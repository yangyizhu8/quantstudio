# `is_trade` 平台事实探针包 · **修订版 R2**（2026-11-15）

> **本件取代** `is_trade_probe_package-20251115.md`（R1 判读表**退修**：把「`globals` 缺名」误判为
> 「API 缺失」、把「函数内部 `NameError`」误判为「API 不存在」；且缺「存在但不可调用／内部异常／
> 日志失败」三分支；未证明「临时副本继承策略回调可能发单」的入口可达性与副作用阻断）。
> **边界不变**：**不运行探针、不运行 D5、不改断言求绿、不自动恢复 `False` 改写、不实施提交**。

## 一、本轮新增的实测事实（影响载体设计）

| 事实 | 实测 |
|---|---|
| 六策略产物中 `is_trade` 命中 | **全部为 0**（`is_trade()` 未出现在任何本批产物的代码里） |
| 其他产物中的命中 | 仅 `全球资产轮动_ptrade.py`／`四象限ETF轮动策略_ptrade.py` 各 **1** 次，且**在 L5 注释**内（「改写为等价字面量 False」） |
| 产物的策略回调 | 每个产物都有 `def initialize(context)`（例：`tech_etf_mvo_rotation` L1934）；**无** `handle_data` |
| ⇒ 载体结论 | **六策略产物不可作探针载体**（不含 `is_trade()`）；且 A-1 的 KEEP 改动**对本批六策略无实际代码影响**，其影响面仅限**测试 fixture**与**未来含 `is_trade()` 的策略** |

## 二、载体方案（两段式，各答一个问题）

| 段 | 载体 | 回答的问题 | 依赖 |
|---|---|---|---|
| **P1 平台事实** | **手写最小 PTrade 脚本**（不经转换管线）：<br>`def initialize(context): _qs_probe_is_trade()` | **平台侧**：`is_trade` 在该语境**是否存在／可调用／返回值／异常** | 无（不依赖转换管线、不依赖六策略、不依赖产物 sha） |
| **P2 产物形态** | **经转换管线生成的含 `is_trade()` 载体产物**（测试专用最小策略源，见 §二.1） | **产物侧**：A-1 形态（V-A 保留／V-B 改写）在该语境的实际行为与是否崩 | 转换管线 ＋ 载体产物 sha |

⚠️ **P1 与 P2 不可互相代替**：P1 回答「平台有无」，P2 回答「我方产物在该平台的表现」。
**P1 结果不蕴含 P2 结果**，反之亦然。

### 二.1 P2 载体源（最小、零交易、测试专用）

```python
# tests/fixtures/is_trade_probe_carrier.py（新增测试专用文件，非正式策略）
def initialize(context):
    if not is_trade():
        set_backtest()
```
⇒ 特征：**零下单**（无任何 `order*` 调用）、**含 `is_trade()`**、**含 `set_backtest()`**
⇒ 经同一转换管线生成 V-A／V-B 两版产物，其 sha256 一并登记（§四）。

## 三、修订判读表（**七态**；先定规则，再看读数）

> ⚠️ **R1 的两处误判已修正**：
> ①「`globals()` 里没有 `is_trade`」**只证明「该作用域未取得该 API」**（可能是作用域/导入路径问题），
> **不得**据此判「API 缺失」；
> ②「函数内部抛 `NameError`」**不等于「API 不存在」**——须**留完整堆栈**以区分
> 「模块级未绑定」与「被调函数内部引用了别的未定义名」。

| # | 读数组合 | verdict | 允许得出的结论 | **禁止**得出的结论 |
|---|---|---|---|---|
| 1 | `scope_resolved=False` | `SCOPE_NOT_RESOLVED` | **仅**「本次作用域未取得 `is_trade`」 | ❌「平台无此 API」 |
| 2 | `scope_resolved=True` ＋ `callable=False` | `PRESENT_NOT_CALLABLE` | 「存在但不可调用」（如为常量/非函数对象） | ❌「API 缺失」 |
| 3 | `callable=True` ＋ 调用成功 ＋ `returns` 落 `True/False` | `CALLABLE_OK` | 「可调用，返回值＝X」；与文档乙逐字比对 | ❌跨语境外推 |
| 4 | `callable=True` ＋ 调用抛错 ＋ **堆栈顶端在探针调用帧**且消息为 `name 'is_trade' is not defined` | `SCOPE_NOT_RESOLVED` | 「调用处未绑定该名」 | ❌「API 不存在」 |
| 5 | `callable=True` ＋ 调用抛错 ＋ **堆栈含被调函数内部帧**（`File "...is_trade..."`, `line …`） | `INTERNAL_ERROR` | 「**API 存在且可调用，但其实现内部抛错**」⇒ **不等于 API 缺失** | ❌把内部 `NameError` 当作「无此 API」 |
| 6 | 上述任一态 ＋ `log_failed=True` | 保留主 verdict，另加 `LOG_FAILED` 标记 | 「读数成立但日志投递失败」；读数须由**文件回退通道**落盘 | ❌因日志失败而丢失读数 |
| 7 | 未覆盖的组合 | `UNKNOWN` | 「读数不完整，须补证」 | ❌任何肯定结论 |

### 3.1 堆栈留存要求（**强制**）

- 调用异常时必须记录**完整 traceback**（`traceback.format_exc()`），且**逐帧**留存：
  文件名、函数名、行号、源码行；
- 必须能据此区分：
  - **模块/作用域层**（帧都在探针内，末帧是 `fn()` 调用处）⇒ 判 #4；
  - **被调函数内部**（存在被调实现体的帧）⇒ 判 #5；
- ⚠️ **若堆栈不可得**（如平台截断）⇒ verdict 降级为 `UNKNOWN`，**不得**猜测。

### 3.2 平台事实与产物形态的合并判读

| P1（C-BT 回测） | P1（C-TR 交易） | 结论 |
|---|---|---|
| `CALLABLE_OK`（`False`） | `CALLABLE_OK`（`True`） | 文档乙**成立** ⇒ A-1 方向正确；同步修 `ptrade-api-signatures.json` 登记源 ＋ 清 `_REWRITE_LITERALS` 死条目 |
| `CALLABLE_OK`（`False`） | 非 `CALLABLE_OK` | 交易侧异常 ⇒ 平台侧不稳定 ⇒ A-1 **须暂缓**，按 P2 实测定 |
| `INTERNAL_ERROR` | — | **API 存在**，但实现内部抛错 ⇒ **不得**判「API 缺失」；须另查实现 |
| `SCOPE_NOT_RESOLVED` | `SCOPE_NOT_RESOLVED` | 两语境均未取得 ⇒ **不能**直接判 API 缺失；须查平台的 API 暴露机制（模块/注入方式），再重测 |
| `SCOPE_NOT_RESOLVED` | `CALLABLE_OK` | 平台**有**该 API（交易语境可取）⇒ 文档乙**部分成立**；回测语境的差异须由 P2 在回测语境实测补齐 |

⚠️ **任何一态为 `SCOPE_NOT_RESOLVED` 时，A-1 都不得仅凭探针结果放行**；
必须补齐「平台 API 暴露机制」这一环，再重测。

## 四、完整探针源码（**唯一权威版本**）

```python
# ===== QS_PROBE_IS_TRADE v2（只读 · 零交易 · 可留证）=====
import traceback

_QS_PROBE_RESULT = {}

def _qs_probe_collect(fn, scope_name):
    """收集 is_trade 的四态 + 完整堆栈；不吞异常。"""
    rec = {
        "probe": "QS_PROBE_IS_TRADE",
        "scope": scope_name,
        "scope_resolved": False,
        "callable": False,
        "returns": None,
        "returns_type": None,
        "exception_type": None,
        "exception_msg": None,
        "traceback": None,
        "frames": [],
        "resolved_from": None,
        "verdict": "UNKNOWN",
        "log_failed": False,
    }
    # —— ① 存在（作用域层）——
    try:
        rec["scope_resolved"] = "is_trade" in globals()
    except BaseException as e:
        rec["scope_resolved"] = False
        rec["exception_type"] = "SCOPE_CHECK:%s" % type(e).__name__
    fn_obj = globals().get("is_trade")
    if fn_obj is not None:
        rec["callable"] = callable(fn_obj)
        rec["resolved_from"] = (getattr(fn_obj, "__module__", None)
                                or getattr(fn_obj, "__name__", None))
    # —— ②/③/④ 可调用 · 返回值 · 异常（留完整堆栈）——
    if fn_obj is None:
        rec["verdict"] = "SCOPE_NOT_RESOLVED"
    elif not rec["callable"]:
        rec["verdict"] = "PRESENT_NOT_CALLABLE"
    else:
        try:
            v = fn_obj()
            rec["returns"] = repr(v)
            rec["returns_type"] = type(v).__name__
            rec["verdict"] = "CALLABLE_OK"
        except BaseException as e:
            rec["exception_type"] = type(e).__name__
            rec["exception_msg"] = str(e)
            tb = traceback.format_exc()
            rec["traceback"] = tb
            for fr in traceback.extract_tb(e.__traceback__):
                rec["frames"].append({
                    "file": fr.filename, "line": fr.lineno,
                    "func": fr.name, "code": (fr.line or "")[:160]})
            # 区分「作用域层未绑定」与「被调函数内部异常」
            inside_api = any(("is_trade" in os.path.basename(fr["file"]).lower()
                              and fr["func"] != "_qs_probe_collect")
                             for fr in rec["frames"])
            rec["verdict"] = "INTERNAL_ERROR" if inside_api else "SCOPE_NOT_RESOLVED"
    return rec


def _qs_probe_is_trade(scope_name="probe"):
    """零交易只读探针：不调 set_backtest()、不调任何 order*、不改任何状态。"""
    import os as _os
    global _os
    rec = _qs_probe_collect(globals().get("is_trade"), scope_name)
    # —— 日志投递（失败不得丢读数）——
    try:
        log.info("QS_PROBE_IS_TRADE " + repr(rec))
    except BaseException:
        rec["log_failed"] = True
    # —— 文件回退通道（保证读数必落盘）——
    try:
        import json as _json
        _p = (_QS_PROBE_OUT_DIR if "_QS_PROBE_OUT_DIR" in globals()
               else "QS_PROBE_IS_TRADE.json")
        with open(_p, "w", encoding="utf-8") as _f:
            _json.dump(rec, _f, ensure_ascii=False, indent=2)
    except BaseException:
        rec["file_write_failed"] = True
    _QS_PROBE_RESULT.clear()
    _QS_PROBE_RESULT.update(rec)
    return rec
```

### 4.1 探针源码的硬性约束

| 约束 | 理由 |
|---|---|
| **不得**调用 `set_backtest()` | 甲的实证正是 `set_backtest` 触发 `NameError`；调用它会污染平台状态 |
| **不得**调用任何 `order*` | 零交易 |
| **不得** `except` 后静默返回默认值 | 沙箱纪律：默认值会掩盖真实失败 ⇒ 本探针的失败必须**显式**（`verdict` ＋ 堆栈） |
| 异常必须 `BaseException` 捕获 | 平台可能抛 `SystemExit` 之类 |
| 必须留**逐帧**堆栈 | 区分 #4／#5 的唯一依据 |
| 必须有**文件回退通道** | 覆盖「日志失败」分支（#6） |
| 探针函数名须唯一前缀 `_qs_probe_` | 便于事后在产物中定位与清除 |

## 五、入口可达性与交易副作用阻断（**用户指出的风险，须证**）

### 5.1 风险陈述

每个转换产物都含 `def initialize(context)`（例：`tech_etf_mvo_rotation` L1934），
而 **PTrade 平台在装载产物时会自动调用 `initialize`** ⇒
「临时副本继承原策略回调可能发单」是**真实风险**，不是假设。

### 5.2 可达性证明（**逐入口枚举**）

| 入口 | 产物中是否存在 | 平台是否自动调用 | 本探针是否触达 |
|---|---|---|---|
| `initialize(context)` | **存在**（每个产物一个） | **是**（PTrade 装载即调用） | **否**——副本中其函数体被**替换为仅调用探针**（§5.3 步骤 4） |
| `handle_data(context, data)` | 本批产物**无** | 是（若有） | 否（不存在） |
| `before_trading_start` / `after_trading_end` / `before_trading_end` | 本批产物**无** | 是（若有） | 否（不存在） |
| `order` / `order_value` / `order_target_value` / `order_target_percent` / `order_percent` | **存在**（产物 L1357/1447/1209/1513/1520） | 由策略体调用 | **否**——策略体不可达（initialize 已替换）**＋ 双保险拒绝桩**（§5.3 步骤 5） |

⇒ **结论**：策略体经 `initialize` 唯一入口可达；副本替换 `initialize` 后，
策略体（含一切下单逻辑）**不可达**。

### 5.3 操作清单（**修订版；审定后方可申请精确执行许可**）

| 步 | 动作 | 产出／判据 |
|---|---|---|
| 0 | 重算 §六 的 8 文件 sha256 ＋ 合成摘要 | 与 §六 逐位比对；**不符 ⇒ 中止** |
| 1 | 确认平台产品/版本、账号（沪A `A222087012`／深A `0028389333`）、**当前为模拟盘非实盘**、**无持仓**、**无挂单** | 平台标识 ＋ 账户快照 |
| 2 | 记录 **P1** 手写最小脚本（§二 P1）到平台，**先在 C-TR（交易/模拟盘语境）**运行 | R-TR 读数（七态全字段 ＋ 逐帧堆栈） |
| 3 | 同一会话、同一机、同一产品版本下，**C-BT（回测语境）**运行 P1 | R-BT 读数 |
| 4 | **P2 载体**：创建最小载体源（§二.1）→ 经转换管线生成 **V-A**／**V-B** 两版产物 → 登记各自 sha256 | 载体产物 sha（两版） |
| 5 | **阻断施工（对副本）**：把副本的 `initialize` 函数体**整段替换**为 `return _qs_probe_is_trade("product")`（见 §5.4 的替换脚本） | 副本 sha ＋ 替换后 sha |
| 6 | **双保险**：在副本内，把 `order` / `order_value` / `order_target_value` / `order_target_percent` / `order_percent` 五个包装**改写为拒绝桩**（记录命中计数 ＋ `log.warning` ＋ 返回 `None`） | 副本 sha ＋ 桩清单 |
| 7 | 在 **C-TR** 与 **C-BT** 分别装载**副本**并让平台调用 `initialize` | R-TR-PROD / R-BT-PROD 读数 ＋ **拒绝桩命中计数** |
| 8 | **安全闸**：若拒绝桩命中计数 **> 0** ⇒ **立即中止**、记录、**不继续** | 命中数须为 **0** 方可进入步 9 |
| 9 | 删除全部临时副本；以 sha 比对证明**正式产物与 §六 输入面均未变** | 删除后 sha 比对 |
| 10 | 归档：七态读数 ＋ 逐帧堆栈 ＋ 载体产物 sha ＋ 副本 sha ＋ 命中计数 ＋ 平台标识 | evidence 件 |

⚠️ **步 8 是硬闸**：命中数 > 0 即说明**策略体可达**，探针设计失效，必须重做阻断。

### 5.4 `initialize` 替换脚本（副本内，**不提交**）

```python
# 对副本按 AST 定位 def initialize(context): 的函数体，整体替换为：
#     def initialize(context):
#         return _qs_probe_is_trade("product")
# 替换后必须：①产物可 ast.parse；②副本 sha 记录；③正式产物 sha 未变
```

⚠️ 替换**只作用于副本**；正式产物与模板**零改动**。

## 六、完整源码 SHA（探针输入面，2026-11-15 实测）

| 文件 | sha256 |
|---|---|
| `quantstudio/strategy_compiler/source_import.py` | `191e9f1e1ad860e5d6b397ce5e65938db332ff6890c810447592fc61563b8497` |
| `quantstudio/strategy_compiler/portability_rules.py` | `84f591ee6f74f9fba990df6b72c1340645b1af5526a9c4adc563a55aa360f397` |
| `quantstudio/strategy_compiler/validators/validate_ptrade_portability.py` | `ce6f8b7caadd7237add9b1178fd011ba255d6123d8a9415243c6ff64d1b087dd` |
| `quantstudio/ptrade_deploy/transformer.py` | `a0c13e8b0171f0af4702cc8351388c4b6d447ef7b2a309510eacd259518ef443` |
| `quantstudio/ptrade_deploy/templates/isolation_template.py` | `72dc820e9d0a67832c18f8cc2b717b0e6ad409bc45cb778c6130dfb8a0267fb2` |
| `quantstudio/ptrade_deploy/templates/persistence_template.py` | `e7dd2929952c1fc108b2fb7c60d4b219a28cc3a0e76eb95b6c4c95f4d608d0b9` |
| `skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json` | `1b4b11ff4ac6e033432ef8e1508076030a8b7bac88c6c08b872f1497edff3b37` |
| `knowledge/ptrade-deploy/PD-2026-0020.md` | `a3406fbd7e6138fd698f586fdbdac2947c697f6d195920952d29392123749e71` |

**合成摘要**（按路径排序的 `path\0sha256\n` 串再 sha256）：
`1b85b55045c7d7612d9f4b9ba5113e3abab849543c267c4fede489494bd40089`

**仓库状态（实测）**：HEAD = `4b3eb0cd0877d95f6de2b0d5c69331b28cc31be8`；
上列 8 路径 `git status --porcelain` **空** ⇒ 输入面可复现。

## 七、边界（执行时同样成立）

- **零交易**：不调 `set_backtest()`、不调任何 `order*`；拒绝桩命中数须为 0；
- **不触生产库**；**不启停 daemon**（`--mode forever` 常驻者不属本次范围，只清本次自建进程）；
- **仅模拟盘/回测语境**，**不在实盘**；
- **不改正式产物/模板/断言**；临时副本事后以 sha 比对证明零改动；
- **不改断言求绿、不自动恢复 `False` 改写、不接受 `NameError` 作为「另一种返回」**；
- 探针结论落地前，**A-1 维持暂缓、两用例维持红灯**。
