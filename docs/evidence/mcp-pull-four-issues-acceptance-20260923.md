# 客户反馈包（2026-09-23）四问题修复 · 验收证据

- **日期**：2026-09-23
- **方案**：`docs/mcp-pull-four-issues-fix-design.md`（已过审，用户 2026-09-23 裁定）
- **回退点**：`9c2e0f7a857b32c9f4e9bcf328b99c7312d22a6c`（`baseline-four-issues-20260923`，已 `git stash store`）
- **提交**：`478ed0e`(T1) / `6ceeff9`(T2) / ~~`996ced2`(T3，已回退)~~ / `151e984`+`0da4d79`(T4) / `4d470e5`(T3 回退)

---

## 一、验收结论总览

| 问题 | 任务 | 结论 | 关键证据 |
|---|---|---|---|
| 1 stock_minutes 拒单 | T1 | **通过** | 云端实测 async 绕过 60s 预算（8 天窗 5M 行 ready）；窗口公式单测 6 例；产物覆盖面核验无空洞/无重叠 |
| 2 stock_float_share OOM | T2 | **通过** | 路由三断言 + 流式 vs 直连 `assert_frame_equal` 逐值等价；批窗口核验 15 批 / 70 天 |
| 3 etf_minutes 判负 | T3 | **回退**（验收反证） | 修复方向被证伪：归一后净增拒 6,763 行 > 消除 3,427 行；根因修正见 §四 |
| 4 pyarrow 未声明 | T4 | **通过** | pyproject 双声明回归钉；缺依赖显式 ImportError（含安装指引）；列不存在仍返回 False |
| （用户追加）隔离区防护复验 | — | **通过** | 顶格→归档腾退→写入恢复 全链路 PASS，归档不丢数据 |

**套件终验**：10 个相关套件合计 **119 例 → 118 passed / 1 failed**；唯一 failed 为
**既有红** `tests/test_pit_filter.py::test_validator_is_single_chokepoint`
（断言 `daemon.py` 中 `writer.write` 仅 1 处，基线 `b50fe43` 上同为 4 处，与本批无关）。

---

## 二、T1 分钟表分批窗口 + async_mode

### 云端实测（只读，本批依据）

| 项 | 结果 |
|---|---|
| 同步 2 天窗 | 2,514,353 行 ✅ |
| 同步 8 天窗 | ✅（被静默截到 5,000,000 行） |
| 同步 50 天窗 | ✅（被静默截到 5,000,000 行） |
| **async_mode=true** | 立即返回 `{"job_id":…,"status":"running"}`；45s 后 `status=ready`，100 分片 SHA 完整 |
| 行数硬上限 | 请求 `row_limit=50,000,000` 仍返回 **5,000,000**（服务端硬上限） |

### 窗口公式核验（`worktable/_verify_t1_equivalence.py`）

```
stock_minutes      2026-01-01~2026-09-22: 批=133  窗口=  1d  批估算≈1.98M 含余量≈2.37M  覆盖=OK
etf_minutes        2026-01-01~2026-09-22: 批=133  窗口=  1d  批估算≈0.49M 含余量≈0.59M  覆盖=OK
stock_minutes      2025-01-01~2026-09-22: 批=315  窗口=  1d  批估算≈1.98M 含余量≈2.37M  覆盖=OK
stock_float_share  2024-01-01~2026-09-23: 批= 15  窗口= 70d  批估算≈4.12M 含余量≈4.94M  覆盖=OK
stock_daily        2025-01-01~2026-09-07: 批=  2  窗口=365d（未声明表，沿用既有窗口）
衔接核验：5/5 组 全批首尾严格衔接、无空洞、无重叠
小表单批路径：不受影响
回退护栏 cfg=0：最大跨度=2d（旧行为可恢复）
```

### 单测（`tests/test_f_series_export_fix.py` 19 passed，新增 6 例）

- `test_minute_window_time_budget_default_is_one_day`：默认 1 天窗（stock/etf 双表）
- `test_minute_window_target_rows_budget_math`：目标行数反算（stock=1 天 / etf=4 天）
- `test_minute_window_config_can_restore_old_behaviour`：cfg=0 → 2 天（回退护栏）
- `test_daily_table_window_not_affected_by_minute_budget`：日线不受影响
- `test_create_export_job_async_mode_passthrough` / `test_create_export_job_sync_mode_no_async_key`
- `test_get_manifest_await_ready_polls_until_ready` / `..._timeout_raises` / `..._without_await_ready_still_raises`

---

## 三、T2 stock_float_share 流式化

### 云端实测

- 云端 `stock_daily_basic` 全史 **14,321,322 行 / 5,919 码**（2010-01-04 起）
- 近窗密度 ≈3,869 行/交易日 ⇒ 70 天窗 ≈27 万行、365 天窗 ≈430 万行（均未触 5M 上限）

### 回归钉（`tests/test_mcp_streaming.py` 新增 3 例）

- `test_stock_float_share_routed_to_export_and_streaming`：三断言
  （在 `_EXPORT_TABLES` ∧ 在 `_STREAMING_TABLES` ∧ 不在 `_QFQ_ADJFACTOR_TABLES`）
  + 与同源表 `stock_daily_valuation` 口径一致
- `test_stock_float_share_streaming_vs_direct_value_equivalence`：
  `assert_frame_equal` 逐值等价（流式 concat vs 直连），`fetch_mode` = `export_streaming` / `export`
