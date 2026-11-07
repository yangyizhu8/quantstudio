# 分钟数据拉取性能复核 + 水位线滞后根因（2026-11-07）

> 探查：节点⑳ ｜ 时间：2026-11-07 21:2x–21:5x ｜ 主库无锁（只读实测确认）
> 数据源：`data/batch_audit.db`（分日批次）、`data/logs/daemon.log`（轮次读数）、主库只读查询、
> **GUI「采集任务」页面截图三张（用户提供，水位状态实况）**
> 关联：`docs/wm-freeze-fix-design.md`（链①）、`docs/evidence/w1-checkpoint-obs1/obs2`、`d4-deadletter-attribution-input-20261106.md`

---

## 一、结论先行

| 层面 | 是否有提升 | 硬证据 |
|---|---|---|
| **单批处理效率** | ✅ **2.6× ~ 8.1×** | 同 raw 量耗时：`stock_minutes` 34,311.8s→13,037.8s；`etf_minutes` 6,226~6,905s→**857.3s** |
| **无效写入削减** | ✅ **-92.4% ~ -100%** | `stock_minutes` 写入 1,145 万→87 万行；`etf_minutes` 624 万→**0 行** |
| **端到端追赶能力** | ❌ **无改善** | 11-07 两个分钟表**直接 FAILED**（服务端 `export_exceeds_time_budget`） |
| **水位推进** | ❌ **四价格表全冻** | GUI 显示 `etf_daily` 09-04、`stock_daily` 09-03、`etf_minutes` 10-09、`stock_minutes` 10-16，状态**「门控保持」** |

**一句话**：**跑得更快了，但没跑得更远。**

---

## 二、性能实测（`batch_audit` 分日聚合，口径已校正）

### 2.1 `stock_minutes`

| 日期 | rows_raw | **written** | 总耗时 | **每百万 raw 耗时** | 阶段 |
|---|---|---|---|---|---|
| 11-01 | 11,450,498 | 11,450,498 | 19,813.8s | 1,730s | 改前（全量重写） |
| 11-02 | 11,450,498 | 11,450,498 | 30,643.8s | 2,676s | 改前 |
| 11-03 | 11,450,498 | 11,450,498 | **34,311.8s** | **2,997s** | 改前（最差） |
| **11-05** | 15,498,714 | **4,067,471** | **17,273.9s** | **1,115s** | **改后（P1+P2+RS）** |
| **11-06** | 16,353,363 | **874,583** | **13,037.8s** | **797s** | **改后** |
| 11-07 | 0 | 0 | 94.4s | — | ❌ 失败（服务端时限） |

⇒ **同 raw 量耗时 34,311.8s → 13,037.8s = 2.63×**；**单位 raw 成本 2,997 → 797 s/百万行 = 3.76×**

### 2.2 `etf_minutes`（raw 完全相同，最干净对照）

| 日期 | rows_raw | **written** | 总耗时 | 阶段 |
|---|---|---|---|---|
| 11-01 | 6,241,659 | 6,241,659 | 5,093.8s | 改前 |
| 11-02 | 6,241,659 | 6,241,659 | 6,609.7s | 改前 |
| 11-03 | 6,241,659 | 6,241,659 | 6,905.0s | 改前 |
| **11-05** | 6,241,659 | **3,615** | **2,286.2s** | **改后（省 99.94% 写入）** |
| **11-06** | 6,241,659 | **0** | **857.3s** | **改后（零写入）** |
| 11-07 | 6,241,659 | 0 | 3,072.2s | ❌ 失败 |

⇒ **6,226~6,905s → 857.3s = 7.3~8.1× 加速**

### 2.3 口径校正（重要，避免误读）

**不可用「written ÷ 耗时 = 行/s」衡量**——RS 让写入量骤降，该指标会**假性下降**
（`stock_minutes` 334 行/s → 67 行/s），因为分母（取数+align+validate 固定成本）未变、分子被 RS 砍掉。

