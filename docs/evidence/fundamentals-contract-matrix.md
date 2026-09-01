# fundamentals-contract-matrix（平台契约白名单 · 唯一事实源 YAML 派生）

> 本文件由 `scripts/check_fund_matrix.py --sync` 从 `fundamentals-contract-matrix.yaml` 生成，**禁止手工编辑**（一致性由 --check 门禁校验）。

> 状态：✅ tested/probed 均真；🔶 单侧；○ 缺口（probed 或 tested 假）。RD-1/2/3 为 known-difference 固化断言（不计缺口）。

> wrapper 模板哈希：`11da6b6e6868`；最后复证：`2026-08-31`

## 一、形态矩阵（YAML 行）

| id | shape | mode | table | fields | tested | probed | probe_ref | rd | notes |
|---|---|---|---|---|---|---|---|---|---|
| f01 | single | date-only | income_statement | np_parent_company_owners,operating_revenue,operating_cost,end_date | True | True | P1 | - | single+date-only 基线；逐股退化路径 |
| f02 | single | date+range | income_statement | np_parent_company_owners,operating_revenue,operating_cost,end_date | True | True | v8.7b 九次复验（2026-08-31）：B6c range 全量执行 QS_GF_CALL n=200×3表；fscore_pass 166→35；roa_removed 0→15（同比真命中） | - | B6c 主路径收敛：range 多期+mul2 拍平+PIT 值域兜底+end_date epoch 毫秒契约（v8.7）；fscore 数值收敛 35（数据源差见 RD-5） |
| f03 | single | date-only | balance_statement | total_assets,total_liability,total_current_assets,total_current_liability,end_date | True | True | P1 | - |  |
| f04 | single | date-only | valuation | total_share | True | True | P2 | RD-1 | 8 字段族 EMPTY → seeds 首调短路 NaN 行；⑦ 恒不加分（RD-1 固化断言） |
| f05 | single | date-only | profit_ability | roe | True | True | P3 | RD-2 | date 模式非 PIT（无 ann_date）；仅 Step5 排序消费（RD-2 固化断言）；roe 列表批量 probe:P5 |
| f06 | single | date-only | growth_ability | or_yoy,publ_date,end_date | True | True | P1 | - | or_yoy→operating_revenue_grow_rate 请求翻译 + 返回逆翻译；PIT 前置保留 publ_date（weekly/周频形态） |
| f07 | single | date-only | eps | eps,publ_date,end_date | True | True | 探针乙（2026-08-24 fidelity，平台 basic_eps/diluted_eps 列存在） | - | P-A2：basis=basic → eps→basic_eps 请求翻译 + 返回逆翻译（CANSLIM/weekly/周频形态）；P5-3 平台 basic_eps/diluted_eps 列存在实证 |
| f08 | batch-single | date-only | * | float_value | True | True | P1 | - | get_fundamentals_batch 委托 list 模式（P-D10 实证 500 码 0.05s）；weekly/周频形态 |
| f09 | list | date+range | income_statement | np_parent_company_owners,end_date | True | True | P5-1（2026-08-31） | - | list+range 小池：P5-1 平台实证（18 行 MultiIndex 6 期）+ test_fm_list_range_small_pool 契约补证 → 缺口闭合 |
| f10 | list | date-only | valuation | float_value | True | True | P1 | - | list+date-only（P-D10 500 码） |
| f11 | prefetch-pool | date-only | profit_ability | roe | True | True | v8.4 平台实证（QS_GF_PREFETCH_SKIP pool=300 幂等 1 行） | - | pool>32 → QS_GF_PREFETCH_SKIP（v8.3 大池卡死保守化，2026-08-31；P5 若证大池 list 可用留作放宽依据，本轮不动） |
| f12 | single | date+range+report_types | income_statement | np_parent_company_owners,end_date | True | True | P1 | - | report_types 1/2/3/4 → 0331/0630/0930/1231 过滤（P1 实证） |
| f13 | industry | pool | get_industry_stocks | - | True | True | P4 | RD-3 | 480000.XBHS+490000.XBHS 并集 121；无效码（801780 等）fail-open → 哨兵 '999999'（RD-3 固化断言） |
| f14 | single | empty-return | * | - | True | True | P2 | - | 平台空返回 → 1 行 NaN 契约（P-D10 v1.2）；策略 KeyError 免疫 |
| f15 | single | date+range-pit | income_statement | np_parent_company_owners,publ_date,end_date | True | True | P5-1/P5-7（2026-08-31） | RD-4 | 平台 list+range 模式 publ_date 全空（P5-1 empty=18）→ PIT 空串须容错；未披露占位期（2026-06-30 值全 NaN）由值域兜底剔除——v8.6 修复（v8.5 空串 ValueError 整表放行 → NaN 期成 cur → fscore 实跑 3 vs 探针复算 79） |
| f16 | data-source | diff | * | - | True | True | v8.7b 九次复验 + P5-7（2026-08-31） | RD-5 | 平台 get_fundamentals 财务数值与本地 provider 数据源差（非 wrapper 可修）：同 F-Score 判定下 fscore_pass 35（平台）vs 97（本地）；P5-7 平台数据复算 79（判定实现差）；rv_removed 6 vs 50（行情 RV 口径差）；候选 8 只均为平台真绩优（ROE 4~11.6） |

## 二、○ 缺口清单（当前未证组合）

- 无（矩阵全绿）

## 三、探针证据索引

| ref | 内容 |
|---|---|
| P1 | date-only/range 多期形态 + publ_date PIT 9/12（probe-v21b-platform-evidence.md） |
| P2 | 3 表×8 净资产/股本字段 EMPTY（种子缺口） |
| P3 | profit_ability.roe date 模式 OK / 无 fin_indicator |
| P4 | get_industry_stocks 480000.XBHS=42 银行池 |
| P5 | 待运行：probe-p5-matrix-20260831.py 已落盘 docs/evidence/（list+date+range 小池 / roe list / eps / or_yoy / report_types×range）；P5-7 dist={0:3,1:6,2:20,3:35,4:47,5:59,6:45,7:21,8:13} pass6=79 cost_missing=0 cur_missing=0（RD-4 定量证据）；v8.7b 九次复验收敛：fscore_pass 35、roa_removed 15、total_share 0——RD-5 定量证据 |
