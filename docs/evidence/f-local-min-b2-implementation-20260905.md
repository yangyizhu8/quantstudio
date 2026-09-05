# B2 实施与验收证据：F-LOCAL-MIN 分钟链日期契约归一 + 静默消音（2026-09-05）

> 六步流水线主线 B 第 3-4 步。设计依据：docs/f-local-min-b2-design.md（复核通过）。
> 实施回退点：stash@{0} = 61deef07（b2-impl-baseline-20260905，refs/stash 持久化）。

## 1. 实施内容（四文件，精确清单）
1. **quantstudio/backtest/providers/intraday_windows.py**（主修复）：新增 `_as_date_str(value)` 五形态归一（str 截前 10 / Timestamp·datetime·date 走 strftime / epoch-ms 容错换算 / 不可识别原样透传不在本层新造失败）；`iter_trading_days_in_range` 入口归一（缓存键 L98/切片 L105/L109 三处切片点自动消除——一处修复全链覆盖）。
2. **quantstudio/backtest/providers/duckdb_data_access.py**（双保险）：L860（单只版）/L934（批量版）两调用点入参归一（局部 import 补 `_as_date_str`）——防调用方再违约，双层防护。
3. **quantstudio/backtest/ptrade_api.py**（辅修复，**共享核心文件**）：L1375 兜底 catch `logger.debug` → 限频 warning `QS_HIST_FAIL`（类级计数器 `_qs_hist_fail_counts`，每类异常文本首条全量告警+计数聚合；返回契约不变；FrequencyCapabilityError L1371-1374 re-raise 分支零改动）。
4. **tests/test_minute_date_contract.py**（新增契约测试 5 组）。

**叠加申报**：ptrade_api.py 含他方在途 D4 缓存键改动（±4 行，未提交）；duckdb_data_access.py 含 A 线 A2 在途改动（+168/−11，本线已验收）——三方叠加事实将随提交信息分层写明。

## 2. 验收四门结果
### 门① 契约测试（tests/test_minute_date_contract.py，5/5 全绿 0.37s）
1. 双形态不炸：Timestamp vs 字符串入参 iter_trading_days_in_range 逐位一致 ✅
2. 归一五形态（str 带时刻/Timestamp/date/datetime/epoch-ms）+ 不可识别透传 ✅
3. get_history 端到端反断言：Timestamp anchor 分钟批量调用非空（B1 零输出反断言）✅
4. 限频告警：首条 QS_HIST_FAIL warning（含异常明细）+ 二次零告警 + 计数 ≥2、返回契约不变 ✅
5. 五分支矩阵：TABLE_MISSING（指数 000852）/FREQ_NOT_IN_TABLE（'5m' 缺失）在 str/Timestamp 双形态下语义不变 ✅
- 实测注记：scratch 库 freq 标签须为 api_to_storage('1m')='1min'（存储标签）；分钟 time 须落在上海时区 09:31-11:30 时段窗口内（两处测试构造修正，已在测试 docstring 记录）。

### 门② 回归钉死清单
契约门重跑：失败集 = 钉死清单在途漂移层 5 项 + 矩阵哈希门红（他方 §21 探针在途，归因在案）——与 A2 前后逐项一致、**零新增**。白名单 5 项未触发。按钉死文档 §4 规则不计入 B2 放行/否决。

### 门③ 复现反断言（决定性）
B1 四组合探针修复后复跑：`L1511-verbatim / include=True / kw-form-normal → CodeDict {'000017.SZ': 3, '001237.SZ': 3}` **全部非空**（B1 时全灭）。is_dict=False 支的 RAISE 为探针脚本自身解包 bug（CodeDict ndarray 误用），非实现问题——已注记。数据同源（stock_minutes 4420 万行真实库）。

### 门④ B3 假绿预警落实
- 本组门③反断言即为「产物原文形态直调数据层」验证（非日线 profile E2E——分钟链路被真实驱动）；
- 完整产物本体 E2E（v10.4.1 parity 驱动形态）随 A 线推送窗 + §21 收口后的统一验证执行（分钟驱动形态，防日线 profile 假绿——审核预警已入验收设计 §4）。

## 3. 纯增益声明
- 归一只作用于非 str 形态：字符串入参行为字节级不变（iter_trading_days_in_range 既有调用方全部传 str 的路径零变化）；
- 限频告警只改日志可见性，返回契约不变；
- 无模板改动 → 产物零重转、G3.5 不涉及。

## 4. 回退条件核查
三条均未触发（字符串路径零漂移 ✅ / 告警量正常（限频+计数）✅ / 无 schema 变更 ✅）。

## 5. 待办
- 送审 → 用户确认 → 与 A 线同窗推送（审核建议：避免叠加文件分批推送反复重钉清单）；推送前矩阵哈希门照他方 §21 收口流程；
- B3 E2E（产物本体驱动）随同窗验证；
- 提交信息分层写明三方叠加（duckdb_data_access 叠 A 线 +168、ptrade_api 叠他方 D4 ±4）。
