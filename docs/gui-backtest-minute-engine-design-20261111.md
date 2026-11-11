# GUI 回测模块支持分钟引擎 + 生成目录策略可见 · 方案（v1）

> **状态**：**②审已通过（2026-11-11 17:50，独立审核会话六项回答 + 三条补强，批准实施）**。下一步进入③实施。
> **裁定来源**：用户 2026-11-11 17:37 裁定「选项 C」。
> **作者**：策略研发专属会话（性能线审核方）

## 知识反哺：[命中] QD-2026-0017（qmt/QD-2026-0017.md，2026-11-11）——engine_profile 与分钟覆盖口径；[命中] DB-2026-0031（db/DB-2026-0031.md，2026-11-11）——回测入口参数契约；[无命中] 检索面无「GUI 策略枚举」专条，2026-11-11

---

## 一、问题定义（实测证据）

用户反馈：「GUI 界面的回测模块没有看见首板1进2策略的选项」。

### 1.1 实测归因（两条独立原因，均有源码行号）

| # | 原因 | 证据（文件:行） |
| --- | --- | --- |
| **A** | **枚举范围不含生成目录** | `quantstudio/gui/tabs/backtest_tab.py` **L190-194**：`strat_dir = root/"quantstudio"/"backtest"/"strategies"`；`strat_dir.glob("*.py")` **单层不递归** ⇒ 只认 `quantstudio/backtest/strategies/*.py`（**33 个正式策略**） |
| **B** | **引擎固定日线** | `run_ptrade_strategy.py` **L114** 默认 `engine_profile='daily-bar-v1'`；`backtest_tab.py` **L283-296** 的 `params` dict **不含 `engine_profile` 键** ⇒ 全程走默认 ⇒ 日线引擎 |
| **C** | 该策略为**分钟引擎** | `output/generated_strategies/first_board_high_open_1to2/strategy.py` **L3**：`engine_profile=minute-bar-v1  match_price_mode=open` |

⇒ **A+B 联合导致**：既看不到，即便看到也会用错引擎。

### 1.2 现状实测数据（支撑改动必要性）

| 项 | 实测值 |
| --- | --- |
| `quantstudio/backtest/strategies/*.py`（非 `_` 开头） | **33 个** |
| 其中声明 `engine_profile` 的 | **0 个** ⇒ **全部默认日线，现状正确** |
| `output/generated_strategies/` 子目录 | **4 个**，仅 `first_board_high_open_1to2` 有 `strategy.py`（另 3 个无该文件） |
| 该策略声明 | `minute-bar-v1` |

---

## 二、关键前提：**改动面比预想小得多**

> 🔴 **实测发现：调用链下游已全部支持 `engine_profile`，缺的只是 GUI 最上层没传。**

| 层 | 文件:行 | 现状 |
| --- | --- | --- |
| **引擎入口** | `run_ptrade_strategy.py` **L114** | `engine_profile='daily-bar-v1'` 形参（已有）；L587-588 校验合法值 `{daily-bar-v1, minute-bar-v1, daily-open-close-proxy-v1}` |
| **Worker** | `quantstudio/gui/workers.py` **L531** | **`engine_profile=self.params.get('engine_profile', 'daily-bar-v1')`（已支持！）** |
| **GUI 回测 tab** | `backtest_tab.py` **L283-296** | `params` dict **缺该键** ⇒ **唯一缺口** |

⇒ **⇒ 本方案只需改 `backtest_tab.py` 一个文件**；`workers.py` / `run_ptrade_strategy.py` **零改动**。
⇒ 这是本方案**影响面最小**的根本原因。

---

## 三、改动设计（三处，均在 `backtest_tab.py`）

### 改动 1：新增引擎下拉（`_setup_ui`，插在 L108 之后）

```python
# 引擎 Profile（v1 方案：GUI 显式声明引擎，避免分钟策略被日线引擎静默跑错）
self.engine_combo = ComboBox()
self.engine_combo.addItem("daily（日线引擎 daily-bar-v1）", userData="daily-bar-v1")
self.engine_combo.addItem("minute（分钟引擎 minute-bar-v1）", userData="minute-bar-v1")
param_form.addRow("引擎 Profile:", self.engine_combo)
```

**要点**：
- 默认落 **daily**（保持既有 33 个策略行为零变化）
- 只列两档；`daily-open-close-proxy-v1` 不在 GUI 暴露（引擎侧仍接受，GUI 不提供入口，避免误用）

### 改动 2：`_refresh_strategies()` 扩展扫描（改 L187-194）

