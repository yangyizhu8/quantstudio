# 证据索引 · snapshot-20261114（原全集 / JUnit / 映射 / 结论 同版本对应）

> **本索引为当前唯一有效版本。**
> 旧件 `../nodeid-mapping-batch12.md`（写 A30/B2/C12/D91）与 `../baseline-run-batch2.md`
> 已被本快照**取代**（其数字在 `fin_growth` 第 10 项取证后已过期）。
> 判据：**完成判据＝原全集、JUnit、映射、结论四者同版本相互对应**。

## 一、版本锚

| 项 | 值 |
|---|---|
| 快照 ID | `snapshot-20261114` |
| 生成时间 | 见 `mapping.json` 的 `generated_at` |
| 基线锚 | `7826aedbf9d634aabb48c2f7c0d988f490c78b07`（`6a98974^`，本批之前） |
| 当前回归 JUnit | `docs/evidence/pd0020-full-regression-20261113/junit.xml`（sha256 见 `mapping.json`） |
| 原 nodeid 全集 | **135**（`full-135-nodeids.txt`，逐行；**不做任何剔除**） |
| 基线 JUnit | 10 个文件（清单＋各自 sha256 见 `mapping.json` 的 `baseline_junit_files`） |

## 二、逐 nodeid 分桶（同版本）

| 桶 | 数量 | 含义 |
|---|---|---|
| **A_基线同样失败** | **40** | 基线该 nodeid 亦 FAILED ⇒ **已证基线既有**（nodeid 级确认） |
| **B_基线通过当前失败** | **2** | 基线 PASSED、当前 FAILED ⇒ **已证本批新增**（根因＝A-1） |
| **C_基线跳过** | **12** | 基线 SKIPPED（**原因明确**）⇒ **环境差异类**，单列 |
| **D_基线未取证** | **81** | 基线尚未跑该文件 ⇒ 状态**未知**，不推测 |
| **合计** | **135** ✓ | 与 `full-135-nodeids.txt` 行数一致 |

> ⚠️ **算术闭合只证明台账总数，不证明分类**——分类依据是逐 nodeid 状态（`mapping.json` 的 `detail`）。

### A 桶按文件

| 文件 | 项数 |
|---|---|
| `test_fin_growth_dividend_staging_tool` | 10 |
| `test_fund_contract_wiring_matrix` | 7 |
| `test_source_import` | 7 |
| `test_phase4_minute_batch_provider` | 6 |
| `test_phase4_minute_memory_slice` | 6 |
| `test_strategy_fidelity_gates` | 4 |

### B 桶（2 项，逐 nodeid）

- `test_source_import::test_08_remove_banding`
- `test_source_import::test_08b_rewrite_literal_inline`

根因已定位：`|is_trade|` 由 `portability_rules.py` 的 **DENY_REMOVE** 迁入
**`PTRADE_REGISTERED_WARN`（KEEP）**（A-1）⇒ 产物保留 `is_trade(`、不再生成 `g.mode = False`。
依据与断言 diff 见 `../test08-08b-attribution.md`。

### C 桶 skip 原因（逐项记录于 `mapping.json` 的 `reason`）

| 原因原文 | 项数 |
|---|---|
| `DB 不存在` | 8 |
| `测试库不存在` | 3 |
| `测试库不存在: D:\...\QuantStudio-wt-baseline\data\quantstudio.db` | 1 |

⇒ **性质＝环境差异类**：worktree 内无 `data/quantstudio.db` ⇒ 自动 skip；
主仓有 DB ⇒ 同 nodeid 实际执行并 failed。
**既非「基线既有」，也非「本批新增」**；**skip 不是缺 nodeid**（nodeid 存在，状态为 skipped）。

## 三、关键表述纪律（按用户裁定）

1. **保留原 135 个 nodeid 全集**，不做任何剔除；
   第一批基线 50 PASS **包含** `test_08`/`test_08b` 这两个当前失败候选
   ⇒ 正确表述为「**其余 48 项与 135 清单无关**」，**不能整体剔除不算**。
2. **基线 PASS ≠ 当前已解决**：基线 passed 只是 `7826aed` 该版本当时的读数。
3. **基线同 FAIL ≠ 当前问题已修复**：A 桶 40 项**不免除**当前修复义务，
   也不代表根因相同（根因仅 `test_08/08b` 已定位）。
4. **skip ≠ 缺 nodeid**：C 桶 12 项 nodeid 存在、原因明确。
5. **A-1 契约断言同步仍先交②审**，不得直接把断言改绿；
   且验证须覆盖**调用上下文**（`initialize` 内 `if not is_trade(): set_backtest()`）
   与**产物语义**（回测/交易分支与 PTrade 文档一致），**不能仅把 `not in` 翻成 `in`**。

## 四、`fin_growth` 第 10 项（真实子进程）取证结论

| 维度 | 实测事实（源码读证） |
|---|---|
| 启动 | `python -m quantstudio.pipeline.daemon --mode once --task … --config-dir <临时> --runtime-manifest <临时> --runtime-nonce <nonce>` |
| 数据根隔离 | `env["QUANTSTUDIO_DATA_ROOT"] = str(staging_root)` |
| 配置隔离 | `--config-dir` 指向 `TemporaryDirectory` 内 |
| 凭据隔离 | `sitecustomize.py` 注入 **FAKE `TushareAdapter`** ⇒ 注释逐字「NO real Tushare credentials」 |
| 生产源码 | 注释逐字「Touches NO production source」 |
| 超时清理 | `TimeoutExpired → proc.kill(); proc.wait(timeout=30)` |
| 模式 | `--mode once`（**一次性**，非 `--mode forever` 常驻） |

⇒ **安全前置满足** ⇒ 已补取证：**基线亦 FAILED** ⇒ 归入 **A 桶（fin_growth 10/10）**。

### 进程清理声明（只清本次 PID）

取证后核查到 `PID=39200` 的 `python -m quantstudio.pipeline.daemon --mode **forever**`。
该进程**非本次测试产生**（本次为 `--mode once`，随测试 12.4s 结束已退出，无遗留）。
按铁律 03「排除自身 PID ＋ 匹配面双向验证」，**该常驻 daemon 不属本次清理范围，未做任何终止操作**。

## 五、未完成

- **D 桶 81 项未取证**（其余 40+ 文件在基线未跑，状态未知）
- **最终 ④ 未通过**；不推送
