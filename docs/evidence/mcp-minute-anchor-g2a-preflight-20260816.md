# G2a 前置报告：定点重锚范围量化 + 执行注记 1 结论（mcp-minute-front-anchor-closure）

| 项 | 内容 |
|---|---|
| 文档版本 | v1.0（2026-08-16） |
| 对应方案 | `docs/mcp-minute-front-anchor-design.md` v1.1 §4 阶段 2a + §5 |
| 状态 | **待用户批准停机窗口后执行 G2a**（ZCode G2a 前置要求 1/2） |
| 数据来源 | 只读备份 `data/quantstudio_backup_pre_pipeline_20260816.db`（主库被生产 daemon 锁定）+ 当前 `qfq_aux.db`（因子） |

---

## 1. 执行注记 1 结论（ZCode G2a 前置要求 2，硬门禁）

`qfq_reanchor_engine.apply_fresh_minute_staged`（line 1035-1145）的 UPDATE：

```sql
UPDATE {minute_table} AS t SET
  open_front = s.open_front, high_front = s.high_front,
  low_front = s.low_front, close_front = s.close_front
FROM {staged_minute} AS s
WHERE t.time = s.time AND t.code = ? AND t.freq = ?
```

- **仅 SET 四个 front 列**；docstring（line 1052-1054）明确"不触碰 raw OHLC、volume/amount/preClose/*_back/**update_time**/data_source；不 DELETE、不 INSERT、不重建"；
- **结论：§5 硬验收"update_time 逐位零改动"成立，无需修改验收条款**；UPDATE 行级触碰仅 4 列。

## 2. 候选范围量化（ZCode G2a 前置要求 1）

**判别口径**：全历史因子变化点（非仅 120 天除权窗口——G1 实测口径，覆盖所有历史漂移）；
**候选 = FAIL（dev>0.5%）119 只 + WARN（0.3-0.5%）5 只 = 124 只 ETF**（完整清单 `docs/evidence/g2a-candidates-20260816.txt`）。

| 指标 | 数值 |
|---|---|
| FAIL 候选 | 119 只 ETF（dev 0.51% - 258.6%；极端值如 588760 dev=1.0、159300 dev=2.59） |
| WARN 候选 | 5 只（0.3-0.5%）——建议一并修复（同批成本低） |
| UPDATE 行数预估 | **7,491,685 行**（119 只 × 平均 6.3 万 bar；占 etf_minutes 8750 万行 ~8.6%） |
| 备份体积预估 | 二进制 ~300 MB（4×DOUBLE + 键，~40B/行）；CSV 导出 ~750 MB |
| A1 巡检耗时 | **54.7-69.4 s < 300 s 验收线 ✓**（备份库实测） |

## 3. 停机窗口时长预估（供用户批准依据）

| 步骤 | 预估 |
|---|---|
| ① code 级备份（四 front 列 → CSV + SHA-256 清单，750 万行） | 2-5 分钟 |
| ② MCP fresh 拉取（McpFreshFetcher，124 只 × 全历史分钟 ≈ 750 万行） | 10-30 分钟（含网络/解析，分批） |
| ③ precheck（覆盖检查 + raw 逐 bar 对齐，B-1 硬门禁） | 5-10 分钟 |
| ④ UPDATE（仅四 front 列，750 万行） | 1-3 分钟 |
| ⑤ postcheck（minute_staged_match/minute_raw_match/minute_coverage/minute_tick_error） | 5-10 分钟 |
| ⑥ V2 判别复验 + 硬验收（raw/volume/amount/update_time 零改动） | 2-5 分钟 |
| **合计** | **约 30-60 分钟**（含失败重试余量） |

**执行约束（R5）**：主库停机窗口内执行（生产 daemon 暂停增量）；分批（每批 ~30 只）执行，单批失败不影响已成功批次（code 级备份可回退）。

## 4. 执行方案概要

1. **停机窗口**：用户批准时间窗 → 暂停生产 daemon 增量；
2. **备份**：每 code 四 front 列导出 `output/reanchor_backup/<code>.csv` + SHA-256 清单（`manifest.txt`）；
3. **fresh 采集**：`McpFreshFetcher.fetch_none_front`（MCP raw + adj_factor → front，不依赖本地污染因子表——ZCode 注记：③ 类真实份额合并标的统一 fresh_staged 正确）；
4. **重锚**：`apply_reanchor_for_security(model='fresh_staged', fresh_minutes=...)` 逐 code；
5. **postcheck + V2 复验**：minute_* 四项 + 判别公式 MATCH；
6. **硬验收**：raw OHLC/volume/amount/preClose/data_source/update_time **逐位零改动**（复用 minute_raw_match 口径 + 备份对照）；
7. **报告**：结果 + 失败/跳过清单 → ZCode 独立复验（含备份库对照）。

## 5. 风险与回退

- 单 code 失败（fresh 拉取失败/raw 不匹配）→ 该 code 跳过（BLOCK），不影响其他批次；回退 = code 级备份恢复；
- MCP 拉取超时 → 分批重试（31 天窗口拉取已有基础）；
- 极端 dev 标的（588760/159300）优先验证 fresh 数据正确性后执行；
- 停机窗口失败 → 恢复 daemon，重锚批次保持 code 级原子性。

## 6. 请求

**请用户批准**：① 30-60 分钟停机窗口（具体时间窗）；② 124 只候选范围（119 FAIL + 5 WARN 一并修复）。批准后执行 G2a。
