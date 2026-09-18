# prev_close_map 去 iterrows 化 · 实施与验收证据（2026-09-19）

- 工作包：`perf/prev-close-map-deiterrows`（**主因件第一子件**）
- 归属：QuantStudio 主仓框架层（回测引擎）—— **纯性能优化·语义等价**
- 审批：总调度方案审核通过（两处轻修订全部采纳）+ 用户 GO
- 回退点：`git stash store` hash **`efaf9b520f7f59a084ca8a8287f3c85173b0db65`**
  （message `baseline-prev-close-map-deiterrows-20260919_0300`）
- 性质：**未提交**（待呈审 → 用户确认 → 双推）

---

## 1. 背景（主因件归因结论）

| 证据 | 读数 |
|---|---|
| A-0 | PRE/POST 缺口集中于 `backtest_engine.py:881 _apply_factor_derived_split` 的 `iterrows`（Δcum +19.26 s，两侧调用次数相同）；**已自证伪「帧结构差异」假设** |
| A-1 | 三层拆分：data_access **1.2 %** / py_runtime **84.0 %** / engine **13.1 %**，**闭合残差 −0.0002 s（−0.00 %）** |
| A-2 | d 层残差集合四项全 0（PASS） |
| A-3 | 8 年窗口：修前 1151.43 s / 修后 1100.16 s（缓存键修复贡献 51.3 s） |
| **全仓普查** | 静态 58 处 `iterrows` → **回测路径实触 4 处** → **`:907` 独占回测总耗时 64.04 %**（85.094 s / 132.87 s） |

**性质闸门**：该写法自纳入起即如此，**非缺陷、非回归**；改造目标是**实现等价替换**
（`prev_close_map` 的键值语义逐位不变）⇒ **实现选择**，属纯性能优化。

## 2. 改动清单

`quantstudio/backtest/backtest_engine.py`（`_apply_factor_derived_split` 内，`prev_close_map` 构造）：

```python
# 原（2 行）
for _, row in prev_data.iterrows():
    prev_close_map[str(row['code'])] = row.get('close', 0)

# 新（向量化；含 close 缺列的全 0 map 分支）
_codes = prev_data['code'].astype(str)
if 'close' in prev_data.columns:
    prev_close_map = dict(zip(_codes, prev_data['close']))
else:
    prev_close_map = dict.fromkeys(_codes, 0)
```

**边界**：净 **+10 / −2 行**（在方案「≤4 行」判据内，含注释）；**其余 3 个被触发的调用点**
（`848`/`1024`/`ptrade_metrics.py:59`，合计 0.16 %）**登记不改**；`strategies/` 下 6 处
`iterrows` **零触碰**（铁律）；**不新增任何开关**。

## 3. 验证证据

### 3.1 T2 等价性预证（map 级，11 组用例）

`agent_workspace/probe_t2_equiv.py`：正常 / 浮点 code / NaN code / 重复 code /
`close` 含 NaN / 阈值邻域 / 空表 / `code` 缺列 / `close` 缺列 → **全 PASS**；
大表 7386 行：keys/vals 全等，**old 0.1943 s → new 0.00124 s（157×）**。

**一处 FAIL 及其处置（如实记录）**：**整数 code 列**下两式**确有差异**
（`iterrows` 走 `df.values` 会把 int64 提升为 float64 → `'600000.0'`；向量化 → `'600000'`）。
**实测真实数据不触发**：`query_daily_snapshot` 的 `code` 列 dtype **恒为 object**、
`df.values.dtype` 亦为 object（2026-03-12 / 2026-07-01 双点实测），此前提下两式逐值一致。
该差异**固化为 E-4 契约测试**，并配套 **E-1 dtype 前提哨兵**——前提一旦失守，测试变红。

### 3.2 T4 契约测试（`tests/test_prev_close_map_equiv.py`，**17 passed**）

| 组 | 内容 |
|---|---|
| E-1 | **真库 dtype 前提**（code 列 object / df.values object）—— 唯一静默差源哨兵 |
| E-2 | map 级等价 7 组（含**带区阈值邻域** 0.99 / 1.01 / 1.10 ±eps） |
| E-3 | **缺列语义**（总调度指定）：`code` 缺列 → 空 map；`close` 缺列 → **全 0 map**；并附消费端等价双重保险 |
| E-4 | 数值型 code 的**已知差异登记**（证明 E-1 前提的必要性，非缺陷） |
| E-5 | **ETF 除权四带区**（送股 / 现金分红带 / 非除权 / 份额合并）+ 送股后持仓变化断言 |

