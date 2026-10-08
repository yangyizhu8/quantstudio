# QMT 管线 M2a 验收证据（④轮，2026-10-08）

> 计划：M2a-rev2（`c52d7a9`，②审复核放行）；实施：③a 预备件+③b 七模块（`6314cf2`）；
> 本文档=六步④验收结论（判据对照 M2a-rev2 §⑧）。

## 判据① 产物核验 —— **PASS（14/14）**

对象：`output/spec_packages/etf_hot_theme_qmt/qmt/etf_hot_theme_rotation_qmt.py`（17402 bytes，gbk）

| 检查 | 结果 |
|---|---|
| gbk 解码 / AST 编译 / `#coding:gbk` 头 / init(C) / handlebar(C) | ✓ ×5 |
| 静态池直灌（C.stock_list）/ E1 wrapper（_qs_get_history 剔当日 bar 取 [-2]）/ 复权（dividend_type） | ✓ ×3 |
| order_target wrapper / 持仓 wrapper / run_daily 门控 / passorder 23买24卖 / get_market_data_ex / POSITION 面 | ✓ ×6 |

复现：`python -m quantstudio.strategy_compiler.cli package output\generated_strategies\etf_hot_theme_rotation\strategy_spec.json --target qmt --out output\spec_packages\etf_hot_theme_qmt` →
`schema=PASS timing=PASS hard_filters=PASS api_portability=PASS`（variant_consistency=NOT_RUN 为 qmt 单目标预期）。

## 判据② QMT 白名单 —— **PASS**

- `portability_rules.py:302` `_QMT_API_WHITELIST` + `:338` `validate_qmt_portability`（`:456` 白名单判定消费）；
- run_card 顶层新键 `qmt_target={"target":"qmt","encoding":"gbk"}`（profile 对象零触碰——M2a-rev2 字段策略达成）+ schema 同批扩（run_card.schema.json +26 行）。

## 判据③ byte-diff 硬门 —— **PASS（同代对照法）**

**载体**：HEAD=c52d7a9（M2a 前）git worktree 同参重转 vs 主工作区（M2a 后）重转；
`--no-smoke` 统一形态；比对对象=`*_ptrade.py` 产物文件本体。

| 策略 | M2a前/后 | 判定 |
|---|---|---|
| CANSLIM突破成长选股策略 | 157604/157604 | IDENTICAL ✓ |
| tech_etf_mvo_rotation | 67390/67390 | IDENTICAL ✓ |
| vol_regime_mom_rev | 92769/92769 | IDENTICAL ✓ |
| weekly_smallcap_growth_momentum_10 | 145274/145274 | IDENTICAL ✓ |
| 周频小市值成长动量（三层止损） | 156434/156434 | IDENTICAL ✓ |
| etf_hot_theme_rotation | 92630/92630 | IDENTICAL ✓（基线 ③a 生成于③b 之前=有效对照） |
| fall_reversal | 61893/62125 | **DIFF→归因闭合（下）** |

**fall_reversal 归因链**：diff 内容=`set→有序 dict 确定性容器`策略源码改造——来源=**他线在途未提交改动**
（`git status: M quantstudio/backtest/strategies/fall_reversal_quantstudio.py`，非本批文件）。
**终证**：同取 HEAD 版源码×同文件名，两侧唯一变量=框架版本 → **IDENTICAL（E075F9E8B23D 双侧同）**
——M2a 框架对 source 路径产物零影响证实；他线源码差异非 M2a 污染（多会话共享工作区隔离成立）。

**历史基线（output/ptrade_export/ 现存）与今日重转的 diff**：为历代注入层演进累计（产物体积约
×2），非本次回归——故判据③以同代对照法执行（M2a-rev2 计划预设命令的基线对象修正说明）。

## 判据④ 既有回归 —— **PASS**

`python scripts\run_contract_gate.py --strategies` → `CONTRACT GATE : PASS`
（契约矩阵哈希一致+MD 一致 / pytest 契约套件全绿+既有白名单无触发 / 六策略 api_portability 冒烟全过）。
矩阵哈希追认：本批未触碰 `_QS_FUNDAMENTALS_EXT`/`_QS_INDUSTRY_EXT`（source_import.py 零改动）——不触发，符合 M2a-rev2 §2.1 边界声明。

## 过程事件（如实记录）

1. **BOM 污染拦截（fail-closed 门价值实证）**：spec 编制时池文件被 PowerShell 写为 UTF-8-BOM，
   `codes[0]='\ufeff159150.SZ'` 入 spec；`package --target qmt` 的 gbk fail-closed 门（独立退出码 5）
   拦截报错不静默替换 → 修数据（spec codes 剥 BOM）而非放宽门禁 → 复跑全 PASS。
2. **编码委派披露**：③b 委派 zcode_code（mode=edit）——goal 超时（>1800s）进程终止，但七模块
   代码已基本落盘；本会话（DSH 模型）完成核对收尾：cli/render 分发验证+BOM 根因定位修复+四判据
   验收+精确提交。未重试第二次委派（超时属任务体量型，剩余收尾为核对性质非批量编码）。
3. **spec（工作项 0）**：编制+package 双渲染验证通过（0.3.2-mvp）；universe=706 codes 与 PTrade 基线
   ETF_POOL_STATIC 同源（2024-01-02 固化池）；approximations 7 条全 user_confirmed。spec 属
   output/ ignore 域本地工件（先例 etf_smooth spec 同惯例不入 git）。
4. **他线隔离**：strategies/ 与 skills/agent_workspaces 的在途 M/?? 文件未卷入本批提交
   （精确 add 九文件）。

## 结论

**M2a 四判据全 PASS**——spec 路径 QMT 渲染端到端打通（首策略 etf_hot_theme_rotation）；
PTrade/quantstudio 既有行为零改变（同代对照+终证法+契约门）；判型=新增检测型成立
（byte-diff 逐字节不变实证）。待⑤用户确认后随批⑥推送（含 M1 三件+M2a 三件）。
