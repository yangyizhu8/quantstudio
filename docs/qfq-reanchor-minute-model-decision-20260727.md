# QFQ 分钟修正模型设计决策：乘法方法 B vs xtquant 减法复权（2026-07-27）

状态：**方案 A（严格 BLOCK）已实现并为当前默认行为；方案 B 为设计提案，
未经用户明确批准前禁止实现。**

关联：第四轮对抗审核意见（2026-07-27 深夜）第 1/2/5 项。

---

## 1. 问题陈述

QFQ 重锚第二批分钟修正引擎（`quantstudio/pipeline/qfq_reanchor_engine.py`）
的方法 B 假设：

> 陈旧分钟 front 与正确 front 之间存在**单一稳定乘法比率**
> R = target_scale / stored_minute_scale，按精确变点切段后逐段
> `front = raw × R` 写回。

独立采集的 fresh xtquant 数据（`tests/fixtures/qfq_real_reanchor/fresh_xtquant/`，
`xtdata.get_market_data_ex`，dividend_type=none/front，OHLC 四列，零平移
end-labeled，sha256 见 metadata_fresh_xtquant.json）证明 **xtquant 是减法复权
模型**：

```
600875: front = raw − 0.53   （2026-07-24 除息，每股现金分红 0.53 元）
600039: front = raw − 0.46   （2026-07-24 除息，每股现金分红 0.46 元）
002864: 现金分红链，同为减法模型
```

减法模型下 `front/raw = 1 − div/raw` **随价格逐日（乃至逐 bar）漂移**，
区间内不存在严格稳定的单一乘法比率。两种模型在数学上不等价：

```
乘法:  front = raw × R          （R 为常数 → front/raw 恒定）
减法:  front = raw − div        （front/raw = 1 − div/raw 随 raw 变化）
```

## 2. 证据（均可复现，脚本 `scripts/dump_real_method_ab_samples.py`）

### 2.1 默认容差下三证券全部 BLOCK（正确行为）

| 证券 | 默认容差结果 | block_reason | 逐日 R 簇结构（ratio_rel_tol=5e-4） |
|---|---|---|---|
| 600875 | blocked | fresh_daily_scale_inconsistent（low 列 max_dev=1.17e-03） | 6 簇（修正簇 5 + 除权后 noop） |
| 600039 | blocked | fresh_daily_scale_inconsistent（open 列 max_dev=2.26e-03） | 7 簇（修正簇 6 + noop） |
| 002864 | blocked | ratio_multi_cluster | 4 簇（修正簇 3 + noop） |

BLOCK 后 `stock_daily` / `stock_minutes` 快照逐值一致（未写回）。

### 2.2 单比率写回 vs fresh xtquant 的可观察价格偏差

以除权前逐日 R_B 中位数为 ratio_plan 模拟（600875/600039 离线模拟；
002864 为诊断容差 committed 的实际写回）：

| 证券 | ratio_plan | max close_front 误差 | 误差>0.01 元 bar 数 |
|---|---|---|---|
| 600875 | 0.9797400493 | 0.032619 元 | 960 / 1928 |
| 600039 | 0.9468272369 | 0.046205 元 | 1491 / 1928 |
| 002864 | 0.7627533166 | 0.020477 元 | 349 / 1446 |

这些是**模型不等价的系统性偏差**（3~4 个 tick），不是浮点误差，不得以
"源精度容差"名义吸收。

### 2.3 第三轮放宽容差的错误

第三轮曾将 ratio/golden/cross 容差放宽至最高 1%，把多个修正簇合并成一个
median ratio 后继续写价。这违反引擎自身的验收原则：单一稳定比率簇、
多簇只能 bootstrap 且逐变点可解释、A/B 超容差整券 BLOCK、不能取平均后
继续。**该做法已在第四轮撤销**（REAL_TOL 已删除，真实测试回归默认容差）。

## 3. 方案 A：严格保持方法 B（已实现，当前默认）

**行为**：真实数据出现多簇 / fresh daily 列间 scale 不一致 → 引擎默认容差
BLOCK，三证券**不得自动写回**，转人工处置队列。

**实现**（本轮已落地）：
- 测试 `tests/test_qfq_reanchor_batch2.py` 真实三证券用例改为默认
  `ReanchorTolerances()` 断言 `rolled_back/blocked` + 数据未变
  （快照逐值一致）+ block_reason 精确匹配；
- 删除 REAL_TOL 放宽容差；禁止任何"容差校准"吸收模型差异；
- fresh xtquant OHLC 四列 fixture 作为独立 oracle 固化（含 07-13
  predecessor），`test_fresh_xtquant_fixture_integrity` 每次校验 sha256。

