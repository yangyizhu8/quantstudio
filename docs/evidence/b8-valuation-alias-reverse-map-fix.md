# B8 修复证据：转换管线 valuation 逆翻译别名丢失（pe_ttm）致 PTrade 平台零交易

- 日期：2026-09-07
- 方案：agent_workspace 会话内 plan v3（审计 v1 放行 / v2 补 2 缺口 / v3 加固终版，ZCode 审计通过）
- 性质：框架层通用缺陷修复（策略生成与转换全链路），策略源码零改动，经重转生效

## 一、根因（审计核验属实）

1. 本地策略请求 `fields=['turnover_ratio','pe_ttm','pb_ratio','float_value']`（低流动性溢价换手尾部极值多头.py:207，用本地别名 pe_ttm）；
2. `_qs_gf_plat_field` 正向翻译：pe_ttm 不在 `_QS_VAL_PLATFORM_MAP` 键集 → 原样透传 → 平台返回含 pe_ttm 列（QS_VAL_MODE 实测）；
3. `_qs_frame_to_contract` `df.rename(columns=_QS_VAL_PLATFORM_REV)` 覆盖式 rename：平台 pe_ttm 列被改名 pe_ratio，pe_ttm 消失；
4. `_qs_fund_select_fields`：请求 pe_ttm 缺失 → QS_SHIM_FIELD_MISSING → 补 NaN；
5. L6 交叉 `pe_ok = isfinite(pe_ttm) and pe_ttm>0` → NaN → False → 437 尾部候选全灭 → L6_cross=0 → 零交易。

**精确触发面**：请求字段名恰为 REV 键（平台原生名）——pe_ttm/pe_static/pb/ps/pcf/total_shares/turnover_rate 七个。

