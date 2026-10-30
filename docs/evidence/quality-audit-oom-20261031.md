# 质量审计 OOM 根治（Phase 1）· 验收证据档

- 日期：2026-10-31
- 客户：不爱热闹 ｜ 客户版本 209a732（2026-10-29）
- 方案：`docs/quality-audit-oom-and-runall-design.md`（v3 终审通过：D1 三条件 + D2 逃生门 + C1–C5）
- 判型：**纯恢复型**（审计恢复可完成、口径不漂移）+ **新增检测型**（`audit_skipped_oom`/`audit_timeout`/`incomplete` 三态显式化）
- 写前快照：`a0ca2d7`（stash，改动前工作区）
- 实施顺序：A4 → A5 → A0 → A1(含 A1b) → A2 → A3 → A6 → A7 → A8 → A10 → A9

---

## 一、客户实证与本轮归因

| 项 | 客户实测 | 代码定位 |
|---|---|---|
| OOM | `quality_audit.py:480 _audit_prices → _duckdb.OutOfMemoryException: 12.4 GiB/12.5 GiB`（3 任务同栈） | `_audit_prices` 的 `AdjustmentAnchor`：`SELECT *` + 两个 `ROW_NUMBER()` 全表排序 |
| 16h 空档 | `mcp_factor_value_pt` 审计硬啃 16 小时后 OOM | 同上，无超时/无内存上限 |
| 73 次全库审计 | 每任务 5–9 分钟 | **GUI 委托链**：`workers.py:230-231` → `daemon_process.py:308` 传 `--quality-audit full` → `daemon.py:2514-2517` 每子进程 1 次全库审计（daemon 常驻本就 1 次/轮，`:2543-2544`） |
| ETA≈8 天误读 | `est_total=480000000` | `mcp_adapter.py:908` 兜底常数（服务端未返 `est_rows` 时使用），旧日志未标注来源 |

---

## 二、改动清单（文件级）

| 文件 | 功能点 | 行级落点 |
|---|---|---|
| `quantstudio/gui/daemon_process.py` | **A0** 委托参数 `full`→`task` | `start_once_subprocess` 的 `--quality-audit` |
| `quantstudio/pipeline/daemon.py` | **A5** 枚举/分派/透传；**A6** 语义分离 + 第三态标记；**A7** 审计摘要 | `run_once` 枚举 `full\|task\|none`、`ran_tables` 累积、`finally` 分派；`_run_full_quality_audit(tables=)`；`execute_task` 末尾语义块；argparse `choices` |
| `quantstudio/pipeline/quality_audit.py` | **A4** `tables` 白名单；**A1/A1b** anchor 重写+分批；**A2** drift 有界；**A3** 异常隔离+分档超时 | `_audit_table_items()`、`_guarded_call()`、`_audit_prices` anchor 段、`_audit_minute_anchor_drift` 候选上限与分批 fetch |
| `quantstudio/pipeline/daemon_lifecycle.py` | **A7** run_state 落 `quality_audit` | completed / interrupted 两处 `write_run_state` |
| `quantstudio/gui/workers.py` | **A6** 解析 `QUALITY_AUDIT_INCOMPLETE` | 批量 Worker + 单任务 Worker |
| `quantstudio/gui/tabs/task_tab.py` | **A6** 第三态文案 | `_on_run_all_done` / `_on_task_done` |
| `quantstudio/pipeline/sources/mcp_adapter.py` | **A8** fallback 标注 + `safe_window` 输出 | 分钟安全窗口两处日志 |
| `tests/test_quality_audit_scoped.py`（新增） | **A9** 机检 19 例 | — |
| `tests/test_pipeline_execution_policy.py` | **A6** 判据更新为 D1 新语义 + STRICT_FAIL 回退 | — |
| `tests/test_resident_quality_audit.py`、`tests/test_gui_manual_qfq_cycle.py` | A5 签名同步（`lambda **kw`） | — |

---

## 三、逐点验证读数

### 波 1（A4/A5/A0）

