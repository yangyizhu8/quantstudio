# A+B 修复验收与窗内对账单 · 2026-09-25

- 案由：GUI 回测（①断板反包策略空跑 ②逐日估值预取性能）+ ST 标记增量回填（同批执行）
- 修复分型（《修复前置纯增益审计》）：**A = 纯恢复型**；**B = 纯性能型**；**ST F-1 = 数据修正型**
- 快照：`caf3e446`（A/B 实施前）、`fa856051`（纳管补录前）——均已 `git stash store` 持久化
- 本轮状态：**实施与验收完成，随 ⑤ 批推送**（推送前由总调度统一盘点呈批）

---

## 一、影响披露（卷首，供用户侧知悉）· **已按窗内实测更正**

| 策略 | 定性 | 影响面 |
|---|---|---|
| **断板反包策略** | **受害策略（唯一）** | 因 `1237a4f`（09-02 pctChg 可移植性）「同路径产物覆盖 → 剥注入区还原源码」丢失 **16 项定义**（13 参数常量 + `_limit_pct` / `_ensure_runtime_state` / `_bare` 三助手 + `_extract_history_field` 退化为弱化版）⇒ **自 2026-09-02 至 09-24 的 22 天回测全部为空跑产物，结论作废**；修复后 161 日窗（2026-01-05→09-01）为新基线 |
| 动量轮动RSRS择时策略 | **非受害（卫生项）** | 静态扫描命中的 `returns` 位于 `_momentum_score` 的 `return scores`（L167）**之后不可达段**（L172）⇒ 死代码引用，运行时永不触发。**运行期铁证**（5 日窗：零错误、`QS_REBALANCE_AUDIT` 5 行、06-02 `buy_filled=1`）⇒ **历史结论恢复有效、不作废**；死代码清理列为卫生项 |
| 连板梯队龙头打板套利策略 | **非受害（卫生项）** | `_QS_BSE_LEGACY` 未定义，但引用位于 `try/except: pass` 内 ⇒ 静默降级（`QS_ASHARES_BREAKDOWN` 审计日志不输出；`_QS_EXCLUDE_BSE=True` 时过滤会静默失效）。当前配置下行为与设计值一致 ⇒ **不作废**；修复单列六步 |

> **披露更正说明**：初版披露曾把动量轮动RSRS 列为受害策略（依据静态调用链 BFS），经窗内运行时铁证推翻，**已撤回该主张**。教训：运行时铁证优先于静态可达性推断。

---

## 二、A 案（纯恢复型）· 已 PASS

### 2.1 根因
`1237a4f`（2026-09-02，`+1206/-69`）对 `断板反包策略.py` 的 diff 明载 `-def _limit_pct(code):`、`-def _ensure_runtime_state():`；仓库文档 `docs/handoff/pctchg-portability-20260901.md:114` 记录「被同路径产物覆盖 → 已从 10:10 产物**剥注入区还原源码**（230 行，备份 `断板反包策略.py.preclean.bak`）」⇒ **整段参数区 + 工具区被剥除，使用点全部保留**。

### 2.2 恢复清单（16 项，全部逐字取自 `.preclean.bak` 并逐字核验）
- 参数常量 13：`INDEX_CODE`(bak L1100)、`HIST_COUNT`、`VOL_RATIO_MIN`、`VOL_RATIO_MAX`、`DROP_MIN_PCT`、`DROP_MAX_PCT`、`NO_VOL_ONEWORD_RATIO`、`LIQ_AMT_MIN`、`HOLD_DAYS`、`MAX_HOLDINGS`、`PER_POSITION_WEIGHT`、`FIELDS`、`F_TOL`（bak L1100-1112 连续块）
- 助手 3：`_bare`(bak L1115-1116)、`_limit_pct`(bak L1119-1128)、`_ensure_runtime_state`(bak L1155-1166)
- 退化版替换 1：`_extract_history_field`（bak L1131-1152；原版缺 `dtype=float` 形参 ⇒ 调用点 `dtype=str` 抛 TypeError 被 `try/except: continue` 吞掉 ⇒ 候选全跳过 ⇒ **零成交**）

