# QMT 转换正确性测试 · 首轮基线留痕（2026-11-03）

> 目的：把**本地回测 → QMT 代码**转换管线的「转换正确性」首轮实测读数固化为**漂移锚**——
> 六策略 canonical 重转产物的 SHA16 基线 ＋ 测试面读数 ＋ 全量回归读数。
> 后续任何一轮重转，可直接与本件六值逐位对照，判定「转换零漂移 / 出现漂移」。
> 上游：用户 2026-11-03 22:5x 指令「现在就按第一段跑一遍本地转换正确性测试」→ 23:0x 执行完成 → 用户裁「①补跑全库大回归 ②把 sha16 基线登记入册」。
> 关联：`docs/evidence/api-surface-behavior-verification-20261028.md`（T5 公开面核验终态）｜`docs/ISSUES.md` T5 段｜铁律 07 验收门槛三件套。

---

## 一、执行环境（隔离纪律，铁律 19 / 会话纪律 a37d3002）

| 项 | 实值 |
|---|---|
| `QUANTSTUDIO_DATA_ROOT` | `<repo>\data\backup_import_20261101_040935`（**只读副本**，非生产主库） |
| `QS_WRITE_LOCK_DIR` | `%TEMP%\qs_r82_isolated\lock`（与 DATA_ROOT 解耦，刻意隔离） |
| `QT_QPA_PLATFORM` | `offscreen` |
| `PYTHONIOENCODING` | `utf-8` |
| daemon 持锁进程 | **PID 9828 `--mode forever`**（自 11-03 06:13:59），**全程在跑、未触** |
| Python | 3.11.9（`…\Programs\Python\Python311\python.exe`） |
| 跑前基线 | `HEAD=cc37029`｜待推 23 笔｜`origin/main` 尚不含 `match_profile` |

> 生产主库 `data/quantstudio.db`（61.7 GB）**未被读写**；所有产物落 `output/`（`.gitignore:31` 忽略）。

## 二、六策略 canonical 重转（转换正确性主体）

命令：`python scripts/qmt_reconvert_six.py`
逐份内置门禁三项：`validate_qmt_portability` → **GBK 物理编码落盘** → 自检（无 BOM / 首行 `#coding:gbk` / GBK 可回读）。

| # | 策略 tag | 源文件（`quantstudio/backtest/strategies/`） | 产物字节 | **SHA16（漂移锚）** | 门禁 |
|---|---|---|---|---|---|
| 1 | CANSLIM突破成长选股策略 | `CANSLIM突破成长选股策略.py` | 126,342 | **`d72117d7729db1dd`** | portability=PASS · enc=gbk · selfcheck=PASS |
| 2 | fall_reversal | `fall_reversal_quantstudio.py` | 112,852 | **`185a5721a76db8f1`** | 同上 |
| 3 | tech_etf_mvo | `tech_etf_mvo_rotation_quantstudio.py` | 120,653 | **`3649288922bf8fb9`** | 同上 |
| 4 | vol_regime_mom_rev | `vol_regime_mom_rev_quantstudio.py` | 128,432 | **`6d3e9fa33a1f4479`** | 同上 |
| 5 | weekly_smallcap | `weekly_smallcap_growth_momentum_10_quantstudio.py` | 122,558 | **`10bc4ad75d93b26c`** | 同上 |
| 6 | 周频三层 | `周频小市值成长动量（三层止损）.py` | 126,940 | **`1e0da519a2fa91ad`** | 同上 |

**结论**：`ALL 6 PASS (enc=gbk)`，rc=0。
**留痕性质**：六值在全仓 `docs/**` 检索**无历史记录** ⇒ 本件为**首次留痕基线**（此前不可声称「跨版本零漂移」，此后可以）。

## 三、QMT 测试面（14 件）

命令：`pytest tests\test_qmt_*.py -q`