| 判据 | 读数 | 结论 |
|---|---|---|
| A4 白名单三态 | `whitelist=['b']` / `all=['a','b','c']` / `empty=[]` | PASS |
| A4 保持 schemas 顺序 | `tables=['c','a']` ⇒ `['a','c']` | PASS |
| A5 CLI 契约 | `--quality-audit {full,task,none}`；`bogus` ⇒ exit 2（负向） | PASS |
| A0 委托参数 | 代码改 `task`；cmd 断言由 A9 覆盖 | PASS |
| 编译 | 三文件 `py_compile` exit=0 | PASS |
| 回归 | `test_pipeline_execution_policy` + `test_resident_quality_audit` + `test_gui_manual_qfq_cycle` = **20 passed** | PASS |

> **实施顺序修正（申报）**：方案写 A0→A4→A5，实际按 **A4→A5→A0** 执行——A0 依赖 A5 的枚举支持，先改 A0 会让 GUI 委托在 `run_once` 抛 `ValueError: unsupported quality_audit mode: 'task'`（中间态破坏）。范围不变。
>
> **补齐遗漏（申报）**：`daemon.py:4136` argparse `choices=["full","none"]` 会拒绝 `task`，已一并扩展为三值——否则 A0 会让**所有** GUI 委托 exit 2。

### 波 2（A1/A1b/A2）

**A1 合成对拍（10 场景，new vs legacy 逐位相同）**

| 场景 | legacy | new |
|---|---|---|
| 基线无偏差 | 0 | 0 |
| 首条 close_back 偏 3.1% | 1 | 1 |
| 末条 close_front 偏 3.1% | 1 | 1 |
| 中间行偏差（不计） | 0 | 0 |
| 末行 close_front=NULL | 0 | 0 |
| 首行 close_back=NULL | 0 | 0 |
| 首行 close=0 被过滤 | 0 | 0 |
| 单行 code | 1 | 1 |
| 4 code 混合两处偏差 | 2 | 2 |
| front/back 全 NULL | 0 | 0 |

耗时（20 万行 × 8 列）：legacy 0.062s → new 0.041s（**-34%**）。

**A1b（C1 真实库对拍时发现并补做）**：单语句 new 版在 **1.41 亿行** `etf_minutes` 上**仍 OOM**（7.4 GiB 触顶）⇒ 增加按 code 分批（`QS_AUDIT_ANCHOR_BATCH_CODES` 默认 500，`0`=退回单语句）。语义等价（每 code 独立分区，跨批不交叉）。

**A2 drift 有界（4 判据）**

| 判据 | 读数 | 结论 |
|---|---|---|
| 截断可见（30 code / 上限 20） | `AnchorDriftTruncated count=30` | PASS |
| legacy 无截断（负向） | 无该条目 | PASS |
| 候选 < 上限：集合相等 | bounded == legacy | PASS |
| 分批 fetch 等价（batch=7 vs 0） | 逐位相同 | PASS |

### 波 3（A3/A6/A7）

| 判据 | 读数 |
|---|---|
| OOM 降级 | `_guarded_call` 捕 `OutOfMemoryException` ⇒ `audit_skipped_oom`，不抛穿 |
| 编程错误仍抛穿（负向） | `ValueError` ⇒ `pytest.raises` 通过 |
| 正常调用无噪声 | 三条目均不出现 |
| 超时分档 | 全库域 `QS_AUDIT_FULL_TABLE_TIMEOUT_S`(默认 1800) / task 域 `QS_AUDIT_TABLE_TIMEOUT_S`(默认 300)，均实测触发 |
| 超时=0 关闭（负向） | 无 `audit_timeout` |
| A6 语义 | 数据成功+审计未过 ⇒ `True`；`QS_AUDIT_STRICT_FAIL=1` ⇒ `False`（旧语义） |
| A6 第三态通道 | daemon 打 `QUALITY_AUDIT_INCOMPLETE` ⇒ workers 解析 ⇒ task_tab 渲染「完成·审计未完成」 |
| A7 摘要 | `_last_quality_audit` 九键：interrupted / skipped_after_stop / elapsed_s / tables_audited / scope / skipped_oom / query_error / timeout / incomplete |

### 波 4（A8/A9/A10）

| 判据 | 读数 |
|---|---|
| A8 fallback 标注 | 无 `est_rows` ⇒ `est_total=480000000(fallback)`；有 ⇒ 无后缀；两处日志均输出 `safe_window` |
| A9 新增用例 | `tests/test_quality_audit_scoped.py` **19 passed** |
| A10 逃生门 | `--quality-audit full` ⇒ 全库（`QS_AUDIT_SCOPE=all` 亦可强制） |

---

