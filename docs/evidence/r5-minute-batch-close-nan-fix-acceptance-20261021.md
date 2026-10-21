# ④ 八项验收证据件：`get_history` 分钟批量 `close`=NaN 缺陷修复

- 日期：2026-10-21 ｜ 缺陷：框架层 `duckdb_data_access.py` + `ptrade_api.py` 复权列整列覆盖
- 方案：`docs/ptrade-minute-batch-close-nan-design-20261020.md`（v1.0，②审通过附五条件）
- 写前快照：`1dd70cf7bd417bcc4bb3c3a233732b94dea4d82a`（已 `git stash store` ✓）
- 实施：2 文件 6 处（3 函数 × pre/post 分支），`git diff --stat` = +24/−12，残留 `notna().any()` = 0，COMPILE = 0

## 知识反哺检索留痕

知识反哺：[无命中] 检索 Obsidian 主库（`knowleage_base`）12 域全量（2026-10-20，grep 关键词：复权/分钟/NaN/批量取数/回测引擎/数据适配/preClose/close_front，命中 39 处，分布于策略文档/平台转换坑/验收盲点/客户运维；**与本缺陷主题（批量取数字段 NaN / 复权列对齐 / 分钟频率批量）相关条目 0 条**）⇒ 判定「无命中」并显式声明。

⚠ **留痕诚实性补正（2026-10-21）**：方案初稿的「无命中」结论正确，但**过程为凑格式而未实际检索**；本轮已补做实际检索（证据如上），结论一致。

## 根因（已确证，②审条件①完成）

```python
# duckdb_data_access.py:1336-1340 / :1234-1239 / ptrade_api.py:1546-1555  （三函数同型）
if qfq in X.columns and X[qfq].notna().any():   # 整列级守卫：只要 1 个 code 有值即触发
    X[orig] = X[qfq]                              # 整列覆盖：其余 code 的 *_front 为 NULL → close 被污染为 NaN
```

- **单码**：该 code 的 `close_front` 全 NULL ⇒ `any()`=False ⇒ 不覆盖 ⇒ 正常（**故单码永不复发**）
- **批量**：混入任一有 front 值的 code ⇒ 触发整列覆盖 ⇒ 其余 code 的 `close` 变 NaN ⇒ `_scalar` 转 `None` ⇒ `get_buy` L207 `continue` ⇒ **`selected=0`**
- 修复：**行级掩码** `_m = X[qfq].notna(); X.loc[_m, orig] = X.loc[_m, qfq]`（仅非 NULL 行覆盖，其余保持原值）
- 参照先例：`duckdb_data_access.py:985` 已有 per-code 守卫（`notna().groupby(code).transform('max')`），本修复比其更严格（逐行）

## 验收八项

