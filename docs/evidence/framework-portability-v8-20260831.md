# 框架层平台可移植性 v8 实施验收（2026-08-31，六步流水线第 3/4 步）

- 方案：v2.1 合并方案（B1-B10 框架层通用化 + 双端财务口径对齐），ZCode 审计有条件通过
- 前置探针：probe_v21b 平台实证（docs/evidence/probe-v21b-platform-evidence.md）——B-1 选项 1（publ_date PIT）可达、B-2 落①、B-3 维持②、B1 双码池实证
- 本次：实施 + 静态验收（6 策略横验 + 契约测试 + v8 重转）；平台复验（用户部署 v8 贴日志）为验收后续段

## 实施改动清单（全部框架层，策略源码零改动）

| 文件 | 改动 | 审计对应 |
|---|---|---|
| `quantstudio/strategy_compiler/source_import.py` | `_QS_FUNDAMENTALS_EXT` 升级：`get_fundamentals` 显式 `start_year/end_year/report_types`；date+range 并存 → date-only 双查询 concat（B6，探针 P1 互斥契约）；`_qs_fund_select_fields` 全缺 → 1 行 NaN 契约（P-D10 v1.2，B8/B2-⑦ 缺列降级）；新增 `_qs_gf_maybe_prefetch`/`_qs_gf_auto_cache_get`（B9/B10 list 批量预取 + g 缓存，破流控）；`_qs_gf_pit_filter`（B-1 publ_date PIT）；`_qs_equity_probe`（B-2 一次性探测）；`_qs_filter_report_types`（B-1 期型过滤） | B-1/B-2/B-3、B4/B5/B6/B8/B9/B10 |
| 同上 | 新增 `_QS_INDUSTRY_EXT`：`get_industry` 平台替代（反向金融池 `get_industry_stocks` 双码裸/.XBHS/.XBKS + 池内首个行业码 / 池外哨兵 999999 fail-open + 池无效回退原生 + 一次性告警 RD-3）；行业码集 `_QS_INDUSTRY_CODES` 转换期从策略源码 AST 烘焙（tuple） | B1/B7、RD-3 |
| 同上 | 门控：`_source_uses_industry_api` + `_extract_industry_codes` + `_render_industry_ext`；`_inject_all` 接线（fundamentals/industry 门控注入 + coverage） | 铁律通用性 |
| `portability_rules.py` | `INJECTED_WRAPPER_NAMES` += `get_industry`；`SHIM_CONTRACT_REGISTRY` += get_industry（dict 契约/空行为哨兵）；get_fundamentals / get_fundamentals_batch 空行为契约更正（P-D10 v1.2：全缺 1 行 NaN；batch 委托 list 模式） | 契约 registry 增补/更正 |
| `validators/validate_ptrade_portability.py` | B2-RSRS-DIMENSION WARN（pct 0-100 vs 分数阈值 0.6-0.8 直接比较检测）| B2 量纲产物侧防线 |
| `skills/quantstudio-strategy-compiler/scripts/validate_agent_strategy.py`（仓库受管副本）| RSRS-DIMENSION-UNIT WARN（策略编写期）| 审计必改项①（经 install 同步 .agents）|
| `tests/test_ptrade_contract_compliance.py` | 契约断言更新（v1.2 NaN 行）+ 新增 5 测试（B6 双查询、缺列 NaN、pit filter、B1 pool/fail-open、B1 门控） | 验收测试 |

## 验收证据

### 1. 契约合规测试
`python -m pytest tests/test_ptrade_contract_compliance.py` → **99 passed**（含新增 5 测试）。

### 2. 回归
`python -m pytest tests/ -k "ptrade or contract or conversion or source_import or portability"` →
453 passed；5 failed 均为既有环境漂移/前置（slippage 默认值 0.0 vs 契约 0.1、qfq DDL 列序漂移、publish R5.5 门缺 week10 三件套——与本次改动域无交叠）。

