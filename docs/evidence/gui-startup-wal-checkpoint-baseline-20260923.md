# GUI 启动卡死案 · CHECKPOINT 维护 + 冷启动基线（2026-09-23）

- 归属：客户运维会话（六步第 1 步 + CHECKPOINT 收尾）
- 关联：`docs/case005-*`（前一案）；本案为新立案件（GUI 启动卡死）
- 审核：ZCode 2026-09-23 归因通过（代码级独立复核四项），本文件为**维护操作验收 + 修复基线**

## 1. 事件与根因（已取证）

**现象**：`python .\main_gui.py` 启动后无窗口、无报错、进程长期存活（CPU 94.7%、50 线程、RSS 3.46 GB）。

**根因链**（线程栈逐帧）：
```
main_gui.py:33 → main_window.py:96 __init__ → :146 _setup_navigation → :159 _create_tab
→ tabs/task_tab.py:71 __init__ → :209 _load_tasks → :213 _render_tasks
→ gui/db_helper.py:186 get_watermarks → :152 query_duckdb → :99 _safe_query
→ duckdb.connect(read_only=True)   ← 阻塞点
```

**机理**：主库 `data/quantstudio.db`（38.08 GB）带着 **2.17 GB 未检查点 WAL** →
**任何 `duckdb.connect()` 都必须先回放 WAL**（实测：connect 期间 ~94% 单核、持续 >15 分钟未完成）；
GUI 把这次打开放在 **`MainWindow` 构造期同步执行** → 窗口永不出现。

**为何既有防御未生效**：`_safe_query` 的忙态重试/降级（`READ_ONLY_RETRY_ATTEMPTS=2` / `0.2s`）
只覆盖 **IOException(busy) 快速失败**形态；而 WAL 回放是**「慢的成功」**——既不抛异常也不返回，
重试与降级永不触发（**审核方确认为真实缺口 → 修复②**）。

## 2. 维护操作：一次性 CHECKPOINT（已完成）

- 脚本：`agent_workspace/db_checkpoint_once.py`｜日志：`agent_workspace/_db_checkpoint_20260923_163304.log`
- 解释器：**与 GUI 同版本** Python 3.11.9 + duckdb **1.4.5**（避免 1.5.x 改写存储格式；安全红线遵守）

| 阶段 | 实测 |
|---|---|
| `open RW`（= WAL 回放） | **1338.4 s ≈ 22.3 分钟** ← **「任何打开都要卡 20+ 分钟」的定量基线** |
| `CHECKPOINT` 本体 | **7.3 s** |
| WAL 体积 | **2.33 GB → 0.00 GB**（文件消失）✓ |
| 主库体积 | 38.08 → **38.87 GB**（吸收 WAL，符合预期）✓ |
| 日志/退出 | 无错误，`closed` → `DONE` ✓ |

**操作定性**：只做 `CHECKPOINT`（DuckDB 官方维护操作，崩溃安全）；**不改表结构、不改数据语义、不重建**。

## 3. 修复后冷启动复测（修复基线）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 窗口出现 | **>15 分钟未出现**（进程仍在回放，人工终止） | **12.4 s**（窗口标题 `QuantStudio 数据管线控制台`） |
| 进程资源 | CPU 94.7% / RSS 3.46 GB / 50 线程 | CPU 11.3 s / RSS 437 MB |
| 日志 | 仅 `GUI 日志桥接已安装` 后无进展 | `GUI 日志桥接已安装（Fluent Design）` 正常 |

- 计时锚：轮询 `Get-Process().MainWindowTitle` 首次非空（客观、可复现）
- 复现命令：
  ```powershell
  cd D:\miniQMT策略实盘\QuantStudio
  $t0=Get-Date; $p=Start-Process python -ArgumentList ".\main_gui.py" -PassThru
  while(-not (Get-Process -Id $p.Id).MainWindowTitle){ Start-Sleep -Milliseconds 500 }
  "startup = $(((Get-Date)-$t0).TotalSeconds) s"
  ```

## 4. 如实记录：探针遗留进程

排查期间我用只读探针多次尝试打开该库，其中 3 个进程（PID 11180 / 39176 / 38544）
卡在内核 I/O 等待：`taskkill /F` 报 `no running instance`（不可终止），
`Invoke-CimMethod Terminate` 才清除；期间它们持有库句柄并加重阻塞（DuckDB 报
`File is already open in PID ...`）。**已清理，防复发纳入修复②**（探针/查询必须带超时）。

## 6. 追加取证（2026-09-23 派单 ①④，只读）

### ④ 09-23 写 WAL 的 daemon 启动方式（决定性证据）