```python
def _refresh_strategies(self):
    """扫描正式策略目录 + 生成策略目录，填充下拉框。

    正式目录：quantstudio/backtest/strategies/*.py（无 engine_profile 声明 ⇒ 视为 daily）
    生成目录：output/generated_strategies/*/strategy.py（读头部 engine_profile 声明）
    """
    self.strategy_combo.clear()
    # ① 正式策略（既有行为，零变化）
    strat_dir = self.mw.root_path / "quantstudio" / "backtest" / "strategies"
    if strat_dir.exists():
        for f in sorted(strat_dir.glob("*.py")):
            if not f.name.startswith("_"):
                self.strategy_combo.addItem(
                    f.name, userData={"path": str(f), "engine": _read_engine_profile(f)})
    # ② 生成策略（新增；带 [生成] 前缀区分来源）
    gen_dir = self.mw.root_path / "output" / "generated_strategies"
    if gen_dir.exists():
        for sub in sorted(gen_dir.iterdir()):
            sp = sub / "strategy.py"
            if sp.is_file():
                self.strategy_combo.addItem(
                    f"[生成] {sub.name}/strategy.py",
                    userData={"path": str(sp), "engine": _read_engine_profile(sp)})
```

**配套模块级函数**（同文件顶部，纯函数、可单测）：

```python
_ENGINE_DECL_RE = re.compile(r"engine_profile\s*=\s*([A-Za-z0-9\-]+)")
_ENGINE_HEAD_LINES = 60          # 只扫头部注释，避免全文扫描

def _read_engine_profile(path, default="daily-bar-v1"):
    """读策略头部 engine_profile 声明。

    无声明 ⇒ 返回 default（daily-bar-v1）。
    这是**声明读取**，不是猜测：33 个正式策略均无此声明，默认即其真实语义。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(_ENGINE_HEAD_LINES):
                line = fh.readline()
                if not line:
                    break
                m = _ENGINE_DECL_RE.search(line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return default
```

### 改动 3：`_on_run()` 传参 + 错配校验（改 L283-296 及之前）

**3a. `params` 增加键**：
```python
params = {
    ...
    'engine_profile': self.engine_combo.currentData(),   # ← 新增，唯一必需的一行
}
```

**3b. 错配校验**（插在 `strategy_path` 取到之后、`params` 构造之前）：
```python
_declared = _read_engine_profile(strategy_path)
_selected = self.engine_combo.currentData()
if _declared == "minute-bar-v1" and _selected != "minute-bar-v1":
    QMessageBox.information(
        self, "引擎不匹配",
        "该策略为分钟回测策略，请选择 minute 下拉项。")
    return
if _declared != "minute-bar-v1" and _selected == "minute-bar-v1":
    QMessageBox.information(
        self, "引擎不匹配",
        "该策略为日线回测策略，请选择 daily 下拉项。")
    return
```

**3c.（建议）选中即提示**：`strategy_combo.currentIndexChanged` 连一个轻量检查，选中就提示，不让用户点「运行」才发现。

---

## 四、🔴 需②审重点裁决的两个设计问题（我不自决）

### 议题 1：无 `engine_profile` 声明的策略，选 minute 该拦还是放？

| 方案 | 说明 | 我方倾向 |
| --- | --- | --- |
| **甲：拦住**（按用户原话字面执行） | 无声明 ⇒ 视为 daily ⇒ 选 minute 一律弹「该策略为日线回测策略」并阻断 | ❌ 不倾向 |
| **乙：只提示不阻断**（允许继续） | 无声明只说明「未声明」，不等于「是日线」。允许用户显式用分钟引擎跑无声明策略 | ✅倾向乙（**②审已裁定乙**） |

**倾向乙的理由**：
1. **语义诚实**：「未声明」≠「日线」。拦死等于把**推断**当**事实**。
2. **不封死合法场景**：想用分钟引擎跑一个未声明策略（做对比实验）是正当需求。
3. **用户原话的语境**是「分钟策略选了 daily / 日线策略选了 minute」——**有声明才能判「是该类策略」**。
4. 与既有纪律一致：**判据不清时不得 fail-closed 阻断合法路径**（项目已有多处「不阻断正常流程」的守卫原则，如 `backtest_tab.py` L255 注释「GUI 门自身绝不阻塞正常回测」）。

### 议题 2：生成策略的显示名与排序

| 方案 | 说明 |
| --- | --- |
| **甲**：`[生成] <子目录名>/strategy.py`，排在正式策略之后 | ✅ 我方倾向（来源一眼可辨） |
| 乙：与正式策略混排按字母序 | ❌ 来源不可辨 |

---

## 五、影响面（纯增益审计，铁律 18）

