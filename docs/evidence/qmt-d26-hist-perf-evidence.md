# D-26 卷证据：QMT 漏斗取数层性能修复与贯通（2026-10-12）

> 判型：**D-26/D-26b/D-26c = 新增检测型**（只增诊断/进度，不改取值）→ **D-26d = 纯性能型**
> （可观察结果不变；口径与证据见 §5）
> 六步流水线：③实施+④验收（本文件）→ ⑤用户确认 → ⑥双仓推送。
> 设计依据：`docs/qmt-progress-audit-design.md` v1.2（D-24 卷）｜前序：D-23（`qmt-l1-listed-date-norm-20261012.md`）

---

## 1. 卷宗脉络（四轮递进，每轮均以端测实证推进）

| 轮 | 动作 | 判型 | 端测结论 |
| --- | --- | --- | --- |
| **D-26** | `_QS_QMT_LOG_EXT` 加进度 helper；`_qs_fin_to_rows_hist` 加 `to_frame()` 批量路径 + `to_dict('records')` 取代 `iterrows` | 新增检测型 | 性能 509s（batch 生效），但 **`QSFUNDHIST kept=0`** → 引正确性事故 |
| **D-26b** | `.loc[_code]` 取不到即回落 `raw[_code]`（batch 仅作加速尝试）；加 `QS_QMT_HISTDBG` 诊断行 | 纯恢复型 | `miss=3666 rows=3666` → **回落 100% 生效、`kept=9718706 cc=n` 正确性恢复** ✅ |
| **D-26c** | 结构探针 `QS_QMT_HISTDBG2`（不改取值） | 新增检测型 | **结构定谳**（见 §2）：`.loc` 假设方向错 + 契约超量 200× |
| **D-26d** | 删「请求字段全 NaN」行（`dropna(how='all')`） | 纯性能型 | ✅ **全漏斗贯通 + 首次产生交易**（见 §3），耗时 10 分钟/轮 |

---

## 2. 结构定谳（D-26c 端测实证，23:03）

```
QS_QMT_HISTDBG2 raw=Panel frame=DataFrame levels=2
  idx_names=['major', 'minor']
  idx_head=[('20260923','inc_revenue_rate'), ('20260923','m_anntime')]
  cols=['000001.SZ','000002.SZ','000006.SZ','000007.SZ']
  one_shape=(8739, 3)
  one_idxcls=Index  one_idx_head=['19901219','19901220','19901221']
```

| 发现 | 说明 |
| --- | --- |
| **`raw.to_frame()` 结构** | index = MultiIndex(**major=日期, minor=字段**)，**columns = 股票代码** |
| **D-26 `.loc` 全 miss 的原因** | 我按「index 以 code 为首层」写 `.loc[_code]`——**方向完全相反**（`miss=3666` 与实测一致） |
| **契约超量** | `raw[_code]` 返回 **8739 行/只**（1990-12 起日频全历史），而「PIT 全报告期」契约只需 ~40 期 → **超量 ~200×** |
| **耗时真因** | 不在"转换方式"，而在**数据量本身**（3200 万单元格）——这是 8.5 分钟/表的根因 |

---

## 3. 最终端测证据（23:17–23:27，全区间贯通；用户实测）

```
QS_FUNNEL_AUDIT rebalance_id=20260105 date=2026-01-05
  L0_all=5227 L1_listed=4410 L2_board=3816 L3_status=3666
  L3v_complete=3663 L4_growth=366 L5_smallcap=30 L6_eps=17
  R_rankable=16 R_selected=10  note=ok            ← 不再是 empty_after_L6
QS_REBALANCE_AUDIT selected=10 tradable=10 buy_submitted=7
QS_PORTFOLIO_AUDIT positions=7 cash_ratio=0.3076 gross_exposure=0.6924
[QS-QMT log.debug] day_end positions=7 cash=308609.49 total_value=1003261.49
```

| 案 | 判据 | 结果 |
| --- | --- | --- |
| **D-23** | `L1_listed > 0` 且**产生交易** | ✅ `L1_listed=4410` → 全漏斗 → **`R_selected=10` / `buy_submitted=7` / 持仓 7 只 / 总资产 1,003,261.49** |
| **D-24** | 四类 func 进度行 + begin/end 配对 | ✅ 全程可见，无静默段 |
| **D-19 契约** | `L3v_complete > 0` | ✅ **3663**（此前恒 0） |
| **D-26** | hist 段从卡死 → 可用 | ✅ 509s → **127s / 109s**（4×），全区间跑通 |

### 3.1 `dropna` 效果（读数）

| 轮次 | `kept` | `drop` | 合计 |
| --- | --- | --- | --- |
| D-26c | 9,718,706 | 22,318,468 | 32.0M |
| **D-26d** | 9,718,706 | **9,168** | **9.7M** |

