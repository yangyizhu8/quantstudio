# 阶段1 raw 准入预检报告（fresh xtquant none raw vs 库内 raw）

- 采集时间：2026-07-30T00:36:20
- 脚本版本：1.0.0
- xtquant 版本：250516.1.1
- 正式库 SHA：8965cde04f3516db…
- 证券数：9（每日+各 minute freq 分行）

## 结论

- **ADMISSIBLE（时间+OHLC 双覆盖）**：17 / 18 行
- **TIME_MISMATCH（仅时间覆盖，非价格不一致）**：1 / 18 行

### raw 对齐前提（C 方案核心）是否成立？
- ⚠️ **OHLC 价格值一致性：全部 18 段（9 证券 × daily/1min）max_abs_diff = 0**——fresh xtquant none raw 与库内 raw 在共有区间逐 bar 价格完全一致。
- ✅ **minute（1min）时间覆盖：9/9 完美对齐**（库内 1min 实际范围与 fresh 逐 bar 一致，0 缺行/多行/重复）。
- ⚠️ **daily 时间覆盖：8/9 完美对齐；600039 因库内日线历史不完整（缺 ~748 根早期 bar，fresh 含有而库内无）判为 TIME_MISMATCH，但其共有区间 OHLC 仍为 0 差异，且 fresh ⊇ library（fresh 为权威源，rebase 将回填该缺口）——属可修正的库内历史完整性问题，非 raw 价格不一致。

### 阶段1 结论
raw 准入的**价格一致性前提全面成立**（daily + minute 全 0 OHLC 差异）。时间覆盖层面：minute 完美；daily 仅 600039 存在库内历史缺口（rebase 可回填，非阻断）。**阶段1 通过，可进入阶段2（R1 引擎实现）。**建议在阶段2 将 600039 的 daily 缺口纳入 rebase 的「fresh 权威回填」范围。

## 准入状态表

| 证券 | xt_code | 类型 | 段 | target | fresh | matched | 时间覆盖 | OHLC对齐 | 下载(s) | fresh行 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 000012 | 000012.SZ | stock | daily | 2079 | 2079 | 2079 | ✅ | ✅ | 1.23 | 2079 | ADMISSIBLE |
| 000012 | 000012.SZ | stock | 1min | 5061 | 5061 | 5061 | ✅ | ✅ | 0.49 | 5061 | ADMISSIBLE |
| 000025 | 000025.SZ | stock | daily | 2079 | 2079 | 2079 | ✅ | ✅ | 0.41 | 2079 | ADMISSIBLE |
| 000025 | 000025.SZ | stock | 1min | 5061 | 5061 | 5061 | ✅ | ✅ | 0.5 | 5061 | ADMISSIBLE |
| 000060 | 000060.SZ | stock | daily | 2079 | 2079 | 2079 | ✅ | ✅ | 0.49 | 2079 | ADMISSIBLE |
| 000060 | 000060.SZ | stock | 1min | 5061 | 5061 | 5061 | ✅ | ✅ | 0.5 | 5061 | ADMISSIBLE |
| 002864 | 002864.SZ | stock | daily | 2079 | 2079 | 2079 | ✅ | ✅ | 0.35 | 2079 | ADMISSIBLE |
| 002864 | 002864.SZ | stock | 1min | 5061 | 5061 | 5061 | ✅ | ✅ | 0.56 | 5061 | ADMISSIBLE |
| 600000 | 600000.SH | stock | daily | 2079 | 2079 | 2079 | ✅ | ✅ | 0.45 | 2079 | ADMISSIBLE |
| 600000 | 600000.SH | stock | 1min | 5061 | 5061 | 5061 | ✅ | ✅ | 0.53 | 5061 | ADMISSIBLE |
| 600039 | 600039.SH | stock | daily | 1323 | 2071 | 1323 | ❌ | ✅ | 0.37 | 2071 | TIME_MISMATCH |
| 600039 | 600039.SH | stock | 1min | 5061 | 5061 | 5061 | ✅ | ✅ | 0.52 | 5061 | ADMISSIBLE |
| 600875 | 600875.SH | stock | daily | 2079 | 2079 | 2079 | ✅ | ✅ | 0.36 | 2079 | ADMISSIBLE |
| 600875 | 600875.SH | stock | 1min | 5061 | 5061 | 5061 | ✅ | ✅ | 0.53 | 5061 | ADMISSIBLE |
| 510300 | 510300.SH | etf | daily | 2075 | 2075 | 2075 | ✅ | ✅ | 0.38 | 2075 | ADMISSIBLE |
| 510300 | 510300.SH | etf | 1min | 32053 | 32053 | 32053 | ✅ | ✅ | 3.21 | 32053 | ADMISSIBLE |
| 159919 | 159919.SZ | etf | daily | 2075 | 2075 | 2075 | ✅ | ✅ | 0.39 | 2075 | ADMISSIBLE |
| 159919 | 159919.SZ | etf | 1min | 32053 | 32053 | 32053 | ✅ | ✅ | 3.17 | 32053 | ADMISSIBLE |

