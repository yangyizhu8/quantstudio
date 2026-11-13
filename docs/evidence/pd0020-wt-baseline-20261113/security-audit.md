# 基线 worktree 安全审计（PD-2026-0020 R-20261113-02 裁甲前置）

## 一、worktree 指纹

| 项 | 值 |
|---|---|
| 基线锚 | `7826aedbf9d634aabb48c2f7c0d988f490c78b07`（提交 `6a98974^`，2026-11-11 01:13:44） |
| worktree 路径 | `D:\miniQMT策略实盘\QuantStudio-wt-baseline` |
| HEAD | `7826aedbf9d634aabb48c2f7c0d988f490c78b07` |
| detached | **YES** |
| 未提交差异 | **0**（干净检出） |

> **定性边界（用户裁定）**：`7826aed` 可作为「**本批之前基线**」，
> **不是 PD0020 单变量归因**——它与当前 HEAD 之间还夹着其他线的多笔提交。

## 二、安全审计发现（worktree **不自动隔离数据与进程**）

### ① 绝对生产路径（18 处命中，其中 1 处真实风险）

| 文件 | 行 | 内容 | 风险 |
|---|---|---|---|
| `test_delivery_gui_route_parity.py` | L20 | `"aux_db_path": r"D:\miniQMT策略实盘\QuantStudio\data\qfq_aux_mcp_gen1.db"` | **⚠️ 指向生产库（绝对路径，不随 worktree 变化）** |
| `test_checkpoint_policy.py` | L117 | 引用 `docs/...md` | 只读文档，无害 |
| `test_engine_config.py` | L95/L100 等 | `assert "D:/miniQMT策略实盘" not in content` | **反向断言**（检查产物不含硬编码），无害 |

### ② `quantstudio.db` 相对路径（129 处非 tmp）

关键：`DB = parents[1] / "data" / "quantstudio.db"`（`test_d3_firstday_noprice.py:L18`、
`test_daily_snapshot_cache_key.py:L36` 等）——**在 worktree 内解析为
`QuantStudio-wt-baseline/data/quantstudio.db`（不存在）**，故**不会触碰主仓生产库**；
但需确认这些测试是否「不存在则创建」（会产生 worktree 内空库，属可接受）。

### ③ daemon 子进程真启动（必须排除）

| 文件 | 行 | 内容 | 风险 |
|---|---|---|---|
| `test_daemon_gui_start_paths.py` | L105/L118/L138/L150 | `subprocess.Popen(cmd, stdout=f, ...)` | **真启动 daemon 子进程** |
| `test_fin_growth_dividend_staging_tool.py` | L2497 | `subprocess.Popen(cmd, ...)` | 真启动子进程（双层任务 E2E） |
| `test_guard_qdb_domain_marker.py` | L64/L111 | `Popen([sys.executable, "-c", "import time; time.sleep(30)"])` | 仅睡 30s，无害 |

### ④ 凭据（无硬编码真实凭据，安全）

| 文件 | 用法 |
|---|---|
| `test_audit_qfq_staleness.py:L425` | `"token": "${TUSHARE_TOKEN}"` —— **模板占位，不解析** |
| `test_mcp_client_budget.py:L34` | `api_key = "test-key-not-real"` —— 显式假值 |
| `test_phase2_cross_validate.py:L43-45` | `os.environ.get("TUSHARE_TOKEN","")` ＋ 未设置则 `log.warning` 跳过 |

⇒ **无用例读取真实凭据文件或硬编码密钥**。

## 三、处置口径（跑 135 子集前）

| 组 | 处置 | 依据 |
|---|---|---|
| `test_delivery_gui_route_parity.py` 相关项 | **排除，不计 PASS**（记「无法安全运行」） | 绝对生产库路径 |
| `test_daemon_gui_start_paths.py` 相关项 | **排除，不计 PASS** | 真启动 daemon |
| `test_fin_growth_dividend_staging_tool.py` 相关项（10 项） | **逐项核**：若该用例走 L2497 Popen 路径则排除；否则可跑 | 需逐项判定 |
| `test_guard_qdb_domain_marker.py` | 可跑（仅 sleep） | 无害 |
| 其余 | 可跑 | 相对路径在 worktree 内解析 |

## 四、未执行声明

**本轮未在 worktree 内运行任何测试**——安全审计尚未完成逐项判定（尤其
`test_fin_growth_dividend_staging_tool` 10 项是否走 Popen 路径），且用户要求
「安全后按组复现」。**不贸然跑**。

## 五、下一步

1. 逐项判定 `test_fin_growth_dividend_staging_tool` 10 项是否触 Popen；
2. 产出「135 项分组复跑清单」（可跑／排除／契约变化），每组附判据；
3. 在 worktree 内**只跑可跑组**，记录「基线是否同样失败」；
4. 输出四类结论：**基线缺 nodeid**／**无法安全运行**／**契约变化**／**基线同样失败（=基线既有）**，
   **均不计 PASS**。
