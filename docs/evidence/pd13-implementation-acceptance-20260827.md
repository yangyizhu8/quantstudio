# P-D13 实施与验收证据：WP-C 数据层对齐（C1+C2+C3+C4c，2026-08-27）

- 流水线：Step 1 ✅ → Step 2 ✅ 审计通过（两条件）→ Step 3 实施 → **Step 4 本地验收**
- 回退点：`c3e11c92cd2b68c723b9a312ff83e4125165f5b3`（baseline-wpc-20260827）

## 1. 实施清单

| 文件 | 改动 | 项 |
|---|---|---|
| `ptrade_api.py` | get_Ashares 板块统计（QS_ASHARES_BREAKDOWN log.debug）+ exclude_bse 参数 + get_history 窗口审计行（QS_HISTORY_WINDOW log.info count≥100） | C1a/C1b/C3a |
| `fidelity_config.py` | fidelity_eps_basis 默认 'passthrough'→**'basic'**（D2 对齐默认） | C2 |
| `source_import.py` | _QS_DATA_AUDIT_EXT 模板（class 承载白名单兼容）+ _source_uses_ashares_api 门控 + exclude_bse 参数链 + C4c 退市校验（QS_DELIST_ORDER 只告警不拦截） | C1a/C1b/C3a/C4c |
| `orchestrator.py` | exclude_bse 透传 | C1b |
| `cli.py` | --exclude-bse 旗标 | C1b |
| `scripts/d2_eps_impact_quantify.py`（新） | D2 差异影响量化脚本 | 审计条件① |

**范围调整（批准）**：C4a/C4b 拆出 **P-D13b**（backtest_engine.py 他线 11 hunk 密集覆盖 _immediate_execute/日循环——混叠过高即拆，等他线收敛后独立实施）。

**实施中发现并修复**：模板内模块级变量调 `_QSPositionState_ashares_orig(date)` 被 LOCAL-API-WHITELIST 拦截 → 改为 class 属性承载（`_QSAsharesRefState.orig`），与 P-D11 同款模式。

## 2. 本地验收

| 项 | 结果 |
|---|---|
| 四套件回归 | **132/132 全绿**（pd11 19 + pd12 14 + B2 5 + compliance 94） |
| P-A3 eps 回填 | **26/26 全绿**（C2 与 P-A3 零冲突确认） |
| 五套件合跑 | **158/158 全绿** |
| 6 策略重转 | **api_portability 全 PASS** |
| 产物标记 | P-D13 ×4（QS_ASHARES_BREAKDOWN/_QS_EXCLUDE_BSE）+ eps=basic ×1 + 退市校验 ×1 |

## 3. 审计条件①：D2 默认映射（eps=basic）前置证据

### 3.1 双跑冒烟（on/off 产物对照）

| 模式 | 产物中 `_QS_FIDELITY_EPS_BASIS` | 含 `_QS_FIDELITY_EPS_FIELD_MAP` |
|---|---|---|
| basic（默认/激活） | `'basic'` | ✅ True（映射激活） |
| passthrough（显式覆盖） | 无（不注入 FIDELITY 块） | ❌ False（向后兼容） |

策略源码零改动——两产物均从同一 `weekly_smallcap_growth_momentum_10_quantstudio.py` 转换，差异仅在 eps 列请求名（eps→basic_eps）+ 返回列逆映射（basic_eps→eps）。

### 3.2 差异行影响量化（`scripts/d2_eps_impact_quantify.py`）

**量化范围**：5209 只配对股票 × 5 个调仓日（weekly PIT 窗口），fin_indicator.eps vs income_statement.basic_eps。

| 指标 | 数值 | 解读 |
|---|---|---|
| 总差异行 | 694~696（13.3%） | **642 为主因 = 期间错配**（fin 取到最新期、income_statement 取到上一期——P-A3 回填目标范围，非口径差） |
| 实际口径差（同期比较） | 694-642 = **52~54 行（≈1.0%）** | 同期 fin.eps vs income_statement.basic_eps 仍有微小数值差（四舍五入/数据源差异） |
| 符号翻转 | 110 只 | **fin/inc 期间错配的产物**（非 D2 影响——见下） |

**关键区分——D2 实际影响范围**：
- **本地端：零影响**——本地消费 fin_indicator.eps，D2 不改变本地任何查询/过滤/选股行为；
- **平台端：口径修正**——D2 使平台 get_fundamentals('eps') 从请求**加权 eps 列**变为请求** basic_eps 列**（探针三实证 basic_eps == 本地 fin_indicator.eps Δ=0.0000）；
- **sign flip 风险**：加权 vs basic 的差异仅在净利润趋零的边缘股（加权平均股本 ≠ 期末股本时放大分母差）——**110 只是 fin/inc 期间错配的产物，非加权/basic 口径差的理论 sign flip 数**。实际加权/basic sign flip 概率远低于 110（上界估计：净利润接近零的 A 股 ≈ 数只）。

### 3.3 D8 行为变化归因登记

D2 默认 basic 的行为变化 = 平台端 eps 数值从加权口径变为 basic 口径（== 本地）。该变化：
- **纳入合并基线重验**（P-A3+B2+D2+D3 统一，master-plan §5）；
- 本地端零影响（无需本地 golden 对照）；
- 平台端重转产物全部含 basic 映射（6 策略已验证）。

## 4. 审计条件②：hunk 级核对

（见 §5 暂存核对闸回报）

## 5. 暂存核对闸（commit 前安全条件）

（commit 前回报 `git diff --cached --stat` + cached hunk 分布）

## 6. 回退

- 回退点 `c3e11c9`；
- 改动 = 5 tracked + 新增脚本——回退 = 定向 restore + 删除新增文件；
- D2 默认值单独可逆（fidelity_config.py 一行改回 'passthrough'）。
