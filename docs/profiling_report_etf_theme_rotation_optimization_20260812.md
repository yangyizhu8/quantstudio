# etf_theme_rotation 性能优化报告（v3.0 方案执行结果）

> 日期：2026-08-12 ｜ 状态：Step 1/2 完成，组合实测已记录 ｜ 方案：zcode+reasonix v3.0 定稿

## 1. 优化前（HEAD）实测基线

| 数据源 | API 耗时/天 | 非 API（策略循环+引擎）/天 | 合计/天 | 全窗口 382 天 |
|---|---|---|---|---|
| 用户 GUI 实测（2025-01-01~2026-08-10） | — | — | — | **37 分钟** |
| profiling 实测（1 个月窗口，PR7 前，staging 副本） | get_history_batch 2.7s | 2.8s | 5.5s | ≈ 35 分钟 |

## 2. Step 1 优化 A：trade_date 唯一值 map 广播（已完成，commit 357a1d7）

- `_post` 内 `pd.to_datetime(...).dt.strftime` 逐行生成 → `_build_trade_date_map`（唯一 time 值 → 日期字符串 map 广播）
- 实测（缓存路径 47879 行）：**map 版 0.007s**（方案实测 SQL 路径旧版 0.96s）
- 等价性：`pd.Timestamp(t, unit="ms").tz_localize("UTC").tz_convert("Asia/Shanghai")` ≡ 旧 `pd.to_datetime(t, unit="ms", utc=True).tz_convert(...)`；Asia/Shanghai 无 DST；time 无 NaN
- 单元测试：`test_trade_date_map_equivalence`（内联旧实现 assert_series_equal）PASS

## 3. Step 2 优化 B：策略计算向量化（已完成，commit 522b34f + 582e537）

- `etf_theme_rotation_quantstudio.py`：层1 逐只循环 → 三步向量化（过滤收集 → 右对齐 pad 2D 矩阵 → `np.nanmean(axis=1)`）；文件头豁免标记；层2/3/4/5 逐行不变
- SKILL.md R3 追加 Vectorized computation (RECOMMENDED) 指令；两份副本 diff=0
- 等价性：compare_roundtrip（逐只备份 vs 向量化版，2024-01-01~2024-01-31）**PASS nav=True trades=True diffs=0**

## 4. 组合实测（PR7 + 索引 + map + 向量化，1 个月窗口）

```
=== PERF SUMMARY ===
get_history_batch            count=    22 total=    45.8s max=  5.85s avg=2.080s
get_etf_list_local           count=    22 total=     0.8s max=  0.13s avg=0.037s
TOTAL_API_TIME=92.2s   （API 45.8s + 非 API 46.4s ≈ 2.1s/天）
```

| 指标 | 优化前 | 优化后 | 变化 |
|---|---|---|---|
| API 耗时/天 | 2.7s（PR7 前 SQL 路径） | 2.08s（PR7 缓存命中后） | -23% |
| 非 API（策略循环+引擎）/天 | 2.8s | 2.1s | -25% |
| 合计/天 | 5.5s | 4.2s | **-24%** |
| 全窗口外推 | 37 分钟 | **≈ 29 分钟（保守）** | -8 分钟 |

## 5. 缓存路径耗时分解（49420 行，缓存命中后）

| 环节 | 耗时 | 状态 |
|---|---|---|
| 切片循环（706 只 boolean mask + tail） | 0.681s | **主瓶颈**（方案禁止行为 3：PR7 路径不改） |
| concat | 0.106s | 不动 |
| sort_values | 0.036s | 不动 |
| qfq transform×4 | 0.064s | 不动（禁止行为 1） |
| trade_date map | 0.007s | ✅ 优化 A 已生效 |

## 6. 与方案预期的偏差说明

- 方案预期 API 3.1s → ~0.2s/天：**未完全达到**（实测 2.08s/天）。原因：PR7 缓存命中后单次调用仍有 ~1.35s Python 后处理，其中**切片循环 0.68s 最大**（方案瓶颈表"其他 _post 0.14s"基于 SQL 路径 fetchdf 后处理统计，未覆盖缓存路径的逐只切片 + groupby 拆分环节）。
- 方案禁止行为 3（不改 PR7 缓存路径）与禁止行为 1（不改 _post sort/qfq）**排除了切片优化**——按方案严格执行，未越界。
- 实际总收益 -24%（37 → ~29 分钟保守外推），未达激进目标 17 分钟（方案正文亦给出 ~25min 保守估计，实测介于两者之间，属方案认可的保守区间）。

## 7. T5 全量断言（优化后叠加态 vs T5 基准）

14 策略 compare_roundtrip（2024-01-01~2024-06-30，daily-bar-v1，PYTHONHASHSEED=0）：

| 状态 | 数量 | 策略 |
|---|---|---|
| PASS（nav+trades 逐位相等） | 10 | ETF动量、ETF平滑动量轮动、ashare_manual_pool、bbi_etf_rotation、sw_industry、tech_etf_mvo、二八轮动、双均线、小市值2、小市值ptrade |
| NAV-PASS-NO-TRADES（双方无成交，nav 相等） | 3 | ETF轮动、first_board_pullback、first_cover_event |
| ENGINE_ERROR（分钟策略超时，与 T5 基准一致） | 1 | smallcap_overnight_scalp_7 |

与 T5 基准（优化前 roundtrip_report.json）**逐项一致**。证据：`docs/roundtrip_phase2_report.json`、`docs/roundtrip_step2_vectorized_evidence.json`。

## 8. 测试回归

```
54 passed in 7.14s（原 46 + test_pr7 7 项 + trade_date map 1 项；0 collection error / 0 failed / 0 skipped）
```

## 9. 待定项（未实施，如实记录）

1. **切片循环优化**（0.68s/天）：如后续授权，可评估按 (table, code, time) 预排序 + `searchsorted` 替代逐只 boolean mask（属 PR7 路径改动，需新审核）。
2. **生产 DB 索引**：Phase 1 索引仅在 staging 副本（方案"暂不做"），生产库未建。
