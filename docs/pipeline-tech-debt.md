# Pipeline 技术债务清单

> 2026-07-17 系统性加固时记录。以下问题在探索阶段发现但**本次未修**（避免范围蔓延），
> 按优先级排列，供后续迭代处理。每项含：位置、症状、风险、建议修法。

本次已修复的问题见 `tests/test_pipeline_guardrails.py`（12 个回归测试覆盖）。

---

## P1（建议优先处理）

### ~~TD-1：PER_DATE 路径绕过 RateLimiter~~ ✅ 已修复（2026-07-17）
- **位置**：`daemon.py` PER_DATE 分支
- **修复**：新增 `_api(api_fn, **kwargs)` helper 封装 `adapter._retry_with_backoff`，5 个裸调点（预拉 adj_factor + process_one_day 内的 daily/daily_basic×2/adj_factor）全部改为 `_api(pro.XXX, ...)`
- **测试**：`test_per_date_api_calls_go_through_rate_limiter`

### ~~TD-2：PER_DATE 路径 isST 格式不匹配~~ ✅ 已修复（2026-07-17）
- **位置**：`daemon.py` PER_DATE process_one_day
- **修复**：`raw_df["isST"] = raw_df["ts_code"].apply(lambda c: 1 if str(c).split(".")[0] in st_codes else 0)`（split 后比较裸码，与 tushare_adapter.py 主路径一致）
- **测试**：`test_per_date_isst_matches_bare_code`

### TD-3：task 级 retry/rate_limit 配置不生效
- **位置**：`daemon.py` `_get_adapter()`（约 :629 行）只传 sources_config 配置给 adapter
- **症状**：collector_tasks.json 每个 task 配的 `retry{max,backoff_sec}` 和 `rate_limit{calls_per_min}` 从未被读取，adapter 用的是 base.py 默认值或 sources_config 的全局值
- **风险**：中。配置失真，task 级精细限流无法实现
- **建议修法**：`_get_adapter` 接收 task 配置，merge 进 adapter config；或在 `_execute_task` 内按 task.rate_limit 动态调整 `adapter.rate_limiter.calls_per_min`

---

## P2（次要）

### TD-4：RateLimiter 非线程安全
- **位置**：`base.py:23`（`_timestamps: List` 共享但无锁）
- **症状**：多线程并发 `acquire` 时，`[t for t in ...]` 过滤 + len 判断非原子，可能短暂突破限流
- **风险**：中。并发下限流精度下降（实际请求略多于配置）
- **建议修法**：加 `threading.Lock` 保护 `_timestamps` 读写；或改用 `collections.deque` + 原子操作

### TD-5：quarantine.py 缺 replay() 方法
- **位置**：`quarantine.py`（只有 mark_fixed/mark_replayed 改状态，无实际重放）
- **症状**：隔离数据无法自动重放，需外部脚本驱动（设计意图未闭环）
- **风险**：中。修复闭环不完整，隔离数据堆积
- **建议修法**：新增 `replay(quarantine_ids, aligner, validator, writer)` 方法，从 original_payload 重建 df → 重跑对齐校验入库

### TD-6：writer.py 双重 pk 字典重复
- **位置**：`writers.py` `pk_for_dedup`（:241-256）和 `pk_cols`（:281-296）两份硬编码字典
- **症状**：主键定义重复维护，容易漂移（新增表需改两处）
- **风险**：低。当前两份一致，但维护负担
- **建议修法**：合并为单一 `PK_COLS` 字典，从 alignment_rules.schema.primary_key 动态读取（与 validator 一致）

### TD-7：validator error_values 只存首行
- **位置**：`validator.py:237`（`error_values_list[0] if error_values_list else None`）
- **症状**：多行隔离时只保留首行错误值，其余行的具体错误值丢失
- **风险**：低。排查时只能看 original_payload，不能批量看错误分布
- **建议修法**：error_values 改为 list（每行一个 dict），或聚合为 `{rule: [values]}`

---

## P3（次要，可选）

### TD-8：daemon 健康检查时间戳解析
- **位置**：`daemon.py:730`（`age_h = (datetime.now() - pd.Timestamp(last))...`）
- **症状**：`last` 是毫秒时间戳字符串（如 `1689000000000`），`pd.Timestamp(大数字字符串)` 解析异常
- **风险**：低。健康检查的过期告警失效（但不影响主流程）
- **建议修法**：`pd.Timestamp(int(last), unit='ms', tz='Asia/Shanghai')`

### TD-9：qfq_aux.db 路径不一致
- **位置**：`daemon.py:285`（用 `self.writer.db_path`）vs `daemon.py:653`（硬编码 `ROOT/"data"/"quantstudio.db"`）
- **症状**：两处 QFQMaintenance 用的 db_path 可能不同，复权快照读写到不同文件
- **风险**：低。默认路径相同时无影响，自定义路径时复权可能错位
- **建议修法**：统一用 data_config 的 qfq_path 配置

### TD-10：日志被强制降级
- **位置**：`daemon.py:267-269` / `:476-478`（PER_DATE/PER_STOCK 静默 aligner/validator/writers 日志到 WARNING）
- **症状**：排障时看不到对齐/校验的 INFO 级审计细节
- **风险**：低。可观测性下降，但不影响正确性
- **建议修法**：改为可配置（task.log_level 或全局 verbose 开关）

### TD-11：check_limit ST 涨跌停阈值未生效
- **位置**：`quantstudio/backtest/libs/shared_ashare_rules.py:38-48` 的 `is_st_stock(code, name)` 当 `name=None` 时返回 `False`
- **调用方**：`ptrade_api.py:903-939` 的 `check_limit` 调用 `get_price_limit_pct(qmt_code)` 没传 name
- **后果**：ST 股票用 10% 涨跌停阈值（主板正常股），而非 5%（ST 股专用）
- **风险**：中。ST 股涨停/跌停判定与 Ptrade 不一致（5% vs 10%），影响 `check_limit` 返回值，可能导致策略在 ST 股上误判涨停
- **依赖**：本地 DuckDB 需有 name 字段（当前 `stock_daily` 无 name，`get_stock_name` 返回 code 作为名称）。数据层补 `stock_basic.name` 或 `stock_info.name` 后才能修复
- **优先级**：P2
- **缓解**：ST 股通常被 `filter_stock_by_status` 过滤掉，策略不买卖 ST 股，因此实际影响有限

---

## 本次加固已修复的问题（对照）

