# 让路覆盖矩阵（T011 · 硬验收）

**生成**: 2026-10-27 | **Feature**: `specs/001-daemon-backtest-coexistence`
**方法**: 代码路径审查（行号可核）+ 单测（fake collector 验证「注入 ⟶ 命中 ⟶ 换算」）+ T001 基线复现。
**诚实声明**: 本页结论为**静态路径级可证**；**逐任务类型的真实运行实测**尚未执行（需生产库与 daemon 占用窗口，属场景 C/J 的集成验证 T023/T043）。凡未实测处均已标注。

---

## 一、机制

让路复用的既有原语（**daemon.py 执行链零改动**）：

| 原语 | 位置 | 语义 |
|---|---|---|
| `execute_task(cancel_check=...)` | `daemon.py:713-716` | 协作式停止谓词，由调用方注入 |
| `_task_boundary(unit_type, value)` | `daemon.py:799-820` | 单元边界钩子：先推进游标 → 再查谓词 → 命中即 `raise TaskCancelled` |
| `TaskCancelled` 收口 | `daemon.py:756-769` | 水位保持、游标保留、`_task_cancelled=True`、向上抛 |
| 注入点 | `daemon_lifecycle.py` `run_one_cycle` 任务循环 | `cancel_check=self._window_active` |

**为什么不让路打断事务**：`_task_boundary` 的注释明确「validator PASS + 写入提交之后调用」——边界 = 一个完整写入单元已提交，中断不会撕开事务。

---

## 二、矩阵：4 处既有检查点 × 执行路径

| # | 检查点（daemon.py） | 日批路径<br>（全市场按天：`stock_daily` 等） | per_stock 路径<br>（`etf_daily`/`etf_minutes`/`stock_minutes`） | 结论 |
|---|---|---|---|---|
| 1 | 派发前 `if not self._running`：`:2043` / `:2302` | ✅ 存在 | ✅ 存在 | 仅响应「停止」，**不响应让路**（让路由 #2 承担） |
| 2 | 派发前 `_task_cancel_check()`：`:2045` / `:2304` | ✅ 覆盖 | ✅ 覆盖 | **覆盖**：命中即停止派发新单元 |
| 3 | 单元完成边界 `_task_boundary(UNIT_TRADE_DATE/UNIT_STOCK)`：`:2056` / `:2326` | ✅ 覆盖 | ✅ 覆盖 | **覆盖**：已提交单元后中断，水位不推进 |
| 4 | 执行中（单个单元内部） | ❌ 不可中断 | ❌ 不可中断（单只分钟拉取实测约 17s） | **退化**：等该单元跑完 |

**覆盖面结论**（静态可证）：两条主采集路径在「派发前」与「单元完成后」均可让路；不可中断的只有**正在执行的那个单元**本身。

---

## 三、按任务类型的映射

配置中 88 个任务全部 `source=mcp`（`config/profiles/mcp_only/collector_tasks.json`），按执行路径分两类：

| 任务类型 | 代表任务 | 实际路径 | 让路覆盖 | 最小等待粒度 |
|---|---|---|---|---|
| 全市场日线/财务/因子类（约 60+ 个） | `mcp_stock_daily`、`mcp_balance_statement`、`mcp_limit_list_d_pt` | 日批（按 trade_date） | **覆盖** | 一个交易日批 |
| 分钟类 | `mcp_etf_minutes`、`mcp_stock_minutes` | per_stock（按 code） | **覆盖** | 一只股票 |
| ETF 日线类 | `mcp_etf_daily` | per_stock（按 code，注释 `daemon.py:1025-1028`） | **覆盖** | 一只 ETF |

> 路径判定依据：`daemon.py:1025-1036`（ETF 日线走 per_stock；分钟表同理），以及 `:2039`（TradeDate 批）与 `:2298`（per code）两处 `ThreadPoolExecutor` 分支。

---

## 四、退化项与影响面（逐项声明）

| 退化项 | 影响面 | 最坏等待 | 正确性影响 |
|---|---|---|---|
| **D1 单元执行中不可中断** | 回测需等「当前正在跑的那只股票 / 那个交易日批」结束 | 分钟表单只约 17s（实测来源：`daemon.py:2313` 注释）；日批单批视任务而定 | **无**：边界处才中断，事务完整 |
| **D2 qfq post-ingest 进行中不可中断** | 若让路发生在「所有任务已跑完、进入 post_ingest」之后，回测须等该阶段结束 | post_ingest（recover/discover/claim/reanchor/gate/commit）时长未知 | **无**：让路只在阶段结束后生效，水位状态机不受影响 |
| **D3 质量审计** | 让路时审计仍启动，但 `should_stop` 注入后**在第一个检查点立即中止** | 一个审计检查点 | **无**：未执行项计入 `skipped_after_stop`，不伪造「已通过」 |
| **D4 任务失败重试/退避** | 若某单元处于写冲突退避（30s 级），让路要等退避结束 | ≤30s（`writers` 退避上界） | **无** |
| **D5 未覆盖路径（若有）** | 任何未走 `execute_task` 的采集入口（如 `_run_incremental_cycle` 自循环）不响应让路 | 该整轮 | **无**：退化为「等任务边界」，不让路也不出错 |

**统一降级规则**：以上退化项一律退化为「等任务边界/等单元结束」，**不会导致错误结果，只延长等待**；等待期间用户可见真实读数并可取消。

---

## 五、未实测项（如实登记）

| 项 | 为什么没测 | 何时测 |
|---|---|---|
| 各任务类型真实中断耗时 | 需生产库 + daemon 占用窗口（铁律 19），本轮为代码级实施 | 场景 C/J（T023/T043），用户择窗 |
| 单轮/单任务耗时分布 | 历史无统计；不编造 | 场景 C 实测记录 |
| post_ingest 实际耗时 | 同上 | 场景 C 观察到即记录 |

**结论**：矩阵满足「覆盖/退化二态可判定」，退化项影响面已逐项声明；真实耗时数据待集成验证补齐，届时本页追加实测列。
