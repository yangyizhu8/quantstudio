# T001–T004 窗前实态基线与回退点（duckdb 首跳升级）

- **日期**：2026-10-29 16:16–16:40（Asia/Shanghai）
- **归属**：duckdb 数据库升级执行会话（图节点⑲）｜spec 002-duckdb-1.5-upgrade
- **阶段**：③实施门已开（检查点 A 批准于 2026-10-29），本基线 = Phase 1（Setup）产出
- **性质**：**只读**；未停 daemon、未装包、未改主库

## 一、回退点（T001）

| 项 | 读数 |
|---|---|
| HEAD | `f0ef3d5ec23519ff5af15dd99ad436050a2df00f` |
| 工作树脏文件数 | **783**（多会话共享工作区，含他线在途改动，**非本会话产生**） |
| 本会话落点文件改动 | 实施前逐个 `git diff --numstat` 均为 **0** |

**对铁律 03「建 stash 回退点」的偏离（如实申报）**：本工作区含他线 783 项在途改动，
`git stash create` 会把他线内容整体卷入回退对象，误 `pop` 的风险高于收益；
且本会话的改动全部为**未提交的工作区修改**，逐文件 `git checkout -- <path>` 即可精确回退、
不触碰他线文件。故回退点 = **HEAD SHA + 精确路径回退**（不建 stash 对象）。

## 二、窗前实态（T002）

| 项 | 读数 | 来源 |
|---|---|---|
| daemon 进程 | **PID 32032**，exe=`C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe`，started **2026-10-29T16:16:55**，status=running | `Get-CimInstance Win32_Process`（双形态正则，排除自身 $PID）＋ `data/daemon_status.json` |
| 计划任务 | `\QS_DaemonForever`：`Status=Running`、`Scheduled Task State=Enabled`、`Next Run Time=N/A`（手动 Run 形态，无 Daily 触发） | `schtasks /Query /TN QS_DaemonForever /V /FO LIST` |
| 主库 | `data/quantstudio.db` **55.32 GB**，mtime 2026-10-29 16:14:35 | 文件属性 |
| WAL | `data/quantstudio.db.wal` **不存在**（= 0 字节） | 文件存在性 |
| 解释器 duckdb 版本 | `Python311`=**1.4.5**；`C:\python3.12.9`=**1.4.5**；`_runtime\venv_quant_studio`=**1.4.5**；`trading-battle-back\venv_miniQMT`=**1.5.3**（不升，登记只读消费者） | 逐解释器 `import duckdb` 探测 |

> 说明：daemon 进程 PID 与 2026-10-29 00:47 那一代（PID 41176）不同、启动时刻为 16:16:55，
> 说明期间有守护/计划任务再次拉起。**窗口实施必须以届时实态重新探测**（spec FR-014 / T002）。

## 三、隔离环境复核（T003）

| venv | duckdb | 用途 |
|---|---|---|
| `%TEMP%\venv_ddb156`（Python 3.11.9） | **1.5.6** | A-14 计量、F2 实验、后续升级前预演（**不需给生产解释器装包即可先量**） |
| `%TEMP%\venv_ddb155`（Python 3.11.9） | **1.5.5** | 事故版本复验 |

两 venv 均可用；**本轮未对任何生产解释器执行 pip 安装**。

## 四、F4 巡检守卫复核（T004）

```
> python scripts/check_passthrough_pk.py --check
SKIP: 库被占用（daemon 运行中），请在停服窗或副本上执行。
IOException: IO Error: Cannot open file "...quantstudio.db": 另一个程序正在使用此文件
exit=3
```

- `scripts/check_passthrough_pk.py`（5,810 B）与 `docs/evidence/pk_baseline.json`（3,966 B）**均存在**；
- 本会话首跑读数：`--check` 输出 **SKIP / 退出码 3**（IO Error：另一个程序正在使用此文件）——守卫**可用**；
- **④实测更正（2026-10-30）**：④在接入每日哨兵（第 4 步）时**三次实测**确认 `read_only` 连接可与 daemon 写连接**共存** ⇒ daemon 运行期 `--check` 可正常返回 **exit 0**。
  ⇒ 本节首跑的 SKIP 读数**不作废但不作通则**：两次读数差异说明「SKIP/3」是**特定时点的库独占态**（当时另有进程持有主库），而非 daemon 在跑时的必然结果。
  以④的三次一致实测为准；后续表述统一为「通常 exit 0；库被独占时 SKIP/3（非失败，不计失败数）」。
- **T004 关闭**：F4 已由④于 **2026-10-29 02:11** 接入总运维每日哨兵**第 4 步**并首轮全绿（接入说明与两处补丁见 `docs/handoff/duckdb-f4-sentinel-onboarding-20261029.md`）。

## 五、本轮实施期动作留痕

| 动作 | 状态 |
|---|---|
| 停/启 daemon | **未做**（Phase 2 纯代码改动，不需停服；窗口在 11-07/11-08，届时先声明占用并与 dev 线联办） |
| 装包 | **未做**（未对任何解释器执行 pip install） |
| 改主库 | **未做**（主库零写入；冒烟写入按 spec FR-014 只落独立临时库/独立文件） |

## 六、回滚路径核对（T023 · **备而不用，本轮未演练**）

| 项 | 核对结论 | 依据 |
|---|---|---|
| 降版命令 | `pip install "duckdb==1.4.5"`（**显式版本**）；**禁用** `pip install -r requirements.txt` —— 声明面已锁 `duckdb==1.5.6`，用 `-r` 会再升回 1.5.6 | quickstart §2 R2 |
| 旧版打开现库 + 抽样读取 | 降版后**同一解释器**只读打开现库，对 `stock_daily` / `trade_calendar` / `index_constituents_snapshot_meta` 执行 `SELECT count(*)`，无格式错误 | quickstart §2 R3 |
| 门禁复核 | 同一解释器跑 CLI ⇒ `PASS 1.4.5`（退出码 0），daemon 放行 | quickstart §2 R4 |
| 库异常路径 | `check_wal.ps1 -Force`（Windows）/ `check_wal.sh --force`（macOS/Linux）已具备该能力：ps1 有 `-Force` 开关（`check_wal.ps1:38`）、sh 有 `--force` 参数（`check_wal.sh:30`），两者均「跳过无需处理早退、强制执行收敛」 | 源码核对（本轮读全文） |
| 红线 | **禁手工删除 `.wal`**；运行期禁 CHECKPOINT（写者占用时脚本直接 fail 并给指引）；脚本移走陈旧 WAL 前已备份 | 铁律 23 + 两脚本注释 |
| 演练记录 | **未演练**：本轮 daemon 在跑（PID 32032），演练须停写者并触碰主库 CHECKPOINT ⇒ 属停服窗动作；窗口内如触发回滚，按上表逐条执行并把读数回填本文件 | — |
