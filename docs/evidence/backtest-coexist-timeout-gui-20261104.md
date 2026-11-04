# 回测遇「常驻采集未停」⇒ GUI 三选项失效（2026-11-04）

- 现象：客户常驻采集在跑时点回测，弹「回测错误」+ 一屏
  `CollectorStopTimeout: 采集在 60 秒内未退出（可能仍在长任务中）` traceback。
- 期望（**早已实现**，FR-019 / SC-013）：「继续等待 / 强制终止 / 取消回测」三选项。

## 1 根因链（逐行取证）

| 环节 | 事实 |
|---|---|
| 采集常驻 | `daemon --mode forever` **全程持 DuckDB 写锁**（Windows 文件锁）。实测：写锁持有时，另一进程**连只读都打不开**（`IOException: 另一个程序正在使用此文件`）⇒ 并行只读回测不可行 |
| 停止语义 | 停止请求只在**任务边界**生效（`daemon.py` 单元边界中断）；长任务（分钟线/全量）≫ 60s |
| 超时上限 | `backtest_window.STOP_WAIT_SEC = 60`（注释明写"超时给三选项"） |
| **真缺陷** | `gui/workers.py` 异常块**只对 `BacktestCancelled` 原样传递对象**，其余一律 `emit(f"{type(e).__name__}: {e}
{traceback}")` **降级成字符串** ⇒ `backtest_tab._on_error` 的 `isinstance(err, CollectorStopTimeout)` 恒 False ⇒ `_handle_stop_timeout`（三选项）**永不触发** |
| 口径分裂 | CLI 路径有 `except CollectorStopTimeout` → exit 4 + 人话提示；GUI 路径无 ⇒ 裸 traceback |

## 2 修复（最小，恢复已批准行为）

| 文件 | 改动 |
|---|---|
| `quantstudio/gui/workers.py` | 结构化异常**原样传递**（`BacktestCancelled` / `CollectorStopTimeout` / `WindowCancelled`），traceback 挂到异常对象 `_gui_traceback`；并把 `coexistence_stop_timeout` / `coexistence_boundary_timeout` 透传给共享入口 |
| `quantstudio/gui/tabs/backtest_tab.py` | `WindowCancelled` 归入"取消"（info 无弹窗）；「继续等待」把等待上限 **×2（封顶 15 分钟）**，并补"采集在任务边界退出，长任务可能还需数分钟"的说明 |

## 3 验证读数

| 项 | 读数 |
|---|---|
| 新增回归用例 | `tests/test_gui_backtest_stop_timeout_options.py` **7 passed**（结构化传递 / 三选项路由 / 取消语义 / 上限递增 / CLI 口径不互相污染 / 异常类互不继承） |
| 回测/GUI 相关回归 | 18 文件：**299 passed / 4 failed** |
| 4 例失败归属 | **既有债，非本批**：HEAD worktree 对照 `test_ptrade_profile_registered_stock_apis` + `test_ptrade_public_signature_contract` **同样 3 例 FAILED**（他线在途 `quantstudio/backtest/ptrade_api.py` 改动，我方未触碰） |
| 知识沉淀 | `DB-2026-0027`（族 db / D4 / artifact_rule，机检通过） |

## 4 客户侧即时可用的处置（在修复版下发前）

1. 等当前长任务（分钟线等）跑完，采集会在**任务边界**退出，再点回测；
2. 或手动结束采集进程后再回测（中断本轮：未提交单元丢弃、水位不推进、下次启动重跑）；
3. CLI 等价物：`--coexistence-stop-timeout` 可调等待上限（GUI 修复后也会提供"继续等待即延长"）。