**正确口径 = 处理每百万行 raw 的耗时**（把固定成本摊到反映真实工作量的分母）。
`stock_minutes` 11-06 仅写 87 万行却仍耗 13,038s ⇒ **写入已不再是瓶颈**（改前几乎全耗在写库）。

---

## 三、GUI 实况：水位滞后 100% 集中在四价格表（强证据）

用户提供 GUI「采集任务」三张截图（2026-11-07），逐表读数：

| # | 任务 | 表 | 水位线 | 数据止点 | **水位状态** |
|---|---|---|---|---|---|
| 2 | mcp_etf_daily_backf… | `etf_daily` | **2026-09-04** | 2026-11-05 | **门控保持** |
| 3 | mcp_etf_daily | `etf_daily` | **2026-09-04** | 2026-11-05 | **门控保持** |
| 9 | mcp_stock_daily | `stock_daily` | **2026-09-03** | 2026-11-05 | **门控保持** |
| 74 | mcp_etf_minutes | `etf_minutes` | **2026-10-09** | 2026-10-30 | **门控保持** |
| 75 | mcp_stock_minutes | `stock_minutes` | **2026-10-16** | 2026-11-05 | **门控保持** |
| 1 | mcp_etf_basic | `etf_basic` | 2026-11-07 | — | 正常 |
| 4 | mcp_index_daily | `index_daily` | 2026-11-05 | 2026-11-05 | 正常 |
| 5 | mcp_index_constitu… | `index_constituents` | 2026-10-30 | 2026-10-30 | 正常 |
| 10 | mcp_stock_daily_val… | `stock_daily_valuation` | 2026-11-05 | 2026-11-05 | 正常 |
| 14 | mcp_trade_calendar | `trade_calendar` | **2026-11-07** | — | 正常 |
| 73 | mcp_stock_float_sh… | `stock_float_share` | 2026-11-05 | — | 正常 |
| 11–13 | fin_indicator / income / cashflow | 各类报表 | 2026-06-30 | — | 正常（业务口径不同） |
| 7/70/71 | stock_basic / sw_daily / sw_weight | 无水位 | — | — | 正常 |

**判定**：
- **5 个「门控保持」全部是四价格表**（`etf_daily`/`stock_daily`/`etf_minutes`/`stock_minutes`，其中 etf_daily 有两个任务共用）；
- **所有非价格表水位全部正常**（`trade_calendar` 到 **11-07 当天**，`index_daily`/`stock_daily_valuation` 到 11-05）；
- 滞后幅度：`etf_daily` **62 天**、`stock_daily` **63 天**、`etf_minutes` **21 天**、`stock_minutes` **20 天**。

⇒ 这就是链① `wm-freeze-fix-design.md` 记载的「**同表一冻一活 = 分类不同**」的 UI 级完整实证：
**冻结不是数据源问题、不是任务调度问题，而是「走 defer+gate 的路径」与「走旧路径」的差异。**

---

## 四、根因定位（代码级，本轮新发现）

### 4.1 水位推进的唯一入口（`daemon.py:625` `_advance_or_defer_watermark`）

```python
cfg = self._qfq_config()
if cfg.can_coordinate_watermark(table):          # ← 四价格表
    if self._qfq_cycle_id is not None:
        orch.defer_watermark(...)                 # ← 写意图(pending)，不推进
    else:
        logger.warning("水位保持不动…")            # ← fail-closed
    return
self.writer.advance_watermark(...)                # ← 非价格表：直接推进
```

### 4.2 gate 的提交开关是**单个全局布尔**（`qfq_resident_orchestrator.py:1234`）

```python
def _commit_or_hold_watermarks(self, conn, *, cycle_id, passed, ...):
    intents = SELECT ... FROM qfq_watermark_intent WHERE cycle_id=?
    for source, table, freq, cand in intents:
        if passed:            # ← 一个 passed 控制【全部表】
            advance_watermark(...); intent.status='committed'
        else:
            intent.status='held'   # ← 全部表一起 held
```

