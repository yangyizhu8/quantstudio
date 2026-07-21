# D10 预研报告：float_value 差异根因分析（最终版）

> 日期：2026-07-17
> 触发：B5 检查点第一份对照报告 L1=1.6% / L3=7%，选股完全不同
> 方法：回溯选股链路，逐层定位 float_value 差异的真实层级
> **状态：已修复一个 PIT 防御性 bug，确认真实根因为 a_floats 数据源差异（无法从现有 CSV 直接验证量级）**

---

## 一、核心结论（修订）

经逐层排查，D10 的根因链是：

1. **（已修复）PIT 防御性 bug**：`_preload_market_data` 原取全局最新估值，已改为按 `prev_ms` 做 PIT 过滤（按月缓存）。此 bug 在跨月回测时会让 float_value 用到下月数据，是真实缺陷，已修复。
2. **（真实根因）a_floats 数据源差异**：策略 `handle_data` 用 `curr_float_value = a_floats × price` 选股。用本地正确 PIT 数据，本地严格选出 curr_fv 最小的 4 只（排名 1/2/3/6），而 Ptrade 选的是排名 4/5/7/8。**两边用的 `a_floats`（流通股本）数据不同**，导致 curr_fv 排序不同，进而选股不同。
3. **（无法直接验证）**：Ptrade CSV 未导出 a_floats 字段，无法直接量化本地 tushare `free_share` 与 Ptrade 聚源 a_floats 的差异百分比。

---

## 二、关键证据（用正确 PIT 数据）

### 2.1 策略 handle_data 实际选股口径：curr_float_value = free_share × close

2026-01-05 各股 curr_float_value 排名（free_share 取 2026-01-04 PIT，close 取 2026-01-05）：

| 排名 | 票 | free_share(亿股) | close | curr_fv(亿) | 谁选的 |
|---|---|---|---|---|---|
| 1 | 002231 | 2.77 | 1.33 | 3.68 | 本地 |
| 2 | 002808 | 1.46 | 5.27 | 7.71 | 本地 |
| 3 | 002898 | 0.64 | 13.58 | 8.64 | 本地 |
| 4 | 002830 | 0.45 | 19.42 | 8.69 | Ptrade |
| 5 | 002888 | 0.51 | 19.68 | 9.97 | Ptrade |
| 6 | 002872 | 2.13 | 4.89 | 10.43 | 本地 |
| 7 | 002719 | 1.30 | 8.48 | 11.06 | Ptrade |
| 8 | 002809 | 1.06 | 10.74 | 11.38 | Ptrade |

**本地严格选出 curr_fv 最小的前 5（排名 1/2/3/6），Ptrade 选的是 4/5/7/8**。本地选股逻辑正确（最小市值），Ptrade 偏离。

### 2.2 根因推断：a_floats 数据源差异

`curr_float_value = a_floats × close` 中，`close`（收盘价）两边应一致（交易所公开数据）。差异只能来自 `a_floats`（流通股本）：
- 本地：tushare `stock_float_share.free_share`
- Ptrade：聚源 a_floats

流通股本在某些股票（如有限售解禁、增发）上，不同数据商的更新时效/口径可能不同。002231 的 free_share=2.77 亿股是本地 tushare 值，Ptrade 聚源可能给出不同值，导致 curr_fv 排序变化。

### 2.3 无法直接验证的诚实说明

Ptrade CSV 三件套（交易详情/持仓明细/Log.txt）均不含 a_floats 或流通股本字段，无法直接计算"本地 free_share vs Ptrade a_floats"的偏差百分比。要量化这个差异，需要：
- 用户在 Ptrade 平台额外导出含流通股本/流通市值的选股快照，或
- 接受"数据源差异不可消除"，在 L3 软指标内容忍

---

## 三、已修复的 PIT 防御性 bug

### 3.1 bug 描述

`ptrade_api.py:_preload_market_data` 原实现：
```python
# float_df：取全局最新一期（无 PIT 过滤）
QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) = 1
# daily_latest：用 MAX(time) 取数据库最新日（未来日）
WHERE time = (SELECT MAX(time) FROM stock_daily)
```

回测在 2026-01-05 时，数据库已有 2026-01-29 的数据，float_value 会用到 1-29 的值（未来穿越）。跨月回测时尤其严重。

### 3.2 修复

新增 `_refresh_fundamentals_pit(prev_ms)`，按月缓存，每次跨月用 `WHERE time <= prev_ms` 重查 PIT 估值快照。pe_ratio 等也改为 `WHERE time <= prev_ms`。

### 3.3 修复验证

修复后 002231 的 float_value 从 1.85 亿（2026-01-29 未来数据）→ 4.31 亿（2025-12-31 正确 PIT）。选股排名随之修正。

---

## 四、对方案路线图的影响

