# QFQ 重锚第二批 · 第四轮对抗审核修复报告

日期：2026-07-28 凌晨（第四轮，针对"方法 B 乘法模型 vs xtquant 减法复权模型根本冲突"审核意见）
状态：**方案 A（严格 BLOCK）已实现为默认行为；方案 B 设计文档已提交待批；不再用容差吸收模型差异**
铁律状态：**未 stage / 未 commit / 未 push / 未创建 PR / 未同步 GitHub**（暂存区为空，QFQ 文件均为未跟踪 `??`）。

关联文档：
- 设计决策（方案 A/B）：`docs/qfq-reanchor-minute-model-decision-20260727.md`
- 逐样本与逐 bar 误差存档：`docs/qfq-reanchor-batch2-real-ab-samples-20260727.txt`（156 行）

---

## 0. 本轮为实际新版本的证明：文件修改时间

| 文件 | 本轮修改时间 (UTC+8) |
|---|---|
| `scripts/capture_fresh_xtquant_golden.py`（OHLC 四列重采版） | 2026-07-27 23:26:07 |
| `tests/fixtures/qfq_real_reanchor/fresh_xtquant/`（重采，metadata） | 2026-07-27 23:26:20 |
| `tests/test_qfq_reanchor_batch2.py`（默认容差 BLOCK 语义重写） | 2026-07-27 23:36:20 |
| `scripts/dump_real_method_ab_samples.py`（actual ratio + 逐 bar 误差版） | 2026-07-28 00:16:03 |
| `docs/qfq-reanchor-batch2-real-ab-samples-20260727.txt`（重新生成） | 2026-07-28 00:16:22 |
| `docs/qfq-reanchor-minute-model-decision-20260727.md`（新增） | 2026-07-28 00:17:29 |
| `quantstudio/pipeline/qfq_reanchor_engine.py` | 2026-07-27 22:18:51（本轮未改引擎——见 §5 说明） |

## 1. 承认并纠正：第三轮"容差校准"是错误做法

第四轮审核意见成立：xtquant 为**减法复权**（fresh OHLC fixture 实证：600875
front = raw − 0.53，600039 front = raw − 0.46），`front/raw` 随价格漂移，
区间内不存在单一稳定乘法比率。第三轮把 ratio/golden/cross 容差放宽至最高
1%、将多簇合并为 median ratio 后继续写价，违反引擎自身验收原则。

**本轮撤销**：`REAL_TOL` 放宽容差已从测试删除；真实三证券测试回归**默认
`ReanchorTolerances()`**，断言 **BLOCK 且数据未变** 为正确结果。

## 2. 默认容差真实案例结果（正式行为，`scripts/dump_real_method_ab_samples.py` 第 1 部分）

| 证券 | 默认容差结果 | block_reason | 逐日 R 簇结构（rel_tol=5e-4，running-ref） |
|---|---|---|---|
| 600875 | **blocked** | fresh_daily_scale_inconsistent（low_front/low 与 close_front/close max_dev=1.17e-03） | 修正簇 5 + 除权后 noop 簇 |
| 600039 | **blocked** | fresh_daily_scale_inconsistent（open_front/open 与 close_front/close max_dev=2.26e-03） | 修正簇 6 + noop 簇 |
| 002864 | **blocked** | ratio_multi_cluster | 修正簇 3 + noop 簇 |

三证券 BLOCK 后 `stock_daily`/`stock_minutes` 快照逐值一致（未写回）、anchor
未推进。修正簇数 5/6/3 与审核意见完全一致。

说明：新 OHLC 四列 fixture 提供了比第三轮（仅 close）更强的证据——600875/
600039 在进入 ratio 聚簇之前就已被 **fresh daily 列间 scale 不一致**（减法
复权对 OHLC 四列同减固定分红 → 各列 scale 不同）先行挡下；将该前置检查
按列放开后（诊断分支）即复现 ratio_multi_cluster（5/6 修正簇）。

## 3. actual xtquant daily OHLC fixture（审核项 3）

`capture_fresh_xtquant_golden.py` 已改为 `fields=["open","high","low","close"]`
× `dividend_type ∈ {none, front}`，daily+1min，三证券，07-13 predecessor 同样
直接采集。**staged fresh daily 四个 front 列全部逐值读取实际 xtquant 输出**
（`_fresh_xt_daily`/`_prealign_predecessor` 已改为直读，禁止 close-scale 合成，
grep 确认测试中无 `close_front / close_raw ×` 合成路径）。

fixture 列结构：`time, open_raw, high_raw, low_raw, close_raw, open_front,
high_front, low_front, close_front`（daily 与 1min 同构）。

**完整 sha256**（`metadata_fresh_xtquant.json`，captured_at=2026-07-27T23:26:18，
接口 `xtdata.get_market_data_ex`，客户端 xtquant_250516，零平移 end-labeled）：

| 文件 | 行数 | sha256 |
|---|---|---|
| 600875_fresh_daily.parquet | 10 | c7a6e6e7cc57ec73027bd09e9c2161be88fc876a633ec99a0cec446ce6a3aa6a |
| 600875_fresh_1min.parquet | 2169 | c4470d995b42538e35d6b4266712a8b45530791db2033d02a2771f7da3ff9085 |
| 600039_fresh_daily.parquet | 10 | 133036efd865a23289d9e63e3659a92edb500f4df4c03ff9daa3bfbb78f47df3 |
| 600039_fresh_1min.parquet | 2169 | 47be076630abbdf87526971611ab7b9a50a5d6fdb5aebeb701357db5b932dc8f |
| 002864_fresh_daily.parquet | 10 | 0000451db89d8d4e768ae5d9fdc8c19b51b260452c6535825cdc81a0ee6e739e |
| 002864_fresh_1min.parquet | 2169 | 650175766f946be06508deffe7214de65d6c655547e67b9dfd15e80b7f635312 |

