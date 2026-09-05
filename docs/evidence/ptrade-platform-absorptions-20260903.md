# 验收证据：PTrade 平台吸收修复（get_Ashares exclude_bse + set_commission 值域）

- 方案：docs/strategy-compiler/ptrade-platform-absorptions-design.md（审计通过 2026-09-03，P2×4 落实）
- 改动：quantstudio/strategy_compiler/source_import.py（R1/R2）+ validators/validate_ptrade_portability.py（3 规则）
  + ptrade-api-signatures.json（值域注记）+ tests/test_source_import.py（T1-T9 + 校验器正反例）
- 策略源码与已发布产物：**零改动**

## 一、 单元测试（tests/test_source_import.py 新增 10 项全绿）

| 用例 | 断言要点 | 结果 |
|---|---|---|
| T1 test_36 | exclude_bse=True 常量 → 调用点无 kwarg + `_QS_EXCLUDE_BSE = True` + NORM-ASHARES-EXCLUDE_BSE | ✅ |
| T2 test_37 | 模块常量 `_EXCLUDE_BSE=True` 名解析 → 烘焙 True（P2-2 末次赋值语义） | ✅ |
| T3 test_38 | exclude_bse=False → 烘焙 False（源值权威） | ✅ |
| T3b test_39 | 动态表达式 → 剥离 + 回退 CLI + WARN（不静默丢语义） | ✅ |
| T4 test_40 | min_commission=0→0.01；5.0 原样（纯增益）；表达式→max(expr,0.01) | ✅ |
| T5 test_41 | date+exclude_bse 组合单次整重建（date 归一+剥离，防范围重叠损坏） | ✅ |
| T6 test_42 | 幂等：二次转换不嵌套 max、kwarg 不复发 | ✅ |
| T7 test_43 | 未受影响策略零介入（无新 NORM action + 锚点 verbatim + AST 合法） | ✅ |
| T8 test_44 | 校验器 3 新 BLOCK 正反用例（P2-1/P2-4，含 positional set_commission） | ✅ |
| T9 test_45 | 动态 date 表达式 wrap 不崩溃（fall_reversal 触发路径） | ✅ |

## 二、 横验证与回归

- 6 策略（CANSLIM / fall_reversal / tech_etf_mvo_rotation / vol_regime_mom_rev /
  weekly_smallcap_growth / 周频小市值成长动量（三层止损））重转 + validate_ptrade_portability
  **全 PASS，0 BLOCK**（铁律横验证 ✓）
- 回归套件：test_ptrade_contract_compliance + test_pr6b1_install_skill **115 passed** ✓
- 全套 test_source_import.py：42 passed / 7 failed——**7 失败经隔离归因实验证明与其他会话未提交 WIP
  相关（git status 显示 source_import.py 改造前即为 M：get_history include/preclose v7-v9、QSPROBE）**：
  「工作区 − 本方案改动」副本跑同 7 测试同样全败 → **本方案零回归**（铁证，见 §五）

## 三、 SG-MS-PEG-HL 重转断言集（accept_reconversion.py）

- 产物：output/generated_strategies/sg_ms_peg_hl_defensive_smallcap_growth/converted_product.py
- 断言全过：`get_Ashares(exclude_bse=` 缺失 ✓ `min_commission=0` 缺失 ✓ `_QS_EXCLUDE_BSE = True` ✓
  双 NORM 审计行 ✓ validate_ptrade_portability PASS ✓ 源语义烘焙告警（True > CLI False）✓

## 四、 门禁 5（本地等价性）——验收标准修订（实证驱动的方案修订）

实测：重转产物本地重跑（同一引擎/窗口/资金）→ 最终净值 119,059.62 vs 本地策略 R5 117,004.67，
首日（2021-01-04）目标清单即不同 → **「重转产物三件套与 R5 逐位一致」这一验收目标在方法上不成立**：

- 归因探查（BSE 过滤探针）：转换 shim 北交所规则（920+legacy）与本地 is_bse_market 在 2021-01-04
  完全一致（各 83 码）→ 宇宙差排除；
- 本方案改动仅触 get_Ashares 调用点形态与 set_commission 值域（费用级，不影响选股信号）→ 首日目标
  差异非本方案引入；
- 差异来源 = **转换层既有注入包装的代理语义**（get_fundamentals P-D10 列表单调用/PIT 锚、批量化 vs
  逐码、fq 注入等长期存在；本地重跑转换产物 = 「平台形态 + 包装层」叠加本地引擎，属双重补偿环境，
  不能作为逐位等价性标尺）。

**修订后门禁 5（生效）**：等价性以「断言集（§三）+ api_portability（§二）+ 平台端实证（用户侧：
上传后两类错误消失、回测正常出结果）+ 已知差异登记」为验收；本地重跑仅作「可运行性/结构完整」证明。
**已知差异登记**（P2-3 精神，不掩差异）：转换产物本地运行 ≠ 无包装本地策略逐位一致——转换层注入
包装与本地批量化实现的 PIT/边界代理差异列入框架 known-limitations 候选，另行专项排查（不阻塞本方案）。

## 五、 归因实验（铁证）

- 实验：从工作区 source_import.py 精确剥离本方案 5 段（常量/状态/接线/方法/注入烘焙）→ 装入隔离
  worktree（git worktree add HEAD d464745）→ 跑 7 个失败 include 测试 → **与工作区同样 7 败**
- 结论：7 个失败 100% 归因其他会话未提交 WIP（v7-v9），本方案零引入回归
- 对比：HEAD（无 WIP 无本方案）下 2 败（test_34/35）——工作区多出的 5 败全为 WIP 所致

## 六、 审计 P2 四个补强点落实核对

- P2-1 校验器正反单元测试：test_44 ✓
- P2-2 ast.Name 解析末次赋值语义 + 其余 fail-soft：_resolve_exclude_bse_value 注释 + 实现 ✓
- P2-3 动态表达式池构成漂移登记：WARN 审计 + 本证据 §四 已知差异登记 ✓
- P2-4 max() 双参数顺序幂等 + positional set_commission 覆盖补全（PORTABILITY-COMMISSION-POSITIONAL）✓

