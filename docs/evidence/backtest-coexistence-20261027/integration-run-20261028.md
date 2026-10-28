# 集成验证实录 · 2026-10-28 凌晨

**Feature**: `specs/001-daemon-backtest-coexistence` | **执行人**: 用户（终端 + GUI），分析: DSH
**环境**: Windows / Python 3.11.9 / DuckDB 1.4.5 | **策略**: `全球资产轮动.py`（116 交易日，2026-01-05~06-30）

---

## 一、阶段 A · 无采集时跑回测（验证 F-4 修复）

| 项 | 结果 |
|---|---|
| 命令 | `python -m quantstudio.backtest.run_ptrade_strategy 全球资产轮动.py 2026-06-01 2026-06-30` |
| 时间 | 02:23:25 ~ 02:23:41（21 交易日） |
| `[resolve]` | ✅ 生效（裸名 → `strategies\全球资产轮动.py`） |
| `[coexist] 采集已停止` | ✅ **未出现**（F-4 修复生效：无 daemon 时 `request_stop` 直接返回 False） |
| `data/backtest_stop.flag` | ✅ 未被创建 |
| 结论 | **F-4 修复验证通过** |

## 二、阶段 B · 采集开启时跑回测（**未通过，暴露 F-5**）

| 项 | 结果 |
|---|---|
| 时间 | 02:25:58 ~ 02:27:00（116 交易日，完成） |
| `[coexist] 已等待 0s · 等待采集收尾（任务边界）` | ⚠️ **等待 0 秒 → 等待被跳过** |
| `[coexist] 采集已停止（原因：回测）` | 出现（说明 `request_stop` 返回 True ⇒ 当时 status 存在且 pid 存活） |
| 回测结果 | 正常完成，净现值 104,026.55（与阶段 A 同参结果一致） |
| 判定 | **不算 T018/T023 证据**：没有真实等待，"停得掉"的时序未被验证 |

**F-5 根因**：`backtest_window.daemon_alive()` 把 `psutil.AccessDenied` 当成"已退出"。
高完整性 daemon（`start_daemon_guarded.ps1` 提权启动）的 `create_time()` 读不到 → 返回 False
→ `wait_daemon_exit` 立即结束 → 回测直接抢库。

**修复**：`AccessDenied` ⇒ 用 `pid_exists` 兜底返回 True（保守：宁可多等，不可误判已退）。
新增 3 项测试（denied→True / NoSuchProcess→False / PID 复用→False），`test_backtest_window_protocol.py` 22 passed。

## 三、T037 · 双端产物逐位一致 ✅

| 文件 | CLI `20261028_022700_…` | GUI `20261028_025030_…` | 一致 |
|---|---|---|---|
| benchmark.csv | 5d9dab86e595bdc3… | 5d9dab86e595bdc3… | ✅ |
| config.csv | 13567136c879df7e… | 13567136c879df7e… | ✅ |
| daily_stats.csv | 7fdada5dc8a80bb8… | 7fdada5dc8a80bb8… | ✅ |
| ptrade_metrics.csv | fa9fcaabd41ec627… | fa9fcaabd41ec627… | ✅ |
| ptrade_metrics.json | 2f292023f791aab5… | 2f292023f791aab5… | ✅ |
| round_trips.csv | f01a374e9c81e3db… | f01a374e9c81e3db… | ✅ |
| trades.csv | e22a87248518dc86… | e22a87248518dc86… | ✅ |

**7/7 一致（SHA256 全等）**；GUI 额外 6 张 PNG（`回测收益曲线/绩效分析-收益分布/绩效分析-月度收益热力图/基本信息/交易记录/日收益`）属 GUI 报告导出，非数据产物，预期差异。

→ **T037 通过**（SC-008 ✅）。

## 四、T028 · 数据不可用时显式拒绝 ✅

| 项 | 结果 |
|---|---|
| 构造 | 终端1 `duckdb.connect(data/quantstudio.db)` 持库 60s；终端2 跑回测 |
| 实际输出 | `❌ DuckDB 打开失败: IO Error: Cannot open file … 另一个程序正在使用此文件（PID 38204）`<br>`❌ 数据未就绪，请先完成数据拉取后再运行回测（退出码 3）` |
| 结果目录 | ✅ 未产出 |
| 判定 | **通过**（SC-003 的"空数据静默 = 0"在此场景成立）；<br>注：触发点是既有的 `_check_data_readiness` 前置闸，我们的 `_precheck_db_available` 未被走到——两者语义等价（都属显式拒绝） |

