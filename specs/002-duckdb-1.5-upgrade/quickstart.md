# Quickstart：窗口执行 / 回滚 / 客户自检

**Date**: 2026-10-29 | **适用**：M-3 维护窗（**11-07 / 11-08 周末凌晨**，避 11 月实盘准备期的交易日盘前链；与 dev 线联办 + 占用声明） | **前置**：M-2 独立审核通过 + 用户放行

> 本文件是**操作序列**，不含实现代码。所有命令在**项目根**执行。

## 0. 窗口前检查（窗外，可提前一天）

| # | 检查 | 命令 / 判据 | 通过标准 |
|---|---|---|---|
| 1 | 判定源就绪 | `python -m quantstudio.pipeline.duckdb_version_gate --check` | 当前 1.4.5 输出 `PASS 1.4.5`，退出码 0 |
| 2 | 双脚本同源 | `grep -nE "duckdb_version_policy" check_wal.ps1 check_wal.sh` 各命中 ≥1；且 `grep -nE "-match '\^1|1\\.4\\.\*" check_wal.ps1 check_wal.sh` **零命中** | **本地版本正则已移除**（脚本只调判定源；文案中的「1.4.5 ≤ x < 1.5」集合描述属正常，不算残留） |
| 3 | 回归门 | `bash scripts/check_wal_regression.sh` | 全绿 |
| 4 | 契约 | `pytest tests/test_duckdb_version_gate.py -q` | **27 项全过**（9 契约点 + 逃生阀 + 导入失败 + 不可解析 + 单源同一性 + CLI↔进程内比对） |
| 5 | 任务定义 | `schtasks /Query /TN QS_DaemonForever /V /FO LIST` | 未携带 `QS_DUCKDB_VERSION_GATE=0`；`IgnoreNew` 生效 |
| 6 | 回退点 | `git stash create` + `git stash store` | hash 记入本轮上报 |
| 7 | 基线 | 主库尺寸 / WAL / `docs/evidence/pk_baseline.json` 与现库比对 | 库尺寸记录；WAL=0；PK 巡检：**daemon 在跑时为 SKIP/退出码 3（库被独占，非失败）**，停服后取 `exit 0` |

## 1. 窗口步骤（目标 ≤60 分钟）

| 步 | 动作 | 判据 / 证据 |
|---|---|---|
| W1 | **群内声明占用**（时段 + 停 daemon + 预期恢复时点），按铁律 19 | 声明留痕 |
| W2 | 关闭 GUI；停 daemon（先 graceful，必要时按 `daemon_status.json` 的 pid 精确停，**排除自身 PID**） | 无写者进程（双形态 grep：`-m quantstudio.pipeline.daemon` 与 `quantstudio\pipeline\daemon.py`） |
| W3 | 记录停机时刻 t0 + 主库尺寸 + WAL | 读数入证据 |
| W4 | 装 1.5.6 到运行主库的解释器（`Python311`；如需 GUI 同环境，另含官方主 venv）：**显式版本命令** `pip install "duckdb==1.5.6"` | `python -c "import duckdb;print(duckdb.__version__)"` ⇒ 1.5.6 |
| W5 | 门禁自检 | CLI ⇒ `PASS 1.5.6`（退出码 0）；daemon `--mode once` 探活被放行 |
| W6 | **冒烟 S1–S6**（详见 spec FR-015）：S1–S3 写入落**独立临时库/独立文件**、S4 主库**只读**点查、S5 **文件头只读**读数（`uint32 LE @12`）、S6 替身触发 | 6/6 PASS，逐项留读数；**窗口内不得写主库既有表** |
| W7 | E7 基线（升级后一次） | 读数入证据，与升级前成对 |
| W8 | 拉起 daemon：`schtasks /Run /TN QS_DaemonForever`（**禁 Start-Process**） | `daemon_status.json` 的 `started_at` 更新且 `status=running`；进程 cmdline 为 `-m …daemon` |
| W9 | 记录 t1、WAL 体积、daemon 首轮耗时 | 窗口时长 = t1 − t0，须 ≤60 分钟 |
| W10 | 拆除占用声明，窗口报告 | 上报块 |