| # | 问题 | 修复 | 测试 |
|---|------|------|------|
| 1 | UnitCheck 误判指数（硬编码跳过） | 读 schema.columns.close.unit | test_unit_check_skips_non_yuan_unit |
| 2 | 财务重述版本丢失（keep="last" 吞掉重述版） | PIT 去重：不同 ann_date 版本**全部保留**；仅对完全相同 `(code,end_date,ann_date)` 完整主键去重；有 `update_flag` 时同完整主键优先 `update_flag=1`，无则确定性去重 | test_financial_dedup_keeps_latest_ann_date（已改为断言两版都保留）+ 新增 4 项见下 |
| 3 | writer 返回提交行数非新增行数 | WriteResult 携带 .new/.updated | test_writer_upsert_distinguishes_new_and_updated |
| 4 | 调试配置残留（kline_1m 600000.SH） | config_lint 启动校验 + 改 ALL | test_config_lint_catches_debug_residue |
| 5 | RateLimiter 双时间戳（限流减半） | 删除重复 append | test_rate_limiter_single_timestamp_per_acquire |
| 6 | 三大报表缺 PIT 门禁 | 补 available_at_field=ann_date | test_financial_pit_gate_required_by_config_lint |
| 7 | IsSTNull 硬编码表名 | 读 schema.columns.isST.required | （含在 test_unit_check_still_active_for_yuan_unit） |
| 8 | PctChgRange 硬编码 22% | 读 pctchg_tolerance_pct + 指数跳过 | （含在 test_unit_check_skips_non_yuan_unit） |
| 9 | TD-1 PER_DATE 绕过限流 | _api 封装走 _retry_with_backoff | test_per_date_api_calls_go_through_rate_limiter |
| 10 | TD-2 PER_DATE isST 格式不匹配 | split('.')[0] 比较裸码 | test_per_date_isst_matches_bare_code |

---

## 2026-07-27 validator PIT 去重语义修订（独立框架行为变更，不与 QFQ B-1 捆绑）

> **重要**：本变更是 validator 框架的通用去重语义修正，**独立于 QFQ 重锚 B-1**，单独回归、单独汇报。

### 问题

旧实现 `keep="last"`（或按 `ann_date` 降序 `keep="first"`）会**吞掉财务重述版**：同一
`(code, end_date)` 下不同 `ann_date` 的公告版本只留一份，历史重述轨迹丢失，as-of 查询看不到
初版与重述版的差异，违反 PIT（point-in-time）语义。

### 修复（validator.py L361-389）

```python
if "update_flag" in df.columns:
    upd_rank = pd.to_numeric(df["update_flag"], errors="coerce").fillna(-1)
    order = upd_rank.sort_values(ascending=False, kind="stable").index
    df = df.loc[order].drop_duplicates(subset=pk_cols, keep="first")
    df = df.loc[df.index.sort_values()]   # 恢复原行序（确定性输出）
else:
    df = df.drop_duplicates(subset=pk_cols, keep="last")
```

**正确 PIT 语义**：

1. **不同 `ann_date` 版本全部保留**（不回退到 `max(ann_date)`，不吞重述版）。
2. 仅对**完全相同** `(code, end_date, ann_date)` 完整主键去重。
3. 有 `update_flag` 时，同完整主键优先 `update_flag=1`；无 `update_flag` 走确定性重复去重。
4. 输出恢复原始行序，保证确定性。

适用表（主键含 `ann_date`）：`balance_statement` / `fin_indicator` / `income_statement` /
`cashflow_statement` / `stock_float_share`；其中 `fin_indicator` 含 `update_flag` 列。

### 测试（tests/test_pipeline_guardrails.py，共 5 项，18 passed）

- `test_financial_dedup_keeps_latest_ann_date`（**契约更新**）：000159 初版+重述版**都保留**，len==2，ann_date 顺序 `[v1, v2]`。
- `test_financial_dedup_retains_all_ann_date_versions`：3 个不同 ann_date 全保留，`fixed_count==0`。
- `test_financial_dedup_same_ann_date_keeps_one`：同完整主键仅留 1 条（无 flag 保留最后一条 5.3e8）。
- `test_financial_dedup_update_flag_priority`：`flag=1` 优先（5.25e8）；不同 ann_date 仍保留。
- `test_financial_pit_asof_sees_correct_version`：as-of 两次公告之间见初版 5.2e8，之后见重述版 3.69e9。


## wsgm10 产物-设计漂移（2026-09-08 登记 · 框架修复验收中独立发现项）

- **状态**：REGISTERED → 处置待推进（与 get_index_day_bar 框架修复解耦，另行立项）
- **位置**：agent_workspace/wsgm10/agent_strategy_design.json ↔ quantstudio/backtest/strategies/weekly_smallcap_growth_momentum_10_quantstudio.py
- **症状**：validate_agent_strategy（design=wsgm10）BLOCK 4 规则：
  - DESIGN-CODE-API / HARDFILTER-LIMIT / PORTFOLIO-CASH-BUFFER-CONTRADICTION / PORTFOLIO-EXPOSURE-CONTRADICTION
  （设计 JSON 与策略文件契约脱节，含 PORTFOLIO 现金/敞口自相矛盾类规则）
- **HEAD 一致证据**：HEAD 校验器对照逐字一致（4 规则同），归因既有；5 canonical 策略源码 get_index_day_bar 调用数=0 → 与本次框架修复零相关
- **解除条件（二选一）**：
  - 选项 A：以已 PASS 的 wsgm10v2（周频小市值成长动量（三层止损）.py）替代并退役 weekly_smallcap_growth_momentum_10_quantstudio.py 旧文件；
  - 选项 B：对该文件重转/重生成修复 design 漂移（对齐 design 与源码契约），走自身验收（六步流水线或 legacy 管线对应流程）。
- **推进归属**：本项与 get_index_day_bar 修复解耦，单独排期推进；推进完成前不得关闭本条。

## D4 平台差异登记：get_index_day_bar 平台等价物探针（2026-09-08 登记）

- **状态**：PROBE_COMPLETE(2026-09-09，两轮平台实证) → 重写规则实施中（docs/evidence/d4-index-bar-probe-evidence.md：首轮 23 日（指数支持/include 语义/双端一致）+ preclose 探针 v2 全 PASS（PRECLOSE_PATH_UNLOCK：P1 非空/P2 preclose==前日close 14 对精确/P3 双端 pctChg -1.8498 vs -1.8497、-3.0460 vs -3.046 MATCH）。重写映射钉死：平台 get_history(count,'1d',field=[open,high,low,close,volume,money,preclose],include=True) → wrapper pctChg 合成 → 本地 8 字段契约。实施=docs/index-bar-rewrite-rule-design.md v3（六步流水线）
- **D4 编号**：D4 登记序列新增条目（get_index_day_bar 平台等价物）
- **探针项**：① 平台 get_history 对指数代码（000001.SS）的支持；② 平台日线 include=True 是否含当日
- **重写映射（登记为后续项）**：converter 将 get_index_day_bar 重写为平台 get_history('000001.SS', count=N, frequency='1d', fields=[...], include=True, fq='pre')——重写仅发生在转换器层，策略源码永不出现 include=True，NO-LOOKAHEAD-INCLUDE 硬闸不破
- **解除条件**：平台探针实证通过后，注册重写映射并解锁转换；探针未过前保持 local_only_symbols 封禁



## %s 日志格式平台差异（2026-09-08 登记 · 转换门禁修复验收中独立发现）

- **状态**：REGISTERED → 排期推进（与转换门禁修复解耦，独立立项）
- **症状**：真实 PTrade 平台 log 不兼容 printf 双参风格（log.info('fmt %s', arg)），%s 字面输出不替换
- **影响**：日志可读性（不影响交易逻辑）；转换产物中策略源码的 %s 风格 log 调用未被改写
- **建议修法**：转换器对策略源码 log 调用做格式改写（%s → f-string 或 str 拼接），或文档明确平台 log 约定要求策略源码用平台兼容写法
- **关联证据**：恐慌抄底策略 PTrade 回测日志（2026-09-08 用户提交，QS_INDEX_BAR_FAIL code=%s err=%s 字面输出）

