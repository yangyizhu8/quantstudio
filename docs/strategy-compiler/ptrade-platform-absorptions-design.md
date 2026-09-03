# PTrade 平台吸收修复设计：get_Ashares exclude_bse 调用点剥离 + set_commission 值域下限

> 状态：**实施版（2026-09-03 审计通过；验收改详见证据 docs/evidence/ptrade-platform-absorptions-20260903.md）** ｜ 归属：quantstudio-strategy-compiler 转换管线（source_import）
> ⚠️ 验收修订（实证驱动）：「重转产物本地三件套逐位一致」不成立（转换产物=平台形态+注入包装，见证据 §四）；等价性修订为断言集+api_portability+平台实证+已知差异登记。
> 📌 卷入追认：本方案 source_import R1/R2 改动被其他会话提交 `0321364`（pctChg v6-v9.1）整体卷入并推送（origin/main 同步，2026-09-03）；审计独立核验内容正确，**事后追认**（证据 §9.3；AGENTS.md 已加共享核心文件提交纪律防重演）。
> 相关铁律：策略生成与转换全链路修复仅限框架层（策略源码与转换产物零改动）；框架层改动六步流水线；框架问题立即解决

## 1. 问题定义

真实 PTrade 平台回测（2026-09-03，转换产物「测试1」= SG-MS-PEG-HL 策略转换版）两处平台失败：

| # | 平台错误 | 触发行 | 平台侧实况 |
|---|---|---|---|
| A | `IQInvalidArgument: 佣金费率和最低交易佣金不能小于或者等于0` | `set_commission(commission_ratio=0.0003, min_commission=0)` | IQEngine arg_checker 对 `set_commission` 要求 **commission_rate>0 且 min_commission>0**；min_commission=0 非法 |
| B | `TypeError: get_Ashares() got an unexpected keyword argument 'exclude_bse'` | `get_Ashares(exclude_bse=_EXCLUDE_BSE)` | 平台 `get_Ashares` 仅接受 `date`（注册表 allowed_keywords=["date"]）；`exclude_bse` 是本地扩展参数（P-D13 C1b），转换产物中未被剥离 |

两处均为**转换管线未吸收的平台差异**，非策略语义问题。根因已定位（见 §2），非猜测。

## 2. 根因（代码取证）

### 根因 A：set_commission 参数原样直通

- 转换管线对 set_commission 仅做「set_backtest 函数体内联保执行时机」（source_import.py:2768-2807 区域），**参数不改写**；
- 注册表 `ptrade-api-signatures.json` set_commission 仅登记关键字 `[commission_ratio, min_commission, type]`，**无值域约束**；
- F-2 客户确认的本地语义 `min_commission=0`（纯费率万3，无最低佣金）在平台数学上不可表达 → 需转换层吸收为平台可表达下限。
- 全仓旁证：其余策略 min_commission 均为 0.1/0.5/5.0（>0），仅本策略为 0——值域吸收规则缺位是唯一缺口。

### 根因 B：get_Ashares 调用点只改 date、不剥 exclude_bse

- `_normalize_ptrade_contract_calls`（source_import.py:3024）对 `get_Ashares` 仅调用 `_rewrite_asharess_date`（3091，只改日期格式 YYYY-MM-DD→YYYYmmdd），**无 exclude_bse 剥离规则**；
- P-D13 注入的包装 shim（2358）签名 `def get_Ashares(date=None)` **不接受 exclude_bse**——设计前提是「调用点已无 kwarg」，但该前提从未被转换器保证；
- 即便不注入 shim，平台原生 get_Ashares 同样拒绝该 kwarg → 两条路径同归于 TypeError。

## 3. 改动范围（仅框架层，策略源码零改动）

落地文件：`quantstudio/strategy_compiler/source_import.py`（转换管线）；配套注册表/校验器（防复发）；文档同步。

### 3.1 调用点吸收规则（_normalize_ptrade_contract_calls 内新增）

**规则 R1（get_Ashares exclude_bse 剥离 + 语义烘焙）**
- `exclude_bse` kwarg 从调用点剥离（重写为无该 kwarg 的调用）；
- 北交所过滤语义 **从源策略值烘焙**（P-D9 本地语义权威）：解析调用点常量或模块常量（如 `_EXCLUDE_BSE = True`）→ 注入 `_QS_EXCLUDE_BSE = True`（沿用现有 P-D13 注入通道 3595-3601，`exclude_bse` 参数改为「源值推导，CLI --exclude-bse 显式覆盖」）；
- 审计行：NORMALIZE / NORM-ASHARES-EXCLUDE_BSE（WARN，注明剥离原因 + 烘焙值）。