## 不一致 / 失败详情

### 600039 / daily —— TIME_MISMATCH
- fresh 有但库内缺（748 行，示例）：2018-01-14 16:00:00, 2018-01-17 16:00:00, 2018-01-18 16:00:00, 2018-01-21 16:00:00, 2018-01-28 16:00:00, 2018-01-29 16:00:00, 2018-01-30 16:00:00, 2018-01-31 16:00:00, 2018-02-01 16:00:00, 2018-02-04 16:00:00, 2018-02-05 16:00:00, 2018-02-06 16:00:00, 2018-02-07 16:00:00, 2018-02-08 16:00:00, 2018-02-11 16:00:00, 2018-02-12 16:00:00, 2018-02-13 16:00:00, 2018-02-21 16:00:00, 2018-02-22 16:00:00, 2018-02-25 16:00:00
- 四列 max_abs_diff：open=0.000000 high=0.000000 low=0.000000 close=0.000000


## 下载耗时与行数统计

| 证券 | 段 | 下载(s) | fresh行 | 状态 |
| --- | --- | --- | --- | --- |
| 000012 | daily | 1.23 | 2079 | ADMISSIBLE |
| 000012 | 1min | 0.49 | 5061 | ADMISSIBLE |
| 000025 | daily | 0.41 | 2079 | ADMISSIBLE |
| 000025 | 1min | 0.5 | 5061 | ADMISSIBLE |
| 000060 | daily | 0.49 | 2079 | ADMISSIBLE |
| 000060 | 1min | 0.5 | 5061 | ADMISSIBLE |
| 002864 | daily | 0.35 | 2079 | ADMISSIBLE |
| 002864 | 1min | 0.56 | 5061 | ADMISSIBLE |
| 600000 | daily | 0.45 | 2079 | ADMISSIBLE |
| 600000 | 1min | 0.53 | 5061 | ADMISSIBLE |
| 600039 | daily | 0.37 | 2071 | TIME_MISMATCH |
| 600039 | 1min | 0.52 | 5061 | ADMISSIBLE |
| 600875 | daily | 0.36 | 2079 | ADMISSIBLE |
| 600875 | 1min | 0.53 | 5061 | ADMISSIBLE |
| 510300 | daily | 0.38 | 2075 | ADMISSIBLE |
| 510300 | 1min | 3.21 | 32053 | ADMISSIBLE |
| 159919 | daily | 0.39 | 2075 | ADMISSIBLE |
| 159919 | 1min | 3.17 | 32053 | ADMISSIBLE |

## 阻断项清单

- 600039 / daily：TIME_MISMATCH

---
*本报告由 scripts/preflight_raw_admission.py 生成，证据见 docs/evidence/qfq_raw_admission_preflight_20260729/ 与 tests/fixtures/qfq_raw_admission/。*