## fail-soft except 吞 NameError 教训（2026-09-08 登记 · 转换门禁修复同源）

- **状态**：REGISTERED → 立项评估（fail-loud 边界）
- **教训**：恐慌抄底策略 _signal_fired 的 except Exception 把平台 NameError 静默转为 fail 降级，掩盖了转换缺陷（get_index_day_bar 透传）整窗空仓——契约违约被 fail-soft 吞掉一个月才发现
- **评估方向**：本地专用 API 调用失败应 fail-loud（抛错/显式告警）而非静默降级；fail-soft 仅适用于数据缺失类（指数数据不足→不触发信号是合法 fail-soft），不适用于 API 契约违约
- **关联**：本策略 _signal_fired fail-soft 设计（R2.5 确认时未被识别为掩盖风险）；转换门禁修复后该场景已从源头消除（转换即 BLOCK），本项评估为通用边界（其他策略的 fail-soft except 是否有同型掩盖）
- **推进归属**：单独排期评估，不阻塞转换门禁修复

## 云端 etf_minutes close 口径与客户端还原链自洽性疑问（2026-09-23 登记 · **已升级为近期解决事项**）

- **状态**：**已响应 · 修复派单 Trae（生产端）**（`RESPONDED_FIX_DISPATCHED_TO_TRAE`）——
  2026-09-23 用户裁定升级为近期解决事项；勘察已完成并**提前于 9/27 节点**交付
  （`docs/evidence/p1-cloud-etf-minutes-close-caliber-aprraisal-20260923.md`）。
  **上游已响应**：定位真实缺陷 = **分钟表 r² 双重复权 + B 类混存**，**归生产端**，
  **修复派单 Trae**。
  **我方证据经云端三点复核并已出勘误**（`docs/evidence/p1-errata-20260923.md`，诚实归档）：
  ①「~10×」被**单位差证伪**（实为单位换算常数，P25~P75 仅 0.03% 宽）⇒ 我方"口径分裂"定性过强已修正；
  ② 96.75% vs 82.36% 系**容差口径差**（±5% vs ±1%，同分布下 94.40% 对 79.05%，均自洽）；
  ③ n=3,214 vs 1,600 系**我方统计含次日行**（未按 trade_date 单日过滤）⇒ 单日真实码数 1,607。
  ⇒ 本条目**等待上游修复落地**；生产端修复完成后，由 Trae 双层扫描器验证收敛
  （`close/(amount/vol)` 与 tdx 比值双双 ≈1）。
  **本会话角色**：证据提供方 + 勘误记录方 + 对拍基准口径移交方（**不实施生产端修复**）。
- **来源**：客户反馈包（2026-09-23，反馈方＝美德双休）问题3 修复过程中的实证，非客户原报问题
- **已实证事实（初版）**：
  - 云端 `etf_minutes` 的 `close` 与 `amount/vol` 同基准（`close/(amount/vol)≈1` 行占比
    84.40% / 92.03% / 99.94%，三时段各 15~20 万行）⇒ close 是**可成交价口径**，
    不需要任何复权归一（该结论已固化为回归钉
    `tests/test_validator_behavior.py::test_unitchk_must_not_be_normalized_by_adjustment_factor`）；
  - 但码内 `adj_factor` 与 `close` 口径**并非全域自洽**：约 3.2%~5.1% 的行
    `amount/(close×vol)` 越界，其中 2026-01 前部分码达 `≈9×`；
  - 客户端还原链 `raw = qfq × adj_latest_global / adj_i`（`mcp_adapter._restore_to_raw`）
    在 84%~92% 的行上乘数为 1（不变），其余行乘数 ∈ [2, 6]，其正确性未能用本仓可用
    手段独立证明（缺外部基准行情源）。
- **P1 勘察后的事实修订（2026-09-23，决定性）**：
  - **三基准均可用**（含外部基准 tdx MCP **支持历史日期查询**）⇒ 无需「需外部基准源」兜底；
  - **核心定谳**：`etf_minutes` 与 `etf_daily` 同码同日 close 存在 **~10× 系统性口径分裂**
    （少子集 ~⅓）；两表 `amount/vol` 互不一致约 10×；全市场日表 `close/(amount/vol)`
    中位稳定在 **10.0005**（P10~P90 9.53~10.04，非噪声）；
  - **外部基准判定**：`etf_daily.close` ÷ tdx 前复权 ≈ **0.9950**（✅ 在真实价格水平）；
    `etf_minutes.close` ÷ tdx ≈ **0.3325**（❌ 偏离约 ⅓）；
  - **客户端定谳**：还原公式在「输入同基准」前提下**未被证否**；问题在**输入口径**
    ——故本项**根因在云端数据生产侧，客户端无修正手段**（改公式＝用未知口径拟合已知不一致数据）。
- **影响**：①被 UnitCheck 正确拦截的异常行会持续进隔离区（已由分表型门禁解除
  「批次判负」，见 commit `c62c1cc`）；②**分钟表 close 不可作为价格源**（与日表差 ~10×）；
  ③主库 raw 价格是否受影响需在上游答复后复核（当前无证据表明已污染）。
- **解除条件**：①上游答复 R1~R7（口径定义 / 成因 / 权威方 / 单位声明 / 修复承诺 / 临时指引）；
  ②口径确认后本地消费链复核报告（受影响表、代码区间、行数）；
  ③若修复：对拍收敛验证（`close/(amount/vol)` 与 tdx 比值双双 ≈1）。
- **推进归属**：本会话（客户运维会话 2）持有；**C 路径（对拍防线）并入 Trae 双层扫描器**
  （移交件 `docs/handoff/handoff-p1-baseline-to-trae-20260923.md`，经用户转发），本会话不单独建设。
- **B 路径受控记录（用户 2026-09-23 裁定：不单独实施）**：B＝「分钟表 close 不再作价格源」
  的客户端缓解。**不实施理由**：治标（掩盖云端口径不明）、零成交 bar 下 `amount/vol` 不可用、
  对复权/回测口径有连锁影响。**重启用条件**：**仅当 A 路径长期无响应**时作为受控缓解评估，
  且必须走六步流水线全文（含影响面与等价性验收）。

---

## ETF 因子/基础信息通道覆盖缺失（NO_ADJ 族，2026-09-24 登记 · 数据域新发现）

- **状态**：**REGISTERED（数据域）** —— 用户 2026-09-24 裁定④：转数据域新发现登记；
  列入**下次云端交互议题**或 Trae 后续批；**不并入分钟表「施加次数」主修单**（异族，见下）。
- **来源**：挂账定性批 V-4（`docs/evidence/backlog-v4-noadj-verdict-20260924.md`）；
  数据载体 `trading-battle-back/logs/qfq_triage_etf_minutes_20260924_171935.{json,codes.csv}`。