**规则 R2（set_commission 值域下限吸收）**
- `min_commission` 为常量且 ≤0 → 改平台可表达下限 **0.01**（≈ 无最低佣金，经济影响 <0.01 元/笔，忠实 F-2 语义）；非零常量不变（pure-gain）；
- 非常量表达式 → 包装 `max(expr, 0.01)`（运行期安全，任意表达式正确）；
- `commission_ratio` 常量 ≤0 → 同理下限 1e-6（对称防护；正常策略不受影响）；
- 审计行：NORMALIZE / NORM-COMMISSION-MIN-FLOOR（WARN，注明平台约束 + 与原值差异）。

### 3.2 校验器防复发（防 PTrade 直产源码再犯）

- `validate_ptrade_portability.py`（或校验器等价入口）：PTrade 目标源码中，`set_commission` 常量 `min_commission≤0` / `get_Ashares(exclude_bse=...)` → BLOCK + 指引「转换管线自动吸收；PTrade 直产请用平台契约形态」；
- 本地专用策略（targets=["quantstudio"]）不受影响（合法使用本地语义）。

### 3.3 注册表与文档同步（六步第 6 步推送时执行）

- `ptrade-api-signatures.json`：set_commission 增补值域 note（min_commission 平台要求 >0，转换下限 0.01）；
- README + docs/strategy_toolbox.md + docs/prompt_engineering.md 相关表述同步。

## 4. 影响面

- **已发布本地策略文件：零改动**（SG-MS-PEG-HL 及其发布哈希不变，R4/R5 证据不失效）；
- 现有转换产物：仅含上述模式的策略被改写（全仓仅本策略含 min_commission=0；exclude_bse 亦仅本策略）——其余策略转换产物**逐字节不变**（回归黄金）；
- 新增审计行/常量不影响交易语义（BSE 过滤值忠实源语义；佣金下限仅对 ≤0 生效）。

## 5. 验收标准

1. **单元测试**（tests/test_source_import.py 新增）：①`get_Ashares(exclude_bse=True)` 常量 → 产物调用点无 kwarg + `_QS_EXCLUDE_BSE = True`；②模块常量 `_EXCLUDE_BSE=True` 引用 → 同解；③`set_commission(..., min_commission=0)` → `min_commission=0.01` + 审计；④`min_commission=5.0` 等非零值不变（pure-gain 黄金）；⑤既有转换策略产物逐字节不变。
2. **多策略横验证**（铁律）：6 策略（CANSLIM / fall_reversal / tech_etf_mvo_rotation / vol_regime_mom_rev / weekly_smallcap_growth / 周频小市值成长动量（三层止损））重转 api_portability 全 PASS。
3. **SG-MS-PEG-HL 重转验收**：产物无 `get_Ashares(exclude_bse=`、无 `min_commission=0`、`_QS_EXCLUDE_BSE = True`、审计行齐全且为 WARN 记录；**本地等价性**——重转产物在本地引擎同一窗口重跑，config/daily_stats/trades 三件套 SHA-256 与现有 R5（run1）逐位一致。
4. **相关测试套件全绿**：test_source_import / test_ptrade_contract_compliance / test_pr6b1_install_skill 等。
5. **平台实证（用户侧）**：重转后上传 PTrade，initialize 与 before_trading_start 不再报两类错误。

## 6. 回退条件

- source_import.py 单文件回退（git 基线恢复）；未触碰策略源码与已发布产物；
- 本地校验器规则为新增 BLOCK 规则，可独立回退而不影响本地专用策略；
- 失败即回退并记录，不越过验收在未确认状态下推送。

## 7. 实施路径（审计通过后）

1. 按 §3 修改 source_import.py（R1/R2）+ §3.2 校验器；
2. 跑 §5 验收全套（单元 + 横验证 + 重转等价性 + 套件回归）；
3. 验收证据写入 docs/evidence/；
4. 用户确认 → 双仓库推送（README + toolbox + prompt_engineering 同步）。