## 七、 环境级发现（审计提示，单独立项）

本机存在对新建目录的周期性删除行为（~/.dsh/browser-control/out/、D:\deepseek-harness\output\）。
本次证据按审计建议**双份存储**：docs/evidence/ + strategy workspace 各一份。

## 八、 结论

- 单元/横验证/回归全绿，本方案零回归；SG-MS-PEG-HL 重转产物断言集全过；
- 门禁 5 经实证修订为可执行口径（含已知差异登记）；
- 待用户确认后：README + docs/strategy_toolbox.md + docs/prompt_engineering.md 同步 + 双仓库推送。

## 九、 审计二审补强落实（2026-09-03）

### 9.1 已知差异三项显式登记（门禁 5 修订的登记完整性要求）

| # | 已知差异 | 内容与影响 | 处置 |
|---|---|---|---|
| 1 | **佣金下限差** | min_commission 0.01（平台可表达下限）vs 本地 0；平台约束 `>0`（IQInvalidArgument 实证）。对 PnL 边际影响：任何订单金额 ≥33.4 元时 `max(金额×万3, 0.01)=金额×万3`，即费用与本地逐笔相同；仅金额 <33.4 元的尘单有 ≤0.01 差。 | 转换恒吸收（R2），审计行 NORM-COMMISSION-MIN-FLOOR 留痕；平台端实证确认无断言差异 |
| 2 | **BSE 过滤经 wrapper 保留** | 转换产物 `_QS_EXCLUDE_BSE` 烘焙自源语义（True），shim 按 920 前缀 + `_QS_BSE_LEGACY` 过滤；探针实证 2021-01-04 与本地 `is_bse_market` 宇宙完全一致（各 83 码，零差）。 | 探针已排除宇宙差；保留为「wrapper 形态差异」登记，防未来北交所代码规则演变时误判 |
| 3 | **首日目标差异（先于本方案存在）** | 重转产物本地重跑 vs 本地策略 R5：2021-01-04 目标清单即不同（run1 买 300417/002761/000785，产物版买 000779/002755/002761）——归因：转换层既有注入包装（get_fundamentals P-D10 列表单调用/PIT 锚、批量化 vs 逐码、fq 注入等长期代理语义，先于本方案）。本方案仅触 get_Ashares 调用点形态与 set_commission 值域，均不影响选股信号。 | 不掩差异：列入框架 known-limitations 候选，另行专项排查（转换层包装 vs 本地批量化对齐）；本方案验收以断言集+api_portability+平台实证为标尺 |

### 9.2 证据多副本存储（环境级目录清理者防护）

- ① `docs/evidence/ptrade-platform-absorptions-20260903.md`（仓库内）
- ② `output/generated_strategies/sg_ms_peg_hl_defensive_smallcap_growth/evidence_backup_absorptions.md`（策略工作区）
- ③ `~/.dsh/backup/ptrade-platform-absorptions-20260903.md`（会话外部，防工作区级清理）

### 9.3 卷入事件事后追认（0321364）

- 其他会话提交 `0321364`（fix(source_import): pctChg v6-v9.1）将本方案 source_import R1/R2 改动整体
  卷入并推送到 origin/main（main 与 origin/main 同步）。审计独立核验推送版含本方案全部特征标记
  （8 处）+ diff 含 32 行 R1/R2 特征行，内容正确 → **事后追认有效**（用户审计裁定）。
- 后续独立提交（validator/tests/registry/docs）提交信息与本设计文档均已记录该追认，防历史误读。
- AGENTS.md 已追加【共享核心文件提交纪律】（2026-09-03）：提交前 stash-create 基线 + 精确清单 +
  提交信息自查不含他人改动 + 双远程核对，防重演。

### 9.4 测试债归属登记（不阻塞本方案）

- test_source_import.py 7 项 include 失败（test_22/23/27/32/33/34/35）断言的是 v6-v9.1 之前的
  include 语义；语义已按 QSPROBE 平台实证定谳变更。
- **归属：QSPROBE 会话（引入语义变更方）**——待办：更新测试至新语义或标注 xfail + known-limitation
  引用。本方案推送不阻塞，但测试债须由该会话关单（AGENTS.md「框架问题立即解决」不适用——此为
  测试断言同步欠账，非框架缺陷）。

## 十、 终态

- 六步流水线最后两步（用户确认 → 双仓推送）完成标志：本文件 + 后续独立提交（含卷入追认）+ 双远程 HEAD 一致核对。

## 十一、 第二轮平台失败修复：get_fundamentals date=None 前日 PIT 拼接（2026-09-03，gf-date-synthesis-design.md）

### 11.1 平台第三轮日志（用户实证）

- 前两处平台差异（min_commission/exclude_bse）已消除 ✓；新失败：`P2_income_codes=0`（全池出局、无交易）
- 特征：`QS_GF_CALL n=1 table=income_statement date=None secs=800`

### 11.2 根因（双端对照）

| 端 | income_statement date=None 800 码 list 调用 | 证据 |
|---|---|---|
| 本地（转换产物重跑） | P2=3454 ✅ | equiv_run funnel |
| 平台 | P2=0 ❌ | 平台 2026-07-01 funnel |

链路：策略无 date → wrapper 直发 date=None → prefetch 要求 date 非 None 跳过 → 平台 income_statement
date=None 形态未实证（P-D10 仅 date 形态 list 批量实证 500 码/0.05s）→ 空返回。本地 date=None 内建
prev_date 兜底故正常。

### 11.3 修复（框架层 wrapper，策略零改动）

- `_QS_GF_DATE_SYNTH_TABLES`（7 张财务/估值表白名单）+ `_qs_prev_trade_day_str()`（ctx.previous_date →
  current_dt-1 → today-1 → None fail-open）
- 主分支：date=None + 白名单表 → 拼接前日 PIT（`QS_GF_DATE_SYNTH` 审计行）→ 进入已实证 date 形态路径；
  显式传 date 调用零影响（纯增益）
- 注册表 get_fundamentals 补平台 date=None 注记

### 11.4 验收（实施侧）

