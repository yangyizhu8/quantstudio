# P-D13 设计：WP-C 数据层对齐（C1 宇宙差 + C2 eps 口径 + C3 分位窗口审计 + C4 停牌/退市保真）

- **流水线状态**：Step 1 ✅ → Step 2 ✅ 审计通过（2026-08-27，附两条件）→ **Step 3 实施**
- **审计条件（并入实施）**：
  - ①D2 默认映射变化的验收前置证据：**on/off 双跑冒烟** + 1.9% 历史 fin/inc 差异行对策略选择的实际影响量化入 evidence（合并重验前的局部举证）；
  - ②4 tracked 文件 hunk 分布申报 + 与他线 M 交集复核，commit 前暂存核对闸照 WP-B 同款（`git diff --cached --stat` 回报 + 程序化 hunk 区域验证）。
- 关联：master-plan WP-C；Phase 0 证据（C1 diff / C2 PIT 巡检）；P-A3 二期（4430788，EPS 回补管线已关闭）；P-A2（eps basis 保真基建已就绪）
- 改动侧：**转换管线 + 本地 API 审计 + 保真开关（默认关）**——不触碰数据管线本体（P-A3 已覆盖）

---

## 1. 问题定义

### 1.1 C1：宇宙差（5511 vs 5205，恒差 306~328）

Phase 0 实证（`docs/evidence/c1-universe-diff-20260826.json`）：
- **仅本地 322 只 = 100% 北交所 920xxx**——本地 `stock_daily` 含北交所行情，平台 `get_Ashares()` 不含；
- **仅平台 16 只**（本地 stock_daily 缺行情）——含 605081/688121（fall_reversal ptrade 撤单股）与 300214（CANSLIM ptrade 07-28 买入）。

**影响**：全 A 选股策略的候选池两端天然不同（L0 差 → 漏斗每层差 → top-N 名单分岔）。

### 1.2 C2：eps 口径（平台加权 vs 本地 basic）

- 探针三实证：平台 `basic_eps` == 本地 `eps`（Δ=0.0000）；平台默认 `eps` 列为**加权口径**（+12%/+3.8% 偏差源）；
- P-A2 保真基建已就绪（`fidelity_eps_basis='basic'` 时产物请求 `basic_eps` 列），**但默认 `passthrough`**——未激活时平台策略消费加权口径 → L6 eps>0 过滤两端可分叉（weekly L6 本地 15-17 vs 平台 20-21 的口径分量）；
- P-A3 二期已完成 EPS 数据回填管线（`eps_backfill.py`）——**C2 不重复**，仅做映射激活决策 + PIT 巡检报告。

### 1.3 C3：分位窗口 / 复权快照

- vol_regime `q=0.9333`（本地）vs `0.9000`（ptrade）而 σ 相同——`get_history(count=300)` 的**实际返回窗口深度两端不一致**（平台明确 300 根起点 2025-04-03，本地窗口不同）；
- tech_etf 评分 ±1/7 网格差 = 复权因子快照日微差 → 排名边界翻转。

### 1.4 C4：停牌/退市

- fall_reversal：ptrade 4 只 `bar.volume=0` 撤单（本地无此约束）+ 4 只退市强平（本地无此生命周期）+ 退市日仍可买入的数据矛盾；
- master-plan 裁定：保真开关 opt-in（P-D9 纪律：本地语义权威默认关）。

## 2. 改动范围

### 2.1 C1：宇宙差处置（三件套）

**C1a. FUNNEL 板块统计审计行**（本地 API + 转换模板同构）：

本地 `ptrade_api.get_Ashares()` 返回后追加板块统计（`log.debug`，不侵入策略日志流）：
```python
# quantstudio/backtest/ptrade_api.py get_Ashares() 尾部
from .libs.security_code_rules import is_bse_market
_bse = sum(1 for c in codes if is_bse_market(c))
log.debug("QS_ASHARES_BREAKDOWN total=%d bse=%d non_bse=%d", len(codes), _bse, len(codes) - _bse)
```

转换模板 `_QS_ASHARES_BREAKDOWN_EXT`（门控：`_source_uses_ashares`——已有门控可复用 `_source_uses_fundamentals` 的模式）同构注入。

**C1b. 北交所范围开关**（本地 `get_Ashares` + 转换模板）：

本地 `get_Ashares(date, exclude_bse=None)` 参数——`exclude_bse=True` 时过滤 `is_bse_market` 命中码；默认 `None`（不动现状）。

转换模板：`get_Ashares()` 包装 + `_QS_EXCLUDE_BSE` 常量（转换期从 CLI `--exclude-bse` 旗标烘焙）。

**决策（审计要点）**：默认值 = `exclude_bse=False`（本地保持全 A 含北交所不变——D2 语义权威纪律）；对齐验证时用户显式传 `--exclude-bse` 使产物与平台 5205 一致。

