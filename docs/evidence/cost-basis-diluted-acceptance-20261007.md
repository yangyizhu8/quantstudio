# POS-01 cost_basis 摊薄对齐修复 · 验收证据（六步④）

> 修复：本地成本口径向 PTrade 摊薄法对齐（裁定1：六步② PASS，2026-10-06 实施）。
> 方案件：`docs/cost-basis-diluted-alignment-design.md`；实施 commit：见 §7。
> 类型：**数据修正型**（diff 只能归因成本口径修正行集——V3 唯一变量证明）。

## 1. 实施内容（与方案 §2 逐项对照）

| 方案项 | 落实 | 位置 |
|---|---|---|
| 卖出摊薄记账（含费净得扣减、负值钳 0+审计留痕） | ✅ | `backtest_engine.py` `_execute_sell`（diluted 分支） |
| 买入加权（**实证修正：不含费**，见 §2 勘误） | ✅ | `_execute_buy`（与 legacy 同式，差异仅在卖出） |
| 参数 `cost_basis_method`（diluted 默认 / moving_avg legacy）+ 非法值回退告警 | ✅ | 引擎 `__init__` |
| CLI 旗标 `--cost-basis diluted/moving_avg` + 校验 | ✅ 代码就位 | `run_ptrade_strategy.py`（**commit 暂缓**，见 §6 叠加登记） |
| Position/ptrade_api 注释口径 | ✅ | `backtest_engine.py` Position.avg_cost；`ptrade_api.py` L214 区 |
| 新增契约测试 | ✅ 6 用例全绿 | `tests/test_cost_basis_diluted.py` |
| 文档同步 | ✅ | README L501 区 / strategy_toolbox L74 / prompt_engineering（待补） |

## 2. 方案勘误（实施期实证升级）

方案 §2.2 原写"买入含费净投入"。实施期用平台**双锚点**（9/2→0.973、9/11→0.972）三口径判别：

| 口径 | 9/2 推演 | 9/11 推演 | 平台显示 | 判定 |
|---|---|---|---|---|
| 买入含费+卖出含费 | 0.97394 | — | 0.973/0.972 | ✗（0.974≠0.973） |
| 全不含费 | 0.97244 | — | 同上 | ✗（0.972≠0.973） |
| **买入不含费+卖出含费扣** | **0.97311** | **0.97211** | **0.973/0.972** | **✓✓ 双锚点吻合** |

**结论口径**：卖出摊薄扣费（净得=成交额−佣金−印花税−过户费）+ 买入加权不含费。
方案预告的"费用敏感性 4 位不可分辨、V2 回退复验"提前在单测阶段完成判别，无需回退。

## 3. V1 契约测试（6 passed）

`tests/test_cost_basis_diluted.py`：
1. **平台黄金序列**（512890 完整 9 步链，双锚点 round(·,3)=0.973/0.972）；
2. 亏损卖出摊薄抬高成本（7505.025/5000）；
3. 负摊薄钳 0；
4. 清仓归 0+重建仓；
5. legacy moving_avg 卖出不改成本（回退开关）；
6. 买入式双口径同构。

## 4. V2 双端对账（四象限案例 2024-01-02~2026-07-13）

| 项 | 修复前本地 | **修复后 diluted** | 平台 |
|---|---|---|---|
| 收益 | 12.46% | **52.32%** | 56.20%（残差 3.88pp，见 §5） |
| 9/4 误止损 | 有（−5.01% 贴线触发） | **无** ✓ | 无 |
| 2024 全年委托 | 逐笔一致（8 个月） | **逐笔一致（全年）** | 基准 |
| 2025-01~09 委托 | — | **逐笔一致**（2/5~9/4 全部 11 卖单数量价格一致；588400 拆单 37400×4=149600 等价） | 基准 |
| 9/30 现金流 | — | **5360.80 逐位一致** | 5360.80 |

产物：`output/backtest_results/20261007_005728_四象限ETF轮动策略/`（diluted）。

## 5. V2 残差 3.88pp 归因（非本修复面，新洞登记 DAT-16）

