# F-Score 平台可移植性框架修复 —— 完整验收报告（草稿，六步第 5 步呈报）

> 版本：v8.7b（九次平台复验收敛态）｜日期：2026-08-31｜状态：**待用户/审计对验收目标裁决后推送**
> 主证据：`docs/evidence/framework-portability-v8-20260831.md`（复验轨迹）、`fundamentals-contract-matrix.yaml/.md`（契约矩阵）、`probe-v21b-platform-evidence.md`（P1-P4）、`probe-p5-matrix-20260831.py`（P5 探针）

## 1. 任务背景

QuantStudio 本地回测框架 → PTrade 平台的可移植性修复（框架层，六步流水线）。本地策略源码零改动（mtime 2026-08-28 未变）；修复落点全部为框架层（`source_import.py` 模板/注入管线、`portability_rules.py`、校验器、测试/探针/矩阵资产）。

## 2. 框架修复清单（6 项，全部平台实证）

| # | 缺陷（根因） | v 落点 | 平台实证（九轮复验轨迹） | 静态验收 |
|---|---|---|---|---|
| 1 | **多模板同名覆盖**：P-A2 eps 独立 def 后注入覆盖 B6c/seeds/PIT 全部修复（v8.0~8.2 总根因）| v8.3 注入管线整合（eps 烘焙进统一 wrapper，产物唯一 `def get_fundamentals`）| v8.4 起 `get_fundamentals defs=1` 本地断言 + 平台 SKIP/pool 日志首现（唯一 wrapper 生效）| 唯一 def 断言（重转脚本）|
| 2 | **B6 同比语义**：date-only 双查询（date-1年/反推）取不到"cur 年-1 同月日"期（平台 date 查询=披露时点）| v8.3 B6c：平台原生 range 多期透传 + multi2 拍平 + publ_date PIT | QS_GF_CALL n=200×3 表全量执行；roa_removed 0→15（同比真命中）；fscore_pass 166→35 | test_p10_wrapper_range_split_two_calls / test_b6c_pit_filter_drops_unpublished / test_fm_report_types_filter_range |
| 3 | **大池批量预取挂起**（profit_ability list 300 码）| v8.4 `pool>32 → QS_GF_PREFETCH_SKIP`（幂等）| 平台日志 SKIP 幂等 1 行/表/月（v8.4~8.7b）| test_fm_prefetch_skip_pool_gt32 / test_fm_prefetch_small_pool_cache_hit |
| 4 | **PIT 位置错误**（select 后丢 publ_date → 过滤失效）| v8.5 拍平→PIT→contract + publ 数值兼容 | v8.5 跑通（fscore 数 3 仍受 #5 污染）| test_b6c_pit_filter_drops_unpublished（fields 不含 publ_date）|
| 5 | **PIT 空串 ValueError + 未披露占位**（平台 range publ_date 全空，2026-06-30 NaN 期成 cur → fscore 实跑 3 vs 复算 79）| v8.6 `_qs_pit_filter` 重写：值域兜底（全 NaN 占位剔）+ 空串容错 | P5-7 全池复算（79）与实跑（3）差定位唯一根因；v8.6 修复后 166（残留 #6）| test_fm_pit_empty_publ_date_nan_placeholder_dropped |
| 6 | **end_date 契约**（wrapper 归一 YYYYMMDD vs 本地 ms epoch 消费 → prev 恒失配 → 同比虚高 166）| v8.7 `_qs_norm_fund_dates` end_date→epoch 毫秒 + 全链路适配（gf_pit_filter/report_types/prev_window）| **v8.7b 收敛**：fscore_pass 35、roa_removed 15、total_share 0、B6c 全量执行 | 161 passed（_ed_dstr ms 语义断言全集）|

## 3. 鲁棒性升级（审计批准 A+B+C+D，批次 1 完成）