### 3. 6 策略横验（铁律通用性门槛）
CANSLIM / fall_reversal / tech_etf_mvo_rotation / vol_regime_mom_rev / weekly_smallcap_growth / 周频小市值（三层止损）重转 → **api_portability 全 PASS（blocks=0）**；
用 get_fundamentals 的策略自动获得 B9/B2/B1-pit 注入，不用的逐字节不变（fall_reversal/tech_etf/vol_regime caps=[]）。

### 4. fscore_rsrs v8 重转（新基线，v7 保留为平台基线）
`output/ptrade_export/fscore_rsrs/F-Score选股RSRS择时_v8_framework/F-Score选股RSRS择时_ptrade.py`
（len=68531 / 1662 行）能力注入全确认：
- `_QS_INDUSTRY_CODES = ('801780', '801790', '480000', '490000')`（tuple 渲染修复，set 会 TypeError）
- `_QSIndustryState` / 哨兵 999999 / 双码池；`_qs_gf_maybe_prefetch`/`_qs_gf_auto_cache_get`/`_qs_equity_probe`/`_qs_gf_pit_filter`/`_qs_filter_report_types`
- 棚策略侧 `p_frac = pct / 100.0` 已归一（B2）；validator b2warn=0

### 5. 平台复验清单（待用户执行）
部署 v8 至 PTrade 跑 2026-07 窗口，回贴日志，验收对齐：
- `fscore_pass` 与 v7（34）对比——预期因 B6 双查询 + publ_date PIT 更贴近本地口径
- 候选交集（v8 vs 本地 8 只）≥ **6/8**（known-difference 剔除后目标）
- ⑦ 单列清单（平台恒 8 分 vs 本地 9 分，RD-1）
- `QS_GF_PREFETCH` 日志（批量预取生效：3 表×2 期 list 调用 ≤ 6 次/调仓日）
- `QS_INDUSTRY_POOL industries=801780,801790,480000,490000 pool_size≈121 valid=True`（B1 池构建）
- `QS_EQUITY_PROBE equity_usable=False`（B2 一次性探测，⑦ 恒降级）

## known-difference 登记（双端回测对齐修复铁律——四要素）

### RD-1 ⑦ 增发判定（平台净资产/股本字段族缺失）
- 差异内容：平台 balance/income/valuation 无 total_equity/total_hldr_eqy 族/股本字段 → F-Score ⑦"无增发"恒不计分（最高 8 分）；本地 normal 判（最高 9 分）
- 影响面：fscore 上限差 1 分；FSCORE_MIN=6 资格线两侧一致（8/9 都 ≥6），ROE 并列时取舍微差
- 裁决理由：B-2 探针 8 字段×3 表全 EMPTY 实证彻底缺失；审计裁定落①；缺列 NaN 契约使策略"无数据加分"分支自动降级不判（RD-1 机制由 P-D10 v1.2 实现）
- 对验收影响：剔除项——⑦ 单列清单（双端 fscore 组成对比），不计入 ≥95% 逐位一致目标

### RD-2 ROE 口径（平台无 fin_indicator PIT，profit_ability.roe 非 PIT）
- 差异内容：本地 ROE=fin_indicator PIT（ann_date 过滤）；平台 ROE=profit_ability.roe（date 模式最近值）
- 影响面：Step5 ROE 排序微差（000807 本地 -1.07 vs 平台 10.10 实证），只影响 8 只内排序不改变候选资格集合
- 裁决理由：P3 实证平台无 fin_indicator PIT 表；B-3 维持②；ROE 非筛资格维度
- 对验收影响：剔除项——ROE 列单独对比；候选交集 ≥6/8 目标维持

### RD-3 行业剔除 fail-open（平台反向金融池 vs 本地 get_industry）
- 差异内容：本地 `_is_finance` fail-closed（无法确认 → 剔）；平台 `get_industry` wrapper 池外/池无效 → 哨兵 999999 → 不剔（fail-open）
- 影响面：平台池构建成功时剔除量 = 池（121=银行42+非银79）vs 本地 51；池失败时平台漏剔金融（fail-open 防全剔空仓）
- 裁决理由：B1 初始"平台全剔 300 只空仓"根因；v7 产物 fail-open 实证；审计 B1 双码池裁定
- 对验收影响：行业剔除量差计入 known-difference（剔除后候选对齐）；池构建成功（QS_INDUSTRY_POOL valid=True）时差异收敛为池口径差