### 2.3 验收（161 交易日 2026-01-05→09-01，影子库）
| 判据 | 结果 |
|---|---|
| 三类错误归零 | **PASS**（`_ensure_runtime_state`=0、`initialize error`=0、生命周期错误=0、`dtype` 关键字错误=0）※ 口径瑕疵见 §五 |
| QS_FILL_AUDIT 出成交 | **PASS**：成交日 3（02-03 `buy_filled=1`、02-25 `buy_filled=2`、03-18 `buy_filled=1`）、有持仓日 **140** |
| 总判定 | **PASS**（修复后即新基线） |
| 性能 | 2611.9s / 161 日 = **16.2 s/日**（全市场 1000 只逐日筛查成本；修复前 GUI 观测 4.7s/日 为 handle_data 立即崩的「假快」，不可比） |

---

## 三、B 案（纯性能型）· 已 PASS

### 3.1 根因与改动
- 调用链：日循环 → `ptrade_api.py:582 _fundamental.preload(prev_date)` → `duckdb_provider.py:180 preload → preload_fundamentals_pit` → `duckdb_data_access.py:370 query_valuation_for_preload`（注释明示「按日 PIT 重算，不缓存！」）
- 单次调用含 **3 处** `QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY <ts> DESC) = 1` 全表排序（965 万 / 971 万 / 74 万行）
- 改动（`duckdb_data_access.py::query_valuation_for_preload`）：三处全局排序 → `max(<ts>) GROUP BY code` 哈希聚合 + 等值连接；列名/别名/COALESCE/CASE 逐字不变
- 可观测性（`backtest_engine.py:515`）：预取被跳过 `logger.debug → logger.warning`（控制流零改动）

### 3.2 B0 前置双证（未过则不改框架）
| 证 | 结果 |
|---|---|
| 唯一性前提 | 三表 `(code,time)`/`(code,end_date)` **全唯一**（9,652,068 / 9,714,918 / 744,067）⇒ 无 tie |
| 等价性 | 5 交易日旧/新对照：行数、列序、dtype、**排序后逐值全等**（PASS×5） |
| 行序 | 新旧**原序不同**（唯一不等价项）⇒ 已证消费方 `get_fundamentals_from_preload` 全链（`isin` 掩码 → 逐元素 `apply` → `set_index('code')` → 列筛选）**零位置依赖** ⇒ 不影响可观察结果（总调度裁定②已接受该充分判据） |
| 提速 | 单次 5.58-10.69s → 0.19-0.42s（**24.0×**） |

### 3.3 主证
| 项 | 结果 |
|---|---|
| 等价性主证 | 309 日窗 `ab_prevclose.py new ma`：**nav_sha = e7e3789a355fd941c414a819**，与卷内 9-19 基线**逐位一致**（nav_len 309、iterrows_907=0 同） |
| 性能主证（同日同环境对照） | 修复前 309 日窗 >600s 未跑完（30 日窗 427.5s ≈ 13.4s/日）→ 修复后 **104.4s ≈ 0.34s/日**（**≈39×/日**） |

> **口径标注（防歧义）**：0.34 s/日 与 16.2 s/日 **不同策略、不同测量对象**——前者为引擎侧每日取数成本（双均线策略＝单股样本，309 日窗同日对照），后者为断板反包策略自身全市场筛查成本（1000 只 × 27 根 + e0-e9 漏斗）。两者均跑于影子库，与主库 daemon 无关。

---

## 四、ST F-1（数据修正型）· 已闭环