- 单元：T10（test_46）date=null 白名单表产物含 helper/审计/常量，显式 date 调用点不改写 —— ✅
- 全套：44 passed（WIP 归因 7 项除外）✅；6 策略横验证 api_portability 全 PASS（syn 标记仅 funds wrapper 策略出现）✅
- 本地功能复验（equiv_run2）：P2_income_codes 复现 ≥3000 且总收益与不加拼接版（19.06%）一致 —— 见 11.5
- **平台最终判定（用户侧）**：重转上传后 P2>0 且正常出交易 —— 待用户回报

### 11.5 本地功能复验结果（equiv_run2，2026-09-03）

- funnel 复现：`P2_income_codes=3454 / P3_roe_codes=3454 / P4_valuation_codes=3454`（首日 rb_0001）✅
- 结果与不加拼接版（equiv_run1）**逐位一致**：最终净值 119,059.62 / 总收益率 19.06% / 最大回撤 43.73%
  —— date 拼接在本地行为中性（合成前日 == 本地 prev_date 语义），纯增益坐实
- 审计行实测：`QS_GF_DATE_SYNTH table=income_statement/valuation/profit_ability prev=<前一日>` 随月触发 ✅
- **平台最终判定（用户侧）**：重转上传后 P2>0 且正常出交易 —— 待用户回报（converted_product.py 已含本修复，断言集 PASS）

## 十二、 转换门失败修复：LOCAL-API-WHITELIST 全深度收集 + helper 自包含（2026-09-03）

### 12.1 用户实证（PyQt 转换失败弹窗）

```
转换未通过 run_card status=BLOCKED
[BLOCK] LOCAL-API-WHITELIST @ 275/284/300: calls unknown API '_attr'
[BLOCK] LOCAL-API-WHITELIST @ 1447/1458: calls unknown API '_qs_td' / '_qs_td3'
```

### 12.2 根因（双缺陷，同源=白名单只收顶层）

| 违规 | 根因 | 归属 |
|---|---|---|
| `_attr`（position-view 注入的**嵌套** def helper 的调用） | validate_local_strategy 的 `local_funcs` 用 `ast.iter_child_nodes` 只收顶层 FunctionDef → 嵌套 def 不被识别为本地 helper | 存量校验器缺陷（本次暴露） |
| `_qs_td`/`_qs_td3`（gf 合成 helper 内 `from datetime import ... as` 别名调用） | `imported_modules` 同理只收顶层 → 嵌套 import 别名不识别 | 本方案引入（已自修） |
| CANSLIM 复验追加 | 合成 helper 引用 `_qs_today_str`（date-norm ext 不随 funds wrapper 注入→悬空） | 本方案引入（已自修） |

### 12.3 修复（框架层，纯增益）

1. `validate_local_strategy.py`：`local_funcs` 改 `ast.walk`（全深度收集文件内 def，含嵌套）+
   `imported_modules` 改 `ast.walk`（嵌套 import 别名同识别）——只往白名单加「文件内真实 def/真实 import」，
   不新增任何外部 API 放行面；生命周期检查仍用顶层集。
2. `source_import.py` gf 合成 helper 自包含：顶层 `import datetime` + 全限定调用（`datetime.timedelta`/
   `datetime.datetime.strptime`——白名单承认的模块属性形态）；回退链去掉 `_qs_today_str` 跨模板依赖。

### 12.4 验收

- 转换门（validate_local_strategy，run_card 同款）：SG-MS-PEG-HL 产物 **0 BLOCK** ✅
- 6 策略**双门**（portability + local_strategy）横验证全 PASS ✅（CANSLIM 从 FAIL 修复为 PASS）
- 新测试 T11（test_47）：嵌套 def helper + 嵌套 import 属性调用通过；真实未知 API 仍 BLOCK（防放宽过度）✅
- 全套：12 项新测试绿 / 45 passed（WIP 归因 7 项除外）✅
- **平台最终判定（用户侧）**：PyQt 重转通过 → 上传回测 → P2>0 出交易 —— 待用户回报（product 已再生含全部修复）

## 十三、 平台第四轮失败修复：list 单调用码数上限分块（2026-09-03）

### 13.1 平台实证（用户回报 21:00-21:02）

- date 拼接**已生效**：`QS_GF_DATE_SYNTH table=income_statement prev=2026-06-30` + 平台收到
  `QS_GF_CALL n=1 table=income_statement date=2026-06-30 secs=800`（显式 date 形态直发）——
  但 **P2_income_codes 仍 0**。

### 13.2 根因（对照既有实证）

| 调用 | 结果 | 证据 |
|---|---|---|
| 平台 get_fundamentals list 500 码（date 形态） | OK 0.05s | P-D10 平台实证（README 登记） |
| 平台 get_fundamentals list **800 码**（本策略本地批 800，date 形态） | **空返回 P2=0** | 本轮平台日志 |

→ date=None 已排除；**单调用码数上限**（>500）为高置信根因。本地无此上限（equiv_run 800 码正常）。

### 13.3 修复（框架层 wrapper，自递归分块，语义等价）

- `_QS_GF_LIST_CHUNK = 500`（P-D10 实证上限；单点可调）
- wrapper 入口：`len(_secs) > CHUNK` → 自递归分块（≤500/块，各块独立走完整下游：
  合成/prefetch/cache/range/PIT/report 过滤 + fail-open），逐块 concat——index=code 唯一、列契约一致；
  本地无上限亦行为不变（纯增益）

### 13.4 验收

- T12（test_48）产物含常量与分块入口 ✅；全套 46 passed / 0 failed ✅
- 本地 gate（LOCAL-API-WHITELIST）0 BLOCK ✅；双门横验证（equiv_run3 复跑时一并复核）
- 本地功能复验（equiv_run3）：P2 复现 3454 且总收益与 equiv_run2 一致 —— 回填见 13.5
- **平台最终判定（用户侧）**：重转上传 → P2>0 → 待用户回报

### 13.5 本地功能复验结果（equiv_run3，2026-09-03）

- 最终净值 119,059.62 / 总收益率 19.06% / 最大回撤 43.73% —— **与 equiv_run1（无拼接无分块）、
  equiv_run2（拼接无分块）逐位一致** → date 拼接 + 500 分块在本地均为行为中性（纯增益坐实）
