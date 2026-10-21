# 证据：rebase 一致性量级谱取证（firefly 水位案·专项①，2026-10-21 10:00–10:30）

- 取证人：客户运维会话（firefly 案 N5 延伸）
- 方式：**全只读**（qfq_aux.db 以 `mode=ro` 打开；两快照 parquet 直读；在线 MCP 单码只拉取；
  主库因 daemon 独占锁全程未触碰——文件级 Copy-Item 亦被拒，取证改走「双快照 + 因子序列」路径）
- 素材留存：`data/mcp_landing/exp_etf_daily_{00278ba0,a3f75693}`（两时间点导出快照）、
  `data/qfq_aux.db::fund_adj / qfq_factor_revision_alert`

## 1. 结论（一句话）

**三条候选收敛为候选②：上游 ETF 复权因子（fund_adj）历史序列被持续微调，
库内 raw 是"旧因子快照"的还原产物，而 rebase 预检用"现因子"还原 fresh——
两者在因子微调覆盖的历史段产生 1e-4~1e-1 的比例差，永远过不了 |Δ|>1e-9 的逐位判据。**

候选①（判据过严/浮点末位）与候选③（float 精度）被量级谱**排除**：差异不是 1e-9~1e-7 末位噪声，而是比例差。

## 2. 证据链

### 2.1 因子序列跳变谱（qfq_aux.db::fund_adj，code=513530，7402 行）

| 指标 | 值 |
|---|---|
| 相邻跳变点（\|af_t/af_{t-1}−1\|>1e-9） | **1347 个** |
| 其中大跳（>1e-4 相对） | **889 个** |
| 按年分布 | 2025：281；**2026：1066** |
| 典型形态 | **成对 round-trip**：同日先 0.999704 再 1.000296（来回微调）；单日最大 1.0123（2025-06-15）；历史最大 **+9.4%（2026-07-01，af 1.0→1.0939）** |
| 累计幅度 | af 1.0 → 1.1223 |

**判读**：除权/分红的因子阶梯应是**单次跳变**；1347 个跳变中大量为**同日成对往返**且 2026 年几乎逐日出现——
这是上游对历史因子的**重算/重标定**，不是公司行为。ETF 被抽样的 3 只（513530/515180/530880）全部 blocked，
blocked 行数（1061/1645/461）与 2025–2026 微调覆盖段吻合。

### 2.2 近窗对照（两导出快照，各 24 行/码）

`exp_etf_daily_00278ba0`（前次）与 `exp_etf_daily_a3f75693`（本轮 23:53）在共同增量窗内
close/adj_factor **逐位一致（0 不一致行）** ⇒ 上游**近窗无漂移**，差异集中在**历史段**——与 2.1 的年度分布互证。

### 2.3 框架自身已感知因子漂移（旁证）

- 在线拉全历史 fresh 被框架安全闸拒绝：
  `ValueError [MCPAdapter] etf_daily/daily factor snapshot synchronization failed; refusing qfq restore with a potentially stale anchor`
  ——MCPAdapter 检测到**因子快照不同步**，拒绝用可能陈旧的锚做 qfq 还原；
- `qfq_aux.db::qfq_factor_revision_alert` 已累计 **137 条因子修订告警**——同族现象的既有留痕。

## 3. 结构性矛盾（设计层根因）

```
写入侧：raw = qfq ÷ af(写入时快照)          → raw 隐含因子版本
校验侧：fresh raw = qfq ÷ af(现快照)，要求与库内 raw 逐位一致（|Δ|≤1e-9）
上游现实：af 历史序列持续微调
⇒ 每次因子微调后，库内历史 raw 即"过时"；rebase 预检将其 block；
   而 rebase 恰是唯一能重写库内 raw 的操作 —— 自锁。
```

该矛盾解释了全部现象：blocked 单元全覆盖、committed=0、gate 永不通过、水位自 09-03 冻结、
`qfq_trigger_queue` dead_letter 11,993 持续增长（同一批触发反复重试反复 block）。

## 4. 修复方向（供专项方案选项，本证据文档不实施）

| 方向 | 内容 | 判型/量级 |
|---|---|---|
| **B·因子等价识别**（最小） | 预检判据升级：若 Δ 可由 af_t'/af_t 比值解释至 1e-9（即 qfq 序列实际一致，仅因子版本不同），**不 block**，只触发 front 重算 | 行为修正型（数据正确性），改动限 `qfq_reanchor_engine` |
| **C·因子版本固化**（过渡） | 写入时把当次 af 快照存档（qfq_aux 已有 observation/revision 骨架），rebase 预检改用「写入时版本」还原比对——校验传输完整性而非因子时效 | 新增列/表 + 校验换源 |
| **A·raw 与因子解耦**（终态） | 库内改存 qfq 原值 + 因子版本（或 raw + af 快照列），读取时按需还原；raw 一致性判据退化为 qfq 一致性 | 数据模型变更，最大 |

## 5. 复现命令（只读）

```python
# 因子跳变谱
sqlite3 file:.../qfq_aux.db?mode=ro "SELECT time,adj_factor FROM fund_adj WHERE code LIKE '513530%' ORDER BY time"
# 双快照近窗对照
duckdb: read_parquet('exp_etf_daily_00278ba0/*.parquet') JOIN exp_etf_daily_a3f75693 ON ts_code,trade_date
# 框架因子闸复现
McpFreshFetcher(...).fetch_none_front("ETF","513530.SH","1d","20180101","20261021")
# → ValueError: factor snapshot synchronization failed
```