**C1c. 仅平台 16 只：登记不修**（本地缺行情是数据源覆盖差——修复属数据管线范围，非转换层——**登记移交数据管线后续**）。

### 2.2 C2：eps 口径映射激活

**决策：`fidelity_eps_basis` 默认从 `'passthrough'` 改为 `'basic'`**（对齐默认——理由：探针三已实证 basic_eps == 本地 eps 逐位一致；passthrough 容忍加权口径差异 = L6 分叉的口径分量持续存在）。

改动点：
- `fidelity_config.py`：`fidelity_eps_basis` 默认值 `'passthrough'` → `'basic'`（含 docstring 同步）；
- `source_import.py`：`self._fidelity_eps_basis` 默认传递链同步；
- **不改动** `_QS_FIDELITY_EPS_EXT` 模板本体（已就绪，激活即生效）。

**兼容性**：已有产物（未含 FIDELITY_EPS 块）重转后自动获得 basic 映射；`passthrough` 仍可显式指定。

**PIT 巡检报告**（C2② 收尾）：`scripts/pit_inspection_report.py`（只读脚本，输出本地 vs 平台 PIT 截面的 EPS 覆盖差/口径差/回填状态摘要——消费 P-A3 的 `check_eps_backfill_gap` 与 C2 Phase 0 巡检结果）。

### 2.3 C3：分位窗口审计行 + 复权快照巡检

**C3a. get_history 窗口审计行**（双端同构）：

本地 `ptrade_api.get_history()` 返回前追加（`log.debug`）：
```python
log.debug("QS_HISTORY_WINDOW code=%s count=%d actual=%d first=%s last=%s",
          code, count, len(df), first_date, last_date)
```

转换模板 `_QS_HISTORY_WINDOW_EXT`（门控：策略调 `get_history` 时注入）同构。

**审计行设计**：`log.info` 级（非 debug——定位器需在平台日志中直接可见）；仅在 `count` 参数 ≥ 100 时输出（避免高频噪声）。

**C3b. 复权快照巡检**（只读脚本）：`scripts/fq_snapshot_check.py`——对比本地 vs 平台同一股票同日的前复权收盘价差（需平台探针配合采集 3~5 只代表股的 fq='pre' 序列——**探针需求登记**，与 P-POS-2 可同场执行）。

### 2.4 C4：停牌/退市保真开关 + 下单前状态校验

**⚠️ 实施期范围调整（2026-08-27）**：C4a/C4b 目标文件 `backtest_engine.py` 当前有他线 11 个未提交 hunk（L429-718 密集覆盖 `_immediate_execute` 与日循环区域）——混叠风险过高。**裁定：C4a/C4b 拆出为 P-D13b，待 backtest_engine 他线收敛后独立实施**。本 WP-C 仅实施 C4c（转换模板退市校验——source_import.py 干净无冲突）。

**C4c. 下单前退市状态校验**（转换模板，只告警不拦截）：

`_QS_ORDER_SPLIT_EXT` 的 `order_target_value` 包装内，下单前调 `get_stock_status([code], query_type='DELISTING')` → 若已退市则 `log.warning('QS_DELIST_ORDER code=%s status=delisted')` 后**继续下单**（平台数据矛盾的检测器，不吞单）。

**P-D13b（排队，不在本 WP）**：
- C4a `fidelity_halt_reject`（停牌撤单保真开关）
- C4b `fidelity_delist_force_close`（退市强平保真开关）
- 前置条件：backtest_engine.py 他线 11 hunk 收敛（合并基线重验前）

### 2.5 涉及文件

| 文件 | 改动 | 项 |
|---|---|---|
| `quantstudio/backtest/ptrade_api.py` | get_Ashares 板块统计 + exclude_bse 参数 + get_history 窗口审计行 | C1a/C1b/C3a |
| `quantstudio/backtest/fidelity_config.py` | fidelity_eps_basis 默认→'basic' | C2 |
| `quantstudio/strategy_compiler/source_import.py` | _QS_ASHARES_BREAKDOWN_EXT + _QS_HISTORY_WINDOW_EXT + _QS_DELIST_ORDER 校验 + exclude_bse 烘焙 | C1a/C1b/C3a/C4c |
| `quantstudio/strategy_compiler/cli.py` | --exclude-bse 旗标 | C1b |
| `scripts/pit_inspection_report.py`（新） | PIT 巡检报告 | C2② |
| `scripts/fq_snapshot_check.py`（新） | 复权快照巡检 | C3b |
| `tests/test_pd13_data_alignment.py`（新） | 测试矩阵 | 全部 |
| ~~`backtest_engine.py`~~ | **拆出至 P-D13b**（他线 11 hunk 冲突） | ~~C4a/C4b~~ |

## 3. 关键设计决策（审计要点）