**优点**：
- 无错误写价风险；引擎语义（单一乘法比率簇）保持自洽；
- 未触碰框架行为，无需审批。

**代价 / 限制**：
- 凡 xtquant 减法复权链上有现金分红的证券，方法 B 均无法自动修正分钟
  front（预计覆盖大量分红股）——第二批自动化率显著下降；
- 陈旧分钟 front 继续留存，直至采用获批的精确模型修正。

## 4. 方案 B：修改分钟修正模型（提案，**须用户批准后方可实现**）

### B-1 fresh xtquant 分钟逐值写入（推荐候选）

直接采集 fresh xtquant 1min 前复权 OHLC，四个 front 列逐 bar 逐值写入，
不经任何比率/差值模型换算。

- 精度：与源零偏差（oracle 即写入值）。
- 前置：全量分钟重采能力（xtdata 下载配额、时段、增量策略）；
  staged→postcheck→commit 事务扩展为"分钟逐值 staged 表"；
  方法 A/B 校验退化为 fresh 自身一致性 + 与 daily 跨表校验。
- 风险：采集窗口内 xtquant 缓存/服务端数据变动导致不可重现；
  大批量证券的采集时长与失败重试；本地 stored 分钟 raw 与 xt raw
  存在个别 bar 差异时的仲裁规则。

### B-2 additive delta 模型

按除权事件链推导每段每股分红 div_seg，`front = raw − div_seg` 写回
（对 OHLC 四列同一 div_seg）。

- 精度：与 xtquant 减法模型同构，理论上逐值一致（tick 舍入内）。
- 前置：div_seg 必须来自权威除权除息事件源（非从价格反推）；
  复杂事件（送转+派现叠加、多次除权累计）需要事件叠加代数；
  与既有乘法段结构（RatioSegment）并存 → PlanSegment 需扩展
  `model ∈ {ratio, delta}`。
- 风险：事件源错误直接写错价；送转类事件仍是乘法性质，混合事件
  需要"先乘后减"复合模型，复杂度高。

### B-3 混合守门（保守变体）

方法 B 保持乘法，但 commit 前新增强制 postcheck：
`post-write vs fresh xtquant 逐 bar 误差 ≤ 1 tick`，超限整券回滚。
（B-1/B-2 的精度门槛作为独立护栏，模型本身不变。）

- 实质效果：减法复权证券仍会被挡下（同方案 A），但把"挡下"的判据
  从簇结构前移到最终价格误差，语义更直接。

### 影响 / 回退（B 系共通）

- 影响面：qfq_reanchor_engine 计划/事务/审计三层；batch1 日线路径不受
  影响；下游回测读取 front 列无 schema 变化。
- 回退：模型选择由参数控制（默认 ratio=方案 A 行为）；任何 B 变体上线
  前须以三证券真实 fixture 全量逐 bar 零偏差（≤1 tick）为验收门槛；
  回退 = 参数切回 + 逆事务恢复 front 列（事务日志已含 pre-image）。

## 5. 决策请求（已批示）

- [ ] **维持方案 A**：真实多簇即 BLOCK，减法复权证券转人工/后续批次；
- [x] **批准 B-1**（fresh xtquant 分钟逐值写入）——**用户批准（2026-07-27，
  会话基准日期），并已实现**：引擎 `model="fresh_staged"` 路径 + staged
  事务 + precheck（raw 逐 bar 一致 / 完整覆盖 / session/cadence / 交易日
  历校验）+ 10 项 postcheck + 事件审计（committed/blocked/rolled_back/
  failed 四态均记录 model/model_reason/fresh_source/fresh_capture_id/
  metadata_sha256/tick_size/freqs）。三证券真实 fixture（600875/600039/
  002864）fresh_staged 全部 committed，minute front vs fresh 全量逐 bar
  ≤1 tick（bars_over_1_tick==0），002864 daily 逐值不变。API 契约见
  `docs/strategy_toolbox.md` 与 README「QFQ 重锚引擎」章节。
- [ ] **批准 B-2**（additive delta）——未采用；
- [ ] **批准 B-3**（乘法 + 1 tick 终检护栏）——未采用。

批准边界（B-1 强制约束，引擎已逐条落实）：保留 ratio 方法 B；模型显式
`model={ratio,fresh_staged}` 禁止静默切换；`fresh_staged` 必须提供非空
`model_reason`（写入事件审计）；staged 分钟主键 (code,freq,time)，只
UPDATE 四个 front 列；tick_size 按资产路由（STOCK=0.01 / ETF=0.001，
`resolve_tick_size`）；分钟逐自然日经 CalendarService 验证真实交易日。

---
*本文档为第四轮对抗审核交付物之一；第六轮更新决策状态为已批准/已实现。
GitHub 同步须待用户明确批准后进行。*
