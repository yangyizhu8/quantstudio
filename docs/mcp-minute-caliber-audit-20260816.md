# MCP 分钟数据复权口径审计（raw + adj_factor 客户端复权链路）

| 项 | 内容 |
|---|---|
| 文档版本 | v1.1（2026-08-16） |
| 审计范围 | 分钟数据（`stock_minutes` / `etf_minutes`）拉取入库管线的复权口径处理 |
| 前提（用户确认） | **MCP 是当前唯一权威源**，其他所有数据源（xtquant/tushare/akshare/baostock）全部废弃，不再考虑 |
| 前提（用户确认） | 当前云端分钟数据默认是**不复权的原始数据**（raw） |
| 治理目标（用户提出） | 云端写入统一 raw（治本）→ 巡检分钟口径（早发现）→ 巡检新鲜度/任务（防缺失）→ 任何一环出问题：巡检 WARN/FAIL 报警 + 修复提示，不再静默积累 |
| 状态 | **审计 + 实测完成**（2026-08-16 回填完成后 V1-V4 实测）；核心风险证伪，锚点漂移实锤 |

---

## 1. 云端分钟契约（`config/mcp_dataset_requirements.json` coverage_matrix）

| 项 | 内容 |
|---|---|
| 云端表 | QuestDB `stock_minutes`（约 4.79 亿行）/ `etf_minutes`（约 1.20 亿行） |
| 列结构 | `ts_code, trade_time, freq, open, high, low, close, vol, amount, adj_factor, is_qfq` |
| designated_timestamp | `trade_time` |
| **adj_policy** | **`raw + adj_factor (客户端复权)`**（etf_minutes 为 `raw + adj_factor`） |

> **结论 1**：云端契约声明的分钟口径 = **raw 价格 + 逐 bar adj_factor 因子列，复权由客户端（本地）负责**——与用户"云端分钟默认是不复权原始数据"的陈述一致。

---

## 2. 本地 MCP 分钟处理链（唯一权威源视角）

```
云端 QuestDB（raw 价 + adj_factor + is_qfq）
  │
  ▼ mcp_adapter.fetch_table / fetch_table_streaming（daemon 流式分片）
  │   线1 _restore_qfq_if_required → _restore_to_raw
  │     ⚠️ 全部行走还原公式：raw_i = qfq_i × adj_latest_global / adj_i
  │     （P1-③ 2026-08-03 实测定论，不按 is_qfq 分流；_RESTORE_TABLES 含分钟表，
  │       mcp_adapter.py:273-274；无 is_qfq 列时默认整批 qfq，line 1710-1713）
  │
  ▼ daemon（daemon.py:925-942）
  │   分片带 adj_factor → normalize_mcp_adj_factor_df 提取 adj_factor_df
  │   → 从分片移除 adj_factor 列（避免 merge 冲突）
  │
  ▼ aligner.align（aligner.py:415-422）
  │   MCP 非原生复权源 → _apply_qfq：front = price × adj_i / adj_latest
  │   （前提：price 列必须是真 raw；否则双重错误）
  │
  ▼ writers 落库（writers.py:132-153 DDL）
  │   raw OHLC + *_front + *_back + *_front_ratio/_back_ratio（ratio 填 NULL）
  │
  ▼ 读侧（duckdb_data_access.py:637-710）
      引擎 fq='pre' 分钟查询 → *_front 列就地替换（open_front AS open 等）
```

**环节证据索引**：

| 环节 | 代码位置 |
|---|---|
| 三段式 xtquant 拉取（已废弃，仅供历史参考） | `quantstudio/pipeline/sources/xtquant_adapter.py:218-254` |
| 线1 is_qfq 还原（无条件，含分钟） | `quantstudio/pipeline/sources/mcp_adapter.py:1624-1761` |
| 还原白名单含分钟表 | `mcp_adapter.py:271-274` |
| 还原 fail-fast 开关 | `mcp_adapter.py:275-277`（缺 adj_factor → raise） |
| daemon 流式 adj_factor 提取 | `quantstudio/pipeline/daemon.py:925-942` |
| aligner 原生直通 / 因子计算分治 | `quantstudio/pipeline/aligner.py:406-435` |
| 分钟表 DDL（raw + front + back） | `quantstudio/pipeline/writers.py:132-153, 970-983` |
| 读侧 fq='pre' 分钟路径 | `quantstudio/backtest/providers/duckdb_data_access.py:637-710` |
| QFQ fresh capture 分钟 front 重建（raw + adj_factor → front） | `quantstudio/pipeline/qfq_fresh_capture.py:738-769` |