减法模型 sanity（fixture 内实证，`test_fresh_xtquant_fixture_integrity` 固化）：
除权日前 `raw − front ≈ 每股分红`（600875: 0.53；600039: 0.46，OHLC 四列同值）。

## 4. actual plan ratio 逐样本 A/B 报告 + post-write vs fresh xtquant 逐 bar 误差（审核项 4）

`dump_real_method_ab_samples.py` 已重写：

- **R_applied 缺陷已修**：committed 路径直接读取 `ReanchorResult.plans` 的
  `RatioSegment.ratio`（真正落库值）；BLOCK 路径（600875/600039 在新 OHLC
  证据下诊断容差也被挡）以**离线模拟单比率写回**（simulated front =
  stored_raw × ratio_plan，不触库）量化偏差，模拟 ratio_plan 与第三轮实际
  落库 ratio 同构且数值一致。
- 逐样本列：`bar_time / R_B_obs / plan_ratio / R_golden / postwrite /
  xt_front / abs_err / rel_err`（每更新日等距 3 根合法连续竞价 bar）。

**逐 bar 误差（更新窗口全 bar，与审核意见数字逐位一致）**：

| 证券 | ratio_plan（实际/模拟单比率） | max close_front 误差 | 误差>0.01 元 bar 数 |
|---|---|---|---|
| 600875 | 0.9797400493（模拟，未写库） | **0.032619 元** | **960 / 1928** |
| 600039 | 0.9468272369（模拟，未写库） | **0.046205 元** | **1491 / 1928** |
| 002864 | 0.7627533166（诊断容差 committed 实写） | **0.020477 元** | **349 / 1446** |

**测试断言已按审核要求改造**：`test_exdiv_real_reanchor_blocks_by_default`
与 `test_002864_...` 直接断言 **post-write（/模拟写回）与 fresh xtquant
close_front 的绝对误差**（max_err 与 >0.01 元 bar 数逐位断言），不再只断言
`post == raw × ratio`。

## 5. 设计决策：方案 A 已实现，方案 B 待批（审核项 5）

详见 `docs/qfq-reanchor-minute-model-decision-20260727.md`：

- **方案 A（本轮已实现，默认行为）**：严格保持方法 B 语义——真实数据多簇 /
  fresh daily 列间 scale 不一致 → 默认容差 BLOCK，三证券**不得自动写回**。
  引擎代码**本轮零修改**（第三轮引擎在默认容差下本就正确 BLOCK；错误在
  测试用放宽容差绕过），修复全部落在测试与工具脚本层。
- **方案 B（提案，未实现）**：B-1 fresh xtquant 分钟逐值写入（推荐候选）/
  B-2 additive delta / B-3 乘法 + 1 tick 终检护栏。属框架行为变更，文档已
  含影响、风险、回退与验收门槛（逐 bar ≤1 tick），**获您明确批准前不实现**。

## 6. 测试改造摘要（tests/test_qfq_reanchor_batch2.py）

- `REAL_TOL` 删除；真实三证券用例更名并重写为 `*_blocks_by_default`：
  默认容差断言 blocked + block_reason 精确匹配 + 快照逐值一致 + anchor
  未推进 + 真实收益数字断言（600875 −7.819820%/−6.024982%，600039
  −7.457983%/−2.759382%）+ 簇数断言（error 消息含"5/6 个需修正比率簇"）
  + 单比率模拟写回 vs fresh xtquant 逐 bar 误差断言（§4 数字）。
- `test_fresh_xtquant_fixture_integrity`：8 列 OHLC schema + 减法模型 sanity
  （raw − front ≈ 分红）+ sha256 + 零平移逐 bar 对齐校验。
- `_fresh_xt_daily`/`_prealign_predecessor`：逐值直读真实 xt OHLC front。

## 7. 工作区描述

QFQ 第四轮仅触碰：`tests/test_qfq_reanchor_batch2.py`、
`tests/fixtures/qfq_real_reanchor/fresh_xtquant/`（重采）、
`scripts/capture_fresh_xtquant_golden.py`、`scripts/dump_real_method_ab_samples.py`、
本报告、逐样本存档、设计决策文档。引擎 `qfq_reanchor_engine.py` 本轮未改。
工作区同时存在大量并行任务改动（writers.py/README/docs/backtest 策略/
ptrade/行业 F4/指数/dist 等）——**QFQ 本轮未触碰任何并行文件**。

## 8. pytest 新输出（2026-07-28 凌晨）

```
# batch2 单独（tests/test_qfq_reanchor_batch2.py）
51 passed in 34.70s

# batch1 + batch2 联合
140 passed in 41.29s

# 项目全量
1350 passed, 1 warning in 306.43s (0:05:06)
```

（warning 为并行 ETF 轮动参考实现的 np.log RuntimeWarning，与 QFQ 无关。
用例总数与第三轮持平：真实用例由"放宽容差 committed"改为"默认容差
BLOCK"语义重写，数量不变、含义反转。）

**验收口径声明**：本轮全绿**不依赖任何放宽容差**——真实用例在默认
`ReanchorTolerances()` 下断言 BLOCK 为正确结果；诊断容差仅出现在
dump 脚本第 2 部分并明确标注"非验收路径"。

铁律重申：本轮未执行任何 stage、commit、push、PR、GitHub 同步操作。
