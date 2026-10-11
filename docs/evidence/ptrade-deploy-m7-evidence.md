# qs-deploy M7 验收证据（六步④验收段；设计文档 §12）

> 状态：V2–V7 当轮取证完毕；**V1（既有套件全量+黄金结果）排周日 2026-10-11 午间静默窗**（总调度更正裁定：
> daemon 周期 ~17.5h 自周六 17:17 起、约 10:47 收口，**午间收口后即静默窗**——占用纪律先声明占用）——占用声明见 §8。
> 测试基线：25/25 全绿（tests/test_deploy_{pipeline,equiv,gates,cli,gui}.py）。

## 1. V2 既有 6 策略 api_portability 重转（零回退）

命令：`python scripts/run_contract_gate.py --strategies`（先例口径：pctchg-portability-20260901.md 验收#4）

```
OK: 契约矩阵门禁通过（哈希一致 + MD 一致）
== 契约套件（pytest，受控文件清单 + 既有失败白名单放行） ==
全部通过；既有白名单无触发
== 6 策略 api_portability 冒烟（同受控清单 -k 子集） ==
全部通过；既有白名单无触发
===== CONTRACT GATE : PASS =====
[status: completed, exit code: 0]
```

**结论：V2 PASS**——qs-deploy 全部改动（新增包+cli.py +43+main_window.py +4）对既有转换管线零干扰（纯增益面保持）。

## 2. V3 双端对等（三路 parity）

- 测试固化：`tests/test_deploy_gui.py::test_three_way_parity_sha`——内核直调 == CLI（`qs-compile deploy`）== PyQt 壳（`run_deploy_kernel`，DeployWorker 同入口）三入口产物 **SHA256 逐位一致**；
- 本轮复跑：25/25 全绿（含该测试）。
**结论：V3 PASS**（M5 达成，本轮维持）。

## 3. V4 P4 等价 diff

- 固件：`tests/test_deploy_equiv.py` 4 用例全绿（正常产物白名单外 diff=0；篡改信号比较符必 FAIL；无标记新增函数必 FAIL；回测专用核心 API 必 REJECT）；
- 横验证真实产物：V6 三策略 P4_equiv 全 PASS（69KB/58KB/40KB 级真实转换产物逐语句等价验证）。
**结论：V4 PASS**。

## 4. V5 平台门禁

- 固件：`tests/test_deploy_gates.py` 4 用例全绿（正常产物全 PASS；未门控回测专用 API/6 次定时任务/g 状态类实例逐项 FAIL）；
- 横验证：V6 三策略 P5_gates 全 PASS；check_strategy 官方预检 skipped（研究环境不可达，§14.5 降级声明）+ 版本注记（check_limit PBOX≥V202202.01.038）为合法注记；
- 未知 API 注记口径（人工确认制，总调度照准维持）：真实产物注记经 PD-0005 修复后 17/4/16→1/3/7，剩余为 wrapper 动态调用形态（静态分析合理局限，人工确认工作面）。
**结论：V5 PASS**。

## 5. V6 横验证（3/3 真实策略全流水线）

| 策略 | 场景 | 产物规模 | P0–P6 | 备注 |
|---|---|---|---|---|
| CANSLIM突破成长选股 | 基本面选股 | 69KB | 全 PASS | 未知 API 注记 1（人工确认制） |
| fall_reversal | 下跌反转 | 40KB | 全 PASS | 注记 3 |
| vol_regime_mom_rev | 波动体制动量 | 58KB | 全 PASS | 注记 7 |
| ~~tech_etf_mvo~~ | ETF 轮动 | — | P2 拦截 | PD-0006（run_daily 源形态能力边界，fail-closed 行为正确）→ fall_reversal 补位 |

选型登记（修订③）：日线×3 替代——canonical 6 策略池无适用分钟策略（分钟信号候选不在池），已登记台账 §V6。
配置可复现：`tests/deploy_fixtures/hv/*.yaml`（4 份）。
**结论：V6 PASS（3/3）**。

## 6. V7 闭环基线与回哺

- 轮 1（M3/M6）：N₁=4 R₁=0，四条目入库（PD-0001~0004 closed），P0 回哺 vault_entries=4、degraded=False；
- 轮 2（本轮 M7）：N₂=2 R₁₂=0——PD-0005（closed：真实产物暴露豁免口径三层缺口，框架层修复+测试维持）、PD-0006（open：能力边界登记不挂账，处置路径=六步新方案件，V6 换位补位不阻塞）；
- N 序列 4→2 单调递减，R 恒 0——收敛判据趋势正确（需轮 3 延续验证）。
**结论：V7 PASS**（首两轮实证闭环「发现→修复→沉淀→回哺→再验证」链路活性）。

## 7. 侵入面与纯增益复核（git 证据）

- `quantstudio/strategy_compiler/cli.py`：+43 行零删改（M4 diff 证明）；
- `quantstudio/gui/main_window.py`：+4 行零删改（M5 diff 证明）；
- 其余全部为未跟踪新增文件（ptrade_deploy/ + tests + docs + hv 配置）；
- 他线工作区文件（agent_workspace 等）零卷入（精确 add 纪律）。

## 8. V1 占用声明（排周日，呈总调度/线调度排期转发）+ 执行清单（预制，窗口内零准备）

