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

## 四、根因定位（代码级；§4.4 机理已于 22:0x 按②审运行时证据勘误）

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

### 4.4 结构性死锁（结论保留，**机理已更正**）

> ⚠ **2026-11-07 22:0x 勘误**（②审运行时复核指出，我方核查确认审核方正确）：
> 本节初版写的「gate 用**全表**计数、历史存量 17,520 恒超 ⇒ 死锁」**是错的**。
> 真实机理如下（已按主库只读实测的 `hold_reason` 原文与 `qfq_cycle_run` 终态改写）。

**代码事实（更正）**：FP-6（2026-10-20）**已把 gate 判定域收窄为「本周期 claim ∪ 水位窗口」**，
`QS_QFQ_GATE_SCOPE` 默认 `cycle` 且配置无覆盖。**全表 17,520 死信只进 health 报告，不参与 `passed`。**

**运行时铁证（主库只读实测，2026-11-07 22:0x）**：

```
hold_reason 原文 =
  "dead_letter=428 超过阈值 0;
   trigger d60662a5a98dec84fe25c8fd1c146fa9394ceb0c 未 committed（dead_letter）;
   trigger 04e377495092e340ca64026b7c2d267463f0d02b 未 committed（dead_letter）;
   trigger b36c3fd6111673c8dc4e60a836181bce312269b1 未 committed（retryable_failed）; …"

qfq_cycle_run 终态（近 4 轮）：
  cyc_b2ac05106fd1  finalized_held  exec=60 ok=0  fail=60   11-06 23:03 → 11-07 00:42
  cyc_467e18d0a543  finalized_held  exec=60 ok=0  fail=60   11-06 21:23 → 22:55
  cyc_edc5da5db36d  finalized_held  exec=60 ok=1  fail=59   11-06 20:06 → 21:18
  cyc_0d7c51171df5  finalized_held  exec=60 ok=5  fail=55   11-06 09:14 → 15:32
```

**⇒ 真实根因链（更正）**：

```
水位冻结
  → 重拉窗口巨大（etf_daily 09-04 → candidate 11-05，约 62 天）
  → 窗口内积累死信（实测 dead_letter=428，属"即将提交区间的真实数据空洞"）
      + 本轮 60 个重锚单元全部失败（ok=0；ART 索引损坏 + export 时限所致）
  → gate 双判据【窗口内死信=0】、【本轮全 committed】**双双不过**
  → finalized_held ⇒ 四表水位 held
  → 窗口继续巨大（自维持）
```

**这解释了全部现象（机理更正后仍成立）**：
1. 为什么五张表同时冻——**同一个全局 `passed`**（§4.2 的 L1241，此点审核方确认成立）；
2. ~~为什么 `error=None`（不是异常，是判据不满足）~~ → **更正**：`error=None` 只说明周期未抛异常；
   **单元每轮都在真实失败**（`ok=0/fail=60`），gate 的 hold 是对真实失败的正常拦截，**不是"空转判据"**；
3. 为什么数据在进、水位不动（`etf_daily` 数据止点 11-05、水位 09-04）——单元失败 ⇒ 不提交；
4. 为什么 W1-B（周期自动恢复）救不了——新周期单元仍失败 ⇒ `passed` 仍 False；
5. 为什么死信数一路涨（16,722→17,520）——重拉不停产生新失败。

**⇒ 结论（保留）**：死锁自维持判断成立；**但入口是「单元真实失败」+「窗口内死信」**，
不是「全表历史计数」。**因此"按时间收窄计数"（初版 P0-A）解不了**——窗口内死信本就是该挡的数据空洞。

### 4.5 附：GUI once 轮会制造悬挂周期（本轮新发现，与本报告关联）

主库/日志实测：

```
01:28:26 INFO __main__: [qfq] 协调周期开启 cycle_id=cyc_ba891d0f25b8
06:01:51 WARNING [qfq_orch] FP-3 回收悬挂周期 1 条（终态置 interrupted，水位/intent 未动）：cyc_ba891d0f25b8(started/observing)
```

- `__main__` logger ⇒ **GUI 拉起的 `--mode once` 进程**（非 daemon）；
- 该 once 轮创建 qfq 协调周期后未走完 ⇒ 周期悬挂 `started/observing`；
- 下一个 forever 轮用 **FP-3** 回收为 `interrupted`，**「水位/intent 未动」** ⇒ pending 残留 ⇒ 下轮 supersede。

⇒ 这解释了 `qfq_watermark_intent` 中 **`superseded=164`** 的累积来源之一。
**不是本次冻结的主因，但会持续制造 superseded 噪声。**

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