## 回退条件
- 平台复验失败（候选交集 <6/8 且不可归因）→ 保留 v7 平台基线，v8 产物降级为候选；框架改动回退 = 恢复 source_import/portability_rules/validators 改动前状态（git checkout 逐文件 + 写前快照）。
- 契约测试回归（非既有 5 失败）→ 立即修复不推送。

## 状态
- 实施 ✅ / 静态验收 ✅（测试 + 横验 + 重转）
- 平台复验 ⏳（用户部署 v8 回贴日志）
- 用户确认 → 双仓推送 + README/docs/strategy_toolbox/prompt_engineering + skill install 同步（六步第 5/6 步，未执行）

## 附加：验收鲁棒性升级（A+B+C+D 总方案，审计批准 2026-08-31）

### 背景
框架修复验收曾绑定单策略（fscore）平台数值对齐——6 横验策略中 weekly_smallcap_growth / 周频三层止损使用 fscore 未覆盖形态（get_fundamentals_batch / or_yoy / eps），换策略存在再踩坑风险。审计批准 A+B+C+D 闭环（D 契约矩阵底座 / C 离线契约回归 / B 分支覆盖率验收 / A P5 探针矩阵）。

### 批次 1 交付（本地，2026-08-31）
| 项 | 交付物 | 状态 |
|---|---|---|
| D | `docs/evidence/fundamentals-contract-matrix.yaml`（唯一事实源）+ `scripts/check_fund_matrix.py`（缺口清单 / --check 哈希+MD 门 / --reverify / --sync）+ 人读 MD 派生 | ✅ 门禁正反例验证通过 |
| B | wrapper 分支覆盖率审计（附表见下）| ✅ |
| C | `tests/test_fund_matrix_coverage.py` 14 测试（B6c 回退两级 / gap 部分缺列 / prefetch SKIP+小池缓存 / eps P-A2 双语 / RD-1/2 固化断言 / report_types / multi2 / cache miss / list 批量 / eps 反证）| ✅ 契约套件 480 passed（-k 过滤；5 项既有失败不变）|
| A | P5 探针探针文件落点 `docs/evidence/probe-p5-matrix-20260831.py` | ⏳ 批次 2（v8.5 复验后）|

### B wrapper 分支覆盖率附表（测试命中）
| 分支 | 测试命中 |
|---|---|
| B6c 主路径（range 透传+拍平+PIT 前置）| test_p10_wrapper_range_split_two_calls / test_b6c_pit_filter_drops_unpublished / test_fm_report_types_filter_range |
| B6c 回退第 1 级（GF-RANGE-FAILOPEN→B6b）| test_fm_b6c_range_exception_fallback_b6b |
| 回退第 2 级（GF-RANGE-FAILOPEN2）| test_fm_b6c_range_failopen2_both_levels |
| gap 种子短路（f04）| test_p10_wrapper_gap_seed_shortcut_first_call / test_fm_rd1_seeds_no_score_semantics |
| gap 动态登记 + 二次短路 | test_p10_wrapper_gap_shortcut_single_alarm |
| gap 部分缺列补 NaN | test_fm_gap_partial_missing_nan_cols |
| prefetch SKIP(pool>32) 幂等（f11）| test_fm_prefetch_skip_pool_gt32 |
| prefetch 小池 + cache_get 命中 | test_fm_prefetch_small_pool_cache_hit |
| cache 未命中回退平台 | test_fm_cache_miss_fallback_platform |
| list 批量（f10）| test_fm_list_multi_secs_contract / p10 系列 |
| report_types（f12）| test_fm_report_types_filter_range / P1 探针 |
| multi2 拍平 / 原样直传 | test_p10_wrapper_range_split_two_calls / test_fm_multi_flat_passthrough_and_flat |
| eps 请求翻译 + 返回逆翻译（f07）| test_fm_eps_basic_translation / test_fm_eps_passthrough_no_translation |
| or_yoy 翻译（f06）| test_p10_field_map_request_translated / test_p10_field_map_only_growth_ability |
| 空→NaN 行（f14）| test_p10_wrapper_missing_field_nan_row |
| 逐股退化 | p10 系列（GF-FAILOPEN） |
| RD-2 ROE 透传（f05）| test_fm_rd2_roe_passthrough_no_ann_date |
| RD-3 行业 fail-open（f13）| test_b1_industry_wrapper_pool_and_failopen |

