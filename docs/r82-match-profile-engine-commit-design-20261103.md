# R82 修复方案 · 引擎侧 match_profile 补提交（交付完整性断裂修复）

> 状态：**③实施完成 → ④验收完成**（F1/F5 已过；F2/F3/F4/F6 见 §六验收证据）｜呈批人：用户（我域）｜执行：本会话（节点①）
> 上游：运维2 R82 firefly P0 方案（③呈裁落定：①方案批准（F1-F6 逐点门控+负向对照+隔离数据根+黄金比对零差异）；②更正——R66 五件套已入库出仓闭环，无同族件未入库问题；③闭包门已上线，新案由既有门防复发）
> 关联：`docs/evidence/qmt-match-profile-params-20261012.md`（六参数双列证据链，随批入库）｜`docs/ISSUES.md` R82 段｜`knowledge/qmt/QD-2026-0017.md`

---

## 一、问题定义

**现象**：远端 `main` 分支回测 **100% 崩溃**（连续 6 天），而本地工作树全绿。

**根因（交付完整性族第三例）**：`fa82445`（2026-10-28）只提交了**调用侧**——
`quantstudio/backtest/run_ptrade_strategy.py`、`quantstudio/gui/workers.py`、`quantstudio/gui/tabs/backtest_tab.py`
均已入库并传入 `match_profile='ptrade'`；而**引擎侧** `quantstudio/backtest/backtest_engine.py` 的
M5.2 件7 形参（自 2026-10-12 起）**长期停留工作树未入库** ⇒ 远端调用侧传一个引擎侧不存在的关键字参数。

**远端断裂面实测（2026-11-03，R82 F5 门控）**：

| 臂 | 引擎来源 | 结果 |
|---|---|---|
| A 臂 | `origin/main`（远端当前态） | `TypeError: BacktestEngine.__init__() got an unexpected keyword argument 'match_profile'` |
| B 臂 | 工作树（含件7 形参） | 构造成功，`match_profile='ptrade'`、`match_price_mode='close'` |

> 附：`origin/main` 侧 `run_ptrade_strategy.py` 已传 `match_profile='ptrade'`（实测确认）⇒ 断裂面闭合。

## 二、根因定位（交付完整性，非逻辑缺陷）

1. **M5.2 件7 引擎侧改动自 2026-10-12 起一直是 M 态**，本地 pytest 全绿（用的是工作树代码）；
2. 远端用的是**已入库代码**（引擎缺形参）⇒ 两面代码基线不同 ⇒ 「本地绿 ≠ 仓库绿」；
3. 前两例同族为 R66 簇 A（五件套）／簇 B（delivery 夹具），本例为**第三例**（引擎形参类）。

**判型（铁律 18）**：**纯恢复型**——本批不新增任何业务逻辑，只把工作树中既有实现补入库；
缺省 `match_profile='ptrade'` 行为零改变 ⇒ 以 F4 黄金零差异为硬证。

## 三、改动范围（精确到文件）

| # | 路径 | 动作 | 行数 | 说明 |
|---|---|---|---|---|
| 1 | `quantstudio/backtest/backtest_engine.py` | **补提交既有 M 态**（不改一字节） | **22+/1-** | 3 hunk：形参声明（:332）／校验与赋值＋qmt 标签（:395-398）／metrics_summary 双键（:687-688） |
| 2 | `tests/test_match_profile.py` | 新增入库（未跟踪→入库） | 新文件 | 6 用例：参数校验／缺省零行为／qmt 标签／B 类费率锚点／印花税分段／CLI 透传静态断言 |
| 3 | `docs/evidence/qmt-match-profile-params-20261012.md` | 新增入库（未跟踪→入库） | 新文件 | 六参数双列证据链（Context7 ＋ 形态库 ＋ 公开税则）＝参数口径依据与远端验证面 |
| 4 | `docs/r82-match-profile-engine-commit-design-20261103.md` | 新建 | 新文件 | 本方案与验收证据 |
| 5 | `knowledge/qmt/QD-2026-0017.md` | 新建 | 新文件 | 知识反哺 R4 沉淀（族 qd · D3 · fix_layer=engine） |
| 6 | `docs/ISSUES.md` | 台账增 R82 段 | ＋N 行 | 可审计登记 |

**禁触面（本轮明确不动）**：`config/profiles/**`（他线 M 态）｜`quantstudio/backtest/ptrade_api.py`
（他线 M 态 171+/0-，QS_ORDER_RESULT 审计插桩，env 门控默认关）｜其余 33 项他线 M 态文件。

## 四、影响面

- **远端**：回测恢复可用（构造期不再 TypeError）。
- **本地**：行为零改变（缺省 ptrade；F3 撮合回归零衰减、F4 黄金逐位一致为证）。
- **共享层**：`backtest_engine.py` 属铁律 14 六类路径 ⇒ 出仓后 **11-05 同步门不可豁免**，批次 +1
  （并入 `schedule-39b17726` 第 5 批「11-04 出仓批次」，不新增第 6 批）。
