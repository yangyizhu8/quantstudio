# S1 A′ 生产首验件（2026-11-06 首轮实读）

- **回签方**：主仓 dev 会话（节点⑤）｜**验证对象**：`docs/design-s1-baseline-recent-median-20261105.md`（A′ 近期窗口中位 N=60）
- **生效方式**：daemon 自然重启（pid=5908 · 2026-11-06 09:14:47 起）⇒ 新基准首次进入生产路径

## 一、首验（etf_daily · 09:16）

| 项 | 昨日 11-05 同批 | **今日 11-06 同批** |
|---|---|---|
| S1 行级校验 | **判不过** `code_count_out_of_tolerance` | **通过** `ok_degraded_calendar_unavailable` |
| poison | 复现 1/3 | **未复现** |
| 水位 | 不动（09-03） | **推进** `candidate=1793808000000`（延迟提交·cycle=cyc_0d7c51171df5） |
| 落库 | 0 | **81,801 行**（新增 10,942 + 更新 20,862） |
| Validator | — | `passed=31804 rejected=0 fixed=0 warned=0`（首分片） |
| 阶段时延 | — | `shards=2 rows=81801 total=91.6s` |

日志锚：
- `09:16:22 WARNING writers: [DuckDBWriter] etf_daily batch=…0aa916 own 冲突耗尽但行级校验过（reason=ok_degraded_calendar_unavailable…）`
- `09:16:25 INFO validator: etf_daily batch=…0aa916: passed=31804 rejected=0`
- `09:16:27 INFO daemon: [mcp_etf_daily_…] ✅[streaming] raw=81804 aligned=81804 passed=81801 rejected=3 written=81801`

## 二、全轮侧证

| 项 | 读数 |
|---|---|
| 今日轮次 | 09:14:48 起 → **17:18:55 结束**（单轮 8h04m） |
| **S1 拒绝行（全轮）** | **0**（无任何 `code_count_out_of_tolerance`）⇒ A′ 口径修复**在全轮持续成立** |
| poison 键 | 未复现 |
| 其他失败 | 1 条 = `mcp_etf_minutes`（09:54）——**还原缺因子**面（11-05 已登记新开放项·**与 A′ 不同因不同层**） |

## 三、结论与限定（如实）

**结论**：A′ 在 etf_daily 上达成**数据修正型预期修正**——同一批次数据（81,801 行）在旧基准（1,060）下必判不过、在新基准（近期 60 日中位 2,117）下通过并推进水位。

**限定**：
1. **仅覆盖 etf_daily 一表**——其余表码数稳定（校验本就通过），不构成新证据；
2. **`ok_degraded_calendar_unavailable`** = 日历不可得降级口径（该分支判据本案未改）；首验走的是降级分支，**非 full 日历分支**——如实标注；
3. 水位为**延迟提交**（QFQ 协调周期 `cyc_0d7c51171df5`），最终落定待周期结束；
4. **etf_minutes 缺因子失败与本案无关**（还原面 vs 校验口径面）。

## 四、机检

`python -m pytest tests/test_watermark_decouple_s1.py -q` ⇒ **27 passed**；`N=60→2,117 / N=30→2,158 / 全表→1,060（缺陷态）`（生产库只读直读）。