**实施中发现的同族第二层缺陷（一并修复）**：`_QS_GF_GAP_SEEDS` 静态种子含 v8 时代旧实证 `(valuation,'total_share')/`(valuation,'total_shares')`——§17 QS_VAL_MODE 实测平台列集已含 total_shares（total_share 经正向映射可达），种子与事实矛盾 → gap 短路先于别名保留构造执行，请求直接 NaN 契约行（B8-(a) total_shares 场景实测复现）。

## 二、修复内容（全部在 quantstudio/strategy_compiler/source_import.py，wrapper 模板串内）

| 改动 | 内容 |
|---|---|
| 改动 1 | `_qs_frame_to_contract` valuation 逆翻译「rename 覆盖」→「别名保留」：平台列保留 + 本地主名补充（主名已存在不覆盖、平台列不存在跳过、赋值前 copy）——构造级 cure，未来 REV 扩键自动获得安全语义 |
| 改动 2 | `_qs_fund_select_fields` valuation 别名双向兜底（table=='valuation' 门控、available/missing 计算之前复制、REV 派生显式双向表、无别名字段真缺列仍告警） |
| 改动 3 | `_QS_VAL_PLATFORM_REV` 注释语义澄清（双射、别名保留语义、表内容不可重排） |
| 改动 4 | gap 种子校准：`_qs_val_map_enabled` 判型 platform 处原位 discard `total_share/total_shares` 两项过时种子（known_gaps 不触发判型 probe，保 probe 计数契约） |

## 三、同型面排查结论（审计§三.4 前置，实施第一步落档）

- **eps 表**：`_QS_FIDELITY_EPS_FIELD_MAP_REV`（basic_eps/diluted_eps→eps）覆盖式 rename，但本地契约仅暴露 eps 单名、不暴露平台名别名 → 请求名不可能命中 REV 键 → 无缺陷。
- **growth_ability 表**：`_QS_GF_FIELD_MAP_REV`（operating_revenue_grow_rate→or_yoy）同上，本地仅 or_yoy → 无缺陷。
- **复活条件（显性化落档）**：未来 provider 向 eps/growth_ability 表新增平台名同名列（即本地暴露 basic_eps/diluted_eps/operating_revenue_grow_rate 作可请求别名）即触发同型缺陷——届时须按本批"别名保留"构造同步改造。

## 四、验收结果

### 单元测试（分组 a-f，tests/test_ptrade_contract_compliance.py 追加 6 用例）
- (a) `test_b8_valuation_rev_parametrized_alias_preserved`：REV.items() 程序化参数化（七键自动覆盖，非硬编码）——中间层双列并存同值 + 最终输出主名/别名同请求双列命中零告警 ✅
- (b) `test_b8_valuation_local_primary_name_regression`：主名请求回归（pe_ratio→pe_ttm 往返）✅
- (c) `test_b8_published_strategy_mixed_fields_no_alarm`：已发布策略实际混用组合零 SHIM 告警、全列有值 ✅
- (d) `test_b8_true_missing_field_still_alarms`：无别名字段真缺列仍告警（p10 语义不被吞）✅
- (e) `test_b8_turnover_synth_fallback_still_works`：平台无 turnover_rate 列时 §18 合成兜底仍产出非 NaN turnover_ratio ✅
- (f) `test_b8_local_alias_surface_full_coverage`：本地别名面 8 字段（清单源=duckdb_data_access query_valuation_* SELECT 别名 :1422-1424/:1458-1465）逐一往返命中 ✅

### 回归
- B8 六用例 + `test_p10_wrapper_gap_seed_shortcut_first_call` + `test_p10_wrapper_range_split_two_calls`：8/8 PASS（后两者为实施中引入又修复的回归——known_gaps 判型调用改为 probe 处原位校准后恢复基线语义）
- `tests/test_source_import.py` + `tests/test_ptrade_contract_compliance.py` 全量：158 passed / 7 failed
- **7 个失败基线归因**：test_22/23/27/32/33/34/35（include 版本标识 diag 断言，test_source_import.py:868）——经 git stash 基线核对（stash 后 test_22 仍 FAIL）确认为**既有失败**，属 include 版本标识进行中工作（另一线），与本次 valuation 修复无关。本次改动前后失败集完全一致（零新增失败）。

### 多策略横验证（铁律门槛）
`python scripts/run_contract_gate.py --strategies`：6 策略 api_portability 冒烟全通过（CANSLIM/fall_reversal/tech_etf_mvo_rotation/vol_regime_mom_rev/weekly_smallcap_growth/周频小市值成长动量）。CONTRACT GATE : PASS。

### 矩阵哈希同批追认
wrapper 模板哈希 9e00d6600a4a → 7a424f397983；`python scripts/check_fund_matrix.py --check --reverify` 执行，f01-f16 全场景 tested/probed 复证通过，docs/evidence/fundamentals-contract-matrix.{yaml,md} 哈希更新与本修复同 commit。

### 文档同步核查（审计§三）
经核查 README.md、docs/strategy_toolbox.md、docs/prompt_engineering.md 均无涉及 valuation 逆翻译别名机制的表述 → **无需同步**（显式记录，不留"未同步"疑点）。

## 五、关单标准（平台复测——已通过，2026-09-07 15:19）

重转「测试1」（qs-compile import）上传 PTrade 实测（2026-07-01 调仓月快速复测），三项关单标准全部通过：

| # | 标准 | 缺陷前 | 复测结果 | 判定 |
|---|---|---|---|---|
| 1 | QS_SHIM_FIELD_MISSING (valuation, pe_ttm) 消失 | 告警存在 | 零出现 | ✅ |
| 2 | QS_FUNNEL_AUDIT L6_cross>0 | L6_cross=0 | **L6_cross=185**（L5_tail=437 → L6=185 → L7=185 → keep=92 → R_selected=12） | ✅ |
| 3 | 调仓日有成交 | buy_submitted=0 | **buy_submitted=12**、12 笔买入订单落单、positions=12、cash_ratio=0.0533、gross_exposure=0.9467 | ✅ |

复测订单清单：601857/601288/601988/601398/001965/601728/601998/600000/601658/600018/601319/600028（12 只，与低换手尾部特征吻合——大盘蓝筹低换手标的）。

**结论：B8 修复平台复测通过，正式关单。**

## 六、回退条件
- 任一既有测试 FAIL（非上述 7 个既有失败）→ git 定向 revert 本修复；
- 回退 commit 同样需 reverify 矩阵哈希；
- 回退点：stash baseline（提交前创建）。