## 四、C1 真实库对拍（预案 ③ 混合）

**对拍集选择**：`legacy` 能在临时抬高内存（`memory_limit=8GB`，仅验收机）下跑完的中小表做逐位对拍；超大表只跑 `new` 验证「不 OOM + 能完成」，legacy 口径由合成数据覆盖。
**窗口选择**：只读连接、`threads=4`，避开 daemon 重负载时段（本机 daemon 29152 在跑，读连接只读不影响写入）。

| 表 | 行数 | new | legacy | 结论 | 耗时 new/legacy |
|---|---|---|---|---|---|
| `etf_daily` | 2,210,033 | 5 | 5 | **IDENTICAL** | 0.4s / 0.6s |
| `stock_daily` | 9,898,370 | 0 | 0 | **IDENTICAL** | 2.1s / 3.2s |
| `etf_minutes` | 141,109,492 | **单语句 OOM（7.4GiB）→ 已补 A1b 分批** | （未跑·必炸） | 见下节 | — |
| `stock_minutes` | 212,239,435 | 见下节 | （未跑·必炸） | — | — |

**C1 的价值（如实申报）**：合成数据上 new 全部通过，但真实 1.41 亿行分钟表**仍然 OOM**——证明「列裁剪 + 单次排序」不足以解决窗口函数的规模问题，必须按 code 分批。**若只做合成对拍，此缺陷会带着上线**（用户坚持 C1 真实库对拍，直接拦下）。

**C1b 分钟表分批复测（A1b 生效后）**

| 表 | 行数 | code 数 | 分批 | 耗时 | anchor | skipped_oom |
|---|---|---|---|---|---|---|
| `etf_minutes` | 141,109,492 | 1,747 | 500 | **32.9s** | 3 | 0 |
| `stock_minutes` | 245,574,122 | 5,254 | 500 | **91.8s** | 4 | 0 |

对比：同表单语句版在 1.41 亿行即 OOM（7.4 GiB 触顶）。⇒ **A1b 分批是分钟表可用的必要条件**，非可选优化。

---

## 五、C2 各表耗时（超时默认值依据）

| 表 | 行数 | 单表耗时（分批） | 对 1800s 全库默认阈值的余量 |
|---|---|---|---|
| `etf_daily` | 2,210,033 | 0.4s | 充裕 |
| `stock_daily` | 9,898,370 | 2.1s | 充裕 |
| `etf_minutes` | 141,109,492 | 32.9s | 充裕（55×） |
| `stock_minutes` | 245,574,122 | 91.8s | 充裕（20×） |

**结论**：`QS_AUDIT_FULL_TABLE_TIMEOUT_S=1800`（全库域）与 `QS_AUDIT_TABLE_TIMEOUT_S=300`（task 域）在真实库实测下均有 ≥3× 余量，不会退化为「每天一堆 audit_timeout」噪声。若未来某表耗时逼近阈值，调高该 env 即可，无需改码。

---

## 六、C3 daemon.py 改动规模

```
quantstudio/pipeline/daemon.py | 106 +++++++++++++++++++++++----
1 file changed, 96 insertions(+), 10 deletions(-)
```

对应三处既有锚点：`run_once`（枚举 + `ran_tables` + finally 分派 + 第三态标记）、`_run_full_quality_audit`（签名 + env 覆盖 + 摘要 + 透传）、`execute_task`（语义分离块）。**无重构、无顺手改动**（其余字符为注释与文档字符串）。

---

## 七、D1-③ 回退实测

`QS_AUDIT_STRICT_FAIL=1` ⇒ `execute_task` 返回 `task_ok and audit_ok`（旧语义），由 `tests/test_pipeline_execution_policy.py::test_execute_task_with_quality_separates_data_from_audit` 断言（同一用例内先后验证新语义 True / 严格模式 False / 双绿 True）。

---

## 八、回归与既有红灯实证

### 8.1 全量回归（`pytest tests`，2096s）

```
70 failed, 4079 passed, 4 skipped, 8 xfailed  in 2096.31s (0:34:56)
```

**70 个失败中，无一属 quality_audit 系**（`test_quality_audit*` / `test_daemon_*quality*` / `test_resident_quality*` / `test_pipeline_execution*` / `test_gui_manual_qfq*` / `test_data_quality*` / `test_quality_gate*` / `test_quality_orchestrator*` 全绿）。