## 五、结论与待办

| 任务 | 状态 |
|---|---|
| T037 双端产物一致 | ✅ 可勾（证据：上表） |
| T028 空数据/不可用拒绝 | ✅ 可勾（证据：上表；样本 1 次，SC-003 要求 ≥10 次，故 SC 仍待补样本） |
| F-4（status 残留写假停止） | ✅ 已修并验证 |
| F-5（AccessDenied 误判退出） | ✅ 已修，**待重跑阶段 B 验证** |
| T018/T023/T033/T044 | ⏳ **需重跑阶段 B**（采集真在跑 + 有真实等待） |
| T042 逐位一致 | 已有两侧（阶段 A/B 同参同结果），待与窗口那次统一比对 |
| T043 数据完整性 | ⏳ 需让路后重跑采集 |


---

## 六、03:03 复跑 · 修复后首验（F-5 生效）

| 项 | 读数 |
|---|---|
| daemon 启动 | 03:02:34（pid 30284，status=running） |
| 发起回测 | 03:03:29（写 stop.request reason=backtest） |
| **daemon 收到停止请求并退出** | **03:03:29**（daemon.log：`收到停止请求（reason=backtest），准备优雅退出` → `已清理本进程 status 文件`） |
| 回测开始 | 03:03:30 |
| 回测结束 | 03:03:42（21 交易日） |
| 等待读数 | `[coexist] 已等待 0s · 等待采集收尾（任务边界）`（真实等待 ≈1s；`:.0f` 格式化所致，已改 0.1s 精度） |
| 5 分钟后 | `daemon_status.json` 不存在、pid 30284 不存在 → **未复活** |
| `backtest_stop.flag` | 存在，`reason=backtest, at=03:03:42`（回测进程写） |
| 启动采集后 flag | 已被 `run_forever` 开头清除（用户 `Test-Path` 为 False） |

**判定**：T018 ✅（免手动停成功）、T033 ✅（不复活）、T046 ✅（启动清 flag）；
T023 ⚠️ 部分（读数真实出现，但 daemon 空闲时停止 <1s，未观察到"递增"——需在采集轮内复验）。

**纠正记录**：02:58 那次我曾误判"等待被跳过"。实际 `wait_daemon_exit` 是先判存活、False 即 return 且**不回调**；
出现读数即证明第一次判定为真。故 0s 属"停止太快"，不是缺陷。显示精度已修为 0.1s，避免再次误读。

## 七、T042 · 场景 G 逐位一致 ✅

| 文件 | A：采集未运行（02:23） | B：开启→自动停（03:03） | 一致 |
|---|---|---|---|
| benchmark.csv | 73319a0e408f506a… | 73319a0e408f506a… | ✅ |
| config.csv | c0ed57122d527631… | c0ed57122d527631… | ✅ |
| daily_stats.csv | da0c870b732b49ac… | da0c870b732b49ac… | ✅ |
| ptrade_metrics.csv | 55a73ef72347bced… | 55a73ef72347bced… | ✅ |
| ptrade_metrics.json | 4ef89ceb5c209818… | 4ef89ceb5c209818… | ✅ |
| round_trips.csv | f01a374e9c81e3db… | f01a374e9c81e3db… | ✅ |
| trades.csv | 0fdd40e76b887680… | 0fdd40e76b887680… | ✅ |

**7/7 一致**（同参 2026-06-01~06-30）→ T042 通过（SC-004 ✅）。

## 八、本轮汇总

| 任务 | 状态 | 证据 |
|---|---|---|
| T018 免手动停采集 | ✅ | 03:03 全程用户未手动停 |
| T028 不可用显式拒绝 | ✅ | 库被占 → 退出码 3、无产物 |
| T033 停止后不被拉起 | ✅ | >5 分钟 status/pid 均无 |
| T037 双端产物一致 | ✅ | 7/7（116 天） |
| T042 场景 G 逐位一致 | ✅ | 7/7（21 天） |
| T044 中断÷回测比值 | ✅ | 停止 03:03:29 → 回测结束 03:03:42 = 13s / 回测 12s ≈ 1.08（落 0.9–1.1） |
| T046 启动清留痕 | ✅ | 启动后 flag=False |
| T023 读数递增 | ⚠️ 部分 | 读数真实（≈1s），"递增"需采集轮内复验 |
| T043 数据完整性 | ⏳ | 需让路中断后重跑采集比对 |


