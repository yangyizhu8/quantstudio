# D4 归因输入 · 死信/积压明细（日志路径）· 2026-11-06

> 用途：D4（global-segment 四项错误）归因输入；由总调度 2026-11-06 照准「dead_letter 明细顺手取（Obs-2 顺带）」
> 取数方式：**日志路径**（主库被 daemon forever 持锁，`qfq_trigger_queue` 无法只读查询——见 §四）
> 范围：11-06 补跑轮（09:14–17:18）

## 一、单元诊断（单元级失败明细）

`[qfq_orch] 单元诊断 <asset>/<code> status=<s> attempt=<n> err=<原因>`

| 维度 | 读数 |
|---|---|
| 总条数 | **55** |
| status | **blocked × 55（100%）** |
| attempt | **0 × 55（100%）**（从未重试，直接 blocked） |
| asset | STOCK 54 / ETF 1 |

**err 归因族（高度集中）**：

| 条数 | err 摘要 |
|---|---|
| **49** | `rebase 分钟 freq=1min 覆盖不全：缺 964/多 11 行（要求全历史严格一致；allow_partial=True）` |
| 2 | `缺 482/多 10 行` |
| 1 | `缺 482/多 1523 行` |
| 1 | `缺 482/多 11 行` |
| 1 | `缺 1205/多 11 行` |
| 1 | `缺 723/多 11 行` |

⇒ **单一根因族**：分钟表 rebase 的「全历史严格一致」判据 vs 实际缺行（482~1205）。`allow_partial=True` 已允许部分，但仍 blocked ⇒ 判据与容差之间有缺口。

## 二、export_cache 重取无 shard（链②实证）

`[MCPAdapter] export_cache 重取无 shard（ckey=<表>|<起>|<止>）：本批降级为空、不写 manifest，周期继续`

| 维度 | 读数 |
|---|---|
| 总条数 | **1,254** |
| 按表 | **stock_minutes 1,226** / etf_minutes 28 |
| 按年 | 2026 **1,238** / 2025 16 |

**因果链（本文最重要结论）**：

```
服务端 export 无对应分片（链②：part_000NN 纯序号、客户端无法定位）
  ⇒ export_cache 重取无 shard ⇒ 该日期区间降级为空
  ⇒ 分钟表历史覆盖不全（缺 482~1205 行）
  ⇒ rebase「全历史严格一致」判据不通过
  ⇒ 单元 blocked / dead_letter
  ⇒ 水位 hold（held=3）
```

⇒ **D4 的死信与 W1 的水位冻结共享同一上游根因（服务端分片缺失）**，与链①「gate 不提交」是**两级问题**：
- 一级（上游）：数据取不全 ⇒ 单元 blocked（本件，链②域）
- 二级（下游）：即便单元成功，水位仍走 defer/gate（链① W1 域）

## 三、QualityAudit 计数（16:35:03，checks=336）

| 项 | 读数 | 级别 |
|---|---|---|
| **qfq_trigger_queue/QfqDeadLetter** | **16,722**（dead_letter=16,722） | ERROR（阈 0） |
| **qfq_trigger_queue/QfqPendingSla** | **48,089**（pending/retryable_failed 超 72h SLA） | ERROR |
| stock_minutes/AdjustmentAnchorDrift | **154**（FAIL>0.5%） | ERROR |
| etf_minutes/AdjustmentAnchorDrift | **66** | ERROR |
| etf_daily/RequiredValueNull | 6（open/high/low 各 6） | ERROR |
| etf_daily/AdjustmentCompleteness | 6（front） | ERROR |
| etf_daily/AdjustmentFactorConsistency | 2（front） | ERROR |
| 汇总 | **9 类错误 + 32 类警告** | — |

## 四、取数限制（如实申报）

- `qfq_trigger_queue` 位于**主库**（`quantstudio.db` 63.8 GB），daemon `--mode forever` 常驻持锁 ⇒ **无法只读查询**；
- 本件全部读数来自**日志**（`单元诊断` / `export_cache 无 shard` / `QualityAudit` 三类行）；
- 若需**库级明细**（trigger_id / 每 code 的 attempt 历史 / effective_date 分布）⇒ 须**无锁窗**（daemon 收工后）另行取数，属候裁项。

## 五、今日 2 个 FAILED 任务（旁证，归运维面）

| 任务 | 错误 |
|---|---|
| `mcp_llm_text_events_enriched_pt` | `create_export_job 服务端错误: export_exceeds_tim…`（服务端限制） |
| `mcp_etf_minutes` | `线1 还原缺因子：etf_minutes/1min 8952 行无法还原，涉及 373 个 code，fail-fast`（**正确的 fail-fast**：放行会把 qfq 当 raw 写入主库造成双重复权） |

## 六、对 D4 / W1 的分流建议

| 问题 | 归属 | 建议 |
|---|---|---|
| export_cache 无 shard（1,254）、单元 blocked（55）、DeadLetter（16,722）、PendingSla（48,089） | **D4 + 链②** | 服务端分片补齐是主要解；客户端不宜强行绕行 |
| held=3 水位保持 | **链① W1** | 待 Obs-2 三读数后呈裁 A/B |
| AdjustmentAnchorDrift（stock 154 / etf 66） | D4 子项 | 与「上游可变因子」族相关，判据族见铁律 25 |
