# 形参契约闭合校验小批 · 方案（草稿）

> 状态：**方案草稿，未实施**（用户 2026-11-03 21:4x 追加裁定「形参契约闭合校验小批归你域，P0 闭环后出方案」；总调度 2026-11-03 23:5x 授权「等⑤期间先出方案草稿，只出不实施」）
> 前置证据：`knowledge/qmt/QD-2026-0017.md`（R82 firefly P0 —— 调用侧先行入库、引擎侧缺形参 ⇒ 远端回测 100% 崩溃 6 天）
> 算法原型：运维2 R85 实测「**形参契约 AST 双臂负向对照成立**」（其代码可作本批算法原型）
> 知识反哺：`知识反哺：[命中] QD-2026-0017（qd/D1，2026-11-03）`——本条目的 `feedback.gate_rule` 即本小批的前置需求；`知识反哺：[无命中] 依赖闭包族（R66 门只查 import 依赖，不查调用面形参），2026-11-03`

---

## 一、问题定义（R82 事故的一般化）

**已发生的失效链**（6 天窗口，远端回测 100%）：

1. 2026-10-12：`backtest_engine.py` 的 M5.2 件7 形参 `match_profile` 在工作树实现，**未入库**；
2. 2026-10-28：`fa82445` **只提交调用侧**（`run_ptrade_strategy.py` / GUI `workers.py` / `backtest_tab`）并传 `match_profile='ptrade'`；
3. 结果：远端 `main` 上「调用侧有实参、引擎侧无形参」⇒ 构造期 `TypeError: unexpected keyword argument 'match_profile'`，**远端回测 100% 崩溃**；
4. 本地 pytest 全绿（用工作树代码）⇒ **「本地绿 ≠ 仓库绿」**，直到客户反馈才暴露。

**既有闸为何没拦住**：
- R66 依赖闭包门（`scripts/check_dependency_closure.py`）只查 **import 依赖**是否随推，**不查调用面实参**；
- F5 式 `inspect.signature` 验证是**人工一次性动作**，未机检化、未挂闸。

## 二、判据（本批的核心，写死）

> **凡提交面内的任一 `BacktestEngine(...)` 构造点，其 keyword 实参名集合必须 ⊆ `BacktestEngine.__init__` 形参名集合。**
> 违者 **FAIL（fail-closed，退出码非 0，阻断推送）**。

补充三条同族判据（同批实现，共用一套 AST 设施）：

| 判据 | 内容 | 目的 |
|---|---|---|
| C1 | keyword 实参名 ⊆ 形参名 | 拦「调用侧先行」 |
| C2 | 必需形参（无默认值）⊆ 调用点提供的实参（位置实参数 ＋ keyword） | 拦「引擎侧新增必填形参、调用侧未跟上」 |
| C3 | 形参名唯一性 ＋ 无 `**kwargs` 兜底遮挡（若引擎形参表出现 `**kwargs`，C1 判据自动弱化，须显式告警） | 防判据被兜底吞掉 |

## 三、被测面清单（本轮取证）

引擎形参实测（`inspect.signature(BacktestEngine.__init__)`，**18 项含 self / 17 项实际形参**）：

```
db_path* strategy* start* end* capital=100000 cost=None strategy_type='ptrade'
min_rebalance_pct=0.005 progress_callback=None config=None match_price_mode='close'
providers=None engine_profile='daily-bar-v1' etf_t0=False rebalance_mode=None
cost_basis_method='diluted' match_profile='ptrade'          （* = 必需）
```

构造点实测（`quantstudio/**/*.py`）：

| 位置 | 性质 | 纳入校验 |
|---|---|---|
| `quantstudio/backtest/run_ptrade_strategy.py:407` | **生产调用点**（CLI 入口） | ✅ |
| `quantstudio/backtest/calibration.py:108` | **生产调用点**（校准） | ✅ |
| `quantstudio/backtest/backtest_engine.py:6` | **docstring 示例**（非代码） | ❌ 必须排除 |
| `quantstudio/backtest/ptrade_api.py:3037` | **注释**（提及 `min_rebalance_pct=0.005`） | ❌ 必须排除 |
| GUI 链（`gui/tabs/backtest_tab.py` → `gui/workers.py`） | 参数经 dict 透传，**非直接构造** | ⚠️ 二级判据（见 §四-3） |

> **这是本批最容易写错的地方**：若用正则 `BacktestEngine\(` 扫源码，会把 docstring 与注释计为调用点 ⇒ **误报**。
> 因此实现**必须用 AST**（`ast.parse` → `ast.Call` 节点），这也是运维2 原型「AST 双臂负向对照」的要义。

## 四、改动范围（精确清单）

| 序 | 文件 | 改动 | 行量预估 |
|---|---|---|---|
| 1 | `scripts/check_engine_param_closure.py` | **新建**：AST 扫描 → 取 `ast.Call` 中 `func` 为 `BacktestEngine` 的节点 → 提取 keyword 实参名 → 与 `inspect.signature` 比对 → 打印逐点结果并 `sys.exit(0/1)` | ≈180 行 |
| 2 | `tests/test_engine_param_closure_gate.py` | **新建**：**双臂负向对照**（见 §五 V2）＋ 现状 PASS ＋ docstring/注释不误报 ＋ `**kwargs` 告警态 | ≈120 行 |
| 3 | `.githooks/pre-push` | 追加一段：在既有铁律预算门**之前或之后**串接本闸（fail-closed：找不到 python 即 exit 9，与既有纪律一致） | ≈10 行 |
| 4 | `docs/ISSUES.md` | `ISS-R82-B` 状态更新（方法论缺口 → 已由本批机检化关闭） | 3 行 |
| 5 | `knowledge/qmt/QD-2026-0019.md` | **新建**（R4 沉淀）：「调用面形参 ⊆ 引擎形参」族 | ≈55 行 |