- `test_stock_float_share_row_estimate_registered`：行数估算已登记

### 作用域收窄（零衰减证明）

`_ROW_LIMIT_BUDGET_TABLES = {"stock_float_share"}` 仅声明表生效；
未声明表（stock_daily）批次数**逐位不变**（2 批 / 365 天，与基线一致）——
实测核验见 §二窗口核验输出。

---

## 四、T3 回退与问题 3 根因修正（验收反证）

### 反证数据（云端只读，三项独立取样，各 15~20 万行）

| 判据 | 2025-06 窗 | 2026-01 窗 | 2026-09 窗 |
|---|---|---|---|
| `close/(amount/vol) ≈ 1` 行占比 | 84.40% | 92.03% | **99.94%** |
| 旧行为拒绝率 | 3.1738% | 3.3273% | 0.0000% |
| **归一后拒绝率** | **4.9760%** | **3.6890%** | 0.0000% |
| A 类：归一消除的拒绝 | 1,276 | 2,151 | 0 |
| D 类：归一**新增**的拒绝 | 3,874 | 2,889 | 0 |

→ A 合计 3,427 < D 合计 6,763 ⇒ **净增拒**，修复方向证伪。

### 单码日内实证（`159388.SZ` 2025-06-03）

```
close / (amount/vol) 分位：{1%: 0.39902, 50%: 0.39979, 99%: 0.40067}
0.3998 = adj_i/adj_latest = 1/2.5011
```

⇒ 云端 close 是**已复权到最新锚的可成交价**，不是需要再乘倍数的 qfq。

### 数据画像

`amount/(close×vol)` 的 99% 分位：2025-06 = **9.0046** / 2026-01 = **9.0028** /
2026-09 = **1.0021** ⇒ 2026-01 前部分码存在 `amount ≈ 9×close×vol` 的真实数据异常
（倍率不可由「手/股 ×100」「千元/元 ×1000」解释）。

### 结论与保留物

- **根因修正**：UnitCheck 在 etf_minutes 上的拦截**绝大部分是真阳性**（数据/因子自身
  矛盾），不是判据与还原口径冲突导致的系统性误拒；
- 真正的框架层问题是门禁阈值 `max_reject_rate_mcp=1%` 系**日线口径校准**，对分钟表偏严
  （与 `docs/handover/pre-handover-test-report.md:50`、`docs/pipeline-tech-debt.md:93` 一致）；
- **保留物**：`tests/test_validator_behavior.py` 新增 2 例，用云端真值锁定证伪结论
  （`159327.SZ` 2026-01-05 10:03：close=0.214 / vol=550300 / amount=1056648.8 /
  adj_factor=1.0 ⇒ base ratio≈8.97、归一后≈26.9，两者皆越界），防止后人重跑同一错误路径。

### 回退事故与补救（如实记录）

回退 T3 时执行 `git checkout 6ceeff9 -- mcp_adapter.py`，而 T3 与 T4 **都改动过该文件**
⇒ T4 在 `mcp_adapter.py` 中的改动被一并回退（`pyproject.toml` 与测试文件不受影响）。
**发现方式**：回归测试 `test_parquet_has_column_missing_pyarrow_raises_explicit_error`
失败（DID NOT RAISE），非人工复查发现。已重做并逐项复核 T1/T2/T4 在 adapter 中的
全部改动点（`0da4d79`）。

---

## 五、T4 pyarrow 声明 + 消除静默降级

### 回归钉（`tests/test_mcp_fetch_routing.py` 14 passed，新增 3 例）

- `test_parquet_has_column_missing_pyarrow_raises_explicit_error`：
  清 `sys.modules` 缓存 + monkeypatch `__import__` ⇒ 断言抛 `ImportError` 且消息含
  `pyarrow` 与 `pip install` 指引（不再伪装成「列不存在」）
- `test_parquet_has_column_returns_false_for_missing_column`：业务兜底逐位不变
- `test_pyproject_declares_pyarrow`：`dependencies` 与 `[all]` 双断言含 `pyarrow`

---

## 六、用户追加验收项：隔离区防护能力复验

`worktable/_verify_quarantine.py`（临时隔离库，不触碰生产 `data/quarantine.db`）：

```
[顶格] 写入序列 = 50, 50, 0   （第三批因顶格被拒 —— 模拟客户 500,809/500K 现象）
[腾退] archive_expired(0) 归档 100 行；状态分布 = {'archived': 100}
[恢复] 腾退后再次写入 = 30     （防护能力恢复）
[不丢数据] 全表行数 = 130；payload 长度 = 30
PASS
```

⇒ 归档只转 `status=archived`（不删除、payload 保留），腾退后写入路径即恢复。

---

## 七、待用户裁定事项

**问题 3 实现层不修（T3 已回退），根因修正后需裁定处置方向（三选一）**：

1. **门禁分表型校准**：分钟表独立阈值（如 6%），日线保持 1%——不改变任何行的判拒结果，
   只改变「批次是否判负」；与既有技术债登记（`pipeline-tech-debt.md:93`）一致；
2. **判负语义调整**：超限时照常入库合格行并推进水位，仅告警 + 登记异常清单
   （属行为变更，需评估下游口径影响，走框架流程）；
3. **云端数据侧治理**：定位 `amount ≈ 9×close×vol` 的码与日期区间并交云端修复
   （数据侧、跨团队，非本仓可闭环）。

另：T1/T2/T4 已具备推送条件，等待用户确认（六步流水线步骤 5）。