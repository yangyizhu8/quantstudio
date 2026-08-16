# G2 双档回归报告（ETF T+0 per-code，最终版）

| 项 | 内容 |
|---|---|
| 日期 | 2026-08-16 |
| 门禁 | G2（docs/etf-t0-per-code-design.md §9.2，ZCode 修订1/6 + G2 注记1/2） |
| 结论 | **(a) 零差异档 ✅ 全部通过；(b) per-code 语义档 ✅ 差异全部归因** |
| 回归脚本 | `scripts/etf_t0_regression.py`（capture/compare 双模式，增量落盘） |

---

## 1. (a) 零差异档：hash 逐位一致 + 日志零 diff

### 1.1 矩阵与结果

| 格 | 载体 | pre/post 对照基线 | config/daily_stats/trades/metrics/round_trips/benchmark | 日志（规范化后） |
|---|---|---|---|---|
| a_ETF动量_daily | `--etf-t0 false` + daily-bar-v1 | 修正对：HEAD−G1 vs HEAD | ✅ 6 文件 SHA-256 逐位一致 | ✅ 零 diff |
| a_tech_etf_mvo_daily | 同上 | 同上 | ✅ 逐位一致 | ✅ 零 diff |
| a_etf_theme_daily | 同上 | 同上 | ✅ 逐位一致 | ✅ 零 diff |
| a_ETF动量_minute | `--etf-t0 false` + minute-bar-v1 | seeded 全矩阵对（f69462e vs HEAD） | ✅ 6 文件逐位一致 | ✅ 零 diff |
| a_tech_etf_mvo_minute | 同上 | 同上 | ✅ 逐位一致 | ✅ 零 diff |
| a_etf_theme_minute | 同上 | 同上 | ✅ 逐位一致 | ✅ 零 diff |

### 1.2 方法学（ZCode G2 注记1/2 落实）

1. **干净基线（注记1）**：回归期间其他代理并发提交了引擎改动（ETF 除权补正 `005da48`、现金分红入账 `72182ee`/`68f2f60`），且将本方案 G1 独立提交为 `ea9cc8a` 并推送。为避免污染，**最终对照采用修正对**：
   - post 侧：`git worktree` @ HEAD（含 G1 + 分红修复）；
   - pre 侧：同一 HEAD worktree 上 `git revert ea9cc8a --no-commit`（仅移除 G1，保留分红修复）；
   - 两侧同一 DB 快照副本（`output/g2_etf_t0_regression/quantstudio_g2.db`，与 live 库行数逐表核对一致，源 mtime 静态）；
   - 两侧 `PYTHONHASHSEED=0`（存量策略存在依赖 dict/set 迭代顺序的决策逻辑，见 §4 T3）。
2. **日志零 diff（注记2）**：规范化 `YYYY-MM-DD HH:MM:SS[,mmm]`、`HH:MM:SS[,mmm]`、以及导出目录名内嵌运行时刻 `backtest_results\YYYYMMDD_HHMMSS_` 三类时间伪影。
3. **首轮污染对照（记录）**：未修正前（f69462e vs HEAD）daily 格出现真实交易差异（如 159995 卖 33500 vs 67000 股），经 hash 复现性与代码审查归因于**分红/除权引擎改动**（现金入账改变净值→不同下单序列）；minute 格同对下已全文件一致。修正对（仅 G1 差异）下 daily 格逐位一致——**证明 G1 对 etf_t0=false / daily 路径零行为影响**（与代码审查结论一致：`self.etf_t0 and …` 短路，daily 恒 False，`_is_t0` 不触达）。

## 2. (b) per-code 语义档：探针差异逐条归因

pre（无 G1，全局 T+0）vs post（G1，per-code），24 只标的当日卖出结果：

| 结果 | 标的 | 归因 |
|---|---|---|
| ✅ 预期差异 5 | 510300/510500/159915/512480/159995（equity） | pre FILLED（旧语义放行实盘不存在的当日买卖）→ post PENDING（本地拒单 Order 为 falsy，探针 `_oid_ok` 记为受理→14:50 判 PENDING，语义等价拒单，见设计 §7 对账模式） |
| ✅ 无差异 16 | qdii 7（含 520830、513100）+ gold 2 + commodity 2 + bond 2 + money 2 + 恒生/中概/恒生科技 3 | pre/post 均 FILLED（per-code 下这些类别本为 T+0） |
| ✅ 无差异 3 | 161226/501018/162411（LOF） | 两侧均 NO_POS（本地无分钟数据，买入无价未成交，与 A1 近似一致） |

> 513100 归因说明（ZCode 数据事实修正）：本地存在零量 bar（68125 bar 中 1321 根零量、09:35 近期连续零量）但引擎不建模成交量门槛故本地仍成交，与 PTrade 零量不成交形成已知撮合近似。

## 3. 证据索引

- 修正对捕获：`output/g2_etf_t0_regression/pre_v2/`（HEAD−G1）、`post_v2/`（HEAD），各 4 runs（3 daily + probe），含全部 CSV + SHA-256 + 原始日志
- seeded 全矩阵捕获：`pre/`、`post/`（minute 格证据）
- 回归脚本：`scripts/etf_t0_regression.py`；DB 快照：`output/g2_etf_t0_regression/quantstudio_g2.db`（19.9GB，行数核对一致，用后可删）
- 平台实证对照：2026-08-15 PTrade 双轮探针（19 一致/4 平台偏差/1 数据缺口）

## 4. 过程事件与技术债记录

1. **并发提交事件（需用户知悉）**：G2 执行期间其他代理提交并推送了 `005da48/72182ee/68f2f60`（引擎分红/除权修复）与 `ea9cc8a`（本方案 G1，内容与 G1 产出逐行一致）。`ea9cc8a` 的提交早于 G2 通过与用户确认，**违反 AGENTS.md 铁律**（框架层改动须确认后提交推送）；因内容恰为已审 G1 且 G2 现已通过，建议保留该提交，但 G4 文档同步与最终推送仍须按流程补办。
2. **T1 私有访问技术债**：`_load_etf_t0_cache` 经 `providers.market._data` 触达数据访问层（设计文档 §13 T1，G3/后续改为 provider 公共接口）。
3. **T2 GUI 三态控件**：G5 前补齐或文档显式降级（设计文档 §13 T2）。
4. **T3 存量策略迭代顺序依赖（新发现）**：`ETF动量.py`/`etf_theme_rotation` 等存量策略存在依赖 dict/set 迭代顺序的决策/日志逻辑（跨进程结果不稳定）。不影响本回归（已用 PYTHONHASHSEED=0 固定），但 **skill 生成的策略必须避免此类逻辑**（确定性要求），建议在 skill 校验器/契约中明示。

## 5. 结论

G2 双档验收通过：G1（per-code T+0）对 `etf_t0=false` / daily / minute 路径零行为影响（hash 逐位一致 + 日志零 diff），per-code 语义在本地引擎行为正确且与平台实证交叉印证。**建议 ZCode 复核后关闭 G2，进入 G3（skill 契约）**。