| 项 | 交付 | 状态 |
|---|---|---|
| D 契约矩阵门禁 | `fundamentals-contract-matrix.yaml`（唯一事实源，16 形态行 × tested/probed/rd/notes + wrapper 哈希）+ `scripts/check_fund_matrix.py`（缺口清单 / --check 哈希+MD 门 / --reverify / --sync）| ✅ **○ 缺口 0（矩阵全绿）**；正反例验证通过；哈希门 d7efdd87b465 |
| C 离线契约回归 | `tests/test_fund_matrix_coverage.py`（16 测试：B6c 回退两级/gap 种子+动态+部分缺列/prefetch SKIP+小池缓存/cache miss/eps 双语+反证/RD-1/2 固化/PIT 空串+list+range/report_types/multi2/list 批量/矩阵缺口快照）| ✅ 契约套件 **161 passed**（-k "…or fundmatrix"）|
| B 分支覆盖率 | wrapper 全分支 × 测试命中表（18 分支，无未覆盖）| ✅ 进验收文档 |
| A P5 探针矩阵 | `docs/evidence/probe-p5-matrix-20260831.py`（docs/evidence/ 落点，审计必改①；P5-1~P5-7 平台实证：list+range/roe list/eps/or_yoy/report_types/fscore 全池复算 79/数值差分）| ✅ 平台 4 轮运行取证闭环 |
| RD 固化断言 | RD-1（⑦ NaN 不加分）、RD-2（ROE 透传）、RD-3（行业 fail-open）契约固化（防意外对齐/偏离漏检）| ✅ |

## 4. 平台复验轨迹（九轮收敛）

| 轮 | 产物 | 结果 | 归因 |
|---|---|---|---|
| v8.0 | B6 date-1年 | fscore_pass 166 / 候选 0 交集 | #1 覆盖 + #2 同比 |
| v8.1 | B6b 反推 | 同 v8.0 | #1 覆盖 |
| v8.2 | +gap 动态 | 同 v8.0（无告警）| #1 覆盖（唯一 wrapper 缺失实证）|
| v8.3 | +range+PIT+seeds | 卡死（profit_ability list 300）| #3 |
| v8.4 | +SKIP 大池 | 跑通；fscore_pass 3 | #5（PIT 置后失效）|
| v8.5 | +PIT 前置 | 跑通；fscore_pass 3 | #5（空串 ValueError 放行）|
| - | P5-1~P5-7 探针 | 平台数值=真实财报；全池复算 79 | **#5 根因闭环** |
| v8.6 | +PIT 值域兜底 | 跑通；fscore_pass 166 | #6（end_date 契约）|
| v8.7 | +ms epoch | 平台偶发卡 2 次 | 平台侧偶发（进度日志 v8.7b 未复现）|
| **v8.7b** | +QS_GF_CALL 进度 | **跑通；fscore_pass 35、roa_removed 15、total_share 0、候选=8 只绩优** | **修复链全收敛** |

## 5. known-difference 清单（RD，四要素登记）

| RD | 差异内容 | 影响面 | 裁决理由 | 对验收影响 |
|---|---|---|---|---|
| RD-1 | 平台 balance/income/valuation 无 total_share 等 8 字段（P2 实证）| fscore ⑦ 平台恒 0 分（fscore 上限 8）| B-2 审计落①；seeds 短路 | ⑦ 单列剔除，不计缺口 |
| RD-2 | 平台 ROE=profit_ability.roe date 模式（非 PIT）| Step5 ROE 排序微差 | P3 实证维持② | ROE 排序差剔除 |
| RD-3 | 行业池 480000/490000.XBHS=121 vs 本地 51 交集；801780 等无效码 fail-open | Step1 剔除集（universe 内交集 51 一致）| P4 + 双码池 | 剔除口径一致，无效码日志 fail-open |
| RD-4 | 平台 range 模式 publ_date 缺失 + 未披露 NaN 占位判据 | B6c PIT 过滤语义（v8.6 修复）| P5-1 empty=18 / P5-7 | 已修复，登记留痕 |
| **RD-5（新）** | **平台 get_fundamentals 财务数值 vs 本地 provider 数据源差**：同 F-Score 判定 fscore_pass 35 vs 97；P5-7 平台数据复算 79（判定实现差）；rv_removed 6 vs 50（行情 RV 口径）| 选股资格集（35 vs 97）、候选 8 只（平台真绩优，ROE 4~11.6）| wrapper 六修复全部平台实证收敛；差异非 wrapper 可消除，属数据源口径差 | **验收目标裁决项**（见 §6）|

