# nodeid 级精确映射（第一批＋第二批，已取证 8 文件）

> 判据来源：当前 `docs/evidence/pd0020-full-regression-20261113/junit.xml`（135 失败/错误）
> × 基线 `docs/evidence/pd0020-wt-baseline-20261113/baseline-junit/*.xml`（8 文件，本次补取证）
> 规范化为 `文件名::用例名`（去 `tests.` / `.py` 差异）。

## 一、映射结果（nodeid 级，非文件级数量）

| 类别 | 数量 | 明细 |
|---|---|---|
| **A. 基线同样失败** | **30** | 见 §二 |
| **B. 基线通过、当前失败** | **2** | `test_08_remove_banding`、`test_08b_rewrite_literal_inline` |
| **C. 基线 skipped（DB 不存在）** | **12** | 见 §三 |
| **D. 基线未取证（该文件未跑）** | **91** | 其余 40+ 文件 |
| **合计** | **135** ✓ | A+B+C+D |

> ⚠️ **算术闭合只证明台账总数，不证明分类**——本表的分类依据是**逐 nodeid 状态**，不是文件级失败数。

## 二、A 类：基线同样失败 30 项（nodeid 级确认）

| 文件 | 项数 |
|---|---|
| `test_source_import` | 7 |
| `test_fund_contract_wiring_matrix` | 7 |
| `test_phase4_minute_batch_provider` | 6 |
| `test_phase4_minute_memory_slice` | 6 |
| `test_strategy_fidelity_gates` | 4 |

## 三、C 类：基线 skipped 12 项 —— **原因是 DB 不存在（环境差异，非代码回归）**

| 文件 | 项数 | skip 原因（原文） |
|---|---|---|
| `test_batch_apis` | 4 | `测试库不存在` / `测试库不存在: D:\...\QuantStudio-wt-baseline\data\quantstudio.db` |
| `test_d3_firstday_noprice` | 4 | `DB 不存在` |
| `test_pd14b_time_normalize` | 4 | `DB 不存在` |

### 关键纠正（我方此前误判）

| 我此前的判定 | 实际 |
|---|---|
| `test_batch_apis` 4 项 = 「候选新增」（因基线文件无 failed） | **错误**——基线是 **skipped（测试库不存在）**，非 passed |
| `test_d3_firstday_noprice` / `test_pd14b_time_normalize` = 「基线缺 nodeid」 | **不准确**——nodeid **存在**于基线 junit，状态是 **skipped 且原因明确** |

⇒ 正确分类：这三组共 **12 项属「环境差异类」**——worktree 内无 `data/quantstudio.db`
⇒ 测试自动 skip；主仓有 DB ⇒ 同样用例实际执行并 failed。
**这既不是「基线既有」，也不是「本批新增」**，单列，**不计 PASS、不判新增**。

## 四、D 类：91 项未取证

其余文件（`test_smallcap_overnight_scalp_agent_v2` 10、
`test_first_cover_event_daily_agent` 9、`test_smallcap_overnight_scalp_agent` 6、
`test_fund_statement_acceptance` 5、`test_fin_growth_dividend_staging_tool` 10、
`test_audit_qfq_staleness`、`test_gui_*`、`test_minute_*` 等）**在基线尚未跑**
⇒ 状态**未知**，不得推测归属。

## 五、表述纠正（按用户裁定）

1. **第一批基线 `50 passed` 中实际包含 `test_08` / `test_08b` 这两个当前失败项**
   ⇒ 正确表述：**「其余 48 项与 135 清单无关」**，不能说「50 项全无交集」。
2. **基线 PASS ≠ 当前已解决**：基线的 passed 只是该版本当时的读数，
   与我们当前修复进度无关。
3. **30 项「基线同样失败」≠ 根因相同**，也**不免除**当前修复义务；
   基线同失败只对 `7826aed` 该版本、该配置、该输入成立。

## 六、A-1 契约同步的验证要求（按用户裁定）

`test_08/08b` 的断言修订**不能仅把 `not in` 翻成 `in`** 就当作保真通过：

- 必须覆盖 **调用上下文**：`is_trade()` 在产物中的实际调用位置与语义
  （`initialize` 内 `if not is_trade(): set_backtest()` 的执行路径）；
- 必须覆盖 **产物语义**：保留原调用后，回测环境 vs 交易环境的分支行为是否与
  PTrade 文档一致；
- 断言修订须随 **A-1 批次六步②审**提交，列明上述两项证据。

## 七、本轮取证成本说明

原第一批/第二批基线跑**仅取 tail、未存 junit** ⇒ nodeid 级证据缺失，
分类无法成立。本轮**重跑 8 个已做文件并保存 junit**（`baseline-junit/`），
目的是**取得分类必需的 nodeid 级证据**，不是「为补原件而重复跑」。
后续已取证组**不再重复跑**。
