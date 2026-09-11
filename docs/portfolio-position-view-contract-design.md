# 持仓视图契约修复 · 设计方案（六步流水线第 1 步 · 审计通过）

- 状态：**方案定稿 + 审计通过（5 项实施条件 + 4 项打磨项已钉入）**；实施见 `docs/evidence/portfolio-position-view-acceptance.md`
- 触发来源：B2 验收期间 round_trips 空输出疑点的最小复现 → 深挖为框架层持仓视图契约缺陷
- 取证：`docs/evidence/portfolio-position-view-defect.md` · 影响面：`docs/evidence/portfolio-position-view-impact.md`

## 一、目标与成功判据

三个本地持仓入口（`context.portfolio.positions` / `get_positions()` / `get_position()`）在引擎在场时
**统一返回 PTrade `Position` 契约**；消除「策略读到引擎 dataclass、契约字段全 MISSING→0」的静默错误。

**关单口径**：以「修复后行为符合 PTrade 契约」关单，**不以「修复前后一致」关单**（本修复是正确性变更）。

## 二、根因（运行时定谳 + 双端核对）

**唯一缺陷点** `quantstudio/backtest/ptrade_api.py` `Portfolio.positions`：引擎在场时 `return dict(acc.positions)`，
绕过既有适配器，直吐 `backtest_engine.Position`（`volume`/`can_sell`）。

**单一适配源已存在**：`BacktestEngine._get_ptrade_positions()`（键归一 `.SS/.SZ`、返回 `ptrade_api.Position`、`volume>0` 过滤残影）；
`get_positions()` / `get_position()` 已经走它。→ 全链路唯一不走适配器的路径就是该属性。

**平台侧无需接管**：P-D11 wrapper 只包函数；平台 `context.portfolio.positions` 是 PTrade 原生对象（契约本已正确）。
当前「本地静默错 / 平台对」的反向分叉，修复即消除。

## 三、修复设计

### 3.1 落点：本地单文件委托（单次解析引擎对象）

```python
eng = self._engine()                     # 单次解析（与 _engine_account 同源，避免二次解引用漂移窗口）
if eng is not None and getattr(eng, "account", None) is not None:
    getter = getattr(eng, "_get_ptrade_positions", None)
    if getter is not None:
        return getter(getattr(_api, "_prices", None) or {})
return dict(self._init_positions)        # 无引擎/无适配器：D2 只读快照 + 同形状
```

**不下沉、不触模板** → 转换产物零变化、矩阵哈希无需 reverify。
键语义保持 exact-match（`.SS/.SZ` 精确匹配、alias 故意不感知）。

### 3.2 双侧 parity 防漂移

`tests/test_position_view_contract_parity.py`：以字段映射表为**测试内单一常量 dict**，
本地适配器与转换侧 `_QSPositionView`（渲染模板 + 桩对象执行）共同断言 → 机械防漂移。

## 四、字段映射表（唯一规范）

| 契约字段 | 来源 |
|---|---|
| 键 / `sid` | `normalize_to_ptrade(bare)` → `.SS/.SZ/.BJ` |
| `amount` | `engine Position.volume` |
| `enable_amount` | **`can_sell − pending_sell_shares`** |
| `cost_basis` / `avg_cost` | `engine Position.avg_cost` |
| `last_sale_price` | **`_prices[engine_code]`；缺失回退 `avg_cost`** |
| `market_value` | `last_sale_price × amount` |

仅 `volume > 0` 入视图。

**`last_sale_price` 反静默兜底契约**：回退**仅允许**两种合法空窗（① 盘前/收盘后 attach 空窗；② 该标的当日无行情=停牌），
**禁止成为常态路径**。键格式已核：`_build_match_prices` 返回 QMT 键，与 `account.positions` 一致 → 命中为常态。

**`enable_amount` T+1 时序**：`backtest_engine.py:574-575` 每日全量解锁在策略回调前；`:1138-1140` 当日买入股票 `can_sell` 不增。
若实测时序不成立 → 仅回退该行，property 委托照常交付。

## 五、禁止面

不改引擎撮合/持仓核算本体；**不改任何策略源码**；不改 `source_import.py`/P-D11 wrapper/不新增注入模板；
不引入持仓缓存；`Portfolio.market_value` 保持现状不改；不借委托夹带其他口径改动。

## 六、影响面

18 个引用 `portfolio.positions` 的文件逐条结论见 `docs/evidence/portfolio-position-view-impact.md`：
甲类 0、乙类 5（修复前静默错 → 修复后正确）、丙类 3（连带作废）、其余为键语义（不受影响）。
**连带作废重跑清单 = 6 文件**（具名见该文档 §四）。

## 七、改动范围（精确文件清单）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `quantstudio/backtest/ptrade_api.py` | `_engine()` 单次解析（`_engine_account` 复用）；`positions` 委托适配器；`get_positions`/`get_position` 传 `_prices`；`Position.__init__` 增可选 `enable_amount` |
| 2 | `quantstudio/backtest/backtest_engine.py` | `_get_ptrade_positions` 传 `enable_amount = can_sell − pending_sell_shares`；docstring 对齐为「唯一适配器契约」 |
| 3 | `tests/test_position_view_contract_parity.py` | 新增：双侧 parity |
| 4 | `tests/test_position_view_engine_contract.py` | 新增：三入口一致性 / 只读快照 / 空仓语义 / 键归一含 BJ / 无引擎分支同形状 / 取价命中与回退两态 |
| 5 | `tests/test_strategy_alignment_regressions.py` | 两条既有红测试转绿（含 `_api` 单例跨测试残留的测试隔离修复） |
| 6–8 | `docs/portfolio-position-view-contract-design.md` / `docs/evidence/portfolio-position-view-acceptance.md` / `docs/evidence/portfolio-position-view-impact.md` | 新增 |
| 9 | `README.md` / `docs/strategy_toolbox.md` / `docs/prompt_engineering.md` | 契约同步 |

## 八、验收标准

① 红测试转绿；② `probe_position_view.py` 转绿；③ smallcap_overnight 同窗口恢复正常买卖回合（**记录 DB 绝对路径与窗口配置**）；
④ 6 策略转换产物逐位一致；⑤ 全套回归绿 + 契约门 PASS（**另单列「测试断言更新清单」**）；
⑥ 只读快照语义保持 + 热路径微基准（**命中与兜底两态耗时**）。

## 九、回退条件

验收 ④ 非预期差异 / ⑤ 新增失败（断言更新清单除外）/ `get_positions()` 语义出现非声明内变化 /
⑥ 耗时劣化 > 2 倍且无可辩护理由 / T+1 时序实测不成立（仅回退 `enable_amount` 一行）。

## 十、六步流水线

① 方案 ✅ → ② 审计 ✅ → ③ 实施（写前 `stash create+store` 覆盖全部代码文件、edit 后即时 `git diff` 自检、精确清单 `git add`）
→ ④ 验收 → ⑤ 用户确认 → ⑥ 双仓库推送 + QuantStudio-trading 同步门。

## 十一、Backlog

见 `docs/evidence/portfolio-position-view-impact.md` §四（6 文件重跑清单 / 乙类重验 / `ETF平滑动量轮动.value` 独立缺陷 /
矩阵文档缺口 / 市值口径观察）。
