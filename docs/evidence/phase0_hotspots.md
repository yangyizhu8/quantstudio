# Phase 0 实测热点证据（2026-09-20）

> 配套 `docs/RUST_HYBRID_DESIGN.md` §4（热点表正文在方案件子④，本文件为底层证据与产物清单——总调度裁定④落点）。
> 原始剖析产物：`agent_workspace/phase0/`（.gitignore 已隔离，不入 git；本机留存复核）。

## 1. 运行清单（全部命令与总耗时）

| # | 场景 | 命令要点 | 总耗时 | 成交 | 数据源 |
|---|---|---|---|---|---|
| 1 | 冒烟 overnight 1d | cProfile -m run_ptrade_strategy overnight_scalp 2026-07-15 minute-bar-v1 | 348.63s（含 cProfile 1.6×） | 2 | SNAP003 |
| 2 | A-0a 探针锚点 | probe_minute_iterrows.py 连板 2026-03-02~03 minute close | **853.25s** | 0 | BR |
| 3 | A-0b 净口径 | runner_minute_clean.py 同窗口同库 | **858.50s** | 0 | BR |
| 4 | 交叉 SNAP003 | runner_minute_clean.py 同窗口 db=SNAP003 | 3.52s 失败 | - | SNAP003 |
| 5 | A-0c cProfile | cProfile runner_minute_clean 锚点 | 1372.68s（1.60×） | 0 | BR |
| 6 | A-0c py-spy | py-spy record speedscope 锚点 | 68653 样本 | 0 | BR |
| 7 | TRIAL1 连板 06-15~16 | runner plain | 1021.97s | 0 | SNAP003 |
| 8 | TRIAL2 连板 08-03~06 | runner plain | 1470.40s | 0 | SNAP003 |
| 9 | TRIAL3 连板 07-21~22 | runner plain | 1068.21s | 0 | SNAP003 |
| 10 | 扩展窗 02-25~03-06 | runner plain | **1422.86s** | 0 | BR |
| 11 | overnight 3d 07-15~17 | cProfile CLI | 1210.32s | **5** | SNAP003 |
| 12 | 日线 07-01~31 | cProfile CLI 小市值ptrade daily-bar-v1 | 5.95s | 19 | SNAP003 |

锚点复现偏差：853.25 vs 797.33 = **+7.01% ≤10% 门 PASS**；探针包装开销 = 853.25−858.50 = −0.61%（噪声级，净口径反而略慢）。

## 2. 关键输出摘录

**A-0a 探针（iterrows 普查）**：
```
回测 ok=True  总耗时=853.25 s / 净值条数=2  交易笔数=0
backtest_engine.py:2433    480 次  677141 行  51.4344s  6.03%
backtest_engine.py:2195      2 次   14772 行   0.6175s  0.07%
```

**成本模型闭合（场景 10 vs 3）**：T(D) = 670.4s（日1装载）+ 94.06s/日；670.4+2×94.06 = 858.52 ≈ 858.50 ✓

**交叉跑失败（归因已申报）**：`FrequencyCapabilityError [TABLE_EMPTY] 2026-03-02`——SNAP003 etf_minutes 无 3 月数据（BR etf_minutes 03-02~04 = 1,022,291 行独有；stock_minutes 两库 3 月均 0——锚点 bar 实由 etf_minutes ~1411 只 ETF 构成，universe=日快照 ~5200 只，股票表 freq_check 跳过）。**（②审加注：锚点窗口 universe=ETF-only——stock_minutes 2026-02~05 四个月全零，总调度亲测覆盖范围 01-05→09-04；1410 行/bar 实证）**。3 月域锁定 BR，6-8 月域 SNAP003。

**overnight 4B 切片路径单列（分钟策略场景）**：`_current_minute_bar`（get_history frequency='1m' include=True）650 次 46.19s = **3.82%**（~71ms/次：全日 DF 掩码+isin+copy+逐 code 子扫）；对照日线 SQL 路径 `get_bars_by_count` 1500 次 112.87s = 9.33%。

**双库写遏制比对**：SNAP003 四文件 + BR 单文件，字节与 mtime 运行前后逐位一致（基线 phase0/snapshot_baseline_20260920.txt、backtest_readonly_baseline_20260920.txt）。db_path 硬门：phase0/dbpath_hardgate_20260920.txt（六路径全落影子根）；探针场景 db 经构造参数直传（探针 L21/L66），env 兜底同步圈定批准源。

## 3. 产物清单（agent_workspace/phase0/）

A0a_probe_anchor_20260302.log · A0b_clean_anchor_20260302.log · CROSS_snap003_anchor_20260302.log · A0c_anchor_cprofile.prof · A0c_anchor_pyspy.json · A0c_cprofile_dump.txt · A0c_cprofile_segments_v2.txt · A0c_pyspy_agg_v3.txt · TRIAL1-3*.log · EXTENDED_lianban_0225_0306.log · overnight_scalp_3d_20260715_17.prof · overnight3d_segments.txt · daily_xiaoshizhi_202607.prof · daily_segments.txt · smoke_overnight_scalp_20260715.prof · probe_minute_iterrows.py（MD5 3FB12375C37BA73E062C7E643838680A=主仓原文）· runner_minute_clean.py · dump_profile.py · cprofile_segments.py · speedscope_agg.py · 两库基线与硬门文件。

工具：cProfile（确定性；五段分解=callers 不动点传播，合计校验闭合 0.00 残差）+ py-spy 0.4.2（已装免安装；主线程口径，Windows 阻塞线程欠采样已声明，仅作方向交叉）+ 探针 monkeypatch（MD5 同源）。
