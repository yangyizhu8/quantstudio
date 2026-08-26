# P-A3 writer 自动回补 feature gate 修复方案（2026-08-25）

> 触发：用户验收一期时发现激活时序缺陷——writers.py 当前在每次 fin_indicator 写入后无条件调用 `backfill_eps_gap(conn)`，代码一旦被生产进程加载即可能提前修改真实库，绕过"周三推送后、用户确认 daemon 状态再启动二期"的控制点。
> 类型：框架行为层安全加固（非新功能）。
> 范围：`quantstudio/pipeline/writers.py` + `tests/test_eps_backfill.py`。
> 硬约束：CLI `--apply` 保持人工独立执行；默认关闭、显式开启、fail-closed。

## 1. 缺陷定义

当前 `DuckDBWriter._write_locked` 在 `table == "fin_indicator"` 时无条件调用 `backfill_eps_gap(conn)`。该调用发生在任何加载 `DuckDBWriter` 的进程写入 fin_indicator 时，包括：
- 用户/守护进程按计划写入新财报（期望行为，但需二期控制点后才启用）
- 其他会话/测试/生产进程一旦写入 fin_indicator 就自动回补真实库

后果：P-A3 二期"真实库 --apply 回补"的启用控制权被提前消耗。

## 2. 修复范围

仅改动 `quantstudio/pipeline/writers.py`：
1. 增加 feature gate 判断函数 `_is_writer_auto_backfill_enabled()`（读取环境变量 `QS_AUTO_BACKFILL_EPS`）。
2. `fin_indicator` 写后回补仅在该 gate 显式开启时执行；默认关闭。
3. gate 判定 fail-closed：仅 `"1"`/`"true"`/`"on"`（不区分大小写）视为开启；未设置、`"0"`/`"false"`/`"off"`/空字符串/任何其他值均关闭。
4. 日志：gate 关闭时若写 fin_indicator，记录 debug 级"auto backfill disabled by feature gate"；开启时按现有 info 记录。

**不改动**：
- `scripts/backfill_eps_gap.py` CLI `--apply` 保持独立（直接调用 `backfill_eps_gap(conn)`，不受此 gate 影响）。
- `quantstudio/pipeline/quality_audit.py` 的 `EpsBackfillGap` 门禁（仍独立运行，继续 fail-closed）。
- `quantstudio/pipeline/eps_backfill.py` 核心回补逻辑。

## 3. 影响面

- 生产守护进程默认不会自动回补；二期启用时由运维/配置显式设置 `QS_AUTO_BACKFILL_EPS=1`。
- 现有 17 个 eps_backfill 测试：test 14（writer 路径）原期望自动回补；修复后需显式开启 gate 才能触发，否则失败 → 需更新测试。
- 其他框架组件无依赖（grep 确认仅 writers.py 调用 `backfill_eps_gap`）。

## 4. 测试策略（四类）

1. **默认关闭**：`QS_AUTO_BACKFILL_EPS` 未设置/空/0/false/off 时，`DuckDBWriter.write(fin_indicator)` 不写回 `backfill_eps_source`，eps 保持 NULL。
2. **显式开启**：`QS_AUTO_BACKFILL_EPS=1`（或 true/on）时，writer 写入 fin_indicator 后自动回补，eps 非 NULL，`backfill_eps_source` 被标记。
3. **CLI 独立**：writer gate 关闭时，直接调用 `backfill_eps_gap(conn)`（即 CLI `--apply` 等价路径）仍可成功回补；CLI 与 writer gate 解耦。
4. **fail-closed**：参数化验证非法/缺失值全部不触发自动回补。

## 5. 验收标准

- `tests/test_eps_backfill.py` 全绿（含新增 4 类 gate 测试 + 调整后的 test 14）。
- 相关子集回归（schema 迁移 7 + authority policy 6 + validator 43）全绿。
- 全量回归失败集合与实施前基线一致（19 失败无新增），含 P-A3 清单中的真实库直连用例。
- 用户书面确认修复后，方可进入六步流水线第 5 步（P-A3 一期关闭）和第 6 步（推送）。

## 6. 回退条件

若 gate 引入回归异常，回退到回退点 `fa98d905` 或移除 gate 调用（保留环境变量判断但默认改为开启，作为临时兼容）。