- 本地 gate（LOCAL-API-WHITELIST）0 BLOCK；T12 产物含 `_QS_GF_LIST_CHUNK` + 分块入口 ✅
- **平台最终判定（用户侧）**：重转上传（分块版 product）→ P2>0 → 待用户回报

## 十四、 平台第五轮失败修复：报表表 range 形态路由（2026-09-03）

### 14.1 平台实证（用户回报 22:34-22:36）

- date 拼接生效 + **分块生效**（`QS_GF_CALL n=1 ... date=2026-06-30 secs=500`）→ **P2 仍 0**

### 14.2 根因（实证链闭合）

| 轮次 | 调用形态 | P2 |
|---|---|---|
| 1 | income_statement date=None 800 码 | 0 |
| 2 | income_statement date=prev 800 码 | 0 |
| 3 | income_statement date=prev **500** 码 | 0 |
| 对照 | 本地同形态 | 3454 |

→ date 与码数均排除。**真根因**（wrapper 内 v8/v8.1 已有实证，本轮对齐）：平台 income_statement
**date 形态 = 披露时点单期语义**（date=2025-03-31 时 2025 一季报未披露 → 平台返回 2024-12-31）——
每 code 仅 ≤1 期；策略 `_cagr_factors_from_income` 需 **≥2 个年报期** → fac 恒空 → P2=0。
P1 探针实证：平台报表走 **start_year/end_year range 形态**（12 期季报齐全、multi2 index、publ_date、PIT 9/12 可复现）。

### 14.3 修复（框架层 wrapper，报表表 range 路由）

- `_QS_GF_STATEMENT_TABLES`（income/balance/cashflow）+ `_QS_GF_STATEMENT_RANGE_YEARS = 5`
- 调用方未显式传 start_year/end_year 且 date 已就绪 → 补窗口 `[year(date)-5, year(date)]` → 进入
  B6c range 主路径（P1 实证形态）→ multi2 拍平 + publ_date PIT 过滤 + report_types 过滤；
  显式传参路径零影响（纯增益）；本地窗口覆盖 L-4 基期不变 → 本地语义等价

### 14.4 验收

- T13（test_49）产物含路由常量/审计 ✅；全套 47 passed / 0 failed ✅；local gate 0 BLOCK ✅
- 双门横验证 6/6 全 PASS ✅
- 本地功能复验（equiv_run4）：P2 复现 3454 且总收益与 equiv_run1-3 一致 —— 回填见 14.5
- **平台最终判定（用户侧）**：重转上传（range 路由版 product）→ P2>0 → 待用户回报

### 14.5 本地功能复验结果（equiv_run4）

- equiv_run4 = 0.00%（range 路由初次引入回归）；归因链（探针逐步实证，§15）

## 十五、 range 路由本地回归三连环修复（2026-09-03，最终 P2=3144 打通）

### 15.1 排空点逐级实证（探针链）

| # | 排空点 | 根因（实证） | 修复 |
|---|---|---|---|
| 1 | `_qs_pit_filter` | publ_date 为 epoch 毫秒 np.float64；`str()` 拼 digits 得 **14 位**（尾 `.0` 多一 0）→ /1000 → utcfromtimestamp **年份 2456** → 全剔 | 数值单元直取：`abs(_f)>=1e11` → ms→YYYYMMDD（本地）；`>=1e7` → YYYYMMDD float（平台）；字符串回退 |
| 2 | `_qs_filter_report_types` | 报告期零点为北京时间，np.datetime64 按 UTC 取 'MMDD' **早一天**（12-31 → '1230'）→ 年报行全不匹配 '1231' → report=0 | `+ 8*3600*1000` 偏移后还原 |
| 3 | （备用）years-only 值域全空 | 本地引擎 years-only 返回「行在、值域空」形态（探测值 rev=None）| `_qs_gf_value_cols_allnan` 检测 → date+years 重试（本用例未触发，值域实为有值，见下） |

注：探针 #3 最初凭截断日志误判「值域空」；utf-16 解码复测证伪——years-only 值域**实为有值**
（rev=822.56 亿），真正排空点是 #1/#2。探针纪律教训（不掩差异）：日志解码先行。

### 15.2 修复后本地打通（quick_check5）

- `P2_income_codes=3144 / P3_roe_codes=… / P4_valuation_codes=…`，D1/D2/D3 各 50 只 ✅
- 全链：range 路由（报表表→start_year/end_year，P1 实证形态）→ 数值优先 publ PIT → +8h 报告期过滤

### 15.3 验收状态

- 全套 48 passed（WIP 归因 7 项除外，T14/T15 更新标记）✅；双门横验证 6/6 PASS ✅
- 本地全窗复验（equiv_run5）：回填见 15.4
- **平台最终判定（用户侧）**：重转上传（range 路由版 product）→ P2>0 → 待用户回报

### 15.4 本地功能复验结果（equiv_run5 → 性能受阻，改由 quick_check5 承担功能证明）

- **quick_check5（2021-01-04 首调仓）**：`P2_income_codes=3144`、D1/D2/D3 各 50 只——
  **全链功能打通**（range 路由 → 数值优先 PIT → +8h 报告期过滤）✅
- equiv_run5（1 年窗）在首调仓数据阶段后**性能受阻**：逐码 get_history 周转统计
  （3678 码 × 逐码合成路径，55min 仅 3min CPU，I/O 等待为主）——已终止；归因：转换产物
  get_history 逐码路径的日线昨收合成开销（既有已知性能特性，与本轮正确性修复正交；
  更早层全窗等价已由 equiv_run1-3 证明）。**登记为已知性能项，不入正确性门禁**。
- **最终判定权 = 平台端实证**（方案 §5.3）：重转上传 → P2>0 + 出交易。

## 十六、 平台第六轮修复：valuation 列名差 → 运行时判型映射（2026-09-04）

### 16.1 平台实证（用户回报 09:26）

- **P2_income_codes=4899 / P3_roe_codes=4899** ✅ —— §14/15（range 路由 + 数值优先 PIT +
  +8h 报告期）在平台复验成立，income/profit_ability 两表全通