### 验收口径（三轨，自批次 1 起）
1. **契约断言全绿**：契约套件（-k "ptrade or contract or conversion or source_import or portability or fidelity or fundmatrix"）≥160 passed；wrapper 全分支测试命中（B 表）。
2. **矩阵覆盖完整**：`scripts/check_fund_matrix.py --check` 通过（wrapper 哈希一致 + MD 与 YAML 派生一致）；○ 缺口清单收敛（当前仅 f09，批次 2 由 P5 补证）；wrapper 模板改动未复证即回归 fail。
3. **fscore 数值对齐**（最终确认）：平台复验 fscore_pass≈97 / 候选交集≥6/8 / total_share 0。RD-1/2/3 按登记固化断言，不按缺口计。

## 追加：v8 平台复验归因 + v8.1 修正（2026-08-31 02:35 日志）

### v8 复验结果（用户部署 `F-Score选股RSRS择时_v8_framework`）
- ✅ B1 行业剔除对齐：`QS_STEP1_REMOVED universe=300 finance=51`（平台反向金融池 universe 交集 51 只 = 本地一致）
- ❌ fscore_pass=166（本地 97，v7 平台 34）→ 虚高；候选交集 3/8（688111/600809/600887）未达 6/8
- ❌ total_share 缺列告警 60+ 次/日（QS_SHIM_FIELD_MISSING）→ 每码回退平台调用 + 刷屏
- ✅ ⑦ 降级机制实测：NaN 行 → `ts_now<=ts_prev` 恒 False → 平台恒不加分（RD-1 生效）

### 根因（日志归因）
1. **B6b 同比窗口偏差**：v8 双查询 prev_date 用 `date-1年`（07-01→2025-07-01，平台返回 2025-06-30 中报），而本地 `_latest_statement` 同比匹配"cur end_date 年-1 同月日"（2026-03-31→2025-03-31）→ 平台 prev=None → 本地语义"prev 缺失即加分"吞掉全部同比项 → fscore 虚高 166。**修正：prev 窗口由 cur 最新 end_date 反推**（`_qs_prev_window_date`，cur年-1+同月日）——与本地逐位一致。
2. **B8 缺列刷屏**：⑦ 每次单码查 valuation，预取缓存空（平台缺列），未命中 → 平台调用 + 告警。**修正：g 级 `_qs_gf_field_gaps` 一次性登记 + gap 短路**（全部请求字段已确认缺 → 直接 NaN 行，0 平台调用 0 告警）。

### v8.1 复验（用户部署 `F-Score选股RSRS择时_v81_framework`，2026-08-31 02:46-02:48 回测）
- ✅ B6b/gap 修复代码在位（告警带 table= 前缀 = v8.1 版；行业池 `pool_size=121 valid=True`）
- ❌ **fscore_pass=166 未降、候选交集仍 3/8** → B6b 取不到同比期
- ❌ **total_share 告警仍 60+ 次/日** → gap 运行时登记未在平台生效

### 根因定论（二次归因，实证）
1. **平台 date 查询 = 披露时点语义（非 period 语义）**：date=2025-03-31 时 2025 一季报
   （4 月底披露）未及 → 平台返回 2024-12-31 → **date-only 双查询（v8 date-1年 / v8.1
   curED 年-1同月日）两个窗口都永远拿不到『cur 期年-1 同月日』期** → prev 恒 None →
   同比项按本地『prev 缺失即加分』语义全加分 → fscore_pass=166 稳定。B6b 失效根因。