| 面 | 读数 | rc |
|---|---|---|
| 14 件 `test_qmt_*` | **168 passed in 8.41s**（0 failed / 0 error） | 0 |
| 双端对等哈希门 `test_qmt_export_parity.py`（专项） | **3 passed in 0.84s** | 0 |

对等门三用例（铁律「新功能模块双端对等」的可机检面）：
1. `test_kernel_deterministic_same_input_same_sha` —— 单一内核两次转换产物 SHA **逐位一致**；
2. `test_shell_purity_pyqt_and_cli_call_same_kernel` —— 薄壳**纯透传**静态断言（CLI/Qt 同内核）；
3. `test_tab_registered_in_main_window` —— QMT export tab 导航注册在位。

## 四、全量回归（铁律 07 门槛第三项「既有功能回归全绿」）

命令：`pytest tests -q`（isolated env 同上）

**实测读数**（`pytest tests -q`，isolated env 同上；开跑 23:07:07，收官 23:40:39，**2012.90s / 33:32**）：

```
94 failed, 4380 passed, 5 skipped, 8 xfailed, 5 warnings, 9 errors in 2012.90s (0:33:32)
```

### 4.1 判据结论（如实）

**铁律 07 第三项「既有功能回归全绿」⇒ 未达标**（94 failed ＋ 9 errors）。
但经下述**三项归因**，**本批两笔（`293c520`／`cc37029`）零新增失败**；失败集属**仓库既有基线红**（他线在途 ＋ 历史债）。

### 4.2 归因证据一：干净树反证（决定性）

建 `git worktree add --detach <tmp>/wt_pre f60845f`（**我两笔之前**的提交，`f60845f`，其引擎 `match_profile` 命中 = **0**），
对失败最集中的 4 组同环境重跑：

| 组 | 干净树 `f60845f`（我 2 笔之前） | 当前工作树 |
|---|---|---|
| `test_phase4_minute_memory_slice.py` | 6 failed | **6 failed（同 6 项）** |
| `test_phase4_minute_batch_provider.py` | 6 failed | **6 failed（同 6 项）** |
| `test_source_import.py` | 7 failed | **7 failed（同 7 项）** |
| `test_strategy_fidelity_gates.py` | 5 failed | **4 failed**（`test_required_gate_sample_directories_exist` 在干净树红／当前树绿——因干净树无 `output/` 产物） |
| **合计** | **24 failed / 62 passed** | **23 failed / 63 passed** |

⇒ 失败项**逐条同构**，且当前树反而**少 1 项** ⇒ 该批失败**与本批两笔无关**，是 `f60845f` 即已存在的基线红。

### 4.3 归因证据二：失败分布与本次改动的路径交集

- 103 项（94 failed ＋ 9 errors）分散于 **37 个测试文件**；失败最集中：`test_smallcap_overnight_scalp_agent_v2`(10)／`test_fin_growth_dividend_staging_tool`(10)／`test_first_cover_event_daily_agent`(9, errors)／`test_source_import`(7)／`test_phase4_minute_memory_slice`(6)／`test_smallcap_overnight_scalp_agent`(6)／`test_phase4_minute_batch_provider`(6)／`test_strategy_fidelity_gates`(5)／`test_qfq_schema_migration`(4)…
- 本批两笔触碰的代码/测试路径仅 **4 条**（`quantstudio/backtest/backtest_engine.py`、`tests/test_match_profile.py`、`docs/**`、`knowledge/qmt/**`）
- **交集仅 3 项**：`test_minute_limit_halt`(1)／`test_next_open_limit_and_halt`(1)／`test_engine_config`(1)——**三项均已在 R82 F3 四臂对照中判定**（前两项＝既有基线红，HEAD 隔离工作树同红；后一项＝隔离数据根环境产物，`QUANTSTUDIO_DATA_ROOT` 使 `EngineConfig.default()` 指向副本库与该断言互斥）

### 4.4 归因证据三：9 个 errors 的根因（非代码）