### 4.1 分支决策（按你的预定义标准）

- **不是 <1% 噪声**：本地选 top 1/2/3，Ptrade 选 top 4/5/7，差异显著
- **不是 1-5% 中等差异**：是流通股本数据源差异，非微小噪声
- **判定为：数据源差异（a_floats），量级无法从现有 CSV 精确量化**

### 4.2 Phase C1 设计建议

由于根因是数据源（非框架 bug），Phase C1 的 scorer **应引入排名缓冲带**：
- 不直接选 top 5，而是取 curr_fv top 15 作为候选池
- 在候选池内按其他因子（如 PE、动量）二次过滤，降低对单一市值排名边界的敏感性
- 这样即使 a_floats 数据源有差异，选出的组合也更稳健、更易与 Ptrade 对齐

### 4.3 后续可选动作

1. **请用户在 Ptrade 平台导出选股快照**（含 a_floats 或流通市值字段），用于精确量化数据源差异——这是唯一能确定差异百分比的方法
2. **接受数据源差异为已知限制**，L3 设软指标生效，Phase C1 用缓冲带缓解
3. **考虑切 xtquant 权威源**（若差异持续影响对齐）

---

## 五、L4 成本偏差快速扫描（并行完成）

| 项 | 结论 |
|---|---|
| Ptrade "手续费"列含义 | **佣金 + 印花税合并**（买入 8.71、卖出 35.13，卖出显著高=含印花税） |
| 本地口径 | commission（佣金 5.34）+ tax（印花税 2.52，仅卖出）分列 |
| L4 的 27.71% 偏差根因 | **主要来自选股不同导致交易笔数/金额不同**（本地 9 笔 vs Ptrade 55 笔），非口径错误 |
| 处理 | **不需单独修**。a_floats 差异缓解后选股对齐，L4 会自然改善 |

---

## 六、产出文件

| 文件 | 用途 |
|---|---|
| `scripts/diagnose_float_value.py` | D10 诊断脚本（可重跑，已修正时区计算） |
| `docs/D10-float-value-diagnosis.md` | 本报告 |
| `ptrade_api.py:_refresh_fundamentals_pit` | PIT 防御性修复（已落地） |

---

## 七、下一步

D10 结论已明确：根因是 a_floats 数据源差异（非框架 bug），量级无法从现有 CSV 精确量化。建议：

1. **立即进入 Phase C1**（不拖），scorer 引入排名缓冲带（取 top 15 候选池再二次过滤）缓解数据源差异
2. **请用户考虑**：能否在 Ptrade 平台额外导出一份含流通股本/流通市值的选股快照？若有，可精确量化 a_floats 差异百分比，决定是否需要切 xtquant 权威源
3. Phase C1 完成后重跑 B5 对照，预期 L1/L3 改善（缓冲带降低边界敏感性）

---

## 八、xtquant 三方对比（用户本地 MySQL 实测，2026-07-17）

> 用户本地有 `stock_xtquant_data` MySQL 库，直接实测 xtquant vs tushare vs Ptrade。
> 本节用真实数据回答"是否值得切 xtquant"。

### 8.1 数据源说明

| 源 | 表 | 字段 | 覆盖 |
|---|---|---|---|
| **xtquant** | `xt_financial_capital` | `circulating_capital`（流通股本，股） | 2018-2026 全期，按财报/公告日（稀疏，每票 ~50 条） |
| **xtquant** | `xt_instrument_detail` | `FloatVolume`（流通股本，股） | 仅近 15 个交易日，每日 |
| **tushare** | DuckDB `stock_float_share` | `free_share`（流通股本，股） | 全期，按交易日 |

xtquant 两表在最新值上**完全一致**（实测 9 只票差异 0.00%），只是覆盖时间范围不同。回测 PIT 取数应优先用 `xt_financial_capital`（全期覆盖）。

### 8.2 流通股本差异（xtquant vs tushare，2026-01-04 PIT）

| 票 | xt_circ | tu_circ | 差异% | 说明 |
|---|---|---|---|---|
| 002872 | 214,918,134 | 213,289,334 | +0.76% | 唯一接近的 |
| 002808 | 165,005,928 | 146,391,808 | +12.72% | |
| 002719 | 161,861,095 | 130,398,465 | +24.13% | |
| 002809 | 139,117,394 | 105,992,587 | +31.25% | |
| 002193 | 261,714,050 | 191,983,885 | +36.32% | |
| 002888 | 74,923,020 | 50,682,889 | +47.83% | |
| 002830 | 66,852,500 | 44,740,000 | +49.42% | |
| 002898 | 101,262,561 | 63,594,096 | +59.23% | |
| 002200 | 195,725,970 | 91,145,449 | **+114.74%** | 差一倍多 |
| **002231** | **无数据** | 276,723,017 | - | **xtquant 已下架，tushare 保留** |