2. **gap 运行时登记依赖首调探测**：平台侧 g 状态不可靠 → 首调 mark 后二次未短路。
   探针 P2 **已实证** balance/income/valuation × 8 净资产/股本字段全 EMPTY（审计在案）
   → 无需运行时探测，直接内置缺列种子。

### v8.2 产物（三次平台复验）
`output/ptrade_export/fscore_rsrs/F-Score选股RSRS择时_v82_framework/F-Score选股RSRS择时_ptrade.py`
（len=74468 / 1808 行；注入 12/12 OK；validator blocks=0 b2warn=0；契约测试 102 passed）

- **B6c 主路径**：date+range 并存 → 平台原生 `start_year/end_year` 多期透传（单次调用，
  探针 P1 实证 12 期齐全）→ `_qs_multi_flat` 拍平 MultiIndex(end_date,secu_code) →
  `_qs_pit_filter` publ_date<=date（对齐本地『ann_date<=date 最新已披露』）→ 本地
  `_latest_statement` 自取 cur + prev（年-1 同月日）两行 → **同比两期必然在集合内**。
- **B8 seeds**：`_QS_GF_GAP_SEEDS` = 探针 P2 24 组合（3 表 × 8 字段）内置 → ⑦ 首调即
  短路 NaN 行（0 平台调用 0 告警）；运行时动态登记（g._qs_gf_field_gaps）追加合并。
- 双查询（B6b）保留为异常回退（GF-RANGE-FAILOPEN2）。

### v8.2 复验（用户部署 `F-Score选股RSRS择时_v82_framework`，2026-08-31 三次）
- ✅ **种子短路部分生效**：QS_SHIM_FIELD_MISSING 告警消失（seeds 静默缺列）
- ❌ **平台仍收到 total_share 请求**（KeyError 仍刷屏）→ B6c/seeds 逻辑未真正执行

### 根因定论（三次归因 —— v8.0~v8.2 平台复验总根因）
1. **转换管线多模板同名覆盖**：产物含**两个 `def get_fundamentals`**——
   `_QS_FUNDAMENTALS_EXT`（B6c/seeds/PIT 版的 wrapper）先注入，
   `_QS_FIDELITY_EPS_EXT`（P-A2 eps 覆写）后注入 → 模块级**后者覆盖前者**，
   策略实际调用的是旧的 P-A2 版：无 B6 拆分、无 seeds 短路、⑦ total_share 直调平台
   （KeyError 刷屏），B6c range 主路径从不执行（fscore_pass=166 恒虚高）。
   三轮复验（v8/v8.1/v8.2）所有修复均被该覆盖吞掉。
2. 二次归因（平台披露时点语义、g 状态不可靠）为真问题，但**被覆盖后根本不生效**。

### v8.3 产物（四次平台复验）—— 注入管线整合
`output/ptrade_export/fscore_rsrs/F-Score选股RSRS择时_v83_framework/F-Score选股RSRS择时_ptrade.py`
（len=72604 / 1771 行；**唯一 get_fundamentals wrapper（defs=1 验证通过）**；注入 14/14 OK；
validator blocks=0 b2warn=0；fidelity+contract 146 passed）

- **整合**：eps 常量与映射烘焙进 `_QS_FUNDAMENTALS_EXT`（`eps_basis` 由转换器 format 传入）；
  独立 `_QS_FIDELITY_EPS_EXT` 模板（含同名 def）删除，`_inject_all` 不再二次注入。
  产物恒单一 wrapper = B6c range+PIT 主路径 + B8 seeds 缺列短路 + P-A2 eps 双端映射。
- B6c / B6b 回退 / B8 seeds / PIT / multi2 拍平 / eps 映射全部在位且**唯一生效**。

### v8.3 复验（四次，2026-08-31 03:11 回测卡死）
- ✅ **唯一 wrapper 生效铁证**：`QS_GF_PREFETCH date=2026-07-01 table=profit_ability pool=300`
  首次出现（v8.0~v8.2 三轮平台日志从未出现——被 P-A2 覆盖版吞掉）
