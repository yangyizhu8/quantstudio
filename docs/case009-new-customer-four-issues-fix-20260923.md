# CASE-009 客户事故归档：新客户四问题修复批（2026-09-23）

> 状态：**已闭环**（本批 10 笔已并批上远程，`origin/main` 含全部提交）
> 事件：新客户（`D:\QuantStudioNew`，2026-09-16 全新克隆部署）技术支持反馈包（2026-09-23）四问题
> 环境：Windows 11 / 16GB / Python 3.12.10 / 代码版本 `9aa462b` / duckdb 1.4.5
> 交付形态：随推送协调批下发客户（文案定稿见「七、客户交付文案」）
> 关联：CASE-007（GUI 启动卡死）、CASE-008（duckdb 混版统一）——三案同批 21 笔上远程

## 0. 一页定谳表

| # | 客户问题 | 层级 | 定谳 | 落点 | 状态 |
|---|---|---|---|---|---|
| 1 | stock_minutes 全量拉取两次被服务端拒单 | 框架（取数分批策略） | 服务端单作业 **60s 软时间预算**触顶；窗口仅由行数驱动（2 天/批），`row_limit` 只截断返回行数不缩短扫描时间 | T1 `478ed0e` | 闭环 |
| 2 | stock_float_share 三次 OOM | 框架（路由缺失） | 该表 1,430 万行未进 `_EXPORT_TABLES` ⇒ 走 `fetch_page` 全量累积 JSON 进内存 | T2 `6ceeff9` | 闭环 |
| 3 | etf_minutes 质量门禁判负 + 隔离区满 | 框架（门禁阈值口径） | **拦截是真阳性**（数据/因子自身矛盾）；真正问题是门禁阈值 1% 系日线口径校准，对分钟表偏严 | 门禁分表型校准 `c62c1cc` | 闭环 |
| 4 | pyarrow 未声明（新机必踩） | 打包 + 异常语义 | `pyproject` 两处均缺声明；`_parquet_has_column` 静默吞 ImportError 伪装成「列不存在」 | T4 `151e984` + 补救 `0da4d79` | 闭环 |
| 3-原案 | （T3 判据归一） | — | **真阳性误判为误拒，方案方向被验收反证推翻** | `996ced2` → 回退 `4d470e5` | **已回退** |

## 1. 问题 1：stock_minutes 拒单（T1）

**现象**：2 天窗/89 批，两次分别死于批 44/87 与 70/89，错误恒为
`MCPExportBudgetError: export_exceeds_time_budget`。

**云端实测（只读，本批依据）**：

| 项 | 结果 |
|---|---|
| 同步 2 天窗 | 2,514,353 行 ✅ |
| 同步 8 天窗 / 50 天窗 | ✅（两者均被静默截到 **5,000,000 行**） |
| `async_mode=true` | 立即返回 `{"job_id":…,"status":"running"}`；45s 后 `status=ready`，100 分片 SHA 完整 |
| 行数硬上限 | 请求 `row_limit=50,000,000` 仍返回 5,000,000 |

**结论**：`row_limit` 不是分批杠杆（只限返回行数）；唯一有效杠杆是缩小时间窗口。
**修复**：分钟表窗口默认 2 天 → 1 天（预算反算 1 天），可配 `minute_export_window_days`
（0 = 回退旧行为）；`async_mode` 透传（默认关，灰度）。

## 2. 问题 2：stock_float_share OOM（T2）

**现象**：三次 OOM，`Unable to allocate 109 MiB`（shape 14,299,109）→ 1.28 GiB；进程峰值 ~6.3GB。

**根因**：该表未进 `_EXPORT_TABLES` ⇒ `fetch_table` 落 `_fetch_small_table` ⇒
`_fetch_all_pages` 把 1,430 万行 JSON dict 全量累积再一次性 `pd.DataFrame(rows)`。
同源的 `stock_daily_valuation`（同量级）早已在 export + streaming 白名单 ⇒ 两表口径分裂。

**修复**：`_EXPORT_TABLES` + `_STREAMING_TABLES` 补 `stock_float_share`；
`_EXPORT_ROW_ESTIMATE` 登记 14,300,000；新增 `_ROW_LIMIT_BUDGET_TABLES`
（**仅声明表**施加预算窗口，未声明表分批行为逐位不变）。

## 3. 问题 3：UnitCheck 门禁（本案最大教训）

### 3.1 原判（被推翻）

原定性：UnitCheck 判据 `amount/(close×vol) ∈ [0.5,2.0]` 中的 `close` 是已还原 raw，
而 `amount`/`volume` 不复权 ⇒ 比值恒 = `adj_i/adj_latest` ⇒ 系统性误拒。
方案层代数论证成立，经审核批准，实施「判据归一（乘复权因子）」`996ced2`。

### 3.2 验收反证（步骤 4）

| 判据 | 2025-06 窗 | 2026-01 窗 | 2026-09 窗 |
|---|---|---|---|
| `close/(amount/vol) ≈ 1` 行占比 | 84.40% | 92.03% | **99.94%** |
| 旧行为拒绝率 | 3.1738% | 3.3273% | 0.0000% |
| **归一后拒绝率** | **4.9760%** | **3.6890%** | 0.0000% |
| A 类：归一消除的拒绝（收益） | 1,276 | 2,151 | 0 |
| D 类：归一**新增**的拒绝（代价） | 3,874 | 2,889 | 0 |

- A 合计 3,427 < D 合计 6,763 ⇒ **净增拒，方向被证伪**；
- 单码日内实证（`159388.SZ` 2025-06-03）：`close/(amount/vol)` 恒为
  `0.3998 = adj_i/adj_latest(1/2.5011)` ⇒ 云端 close 是**已复权到最新锚的可成交价**，
  不是需要再乘倍数的 qfq；
