# WP-E 审计与对账基建设计（2026-08-27）

- **流水线状态**：Step 1 方案（本文件）→ 待 ZCode 审计
- **差异化定位**：E1（QS_FILL_AUDIT 可诊断性）已在 `docs/backtest-align-diagnosability-design.md`（2026-08-17 审计通过 + 已落地：引擎 L599/754 + audit_etf_corporate_actions.py）→ **本 WP 聚焦 E1 完成度核验 + E2（统一公式对账）+ E3（归档规范）**，不重复已落地项

---

## 1. 现状盘点（避免重复建设）

| 项 | 状态 | 证据 |
|---|---|---|
| E1 拒单采集（QS_FILL_AUDIT 口径） | ✅ **已落地** | backtest_engine.py L432-433（采集）/L599-600（日末审计）/L737/L754-755（单出口+emit）；设计文档 backtest-align-diagnosability-design §2.1/2.2 |
| E1 巡检脚本 | ✅ 已落地 | scripts/audit_etf_corporate_actions.py（停牌/公司行为日巡检） |
| E1 单测 | ✅ 已落地 | tests/test_fill_audit.py（四拒单场景+跨日重置+截断） |
| **E2 统一公式对账** | ❌ **待建** | 无 `scripts/dual_end_reconcile.py`（master-plan E2/FE6） |
| **E3 归档规范** | ❌ **待建** | 无强制归档校验（master-plan E3） |

## 2. WP-E 范围（差异化）

### 2.1 E1 完成度核验（轻量，不自建）
- 核对 QS_FILL_AUDIT 是否全链路可用：本地重跑任策略 → 日志含 QS_FILL_AUDIT 行（date/sell_buy filled/rejected/positions_total/rejected_detail）；
- 确认 `below_rebalance_threshold` 不计入 rejected（集中采集排除规则在案）；
- **若核验发现缺口**（如某拒单路径未采集）→ 补丁立项（小修，随 WP-E 实施）。

### 2.2 E2：`scripts/dual_end_reconcile.py`（主交付，纯新增脚本）

**目标**：统一公式重算双端指标（消除平台 UI 指标的口径降级），输出差异归因分解表。

**输入**（双端各一份）：本地 `trades.csv`+`daily_stats.csv`（output/backtest_results/*/）+ 平台「交易详情.csv」+「持仓明细.csv」+「ptrade回测数据.txt」（对账目录）。

**统一公式（消除口径差异）**：
| 指标 | 本地口径 | 平台 UI 口径 | 统一重算 |
|---|---|---|---|
| 胜率 | trades 平仓笔数 | 「盈利次数/总次数」含未平仓 | **平仓口径：pnl>0 平仓 / 总平仓** |
| 盈亏比 | — | 「盈亏比=亏损次数/盈利次数」反了 | **avg_win / avg_loss** |
| 年化 | — | UI 显示 | (final/initial)^(252/days)-1 |
| 索提诺 | 引擎已有 | UI 不同 | 统一下行标准差公式 |

**输出（--out 目录）**：
1. `reconcile_report.md`：双端指标对照表（本地重算/平台重算/差值）+ **差异归因分解表**（Δ收益 → 每笔成交差异/每日持仓差异/费用差异逐项归属）；
2. `trades_aligned.csv`：双端交易日逐日对齐（当日买卖金额/持仓数/拒单审计行）——机械对照 PTrade 日志 `QS_FILL_AUDIT` /「生成订单」行。

**判据标记**：每项差 Δ 标记 `[已归因]`/`[需平台数据]`/`[未解释]`（未解释即驱动下一轮修复）。

### 2.3 E3：对账材料归档规范（文档 + 校验 CLI）

**规范**（`docs/dual-end-reconcile-material-spec.md` 或并入设计）：
- 每次双端对账，强制随附：本地 trades.csv + daily_stats.csv + 平台交易详情/持仓明细/ptrade回测数据 + 双端日志；
- 目录约定 `D:\...\双端回测数据汇总\<strategy>_ptrade\`（现状已在用，规范固化）；
- 归档校验：`scripts/dual_end_reconcile.py --check-archive <dir>` 验证必备文件齐全 + 时间戳同批（防跨版本混杂）。

## 3. 关键设计决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | E2 只新增脚本（不改框架代码/引擎）| 纯观测手段（master-plan「观测手段先行」）；零行为变更 |
| D2 | 统一公式以**本地 engine 度量**为锚（平台 UI 指标仅降级参考）| 平台指标口径不一致（盈亏比反了）——本地公式可信且可复算 |
| D3 | 差异归因表强制 `[未解释]` 标记驱动修复 | 对齐闭环：任何未解释残差 = 下一修复目标（与重验 PASS 判据一致） |
| D4 | `--check-archive` 校验文件齐全+时间戳同批 | 防跨版本/跨批次混杂（E3 的核心价值） |
| D5 | E1 完成度核验若发现缺口 → 小修随本 WP | 杜绝"已落地"认知盲区（以实测为准） |

## 4. 涉及文件

| 文件 | 改动 |
|---|---|
| `scripts/dual_end_reconcile.py`（新） | 统一公式重算 + 差异归因分解 + --check-archive |
| `docs/dual-end-reconcile-material-spec.md`（新） | E3 归档规范 |
| `tests/test_dual_end_reconcile.py`（新） | 公式/对齐/归档校验测试 |
| （候选）`docs/backtest-align-diagnosability-design.md` | E1 完成度核验记录 |

## 5. 测试矩阵

| 用例 | 场景 | 断言 |
|---|---|---|
| T1 胜率平仓口径 | 构造 trades（3平仓 1持） | 胜率=2/3 非 2/4 |
| T2 盈亏比 | avg_win/avg_loss | 正确符号与值 |
| T3 年化公式 | (final/init)^(252/days)-1 | 数值正确 |
| T4 索提诺 | 下行标准差公式 | 与引擎值一致 |
| T5 双端对齐表 | 两 trades 逐日 | 日期键对齐、交易计数一致 |
| T6 归因标记 | 构造 Δ | `[已归因]`/`[未解释]` 正确分配 |
| T7 check-archive | 缺文件/跨批次 | 校验失败并指出缺项 |
| T8 E1 完成度 | 本地重跑含 QS_FILL_AUDIT | 行存在、格式正确 |

## 6. 验收标准

1. T1~T8 全绿；
2. 用既有双端数据（tech_etf/CANSLIM 对账目录）实跑 reconcile，产出报告含差异归因分解表；
3. 指标重算与本地引擎指标一致（同源同公式）；
4. E1 完成度核验记录（QS_FILL_AUDIT 全链路可用或缺口清单）；
5. 全量套件除已知存量红外零新增。

## 7. 回退

- 纯新增脚本/测试/文档——删除即回退；
- E1 完成度核验发现缺口的小修：stash create -u 回退点 + 定向 restore。

## 8. 明确不做

- 不改引擎/框架代码（E2 纯脚本）；
- 不重写已落地的 E1（QS_FILL_AUDIT）；
- 不修平台 UI 公式（平台侧不可改，统一公式在本地重算）；
- 不处理平台数据源缺行（对账口径问题，标记 `[需平台数据]` 而非伪造）。