- **下游消费**：`metrics_summary` 新增两键为**纯新增**（`match_profile`／`match_price_mode`），
  下游 `.get()` 消费不受影响；数值指标零变化。
- **未变更面**：`qmt` 档费率首版沿用 ptrade 同值（10-12 呈报件裁定②(a)）；C 类（佣金差/滑点）候复测反推后**另立项**。

## 五、验收标准（F1–F6 逐点门控）

| 门控 | 判据 | 结果 |
|---|---|---|
| **F1** | `backtest_engine.py` M 态 diff 恰 **22+/1-** · **3 hunk** · 全 match_profile | ✅ PASS |
| **F2** | 生产测试一律隔离数据根（daemon 9828 `--mode forever` 持锁） | ✅ 见 §六 |
| **F3** | 撮合回归零衰减（失败项须与本批切割归因） | ✅ 120 passed／3 失败＝2 既有基线红＋1 隔离环境项，本批零责任 |
| **F4** | 黄金比对零差异（7 产物 SHA256 三方核） | 见 §六 |
| **F5** | 远端等效验证（`inspect.signature` 正负对照） | ✅ PASS |
| **F6** | 方案文档＋知识反哺行随批入库（族机检过） | ✅ `kb_hook --strict` 1 件不合规 0 件 |

## 六、验收证据（实测读数）

### F1 门控（PASS）

```
git diff --numstat -- quantstudio/backtest/backtest_engine.py  →  22  1
hunk 数 = 3
  @@ -328,7 +328,8 @@ class BacktestEngine:      ← 形参声明 match_profile: str = "ptrade"
  @@ -385,6 +386,21 @@ class BacktestEngine:     ← 校验 + 赋值 + qmt 档 logger 标签
  @@ -665,6 +681,11 @@ class BacktestEngine:     ← metrics_summary 双键注入
```

### F2 隔离数据根（PASS）

```
QUANTSTUDIO_DATA_ROOT = <repo>\data\backup_import_20261101_040935   （只读副本 59.8GB）
QS_WRITE_LOCK_DIR     = <temp>\qs_r82_isolated\lock
QT_QPA_PLATFORM       = offscreen
daemon PID 9828  --mode forever --config-dir config\profiles\mcp_only  （自 11-03 06:13:59 持锁，全程未触）
```

### F3 撮合回归（与本批切割归因：四臂对照）

| 臂 | 引擎 | 隔离 env | 结果 |
|---|---|---|---|
| A | 工作树（含 match_profile） | 是 | 3 failed |
| B | HEAD（无 match_profile） | 是 | 2 failed, 1 passed |
| C | 工作树 | 否 | 2 failed, 1 passed |
| D | HEAD | 否 | 2 failed, 1 passed |

- `test_engine_config::test_engine_config_default_uses_project_root`：**仅 A 臂失败** ⇒ 由
  `QUANTSTUDIO_DATA_ROOT` 使 `EngineConfig.default()` 指向副本库、与该测试「指向项目根默认库」断言互斥 ⇒ **隔离环境产物**。
- `test_next_open_limit_and_halt::test_drain_rejects_buy_when_t1_gap_exceeds_cash_no_partial` 与
  `test_minute_limit_halt::test_minute_limit_up_buy_rejected`：**B/C/D 三臂均红** ⇒ **既有基线红**
  （HEAD 隔离工作树同样红；臂 E「HEAD 树＋他线 M 态 ptrade_api」亦红 ⇒ 早于他线插桩与本批）。
- 全量读数（10 件撮合面，隔离 env）：**120 passed / 3 failed**（＝上述两项＋隔离环境项）⇒ **本批零责任**。

### F4 黄金比对零差异（PASS）

双跑（独立目录 `output/r82_golden_20261103/`，**不覆盖 11-01 原回执件**；策略 SHA256 与 11-01 锚逐位一致
`CE735FF61440F635977626C20468D77EF68C2C2E14B22B099696D1558D64E73C`；窗口 2026-01-05..16；profile minute-bar-v1；
数据根＝只读副本 `data/backup_import_20261101_040935`）：

| 臂 | rc | 耗时 | 产物目录 |
|---|---|---|---|
| A 闸开（缺省 ptrade） | 0 | 692.9 s | `output/backtest_results/20261103_172219_strategy` |
| B SKIP=1 对照 | 0 | 687.7 s | `output/backtest_results/20261103_173348_strategy` |