- **实测事实（决定性）**：
  - 含 `NO_ADJ` 的码 **167 只**、`NO_ADJ` code-day 合计 **4,441**；
  - 这 167 码在 `data/qfq_aux.db` 三张因子表**命中 0/167**：
    `adj_factor`（25,219,309 行 / 5,815 码）· `fund_adj`（11,930,536 行 / 2,277 码）·
    `adj_factor_snapshot`（5,780 码）；
  - **三表时间覆盖正常**（`adj_factor.time` 至 `1790006400000` ≈ 2026-09-26）⇒ 缺口是
    「**码不在表里**」而非「时间不够」；
  - **交叉验证**：同一 167 码在 `etf_basic`（1,606 码）**亦命中 0/167**，逐个查询为 `None`
    ⇒ **fundamentals 通道 ⊆ 行情通道** 的覆盖不一致（`etf_minutes` 有行情、基础信息与因子均缺）。
- **形态**：逐码缺口量**双峰** —— P50 = 3（零星，首末/停牌边界）与 TOP 群 ≈ 300+（**整码级**，
  ≈ 该码窗口内交易日数）⇒ 主体是整码级覆盖缺失，非零星日期缺漏。
- **与主修缺陷的异族判定**：主修（分钟表施加次数）= 因子**值**错误（可回填 corrected）；
  本族 = 因子**记录**缺失 ⇒ **不可用回填解决**（须先补因子/基础信息）。
- **待查**：①167 码缺席 `etf_basic` 的原因（新上市未同步？被基础信息源过滤？）；
  ②分钟表码池（519 只风险码池）与 `etf_basic`（1,606 只）的**口径差异**；
  ③缺口时效性（历史遗留 vs 仍在扩大）；④**下游数据可用性风险**——无因子的码其分钟 close
  无法评估是否需复权，下游消费其分钟数据的**价格口径无法保证**。
- **推进归属**：数据域（含 Trae 后续批）；本会话仅登记与移交（不移交实施）。

---

## daemon 守护可观测性三件（2026-10-19 登记 · 待空闲轮评估）

- **登记依据**：②审 2026-10-19 照准扩容（原两件 → 现**三件**）。三件**同族**：均属
  **「证据可观测性缺陷」**——即系统出问题时**看不到、或看到了但证据被销毁**，
  与 **QCAL-05 / DAT-17 静默语义陷阱族**同治（静默失败/静默丢证据必须转为可观测信号）。
- **状态**：**不立项、不排期、不动代码**；候空闲轮一并评估。

### W-1 写-写冲突可否重试（2026-10-17 发现）

- **现象**：2026-10-17 两个采集任务**在 commit 阶段整体失败**——
  - `mcp_etf_minutes` 17:17:47：`❌ FAILED[streaming]: TransactionContext Error: Failed to commit: write-write conflict on key: "159512…"`（path=`_write_passthrough`）
  - `mcp_stock_minutes` 18:14:52：同型，key `"0000…"`
- **关键读数**：两案**数据均已通过校验**（`passed=49992` / `passed=50000`），失败点在
  `daemon.py:1473 _stamp_and_write` 的 commit；且候选源**仅 `mcp` 一个** ⇒ **无重试兜底**，
  直接判任务失败 ⇒ **一轮分钟数据静默丢失**。
- **强指向（未证实）**：QFQ applying（小时级长事务）与 passthrough 采集**并发写同一张分钟表**。
  **并发写者身份未直接取证**（日志未记录另一事务）。
- **待评估**：①冲突可否重试（幂等 UPSERT 前提下重试是否安全）；②是否需 applying 与 ingest 互斥；
  ③若加取证，在冲突分支留痕「当前活跃写者/事务 id」。

#### W-1 复发率统计（2026-10-19 补 · 24h 窗口口径 · ②审旁登记小项）

**统计口径**：按 `daemon.log*` 逐日扫 `❌ FAILED[streaming]: … write-write conflict`（**只计失败任务行**，
不重复计同一次失败的伴随行）。**窗口**＝24h（以日志自然日切分）。**阈值**＝**≥3 次/周 ⇒ 触发 W-1 实施硬门槛**。

| 日期 | 同型失败数 |
|---|---|
| 2026-10-13 | 1 |
| 2026-10-14 | 1 |
| 2026-10-15 | 2 |
| 2026-10-16 | 4 |
| 2026-10-17 | 2 |
| 2026-10-18 | **0**（周日无采集轮，非「已修好」） |
| 2026-10-19 | 2 |
| **合计** | **12 次 / 7 天** |

⇒ **实测 ≈12 次/周，阈值 3 次/周 ⇒ 超阈值 4.0 倍 ⇒ W-1 实施硬门槛已触发。**

#### ★ W-1 的决定性规律：**每次撞的都是同一个 key**（本条的真正价值）

| 任务 | 恒定 key | 该 bar 时刻 |
|---|---|---|
| `mcp_etf_minutes` | `159512, 1789090200000, 1min` | **2026-09-11 09:30:00** |
| `mcp_stock_minutes` | `000055, 1788744600000, 1min` | **2026-09-07 09:30:00** |

**七天 12 次，两个 key 一次没变** ⇒ **不是随机争用，是确定性碰撞。**

**与水位缺陷的因果链接（关键）**：两个 key 的时刻**均 ≥ 各自表的陈旧水位**——
`etf_*` 水位 2026-09-04、`stock_minutes` 水位 2026-09-04 15:00（见本文档外 D7 件 §2.1）——
⇒ **因水位不推进，每轮增量都从 09-04 起重拉并重写同一段历史 bar** ⇒ **同一 key 被反复重写**
⇒ 与并发写者每次都撞在同一处。

⇒ **结论：这 12 次失败是「水位永不推进」的症状，而非独立缺陷。**
⇒ **优先级推论**：服务端 41×（⇒ cycle 可 finalize ⇒ 水位推进 ⇒ 重拉消失）**会同时消除本规律**；
⇒ **W-1 的「重试能力」仍值得具备**（任何冲突都会整轮丢分钟数据），但其**紧迫性应在水位/41× 修复后重估**——
**不得在水位缺陷未修时把 W-1 当成根因去独立实施**。

**⇒ 交叉引用（双向）**：本条已回链至服务端需求件
`docs/qfq-mcp-server-shard-layout-requirement-20261017.md` **§8 交叉引用（2026-10-19 追加）**——
该节以本条的复发率与「key 恒定」实证，支撑需求件 §5「二阶收益」的**可验证性**
（41× ⇒ cycle finalize ⇒ 水位推进 ⇒ 重拉消失 ⇒ 本规律同步消失）。**两侧互为证据，任一侧更新须同步另一侧。**

#### W-1 补录（2026-10-21 · 第 13 次 + 「水位推进 ⇒ key 出窗」自然实验验证）

**第 13 次复现**：2026-10-21 02:19:52 `mcp_etf_minutes`（batch `mcp_etf_minutes_mcp_20261021_013116_37ddc1`）
同型冲突，**key 仍为 `159512, 1789090200000, 1min`**（= 2026-09-11 09:30，自 10-13 起 13 次零漂移）。
累计：**13 次 / 9 天**（10-13→10-21，其中 10-18 周日无轮）。