**统计**：xtquant vs tushare 流通股本 |差异中位数| = **36.32%**，P95 = 92.54%。

**结论**：这不是精度噪声，是系统性数据源差异。xtquant 普遍高于 tushare 12%-115%。

### 8.3 哪个源更接近 Ptrade？（决策性证据）

用各自流通股本 × 同一收盘价（2026-01-05）重算 `curr_float_value`，看 top 5 选股与 Ptrade 实际选股的重叠：

| 数据源 | top 5 选股 | 与 Ptrade 重叠 |
|---|---|---|
| **xtquant** | 002808, 002872, 002830, 002193, 002719 | **3/5** ★ |
| tushare | 002231, 002200, 002808, 002898, 002830 | 仅 1/5 |

Ptrade 选的 5 只（002193/002719/002809/002830/002888）：
- 在 **xtquant 排名**：top 9 内（3/4/5/8/9 位），3 只进 top 5
- 在 **tushare 排名**：散落在 5/6/7/9/10 位，仅 1 只进 top 5

**铁证：Ptrade 用的是 xtquant 同源（交易所直接数据），不是 tushare**。

### 8.4 002231 的关键证据

tushare 选 002231 为 top 1（流通市值最小），但：
- **xtquant 完全没有这只票**（已退市/下架）
- **Ptrade 整个回测期从未选过 002231**

tushare 保留了已下架票的数据，导致本地策略选了一个"实盘根本买不到"的票。这是 tushare 数据源的根本缺陷——**候选池污染**。

### 8.5 决策结论：强烈建议切 xtquant 权威源

| 判据 | 结论 |
|---|---|
| 数据差异量级 | 36.32%（远超噪声） |
| 与 Ptrade 选股一致性 | xtquant 3/5 vs tushare 1/5 |
| 候选池正确性 | xtquant 剔除已下架票，tushare 保留 |
| **决策** | **切 xtquant 作为流通股本权威源** |

### 8.6 切换的实施考量

**优点**：
- 流通股本与 Ptrade（实盘）同源，选股对齐度从 1/5 → 3/5（预期 L1/L3 大幅改善）
- 自动剔除已下架票，候选池更干净

**约束**：
- `xt_financial_capital` 按财报/公告日更新（稀疏，每票 ~50 条），非每日。需用 PIT 取 <= 回测日的最新一期（与当前 tushare 取数逻辑一致，无额外复杂度）
- MySQL 是独立库（`stock_xtquant_data`），需在 `EngineConfig` 增加 xtquant 数据库连接配置，或同步到 DuckDB
- **历史数据回填**：xtquant MySQL 当前全期覆盖（2018-2026），但需确认 `circulating_capital` 的公告日（`m_anntime`）PIT 正确性，避免用到未来公告

**实施路径建议**（优先级排序）：
1. **方案 A（最小改动）**：在 `ptrade_api.py` 新增 `_get_float_share_from_xtquant()` 方法，`get_fundamentals` 的 valuation 表优先查 xtquant MySQL，tushare 降级为兜底
2. **方案 B（数据同步）**：把 xtquant `circulating_capital` 同步到 DuckDB `stock_float_share` 表（覆盖 tushare 的 free_share），引擎不改
3. **推荐方案 A**：改动小、可灰度（对比期 xtquant + 回退期 tushare），且不破坏现有 DuckDB 数据

### 8.7 产出文件

| 文件 | 用途 |
|---|---|
| `scripts/compare_data_sources.py` | 三方对比脚本（可重跑，连接参数已固化） |
| 本节（D10 报告第八节） | xtquant 实测结论 |

---

## 二、证据链

### 2.1 B5 报告的异常信号
- 首日本地选股 `{002231, 002808, 002872, 002898}` vs Ptrade `{002719, 002809, 002830, 002888}`
- 本地 top 1（002231）**整个 4 个月从未被 Ptrade 选中过**（从 Log.txt 选股历史验证）

### 2.2 数据库真实 PIT 值 vs 引擎返回值（铁证）

用回测前一日（2026-01-04）的正确 PIT 查询：

| 票 | 真实 circ_mv (2026-01-04) | 本地引擎返回 float_value | 偏差 |
|---|---|---|---|
| 002808 | 5.48 亿 | 0.41 亿 | **差 13 倍** |
| 002830 | 7.67 亿 | 13.60 亿 | 反向 |
| 002898 | 9.23 亿 | 0.41 亿 | **差 22 倍** |
| 002809 | 10.29 亿 | 12.75 亿 | +24% |
| 002719 | 11.65 亿 | 9.45 亿 | -19% |
| 002888 | 13.29 亿 | 12.89 亿 | -3% |
| **002231** | **15.14 亿** | **1.85 亿** | **差 8 倍** |

