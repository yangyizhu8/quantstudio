# CASE-008 客户事故归档：duckdb 混版统一（A 案 + 版本闸 + 主 venv 迁移，2026-09-23）

> 状态：**已闭环**（同批 21 笔上远程）
> 事件：GUI 启动卡死案（CASE-007）排查中发现**机器并存三个 duckdb 版本**，且「钉版」未覆盖实际运行环境
> 定谳：**钉版修复从未覆盖实际运行环境**——`pyproject` 双处 `duckdb>=1.4.5,<1.5`（issue duckdb#23645 回归防护）
> 仅落到 `Python311`（09-21 09:19）与 `python3.12.9`（09-23 17:40）；而 daemon 实际所用 `venv_miniQMT` = **1.5.3**、
> 官方主 venv `_runtime\venv_quant_studio` = **1.5.4** 均未覆盖；daemon 曾以 1.5.3 写主库两天
> 裁定① = **QuantStudio 侧版本闸（覆盖受支持入口）+ 连接级隔离约定**；trading 侧 venv 保持 1.5.3（read_only 消费）
> 裁定② = 今晚 daemon 由合规解释器（1.4.5）启动（已落地：pid 40096 / `python3.12.9` / WAL=0）
> 裁定 (c) = V8 两者都做：(b) 口径修正笔 + (a) 补装与冒烟

## 1. 全机环境地图（一页表）

| 解释器 / venv | py | duckdb | dist-info mtime | 说明 |
|---|---|---|---|---|
| `C:\python3.12.9\python.exe` | 3.12.9 | **1.4.5** | 2026-09-23 17:40 | 系统默认；17:40 补装（总调度裁定 (a) 授权、回补执行执行） |
| `...\Python311\python.exe` | 3.11.9 | **1.4.5** | 2026-09-21 09:19 | **官方解释器口径**（含全部依赖；GUI/daemon 实测可跑） |
| `D:\...\_runtime\venv_quant_studio` | 3.11.9 | ~~1.5.4~~ → **1.4.5** | 2026-07-19（原装）/ 09-23 降级 | 官方 `activate_venv.bat` 指向的隔离环境（V8 已降级+补装） |
| `D:\...\trading-battle-back\venv_miniQMT` | 3.12.9 | **1.5.3** | 2026-05-31 | 09-21~09-23 daemon 实际所用（写主库两天）；**按裁定保持不动** |
| 其余 20 个 venv | 3.11.9/3.12.9 | 无 duckdb | — | 与主库无交互面 |

`py -0p` 仅注册两个系统解释器：3.12.9（默认）与 3.11。

## 2. 关键取证（含取证等级）

| 项 | 结论 |
|---|---|
| 钉版落点 | 仅 `Python311`（09-21 09:19，早于钉版提交 `5913785` 12:55）与 `python3.12.9`（09-23 17:40） |
| **daemon 实际版本** | **环境推定（强）1.5.3**：`daemon_status.json` 记 `exe=...venv_miniQMT...`，该 venv 的 `duckdb-1.5.3.dist-info` mtime（05-31）早于 daemon 启动（09-21 15:44）。**无直接证据**（日志无版本记录）→ 标注为环境推定，非运行期自证 |
| trading 依赖面 | 实盘主链（`main.py` + `core/`）**零 duckdb、零主库**（数据源 = QuestDB→xtdata→MySQL）；**同 venv 的 `OSkhQuant` 子系统有 duckdb**（`duckdb_provider` read_only + `小市值策略_khQuant.py` 硬编码 read_only 读主库）→ 按裁定「发现依赖即受阻、**禁止强降**」**未强降** |
| 隔离约定附注 | **read_only 读者打开时会回放 WAL**（CASE-007 副本实验实证）→ OSkhQuant 读主库须**避开 daemon 写入高峰** |

## 3. 版本闸（笔5）与连接级隔离约定

- **闸门**：`quantstudio/pipeline/duckdb_version_gate.py` —— `check_duckdb_version()` / `require_duckdb_version(component)`
  （非 1.4.x → `SystemExit(3)` + 打印修复指引）；逃生阀 `QS_DUCKDB_VERSION_GATE=0`（显式放行 + 醒目警告）。
- **覆盖入口**（实施期实测界定）：`main_gui.py`（GUI）· `-m quantstudio.pipeline.daemon` ·
  GUI 拉起的 daemon 子进程（等价 `-c "from quantstudio.pipeline.daemon import main; main()"`）· `scripts/activate_venv.bat`。
  **「直启文件形态」结构上不支持**（相对导入），已如实更正并回退无用引导。
- **V7 真实触发**：`venv_miniQMT`（1.5.3）三入口**全部拒启 exit 3**；合规解释器不受影响。
- **连接级隔离**：写侧仅 1.4.x 可启动、daemon 为唯一写者并轮次收尾检查点；读侧 trading `venv_miniQMT` 保持 1.5.3、
  限定 read_only 消费、避开写入高峰（依据：1.5.3 读 1.4.5 写的库向后兼容成立，双向已实证）。

## 4. V8：主 venv 迁移（裁定 (c)）

| 步骤 | 结果 |
|---|---|
| 降级前快查 | 扫全部 dist-info `Requires-Dist: duckdb` → 仅 `quantstudio-0.1.0`（`>=0.9.0`）；`pip check` 无破损 → **安全** |
| 降级 | `pip install "duckdb>=1.4.5,<1.5"` → **Successfully installed duckdb-1.4.5** |
| 补装（(a)） | `PyQt6-Fluent-Widgets 1.11.3`、`psutil 7.2.2`、`pyarrow 25.0.1`（matplotlib 3.11.0 已有） |
| GUI 冒烟 | **PASS：窗口「QuantStudio 数据管线控制台」1.76 s 出现** |
| psutil 自愈路径 | **PASS**：psutil 7.2.2 → 死 pid 锁判 `stale_dead`（`v2_local_dead`）→ 回收成功 → 锁移除 → 审计 1 行（隔离目录，未触生产锁） |
| 口径修正（(b)） | `scripts/activate_venv.bat`：**官方解释器 = Python311**；说明该 venv 为隔离环境及其现状；记录修正原因（原帮助文本失实：当时缺 qfluentwidgets/psutil → 既跑不起 GUI，也使批一写锁自愈 fail-closed） |

## 5. 归档信息

- 卷宗编号：**CASE-008**
- 证据指针：`docs/evidence/duckdb-version-environment-map-20260923.md`（环境地图 + 定谳 + §6 今晚口径落地闭环
  pid 40096/`python3.12.9`/1.4.5/WAL=0）、`docs/gui-startup-nonblocking-design.md` §3-⑥/§4.4/§10
- 涉及提交：`b429f98`（笔5 版本闸）、`0089425`（笔6(b) 口径修正）、`ea4bca1`/`5ee3149`/`000b5bb`（证据与台账）
- 三方核对：`000b5bb`（local = origin/main = 双远程）
- 关联案：**CASE-007**（GUI 启动卡死，同批交付）
- 残留注记：`venv_miniQMT`（1.5.3）按裁定保持不动（read_only 消费 + 避开写入高峰）；若未来 trading 需写主库，须重议隔离方案