**★ 自然实验验证（2026-10-21 · 本登记件预测的直接实证）**：
本轮 `etf_minutes` 水位**首次发生推进**——`last_watermark` 由 1788451200000（09-04 00:00）
推进到 **1789023600000（09-10 15:00）**（`daemon.log` 2026-10-21 01:31:28 实读）；
随之增量窗起点由 09-05 移到 **09-11**。而恒定冲突 key 的时刻恰为 **09-11 09:30**——
**恰好压在收缩后窗口的第一根 bar**。

⇒ **量化关系（②审 2026-10-21 照准入册）**：**水位再推进一个交易日，该 key 即被甩出窗外 ⇒ 冲突消失。**
⇒ 这正是本登记件 2026-10-19 定性（「水位不推进 ⇒ 反复重写同段历史 bar ⇒ 同 key 确定性碰撞」）的
**可证伪预测之直接验证**：水位推进多少，窗口就收缩多少，key 距出局就差多远。
⇒ 交叉引用侧（需求件 §8）据此获得一个**可机检的验收判据**：41× 兑现后水位应连续推进，
`159512 @ 09-11 09:30` 出窗即冲突清零——**两侧可同时核验**。

**第 14 次复现（2026-10-21 22:31:14）**：`mcp_etf_minutes` once 补跑（batch `mcp_etf_minutes_mcp_20261021_221755_016523`）同型冲突，key 仍 `159512, 1789090200000, 1min`（09-11 09:30），累计 **14 次/9 天**；失败点 `writers.py:1033`（**主 upsert 路径，该路径无 `record_write_failure`** ⇒ 失败槽空、`ctx_src=last` 误导——**W-4 空槽根因由此收窄**）；同轮 `mcp_stock_minutes` 亦 failed（74 任务 2 失败）。**六步立项**：`docs/minutes-write-conflict-design.md`（2026-10-22，②审有条件通过，M1-M4 已修订）。

**第 15/16 次复现（2026-10-22 凌晨 · 两表同日各一次 · ②审预判逐字命中）**：

| 次 | 时刻 | 任务 / 批次 | 恒定 key | 判定 |
|---|---|---|---|---|
| 15 | **00:23:25** | `mcp_etf_minutes`（`..._20261022_001402_70d784`） | `159512, 1789090200000, 1min`（09-11 09:30） | 同族，key 零漂移 |
| 16 | **01:07:23** | `mcp_stock_minutes`（`..._20261022_002326_87d0ca`） | `000055, 1788744600000, 1min`（09-07 09:30） | 同族，key 零漂移 |

- **判别特征仍同型**：两起均 `serial=False ctx_src=last ctx_table=sw_weight` ⇒ **W-4 的误导值仍在**（W-4 未修，符合预期）；
- **无兜底**：两起均 `所有候选源均失败: ['mcp']` ⇒ 整任务失败，**无重试**；
- **累计**：**16 次 / 10 天**（10-13 → 10-22）；
- **处置（②审 2026-10-22 口径照准）**：**不硬拉**——不重试、不提 A4 阈值、不停 `stock_minutes`；
  **归入既有六步立项** `docs/minutes-write-conflict-design.md`，**不单独立项**；与 `etf_minutes` 同归族处置。
- **附带证据（配置生效时序）**：`mcp_etf_minutes` 已于 2026-10-21 23:37 置 `enabled: false`，
  但本次 once（pid 14476，23:03:54 启动）**启动时已加载旧配置**故仍跑了一次（00:14:02 起）
  ⇒ **证明 config 在进程启动时加载**，停用对**下一次** daemon 启动生效。该次仍 `A4 熔断：39 个
  repair/full 窗口 > 阈值 20`（`a4_verdict=-1`）⇒ 修复未到时，只要它再跑就仍走 repair 长路径。

### W-2 09:00 载体 rc=1 失败可见性

- **登记来源**：②审 2026-10-19 并入（原小项）。
- **待评估**：09:00 载体以 `rc=1` 退出时，**失败是否被上游可见**（是否会像静默失败那样无人察觉）。
- **注**：本条细节本会话未取到，**按原令层级登记，不代拟**；评估时须先补齐现场（载体名 / rc 来源 / 现有告警面）。

### W-3 `daemon.log` 被 CLI `once` 运行覆盖（2026-10-19 发现 · 取证证据灭失实证）

- **现象**：`data/logs/daemon.log` 由 **6,044,080 字节降至 7,657 字节**，
  末次写入 **2026-10-18 22:58:56**；其内容为一次 **CLI `once` 模式运行**的输出
  （`[CLI] 单次执行模式 task=fin_indicator`，且该次因「目标库非主库」被拒）。
- **后果（本条的严重性所在）**：**daemon 的输出被整体覆盖**，且**不存在 `daemon.log.2026-10-18`
  轮转件** ⇒ 10-18 的 daemon 日志**全部丢失** ⇒ 当晚 daemon（PID 25480）的死亡
  **无法归因**（外杀 / 崩溃 / 正常退出**不可区分**）。
  ⇒ 直接后果：一次真实事故（daemon 隔夜死亡、次日盘中无守护）**因取证被销毁而无法定谳**。
- **待评估**：①`once` 运行与 daemon **按进程分离日志文件**（或改 append-only / 先轮转再写）；
  ②轮转件缺失的原因（为何 10-18 无 `.2026-10-18`）；③是否需「取证保底」——即任何写 `daemon.log`
  的路径都不得截断既有内容。
- **纪律关联**：与既有「**读数前先清旧输出**」「静默语义陷阱族」同源；
  本条是**「证据被销毁」**形态，比「看不到」更严重（**看不到可加日志，被销毁则不可追**）。

### W-4 写冲突归因字段在失败路径上不生效（2026-10-19 发现 · W-1 的归因能力缺陷）

- **现象（今日复现，签名与 10-12 首例同型）**：2026-10-19 11:32:16
  `mcp_etf_minutes` 冲突，判别特征为
  `path=_write_passthrough serial=False ctx_src=last ctx_table=sw_weight`。
- **关键读数**：**`ctx_src=last`** ⇒ 取的是**全局「最近写」槽**，而**非失败写自身**槽
  （`_LAST_WRITE_FAILURE` 为空）⇒ 归因**回退到已知误导路径**。
- **代码自身的定谳**：`writers.py:127-130` 注释原文——
  「§10.2 修正（2026-10-12 实战暴露：全局最近槽会与失败写错配）— **首例实战（10:18:43
  `stock_minutes` 冲突）显示 `ctx_table=sw_weight` / `serial=False` ——即「最近一次写」
  并非「失败的那次写」⇒ 该字段误导**」。
  ⇒ **今日签名与该首例完全同型**（同为分钟表冲突、`ctx_src=last`、`ctx_table=sw_weight`）。
- **修正为何未生效**：`record_write_failure` 全仓**仅 2 处调用**
  （`writers.py:1123` 在 `_write_passthrough` 的 except 内、`writers.py:1257` 在分片变体内）。
  两处**都已在写路径 except 中**，但实测失败槽**仍为空** ⇒ **本次失败未经过这两处 except**
  （失败发生在被 except 包裹的语句之外，或走的是第三条写入口，如 daemon 侧
  `_stamp_and_write` → 其它 writer 方法）。**具体未覆盖路径待定**（须读 `_stamp_and_write`
  调用链确认）。