---

## 3. 风险清单

### 3.1 🔴 核心风险：线1 还原与"云端分钟 raw"错配 → 两列语义互换 —— **实测证伪（2026-08-16）**

`_restore_to_raw` 的设计假设是"**云端存前复权价**"（注释：日线实测 `is_qfq=True` 占比 100%），
因此把收到的价格**一律当 qfq 反解回 raw**（P1-③ 实测定论，不按 is_qfq 分流）。

理论推演：若云端分钟按契约存**真 raw**，则线1 还原与 aligner 复权互逆 → `close`/`close_front`
两列语义互换。

**实测结果（V2，因子比值判别法）**：
- 30 只除权前股票（06-16 除权，06-15 分钟 bar）：`close_front/close` 与 `adj_prev/adj_latest`
  **精确匹配到 6 位小数**（如 603566 ratio=0.962097==expect=0.962097；001395 送转 0.749242 精确吻合）；
- 6 只除权前 ETF：3 只 normal（<0.3%），3 只偏差 ~0.5-1.2%（见 §3.5，归因锚点漂移）；
- **无一 SWAPPED** → 分钟入库路径实际为 **raw 直通 + aligner 因子复权**，无互换污染。

**结论**：P1-③ 的"全部行走还原"对分钟表未造成互换（流式路径与普通路径的还原行为或云端
分钟实际落 raw 的路径组合下，实测 front = raw × adj_i/adj_latest 精确成立）。原 §4.2
"分钟表从线1 还原白名单分离"的修复**不再需要**。

### 3.2 🟡 无 is_qfq 列时默认整批 qfq（日线经验推广到分钟）

`mcp_adapter.py:1710-1713`：云端返回无 `is_qfq` 列时，按"日线实测 100% True"默认整批为前复权。
若云端分钟表缺 `is_qfq` 列（契约列清单含该列，但需确认实际落表），真 raw 会被当 qfq 还原，仅 warning 无阻断。

### 3.3 🟡 分钟 front 锚点漂移无直接巡检

xtquant 时代 front 以最新交易日为锚（已废弃）；MCP 路径 front 由本地 `_apply_qfq` 用
`adj_factor` 计算（锚 = 因子全局最新）。增量拉取遇除权事件后，历史 front 与新增 bar front
基准的一致性依赖因子序列正确性；`AdjustmentReturnConsistency` 巡检仅 daily（分钟无 pctChg 可比），
`AdjustmentAnchor` 只查库内首尾 = 原价——**分钟 front 跨段锚点漂移无直接检测**。

### 3.4 🟡 front 缺失静默降级

拉取端 front 相关失败为 warning 级；`AdjustmentCompleteness`（front/back 4 列部分缺失 → error）
属事后巡检，非实时告警——不符合"不再静默积累"目标。

### 3.5 🟠 分钟 front 锚点漂移 —— **实测实锤（2026-08-16）**

**案例 520810（V2f/V2i）**：
- 因子时间线：06-10 除权 → 1.008；**07-01 白天因子回落 1.0076 → 1.0**（世代混存痕迹）；07-10 → 1.0108；**08-12 → 1.0158**（最新除权）；
- 06-08 的分钟 bar 于 **08-09 11:51 批量回填**（`update_time` 统一），front 锚定当时因子 **1.0108**；
- 08-12 除权后（锚 1.0158），历史 front 未重算 → 06-08 bar 的 `close_front/close` 与最新因子预期
  偏差 **~0.5%（= 1.0158/1.0108 − 1）**；
- 同理 563020/159307（08-09 回填批次）偏差 0.5-1.2%。