| 维度 | 评估 |
| --- | --- |
| **对既有功能** | **零回归**：33 个正式策略**全部无 engine_profile 声明** ⇒ `_read_engine_profile` 返回默认 daily ⇒ 下拉仍显示 daily ⇒ 行为与现状**逐位相同** |
| **对回测性能** | 零影响（引擎未改；仅 GUI 多一个下拉） |
| **对回测精度** | 零影响（daily 路径参数与现状完全一致） |
| **新增能力** | ①分钟引擎入口 ②生成目录策略可见 ③引擎错配提示 ⇒ 属**新增检测型/新增能力型**，非行为漂移 |
| **对 worker/引擎** | **零改动**（两者已支持 engine_profile） |

> **遗留项（②审补充，本次不改代码）：`etf_t0` 与 `engine_profile` 有隐式耦合** —— `run_ptrade_strategy.py` L607：`etf_t0` **仅在 `minute-bar-v1` 时生效**，日线强制 `False`。当前 GUI **无 etf_t0 控件**所以本次不受影响；**将来若给 GUI 加 etf_t0 复选框，必须与 `engine_combo` 联动校验**（daily 下禁用/灰化）。

---

## 六、验收标准

| # | 判据 | 方法 |
| --- | --- | --- |
| 1 | 33 个正式策略**全部仍在**下拉框（数量不减少） | 启动 GUI 目视 / 单测枚举数 == 33 |
| 2 | `[生成] first_board_high_open_1to2/strategy.py` **出现在**下拉框 | 同上 |
| 3 | 选它 + **daily** ⇒ 弹「该策略为分钟回测策略，请选择 minute 下拉项」**且不启动** | 手工点击 |
| 4 | 选它 + **minute** ⇒ **正常启动**，且 `run_backtest` 收到 `engine_profile='minute-bar-v1'` | Worker 日志 / 临时 monkey-patch 断点 |
| 5 | 任一正式策略 + daily ⇒ 行为与改动前**完全一致**（黄金对照） | 跑 5 日窗 `2026-03-02~03-06`，golden 应为 `b4b538a0…`（daily 路径既有锚） |
| 6 | 选它 + minute 跑 5 日窗 ⇒ golden = **`b4b538a0ca0eff1ae69a…`**（R-PERF 探针同锚） | 对照 `agent_workspace/perf_eval_rperf01/` 下 json |
| 7 | `_read_engine_profile` 单测**五态**：①有声明→返回值 ②无声明→daily ③文件不存在→daily ④乱码→daily **⑤ BOM/GBK 编码也必须安全返回不抛异常**（②审补强：正式目录 UTF-8，但**生成目录 agent 产物编码不确定**）⇒ 实现用 `open(path, encoding="utf-8", errors="replace")` + 仅读前 60 行，最坏返回 daily | pytest |

---

## 七、回退条件与方式

| 项 | 内容 |
| --- | --- |
| 回退触发 | ①33 个正式策略任一消失或行为变化 ②选 minute 后 golden ≠ 锚 ③GUI 启动异常 |
| 回退方式 | **单文件**：`git show <baseline>:quantstudio/gui/tabs/backtest_tab.py > <path>`（铁律 03 ④ 逐文件定向）；脏树先按铁律 03 建 per-path patch 锚并立验 |
| 回退成本 | 极低（一个文件、三处改动、无数据/契约变更） |

---

## 八、不做清单（红线）

| 不做 | 理由 |
| --- | --- |
| 不改 `workers.py` / `run_ptrade_strategy.py` | 已支持，改了反而扩影响面 |
| 不动 `engine_profile` 默认值 | 铁律 02：禁改默认值 |
| 不为生成策略自动复制进 strategies/ | 那会污染正式目录、绕开 R5 发布门 |
| 不在 GUI 暴露 `daily-open-close-proxy-v1` | 非通用路径，避免误用 |
| 不做「自动按声明切换引擎」 | 引擎选择必须是用户显式动作，静默切换=隐式改行为 |

---

## 九、改动清单（供实施轮）

| 文件 | 位置 | 改动 | 行数估计 |
| --- | --- | --- | --- |
| `quantstudio/gui/tabs/backtest_tab.py` | L9-14 imports | `import re`（若未导入） | +1 |
| 同上 | 模块级（L20 附近） | `_ENGINE_DECL_RE` + `_read_engine_profile()` | +20 |
| 同上 | L108 后 | `engine_combo` 创建 + `param_form.addRow` | +4 |
| 同上 | L187-194 | `_refresh_strategies()` 扩展 | +18 / -3 |
| 同上 | L196-200 | 错配校验 | +12 |
| 同上 | L283-296 | `params` 加 `engine_profile` | +1 |
| `tests/` | 新增 | `test_backtest_tab_engine_profile.py`（5 例） | 新文件 ~60 |

**合计**：`backtest_tab.py` 约 **+56 / -3 行**；新增测试 1 文件。**不改引擎、不改 Worker、不改策略、不动数据。**