- **后果**：**并发写者身份至今无法从日志指认**。W-1 的「强指向 QFQ applying」为**未证实假设**；
  本次一度据 `ctx_table=sw_weight` 推断写者为 `sw_weight`，**经查该字段是已知误导，已撤回**。
- **待评估**：①把 `record_write_failure` 补到**所有**写入口（含 daemon 侧 `_stamp_and_write`
  调用链）；②或在冲突分支直接记录**当时活跃事务/写者**（而非依赖「最近一次」槽）；
  ③归因字段若不可靠，**宁可输出 `ctx_src=unavailable` 也不得输出误导值**（照静默语义陷阱族：
  **误导比缺失更坏**）。
- **纪律关联**：本条与 W-3 同族（可观测性缺陷），且是**「看到了但看到的是错的」**形态——
  比 W-3 的「看不到」更隐蔽：**W-3 会让人知道证据没了，W-4 会让人拿错证据去定罪**。

### W-5 `stock_minutes` 流式读分片中途文件消失（2026-10-21 登记 · 定谳：非 01:0x 误删 · 竞态/TTL 候选继续）

- **现象**：2026-10-21 04:20:27 `mcp_stock_minutes`（batch `mcp_stock_minutes_mcp_20261021_021952_00d269`）
  `❌ FAILED[streaming]: [Errno 2] No such file or directory: data\mcp_landing\exp_stock_minutes_32433438\j_1792521489_stock_minutes_part_0xx`
  ——导出落盘分片在**读取途中消失**。任务 02:19:52 起，导出 24 批次流式读，失败于约 1h40m 处。
- **★ 定谳（②审 2026-10-21 令，按 job 创建时间戳裁定）**：
  - **裁定准则**：job `1792521489` 创建于 01:05 前 ⇒ 总调度 01:0x 清理误删实锤；其后 ⇒ 另因（竞态/TTL）。
  - **证据链**：①manifest 中该 job 条目**已被清**（`_export_cache_manifest.json` 现存无此记录——拿不到
    manifest 时间戳）；②**替代证据：j_id 即 epoch 秒**，且与日志行**同秒互证**——`daemon.log` 02:38:08
    `export 批次 1/24` → 02:38:09 `[MCP] export_dataset stock_minutes`，而 `j_1792521489` = epoch
    **2026-10-21 02:38:09 CST**（幂等可复算；幸存目录 `j_1792543265`≈08:41 创建/09:03 落盘亦吻合该关系）；
  - **⇒ 定谳结论：该 job 创建于 02:38:09，在 01:0x 删除之后 1.5 小时——01:0x 清理在物理上不可能删到它。
    非总调度方误删，按原候选（竞态/TTL）继续。**
  - **佐证二**：dir 与 manifest 条目**同时消失**（干净驱逐特征；裸 glob 删除会留孤儿条目）；
  - **佐证三**：01:0x–02:19 窗口内**无任何任务以 Errno-2 失败**（01:31 `etf_minutes` 失败是写冲突非文件丢失）。
- **候选机制（待查，根因未证实不动手）**：stock_minutes 单任务**连发 24 个导出批次**，长流式读 1h40m；
  **后续批次的落盘驱逐前批分片**（export cache 磁盘层容量/代际清理与跨批次流式读竞态）为首要候选。
- **已定防护**：**`data/mcp_landing/` 整目录已列清理禁区**（②审 2026-10-21 裁定；任何清理动作不得触及）。
- **待评估**：①export cache 磁盘层驱逐时机与活跃流式读的互斥；②24 批次任务的分片生命周期
  （是否应读完全部批次再驱逐）；③驱逐/清理动作**留痕**（谁删的、何时删的——当前完全不可观测，W-3/W-4 同族）。

### W-6 daemon 非正常终止两起 + **「为何未自动拉起」**（2026-10-21 登记 · ②审并入 watchdog 评估面）

> **编号注**：本条承接本件 W-5。②审 2026-10-21 令称「W-6 已登」——若该处 W-6 与本条为**同一事项**，
> 以②审侧编号为准、本条内容可整体并入；若为**不同事项**，本条顺延为 W-7。**不擅自改②审编号。**

**两起非正常终止**

| # | 时刻 | 形态 | 恢复 | 停摆 |
|---|---|---|---|---|
| ① | 2026-10-19 22:34 | **硬停**（既有案，②审点明；本件此前未记录） | 10-20 23:51:59 起 | 约 25 h |
| ② | 2026-10-21 14:47:53 | `qfq_orch 周期异常: Corrupt snappy compressed data` → `QFQ post-ingest 未完全通过 status=failed` → **日志止于此、进程死亡**（无 traceback 收尾） | 10-21 22:17:33 起 | **7.5 h** |

**★ 共同特征（本条评估价值所在）：两起均未自动拉起。**
⇒ 任何 daemon 崩溃都直接变成**静默停摆**，并与 W-1/W-3/W-4 的可观测性缺陷**叠加**：
死亡无声、死后 status 仍显示 running（W-3 同族）、日志被 CLI once 覆盖后连死因都留不下。

**「为何未自动拉起」纳入评估面（四条）**

1. **自动拉起通道缺位**：现有 watchdog（`watchdog_loop.py` / agent-quant watchdog）覆盖的是
   harness / loop 进程，**不含 `pipeline.daemon`**——2026-10-19、10-21 两起均实证；
2. **死亡可观测性**：daemon 死后**无告警**——10-21 这起靠运维人工巡检才发现，14:47→22:17 的
   7.5 h 内**零信号**；
3. **崩溃留痕不全**：14:47:53 案例日志止于 WARNING，**退出码/堆栈未落盘** ⇒ 无法区分
   「异常退出」与「被外部终止」（W-3/W-4 同族；本次因此**不能**再判「外杀」）；
4. **与 WAL 铁律耦合、放大停摆**：崩溃 ⇒ WAL 残留 ⇒ 需人工 `check_wal` 收敛 ⇒ 停摆窗口进一步拉长。
   10-21 实测链路：`14:47:53` 崩 → `quantstudio.db.wal` **420,180 B** → 人工备份为
   `quantstudio.db.wal.bak.20261021_221658` → 收敛至 **1,259 B**（DB mtime `22:17:06`）→ daemon `22:17:33` 起。

**★ 风险叠加（本次新发现，优先看）**：恢复后的新实例 **22:18:19 又在重放同一批 39 个
`repair/full` 窗口**（`etf_minutes` A4 熔断：39 > 阈值 20，`a4_verdict=-1` ⇒ 强制重拉）
⇒ **导致 14:47:53 崩溃的同一执行路径未被消除**。在无自动拉起的前提下，若再次崩在无人时段，
会重演一次 7.5 h 静默停摆。