**窗口内禁止**：任何 `--mode once` 采集、GUI 跑任务、离线回填工具、任何建表/初始化写者的入口。

## 2. 回滚（任一步判据不达标即触发）

| 步 | 动作 | 判据 |
|---|---|---|
| R1 | 停 daemon（同 W2） | 无写者 |
| R2 | 降版到 1.4.5（**显式版本命令** `pip install "duckdb==1.4.5"`；**禁用** `pip install -r requirements.txt` —— 声明面已锁 1.5.6） | **同一解释器**跑 `python -c "import duckdb;print(duckdb.__version__)"` ⇒ 1.4.5 |
| R3 | 旧版打开现库并抽样读取 | **用降版后的同一解释器**只读打开现库，对 3 张代表表执行 `SELECT count(*)`：`stock_daily`（大表）、`trade_calendar`（日历）、`index_constituents_snapshot_meta`（快照元）⇒ 均成功返回行数、**无格式错误、无 `Conflict on tuple deletion`** |
| R4 | 门禁复核 | **同一解释器**跑 CLI ⇒ `PASS 1.4.5`（退出码 0）；daemon 放行（`require_duckdb_version` 不抛 `SystemExit(3)`） |
| R5 | 库异常（打不开 / WAL 膨胀） | 走 `check_wal.ps1 -Force`（Windows）/ `check_wal.sh --force`；**禁手工删 WAL** |
| R6 | 拉起 daemon（`schtasks /Run`） | 同 W8 判据 |
| R7 | 记录回滚读数 | 时长、失败判据、库状态 |

> 回滚**不需要**重建库、不需要转格式（E4a/E4b 已证 1.4↔1.5 无格式门）。

## 3. 本地验证（pytest / 脚本层，任一时刻可跑）

```bash
pytest tests/test_duckdb_version_gate.py -q          # 契约 27 项（9 契约点 + 5 类判据 + 单源同一性）
python -m quantstudio.pipeline.duckdb_version_gate --check
bash scripts/check_wal_regression.sh                  # 铁律 23 回归门
python scripts/check_passthrough_pk.py --check        # PK 巡检（daemon 在跑时应报 SKIP/退出码 3）
```

## 4. 客户自检（写进通知，一条命令）

```bash
python -m quantstudio.pipeline.duckdb_version_gate --check
```

> 等价的单文件形态（判定源唯一，两入口同结果同退出码）：`python quantstudio/pipeline/duckdb_version_policy.py --check`

| 输出 | 含义 | 客户动作 |
|---|---|---|
| `PASS 1.5.6`（退出码 0） | 版本与门禁均正常 | 无需操作 |
| `FAIL <ver>`（退出码 1） | 版本不在放行集合内 | 按通知「更新依赖」小节执行后重试 |
| `ERROR …`（退出码 2） | 依赖缺失或版本串不可读 | 报运维，附输出原文 |

> 通知须写明：**代码与依赖必须同批更新**，否则门禁拒启（退出码 3）；拒启属预期保护，不是故障。

**通知必含的量化提示（A-14 口径，M-2 意见④）**：「升级到 1.5.6 后，批量写入路径的耗时约为旧版的 4–6 倍——300 行约 3.9 秒、1000 行约 13.4 秒、5000 行超过 90 秒未完成（均为本机实测）。这不影响数据正确性，影响的是该辅助表刷新的时延。」

## 5. 验收清单（窗口末日）

- [ ] S1–S6 全 PASS（逐项读数）
- [ ] 窗口时长 ≤60 分钟（实测）
- [ ] daemon 形态与计划任务一致（`-m … --mode forever`）
- [ ] E7 前后各一次基线成对
- [ ] 首周哨兵关键词已挂（缺陷 FATAL 文本 + PK 巡检）
- [ ] 证据落 `docs/evidence/`（含回滚预案演练记录，若未触发演练则注明「备而不用」）
