# QFQ 重锚第二批 · 第五轮报告：B-1 fresh_staged 本地实现完成

日期：2026-07-28 凌晨（第五轮，执行用户 2026-07-27 设计批示：批准 B-1，否决 B-2/B-3）
状态：**B-1（fresh xtquant 分钟前复权逐值写入）已按 9 条强制边界完成本地实现与真实验收；ratio 方法 B 保留且行为逐位不变**
铁律状态：**未 stage / 未 commit / 未 push / 未创建 PR / 未同步 GitHub**（暂存区为空，等待用户确认后才执行框架层同步流程）。

关联文档：
- 设计决策与批准记录：`docs/qfq-reanchor-minute-model-decision-20260727.md`
- 第四轮报告（方案 A 严格 BLOCK 闭合）：`docs/qfq-reanchor-batch2-fix-report-20260727.md`

---

## 0. 时间线说明（用户第四轮验收备注要求）

用户指出：会话日期为 2026-07-27，但部分报告文件标为 2026-07-28 凌晨。核查结论：

- 系统时钟实测：`date` → **2026-07-28 01:31:18 +0800**（Asia/Shanghai，UTC+8）；
- 成因：第四轮 → 第五轮工作为同一连续会话，从 7-27 晚间持续**跨过午夜**；报告/fixture 落盘时间标 7-28 凌晨是**真实物理时间**，不是时钟或时区错误；
- 处理：审计时间戳（文件 mtime、事件表 started_at/finished_at、报告日期）一律**保留真实值，不做人工校正**；文档中"2026-07-27"指用户批示/采集日期，"2026-07-28 凌晨"指落盘时间，两者并存不构成矛盾。

## 1. 本轮为实际新版本的证明：文件修改时间

| 文件 | 本轮修改时间 (UTC+8) | 修改性质 |
|---|---|---|
| `quantstudio/pipeline/qfq_reanchor_engine.py` | 2026-07-28 01:17:18 | **B-1 引擎实现**（第四轮引擎零修改，本轮为批准后的框架层变更） |
| `tests/test_qfq_reanchor_batch2.py` | 2026-07-28 01:29:54 | 新增 `TestFreshStagedModel` 10 项 + ratio 事件断言显式化 |

## 2. B-1 引擎实现 vs 用户 9 条强制边界逐条对照