```
E   FileNotFoundError: [Errno 2] No such file or directory:
    'output\generated_strategies\first_cover_event_daily\agent_workspace\strategy.py'
```

⇒ 系 **fixture 依赖的策略产物文件缺失**（他线产物未生成或被清理），**非导入错误、非本批所致**。

### 4.5 与上一轮全量基线不可直接比较（量化漂移）

| 项 | 上一轮（`docs/evidence/quality-audit-oom-20261031.md` §8.1） | 本轮 |
|---|---|---|
| 读数 | 70 failed, 4079 passed, 4 skipped, 8 xfailed（2096.31s） | 94 failed, 4380 passed, 5 skipped, 8 xfailed（2012.90s） |
| 用例总量 | ≈4,153 | **4,496（+≈343）** |
| 期间他线落地 | — | **93 笔 commit**（`b5d3c87..HEAD`）＋**29 项 M 态**＋**753 项未跟踪** |

⇒ 失败数 +24 与用例 +343、commit +93、在途改动 782 项**同窗发生**，**不具备直接可比性**；
严谨口径只能给到「**本批零新增失败**」（§4.2 反证）。

### 4.6 处置口径（零越权）

- **不修**上述基线红：它们不属本批、且多数落在 `quantstudio/pipeline/**`、`quantstudio/delivery/**`、策略 agent 产物面等他线在途范围（事实：`quantstudio/backtest/ptrade_api.py` 现为他线 M 态）。
- **登记**：本件如实记录读数与归因链；建议按「回归债清债批」单独立项（须用户裁），
  并把铁律 07 第三项的机检判据从「**全绿**」改为「**无新增失败**（以上一轮读数为基线）」，否则该门槛在当前仓库状态下恒不可满足。

## 五、判据与用途

- **本件判据**：六策略 `api_portability` 全 PASS ＋ 产物 GBK 契约三项自检通过 ＋ QMT 测试面全绿 ＋ 全量回归无**新增**失败。
- **漂移判定**：下一轮重转后取六个 SHA16 与本件逐位对照——
  - 六值全同 ⇒ 转换**零漂移**（框架层无行为改变）；
  - 任一不同 ⇒ 必须给出改动归因（改了模板/规则/内核），否则视为**非预期漂移**回退。
- **不覆盖面（如实标注）**：
  1. **QMT 端保真对照**——`scripts/qmt_align_check.py --qmt-log` 仍无输入（`logs/` 内 qmt 日志命中 = 0），「本地 vs QMT 端」五维逐调仓日对照**从未跑过**；
  2. **P2 三项未修**：`ISS-T5-G`（`get_stock_name` 返代码非名称）／`ISS-T5-I`（`get_instruments` 返回面）／`ISS-T5-M`（`get_stock_blocks` 未来函数面）——若首份保真测试策略命中，会产出**假差异**；
  3. 本件不构成「可出仓」声明——出仓闸在用户 ⑤。

## 六、变更日志