- ❌ **卡死**：日志停在 QS_GF_PREFETCH 后——`_qs_gf_maybe_prefetch` 对 universe=300
  全池执行 profit_ability list 批量（2 期×300 码），平台该表 list 未获探针实证
  （P-D10 仅 income/valuation 500 码）、实测挂起。

### v8.4 产物（五次平台复验）—— 大池禁止批量预取
`output/ptrade_export/fscore_rsrs/F-Score选股RSRS择时_v84_framework/F-Score选股RSRS择时_ptrade.py`
（len=73306 / 1787 行；唯一 get_fundamentals wrapper（defs=1）；注入 11/11 OK；
validator blocks=0 b2warn=0；契约+fidelity 146 passed）

- **修复**：`_qs_gf_maybe_prefetch` 加 `len(_pool) > 32 → QS_GF_PREFETCH_SKIP`（大池
  不批量 list 预取，回退逐股 = v7/v8.2 覆盖版可跑完路径）；SKIP 按月幂等
  （`_done[table]=_mkey`，防每码重复 SKIP 日志）；小池（≤32）保留批量预取。
- 既有保护不变：B6c range+PIT 主路径、B8 seeds ⑦ 短路、P-A2 eps 映射、行业池 fail-open。

### v8.4 复验（五次，2026-08-31 10:13-10:28 完整跑通）
- ✅ **跑通**：07-01~07-31 全月正常结束；`QS_GF_PREFETCH_SKIP pool=300` 幂等 1 行（卡死修复生效）；
  total_share 异常 0；行业 finance=51
- ❌ **fscore_pass=4（本地 97）、候选交集 0/8**——过度回落。根因：
  **PIT 过滤位置错误**——`_qs_pit_filter` 在 `_qs_frame_to_contract`（字段筛选，丢弃 publ_date）
  **之后**执行，而 `_latest_statement` 请求 fields 不含 publ_date → 过滤恒不生效 →
  平台 range 返回的未披露期（2026-06-30 中报，8 月底才披露）未被剔除 → cur 取错期
  （本地 2026-03-31 vs 平台 2026-06-30）→ 数值/阈值判定全偏；另 publ_date 数值格式
  字符串比较亦会误判。

### v8.5 产物（六次平台复验）—— PIT 前置 + 数值兼容
`output/ptrade_export/fscore_rsrs/F-Score选股RSRS择时_v85_framework/F-Score选股RSRS择时_ptrade.py`
（len=73835 / 1799 行；唯一 wrapper（defs=1）；注入 11/11 OK；validator blocks=0 b2warn=0；
契约+fidelity 146 passed 无警告）

- **修复**：B6c 分支顺序改为 `multi2 拍平 → PIT 过滤（select 前）→ contract`；
  `_qs_pit_filter` publ_date 归一 YYYYMMDD 数值比较（兼容 '2026-04-25' / 20260425）。
- 预期：cur=2026-03-31（本地同语义）、prev=2025-03-31 → fscore_pass 回落至 ≈本地 97。

### v8.7 产物（八次平台复验）—— end_date epoch 毫秒契约（残留根因）
`output/ptrade_export/fscore_rsrs/F-Score选股RSRS择时_v87_framework/F-Score选股RSRS择时_ptrade.py`
（len=76762 / 1866 行；唯一 wrapper（defs=1）；注入 9/9 OK；validator blocks=0 b2warn=0；
契约+fidelity+矩阵 **161 passed**；矩阵哈希门 e9116a557b42）

**根因**（v8.6 平台复验 `fscore_pass=166` 残留）：本地策略以 **epoch 毫秒**消费 end_date
（F-Score L86/92 `np.datetime64(int(end_date),'ms')`、CANSLIM L391 `unit='ms'` 实证）——
wrapper `_qs_norm_fund_dates` 却归一为 YYYYMMDD 数值 → `_latest_statement` 把 20250331 当毫秒
→ 1970 垃圾日期 → prev 同月日匹配恒失败 → 同比项恒加分 → 虚高 166（P5-7 探针复算 79
绕过 wrapper 转换，定位到 end_date 契约）。
**v8.7 修复**：end_date → epoch 毫秒（时区安全 UTC）；publ_date 独立归一（YYYYMMDD）；
`_qs_gf_pit_filter`/`_qs_filter_report_types`/`_qs_prev_window_date` 全链路 ms 适配。
**预期 v8.7 平台复验**：fscore_pass ≈79（= 本地 97 − RD-1 ⑦ −1 分边界 − 微差），首个真对齐轮次。