| # | 用户边界（2026-07-27 批示） | 实现位置与方式 |
|---|---|---|
| 1 | 保留 ratio 方法 B；显式 `model={ratio,fresh_staged}`；禁止静默切换；模型选择及原因写事件审计 | `MODELS=("ratio","fresh_staged")`；`apply_reanchor_for_security(model=, model_reason=, fresh_minutes=)`；ratio 传 fresh_minutes → `ValueError`（事务外，防"BLOCK 后换数据重试"）；fresh_staged 缺 model_reason / fresh_minutes → `ValueError`；事件 payload 恒写 `model`/`model_reason` |
| 2 | staged 分钟主键 `(code,freq,time)`，只 UPDATE 四个 front 列；禁触 raw OHLC/volume/amount/preClose/*_back/update_time/data_source；禁 DELETE/INSERT 重建 | `stage_fresh_minutes`（TEMP TABLE，本连接可见）+ `apply_fresh_minute_staged`（单条 `UPDATE ... SET 四 front 列 FROM staged`，无 DELETE/INSERT）；测试 `_assert_nonfront_unchanged` 两表逐列逐值证明 |
| 3 | 写入前逐 bar 验证 stored raw == fresh(dividend_type=none) raw；key/coverage/NULL/session/cadence 异常整券 BLOCK | precheck：raw 逐 bar `|Δ|≤1e-9`（stored raw NULL 亦 BLOCK `minute_raw_null`）；staged 契约校验：缺列/code/freq 不符/dup key/非法 session（午间、偏 cadence）/NULL 或非 finite>0 → 各自 reason 整券 BLOCK |
| 4 | 完整覆盖：staged==target==matched，missing_target/missing_staged/duplicates/raw_mismatch 全 0 | `apply_fresh_minute_staged` coverage 统计不满足即 BLOCK `minute_coverage_incomplete`；统计 dict 写入事件审计 |
| 5 | COMMIT 前新增 4 项 postcheck；post-write front vs fresh 逐 bar ≤1 tick，bars_over_1_tick==0 | `run_postchecks` 新增 `minute_staged_match`（IS DISTINCT FROM 精确一致）/`minute_raw_match`/`minute_coverage`/`minute_tick_error`（记录 bars_over_1_tick、max_abs_err、tick_size=0.01）；仅 fresh_staged 出现，ratio 仍六项 |
| 6 | 原有 daily staged/front-chain/K线/行数/cross-table/NULL/原始字段/CalendarService/list_date/事务回滚/event/anchor 门禁全部保留 | 六项原 postcheck 原样保留；front_chain/scale_consistency 增加**模型感知豁免**（fresh_staged 下：乘法偏离 ≤ 容差 **或** 加法偏离 ≤1 tick；ratio 下逐位原逻辑） |
| 7 | 每证券同一 connection 单事务：staged daily+minute → precheck → minute update → daily update → 全部 postcheck → event → anchor → COMMIT；任一失败 ROLLBACK | 事务主体在 `BEGIN...COMMIT` 内按此顺序执行；`ReanchorBlocked`→blocked、`PostcheckFailed`→rolled_back、其他异常→failed，均 ROLLBACK + 独立短事务记录失败事件 |
| 8 | 真实验收：600875/600039/002864 全部 committed；minute front vs fresh 逐 bar ≤1 tick；002864 daily 逐值不变 | 见 §4（三证券全部 committed，实测逐 bar 误差 ≤1e-9，002864 daily 含 front 四列逐值不变 atol=1e-9） |
| 9 | 仅限本地实现；完成后按框架层门禁汇报并等待 GitHub 同步确认 | 未 stage/commit/push/PR；本报告即框架层修复汇报，**等待用户确认后**才执行 AGENTS.md 规定的同步流程（代码+README+docs 引用文档） |

## 3. 新增测试：`TestFreshStagedModel`（10 项，全部通过）

合成结构（hermetic，tmp_path 临时库）：

1. `test_fresh_staged_committed_only_four_front_cols`：正常提交——分钟 front 逐值 = staged fresh（含 09:30 bar）；两表除四 front 列外逐列逐值未变；对照证券 000001 未动；覆盖统计 30/30/30、missing/duplicates/raw_mismatch 全 0；postcheck **10 项齐全**（原 6 + B-1 4）；`minute_tick_error.bars_over_1_tick==0`、`max_abs_err≤1e-9`；事件审计 `model="fresh_staged"`+`model_reason`+`minute_coverage`；anchor 同事务推进；staged 临时表已清理。
2. `test_fresh_staged_raw_mismatch_blocks`：污染 1 根 fresh raw close → BLOCK `minute_raw_mismatch`，整券回滚、anchor 未推进、失败事件独立记录。
3. `test_fresh_staged_coverage_incomplete_blocks`：(a) fresh 缺 1 bar（missing_staged>0）与 (b) fresh 多 1 根合法时刻 bar（missing_target>0）→ 均 BLOCK `minute_coverage_incomplete` + 整券回滚。
4. `test_fresh_staged_contract_violations_block`：午间 bar → `fresh_minutes_bad_session`；front NULL → `fresh_minutes_null_or_bad`；重复 key → `fresh_minutes_dup_key`。
5. `test_fresh_staged_postcheck_corruption_rolls_back`（故障注入，两级纵深）：
   - **半 tick（0.005）污染**已写分钟 front（穿过全部容差类门禁）→ `minute_staged_match` 精确逐值不变量拦截 → `rolled_back` + 全表回滚；
   - **>1 tick（0.05）粗污染** → 原有 `scale_consistency` 更早拦截 → 同样回滚。
   - 设计事实（写入报告备案）：`minute_staged_match`（IS DISTINCT FROM 精确一致）**严格强于** `minute_tick_error`（≤1 tick），主流程任何写入污染必先被前者拦截；`minute_tick_error` 因此无法在主流程独立触发，其价值为**防御纵深 + 审计证据**（postcheck_summary 固化 bars_over_1_tick/max_abs_err），committed 用例逐项断言其为 0——用户边界 5 的"bars_over_1_tick 必须为 0"以此口径闭合。
6. `test_no_silent_model_switching`：三类防呆 `ValueError`（ratio+fresh_minutes / 缺 model_reason / 缺 fresh_minutes），全部发生在事务外——无写回、无事件、无 anchor。
7. `test_ratio_path_unaffected`：默认 model=ratio 提交行为不变；postcheck 仍为原六项集合；`minute_coverage` 空；事件 `plan["model"]=="ratio"`、`model_reason is None`、无 `minute_coverage` 键。

真实数据（固化 fixture，sha256 校验）：

8-10. `test_real_three_securities_fresh_staged_committed[600875/600039/002864]`：见 §4。

## 4. B-1 真实验收结果（用户边界 8）

三证券使用第四轮固化的 fresh xtquant OHLC 9 列 fixture（raw=dividend_type none / front=dividend_type front，逐值直采，sha256 不变），fresh_staged 模式：

| 证券 | 结果 | minute front vs fresh（2169 bar × 4 列） | bars_over_1_tick | 备注 |
|---|---|---|---|---|
| 600875 | **committed** | 逐 bar 最大误差 ≤ 1e-9（逐值写入，实际为 0） | 0 | 除息日 front 链收益 = **−6.024982%**（伪跳空 −7.82% 消除，abs 5e-5 内断言） |
| 600039 | **committed** | ≤ 1e-9 | 0 | 除息日 front 链收益 = **−2.759382%**（伪跳空 −7.46% 消除） |
| 002864 | **committed** | ≤ 1e-9 | 0 | **daily 全表（含四 front 列）逐值不变**（atol=1e-9 断言）——daily 已正确，仅分钟被修复 |

- 三证券覆盖统计均为 staged==target==matched==2169（9 交易日 × 241 bar），missing/duplicates/raw_mismatch 全 0；
- 两表除四 front 列外全表逐值未变（`_assert_nonfront_unchanged`）；
- 事件审计：`model="fresh_staged"` + 批准依据 model_reason + minute_coverage；anchor 同事务推进至 (1, ok, event_id)；
- **对照组保留**：同一 fixture 在 ratio 模式默认容差下仍 BLOCK（`TestRealDataRegression` 原测试不动）——B-1 是经用户批准的模型切换，不是容差放宽。

第二批原始自动修复目标（消除 600875/600039/002864 真实分钟伪跳变）至此完成。

## 5. B-2 / B-3 否决状态

按用户批示，B-2（additive delta 混合事件代数风险过高）与 B-3（实质等于方案 A）**不实现**；`docs/qfq-reanchor-minute-model-decision-20260727.md` 中对应章节仅作历史设计记录，不进入代码。

## 6. pytest 回验（2026-07-28 凌晨，系统 Python 3.11.9）

```
# batch2 单独（tests/test_qfq_reanchor_batch2.py）
61 passed in 52.80s        # 51 原有（ratio 行为逐位不变）+ 10 新增 B-1

# batch1 + batch2 联合（tests/test_qfq_reanchor_batch1.py + tests/test_qfq_reanchor_batch2.py）
150 passed in 60.58s       # 140 原有 + 10 新增 B-1

# 项目全量（pytest tests/）
1 failed, 1359 passed, 1 warning in 262.87s
```

**全量唯一失败说明（与 B-1 无关）**：

- 失败用例：`tests/test_pipeline_guardrails.py::test_financial_dedup_keeps_latest_ann_date`（断言 `000159 应只保留1条，实际 2`）。
- 根因：`quantstudio/pipeline/validator.py` L359-378 的财务去重逻辑在**前序工作区（未提交）改动**中已由"同一报告期按 max(ann_date) 保留最新"改为"优先按 `update_flag` 列保留"；该测试的 df 无 `update_flag` 列，回落到 `drop_duplicates(subset=pk_cols, keep="last")`，而主键含 `ann_date`，导致两条不同 ann_date 记录均被保留。
- 判定：属前序 validator 工作区改动与既有测试契约的不一致，**不在本轮 B-1 批准范围内**。修复方向（改 validator 回退去重语义 vs 改测试契约）涉及框架行为判断，须用户另行批示，本轮不擅自处理。
- 交叉验证：B-1 改动前该冲突即已存在（validator.py 的 diff 早于本轮 B-1 文件修改时间），且 B-1 涉及的 batch1/batch2 全部 150 项通过，B-1 未触碰 validator.py。

## 7. 框架层修复汇报（AGENTS.md 铁律流程）

本轮属**回测框架数据管道层修复**（QFQ 重锚引擎），按铁律：

- ✅ 本地修复完成（引擎 + 测试 + 本报告）；
- ⏸️ **等待用户确认**后才执行 GitHub 同步（https://github.com/yangyizhu8/quantstudio ）：
  - `quantstudio/pipeline/qfq_reanchor_engine.py`；
  - `tests/test_qfq_reanchor_batch2.py` 与 fixture；
  - `README.md` 及 `docs/strategy_toolbox.md`、`docs/prompt_engineering.md` 中涉及内容（如有）；
- ❌ 未获确认前不 stage/commit/push/PR。