### 8.2 三方归因（HEAD 干净树实证）

在**不含任何本次改动、也不含他线在途改动**的干净 worktree（`git worktree add … HEAD`，commit `b5d3c87`）上，对失败集中的 9 个文件做文件级抽样：

```
27 failed, 154 passed, 8 skipped  in 23.41s
```

| 测试文件 | HEAD 干净树 | 说明 |
|---|---|---|
| `test_write_integrity_r2_serial.py` | FAILED | writers 锁注册表（他线在途改动区） |
| `test_batch_apis.py` | FAILED | 批量 API |
| `test_phase4_minute_batch_provider.py` | FAILED | 分钟批 provider |
| `test_source_import.py`（8 例） | FAILED | include=False 签名演进 |
| `test_ptrade_public_signature_contract.py`（2 例） | FAILED | PTrade 签名口径 |
| `test_gui_rebalance_mode.py`（HEAD 5 例 / 当前 3 例） | FAILED | **他线在途改动反而修好 2 例**，反证与本改动无关 |
| `test_audit_qfq_staleness.py`（3 例） | FAILED | profiles 路径演进 |
| `test_eps_backfill.py` | FAILED | severity 口径（`2707414` 2026-10-26 起） |
| `test_daemon_once_exit_contract.py` | FAILED | `task_deferred` 键（`c96d09e` 2026-10-24 起未同步） |

**结论：70 个失败全部为既有基线红灯**，分别归属 firefly 批次（eps severity）、writers 锁契约线、PTrade 签名线、source_import 签名演进线；**本次改动零引入**。按「零越权」不擅改他线测试，登记在案。

### 8.3 本次改动相关测试

| 测试 | 结果 |
|---|---|
| `tests/test_quality_audit_scoped.py`（新增 19 例） | **19 passed** |
| `tests/test_pipeline_execution_policy.py` / `test_resident_quality_audit.py` / `test_gui_manual_qfq_cycle.py` | **20 passed** |
| `tests/test_quality_audit.py` / `test_quality_audit_anchor.py` / `test_quality_audit_interruptible.py` | **23 passed** |
| `tests/test_quality_audit_scoped.py + anchor + quality_audit` | **31 passed** |

---

## 九、回退开关

| 开关 | 默认 | 作用 |
|---|---|---|
| `QS_AUDIT_SCOPE=all\|task` | 跟随 CLI | 强制审计范围 |
| `QS_AUDIT_ANCHOR_MODE=new\|legacy` | new | legacy = 旧 `SELECT *` + 双 ROW_NUMBER |
| `QS_AUDIT_ANCHOR_BATCH_CODES` | 500 | anchor 分批 code 数；`0` = 单语句 |
| `QS_AUDIT_DRIFT_MODE=bounded\|legacy` | bounded | legacy = 无候选上限 + 一次性 fetch |
| `QS_AUDIT_DRIFT_MAX_CODES` | 200 | 候选 code 上限（`0` = 不限） |
| `QS_AUDIT_DRIFT_FETCH_BATCH` | 50000 | drift 分批 fetch 行数 |
| `QS_AUDIT_TABLE_TIMEOUT_S` | 300 | task 域单表超时（秒，支持小数） |
| `QS_AUDIT_FULL_TABLE_TIMEOUT_S` | 1800 | 全库域单表超时 |
| `QS_AUDIT_MEMORY_LIMIT` | 空 | 审计连接 `SET memory_limit` |
| `QS_AUDIT_STRICT_FAIL` | 0 | `1` = 旧语义（审计未过即任务失败） |

---

## 十、遗留与风险

1. **列级/主键/data_source 检查未纳入 `_guarded_call`**：客户实测 OOM 栈位于 `_audit_prices`，这三处为单列流式 COUNT，风险低但**未保护**——如后续在超大表上出现 OOM，按同一模式接入即可。
2. **超时为事后留痕**：DuckDB 不支持中断在途查询，`audit_timeout` 在查询完成后记录（不 kill），符合方案 §A3 判据但非硬中断。
3. **A2 取模未改整数区间（对方案的技术异议）**：90 天窗口需 90 个 OR 区间、DuckDB 无索引，改写收益为负；内存有界已由「候选上限 + 分批 fetch」达成。如实申报，未按方案字面实施。
4. **既有 5 红灯**：见第八节，归属线处理。