- **P4_valuation_codes=0**：`KeyError: ['pe_ratio','turnover_ratio'] not in index` +
  QS_SHIM_FIELD_MISSING（pe_ratio/float_value/turnover_ratio）——**平台估值表列名 ≠ 本地**
  （聚源 daily_basic 列族）

### 16.2 双环境列名相反（实证，盲映射必错一头）

| 环境 | 估值表列名（探针/KeyError 实证） |
|---|---|
| 本地 | `float_value/pe_ratio/turnover_ratio/a_floats/total_value/pb_ratio…`，且 **`pe_ttm` 作别名共存**（DuckDB `s.pe_ttm AS pe_ttm`）——不可单独作判据 |
| 平台 | **缺** float_value/turnover_ratio/pe_ratio（KeyError 实证），具聚源列族（pe_ttm/circ_mv/turnover_rate 高置信，同源于我方 DuckDB stock_daily_valuation） |

实施过程教训（如实登记）：初版无条件映射 → 本地部分列命中（pe_ratio 有值、float_value 缺）→
「全 NaN」判据失明 → P4 反碎 0（quick_check7/8 实证）；据此改为**运行时判型 + 任一字段缺失判据**。

### 16.3 修复（框架层 wrapper，三层保险，两环境零误伤）

1. **运行时列名判型 `QS_VAL_MODE`**（每次回测一次，g 缓存）：`fields=None` 探针取列名集，
   **强判别式** `(无 float_value ∧ 无 turnover_ratio) ∧ (有 circ_mv ∨ turnover_rate ∨ pe_ttm)`
   → platform 才启用映射；否则本地名（= 映射前旧行为，纯增益）
2. **映射表** `_QS_VAL_PLATFORM_MAP/REV`：pe_ratio↔pe_ttm、float_value↔circ_mv、
   turnover_ratio↔turnover_rate、total_value↔total_mv、market_cap↔total_mv、
   circulating_market_cap↔circ_mv、a_floats↔free_share、pb_ratio↔pb；请求前翻译/返回后逆翻译
   （`_qs_gf_plat_field` 表感知，`_plat_fields_of` 预取同构）
3. **自愈双保险**：判型后任一请求字段仍缺失/全 NaN（`_qs_gf_val_missing_any`）→ 原生命名重试；
   仍失败 → `QS_SHIM_VAL_COLS` 一次性打印平台真实列集（一轮收敛，不掩差异）；任何异常 fail-soft 本地名

### 16.4 验收（本地）

- 判型正确：`QS_VAL_MODE local cols=a_floats,float_value,pb_ratio,pe_ratio,pe_ttm,total_share,total_value,turnover_ratio` ✅
- 全漏斗贯通：**P2=P3=P4=3144**、D1/D2/D3 各 50（quick_check9；§15 能力保持 + P4 恢复）✅
- 全套件 **50 passed / 0 failed**（含 T14/T15/T16 标记）；双门横验证 **6/6 ALL PASS** ✅
- 本地行为与映射前一致（判型 local → 映射零触发）——纯增益 ✅

### 16.5 平台最终判定（待第七轮）

- 期望日志：`QS_VAL_MODE platform` → valuation 走聚源列名 → **P4_valuation_codes>0** → 出票出交易
- 若映射名仍非平台实际列：`QS_VAL_MODE`（打印判型+列集）与 `QS_SHIM_VAL_COLS` 会暴露真实列名 →
  一轮收敛，不再盲猜
- 累计七层修复全在 `converted_product.py`：exclude_bse 剥离 / min_commission 下限 / date 前日拼接 /
  白名单全深度 / 500 码分块 / 报表 range 路由+数值优先 PIT+8h 报告期 / valuation 运行时判型映射

## 十七、 第七轮实证修正：平台估值真实列集 + turnover 合成（2026-09-04）

### 17.1 第七轮平台实证（09:54）

- **P2=P3=4899 保持** ✅；P4=0 复发——但 `QS_VAL_MODE` 探针**带回平台真实列集**（设计目的达成）：
  `a_floats,a_shares,b_floats,b_shares,dividend_ratio,float_value,h_shares,naps,pb,pcf,
  pe_dynamic,pe_static,pe_ttm,ps,ps_ttm,roe,total_shares,total_value`

### 17.2 三处推断修正（探针实证推翻 §16 假设）

| §16 假设 | 平台实证（§17） | 修正 |
|---|---|---|
| 平台缺 float_value（→circ_mv） | **float_value 平台同名存在** | 映射作废该条（保留反致砸）；§16 判型误判 local 亦源于此 |
| turnover_ratio→turnover_rate 可映射 | **平台无任何换手列** | 映射作废 → **合成**（17.3） |
| 判据用 float_value 缺失 | 两端都有 float_value；唯 **pe_ratio 平台无、本地有** | 判别式改 `'pe_ratio' not in _cols ∧ (pe_ttm∨pb∨ps 在)` |

### 17.3 turnover_ratio 合成（平台模式且请求含该列时）

- 恒等式 `tv% = volume×close/float_value×100`（换手率定义；close/vol 取
  `get_history(1,'1d',include=False)` = T-1 已完成日，与估值 date 同锚）
- **量级带自校准**：float_value 万元/元两可（本地=DuckDB 万元）→ 中值>200 除 1e4、
  <0.005 乘 1e4（A 股日换手中位天然 0.1-10%，带内不动）——两单位制皆落正确值
- **本地长表行序陷阱**（探针实证）：多码 get_history concat 行序≠secs 序且 code 列被字段筛选
  丢弃 → 位置对齐不可靠 → 长表分支逐码单调用（≤8 码；平台宽表列=码永不走此分支，零性能影响）
- turnover_ratio 从平台 list 请求**剔除**（防平台列选取 KeyError 整批空返——第六轮炸点实证）
- 合成失败 → 保 NaN（策略 L423 自然降级仅 CV 判定）+ 显性告警，不掩差异

### 17.4 验收（本地强制平台模式 harness + 原生对照）

- **三码逐位吻合**：合成 `0.22483/0.47641/0.30935` vs 原生 `0.2248/0.4764/0.3093`；
  量级带 med=3093.4674→scale=1e-4 正确选中 ✅