| 时间 | 内容 | 状态 |
|---|---|---|
| 2026-11-03 23:0x | v1.0 首轮基线留痕：六策略 SHA16 ＋ QMT 测试面 168 passed ＋ 对等门 3 passed | ④验收证据 |
| 2026-11-05 00:1x | **v1.2 换锚（订单审计行批 · 用户裁①「SHA16 换锚新旧对照留痕」）**：`_QS_QMT_ORDER_EXT` 模板串加格式 A/B 审计行（方案 `docs/qmt-order-audit-line-design-20261104.md` v1.1，②审有条件通过·总调度三修订落盘）⇒ 六产物 SHA16 **必然漂移**——**新六值生效、§二 表六值作废（保留作历史对照）**：CANSLIM `608bdd2fbb413cf5`（旧 `7f31a86baa6344e1`）｜fall_reversal `8318bdc1b59a44a8`（旧 `2f93c2282fe524df`）｜tech_etf_mvo `e690122567065db3`（旧 `6b631fb8d97a6398`）｜vol_regime_mom_rev `321742f71999f8e7`（旧 `351e46483e5cb299`）｜weekly_smallcap `2eb2b1c838b6014c`（旧 `746d6427c7d6a3fa`）｜周频三层 `c1865366ea6e5a48`（旧 `a1a64a643adb4930`）；bytes 100,080–115,660B。**V4b 契约追认**：`check_fund_matrix --check --reverify` ⇒ wrapper_hash → `7a424f397983`｜**qmt_wrapper_hash → `4f2e73bd467a`**，OK 契约矩阵门禁通过（`_QS_QMT_ORDER_EXT` 在 `_QMT_HASH_SCOPE` 哈希面内，铁律 10⑥ QMT 侧同等适用）。qmt_export 域六产物同步重生成（静态 A=1 B=1 全在位）。
| 2026-11-05 16:4x | **v1.3 换锚（停牌判据修复批 · QD-2026-0021）**：`_QS_QMT_STOCK_INFO_EXT` 的 `_qs_status_flags` 移除 `IsTrading` 停牌判据（②审裁 A：InstrumentStatus 为唯一权威判据；缺键/假值/不可解析 ⇒ 走「不可判定保守保留」出口）；六产物 SHA16 再换锚 `8261c6866e2d9dc3`/`8c591a85bc194cb3`/`3ed46b17613398c6`/`edb6dbbee1ba501f`/`49e8ddf2cfb5881c`/`28bfddc5e86590a9`；`meta.qmt_wrapper_hash` `4f2e73bd467a` → `976d9b39349e`（同 commit `check_fund_matrix.py --check --reverify` 追认 · 铁律 10⑥ QMT 侧）；**同时纠正 v1.2 遗留**：v1.2 仅写变更日志、本表未同步（仍停留 v1.0 首轮六值）⇒ 本笔一并修正 | ④验收证据 |
| 2026-11-05 21:3x | **v1.4 换锚（时区解析垫片批 · QD-2026-0022）**：`_QS_QMT_MODULE_HEADER` 新增 `_qs_tz_shim()`（P0 探测/P1 pytz 缓存/P2 maybe_get_tz/P3 ZoneInfo；正常环境零输出，需垫片时打印 QS_QMT_TZ_PROBE，三层全失败 fail-loud）；六产物 SHA16 再换锚 `b3b3ef9f83fa54e5`/`c318199d1eb4727a`/`588c4cddb5747a82`/`65a47679345ab901`/`2cd513d153f1ca9a`/`707d679853eebbdd`；`meta.qmt_wrapper_hash` `976d9b39349e` → `df635bfed97d`（同 commit `--check --reverify` 追认）；批次内自测拦下两处（零输出契约冲突→改为仅需垫片时输出；portability 6 条 BLOCK→模块属性存原函数） | ④验收证据 |
| 2026-11-07 01:4x | **v1.5 换锚（复权因子快照批 · QD-2026-0023 修复）**：`_QS_QMT_HISTORY_EXT` 新增 QFQ 快照三态层（loaded→dividend_type='none'+按本地 `*_front_ratio` 跳变点换算 P_adj=P_raw×F(t)/F(base)；absent/broken fail-loud 回退 front）；六产物 SHA16 换锚 `d72117d7729db1dd`/`185a5721a76db8f1`/`3649288922bf8fb9`/`6d3e9fa33a1f4479`/`10bc4ad75d93b26c`/`1e0da519a2fa91ad`；`qmt_wrapper_hash` `df635bfed97d`→`76e605e8a9e1`（同 commit `--check --reverify` 追认） | ⑥出仓批 |
| 2026-11-03 23:4x | v1.1 补登全量回归：**94 failed / 4380 passed / 9 errors（2012.90s）**＋干净树 `f60845f` 反证（24↔23 同构）＋9 errors 根因（产物缺失）＋与 10-31 基线不可比量化 | ④验收证据 |