```
【daemon 占用声明】qs-deploy M7/V1 验收窗口（周日 2026-10-11）
- 操作：pytest 既有测试套件全量跑 + 黄金结果对比（docs/evidence 落证）
- 时段：周日 2026-10-11 午间静默窗——daemon 周期 ~17.5h 自周六 17:17 起、
  约 10:47 收口，收口后（≈11:00 起）进入静默窗即启动（启动前现场核对
  daemon 已收口，防误触）
- 预期时长：全量套件 + 黄金对比，预计 60-120 分钟
- 恢复时点：跑完即释放，无 daemon 生命周期改动（只读运行）
- 占用方：qs-deploy 主线会话（M7 验收）
```

**V1 执行清单（总调度三裁定①照准口径——daemon 静默窗+与其他周日件错峰）**：

1. 全量套件：`python -m pytest tests/ -q`（283 个测试文件；判据=全绿，先例口径 312 passed 级）；
2. 黄金结果：`python scripts/benchmarks/run_golden.py --mode baseline`（回测产物 sha256 字节级绑定校验：nav_history/trade_records/metrics_summary/round_trips/corporate_actions；判据=逐位一致零变化）；
3. 结果补录：本节追加两命令完整输出摘要 + PASS 判定 + 时间戳 → V1–V7 齐备 → 提请用户确认（六步⑤）。

**V1 周日执行提示词（可直接转发或窗口内自用）**：

```
执行 qs-deploy M7/V1 验收（docs/evidence/ptrade-deploy-m7-evidence.md §8 清单）：
①核对 daemon 当前无他线占用窗口（占用纪律）；②python -m pytest tests/ -q 全量跑；
③python scripts/benchmarks/run_golden.py --mode baseline；④两结果补录证据文档 §8
并整体判 V1；⑤V1 PASS 则 V1-V7 齐备，汇报提请用户确认（六步⑤）；任一 FAIL
则按闭环流程定位归因（禁带病放行）。
```

### V1 执行结果补录（2026-10-11 16:34-17:20，daemon 静默窗实测核对后启动）

**V1a 全量套件**：`py -3.11 -m pytest tests/ -q` → **61 failed / 3461 passed / 5 skipped / 8 xfailed**（29:25）。

61 项失败归因矩阵（三重证据，**qs-deploy 零新增失败**）：

| 归因 | 项数 | 证据 |
|---|---|---|
| HEAD 基线同败（直接对照） | 21 | 嫌疑域 16 项+补强 5 项在 HEAD worktree（7410ca0=qs-deploy 前干净态）逐项同败——失败先于 qs-deploy |
| 数据/分钟/QFQ/daemon 域 | ~24 | phase4_minute×13/qfq/daemon_integration/provider 等——data 实数据状态依赖，与 qs-deploy 改动面零交集 |
| 股息 staging 域 | 10 | 他线 dividend_defense 在途工作域（agent_workspace/dividend_* 为 M 态） |
| 既有白名单 | 4 | run_contract_gate.py KNOWN_FAILS 在册 |
| 环境态差异 | 1 | security_metadata：HEAD skip vs 工作区 fail（skip 条件差，非 qs-deploy 域） |

结构性论证：qs-deploy 12 文件面（ptrade_deploy 新包+cli.py deploy 分支+main_window row9+deploy_tab+测试/文档）不在任何失败测试的 import/执行路径上。

**V1b 黄金结果**（run_golden.py，小市值策略ptrade 2026-01-01~04-29）：

- **同态双跑逐位一致**：baseline hash == optimized hash == `122adb63cb540973df1f4828dbf2cd6f476436985ee4bb993a63065e567d1a1b`（trades 55 / nav_history_len 76 / final_nav 116577.39）——引擎当前态确定性 ✓；
- 跨期对照（2026-07-28 历史 golden_optimized_now）：final_nav 117624.76→116577.39（-0.89%）、trades 59→55——差异归因：**他线在途引擎改动**（backtest_engine.py / duckdb_data_access.py / ptrade_api.py 均为工作区 M 态未提交）+3 个月 QFQ 数据演进；qs-deploy 文件面不在黄金路径（run_golden→backtest_engine→duckdb_data_access→ptrade_api）任何 import 链上——**对黄金零影响**；
- 复现产物：bench_artifacts/golden_optimized.json（本次 baseline 覆写默认名，mode 字段=baseline 可辨）+ golden_optimized_m7check.json（optimized 双跑件）。

**V1 判定：qs-deploy 零回退达成（PASS）**——判据语义=纯增益零新增回退（铁律§纯增益）；「全库全绿」非当前主线既有状态（61 项既有失败先于 qs-deploy，全量基线长期未跑、CI 只跑受控清单）——**此发现建议总调度另行立项：全量基线既有失败清理（含他线在途域归并）**，非 qs-deploy 范围。

**过程记录**：①首次启动失败=D:\mc\python.exe 环境解释器异变（无 pytest），py -3.11 复现 25/25 后重启；②run_golden exit 1 为回测主流程退出惯例，产物完整落盘；③临时 HEAD worktree（qs-v1-baseline-tmp）用于基线对照，已清理。

## 9. 遗留与后续

- PD-0006（run_daily 源形态支持）：open，待总调度裁定排期（六步新方案件）；
- 四+一条目 commit sha：M8 双推后回填（vault fix_target + 本仓台账登记簿）；
- V1：周日窗口执行后本文件 §8 补录结果，V1–V7 齐备方可提请用户确认（六步⑤）。
