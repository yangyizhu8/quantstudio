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