**根因**：QFQ 重锚引擎设计上完整覆盖分钟表（`qfq_reanchor_engine.py`：方法 B ratio R = target_scale/stored_scale、
fresh_staged 逐值 UPDATE 四 front 列、方法 A golden、postcheck minute_* 四项），fresh 源经
`qfq_fresh_capture.McpFreshFetcher`（MCP raw + adj_factor → front）替代 xtquant；但 **08-12 除权
事件未触发重锚**——`qfq_jump_audit` / `qfq_deep_audit_item` / `qfq_factor_revision_alert` 全部 0 行。

**影响**：除权后历史分钟 front 与新增 bar front 基准不一致 → 引擎 `fq='pre'` 分钟信号
（动量回归/止损）在除权日前后出现 ~0.5% 级假跳变；同一 code 序列内锚点不一致。

### 3.6 🟠 因子序列非单调（世代混存/修正痕迹）—— **实测实锤（2026-08-16）**

- `qfq_aux.db.fund_adj`（ETF）：**277 / 2227（12.4%）** 只 code 因子序列非单调（回落）；
- `qfq_aux.db.adj_factor`（股票）：**4826 / 5791（83.3%）** 只非单调；
- 典型：520810 07-01 白天因子 1.0076 → **1.0** 回落；`qfq_factor_observation` 有 979 万行观测
  但 **`qfq_factor_revision_alert` = 0 行**——非单调事实未被巡检告警捕获。

**定性**：因子表存在世代混存/批量修正痕迹（xtquant-legacy 因子与 MCP 因子同表、按分钟冗余写入）。
非单调本身不直接等于当前 front 错误（入库时用"当时最新因子"），但它是锚点漂移的载体，
且因子表作为 aligner 复权输入，其洁净度直接影响 future 回填/重锚的正确性。

---

## 4. 修复方案（待用户批准后立项，框架层改动须走 AGENTS.md 铁律确认流程）

### 4.1 治本（云端侧）

- 云端分钟保持 **raw + adj_factor + 显式 is_qfq=False**（已符合 `mcp_dataset_requirements.json`
  契约 `adj_policy=raw + adj_factor (客户端复权)`，无需改动云端写入）；
- 云端**统一 raw** 目标适用于日线（当前 is_qfq=True 100%）与分钟（当前 raw）——日线侧如需统一 raw
  属云端写入改造，另行评估。

### 4.2 治本（本地侧）

**核心修复（实测后调整）**：分钟 front 锚点漂移的闭环——**除权事件 → 分钟重锚**：

1. **除权事件触发分钟重锚**：`qfq_event_discovery` 发现除权后，对受影响 code 执行分钟重锚
   （`qfq_reanchor_engine` 方法 B / fresh_staged，fresh 源 = `McpFreshFetcher` MCP raw + adj_factor）；
   当前 08-12 除权未触发（qfq_jump_audit 空），需排查事件发现/编排链路为何未执行；
2. **因子表世代清理**：`adj_factor`/`fund_adj` 存在世代混存（xtquant-legacy + MCP）与按分钟
   冗余写入——明确因子表的唯一世代语义与写入节奏（日级追加、修正覆盖），作为重锚输入基准；
3. **巡检新增**：
   - 因子非单调告警（连接 `qfq_factor_revision_alert`，当前 0 行未捕获 83% 股票非单调）；
   - 分钟 front 锚点漂移检测：除权日（`stock_dividend`/`etf_dividend`）后校验受影响 code 的
     `close_front/close` 与 `adj_i/adj_latest` 一致性（复用 V2 判别公式）；
   - 回填批次 front 基准检查：`update_time` 批次内 front 是否使用同一因子快照。

### 4.3 巡检新增（对齐"早发现/防缺失"，与 4.2 合并落地）

1. 分钟 `is_qfq` 占比告警（云端契约 vs 实际列值）；
2. 还原一致性抽查（V2 判别公式，已实测可自动化）；
3. 分钟 front 锚点漂移检测（§4.2-3）；
4. front 缺失实时告警（拉取失败 → WARN/FAIL，可配置）。