## 6. 验收判定（三轨）与待裁决项

- **轨 1 契约断言全绿** ✅：契约套件 161 passed；wrapper 18 分支全覆盖；5 项既有失败不变（slippage/qfq DDL/3×publish robustness，非本次引入）。
- **轨 2 矩阵覆盖完整** ✅：`--check` 通过（哈希 d7efdd87b465 + MD 一致）；○ 缺口 0。
- **轨 3 fscore 数值对齐** ⚠️：平台收敛 fscore_pass=35（≠ 本地 97）——**根因 = 平台财务数据源 vs 本地差异（RD-5），wrapper 已到收敛态不可再修**。候选 8 只均为平台 F-Score 真绩优（000807/002027/601899 等）。

**待用户/审计裁决**：验收目标从"fscore_pass≈97 / 候选交集≥6/8 数值对齐"调整为"**契约三轨全绿 + 已知差（RD-1~5）收敛确认 + 平台候选=平台数据下真实 F-Score 绩优集**"。此为数据源差异下的诚实收敛，不静默改标准（如认可，登记至验收文档定版；如要求继续对齐，需立项数据层对齐（探针差分逐字段口径比对 + provider 层映射），走六步单独方案）。

## 7. 回退条件

- 任一轨回归失败 → 逐文件定向回退（写前快照纪律）；已登记回退基线：v7 产物（SHA f81a2fed…）、各版本产物目录保留。
- 矩阵门禁/脚本/测试/探针为增量资产，可单独禁用不影响框架行为。

## 8. 待办（用户确认后执行）

1. 用户/审计对 §6 裁决项确认（验收目标调整）。
2. 双仓推送（quantstudio-plus / quantstudio）：
   - 框架：`source_import.py`、`portability_rules.py`、`validate_ptrade_portability.py`、`validate_agent_strategy.py`（仓库受管副本）
   - 鲁棒性：`scripts/check_fund_matrix.py`、`docs/evidence/fundamentals-contract-matrix.yaml/.md`、`docs/evidence/probe-p5-matrix-20260831.py`、`tests/test_fund_matrix_coverage.py`、`tests/test_ptrade_contract_compliance.py`、`tests/test_ptrade_fidelity_config.py`、验收文档
   - 文档同步：README / docs/strategy_toolbox / docs/prompt_engineering / skill install（validator 同步至 .agents；check_fund_matrix.py 定位仓库开发工具暂不进 skill，如实施期补接线说明再议）
3. 与 fscore v2 B2 量纲校验就绪项合并清单推送（skill 内不混装）。

## 9. 证据索引

- `docs/evidence/framework-portability-v8-20260831.md`（v8.0~v8.7b 逐轮归因/修复/平台日志）
- `docs/evidence/fundamentals-contract-matrix.yaml|.md`（D 底座：16 形态、RD 四要素、哈希门）+
- `docs/evidence/probe-p5-matrix-20260831.py`（A 探针，平台运行 4 轮）
- `docs/evidence/probe-v21b-platform-evidence.md`（P1-P4 平台契约实证）
- `scripts/check_fund_matrix.py`（缺口/门禁/复证）
- `tests/test_fund_matrix_coverage.py`（C 契约 16 测试）+ `test_ptrade_contract_compliance.py`（161 契约套件）