→ `dropna(how='all')` 删掉 **2200 万行全 NaN 行**（32.0M → 9.7M，3.3×）✓
→ 剩余 9.7M（≈2654 行/只）为「部分有值」行（QMT 对财报字段公告后持续有效）→ 彻底收敛需按 `m_anntime` 过滤 → **D-27 候批**。

### 3.2 性能画像（10 分钟/轮）

| 段 | 耗时 | 优化方向 |
| --- | --- | --- |
| `query_last_close` 3666 只 | 121s | 批量化 |
| `fin_to_rows_hist` ×2 | 127 + 109 = 236s | 按 `m_anntime` 过滤（2654 → ~40 行/只） |
| `get_financial_data` ×3 | 43 + 83 + 52 = 178s | QMT 端取数（不可控） |
| L1 + L3 + 批量前置 | ≈1s | 已达最优 |

---

## 4. 实施中发现的缺陷与修正（诚实记录）

| # | 缺陷 | 轮次 | 处置 |
| --- | --- | --- | --- |
| 1 | `.loc[_code]` 静默空 → `kept=0` → 零交易（**拆东墙式事故**） | D-26 | D-26b 加回落（batch 仅作加速尝试）；**教训：mock 无法代替真实结构** |
| 2 | 探针自身 `x or []` 对 pandas **Index 求布尔** → `ValueError: truth value of a Index is ambiguous` | D-26c | 改显式 `None` 判定，全面禁用 `or []`（该异常在 try 内，未影响主逻辑） |
| 3 | `dropna` 未覆盖 `_one` 非 DataFrame 情形 | D-26d | `try/except` 保护 |

> 缺陷 1 的直接代价：一轮端测白跑（22:56–23:10，用户成本）。**根因定谳**：D-26 的黄金对照基于 self-made fake Panel（Index=code），而真实 QMT Panel 的 `to_frame()` 方向相反——**本地全绿 ≠ 真实成立**。

---

## 5. 判型口径声明（D-26d＝纯性能型）

**争议点**：`dropna(how='all')` **改变了 wrapper 的返回值行数**（32.0M → 9.7M）。

**口径与依据**：
- **可观察对象＝回测结果**（选股/订单/净值），非 wrapper 中间返回值；
- 消费端**显式丢弃 value=NaN 行**：
  ```python
  # quantstudio/backtest/strategies/weekly_smallcap_growth_momentum_10_quantstudio.py
  :149  mask = ~np.isnan(val)
  :158  frame = frame[mask]
  ```
- 故预删「请求字段**全** NaN」的行**不可能改变消费结果**（被删行本就进不了 `mask`）；
- `QSFUNDHIST` 审计行（`kept/drop`）本就是**契约对齐度指标**，其 `drop` 语义即"无有效值行"——本修复使该指标回归真实。

**验收口径**：D-26d 的黄金对照为**消费级等价**（策略 mask 前后一致），**非** wrapper 返回值逐位一致——此差异已由用户批准（"同意按 B（判型仍为纯性能型）实施"）。

**残留口径风险（如实登记）**：若**其他**策略消费者对 `get_fundamentals` 的 NaN 行有依赖（未做 mask），则其行为会变。**已查证**：本次仅 weekly_smallcap 走 hist 路径（CANSLIM 亦消费但走同一 mask 语义，`:406` groupby 拆分前亦剔 NaN）→ **候 D-27 做全策略消费面审计**。

---

## 6. 回退点与验证链

| 层 | 标识 |
| --- | --- |
| stash 快照链 | `9912f07`(D-26) → `2266e36`(D-26 vectorize) → `bdd7efb`(D-26b) → `b23e724`(D-26c) → **`26cf345`(D-26d)** |
| 文件级备份 | `docs/evidence/_bak/source_import_qmt.py.bak-20261012-d26{,b,c,d}`（各轮 SHA256 双向核对） |
| 矩阵哈希 | `qmt_wrapper_hash` 逐轮追认（终值 `75108c301bf3`） |

**验证链**（各轮回退均执行）：单测（49 既有 + D-24 15 + D-26 6 = **70 passed** 零衰减）+ 六策略重转 **ALL 6 PASS (enc=gbk)** + `check_fund_matrix --check` 绿。

---

## 7. 未闭合项

1. **正式 archify 影响面图**（§10；D-24 卷裁定②并入本轮）。
2. **D-27**（候批）：① 按 `m_anntime` 过滤行（10 分钟 → 目标 1 分钟）；② `query_last_close` 批量化；③ 全策略 `get_fundamentals` 消费面 mask 审计（§5 残留口径风险）。
3. wrapper 键名差异补全（D-23 登记 P2，候裁定）。