---

## 5. 实测结果（2026-08-16，回填完成后执行）

| # | 验证 | 结果 |
|---|---|---|
| V1 | 本地分钟表 `is_qfq`/`adj_factor` 列 | ✅ 两列均不存在（daemon 提取后移除，符合契约）；`data_source` 100% = `mcp`（xtquant 数据已彻底退出） |
| V2 | close vs close_front 互换检测（因子比值判别） | ✅ **核心风险证伪**：30 只股票 + 6 只 ETF 除权前 bar 比值与 `adj_prev/adj_latest` 精确匹配，无一 SWAPPED |
| V3 | 本地分钟表列结构 vs 契约 | ✅ 全 mcp 源、行数 4420 万（股票，2026-06-15 起）/ 8750 万（ETF，2025-01 起） |
| V4a | 因子序列单调性 | ⚠️ **非单调：股票 4826/5791（83%）、ETF 277/2227（12%）**；`qfq_factor_revision_alert` 0 行（未捕获） |
| V4b | 除权后分钟 front 重锚 | ⚠️ **漂移实锤**：520810 08-12 除权（因子 1.0108→1.0158）后，08-09 回填历史 front 未重算（偏差 ~0.5%）；`qfq_jump_audit`/`qfq_deep_audit_item` 空 |
| V4c | 日线 raw/front 正确性 | ✅ 与分钟同源同因子，30 只股票因子比值精确匹配（日线基准成立） |

---

## 6. 结论

1. 云端分钟契约 = raw + adj_factor（客户端复权）——与用户陈述一致；
2. 本地 MCP 分钟链路实测正确：**raw 直通 + aligner 因子复权**（`front = raw × adj_i/adj_latest`
   精确成立），**线1 互换污染风险证伪**；
3. **实测实锤风险**：① 除权后分钟 front 未重锚 → 锚点漂移（520810 案例 ~0.5%）；② 因子序列
   非单调（股票 83%/ETF 12%）且 `qfq_factor_revision_alert` 未捕获；③ front 缺失/因子异常
   多为 warning 级静默降级；
4. 修复方向（§4）：除权事件触发分钟重锚闭环 + 因子表世代清理 + 巡检新增（因子非单调告警 /
   锚点漂移检测 / 回填批次 front 基准检查）；
5. 修复属框架层（qfq 编排/巡检），须按 AGENTS.md 铁律：先本地修复 → 用户确认 → 双仓库同步推送；
6. 本审计文档 v1.1 已存档（`docs/mcp-minute-caliber-audit-20260816.md`），未提交 git。

---

## 附录 A：审计证据索引（2026-08-16）

- 云端契约：`config/mcp_dataset_requirements.json`（coverage_matrix Q2_astock_minutes / Q4_etf_minutes，`adj_policy=raw + adj_factor (客户端复权)`）
- 线1 还原：`quantstudio/pipeline/sources/mcp_adapter.py:1624-1761`（P1-③ 2026-08-03 实测定论，commit f7df29c；300182.SZ 实测 108x 尺度断层案例）
- 双重复权实测：`mcp_adapter.py:255-267`（300750 4-22：正确 226.312 vs 错误 222.017；300750 2024-06-03 全局锚 202.5001 vs 分片锚 193.8267）
- daemon 流式：`quantstudio/pipeline/daemon.py:905-949`
- aligner 复权分治：`quantstudio/pipeline/aligner.py:406-435`（native passthrough 仅 xtquant/baostock；MCP 走 `_apply_qfq`）
- 巡检现状：`quantstudio/pipeline/quality_audit.py:300-394`（AdjustmentCompleteness / AdjustmentFactorConsistency / AdjustmentAnchor / AdjustmentReturnConsistency[daily only]）
- 新鲜度机制：`quantstudio/pipeline/update_detector.py`（A4 last_sync 按表粒度）+ `daemon.py` 水位推进（四价格表走 QFQ 协调 gate）
- 已废弃参考：`quantstudio/pipeline/sources/xtquant_adapter.py:218-254`（xtquant 三段式，不再使用）