- 数据画像：`amount/(close×vol)` 99% 分位 = 9.0046 / 9.0028 / 1.0021
  ⇒ 2026-01 前部分码存在 `amount ≈ 9×close×vol` 的**真实数据异常**。

**处置**：整体回退 `4d470e5`，保留 2 例证伪回归钉；根因修正为「拦截是真阳性，
门禁阈值口径才是框架层问题」。

### 3.3 正确修复（用户裁定选项 1）

**阈值推导（非拍值）**——40 个 1 日窗（2025-01~2026-09，两表各 20 窗）：

| 表 | P50 | P90 | P99 | max | 行级聚合 |
|---|---|---|---|---|---|
| etf_minutes | 4.3755% | 4.9338% | 5.0499% | 5.0635% | 3.8940% |
| stock_minutes | 0.1539% | 1.9978% | 2.0273% | 2.0320% | 0.6636% |

**定案**：`max(P99)=5.05% × 1.58 ≈ **8%**`（配置键 `max_reject_rate_minute`）；
日线保持 1%；**只改批次判负线，行级判拒零变更**（越界行仍 REJECT → 隔离区）。

**开发期自纠**：初版把分钟阈值嵌在 `source == "mcp"` 分支内 ⇒ xtquant 分钟表
（历史手/股单位错配事故路径）漏配，由测试失败暴露后移出为独立 `freq` 分支。

## 4. 问题 4：pyarrow 未声明（T4）

**根因**：①`pyproject.toml` 的 `dependencies` 与 `[all]` **均无 pyarrow**；
②`_parquet_has_column` 的 `except Exception: return False` 把 ImportError 伪装成
「列不存在」⇒ 因子列投影空 ⇒ 注入 0 行 ⇒ 误导性报错。

**修复**：两处声明 `pyarrow>=14`；依赖缺失显式抛 ImportError（含安装指引）；
列不存在仍返回 False（业务兜底不变）；投影循环 `except ImportError: raise`。

## 5. 本案事故与自我纠错（如实记录）

| 事故 | 发现方式 | 处置 |
|---|---|---|
| T3 方案方向错误（真阳性误判为误拒） | 步骤 4 验收反证（交叉表 + 云端口径实证） | 整体回退 + 根因修正 + 证伪钉固化 |
| **回退范围过宽**：回退 T3 时 `git checkout 6ceeff9 -- mcp_adapter.py` 连带回退 T4 同文件改动 | **回归测试** `test_parquet_has_column_missing_pyarrow_raises_explicit_error` failed（DID NOT RAISE） | 重做 T4 适配器部分 `0da4d79` + 逐项复核 T1/T2/T4 改动点齐全 |
| 并行会话改写工作区（T1 后追加 7 笔 GUI/运维线提交） | `git log` 复核 | 全程精确文件清单 `git add`，零卷入；逐项复核本线改动存活 |
| pyarrow 用例 monkeypatch 未生效（`sys.modules` 缓存） | 八套件终验 failed | 先清缓存再 patch `c955db8` |

## 6. 验收与防回归

| 项 | 结果 |
|---|---|
| 本线套件 | **128 例 → 127 passed / 1 failed**（既有红 `test_pit_filter::test_validator_is_single_chokepoint`） |
| 门禁/失败/质量关键词全域 | **295 passed + 1 xfailed** |
| 新增回归钉 | T1 6 例 + T2 3 例 + T4 3 例 + 门禁 9 例 + 证伪 2 例 |
| 可复现验收脚本 | `scripts/acceptance/` 5 个（窗口等价性 / 隔离区防护 / 证伪交叉表 / 口径判定 / 阈值推导） |
| 端到端验证 | 真实 `_restore_to_raw` + aligner + validator：旧行为 60/60 误拒 → 新行为 60/60 放行（T3 期）；门禁期「异常 bar 仍全拒、批次不再判负」 |
| 影子等价 | 流式 vs 直连 `assert_frame_equal` 逐值等价；窗口变更覆盖面零空洞零重叠 |
| 用户追加验收项 | 隔离区 顶格→归档腾退→写入恢复 全链路 PASS（归档不丢数据） |

## 7. 客户交付文案

定稿文件：`D:\miniQMT策略实盘\私募工作文件\QuantStudio-MCP全数据源替代任务文件\客户通知-数据采集四问题修复-20260923.md`
（五模块：修复内容 4 项 / 客户侧更新操作 4 步 / 更新后验证清单 5 项 / 预期行为变化 2 条 / 待跟进）。
经用户 2026-09-23 批准（微调：操作第 1 步统一 `git pull origin main`，已核验客户克隆后远端名即为 `origin`）。

## 8. 经验沉淀（已转正）

| 级别 | id | 内容 |
|---|---|---|
| 全局 | `9ffdbc16` | 【铁律·判据类修复的样本抽验前置 + 净收益双计】5 条 |
| 项目 | `37e5dabc` | QuantStudio MCP 数据面关键事实 7 条 |

## 9. 遗留与转域

| 项 | 去向 |
|---|---|
| 云端 `etf_minutes` close 口径与还原链自洽性疑问 | `docs/pipeline-tech-debt.md`（REGISTERED，低优先，与 qfq 域尾巴同池） |
| `amount ≈ 9×close×vol` 真实数据异常定位 | 转数据域跟进（用户 2026-09-23 裁定） |
| `test_pit_filter::test_validator_is_single_chokepoint` 既有红 | 另起追单归因（单一入口期望 vs 4 处 `writer.write` 的契约漂移），入本线 backlog |
| `agent_workspace/` 冒烟/自愈探针脚本 | 并入下一 docs/ 批入库（验收可复现性，非关键路径） |