`data/daemon_status.json`（残留 status 文件）：
```json
{"pid": 1564,
 "exe": "D:\\miniQMT策略实盘\\trading-battle-back\\venv_miniQMT\\Scripts\\python.exe",
 "cmdline": ["D:\\...\\QuantStudio\\quantstudio\\pipeline\\daemon.py","--mode","forever",
             "--config-dir","D:\\...\\QuantStudio\\config\\profiles\\mcp_only",
             "--instance-token","3f926dbf602e4eb682fced1dc9ceac81"],
 "started_at": "2026-09-21T15:44:23", "status": "running"}
```

- ⇒ 写 WAL 的 daemon 由 **`venv_miniQMT`（duckdb 1.5.3）** 启动，且为 **直启**（`python daemon.py …`，
  **非** `-m quantstudio.pipeline.daemon` wrapper 形式）；
  **⚠️ 更正（2026-09-23 实施期实测）**：此判断**不成立**。`daemon_status.json.cmdline` 记录的是
  `sys.argv`，而 `-m` 形态下 `sys.argv[0]` **同为模块文件绝对路径** → 与直启**同形、不可区分**；
  且实测 `python quantstudio/pipeline/daemon.py`（直启文件形态）**必然失败**
  （`ImportError: attempted relative import with no known parent package`，模块使用相对导入
  `from .task_resume import …`）。故 09-21 那次 daemon **应为 `-m` 启动**。
  受支持入口 = `-m` / GUI 拉起子进程 / `python -c "from quantstudio.pipeline.daemon import main; main()"`。
- `daemon_launch*.txt` 只到 **09-19** → 今天这次**不是 wrapper 启动**（与审核所述「非 wrapper 先例」一致）；
- `pid 1564` 现已不在进程表（`status: running` 为残留）→ 与「硬杀/异常消失 → 留 WAL」吻合。

### ① trading 依赖面排查（降级影响面判据）

| 对象 | 结论 | 证据 |
|---|---|---|
| 实盘主程序 `trading-battle-back/main.py` | **不 import duckdb、不连 QuantStudio 主库** | 其 import 头无 duckdb；数据访问经 `core.market.data_provider.MarketDataProvider` |
| `trading-battle-back/core/`（实盘主链） | **零** duckdb / quantstudio.db 命中 | 全目录 grep 0 命中；`core/market/data_provider.py` 文档明确数据源 = **QuestDB → xtdata → MySQL** |
| 同 venv 的 `OSkhQuant` 子系统 | **有 duckdb 使用面** | `OSkhQuant/data_provider/duckdb_provider.py`（默认 `./data/stock.db`，`read_only=True`）仅被 `OSkhQuant/data_provider/factory.py:27` 引用；`OSkhQuant/strategies/小市值策略_khQuant.py` **硬编码 read_only 读 QuantStudio 主库** |
| `aurumq-rl` | 内存 duckdb（`duckdb.connect()`），与主库无关 | `aurumq-rl/src/aurumq_rl/p3/data.py` 等 |

⇒ **判定**：实盘**主链**无 duckdb 硬依赖；但**同一 venv 内存在 duckdb 消费面（含 read_only 读主库）**。
按裁定「若发现 trading 确实依赖 duckdb 或连主库，降级即受阻——立即回报，**禁止强降**」：
**本次未执行降级**，回报待裁（选项见回报正文）。

### ① 附：QuantStudio 无专用 venv

`.venv` / `venv` / `env` 均不存在 → 「今晚 daemon 由 QuantStudio 主 venv 启动」的实际落点
= 用户级 Python311（`C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe`，**duckdb 1.4.5**），
即 GUI 所用同一解释器。建议口径确认后再定今晚启动命令。


## 5. 待办（按派单）

1. **设计文档（六步第 1 步）**：24h 内成稿提交审计；必答项含 ——
   `read_only=True` 在有 WAL 时的行为（回放是否每次打开重复、是否在内存进行 ⇒ GUI 只读连接**永远无法收敛 WAL**，只能由写者检查点）；`CHECKPOINT` 被并发连接阻塞时的行为与失败路径；
   ③ 形态比选（「每轮次结束前 CHECKPOINT」vs「WAL 超阈值且库空闲时检查点 + WAL 体积巡检告警」）与成本核算（本次实测：合并 2.33 GB 仅 **7.3 s**，但**回放**耗时 22.3 min）。
2. **版本混杂核查**（只读）：`venv_miniQMT`（duckdb **1.5.3**）哪些单元打开过 `quantstudio.db`；09-18 daemon 会话的连接/关闭记录。
3. **WAL 增长机制定谳**（禁止未证实归因）：硬杀无干净关闭史、单一巨事务、`checkpoint_threshold`/`wal_autocheckpoint` 类覆盖（全仓 grep）、长持有连接阻塞检查点。
4. 客户侧更新通知（若本案改动落到交付产品面，按运维流程出通知）。