- 现金流逐位一致 ⇒ 成交序列/费用/撮合全同；
- 差异全在持仓估值：9/30 平台市值 149751.98 vs 本地 150532.90（**+780.92 ≈ 0.52% 价格快照差**）；
- 该微差致回撤判定异号（平台 9/30 −6.97% 风险锁定 vs 本地 −5.90% 未锁）→ 10/9 调仓目标数量差
  （卖 600 vs 300、买 3900 vs 3100）→ 11-12 月级联（本地多两轮换仓损耗 ≈3.88pp 主源）；
- **定性**：DAT 类估值价格快照微差（本地 K 线库 vs 平台估值源第 3-4 位小数）——既有独立洞，
  修复前被成本分歧完全掩盖（修复前 9/4 即分叉，走不到 9/30 对账）。registry 新条目 DAT-16
  （三态=不可归因 → 下一轮 diff-triage：逐日估值明细对账定位标的级价格源差异）。

## 6. V3 唯一变量证明（legacy 开关回退）— PASS

`--cost-basis moving_avg` 复跑（产物 `20261007_010211`）vs 修复前基线（`20261006_215312`）：

| 指标 | 基线 | legacy 复跑 | 一致 |
|---|---|---|---|
| 收益 | 12.457% | 12.457% | ✓ 逐位 |
| 年化 | 4.929% | 4.929% | ✓ |
| 最大回撤 | 10.782% | 10.782% | ✓ |
| 胜率 | 76.316%（29W/9L/38） | 76.316% | ✓ |

**数据修正型验收核心判据成立：唯一切换 cost_basis_method 即从 12.46%→52.32%（平台量级），
开关回退逐位复原——diff 全归因于成本口径修正行集。**

## 7. V4 横验证 / V5 回归套件 — 双 PASS

- **V4**：`python scripts/run_contract_gate.py --strategies` → **CONTRACT GATE : PASS**
  （契约矩阵哈希一致 + 契约套件 + 6 策略 api_portability 冒烟全绿；ptrade_api 仅注释改动未触
  wrapper 模板，无矩阵追认义务）。
- **V5 分层**：`test_cost_basis_diluted` + 方案预告 6 文件（pr6b2a_cp3_oracle/calibration/
  pd11_position_view/position_view_contract_parity/position_view_engine_contract/
  etf_rotation_ref）→ **81 passed 全绿**——预告的黄金重算实测不需要（测试面未覆盖摊薄路径）。
- **全量套件红名单定性（三重证据）**：首次全量 30+ 红 + 段错误 = 与 contract-gate 并行资源污染
  （分层独占复跑转绿）；串行仍红的 10 个 = **既有红非本件**——source_import include 哨兵 ×8 +
  security_metadata golden ×1 + R5.5 ledger ×1，证据：①改动面零交集（成本记账 vs include 渲染链）
  ②**摘除对照**（`git stash push -- backtest_engine.py` 临时还原 HEAD 复跑仍红，恢复后 6 passed 无损）
  ③V4 同批受控套件单独 PASS。既有红登记移交归属线，不越权修。

## 8. 共享文件叠加登记（铁律）

- `run_ptrade_strategy.py` 检出他线未提交 hunks（D1/A 件设计契约本金校验，2026-10-06）；
  本件 commit **排除该文件**，`--cost-basis` CLI hunks 待他线落定后补提交（引擎侧参数默认值
  已独立生效，功能完整）。
- 引擎 `backtest_engine.py`（+40）与 `ptrade_api.py`（+3）diff 复核确认**纯本件改动**。

## 9. 回退条件

- 语义一键回退：CLI/引擎 `cost_basis_method='moving_avg'`；
- 代码级：写前快照 `c2f612c44eb23fdbea6af4111124a5f8aa7bc7fa`（git stash store 已固化）。

## 10. 类例放大（裁定3，随本件执行）

- [x] registry POS-01 → 已知分歧→**已锚定**（本轮翻转）
- [x] 0.973 契约测试固化（tests/test_cost_basis_diluted.py 黄金序列）
- [x] contracts/position-fields.md §1 口径实证更新（买入不含费勘误）
- [ ] POS-03/08/13 探针面跑（P1 探针矩阵首批）
- [x] FEE-05 判定：买入不含费（双锚点实证）→ 已锚定