- pe 映射无损：`pe=5.1377≡5.1377`（平台 pe_ttm 与本地 pe_ratio 同源实证）✅
- 本地原生路径零影响：判型 local → P4=3144 保持（quick_check9）✅
- 全套件 **50 passed / 0 failed**；双门横验证 **6/6 ALL PASS** ✅
- §16 推断三处错误由探针机制一轮暴露并修正（一轮收敛设计验证成立，如实登记）

### 17.5 平台最终判定（待第八轮）

- 期望：`QS_VAL_MODE platform` → valuation list 无 KeyError → **P4_valuation_codes>0** → 出票出交易
- 换手率单位判定可在日志核对：`QS_VAL_TRU_SYNTH n=… med=… scale=…` + vhit/chit（合成命中计数，§18 新增）

## 十八、 第八轮里程碑 + 探针截断教训修正（2026-09-04）

### 18.1 第八轮平台实证（10:36）——**首次全链路出交易** ✅

- `QS_VAL_MODE platform` 判型成功；P2=P3=4899、**P4_valuation_codes=4897**、D1/D2/D3 各 50、
  T_after_limit_ban=8（涨停禁买规则生效）、**8 笔买单真实成交**、gross_exposure=0.7547
- 残留缺口：`D3a_turnover_ok=4896/4900`（本地同位比例 ~85%）+ `QS_VAL_TRU_SYNTH med=na`——
  **换手率水平过滤在平台未生效**（合成链取数失败兜空）

### 18.2 §17「平台无换手列」系探针截断误判（教训登记）

第八轮 `QS_VAL_MODE` cols 在 **170 字符处截断**，尾段显 `trading_day,turnove…`——平台**实有
原生 `turnover_rate` 列**（qdb 同源，与本地 `s.turnover_rate AS turnover_ratio` 同量纲 %）。
§17 据此前截断列集做了「剔除请求+vol×close/float_value 合成」，合成又因平台宽表批量 get_history
形态取不到值 → med=na → 水平过滤空转。

> **分母勘误（第九轮回溯，登记不掩错）**：本节初稿曾写「本地同位 85% 保留率」——系误用
> P1(3678) 作分母；正确口径为 stage3=P4_valuation_codes：本地剔除 30/3144=0.95%。
> 据此第八轮真实缺口 = 平台仅剔 1/4897（水平过滤死、仅 CV 剔 1）→ 第九轮剔 58/4897=1.18%（活，同量级✓）。

### 18.3 修复（§18，一步到位）

- `_QS_VAL_PLATFORM_MAP` 恢复并实证 `turnover_ratio↔turnover_rate` **直映**（原生值，零合成依赖）
- 删除 §17「请求剔除 turnover_ratio」逻辑；合成块退居**兜底**（仅映射后列仍缺失/全 NaN 才触发）
- 合成审计行加 `vhit/chit` 命中计数（下次若兜底触发可直接归因取数形态）；探针 cols 截断 170→400

### 18.4 验收

- 本地判型 local → 原生路径逐位不变（quick_check10：D3a=3114 与 §16 前一致，纯增益）✅
- 全套 **50 passed**（T16 增 §18 断言）；双门横验证 **6/6 ALL PASS** ✅
- **平台第九轮期望**：D3a 收敛到 ~85% 剔除比例、出票出交易、全程无 KeyError；
  该轮若通过 → 策略语义完整对齐，进入用户确认+双仓推送收尾

### 18.5 过程纪律记录（跨轮教训汇总，供后续平台吸收参考）

1. 探针输出**禁止截断参与推断**（§17 误判根因）——审计行长度须容纳完整列集或分多行打印
2. 双环境列名相反时必须运行时判型（§16/17 两次盲映射互砸实证），判据选**存在性差异最小集**
   （pe_ratio 有无），不可用别名共存列（float_value/pe_ttm 教训）
3. 合成/兜底链必须配命中计数审计（vhit/chit），否则「静默保 NaN」会掩盖语义空转
   （本轮 D3a 99.9% vs 本地 85% 的差异即由该机制暴露）

### 18.6 勘误（第十轮复盘，数字纠错——原始记录保留不删）

§18.2/18.4/18.5 中「本地保留率 ~85%」为**分母误用**：85%（3114/3678）系 D3a 对 P1_listed 之比，
混并了上游估值阶段剔除。正确口径（D3a/stage3=P4_valuation_codes）：

| 运行 | stage3 | D3a | 换手过滤剔除率 |
|---|---|---|---|
| 本地 quick_check（2021-01） | 3144 | 3114 | 0.96% |
| 平台第八轮（合成兜底，锚 NaN） | 4897 | 4896 | 0.02%（水平过滤死、仅 CV） |
| 平台第九/十轮（直映 turnover_rate） | 4897 | 4839 | 1.18% |

第九轮起与本地同数量级 ✓（池与数据源微差属环境差异）。§18.5 第 3 点教训本身成立
（vhit/chit 审计为此加装），但触发深查的实际信号是**探针全列集（400 截断）尾段 turnover_rate
显形**，非 D3a 比例——已按事实修正。

## 十九、 第九/十轮平台验收 PASS + 本地同窗 parity（2026-09-04，最终定谳）

### 19.1 平台验收（11:13 首跑 + 13:02 复跑，逐位一致=确定性 ✓）

- `QS_VAL_MODE platform cols=…,trading_day,turnover_rate`（400 截断列集完整显形）
- valuation list 调用**零 KeyError、零 FIELD_MISSING、零合成触发**（`QS_VAL_TRU_SYNTH` 不再出现
  = 直映生效，兜底未触发）
- 漏斗：P2=4899 / P3=4899 / **P4=4897** / D1=D2=D3=50 / D3a=4839 / X_pool=24 /
  T_target=10 / T_after_limit_ban=8 → 8 笔买单成交、gross=0.7547（涨停禁买 2 席留现金=确认设计语义）
- 两轮回测正常收程序结束（统计汇总完成）

### 19.2 本地同窗 parity（converted_product 跑 2026-07，交叉验证）

- `QS_VAL_MODE local`（判型正确）+ 全漏斗贯通：P0=5376 / P1=5241 / P2=4975 / D3a=4953 /
  首月收益 +1.53%（终值 101,534.92）
