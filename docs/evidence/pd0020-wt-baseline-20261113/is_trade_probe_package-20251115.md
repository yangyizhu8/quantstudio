# `is_trade` 平台事实探针包（**不批准运行 · 仅提交审定**）

> 目的：解决 A-1 ②审的**③公开 API 依据不通过**——文档证据（乙）与仓内平台实证（甲）冲突：
> - **乙（文档）**：上游 `kay-ou/ptradeapi` 的 `docs/api-reference-en/utilities.md` 逐字
>   「`is_trade()` … Returns `True` in trading environment, `False` in backtest environment」
> - **甲（仓内实证）**：`skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json`
>   `:789` `local_only_symbols` 仍登记 `"is_trade"`；`:820`（`runtime_corrections.set_backtest_is_trade`）逐字
>   「Runtime traceback supplied on **2026-07-25** (platform timestamp 2026-07-26) proves
>   set_backtest/is_trade are QuantStudio-local extensions, **not PTrade backtest public APIs**」
>
> ⇒ 探针须**区分四个独立问题**，不得只答「在/不在」：
> **①存在**（符号是否在作用域内）**②可调用**（是否可被调用）**③返回值**（调用后得到什么）
> **④异常**（调用是否抛错、抛什么）。
>
> **边界（审核锁定）**：**不批准运行**；本件仅交**目标版本／回测语境／零交易探针／完整源码 SHA／操作清单**，
> 待清单审定后方可申请精确执行许可。**不自动恢复 False 改写、不接受 NameError**。

## 一、目标版本（探针须固定的被测版本）

探针必须落在**候选交付版本**的产物上；由于 A-1 裁定未决，探针须能对**多个候选**分别运行，
且每次运行**独立记录版本指纹**。

| 候选 | 说明 | 产物判定 |
|---|---|---|
| **V-A**（A-1 现状） | `is_trade` 迁 `PTRADE_REGISTERED_WARN`（KEEP）⇒ 产物**保留** `is_trade(` | 当前 HEAD |
| **V-B**（A-1 收回） | `is_trade` 回 `DENY_REMOVE` ⇒ 产物被改写为 `False` | 待裁定后生成 |

⚠️ **V-A 与 V-B 是互斥候选**，探针**不预设结论**：探针只回答「平台侧事实」，
产物侧形态由**转换产物**独立记录（产物侧形态与平台事实**分别记录、不得互推**）。

## 二、回测语境（两处必须分别跑）

| 语境 | 说明 | 判读要点 |
|---|---|---|
| **C-BT**（回测模块） | PTrade/IQEngine **回测**环境 | 文档乙称此处返回 `False`；若抛 `NameError` ⇒ **甲成立** |
| **C-TR**（交易模块） | PTrade/IQEngine **交易**环境（模拟盘/实盘） | 文档乙称此处返回 `True` |

⚠️ **必须同一次会话内、同一台机器、同一产品版本**下取得两处读数，否则不可比。
⚠️ **不得**在探针中调用 `set_backtest()`——那会改变平台状态（且甲的实证正是 `set_backtest` 触发 `NameError`）；
**只读探测 `is_trade`**。

## 三、零交易探针（只读、零订单）

> 探针体为**示意代码**，须由执行方按此语义落到**候选产物的临时副本**上；
> **不提交**到正式产物，**不产生任何订单**，**不改任何状态**。

```python
# 追加到候选产物临时副本的模块尾部（仅探针期间，事后删除）
def _qs_probe_is_trade():
    """零交易只读探针：区分 存在/可调用/返回值/异常 四态。"""
    out = {"probe": "QS_PROBE_IS_TRADE"}
    try:
        out["exists_in_scope"] = "is_trade" in globals()
    except Exception as e:
        out["exists_in_scope"] = "probe-error:%s" % type(e).__name__
    fn = globals().get("is_trade")
    out["callable"] = callable(fn) if fn is not None else False
    out["resolved_from"] = getattr(fn, "__module__", None) or getattr(fn, "__name__", None)
    if fn is None:
        out["returns"] = None
        out["returns_type"] = None
        out["exception"] = None
        out["verdict"] = "ABSENT"
    else:
        try:
            v = fn()
            out["returns"] = repr(v)
            out["returns_type"] = type(v).__name__
            out["exception"] = None
            out["verdict"] = "CALLABLE_OK"
        except BaseException as e:
            out["returns"] = None
            out["returns_type"] = None
            out["exception"] = "%s: %s" % (type(e).__name__, e)
            out["verdict"] = "CALLABLE_RAISED"
    try:
        log.info("QS_PROBE_IS_TRADE " + repr(out))
    except Exception:
        pass
    return out
```

### 3.1 四态判读表（**必须逐项记录，不得只给结论**）

