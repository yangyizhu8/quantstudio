# get_fundamentals 平台 date=None 拼接前日 PIT 修复设计（转换管线 wrapper 层）

> 状态：**实施版（2026-09-03 审计通过；验收见 docs/evidence/ptrade-platform-absorptions-20260903.md §十一、
> §十四~§十八（平台复验后续：报表 range 路由 / 白名单全深度 / 500 分块 / 数值优先 PIT+8h 报告期 /
> valuation 运行时判型映射 + turnover_rate 直映，2026-09-04 第八轮平台首次出交易））**
> 铁律：全链路修复仅限框架层（策略源码与转换产物零改动）；六步流水线；根因未证实不得修（本方案根因已取双端对照证据，见 §2）

## 1. 问题定义

平台回测（2026-09-03 16:10，「测试1」= SG-MS-PEG-HL 转换产物上传平台）：
- 前两处平台差异（min_commission、exclude_bse）已消除 ✓
- **新增功能失败**：`QS_FUNNEL_AUDIT ... P2_income_codes=0` —— 转换产物 `get_fundamentals(table='income_statement')`
  在平台返回**空** → 4900 只尽数出局 → 无持仓、无交易。
- 平台日志特征：`QS_GF_CALL n=1 table=income_statement date=None secs=800`（首调即 date=None 的 800 码
  list 直发）。

## 2. 根因（双端对照实证，非猜测）

| 端 | 同调用形状结果 | 证据 |
|---|---|---|
| 本地引擎（转换产物本地重跑） | `P2_income_codes=3454` ✅ | equiv_run 2021-01-04 funnel 行 |
| 平台 | `P2_income_codes=0` ❌ | 平台 2026-07-01 funnel 行 |

分派链路（source_import.py wrapper，已取证）：
- 本策略 `_fetch_statement_rows` 调 `get_fundamentals(batch, 'income_statement', fields=..., report_types='4')`
  **无 date** → wrapper `date=None`；
- `_qs_gf_maybe_prefetch`（B9/B10）**要求 date 非 None**（L2102 `if not _pool or date is None: return`）→
  本调用跳过预取，直发 `_QSFundState.orig(_secs, table, fields=_plat_fields, date=None, ...)`（L1791）；
- 平台契约知识（P-D10/B6 实证，README 登记）：平台 `get_fundamentals` **list 批量仅为 date 形态探针实证**
  （income/valuation 500 码 0.05s）；**date=None 形态的 income_statement list 调用无实证、实测返回空**；
  本地 `get_fundamentals(date=None)` 内建 `prev_date` 兜底，故本地正常。

**结论（高置信）**：平台 income_statement 经 get_fundamentals 需显式 PIT `date`；转换产物 date=None 直发
落入未实证形态 → 空返回。修复必须为 wrapper 层补齐与本地一致的前一交易日 PIT 语义。

## 3. 改动范围（框架层，策略源码与转换产物零改动）

落地：`source_import.py` 注入的 get_fundamentals wrapper（`_QS_GF_WRAPPER` 主分支，L1775 区间前）。

### 3.1 wrapper 日期拼接（核心）

- 主分支入口：`date is None` 且 `table` ∈ 财务/估值表白名单
  `{income_statement, balance_statement, cashflow_statement, valuation, eps, profit_ability, growth_ability}`
  → 合成 `_eff_date = _qs_prev_trade_day_str()`，平台调用与后续字段契约/形状处理全部使用 `_eff_date`
  （即进入**已探针实证的 date 形态 list 路径**）；
- `_qs_prev_trade_day_str()`（新增注入 helper）：① `_QS_RUNTIME_CTX.previous_date`（D4-S7 捕获，平台标准
  属性）→ ② `context.current_dt` 减 1 日再经 `get_trade_days` 前向对齐 → ③ `_qs_today_str()` 减 1 日 →
  ④ 失败返回 `None`（回退原 date=None 行为，fail-open 不阻断）；
- 审计行：`QS_GF_DATE_SYNTH table=%s prev=%s（date=None → 前日 PIT，对齐本地 prev_date 语义）`，
  每表每月首调打一行（沿用 `_qs_gf_progress` 节流寄存器）；
- 显式传 `date` 的调用**零影响**（合成仅在 date=None 且表在白名单时触发）→ date 形态既有策略产物逐字节不变（纯增益）。

### 3.2 范围与边界

| 场景 | 行为 |
|---|---|
| 策略无 date + 白名单表（本策略 income_statement/profit_ability/valuation） | 拼接前日 PIT → 平台 date 形态 list 路径 |
| 调用方显式 date（既有 fscore/成长类策略） | 不触发，行为与字节不变 |
| 非白名单表（如 cashflow/operating 未用） | 不触发（保守，防既有行为漂移） |
| 合成失败（无 ctx/today 均不可得） | 回退原 date=None + WARN（fail-open） |
| 单码与 list 形态 | 统一生效（wrapper 主分支单点） |

### 3.3 校验器/注册表

- 无需新 BLOCK（调用形态未变，仅 wrapper 内部补语义）；`ptrade-api-signatures.json` get_fundamentals 条目
  补一条 note（date=None → 平台需显式前日 PIT，转换 wrapper 已拼接）。

## 4. 影响面

- 已发布产品：转换产物重转后行为变化（date=None 财务调用从「平台空返回」→「前日 PIT 有效返回」）——
  这是修复本身，方向正确；显式 date 调用零变化；
- 本地引擎：零变更（wrapper 仅存在于转换产物；本地原始策略路径不动）；
- 其他会话 WIP：本改动落在 wrapper 模板段（source_import.py），与 pctChg/get_history 段无交集；
  提交走 AGENTS.md 新增「共享核心文件提交纪律」。

## 5. 验收标准

1. 重转 SG-MS-PEG-HL → wrapper 含 `_qs_prev_trade_day_str` + `QS_GF_DATE_SYNTH`；
2. 转换产物**本地**重跑：funnel P2_income_codes ≥ 3000（date 拼接后本地亦走 date 形态路径，结果仍一致
   ——拼接前日 == 本地 prev_date，年同日历故数值不变）；6 策略横验证 api_portability 全 PASS；
3. **平台重跑（用户侧）**：P2_income_codes > 0、三维度正常、出交易与净值——本方案判定依据；
4. 回归：test_source_import 全绿（新增 1 用例：date=None 白名单表调用产物含 `_qs_prev_trade_day_str`、
   显式 date 调用产物不含合成分支触发标记）；业界基准对比（纯增益：date 形态产物逐字节不变用
   既有黄金锚点策略覆盖）；
5. 证据落 docs/evidence/（双份存储 + ~/.dsh/backup/）。

## 6. 回退条件

- wrapper 模板段单点 revert（source_import.py injection 块）；策略源码/产物零触碰；
- 拼接仅在 date=None + 白名单表触发，回退后显式 date 策略无任何变化。

## 7. 实施路径（审计通过后）

1. 新增 `_qs_prev_trade_day_str` helper + 主分支拼接；2. 新增测试/回归；3. 重转 + 本地功能验收 +
   横验证；4. 证据落盘；5. 用户确认 → 重转上传平台实证 → 双仓推送（README/toolbox/prompt_engineering 同步）。

## 8. 平台实证交互

本方案「平台端 P2>0」为最终判定依据——实施后需用户重转上传平台一次回测（一轮），已纳入验收 §5.3。