### v8.6 产物（七次平台复验）—— PIT 空串/值域兜底修复（RD-4 定量）
`output/ptrade_export/fscore_rsrs/F-Score选股RSRS择时_v86_framework/F-Score选股RSRS择时_ptrade.py`
（len=75132 / 1828 行；唯一 wrapper（defs=1）；注入 7/7 OK；validator blocks=0 b2warn=0；契约+fidelity+矩阵 **161 passed**；矩阵哈希门复证 f430d4490788）

**根因**（P5-7 全池复算 vs 平台实跑闭环）：
- P5-7 用平台真实数据复刻 `_f_score`（249 只非金融、B6c range+PIT）→ **fscore_pass=79、cost_missing=0、cur_missing=0**，dist 中位 5 分——但平台实跑仅 3 只过线 → 差异在策略端 wrapper 数据处理（非数据源）。
- **平台 list+range 模式 publ_date 全空**（P5-1 `empty=18` 实证）→ wrapper v8.5 `_qs_pit_filter` 空串 `astype(float)` 抛 ValueError → 整表放行 → **2026-06-30 未披露 NaN 占位期混入并被 `_latest_statement` 取为 cur** → 每只 score 崩溃 → 实跑 3。
- **v8.6 修复**：`_qs_pit_filter` 重写——(1) 值域兜底：非 end_date/publ_date 数值列全 NaN（未披露占位）→ 剔（不问 publ_date）；(2) publ_date 有值且 >date → 剔；缺失/空串 → 不据此剔；逐行安全比对，空串绝不 astype(float)。
- **RD-4 登记**（f15）：平台 range 模式 publ_date 缺失 + 未披露占位判据——P5-1/P5-7 定量（dist/pass6=79/cost_missing=0/cur_missing=0）。
- **预期 v8.6 平台复验**：fscore_pass ≈79（= 本地 97 − RD-1 ⑦ 恒 0 的 -1 分边界效应 − 微差），候选贴近本地高分集。

### v8.5 复验（六次，2026-08-31 14:36-14:52 完整跑通）
| 验收轨 | 判据 | 结果 |
|---|---|---|
| 1 运行 | 全月无卡点、异常可控 | ✅ 07-01~07-31 完整；SKIP 幂等 1 行；total_share 0；PIT 生效（无明显列缺失告警）|
| 2 覆盖 | 矩阵门禁 + 契约断言 | ✅（批次 1 已全绿）|
| 3 数值对齐 | fscore_pass≈97 / 候选交集≥6/8 | ❌ **fscore_pass=3（本地 97）、候选交集 0/8**（cand=605499/300750）|

**归因**：wrapper 契约已正常（③中所有平台实测信号正常：B6c range 执行、PIT 只剔未披露期、seeds 短路、SKIP 幂等）；fscore_pass 3 vs 97 且 v8.4(PIT 位置错)→v8.5(PIT 修正) 仅 4→3——**剩余差异指向平台财务数值口径 vs 本地数据源**（非 wrapper 缺陷）。三只候选 roe=5.80/5.52/6.14 与历轮一致（数据完整读取）。按铁律"根因未证实不得修"，**不动 wrapper**；P5 探针升级加 **P5-6 数值差分**（600000.SS/000001.SZ × income/balance/cashflow × range 多期 + date 单期逐期打印数值），平台回帖后与权威财报（tdx QF 等）对照量级 → 定量登记 known-difference（RD-4：平台财务数值口径）或触发数据层修复。**fscore 验收第 3 轨目标是否调整，待 RD-4 定量后按裁决流程处理（不静默改标准）。**