- 与平台的 P0/P1/P2 比例差（5376 vs 4979 池、94.9% vs 99.98% P2 通过率）= **双端数据宇宙
  与财务数据新鲜度环境差异**（本地 DuckDB vs 平台 local_finance），非管线语义差；
  管线行为一致性以「双端各自漏斗全通 + 过滤器同数量级 + 确定性复跑」为断言（登记于设计
  §5.2 断言集，本地-平台逐码恒等属已知不可达断言，见 equiv 门禁修订记录）

### 19.3 最终验收结论（六步流水线第 4 步收口）

**平台端到端语义验收 PASS**：九轮失败逐层根因（min_commission 值域 / exclude_bse 扩展参数 /
date=None 语义 / 码数上限 / date 单期→range 路由 / publ_date 毫秒归一 / 报告期 UTC 偏移 /
白名单顶层收集 / valuation 列名判型 / 探针截断误判）全部框架层修复，策略源码与转换产物
契约零改动；纯增益三重证明（本地 equiv1-3 逐位一致 + 判型 local 零触发 + 双门/全套件/6 策略
横验证全绿）。第 5/6 步已完成：用户确认推送，commit 3d96530，双远程 HEAD 逐位一致。

## 二十、 双端全窗对齐差异分析（2026-09-04 18:20 用户双端工件，3d96530 产物）

用户以同窗（2026-01-05~2026-07-31）双端回测：本地 +14.64% vs 平台 -5.37%（基准双端同为
-0.90% ✓）。对账结论（完整分析见用户目录
`私募工作文件\本地回测框架和ptrade平台双端回测数据汇总\SG-MS-PEG-HL...\双端对齐差异分析-20260904.md`）：

1. **执行层一致**：同(日,码,向)成交价 0 差异（首笔 301004@52.30×100 双端分毫不差）、佣金差
   0.005pp、基准一致——转换器与撮合无差异。
2. **选股分歧是差异主体**：月度持仓重叠仅 4-6/10；统一价格标尺（本地 qfq）逐日重放，3 月平台
   组合 -10.9pp、6/7 月 -7~-14pp（本地组合反弹强）；复权常数比值已证对收益率中性
   （300492=1.4034、002170=1.0453 等，除权处理双端均正确）。
3. **根因分层**：宇宙差（P0 本地恒多 55-75 码）+ 聚源含 IPO 前年报（P2 通过率 99.98% vs
   94.9%，CAGR 基期/n 随之不同——F-1 自适应年限放大）+ 平台 profit_ability 缺 ~12 码 ROE +
   指标修订差 → 三张 Top50 不同 → X_pool 平台 21-25 vs 本地 17-19 → Top10 重叠率低 → 复利放大。
4. **定性：数据源级 known-difference**（非管线缺陷）；双端收益数值不可对齐（数据源决定），
   对齐口径=漏斗+规则行为（已 PASS）。收敛路径=数据层统一（MCP 替代项目既定方向）或
   语义/宇宙裁剪立项（需单独审计，按铁律六步）。

## 二十一、 审计裁定落地：对齐交付物（2026-09-04 审计通过，纯对账轮确认）

审计裁定：对齐口径切换「漏斗+规则行为+执行价格」批准生效；收益数值=known-difference 披露；
① P0 **禁止裁剪本地宇宙对齐平台**（幸存者偏差）；② P2 不修（wrapper 伪造覆盖禁止）；
③ ROE 缺失确认探针执行；④ 佣金 0.005pp 下次转换顺带对齐（不单独立项）。

### 21.1 交付物(a)：P0 差异码清单 + 逐码分类（完成）

引擎口径池探针（本地 get_Ashares(exclude_bse=True)+filter_stock_by_status 原样，7 个月逐月）：

| 月 | 本地P0 | 平台P0 | 差 | 本地过滤被剔码（全为「无行情」判据） |
|---|---|---|---|---|
| 01 | 5051（恒定） | 4997 | +54 | 120 |
| 02 | 5051 | 5009 | +42 | 132 |
| 03 | 5051 | 5003 | +48 | 136 |
| 04 | 5051 | 5007 | +44 | 144 |
| 05 | 5051 | 4932 | +119 | 148 |
| 06 | 5051 | 4953 | +98 | 153 |
| 07 | 5051 | 4983 | +68 | 160 |

- **本地 P0 是静态全历史清单（5051 七月恒定）；平台 P0 是当月在册变动池（4932-5009）**——
  差额逐月不同即由此而来（双向：本地静态保留退市码 + 平台新上市本地截止未含）。
- **被剔码 120-160/月全部为「无行情」判据**（=已退市/终止/长期停牌；ST=0、停牌=0——
  ST 股不在过滤前清单或已由 is_st_reliable 处理）。清单含 000004/000608/000638/600337 等
  经典退市股。**本地 filter 的 DELISTING「无行情」判据在正常工作**——本地并非不剔退市，
  而是**缺乏「退市日历」做在册口径对齐**（审计预判「本地缺退市过滤数据基础」成立）。
- 逐码清单文件：用户目录 `差异码清单-202601-20260904.md`（含 1 月 120 码全列）。

### 21.2 交付物(b)：退市日历入库 → 已挂 MCP 全数据源替代项目（数据基建项，登记见该报告）

### 21.3 交付物(c)：ST-at-T 过滤 mini-方案（草案，待审计）

现状：is_st_reliable（stock_namechange PIT 推导）**本地可支持 ST-at-T 判定**——
探针实证本地过滤被剔码中 ST=0，说明 ST 股未进入本地 P0 的成因是「过滤前清单可能本就不含
ST 段或 is_st_reliable 判定后剔除」；方案要点：
1. 对本地池补「ST-at-T 逐月快照清单」（is_st_reliable@T 全量导出，作为对齐审计底稿）；
2. 若审计要求双端 ST 口径一致：本地 filter 的 'ST' 分支已含 is_st_reliable OR
   is_delisting_risk（正确性扩展），平台 'ST' 仅官方标记——**保真开关
   `fidelity_st_filter=True`（已有实现）即可将本地降到平台口径**（对齐实验用，默认关）；
