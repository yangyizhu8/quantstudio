# smallcap 冷启动优化实测报告：索引方案否决 + get_stock_status 按日缓存（Phase 4.5）

> 日期：2026-08-12 ｜ 状态：**完成（方案 1 索引实测否决；方案 4 缓存实施并通过 T5）**
> 实验环境：staging 副本 `output/staging_idx_test/quantstudio.db`（20260807 快照 + 生产分钟数据补齐，**未碰生产库**）
> 窗口：2026-07-13 ~ 2026-07-16（4 交易日，数据完整），smallcap_overnight_scalp，minute-bar-v1，PYTHONHASHSEED=0

## 1. 方案 1（`(code, time)` 索引）——实测否决

| 证据 | 结果 |
|---|---|
| `EXPLAIN SELECT ... WHERE code = '600000'`（字面量/参数化均测） | **全部 `SEQ_SCAN`**——DuckDB 优化器对 957 万行列式表不使用 ART 索引 |
| 单 code 全历史加载（17 列 fetchdf，5 只均值） | 无索引 735ms → 有索引 389ms（~1.9x，含首查预热偏差） |
| COUNT 隔离扫描成本（无结果构建） | 参数化 23-32ms → 字面量 7-11ms（zonemap 裁剪 3-4x，**与索引无关**） |
| 引擎 4 天窗口总耗时 | 无索引 1098s → 有索引 1200s（**-9%，噪声范围内，无收益**） |
| 引擎 T5（索引前后 CSV） | **PASS（容差 0，daily_stats/trades/benchmark 逐位相等）**——索引不改变结果（意料之中） |

**结论**：DuckDB 的 ART 索引在本次数据/查询形态下不被优化器选用，索引纯属负担。**方案 1 废弃**，不写入生产库（幸好实验只在副本）。

## 2. 冷启动 289ms/次的真实构成（数据层拆解）

| 环节 | 耗时 | 说明 |
|---|---|---|
| 扫描（参数化，无 zonemap 裁剪） | ~20-30ms | 957 万行全表扫描 |
| 扫描（字面量，zonemap 裁剪） | ~7-11ms | 可省 ~20ms（1.1x，被淹没） |
| **fetchdf 结果构建（固定开销）** | **~200-240ms** | 与行数关系小：1-code（2000 行）265ms vs 5-code（1 万行）195ms 量级相近 |
| 行数相关成本 | ~3μs/行 | 500-code（100 万行）3.9s |

**结论**：冷启动大头是 **fetchdf 固定开销（~200ms/次调用）**，不是扫描。索引/字面量只能省扫描部分（~20ms），**对总耗时的改善 ≤10%**——与引擎实测（无收益）互相印证。

## 3. 方案 4（get_stock_status 按日缓存）——实施并通过

- **实现**：`DuckDBReferenceDataProvider.__init__` 新增 `_status_cache_key/_status_source/_status_source_by_code`；`get_stock_status` 按 `_start_ms(date)` 缓存当日全市场快照与 `{code: 字段字典}`。同日（含不同日期格式）只查 1 次；date 变化自动换缓存；provider 实例生命周期 = 一次回测。
- **单元测试**（tests/test_phase4_status_cache.py，5 项全绿）：同日单查询、跨日重查、与重查基准逐位相等（check_exact=True）、重复调用逐位稳定、空表缺省行。
- **引擎 4 天窗口实测**：

| 指标 | 缓存前（CSV_1） | 缓存后（CSV_2） | 变化 |
|---|---|---|---|
| get_stock_status 总耗时 | 299.4s（1982 次 × 151ms） | **60.1s（1982 次 × 30ms）** | **-80%** |
| get_history | 282.2s | 256.4s | ~-9%（噪声） |
| 引擎总耗时 | 1199.9s | **923.7s** | **-23%** |
| 引擎 T5（CSV_1 vs CSV_2） | — | **PASS（容差 0）** | 逐位相等 |

## 4. 结论与后续方向

1. **本次落地**：get_stock_status 按日缓存（-23% 总耗时，纯内部缓存，T5 逐位等价 PASS）。索引不落地（实测无效）。
2. **日线 get_history 冷启动的剩余空间**：fetchdf 固定开销 ~200ms/次是主体——优化方向是**减少 fetchdf 调用次数**（500 次单 code → 批量加载多 code 一次 fetchdf；引擎预热候选池需先解决"引擎不知候选"与内存约束）或 **arrow 零拷贝导出**（替换 fetchdf 为 `fetch_arrow_table`，存储格式变更，需单独审核）。均不在本次范围。
3. **数据层附注**：字面量过滤可触发 zonemap 裁剪（扫描 -70%），但需白名单校验防注入，且对总耗时影响 ≤10%——暂不实施。

## 5. 证据索引

- 实验日志：`output/t5_idx_0_noindex.log`（无索引）、`output/t5_idx_1_withindex.log`（有索引）、`output/t5_idx_2_statuscache.log`（缓存后）
- T5 CSV：`output/t5_idx/before/`（CSV_0）、`output/t5_idx/after/`（CSV_1）、`output/t5_idx/after_cache/`（CSV_2）
- 单元测试：`tests/test_phase4_status_cache.py`
- 副本：`output/staging_idx_test/quantstudio.db`（实验用，可删除）