| 步 | 结果 |
|---|---|
| 权威口径 | 失配集由 aligner **同源**实现复算：`FieldAligner._pit_st_flags_duckdb(df, namechange_df)`（DuckDB ASOF JOIN）——**不另写第二实现** |
| ⑤-1 dry-run（2025-01-01 起） | 校验 2,282,264 行 → 漏标 3,420 / 误标 119 = **3,539**；指纹 `653343ec8a2e11ff83b268d1`；备份独立 parquet（主库零 DDL） |
| ⑤-2 恢复演练（受控植入） | 植入 50 漏标 → 检出**恰好 50** → apply 后 V1=0 → restore 后失配回 **50** 且 True 行精确回 **3,558** ⇒ 检出/应用/回退**值级精确** |
| ⑤-3 apply（主库） | 指纹闸通过 → 单事务 UPDATE 漏标 3,420 / 误标 119（**与 dry-run 逐位一致、零外溢**）→ COMMIT + CHECKPOINT |
| ⑤-4/7 复验 | 本窗 V1 = **0**；全历史剩余 553（预期，分窗前段未做） |
| ⑤-5/6 前段 apply | 漏标 322 / 误标 231 = **553** → apply → **全历史 V1 = 0** |
| 跨窗守恒（独立验证） | 3,539 + 553 = **4,092**，与请示预估**逐位吻合** |
| 中止条件实绩 | ⑤-3 首跑因 B′ loader（`minutes_window_loader.py`）持主库写锁而**中止、主库零写入**（异常发生在 `connect()` 阶段，未开事务）；锁释后重跑成功——与 B′ 的写锁串行化属设计行为 |

---

## 五、窗内其余对账单

| 项 | 判定 | 证据 |
|---|---|---|
| ⑥ 6 策略横验 | **PASS** | `scripts/run_contract_gate.py --strategies` → 契约套件全过 + 矩阵门禁 OK + api_portability 冒烟全过（无白名单触发）→ `CONTRACT GATE : PASS` |
| ⑦ 全套 pytest | **55 failed / 3176 passed**（exit 1），**归因他线** | 失败集中于 `test_source_import`（include_false/include_mapping）、`security_metadata`、`strategy_fidelity_gates`、`strategy_spec_schema`、`target_aware_strategy_skill`、`user_pyqt_candidate_flow`；**同批 7 文件在 HEAD worktree（不含本批改动）复现 16 failed** ⇒ 既有/他线在途（E1 include=False / 编译发布管线），与本批改动面零交集。**放行条件（总调度裁定）**：本批相关套件全绿 + 既有失败 HEAD 复现 + 逐项声明 ⇒ 本件即逐项声明载体 |
| 附 动量运行时铁证 | **完成** | 见 §一披露表（非受害策略，撤回作废声明） |

---

## 六、实施期自我纠正（入卷，供防线设计引用）

| # | 缺陷 | 实例 | 定性 |
|---|---|---|---|
| 1 | 静态扫描器**只查被调用名**，未查全量 Load | 漏掉 `INDEX_CODE` 等 13 常量 | 已升级为全量 Load 口径 |
| 2 | 静态扫描器**作用域不敏感**（任意 Store 即算已定义） | 漏报 `_bare`（函数内局部变量遮蔽判定） | **防线第 4 口径**（作用域敏感收集）+ `_bare` 回溯验证已通过（内存态移除 `def _bare` ⇒ 检出 L1522 `_bare`） |
| 3 | 验收脚本**判据字符串不匹配** | 统计 `执行失败` 而实际文案为 `handle_data 错误` ⇒ 161 日窗"三类错误归零"曾假绿 | 窗后批修正（小窗校准） |
| 4 | 取证期**用关键词过滤日志**（两次） | 漏掉 `QS_REBALANCE_AUDIT`；漏掉 `handle_data 错误` | 纪律：**取证阶段一律全量打印，筛选仅用于呈报** |
| 5 | 静态可达性**误判** | 据 BFS 将动量策略判为受害（实为死代码引用） | 纪律：**运行时铁证优先于静态推断** |

---

## 七、回退与提交

- **回退**：A/B 单 commit revert 即可（三文件无交织）；ST F-1 回退 = `st_f1_backfill.py --mode restore --backup <备份>`（备份文件 `agent_workspace/st_f1_backup/mismatch_20260925_163425.parquet`（3,539 行）与 `mismatch_20260925_200734.parquet`（553 行））
- **随批清单（本线）**：`backtest_engine.py`、`providers/duckdb_data_access.py`、`strategies/断板反包策略.py` 三文件 + 本证据件；另有**已独立成笔**的纳管补录 `4c30cf2`（7 策略文件，未推送）
- **推送纪律**：待总调度统一盘点（巡检 5 笔 + Q2 两笔 + ST F-1 产物）呈批后，一次推送并按 C7 逐笔声明 + QuantStudio-trading 同步门