### 4.3 `passed` 的判据（`qfq_resident_orchestrator.py:14` docstring + L1119/L1942/L1953）

> `_qfq_gate`：编排级质量门控（**orphan / pending SLA / dead letter / 本轮全 committed**）

```python
if scope:               passed = False   # L1942「scoped reconcile: 全局水位强制 hold」
if detector_degraded:   passed = False   # L1953「因子刷新失败，价格水位强制 hold」
# 另有 orphan：status='in_progress' 且本周期 claim 未终态 ⇒ passed=False（L723-724）
```

### 4.4 **结构性死锁**（本报告最重要的判断）

实测存量（`QualityAudit` 2026-11-07 16:35:03，checks=336）：

| 项 | 读数 | gate 阈值 | 结果 |
|---|---|---|---|
| `QfqDeadLetter` | **17,520** | 0 | 恒超 |
| `QfqPendingSla`（>72h） | **47,199** | 0 | 恒超 |
| orphan（in_progress 未终态） | 本轮 27 死信/27 重试失败下的残量 | 0 | 恒有 |
| 本轮全 committed | `committed=2/60`（Obs-1）/`0`（11-07） | 全数 | 恒不满足 |

⇒ **`passed` 恒为 `False`** ⇒ `finalized_held` ⇒ **四价格表水位永久 `held`** ⇒
**每轮从旧水位重拉同样历史** ⇒ 重拉又产生新的单元失败 ⇒ 死信/超 SLA 存量继续维持 ⇒ **死锁自我维持**。

**这解释了全部现象**：
1. 为什么五张表同时冻（同一个 `passed` 开关）；
2. 为什么 `error=None`（不是异常，是判据不满足）；
3. 为什么数据在进、水位不动（`etf_daily` 数据止点已到 11-05，水位 09-04）；
4. 为什么 W1-B（周期自动恢复）救不了——新周期一样 `passed=False`；
5. 为什么死信数一路涨（16,722→17,520）——重拉不停产生失败。

---

## 五、瓶颈转移图（三轮演进）

| 阶段 | 主瓶颈 | 状态 |
|---|---|---|
| 11-01~11-03 | **写库慢**（invariant 51.8s/批 + 全量重写） | ✅ 已解（P1 34×、RS -92%） |
| 11-05~11-06 | **取数+align+validate 固定成本** | ⚠️ 现占绝对主导（13,038s 中写库仅小头） |
| **11-07 起** | **服务端取数时限** `export_exceeds_time_budget` | ❌ **新阻塞**，两分钟表 2/2 失败 |
| **全程** | **gate 全局 passed 死锁** ⇒ 水位永久 held | ❌ **最根本**，吃掉全部性能红利 |
| 11-07 晨 | DuckDB ART 索引损坏 | ✅ 今晚已修（V1/V2/V3/V6 过） |

---

## 六、数据实态（主库只读实测，21:2x）

| 表 | 行数 | max(time) | 水位（GUI） | 差距 |
|---|---|---|---|---|
| `stock_minutes` | **251,877,433**（11-04：246,976,496 ⇒ **+4,900,937**） | **2026-11-05 15:00**（原 10-30） | 10-16 | **水位落后表内 14 天** |
| `etf_minutes` | 141,527,386（不变） | **2026-10-30 15:00** | 10-09 | 水位落后表内 21 天 |

⇒ `stock_minutes` **数据实际推进了 5 个交易日**，证明 11-05/11-06 轮次写入有效；**水位却不动** ⇒ 下轮继续重拉这段。

---

## 七、R4 沉淀

- `本轮沉淀：N/A`（理由：本件为复核与分析，缺陷结论将在修复方案落地并观察后按族入册；当前为「已定位未修复」态）
