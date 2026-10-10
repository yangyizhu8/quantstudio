# 验收证据 —— ST 真值写入路径覆盖修复（M-1/M-2/M-3/M-4）

- 日期：2026-10-11｜方案：`docs/st-truth-path-coverage-design-20261011.md`（②审通过）
- 事故依据：`docs/evidence/incident-st-flag-overwrite-20261011.md`

## M-4 站点—门矩阵（全部 align 调用点）

| 站点 | 路径 | F-1 门 | F-3 门 | 判定 |
| --- | --- | --- | --- | --- |
| 1156 | 通用 `_execute_task` | 1146 | 1166 | 覆盖 |
| **1393** | **分片流式** `_run_with_source_streaming` | **1347** | **1399** | **本次新增（事故路径）** |
| 1781 | `stock_float_share`（`basic_df`，源 `pro.daily_basic`） | — | — | **不适用**：非 stock_daily、不写 `is_st_reliable` |
| 1808 | `stock_float_share`（同上） | — | — | **不适用**：同上 |
| 1868 | per-day（MCP 逐日） | 1757 | 1873 | 覆盖 |
| 2121 | per-code（MCP 逐码） | 2073 | 2126 | 覆盖 |
| **2805** | **A4 局部重拉** | **2791** | **2809** | **本次新增** |

⇒ **凡可写 `stock_daily` 的 align 站点均已接门**；两处不适用站点已给出排除依据（非 stock_daily 分支）。

## 实施内容

| # | 改动 | 位置 |
| --- | --- | --- |
| M-1 | 分片流式路径循环前准备 `namechange_df`/`valuation_df`（按 `table == "stock_daily"` 限定；其余表保持 None ⇒ 逐位不变） | 1327 后 |
| M-1 | 分片路径 `align(...)` 增传 `namechange_df`/`valuation_df` | 1393 |
| M-2 | 分片路径 F-1 门（依赖缺失 fail-loud，`pull_failed=False`）+ F-3 门（覆盖率门，未过 `return False`） | 1347 / 1399 |
| M-3 | A4 路径循环前准备依赖 + F-1 门（未过中止 A4 段 `return _a4_idx`）；循环内 `align` 增传两参 + F-3 门（未过 `break` 中止 A4 段） | 2791 / 2805 / 2809 |

**签名确认（②审问项）**：`align(..., namechange_df, valuation_df, ...)` 中 `valuation_df` 确有消费——
`is_delisting_risk` 规则 2（近 20 日 `circ_mv < 5 亿`，`aligner.py:724-727`）与 `_derive_valuation_fields`（`aligner.py:383`）⇒ **照传**（非最小面）。

## 验收

| 判据 | 结果 |
| --- | --- |
| 编译 | `py_compile` **PASS** |
| 管线回归（7 文件） | **97 passed / 0 failed**（与基线一致） |
| M-4 矩阵 | **PASS**（见上表） |
| V-1 端到端持久性断言 | **待 daemon 重启后首轮批次执行**（True 退化 = 0） |
| V-2 逐行一致性 / V-4 非 stock_daily 逐位不变 / V-5 矩阵 | 待 V-1 同窗执行 |

## 回退

M-1/M-2/M-3 互相独立可单独摘除；回退后须重跑 D-4 回填（`--db` 指定主库/新副本）。