| # | 决策 | 理由 |
|---|---|---|
| D1 | C1b `exclude_bse` 默认 `False`（本地保持含北交所） | P-D9 纪律：本地语义权威；对齐验证需显式 opt-in |
| D2 | C2 `fidelity_eps_basis` 默认改 `'basic'`（对齐默认） | 探针三实证逐位一致；passthrough 容忍加权差 = L6 分叉持续。与 D1 方向不同——**eps 是口径映射（不是数据范围）**，映射后策略消费的列名/数值与本地一致，无语义损失 |
| D3 | C3a 审计行 `log.info` 级 + count≥100 才输出 | 定位器需直接可见；低频防噪声 |
| D4 | C4a/C4b 保真开关默认 `False` | master-plan 纪律：不默认改变本地引擎行为 |
| D5 | C4c 退市校验只告警不拦截 | 平台数据矛盾的检测器（不吞单——平台自身可能矛盾，策略按平台行为继续） |
| D6 | C1c 仅平台 16 只登记不修 | 数据源覆盖差非转换层问题；移交数据管线 |
| D7 | C2 不重复 P-A3 回填管线 | 4430788 已关闭 EPS 回填；C2 仅做激活决策 + 巡检报告 |
| D8 | D2 默认变更行为影响评估 | passthrough→basic 改变产物运行时行为（eps 列数值从加权变 basic）——**golden 影响需合并基线重验覆盖**（与 P-A3/B2/D3 统一重验） |

## 4. 影响面

### 4.1 受益策略

| 策略 | C1（宇宙差） | C2（eps 口径） | C3（窗口审计） | C4（停牌/退市） |
|---|---|---|---|---|
| weekly/周频 | --exclude-bse 后 L0 两端一致 | L6 eps>0 口径一致 | 窗口可见 | — |
| CANSLIM | 同上 | C/A 因子 eps 口径一致 | 同上 | — |
| fall_reversal | 同上 | — | — | halt/delist 开关模拟平台行为 |
| vol_regime | — | — | **q 分位窗口两端可见定位** | — |
| tech_etf | — | — | 评分窗口可见 + fq 巡检 | — |

### 4.2 行为变化

- **D2（eps basic 默认）**：产物运行时 eps 数值从平台加权口径变为 basic（== 本地）——**合并基线重验覆盖**；
- 其余全部默认关/只增审计行——零行为变化。

## 5. 测试矩阵（tests/test_pd13_data_alignment.py）

| 用例 | 场景 | 断言 |
|---|---|---|
| T1 | get_Ashares 板块统计 | log 输出 bse/non_bse 计数 |
| T2 | get_Ashares(exclude_bse=True) | 920xxx 全滤 |
| T3 | get_Ashares(exclude_bse=False) 默认 | 全量含北交所 |
| T4 | 转换模板含 _QS_ASHARES_BREAKDOWN | 产物含审计行代码 |
| T5 | fidelity_eps_basis='basic' 默认 | 产物含 _QS_FIDELITY_EPS 块（eps→basic_eps 映射激活） |
| T6 | fidelity_eps_basis='passthrough' 显式 | 产物不含 FIDELITY 块（向后兼容） |
| T7 | get_history 窗口审计行（count≥100） | log.info 含 QS_HISTORY_WINDOW |
| T8 | get_history 窗口审计行（count<100） | 不输出（防噪声） |
| T9 | fidelity_halt_reject=True | _immediate_execute 对 suspended 标的拒单 reason='halted' |
| T10 | fidelity_halt_reject=False 默认 | 不拒单（日线无 halted 约束现状保持） |
| T11 | fidelity_delist_force_close=True | 日循环对无行情持仓强平 + 审计行 |
| T12 | fidelity_delist_force_close=False 默认 | 不强平 |
| T13 | 退市校验告警 | 模板内 get_stock_status 检测 + QS_DELIST_ORDER log.warning + 不拦截 |
| T14 | PIT 巡检脚本 | 输出非空报告（消费 check_eps_backfill_gap） |

## 6. 验收标准

1. **单元**：T1~T14 全绿；
2. **回归**：全量套件除已知存量红外零新增；
3. **6 策略重转**：api_portability 全 PASS；
4. **对齐验证**（用户平台执行）：--exclude-bse + eps=basic 后 CANSLIM/weekly 的 FUNNEL L0/L6 两端计数一致（差 ≤ 数据源边缘）；
5. **合并基线重验**：D2 行为变化纳入统一重验（P-A3 + B2 + D3）。

## 7. 回退条件

- stash create -u 回退点；
- D2 默认变更单独可逆（改回 'passthrough' 一行）；
- 保真开关默认关——回退 = config 默认值恢复。

## 8. 明确不做

- 不改 P-A3 回填管线（已关闭）；
- 不改数据源覆盖（C1c 仅平台 16 只移交数据管线）；
- 不默认改变本地引擎撮合/估值语义（C4 保真开关 opt-in）；
- 不修 ptrade 平台显示 bug。