> E-5 系**补覆盖**：端到端 ETF 样本（`ETF轮动`）在窗口内 `trades_len=0`、`ca_len=0`，
> ETF 除权分支**未被触发**，故以单元级直接驱动 `_apply_factor_derived_split` 补齐。

### 3.3 T5 端到端黄金对比（**PASS**）

改前/改后**文件级还原取证**（备份 → `git checkout HEAD --` → 取证 → 恢复 → SHA256 校验，
`RECOVER OK`），窗口 `2025-06-02 ~ 2026-09-03`：

| 样本 | `nav_sha` | `trades_sha` | `ca_sha` | 判定 |
|---|---|---|---|---|
| `双均线策略`（单股） | 全等（309 日） | 全等（37 笔） | 全等（1 条） | **PASS** |
| `ETF轮动` | 全等（309 日） | 全等（0 笔） | 全等（0 条） | **PASS** |

### 3.4 T6+T7 性能 A/B 与横向验证（本件核心）

命令：`agent_workspace/ab_prevclose.py <old|new> <sample> r<n>`（同 T5 文件还原机制，
`RECOVER OK`）。每态含 **`iterrows:907` 调用计数**，用以**证明两态走的是不同路径**。

**样本 ma（双均线·单股·客户场景），窗口 2025-06-02 ~ 2026-09-03**

| 态 | 序列（s） | **中位数** | 极差 | `iterrows:907` |
|---|---|---|---|---|
| OLD | 161.5039 / 159.6745 / 148.2668 | **159.674** | 13.237 | **307 / 307 / 307** |
| NEW | 44.9666 / 44.6211 / 41.7072 | **44.621** | 3.259 | **0 / 0 / 0** |

- **中位差 = −115.053 s（−72.1 %）**；两侧**路径分离确证**（307 → 0）；
- **行为全等**：6 次运行 `nav_sha` 唯一值 `e7e3789a355fd941c414a8197d628c5a`（309 日）。

**样本 fall（fall_reversal·全市场），窗口 2026-03-02 ~ 2026-09-03，各 n=1**

| 态 | 总耗时 | `iterrows:907` | `nav_sha` |
|---|---|---|---|
| OLD | 243.0272 s | 127 | `33067b1c45bb0ee8d001480f…`（129 日） |
| NEW | 264.2626 s | 0 | 同左（全等） |

**对该样本的诚实说明（不作任何方向性结论）**：

1. **单轮 vs 单轮，不足以判定** +21.235 s（+8.7 %）是真实回归还是测量波动——
   同批 ma 样本的**组内相对极差为 8.2 %**，与该幅度**同量级**；
2. 该窗口内 `iterrows:907` 仅触发 **127 次**（ma 为 307 次），**对本改造的敏感度本就更低**；
3. 若需定论，须对该样本做多轮交替复测（**成本另计，未在本件内宣称结论**）。

### 3.5 路径分离（对照有效性的必要前提）

沿用缓存键件教训：**声称"结果/耗时不同或相同"之前，必须先证明两态走的是不同路径**。
本件证据为 **`iterrows:907` 调用计数 OLD 307/NEW 0**（fall：127/0）——两态实现路径**确已分离**，
故上表的耗时差与行为全等均具备证明力。

## 4. 结论

1. **收益**：客户场景主样本（单股 309 交易日）**中位数 159.674 s → 44.621 s，−72.1 %**；
2. **等价性**：map 级（T2/T4）、端到端（T5，两策略）、横向（T6 fall）**全部全等**；
3. **路径分离**已证（`iterrows:907` 计数归零），对照有效；
4. **防回归**：17 条契约断言入库，其中 **E-1 dtype 哨兵**直指本类改造的经典静默差源；
5. **未覆盖/未宣称**：`fall` 样本耗时方向、8 年窗口耗时收益（未测）；
6. **未提交**；待呈审 → 用户确认 → 双推（**按 C7 附提交全清单凭证**）。

## 5. 本件产生的取证工件（均在 `agent_workspace/`，不进提交清单）

| 工件 | 用途 |
|---|---|
| `probe_iterrows_census.py` | 全仓 iterrows 运行时普查（次数×行数×耗时） |
| `probe_t2_equiv.py` | T2 等价性预证 |
| `golden_prevclose.py` | T5 端到端黄金对比 |
| `ab_prevclose.py` | T6/T7 A/B（含 `iterrows:907` 计数） |
| `probe_a2_daily_residual.py` · `analyze_a1_layers.py` · `analyze_callees.py` · `analyze_cprof.py` | A-1/A-2/A-0 归因 |
| `prevclose_golden/` `cachekey_profiling/` | 上述产物 |