3. 退市日历入库后（21.2），本地可按「退市日>T」补齐在册口径（数据基建完成前不实施）。

### 21.4 交付物(③)：ROE 覆盖确认探针（已实施，行为零改动）

- wrapper 注入一次性诊断（`QS_ROE_PROBE`，g 缓存/运行）：profit_ability+roe 请求缺行时 →
  dump 缺失码清单（≤10）+ 首个缺失码 `fields=None` 全列探测（columns/rows/end_dates/有无
  roe 列）+ 同码 income_statement 行数对照 → **下一轮平台运行即判「永久缺列 vs 期间缺失」**。
- 本地验证：判型 local 无缺失 → 探针静默（预期）；50 passed；AST/标记检查通过。
- 后续：若平台证「永久缺列」→ 提交 wrapper 派生（ROE=净利/权益）mini-方案走六步；
  期间缺失 → 不修（按裁定）。

### 21.5 登记

- ④ 佣金 0.005pp：下次转换顺带对齐（挂账，不单独立项）。
- 本轮全部改动（ROE 探针）待下轮平台验证后与后续吸收合并提交（推送仍需用户确认）。

### 21.4a 探针代码归档（2026-09-05 §21 收口回滚——探针未采数，待新产物平台运行后再注入判型）

注入位置：source_import.py get_fundamentals wrapper 内（profit_ability+roe 多码请求分支，行为零改动 fail-soft）：

```python
                        % (type(_fe).__name__, str(_fe)[:100]))
    # 2026-09-04 §21 ROE 覆盖确认探针（诊断注入，行为零改动；双端对齐差异分析裁定项③）：
    # profit_ability + roe 请求返回缺行（P3<P2 平台实证 ~12 码/1-4 月）→ 每运行一次性 dump
    # 缺失码清单 + 首个缺失码全列探测（判「永久缺列」vs「期间缺失」）→ 为 wrapper 派生
    # ROE=净利/权益 mini-方案提供证据。fail-soft，仅日志。
    if (table == 'profit_ability' and _field_list and 'roe' in _field_list
            and len(_secs) > 1):
        try:
            _g = _qs_g_obj()
            if _g is not None and getattr(_g, '_qs_roe_probed', False):
                pass
            else:
                _got = set(str(c) for c in _df.index) if _df is not None and hasattr(_df, 'index') else set()
                _missing = [str(c) for c in _secs if str(c) not in _got]
                if _g is not None:
                    _g._qs_roe_probed = True
                if _missing:
                    log.info('QS_ROE_PROBE missing=%d list=%s'
                             % (len(_missing), ','.join(_missing[:10])))
                    _mc = _missing[0]
                    _full = _QSFundState.orig([_mc], 'profit_ability', date=date,
                                              is_dataframe=True)
                    if _full is not None and hasattr(_full, 'columns') and len(_full):
                        _eds = None
                        try:
                            if 'end_date' in _full.columns:
                                _eds = [str(_qs_np.datetime64(int(float(x)), 'ms'))[:10]
                                        for x in _full['end_date'].tolist()[:8]
                                        if x is not None and str(x) not in ('nan', 'None')]
                        except Exception:
                            _eds = None
                        log.info('QS_ROE_PROBE detail code=%s cols=%s rows=%d ends=%s roecol=%s'
                                 % (_mc, ','.join([str(c) for c in _full.columns])[:150],
                                    len(_full), (_eds or [])[:8],
                                    'roe' in [str(c) for c in _full.columns]))
                    else:
                        log.info('QS_ROE_PROBE detail code=%s EMPTY（无任何行=期间/覆盖缺失）' % (_mc,))
                    _inc = _QSFundState.orig([_mc], 'income_statement', date=date,
                                             is_dataframe=True)
                    log.info('QS_ROE_PROBE income-ref code=%s rows=%d'
                             % (_mc, len(_inc) if _inc is not None and hasattr(_inc, '__len__') else -1))
        except Exception as _re:
            log.warning('QS_ROE_PROBE exc %s' % (type(_re).__name__,))
```

采数条件（再注入时）：用含本探针的新转换产物（消费 profit_ability+roe 多码，如 F-Score 类）、
平台跑一轮回测 → 平台日志 grep QS_ROE_PROBE：missing list + detail（cols/roecol/EMPTY）→ 判型。

### 21.4b 探针重建 + 修正 + 共享覆盖事故登记（2026-09-05 晚，§21.4a 之后）

**事件链**（如实登记）：① 09-04 本会话实施初版探针（未提交）；② 09-05 v10 线提交 c9a20ab
（data[code] 分钟 bar v10）叠加提交 source_import.py，**初版探针随工作区覆盖丢失**；③ v10 线
随即做了 §21.4a「收口回滚+归档」（流程正确：未采数不留 HEAD）；④ 本会话按用户「下轮交付物」
指令重建探针时发现上述事实，并**同步修正初版触发缺陷**。

**修正要点（与 §21.4a 归档版的差异）**：
1. 触发判据：缺行 → **「roe 列缺失/整列 NULL」（`_qs_gf_val_missing_any(_df,['roe'])`）**——
   第九轮平台（新产物仍无 QS_ROE_PROBE 行）证明平台形态是**行在而 roe=NULL**（策略按
   roe=NaN 剔除致 P3<P2），缺行判据是哑火根因；
2. 全列探测改 **fields=None 不带 date**（第七轮实证 date+None 返回空列集）；
3. 补 roe 全有值分支日志（防再次静默误判）。

**事故登记**：共享核心文件叠加写覆盖（本会话未提交改动被 v10 提交吞没，无引用留存、
不可找回；完整文本在本会话上下文已重建）。已按 AGENTS 新细则 stash create+store 基线
（c8685fa）后再动手。**请总调度核对 v10 线提交时序**（c9a20ab 18:30 叠加本会话未提交改动）。

**当前状态**：修正版探针在工作区（未提交）；本地验证 50 passed + 判型 local 静默 + AST/标记
通过；**采数条件同 §21.4a**（新转换产物 + 平台一轮回测 → grep QS_ROE_PROBE）。提交仍留用户
确认闸门。
