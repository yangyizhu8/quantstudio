# B1 定谳报告：F-LOCAL-MIN 本地分钟 get_history 静默异常（2026-09-05）

> 送审对象：ZCode 复核（主线 B 第 1 阶段交付物）。复现形态按产物 L1511 原文逐字（审核修正②）。
> **B1 补录见文末**（B3 E2E 推进中发现的第二层缺口：批量分钟窗口语义）。

## 1. 复现（100% 重现，探针 agent_workspace/b1_repro_v2.py / b1_stack.py）
- 环境：本地引擎真实装配（StrategyRunner + BacktestEngine daily-bar-v1 + DuckDB providers），打板策略语境（lbdt-dalong canonical 加载注入 _api）。
- 调用（产物 L1511 原文形态）：`get_history(3, frequency='1m', field=['close','preClose'], security_list=['000017','001237'], fq='pre', include=False, is_dict=True)`
- 结果：**CodeDict {} 零输出**（include True/False、is_dict True/False 四组合全灭——「探针四组合零输出」完全复现）。
- 数据侧排除：本地库 stock_minutes 4420 万行（2026-06-12 起）、etf_minutes 8750 万行——**分钟数据存在**，非数据缺失。

## 2. 根因（完整异常栈，证据确凿）
```
get_bars_by_count (duckdb_provider.py L95, Phase 4A 批量路径)
  → query_minute_bars_by_range_batch (duckdb_data_access.py L934)
    → iter_trading_days_in_range(start_date, end_date, calendar) (intraday_windows.py L98)
      cache_key = (start_date[:10], end_date[:10], ...)   ← 'Timestamp' object is not subscriptable
```
- **契约违约**：`iter_trading_days_in_range` 签名声明 `start_date: str, end_date: str`（L85-86），实现按字符串切片（L98/105/109）；调用方 ptrade_api L1330/L1321 传入 `anchor_date`（**pd.Timestamp**）。
- 单只版 query_minute_bars_by_range L860 同样直传（同违约暴露面）；打板多码场景走 Phase 4A 批量版先炸。

## 3. 静默机制（为什么表现为「静默」而非报错）
双层吞噬：
1. ptrade_api **L1375-1377**：`except Exception as e: logger.debug(f"get_history 失败: {e}") → return {} if is_dict else pd.DataFrame()` —— **debug 级默认不可见**，任何分钟链异常统一吞成空返回；
2. 策略侧 **L1512-1513**：`except Exception: return`（产物防御性吞）——但本例策略根本感知不到异常（拿到的是空 dict 非异常）。
结果：策略把「框架异常」当「无数据/无信号」，回测静默零订单——与 22+ 轮前「空数据静默出回测」事故模式同族。

## 4. 影响面
- 本地引擎**全部分钟 get_history 调用**（任意 is_dict/include/字段组合）——凡 anchor_date 为 Timestamp 的调用全灭；
- 平台侧**不受影响**（真实 PTrade API，无此 Python 层）——属纯本地双端对齐缺口；
- 日线路径不受影响（unit='1d' 不走分钟分支）。

## 5. 修复落点建议（B2 方案素材，待复核后另案设计送审）
- **主修复（通用，一处归一全链覆盖）**：`iter_trading_days_in_range` 入口对 start/end 做 `str()[:10]` 等价归一（兼容 str/Timestamp/date 三形态）+ 单只版/批量版调用前同款归一（双保险，防调用方再违约）；
- **辅修复（静默消音家族）**：ptrade_api L1375 兜底 catch `logger.debug` → `logger.warning` 限频告警（与 F-DUCKDB-LOCK P3 同款治理；归属 ptrade_api.py 共享核心文件，走共享纪律）；
- **契约测试**：Timestamp/str 双形态调用不炸 + 分钟链回归断言（引用本报告探针编号）。

## 6. 时序疑点（不影响定谳，登记备查）
「R5 43 天之前跑通」与 Phase 4A 批量路径引入时序的交叉（批量版是否后于 R5 引入、或彼时 anchor_date 形态不同）未做 git 考古——根因栈与静默机制已闭环，时序只影响「何时引入」叙事，不影响修复方案。

## 7. 证据清单
- 复现探针：agent_workspace/b1_probe.py（全链出口插桩：3 日回测分钟调用 0 次——本地日线 profile 无分钟节拍，分钟调用仅发生在显式 _try_play_window 语境）、b1_repro_combos.py（四组合全灭复现）、b1_repro_v2.py（数据层直调同炸 + debug 吞噬捕获）、b1_stack.py（完整异常栈）。
- 代码锚点：ptrade_api.py L1192-1377（get_history 签名映射/兜底 catch）、L1330/L1321（Timestamp 直传）、duckdb_provider.py L94-104（Phase 4A）、duckdb_data_access.py L808-890（单只版）/L917-947（批量版）、intraday_windows.py L84-114（str 切片契约）。
- 库态：stock_minutes 44,207,227 行 / etf_minutes 87,495,628 行（探针 b1_minute_tables.py）。

---

## B1 补录（2026-09-05 深夜，B3 E2E 推进中发现的第二层缺口）

### 现象收敛
B2 修复（契约归一）后，B1 复现探针的 TypeError 消失；但产物形态分钟调用仍返回空数据
（CodeDict 键在、DataFrame 0 行——v16/v17 数据层中间态全打印实证）。

### 第二层根因（与第一层契约违约独立）
批量分钟查询的窗口语义与 count 语义不一致：
- `query_minute_bars_by_range_batch`（Phase 4A）以 (start_date, end_date) 双参
  接收 ptrade_api 传入的 (anchor, anchor)——**只查 anchor 单日**；
- 打板场景 anchor=当日、include=False cutoff=当日 09:30 → 时段窗口内 ≤cutoff 的
  bar 为空（当日 09:31 起 bar 全被 cutoff 排除）→ 合法空返回；
- 而 count=3 语义期望「截止当前 bar 的最近 3 根」（可为前一日 bar）——单只版
  range 语义同样单日（query_minute_bars_by_range 也是 (start,end)=anchor 双参）。
- **结论**：这不是本会话引入的回归，而是 Phase 4A/PR3 以来「分钟 count 查询以
  anchor 单日 range 实现」的**设计性限制**——跨日 count 语义缺失。B1 前被 TypeError
  掩盖（更早死于日期契约），契约修复后暴露为「合法空」。

### 处置（铁律⑧：另立 B2' 修复，不与 B2 混装）
- B2（契约归一+消音）维持已验收状态；第二层缺口立项 **F-LOCAL-MIN/B2'：分钟 count
  查询跨日窗口语义修复**（count 语义：向 history 方向枚举交易日直至凑满 count 根，
  或 batch SQL 改「每码最近 N 根」窗口函数——方案六步第 1 步另出）；
- B3 E2E 断言（真驱动非空）依赖 B2' 完成后才能通过——B3 与 B2' 同窗收口；
- B2 已验收部分（归一+消音+契约测试）独立成立，不受影响。

### 证据
- v16 逐变量复刻：mh={'000017.SZ': 0}（0 行 df）；v17 数据层直调中间态：
  day_strs=['2026-07-01']、表内 000017 有 40 行（06-30 09:31-10:10 上海时区，
  freq='1min'）→ 窗口错位实证；
- 探针：agent_workspace/b3_v16.py、b3_v17.py。