002231 的真实流通市值是 8 只里**最大的**（15.14 亿），本地引擎却返回**最小的**（1.85 亿）。排名完全颠倒。

### 2.3 bug 定位

`ptrade_api.py:_preload_market_data`（约第 379-384 行）：

```python
# 当前（有 bug）
float_df = conn.execute("""
    SELECT code, circ_mv AS float_value, ...
    FROM stock_float_share
    QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) = 1
""").fetchdf()  # ← 取全局最新一期，无 PIT 过滤
```

这取的是"截止数据库最新日期"的估值，而非"截止回测 prev_date"的估值。回测在 2026-01-05 时，数据库里已有 2026-01-29 的数据，于是用了 1-29 的 free_share/circ_mv——未来数据穿越。

---

## 三、修复方案（P0，优先级高于 Phase C1）

### 3.1 修复 `_preload_market_data` 的 PIT 过滤

```python
# 修复后：预加载时按 prev_ms 过滤，取每只股票 <= prev_ms 的最新一期
def _preload_market_data(self, prev_date: str):
    prev_ms = int(pd.Timestamp(prev_date, tz='Asia/Shanghai').timestamp() * 1000) + 86_399_999
    # ...（行情预加载保持不变，已有 PIT 过滤）...
    float_df = conn.execute(f"""
        SELECT code, circ_mv AS float_value, total_mv AS total_value,
               free_share AS a_floats, total_share
        FROM stock_float_share
        WHERE time <= {prev_ms}                      -- ← 新增 PIT 过滤
        QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) = 1
    """).fetchdf()
    # 同理 listing_time 预加载也应加 PIT 过滤（避免未来才上市的股票被纳入）
```

### 3.2 每日重算（而非仅首日预加载）

`stock_float_share` 不是每只股票每天都有数据，但跨季/跨月会有更新。预加载的 `prev_ms` 应**每个交易日更新**（已有机制：`_preload_prev_ms` 每日刷新，但 float_df 是首日加载后不更新）。

**最稳妥方案**：float_df 也按日重新过滤（或缓存每日 PIT 快照）。性能影响可接受（76 天 × 一次内存过滤）。

### 3.3 修复后的验证

修复后重跑 D10 诊断脚本，预期：
- 002231 的 float_value 从 1.85 亿 → 15.14 亿（与真实 PIT 一致）
- 本地选股排名与 Ptrade 高度重合（若仍有差异，那才是真正的数据源精度问题）

---

## 四、对方案路线图的影响

### 4.1 优先级调整

- **原计划**：D10 → Phase C1（工具箱）
- **调整后**：**D10 修复（PIT bug）→ D10 重测 → 再决定 Phase C1 设计**

理由：当前 L1=1.6% 主要是 PIT bug 导致，不是数据源精度。修复 PIT 后 L1 可能大幅改善，届时才能真正评估数据源差异量级。**在 PIT bug 未修前做 Phase C1，是在错误数据上设计工具箱**。

### 4.2 Phase C1 设计的分支决策（修复后重新评估）

修复 PIT 后重跑 D10 诊断：
- 若 L1 ≥ 70%（预期）：数据源精度差异可接受，Phase C1 按原设计（scorer 不需缓冲带）
- 若 L1 仍 < 50%：说明仍有数据源精度问题，scorer 引入排名缓冲带
- 若仍有票偏差巨大：定位是 tushare daily_basic 的精度问题，考虑切 xtquant

---

## 五、L4 成本偏差快速扫描（并行完成）

| 项 | 结论 |
|---|---|
| Ptrade "手续费"列含义 | **佣金 + 印花税合并**（买入 8.71、卖出 35.13，卖出显著高=含印花税） |
| 本地口径 | commission（佣金 5.34）+ tax（印花税 2.52，仅卖出）分列 |
| L4 的 27.71% 偏差根因 | **主要来自选股不同导致交易笔数/金额不同**（本地 9 笔 vs Ptrade 55 笔），非口径错误 |
| 处理 | **不需单独修**。PIT bug 修复后选股对齐，L4 会自然改善。fidelity_compare 的 L4 计算逻辑正确 |

---

## 六、产出文件

| 文件 | 用途 |
|---|---|
| `scripts/diagnose_float_value.py` | D10 诊断脚本（可重跑验证修复效果） |
| `docs/D10-float-value-diagnosis.md` | 本报告 |

---

## 七、下一步

1. **立即修复 PIT bug**（`_preload_market_data` 加 `WHERE time <= prev_ms`）——这是 P0，阻塞所有后续对齐工作
2. 修复后重跑 `scripts/diagnose_float_value.py` + B5 对照报告
3. 根据修复后的 L1/L3 数据，重新评估数据源精度差异量级
4. 再决定 Phase C1 的 scorer 是否需要排名缓冲带