**禁触面**：`quantstudio/backtest/backtest_engine.py` 逻辑零改动（本批只读它取签名）；`ptrade_api.py`（他线 M 态）零触碰。

## 五、验收标准

| 门 | 判据 | 方法 |
|---|---|---|
| **V1** | 现状 **PASS**（当前两个生产调用点均闭合） | `python scripts/check_engine_param_closure.py` → exit 0，逐点打印 `OK` |
| **V2** | **双臂负向对照成立**（防恒绿——本批最关键的门） | ①**负向 1**：临时给 `run_ptrade_strategy.py:407` 加 `__fake_kw=1` ⇒ 脚本必须 **exit 1** 并点名该点；②**负向 2**：临时从引擎形参表删一个必需形参 ⇒ **C2** 必须 **exit 1**；③两负向均须**还原后复跑 PASS**（还原用逐文件 `git show <baseline>:<path>`，禁整仓 reset） |
| **V3** | 不误报 | docstring 示例（`backtest_engine.py:6`）与注释（`ptrade_api.py:3037`）**不得**被计入调用点（断言扫描到的调用点数 == 2） |
| **V4** | 钩子实接 | 造一个违规暂存 → `git push` 被拒（exit 非 0）→ 撤销暂存 → 复推不受阻 |
| **V5** | 既有测试无新增失败 | `pytest tests -q` 对基线 `5cd6327`（`4380 passed`）**无新增失败**（裁一口径） |
| **V6** | 知识反哺 | `kb_hook.py --strict` 对 `QD-2026-0019` 不合规 0 件 |

## 六、影响面与判型

- **判型**：**新增检测型**（纯增益铁律的第四型）——verdict 可被新证据改变（原本无闸，现在有闸可拦红）。
  按铁律 18 要求：**方案阶段单列，不得与纯恢复型混办**（本批单独成批，不并入 R82）。
- **会变的行为**：推送前多一道闸；**合法提交零影响**（现状实测两调用点已闭合）。
- **不会变的行为**：引擎/调用侧/转换产物/黄金结果**全部零改动**（本批只新增脚本、测试、钩子段）。
- **挂载点风险**：`.githooks/pre-push` 是共享层敏感文件（多线共用）；改动须
  ①只**追加**一段不改既有逻辑；②实测「不违规不阻断」；③与既有铁律预算门、C7 闸串联顺序明确。
- **误报风险**：若将来引入 `BacktestEngine(**cfg)` 动态构造点，C1 无法判定 ⇒ 须**显式白名单登记**（脚本内置 `ALLOW_DYNAMIC` 列表，登记需注释理由），且白名单本身计入审计。

## 七、回退条件

| 触发 | 手段 |
|---|---|
| V2 负向对照不成立（闸恒绿） | **立即废弃本批**，不合并；说明「检测能力不成立」并重新设计 |
| V4 钩子误拦合法提交 | 移除钩子段（单文件定向还原），保留脚本与测试，降级为「手动门」并登记 |
| V5 出现新增失败 | 逐文件还原（`git show <baseline>:<path>`） |
| 回退点 | 实施轮开工前按铁律 03 四步配方建 `stash`（`add -A -- <paths>` → `stash create` → `stash store` → `reset`），hash 记入轮次申报 |

## 八、与既有闸的关系（不重复造轮子）

| 既有闸 | 覆盖 | 本批补位 |
|---|---|---|
| R66 依赖闭包门 `check_dependency_closure.py` | import 依赖是否随推 | **调用面实参**闭合（本批补齐的另一半） |
| `.githooks/pre-push` 铁律预算门 | AGENTS.md 体积/结构四查 | 串接本闸，互不替代 |
| F5 `inspect.signature` 人工验证 | 一次性、人工 | 机检化、挂闸、可回归 |
| `check_remote_advance.py` | 远端是否已推进（第 0 步机检） | 无关（远端时序面） |

## 九、待用户裁定（2 项）

1. **挂载位**：`.githooks/pre-push`（本地钩子，可 `--no-verify` 绕过）／ CI（不可绕但需远端配置）／**两者都要**。我建议「钩子先行 + CI 后续接入」。
2. **校验面范围**：只校 `BacktestEngine`，还是**泛化为所有被跨文件构造的内核类**（如 `QmtSourceConverter`、`SourceImporter`）？泛化收益大但实现复杂度↑，建议**先做 `BacktestEngine` 单类跑通，下一批再泛化**。

## 十、变更日志

| 时间 | 内容 |
|---|---|
| 2026-11-03 23:5x | v1.0 草稿：C1/C2/C3 三条判据、AST 实现（避开 docstring/注释误报）、双臂负向对照验收门、钩子接入与回退、待裁 2 项 |
