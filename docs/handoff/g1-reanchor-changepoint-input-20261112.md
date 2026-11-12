<!-- 交付件来源锚（2026-11-12 整改：R17）
     原件：D:\miniQMT策略实盘\trading-battle-back\docs\handoff\g1-reanchor-changepoint-input-20261112.md
     原件 sha256(16)：70881ba56ee1a2dc
     本件：主仓镜像（消费方节点1 在主仓，故以主仓副本为可访问交付面；内容与原件一致，仅加本锚头）
     归属：节点21（复权管线修复专属会话）→ 节点1（G1 实施）
-->
# G1 输入包：`explain_changepoints` 增「回填批边界」解释源（2026-11-12 · 节点21 → 节点1）

> **本件＝设计输入，不是实施。** R13 审核已裁：**G1 适配实施派节点1**（低序入队，L2 存量回放步④解除项），
> 本件为节点1 的**实施输入**（问题定义＋落点行号＋设计约束＋验收判据＋两向用例＋边界）。
> 节点21 对主仓**零触碰**（边界红线「⑱/duckdb 只读只报」＋主仓共享核心文件须走主仓六步流水线）。
> 权威设计件：`docs/design-l2-minute-reanchor-20261111-v2.md` §十.2（G1 段）与 §十.3（衔接映射）。

## 一、问题（一句话）

复权回放的**分段变点**里，除「除权事件日」外还有一类合法变点——**回填批边界**：
同一因子区间内，后期回填把部分旧日按当时锚重写 ⇒ 该区间内出现 ratio 变点。
现引擎 `explain_changepoints` 要求**每个变点边界日都能被 `ex_dates_ms`（除权事件日）解释**，
否则抛 `ReanchorBlocked("changepoint_unexplained")` **整券 BLOCK** ⇒ **回填批边界码在现引擎下无法回放**。

## 二、落点（主仓 `QuantStudio`，节点1 实施域）

| 文件 | 行 | 现状 |
|---|---|---|
| `quantstudio/pipeline/qfq_reanchor_engine.py` | `explain_changepoints`（def 约 L789–826） | 变点解释源**仅** `ex_set`（除权事件日）；缺解释 ⇒ `unexplained` 非空 ⇒ 抛 `changepoint_unexplained` 整券 BLOCK |
| 同上 | `RatioSegment` / 分段计划生成处 | 段键 `(code, freq, t_start, t_end, ratio)`，左闭右开；边界取 R 序列**精确变点**（非事件日 ±1） |
| 同上 | `_factor_equivalent_analysis` / `QS_QFQ_FACTOR_EQUIVALENCE` | 已有「上游因子微调 → 结构化比例差 ≠ 损坏」的第二级放行与**逐位回退 env** ⇒ **本项适配应复用该回退模式**（新增 env 或沿用同一开关，须在实施时明确） |

## 三、设计约束（**不削弱门禁**）

1. **解释源增一类，不放宽**：变点解释源枚举增 `backfill_batch_boundary`；**解释缺失仍 BLOCK**。
   ❗禁止改成「无解释也放行」或「有任一解释即整券放行」。
2. **两种证据分级**：
   - **authoritative（首选）**：⑱ 提供的**写时锚 sidecar**（可直接读出边界两侧 `a_write`）⇒ 判定为回填批边界；
   - **degraded（过渡）**：sidecar 未到位时，允许用**可复核的降级证据**：
     `边界两侧 ratio 不同` ∧ `边界日非除权日` ∧ `边界前后 adj_factor 区间一致` ∧ `边界日存在分钟写入痕迹`。
     ⇒ 必须标 `explanation_quality='degraded'`，并**进 `qfq_reanchor_event` 审计**（谁/何时/以何证据解释该变点）。
3. **放行率留痕**：`degraded` 比例进批次放行率面板；**放行率异常高（>90%）须回查**「比值是否掩盖真实漂移」（铁律 25 §5）。
4. **优先 sidecar**：首期以 sidecar 解释；降级路径仅作过渡且必须显式标记。

## 四、验收判据（可机检）

| # | 判据 |
|---|---|
| 1 | **两向用例①**：构造「回填批边界」变点（两侧 ratio 不同、边界日非除权日）⇒ 该券**不被** `changepoint_unexplained` BLOCK |
| 2 | **两向用例②**：构造「无任何解释证据」的变点 ⇒ **仍整券 BLOCK**（`changepoint_unexplained` 不变） |
| 3 | 既有 `tests/test_qfq_reanchor_batch2.py` **全绿不回归**；新增用例一并登记 |
| 4 | `degraded` 证据在 `qfq_reanchor_event` 留痕可查（解释人/时间/证据类型） |
| 5 | 门禁**语义零放宽**：非「回填批边界」类的未解释变点，BLOCK 行为与改动前逐位一致 |
| 6 | 既有回退 env（`QS_QFQ_FACTOR_EQUIVALENCE=0`）下行为与改动前逐位一致 |

## 五、边界（节点1 实施时须守）

- **不改** QuestDB 侧任何逻辑（QuestDB 只读观测面在节点21 侧）；
- **不改** `apply_minute_segments` 的 front-only UPDATE 语义（只写 `*_front`，raw 不动）；
- **不改** 段键与左闭右开语义；边界仍取精确变点；
- 与节点21 的**段级口径定谳**保持一致（**段级只用 ratio**；`ff` 仅作来源证据 own_day / intermediate）——见 §六。

## 六、节点21 侧已备好的输入（节点1 直接取用）

| 资产 | 路径 | 说明 |
|---|---|---|
| 段级计划器（可复现） | `scripts/qfq_reanchor_minutes.py --plan` | 逐码逐因子区间产 B 类可回放清单＋回填行例外＋铁律25 自检；**只读** |
| 加密抽样实测（2026-11-12 03:0x） | `docs/evidence/l2-step6-dense-sampling-20261112.md` | 40 码无偏、days-per-interval=12：可回放段 **64**、涉及码 18（45.0%）、可回放行 **1,219,701**（67,761 行/码）、显式排除回填行 **840 天 / 202,620 行**、自检 FAIL 0；**边界来源 31/64 靠实测 ratio 变点**（`measured_ratio`） |
| 判据方向定谳 | 主仓 `knowledge/db/DB-2026-0043.md` | **比值 ratio 与修正因子 ff 互为倒数**；判据须 `|ratio×ff−1|≤tol`（单点）/`|ratio−1|>tol`（段级）；写成 `|ratio−ff|≤tol` 会把真旧锚行整批误判为 C（曾致规模低估约 2,000 倍） |
| 口径定谳与衔接映射 | `docs/design-l2-minute-reanchor-20261111-v2.md` §二/§十.3 | 段级口径 + 普查侧→delivery 侧映射（**R 不可直接取 ratio**；时间口径须用仓内 `day_start_ms` 同源日界） |

## 七、解除关系

G1 落地 ⇒ **L2 存量回放步④解除**（回填批边界码不再被整券 BLOCK）。
其余三步的解除条件不受本件影响：步①＝**节点1（运维2 P2/A4 联合项）**修 delivery cycle（根因 trigger 积压 A4 侧）、
步③＝⑱ 提供写时锚 sidecar（sidecar 未到位时 G1 走 degraded 路径）、步⑤＝delivery 侧复算覆盖对账。