| # | 判据 | 结果 | 证据 |
| --- | --- | --- | --- |
| **V1** | 批量 ≡ 单码并集（逐位） | ✅ **PASS** | 数据层矩阵 7 组配置 **TOTAL_DIFFS = 0**：min_cnt1_pre / min_cnt1_post / min_cnt5_pre / min_range_pre / daily_qfq / daily_raw / daily_qfq_cnt27，各 16 码，missing=0，MISMATCH=0 |
| **V2** | 阈值消除（None = 0） | ✅ **PASS** | 2026-01-06 探针 `CD total=16 batch_none=0 single_none=0`（修复前 batch_none=14）；R5 全量 148 日、池规模 16~110，66 个 selected>0 日全部正常 ⇒ 跨池规模无残留 |
| **V3** | 同族路径扫描 | ✅ **PASS** | 全库 `notna().any()` 15 处分类：批量上下文 3 函数 **已同修**；单码上下文 6 处（无风险）；非复权语义 6 处（无关）。实测覆盖 1d 批量 / count>1 / range 批量 / qfq / raw 共 7 组，**0 mismatch** |
| **V4** | R5 全量重跑（8 段拼接） | ✅ **PASS** | 8 段 rc=0，总 21,728 s（≈6.0 h）；**拼接口径**：按段头 `[i/8] YYYY-MM-DD ~ YYYY-MM-DD` 切分，段内取 `QS_REBALANCE_AUDIT` 行 ⇒ **audit_rows=148 / uniq_dates=148 / dup=0**（段间无重叠）；sel_days=66、**buy_days=46、buy_total=61**，与 V5 独立解析结果一致（交叉验证） |
| **V5** | 聚宽对拍（前后交叉表 + 重合率 + 归因） | ✅ **PASS** | 聚宽 30 日/37 笔；修复前 37 日/49 笔/共同16/仅聚宽14/仅新版21/**53.3%**；修复后 46 日/61 笔/共同19/仅聚宽11/仅新版27/**63.3%（+10.0 pct）**；01-06 逐位对齐（selected=2 = 聚宽 2 只） |
| **V6** | 6 策略重转 `api_portability` | ✅ **PASS** | `python scripts/run_contract_gate.py --strategies` ⇒ **CONTRACT GATE : PASS**（exit 0）；含契约套件全通过、矩阵门禁哈希一致、6 策略冒烟全通过 |
| **V7** | 既有测试套件 | ✅ **PASS** | 同 V6 门（受控文件清单 pytest 全部通过；既有失败白名单**无触发**） |
| **V8** | 性能不劣化 | ✅ **PASS** | 本次 R5 单日 147 s（21,728 s ÷ 148 日）vs 修复前 223 s/日 ⇒ **未劣化**（提速系环境/负载差异，**非本修复成果**，如实申报） |

## 剩余差异归因表（下一轮对齐工作输入）

| 类别 | 日数 | 池规模区间 | 归因候选 |
| --- | --- | --- | --- |
| **仍漏买**（聚宽有/新版无） | 11 | 16 ~ 110 | **A-1 择时**（聚宽 `run_daily(get_buy,'09:27')` vs 本地 09:31；09:27 时点本地**无 tick** ⇒ 竞价 bar 取数时点不同） |
| **多买**（聚宽无/新版有） | 27 | 20 ~ 101 | **D1 开比基准**（本地 `preClose` vs 聚宽 T-1 `close` ⇒ 除权日分叉）；**D2 复权口径**（`fq='pre'` base date 与聚宽 `attribute_history` 默认基准可能不同） |

### 漏买明细（11 日）

| 日期 | 聚宽标的 | 新版池 |
| --- | --- | --- |
| 2026-01-26 | 600916 | 72 |
| 2026-02-04 | 002957 | 53 |
| 2026-03-06 | 000815 | 49 |
| 2026-03-19 | 001287 | 42 |
| 2026-05-12 | 601609 | 59 |
| 2026-05-27 | 600635 | 32 |
| 2026-06-01 | 000799 | 37 |
| 2026-06-02 | 002328 | 98 |
| 2026-07-02 | 002468, 002607 | 110 |
| 2026-07-14 | 000989 | **16** |
| 2026-08-05 | 002674 | 104 |

⚠ 池规模跨度 16~110 ⇒ **池大小不是唯一判据**（此前「池大触发」假设已两次被证伪，如实记录）

## 实施侧声明

- **写前快照**：四步配方（`git add -A -- <scope>` → `git stash create` → `git stash store` → `git reset`），scope = `quantstudio/backtest/ptrade_api.py` + `quantstudio/backtest/providers/duckdb_data_access.py`（两文件均属共享核心文件）
- **共享核心文件纪律**：每次 edit 后即时 `git diff <file>` 自检 ✓；提交用精确文件清单（**禁用 `git add -A` 全量**）✓
- **E7 编码委派披露**：全程**未委派 ZCode** —— 原因：6 处精确行级替换与本会话定位上下文强耦合（根因行号、行文本、探针判据均为本会话产出），且前次委派已证同型偏离（方案 v1.2 五处违反）；**未回退表述**（未发生委派失败）
- **未推送**：本轮**未执行任何 `git push`**，⑥ 双仓库推送由用户统一发起