**待评估**：①daemon 级自动拉起（supervisor / 计划任务 / 看护进程）——两起均证明现有覆盖不足；
②死亡告警通道（心跳超时 ⇒ 主动告警）；③崩溃退出码 + 末段堆栈**强制落盘**（区分异常退出／外部终止）；
④`etf_minutes` A4 熔断阈值与 39 个 repair 窗口积压的清理路径——不清则每次运行都重放同一长路径。

### W-7 `etf_minutes` 落盘分片损坏（2026-10-21 定位 · 4/13 坏片 · **否定截断假说**）

**现场**：崩溃点批次 job `1792565236`（= 2026-10-21 14:47:16，ckey `etf_minutes|2025-09-01|2025-09-02`），
落地目录 `data/mcp_landing/exp_etf_minutes_e13d3c1c`（13 片，mtime 恰为崩溃时刻 14:47:53）。

**★ 逐片只读校验结果（pyarrow 实读，触发列块解压）**：

| 分片 | 大小 | 判定 |
|---|---|---|
| `part_00002` / `00003` / `00005` | 797 / 794 / 811 KB | `OSError: Corrupt snappy compressed data` |
| `part_00004` | 791 KB | `Could not read schema`（**连 footer 都读不出，损坏更深**） |
| 其余 9 片 | 750–946 KB | **全可读，各 50,000 行 / 11 列** |

**★ 关键判据：坏片大小全部落在正常范围内（791–811 KB vs 好片 750–946 KB）⇒ 不是截断。**
⇒ **W-5 的「驱逐截断」假说在本案被否定**（截断必然显著变小）。坏片集中在**连续区间 00002–00005**，
且同一区间内两种损坏深度并存（解压坏／footer 坏）⇒ 更像「落盘写入未完成/写入中即被读」或服务端分段异常。

**★ 归因修正（推翻 2026-10-21 早前判断）**：
早前判为「读 DuckDB 时撞损坏压缩块」——**实据不支持**：崩溃发生在 **pyarrow 读刚落盘的 parquet 分片**路径上，
**`quantstudio.db` 本身未证损坏**。⇒ WAL 收敛（420,180 B → 1,259 B）是**按铁律应做且已做**的合规动作，
但**不是本次崩溃的因**。真实链条：**服务端导出 → 落盘 13 片 → 其中 4 片损坏 → 流式读 → pyarrow 解压失败 → 进程死亡**。

**★ 附带发现：manifest 孤儿条目（影响修复能否「粘住」）**：
`_export_cache_manifest.json` 中 ckey `etf_minutes|2025-09-01|2025-09-02` 的条目指向 job
`exp_etf_minutes_0702a84e` / 分片名 `j_1791546923_*`（2026-10-09 创建，**该目录已不存在**），
但其 `shard_sizes.part_00000 = 946,078` **恰好等于 10-21 那批的实际文件大小** ⇒ **条目内部不一致**
（名字是旧的、尺寸是新的）。好消息：日志实证 `export_cache 校验失败 ⇒ 回退直连`，
即校验确实拦住了该条目、未把坏片当有效缓存。⇒ **修复动作须含：清理该 ckey 的孤儿条目**，否则下次缓存行为不可预期。

**今晚止损（②审 2026-10-21 裁定「(c) 变体」已执行）**：
`config/profiles/mcp_only/collector_tasks.json` 中 `mcp_etf_minutes.enabled: true → false`
（**diff 仅 1 行**；JSON 解析校验 OK；`enabled=true` 76→75，只动此一处）。
**A4 阈值不动**——熔断是正确防御，问题在积压窗口与损坏分片，不在阈值。

**恢复条件（四项全达才恢复 `enabled: true`）**：
1. 损坏分片**隔离并备份**（移出 `exp_etf_minutes_e13d3c1c`，保留原件供取证）；
2. 清理 manifest 中该 ckey 的**孤儿/不一致条目**；
3. 从源**重导该窗口**（2025-09-01|09-02）；
4. 重导后**逐片只读校验 13/13 全 OK**（验收判据，任一片坏即不得恢复）。

**明晨修复序列（dev 执行）**：①定位＝**已完成**（本段即结果）；②隔离+备份；③清 manifest 条目；
④重导；⑤13/13 校验；⑥恢复 `enabled: true`；⑦另立案深挖落盘损坏机理（是否「边写边读」／服务端分段异常），
与 W-5 同族取证。

**风险提示**：损坏发生在**落盘环节**而非缓存层 ⇒ 只要同一批数据再导出一次仍可能再坏。
⇒ 重导后必须**真的逐片验过**再恢复，不能以「重导成功」代替「分片可读」。

### W-8 分钟捕获链停摆：`agent_quant_daily_sync` 实例挂死 + `IgnoreNew` + 执行时限失效（2026-10-22 登记 · QuestDB 断档真因）

**背景**：QuestDB `etf_minutes`/`stock_minutes` 在 **2026-10-21 交易日整日缺失**（云端与**本地 QuestDB** 皆无）。
经查**不是**云推送链问题（推送链 10-22 03:10 已正常补齐 10-20），也**不是** etf 分支专属——**两张分钟表同日一起缺**。

**任务配置实测**（`schtasks /Query /TN agent_quant_daily_sync /XML`）：

| 项 | 值 |
|---|---|
| 触发 | `StartBoundary 2026-09-28T21:00:00+08:00`（每日 21:00） |
| **`MultipleInstancesPolicy`** | **`IgnoreNew`** |
| **`ExecutionTimeLimit`** | **`PT3H`（3 小时）** |
| 脚本 | `agent_quant\run_daily_sync_task.ps1` → `get_xtquant_stock_data.py --periods 1d` |
| Last Run / Result | **2026-10-21 21:00:01 / Result=1（失败）** |
| Next Run | 2026-10-22 21:00（`Status=Ready`） |

**三项叠加成因**：
1. **实例挂死**：pid 37016 于 **2026-10-18 21:00:01** 启动，卡在 `xt_kline_security_1d batch=71 下载K线`，
   **挂 77 h 36 m**（batch=71 重复进度日志 **9,309 条**、3.2 天仅 **193 s CPU**、内存 6 MB、线程 114 ⇒ 活锁形态）；
2. **`IgnoreNew` 放大**：旧实例不退出 ⇒ 10-19 / 10-20 两日 21:00 的**新实例按策略被跳过**
   （两日分钟数据靠旧实例残留推进才拿到）；
3. **`ExecutionTimeLimit=PT3H` 未生效**：若 3 小时时限生效，该实例 10-18 24:00 前后就该被杀。
   **它活了 77 小时 ⇒ 时限失效**（机理待查：principal 类型 / "Run only when user is logged on" /
   Settings 组合均可致此）。

**⇒ 10-21 缺失的直接原因**：该日 21:00 那轮 **`Last Result=1`（失败）** ⇒ 分钟捕获未产出。

**处置（②审 2026-10-22 授权）**：pid 37016 已 **kill**（BEFORE：start 10-18 21:00:01 / CPU 193 s / WS 6 MB / threads 114 / 子进程 0；
AFTER：已终止、无残留）⇒ 今日 21:00 可正常新起。