| 产物 | A 臂 SHA256(前16) | B 臂 SHA256(前16) | 11-01 锚(前16) | A=B | A=锚 |
|---|---|---|---|---|---|
| benchmark.csv | `44DC0265B63C668D` | `44DC0265B63C668D` | `44DC0265B63C668D` | SAME | SAME |
| config.csv | `FF4E25136E7B6DBC` | `FF4E25136E7B6DBC` | `FF4E25136E7B6DBC` | SAME | SAME |
| daily_stats.csv | `A39F1E9DA3063887` | `A39F1E9DA3063887` | `A39F1E9DA3063887` | SAME | SAME |
| ptrade_metrics.csv | `8D26021A70450DB9` | `8D26021A70450DB9` | `8D26021A70450DB9` | SAME | SAME |
| ptrade_metrics.json | `1E294532D1BF4119` | `1E294532D1BF4119` | `1E294532D1BF4119` | SAME | SAME |
| round_trips.csv | `1D0F01033EB6C903` | `1D0F01033EB6C903` | `1D0F01033EB6C903` | SAME | SAME |
| trades.csv | `DDE240B02B49E76D` | `DDE240B02B49E76D` | `DDE240B02B49E76D` | SAME | SAME |

**SAME_AB = 7/7｜SAME_ANCHOR = 7/7** ⇒ **黄金比对零差异**（判型＝纯恢复型的硬证：缺省 ptrade 行为零改变）。

关键指标（A 臂＝B 臂，与 11-01 回执逐位同）：最大回撤 `5.641001455691985`｜夏普 `7.877695491898896`｜胜率 `25.0`｜年化 `357.9904059833789`。

> **锚值勘误留痕**：11-01 回执 markdown 表格「全量 SHA256」列的 `config.csv` 行写作 `FF4E25136E7B**DBC4**283…`
> （**该回执自身笔误**——其同表「锚(前16)」列写的正是 `FF4E25136E7B6DBC`）；11-01 可执行比对脚本
> `output/v4_golden_20261101/v4_golden_compare.ps1` 锚值亦为 `FF4E25136E7B6DBC`。经**实物取证**
> （11-01 原始产物 `20261101_202233_strategy`／`20261101_203432_strategy` 的 `config.csv` 实物 SHA256
> ＝ `FF4E25136E7B6DBC4283…`）判定**锚取实物值为准**；本次 A/B 臂实物与之**逐位一致**。
> 本次回执以实物值登记（`output/r82_golden_20261103/F4_COMPARE_RESULT.json`）。

### F5 远端等效验证（PASS）

```
A 臂签名：(self, db_path, strategy, start, end, capital=100000, cost=None, strategy_type='ptrade',
          min_rebalance_pct=0.005, progress_callback=None, config=None, match_price_mode='close',
          providers=None, engine_profile='daily-bar-v1', etf_t0=False, rebalance_mode=None,
          cost_basis_method='diluted')                      ← 无 match_profile
B 臂签名：…… cost_basis_method='diluted', match_profile='ptrade')   ← 有且缺省 'ptrade'
判据1 A 含=False／B 含=True 缺省='ptrade'   PASS
判据3 A 16 项 / B 16 项（去 match_profile）逐项一致=True   ⇒ 零收窄 PASS
判据2 负向：A 臂 TypeError（=远端崩溃面）PASS
判据2 正向：B 臂构造成功 match_profile='ptrade' PASS
缺省臂：B 不传参 ⇒ 'ptrade'（现行行为零改变）PASS
非法值：B(match_profile='bad') ⇒ ValueError（fail-closed）PASS
[F5] RESULT=PASS
```

### F6 知识反哺

```
kb_draft.py → knowledge/qmt/QD-2026-0017.md（族 qd / D3 / fix_layer=engine）
kb_hook.py --files knowledge/qmt/QD-2026-0017.md --strict
  → [kb_hook] 体检 1 件（族：qd），不合规 0 件   rc=0
```

## 七、回退条件

| 触发 | 回退手段 |
|---|---|
| F4 任一产物 SHA 漂移 | **不入库**；`git checkout -- quantstudio/backtest/backtest_engine.py` 恢复 M 态（本批为纯补提交，回退＝撤暂存） |
| F3 出现本批所致新增失败 | 撤暂存 ＋ 重做归因（四臂对照） |
| F6 kb_hook 非 0 | 修条目至过检再入库；不过检不落盘 |
| 已入库未推送 | `git revert --no-commit` 定向回退（不整仓 reset） |
| 已推送 | 事故流程：客户通知走铁律 12 ＋ 同步门重跑 |

**写前快照**：`stash@{0} = baseline-R280-20261103`（`6c456d214b68`，四步配方：add→stash create→stash store→reset）。

## 八、变更日志

| 时间 | 内容 | 状态 |
|---|---|---|
| 2026-11-03 | v1.0 方案＋验收证据（引擎侧 match_profile 补提交，六路径精确入库） | ③实施＋④验收完成，候 ⑤ 随 11-04 呈批 |