---

## 九、T023 决定性证据（09:56 长轮让路 · F-6 修复后首跑）

- 撞上运行中的采集轮（collector_run.lock 被持有）→ F-6 修复生效（跳过前置检查进入共存流程）
- 读数 60 条：0.0s → 59.1s **单调递增**；30s 处**阶段升级**（任务边界 → 已请求中断当前任务）
- 60s 停止超时 → **exit 4**（契约退出码），CLI 提示加大 --coexistence-stop-timeout
- daemon 侧（F-7 观测）：09:56:07 **7 秒完成单元边界中断**（unit=35 已完成/25 剩余、水位 hold 保持、
  审计中止并标注）→ 收尾 11 分钟 → 10:07:14 干净退出
- **判定：T023 PASS；SC-006 全量兑现；停止超时保护路径真实场景验证**

## 十、T043 最终判定（16:03 · PASS）

- 基线 10:22:36（stock_daily 636,261 / etf_daily 225,224 / index_daily 3,596 / trade_calendar 181）
- after 16:03:11（历经：回测让路 → daemon 重启 → 5.7h 数据工程大轮：分钟线建基线 + QFQ 清障 4575 单元）
- **硬判据（行数/日期范围）：4/4 表逐项一致 → 让路未造成历史区间丢行/漏写，SC-011 成立**
- 软差异仅 1 处：stock_daily.sum_amount 317,527,109,226,524.75 → …524.7（相对差 ~1.6e-16，
  **纯浮点求和噪声**；sum_close 完全一致 ⇒ 历史 close 零改写）
- **判定：T043 PASS**

## 十一、验证总账（2026-10-28 收）

| 任务 | 判定 | 关键证据 |
|---|---|---|
| T018 免手动停 | ✅ | 空闲期（03:03）+ 长轮内（09:56）双场景 |
| T023 读数递增 | ✅ | 60 条递增 + 阶段升级 + exit 4 保护 |
| T028 不可用拒绝 | ✅ | exit 3 无产物（既有闸）+ F-6 后共存流程内复检 |
| T033 不复活 | ✅ | >5min 未复活 |
| T037 双端一致 | ✅ | 7/7 SHA256 |
| T042 逐位一致 | ✅ | 7/7 SHA256 |
| T043 数据完整性 | ✅ | 硬判据 4/4；唯一软差异为浮点噪声 |
| T044 中断比值 | ✅ | 1.08 |
| T046 启动清留痕 | ✅ | flag 被清 |
| 修复 | F-1~F-6 已修+测试；F-7 观测归档 | 62+ passed |


## 十二、T039 全量回归（16:10–16:44）

- 全量：**3937 passed / 67 failed / 4 skipped / 8 xfailed**（2027.7s）
- 本 feature 域内测试：**80/80 全绿**（window_protocol / empty_data_guard / identity_denied /
  yield_boundaries / gui_backtest_timer / daemon_gui_start_paths）
- 67 个失败归因：全部位于与本 feature 改动无交集的模块（fin_growth/phase4_minute/
  source_import/validator/qfq 族/ptrade_api/gui_rebalance 等）；同域精查 3 文件 2 过 1 败，
  唯一败因 = 已提交代码 B4（2026-10-23，daemon.py task_deferred 键）与测试断言不同步——
  **既有失败，非本 feature 引入**（daemon.py 无未提交改动，git 证）。
- 纯增益判定：本 feature 零回归。

## 十三、终态（2026-10-28 16:45）

- 任务：46 项中 44 done（T039/T040/T041 本节收口）/ 2 项移交后续（见下）
- 对账表终态：17 ✅ / 5 ⏳ / 3 ➖ —— 5 项待实测 = A1（强杀路径 E1–E4）、B1（任务类型实测）、
  B4（跨周期）、G1（仅剩 SC-013 GUI 三选项）、SC-003 样本量（≥10 次）；均非代码缺口，属后续验证窗口
- T038 文档同步：specs 内五件套 + fix.md + 证据 + 对账表已全部同步；
  主 README 留待⑥统推窗一并处理（避免客户可见文档中途态）
- 代码面：Converged（F-1~F-6 修复全带测试，F-7 观测归档）