| 判读 | 含义 | 结论影响 |
|---|---|---|
| `exists_in_scope` | 符号是否在该语境作用域内 | 符号存在 ≠ 可调用 |
| `callable` | 是否可调用（不是 `None`、不是不可调用对象） | `callable=False` ⇒ **不可用** |
| `returns` / `returns_type` | 调用**成功**时的返回值与类型 | 与文档乙的 `True`/`False` 比对 |
| `exception` | 调用**抛错**时的类型与消息 | `NameError` ⇒ **甲成立**（回测模块无此 API） |
| `verdict` | `ABSENT` / `CALLABLE_OK` / `CALLABLE_RAISED` | 三值穷尽 |

⚠️ **`NameError` 与「返回 False」不可互相替代**：文档乙的「回测返回 False」**蕴含 API 存在**；
若实测 `NameError` ⇒ 文档乙在该语境**不成立**，而**不是**「返回了 False 的另一种写法」。

## 四、完整源码 SHA（探针输入面，2026-11-15 实测）

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

**仓库状态**：HEAD = `4b3eb0cd0877d95f6de2b0d5c69331b28cc31be8`；
上列 8 个路径**无未提交改动**（`git status --porcelain` 空）⇒ 输入面**可复现**。

⚠️ **执行前必须重算**：若任一 sha 与当时实测不符 ⇒ **中止**，重新生成产物与探针。

## 五、操作清单（**待审定；审定后方可申请精确执行许可**）

| 步 | 动作 | 产出 | 判定 |
|---|---|---|---|
| 0 | 重算 §四 8 个 sha256 ＋ 合成摘要 | 与本表逐位比对 | 不符 ⇒ **中止** |
| 1 | 确认平台产品/版本、账号环境（沪A `A222087012`／深A `0028389333`）、当前为**模拟盘**而非实盘 | 平台标识记录 | 与本清单不符 ⇒ 停 |
| 2 | 生成 **V-A** 候选产物（当前 HEAD 六策略之一）并**冻结 sha256** | 产物 sha | 与 §四 输入面一致 |
| 3 | 复制 V-A 产物到**临时副本**，追加 §三 探针体（**仅此副本**） | 临时副本 sha | **正式产物零改动** |
| 4 | 在 **C-TR**（交易/模拟盘语境）加载临时副本，**只调用** `_qs_probe_is_trade()` | 读数 R-TR | — |
| 5 | 在 **C-BT**（回测语境）加载临时副本，**只调用** `_qs_probe_is_trade()` | 读数 R-BT | — |
| 6 | 记录两处 `verdict` ＋ 四态全字段 | `QS_PROBE_IS_TRADE` 日志行 | — |
| 7 | **删除临时副本**；核对正式产物 sha 未变 | 删除后 sha 比对 | 产物被改 ⇒ **事故** |
| 8 | 归档读数到 evidence（附 §四 sha 与本次产物 sha） | evidence 件 | — |

### 5.1 判读规则（**先定规则，再看读数**）

| R-BT.verdict | 结论 | 后续 |
|---|---|---|
| `CALLABLE_OK` 且 `returns == 'False'` | 文档乙**成立** | A-1 方向正确；同步修 `ptrade-api-signatures.json` 登记源 ＋ 清 `_REWRITE_LITERALS` 死条目 |
| `CALLABLE_RAISED`（`NameError`） | **甲成立** | A-1 **必须收回**（产物回测模块会崩）；**不自动恢复 False 改写** ⇒ 须另立方案（保留调用 ＋ 平台侧注册，或显式降级策略） |
| `ABSENT` | 回测模块无此 API | 同 `CALLABLE_RAISED`，A-1 **暂缓并收回方向** |
| `CALLABLE_OK` 且 `returns` 非 `False` | 文档乙**不准确** | 按实测值修订文档依据，**不得**反推为「甲成立」 |

⚠️ **R-TR 的作用**：确认「交易语境下该 API 存在且返回值符合文档」；
若 R-TR 亦 `NameError` ⇒ 该 API 在平台上根本不可用 ⇒ A-1 无论哪个方向都不成立。

### 5.2 边界（执行时同样成立）

- **零交易**：探针**不下单**、不调 `set_backtest()`、不改任何账户/持仓状态；
- **不写生产库**：探针不触 `data/quantstudio.db`；
- **不推送、不改正式产物**：临时副本事后删除并以 sha 比对证明；
- **不动 daemon**：不启停采集进程（`--mode forever` 常驻者**不属本次范围**）；
- **不在实盘**：仅模拟盘/回测语境；
- **不改断言、不改代码**：本探针只取平台事实。

## 六、与 A-1 的衔接

探针结果落地前，**A-1 维持暂缓**、**两用例维持红灯**（不静默改、不跳过）、
**不自动恢复 `False` 改写**。
探针结论出来后，须同步：
①`ptrade-api-signatures.json` 的 `local_only_symbols`（移出或加注更正＋裁定依据）；
②`source_import.py:3673` `_REWRITE_LITERALS` 死条目（清理或加注「A-1 后不可达」）；
③`profile-contract` 文档同步状态（此前审查中该文件在主仓**未能定位**，须重新定位并如实记录）。
