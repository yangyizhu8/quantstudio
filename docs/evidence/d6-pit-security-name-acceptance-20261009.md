# D-6 验收证据 —— 证券简称 PIT（display_name 接真实简称）

- 提出：策略研发会话　｜　日期：2026-10-09　｜　方案：`docs/r1-framework-fix-design-20261009.md` §3.5
- 裁定：②审第②项（D-6 第二件，独立 commit + 独立验收）；PIT 取值依 `stock_namechange` as-of，与 D-4 同源同窗

## 1. 验收判据与结果

| # | 判据 | 结果 |
| --- | --- | --- |
| 1 | 简称按 PIT 随更名日切换 | **PASS**：002743.SZ 在 2026-09-03 = 「富煌钢构」、2026-09-15 = 「**ST富煌**」（更名日 09-08 精确切换） |
| 2 | 双 API 面一致 | **PASS**：`get_stock_info(..., 'stock_name')` 与本地 `get_security_info().display_name/.name` 同值 |
| 3 | 其余证券名称稳定 | **PASS**：600000.SS=浦发银行、000001.SZ=平安银行、300464.SZ=星徽股份（两日一致） |
| 4 | `as_of` 可选，既有调用方字节级不变 | **PASS**：不传 `as_of` 时 `display_name` 仍为入参代码（保留原行为） |
| 5 | 回归零新增 | **PASS**：`test_g2_cp3_reference` + `test_ptrade_profile_registered_stock_apis` + `test_valuation_date_pit` + `test_minute_engine_lifecycle` = **53 passed / 1 failed**；worktree@HEAD 基线同为 **53 passed / 1 failed（同用例同错误）** ⇒ 唯一失败为既有失败（`publish_agent_strategy.py:49` R5.5 台账门） |

复现命令：

```
QUANTSTUDIO_DATA_ROOT=<r55_readonly> python quantstudio/backtest/run_ptrade_strategy.py \
    agent_workspace/first_board_high_open_1to2/probe_d6_name.py <day> <day> --profile minute-bar-v1
```

## 2. 改动落点（框架层，策略源码零改动）

| 文件 | 改动 |
| --- | --- |
| `quantstudio/backtest/providers/duckdb_data_access.py` | 新增 `query_security_name_pit(query_ms)`：`stock_namechange` as-of（`change_date <= query_ms` 最近 `name_after`）+ 回退 `stock_basic.name`，返回 `code/name/name_source` |
| `quantstudio/backtest/providers/duckdb_provider.py` | 新增 `_name_pit_map`（按 as_of 缓存，一日一次查询）与 `get_security_name(code, as_of)`；`get_security_info(code, as_of=None)` 股票分支 `display_name` 改用 PIT 名 |
| `quantstudio/backtest/ptrade_api.py` | `get_stock_info` 传 `as_of=current_date or prev_date`；本地 `get_security_info` 用 PIT 名，**缓存键加入 as_of**（简称按日 PIT，跨日不可复用） |

## 3. 已登记的口径说明

- **回退轻度前视**：无 `stock_namechange` 记录的 code 回退 `stock_basic.name`（**当前快照**）。因「名称不含退」判据所需的退市/ST 标记**均出现在 namechange 中**，回退路径不影响该判据正确性——已在代码注释与本证据中显式登记。
- **与 D-4 的联动**：D-4 修复的是 `stock_daily.is_st_reliable`（数据层字段）；D-6 走的是 `stock_namechange` PIT 直读，**不依赖** `is_st_reliable` 是否已回填——故 D-6 在本批可独立生效（与裁定「is_st 可靠面恢复前 name 路径是唯一正确途径」一致）。

## 4. 提交说明（独立 commit）

本 commit 仅含 D-6 改动 + 一处**前置**（`duckdb_provider.py` 的 `import logging` / `logger = logging.getLogger(__name__)`，为 `_name_pit_map` 的告警路径所需）；
D-2/D-3 的估值契约改动（同文件）按 hunk 级外科法**留在工作区**，未入本 commit。