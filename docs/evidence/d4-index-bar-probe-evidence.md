# D4 平台探针证据：get_index_day_bar 平台等价物（PTrade get_history 指数支持 + include 语义）

- 探针日期：2026-09-09（用户在真实 PTrade 平台执行，策略名"探针测试"）
- 探针脚本：ptrade/probe_get_index_day_bar_ptrade.py
- 回测区间：2026-07-01 ~ 2026-07-31（23 交易日全量运行）
- D4 登记：get_index_day_bar 平台等价物探针（docs/pipeline-tech-debt.md）

## 一、探针结果（23/23 交易日逐日一致，零波动）

| 探针 | 验证项 | 结果 | 关键数据 |
|---|---|---|---|
| IDX1 | 平台 get_history 支持指数代码 | **PASS** | 000001.SS include=False 返回 5 根，close 3764~4112（指数点位量级；非个股价格——量级上下界判定直接排除 000001 平安银行串码风险） |
| IDX2 | 日线 include=True 含当日 | **PASS** | 23 天全部：handle_data 时点末根 bar 日期 == 当日（如 07-17 last_dt='2026-07-17'，close=3764.15） |
| IDX3 | include=True/False 对比 | FAIL（探针脚本判定 bug） | 判定假设错误：include=False(count=3) 返回"止于昨日"的 3 根（非同窗口少一根）；**RAW 数据自证 include 语义正确**（07-17 include=True=[07-15,07-16,07-17]，include=False 末根=07-16——恰好多一根当日） |
| IDX4 | pctChg 字段可用 | **平台不支持** | IQInvalidArgument：合法字段集 = {money/close/price/is_open/preclose/unlimited/high_limit/low/volume/open/high/low_limit}，**无 pctChg** |

## 二、双端数据一致性铁证（本地副本库 vs PTrade 平台）

| 日期 | 本地 index_daily pctChg | 平台 close 计算 (close[i]/close[i-1]-1)×100 | 一致 |
|---|---|---|---|
| 2026-07-16 | -1.8497 | (3882.41/3955.58-1)×100 = -1.849 | ✅ |
| 2026-07-17 | -3.0460 | (3764.15/3882.41-1)×100 = -3.046 | ✅ |

平台 close 序列与本地 index_daily close 序列逐日一致（3764~4112 区间全对齐）。

## 三、探针结论（D4 判定）

1. **平台 get_history 支持指数代码**：实证 PASS（含串码排除铁证）
2. **平台日线 include=True 含当日**：实证 PASS（23/23）
3. **pctChg 字段平台不可用**：重写映射必须改用 **close 相邻比值计算**（或 preclose），不可直取 pctChg
4. **重写映射可行**：`get_index_day_bar` → 平台 `get_history(count+1, '1d', 'close'[/preclose], include=True, fq='pre')` + close 相邻计算 pctChg + 本地契约 DataFrame 组装

## 四、对重写映射设计的修订（相对原设想）

- 原：重写为 get_history(fields=['pctChg'], include=True)——**不可行**（IDX4）
- 修订：重写 shim 内部 get_history(count+1, fields=['close'], include=True) → pctChg = close.pct_change()×100 → 组装本地契约（丢弃首行 NaN，返回 count 根）
- 边界：重写 shim 语义 = 日线 close 回测（include=True 含当日，探针实证）；分钟 profile 场景未探针，维持不支持

## 五、附带发现（探针脚本自身）
- IDX3 判定逻辑 bug（对比窗口假设错误）——数据自证语义正确，不影响结论；脚本 bug 记录在案
- 平台合法字段集为 D4 证据的重要补充（后续任何平台取数设计参考）

## 六、preclose 补充探针 v2 结果（2026-09-09 平台实跑，PRECLOSE_PATH_UNLOCK）

- 探针：ptrade/probe_index_preclose_v2_ptrade.py（逐日累积修订版，窗口 2026-07-01~07-31 全量 23 交易日）
- P1：preclose 全累积集非空且>0 —— PASS（每日判定 PASS）
- P2：preclose[i]==close[i-1] 逐对精确（<0.01）—— PASS（累积至 14/14 对全 True）
- P3：锚点日 (close/preclose-1)*100 vs 本地 index_daily.pctChg ——
  07-16: calc=-1.8498 vs local=-1.8497 MATCH（差 0.0001 < 0.01 容差）；
  07-17: calc=-3.0460 vs local=-3.046 MATCH ✅
- PCL2-FINAL ALL_PASS=True **VERDICT=PRECLOSE_PATH_UNLOCK**（07-17 起每日重复确认至 07-31）
- 结论：pctChg 合成走 **preclose 路径**（复用既有 _QS_HISTORY_WRAPPER close/preClose 合成，无损首行）；
  count+1 close 相邻计算 fallback 不再需要。