**待评估**：①`ExecutionTimeLimit` 为何失效（须读 principal 与 Settings 全文）；②`IgnoreNew` 是否应改
`Queue`/`Parallel`，或加「陈旧实例自动收割」；③挂死点 `batch=71` 的根因（xtdata 回调不返回？
网络半开？）；④分钟捕获失败（Result=1）应**主动告警**而非静默（与 W-3/W-4 同族可观测性缺陷）。

### W-9 源侧时区口径瑕疵：CST 墙钟标 Z（2026-10-22 登记 · ②审令入 W 族）

- **现象**：本地 QuestDB REST `/exec` 返回的时间值带 **`Z` 后缀**，但数值为 **CST（UTC+8）墙钟**。
  例：`etf_minutes` 最末一条为 `2026-10-20T15:00:00.000000Z`，实为**北京时间 10-20 15:00 收盘**，
  若按字面当 UTC 解读则相当于北京时间 10-20 23:00 —— **偏 8 小时**。
- **影响**：跨时区消费方（含本会话早前的覆盖度读数）可能把「收盘时刻」误读为「深夜时刻」，
  进而误判「当日数据缺失/存在」。**本次断档排查中即产生过一次误导性读数。**
- **定性**：**源侧口径瑕疵**（写入或 REST 输出层未区分「本地墙钟」与「UTC 瞬时」），
  **非消费方错误**。按①「数据修正型」同族管理：须显式声明影响面后再动，且**不得只改一端**。
- **待评估**：①确定是**写入层**（ILP 时间戳按本地墙钟写入）还是**输出层**（REST 序列化加 Z）；
  ②统一口径（推荐真 UTC 存储 + 消费侧按需转 CST）；③登记受影响消费方清单（同步链 / 巡检 / 本会话读数习惯）。

### W-10 编码坑族：PowerShell 5.1 `Get-Content -Raw` + `WriteAllText(UTF8)` 毁 UTF-8 中文文件（2026-10-22 登记 · ②审令入 W 族）

**事故**：2026-10-22 改 `config/profiles/mcp_only/collector_tasks.json` 的 `enabled` 一行（`false`→`true`），
用 `Get-Content -Raw` 读 + `[IO.File]::WriteAllText(..., UTF8)` 写 ⇒ **全文件 91 行中文 `_note` 全变乱码**
（`类别A` → `绫诲埆A`）、**JSON 变非法**（`Invalid control character at line 2 column 118`）。

**后果严重性**：该文件是采集任务清单。**daemon 下次重启会在解析阶段抛错 ⇒ 74 个任务全线停摆。**
本次幸在 daemon 未重启前修复；**若修复窗口错过，即重演一次全量停摆**（与 W-6 同族的停机风险）。

**机理**：PS 5.1 的 `Get-Content`（含 `-Raw`）按**系统 ANSI 代码页**（简中机器＝GBK/936）解码字节流 ⇒
UTF-8 多字节中文被错误解码；再用 UTF-8 编码写回，则把错误解码结果**持久化**。

**正确做法（三选一）**：
1. **首选 Python**：`open(p, encoding="utf-8")` 读、`open(p,"w",encoding="utf-8",newline="")` 写；
2. PS 7+（默认 UTF-8）；
3. PS 5.1 显式 .NET：`[IO.File]::ReadAllText($p,[Text.Encoding]::UTF8)` +
   `WriteAllText($p,$s,(New-Object Text.UTF8Encoding($false)))`。

**强制三验（改生产配置文件后必做，缺一即不得收工）**：
① **语法可解析**（JSON/YAML 实际 `load` 一遍）——本次即由此发现，若只验 `enabled` 值就会漏；
② **中文未损**（断言原有中文串仍在、乱码串不存在）；
③ **diff 仅预期行数**（本次最终为 1 行）。

**配套纪律**：写坏后**立即 `git checkout --` 还原**再换正确方法重做，**禁止手工修补乱码**。

**同族登记**：与 H-1（commit message BOM，`Set-Content -Encoding UTF8` 在 PS 5.1 必写 BOM）同源——
**PowerShell 5.1 的默认编码是隐形杀手**，读写两个方向都会伤人（H-1 是写、W-10 是读）。

### W-7 完成记录（2026-10-22 · 六步全通）

| 步 | 结果 |
|---|---|
| ② 隔离+备份 | 13 片 / 10,525,521 B → `data/mcp_landing_quarantine/exp_etf_minutes_e13d3c1c_20261022`（Move 不删，含逐文件清单与回退命令） |
| ③ 清 manifest 孤儿 | `etf_minutes` 648→647，删 `job 0702a84e`（rows 39283 / ts 2026-10-09T19:55:39）；仅缩 1,287 B；10 秒持久性验证通过 |
| ④ 从源重导 | daemon repair replay 自然重导（未由本会话直连执行：直连跳过因子快照构建，被 QFQ 还原拒绝——判断正确，未制造写冲突） |
| ⑤ **13/13 硬门** | **PASS**。新导出 `job exp_etf_minutes_6bb6a4f6`（ts 2026-10-22T09:45:04），13 片全可读、0 损坏，**总行数 612,863 ＝ 10-21 崩溃前日志记录的 total_rows 逐位相同**；原坏片位（00002–00005）已换新文件 |
| ⑥ 恢复 enabled | `enabled: false → true`（diff 仅 1 行；JSON 解析 OK；中文完好）。写入段仍可能败于 W-1 已知冲突——由 minute 修复窗解决 |
| ⑦ 机理深挖 | **未开始**——落盘损坏根因（边写边读／服务端分段异常）另立案，与 W-5 分族取证 |





---

## 工具链卫生项（2026-10-19 登记 · 本线专属 · 不改历史）

### H-1 commit message 首字符带 UTF-8 BOM

- **现象**：本线 2026-10-19 前的 **8 笔**提交（`28fc9c9`…`f6f6dbe`）commit message **首字符带 UTF-8 BOM**
  （`﻿docs(...)`）。**实测（原始 commit 对象直读首字节，非日志显示）**：
  - 本线 8 笔：**BOM = 8/8**
  - 同区间他线 5 笔（`a7172f9`…`1ee8e8b`）：**BOM = 0/5**
  ⇒ **BOM 专属本线**，非仓内普遍现象。
- **根因（已定位）**：本线在 **Windows PowerShell 5.1.26100.9444** 下用
  `Set-Content -Encoding UTF8` 写 commit message 临时文件，再 `git commit -F`。
  **PS 5.1 的 `-Encoding UTF8` 必写 BOM**（PS 7 才有 `utf8NoBOM`）。
  他线走的路径无 BOM。
- **后果**：**仅表述层**——不影响提交内容、树对象、diff；BOM 落在 message 首字符，
  部分工具链的 `git log --oneline` 会将其显示为不可见前缀。
- **处置（②审 2026-10-19 裁定）**：**不改历史、不重写已推提交**；
  **下次提交前消除**——本线改用
  `[System.IO.File]::WriteAllText($p, $msg, (New-Object System.Text.UTF8Encoding($false)))`
  写 commit message（**不写 BOM**），再 `git commit -F $p`。
- **自证**：本登记所在的提交（**BOM-free 样本**）即以该方式提交，可据
  `git cat-file commit <sha>` 首字节复核（应为 `d` 而非 `EF BB BF`）。

