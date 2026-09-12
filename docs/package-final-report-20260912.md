# 数据包终态报告（88/88 全量盘点）

- 生成：2026-09-12 21:59（总调度指令 2026-09-12 21:3x）
- 包文件：quantstudio_data_package_20260912.db（38.6 GB）
- 盘点口径：collector_tasks.json 的 88 任务表 —— 逐表 存在性 / 行数 / 日期范围 / manifest 勾稽
- **结论：INVENTORY PASS** —— 88 表存在、0 缺失、0 行数勾稽不符

## 一、包结构

| 段 | 表数 | 行数 | 来源 |
|---|---|---|---|
| Part 1（主库拷贝）| 51 | 250,813,103 | file-copy（schema=complete_2_1，约束完整）|
| Part 2（QuestDB 回填）| 64 | 186,315,351 | chain 30天/片 + B+ ledger |
| 附属（非任务表）| 27 | — | 随 Part1 复制（qfq_* 14 / source_watermark / 备份表等）|
| **合计** | **115** | **426,295,560** | — |

## 二、盘点全表清单（88 任务表）

| # | 表 | 来源 | 行数 | 日期范围 | 状态 |
|---|---|---|---|---|---|
| 1 | ai_research_snapshot | Part1 | 17,239 | - | OK |
| 2 | balance_statement | Part1 | 167,486 | 2018-01-02 ~ 2026-07-31 | OK |
| 3 | block_trade | Part1 | 61,290 | 2018-01-10 ~ 2026-08-03 | OK |
| 4 | broker_monthly | Part1 | 365 | 2026-06-21 ~ 2026-06-21 | OK |
| 5 | broker_recommend | Part1 | 13,326 | - | OK |
| 6 | cashflow_statement | Part1 | 129,207 | 2018-04-04 ~ 2026-06-04 | OK |
| 7 | cninfo_first_rating | Part2 | 551,008 | 2015-01-05 ~ 2026-06-24 | OK |
| 8 | cnthesims_events | Part2 | 102,625 | 2025-07-14 ~ 2026-09-11 | OK |
| 9 | cnthesims_factors | Part2 | 69,029 | 2025-07-14 ~ 2026-09-11 | OK |
| 10 | cyq_chips | Part2 | 106,802,636 | 2023-05-30 ~ 2026-09-11 | OK |
| 11 | cyq_perf | Part2 | 3,345,767 | 2024-03-21 ~ 2026-09-10 | OK |
| 12 | daily_info | Part2 | 37,016 | 2018-01-02 ~ 2026-09-11 | OK |
| 13 | em_first_cover_rating | Part2 | 9,036 | 2022-08-31 ~ 2026-09-11 | OK |
| 14 | etf_adj_factor | Part2 | 1,618,279 | 2005-02-23 ~ 2026-09-11 | OK |
| 15 | etf_basic | Part1 | 1,606 | 2005-02-23 ~ 2026-07-22 | OK |
| 16 | etf_daily | Part1 | 2,147,623 | 2018-01-02 ~ 2026-09-10 | OK |
| 17 | etf_dividend | Part1 | 882 | 2006-05-13 ~ 2026-08-11 | OK |
| 18 | etf_minutes | Part1 | 120,926,583 | 2025-01-02 ~ 2026-09-08 | OK |
| 19 | etf_sentiment_daily | Part2 | 230 | 2026-07-24 ~ 2026-09-11 | OK |
| 20 | etf_share_size | Part2 | 1,952,321 | 2010-01-04 ~ 2026-09-10 | OK |
| 21 | factor_value | Part2 | 5,740 | 2018-01-03 ~ 2026-04-23 | OK |
| 22 | fin_indicator | Part1 | 135,840 | 2018-04-04 ~ 2026-08-01 | OK |
| 23 | forecast_vip | Part2 | 66,808 | 2017-01-16 ~ 2026-09-10 | OK |
| 24 | gisisi_daily | Part2 | 812 | 2011-01-07 ~ 2026-07-24 | OK |
| 25 | hm_detail | Part2 | 30,749 | 2025-03-25 ~ 2026-09-10 | OK |
| 26 | hm_list | Part2 | 6,496 | 2026-06-21 ~ 2026-09-11 | OK |
| 27 | idx_anns | Part2 | 1,088 | 2024-05-08 ~ 2026-09-11 | OK |
| 28 | idx_factor_pro | Part2 | 6,235,737 | 2018-01-02 ~ 2026-09-11 | OK |
| 29 | income_statement | Part1 | 128,764 | 2018-04-04 ~ 2026-06-04 | OK |
| 30 | index_classify | Part2 | 7,908 | 1984-05-09 ~ 2026-09-03 | OK |
| 31 | index_constituents | Part1 | 246,707 | 2018-01-31 ~ 2026-07-31 | OK |
| 32 | index_daily | Part1 | 18,795 | 2018-01-02 ~ 2026-08-03 | OK |
| 33 | industry_classification | Part1 | 62 | - | OK |
| 34 | inst_survey | Part2 | 100,056 | 2021-08-05 ~ 2026-09-10 | OK |
| 35 | limit_cpt_list | Part2 | 13,815 | 2023-11-13 ~ 2026-09-11 | OK |
| 36 | limit_list_d | Part2 | 165,588 | 2019-11-28 ~ 2026-09-11 | OK |
| 37 | limit_list_ths | Part2 | 54,633 | 2023-11-01 ~ 2026-09-11 | OK |
| 38 | limit_step | Part2 | 13,809 | 2023-11-13 ~ 2026-09-11 | OK |
| 39 | llm_text_events | Part2 | 129,977 | 2018-01-02 ~ 2026-05-07 | OK |
| 40 | llm_text_events_enriched | Part2 | 129,977 | 2018-01-02 ~ 2026-05-07 | OK |
| 41 | llm_text_raw_feed | Part2 | 129,977 | 2018-01-02 ~ 2026-05-07 | OK |
| 42 | margin | Part2 | 5,079 | 2018-01-02 ~ 2026-09-10 | OK |
| 43 | margin_detail | Part2 | 5,710,368 | 2018-01-02 ~ 2026-09-10 | OK |
| 44 | margin_secs | Part2 | 6,935,451 | 2018-01-02 ~ 2026-09-11 | OK |
| 45 | market_breadth_daily | Part2 | 4,055 | 2010-01-04 ~ 2026-09-11 | OK |
| 46 | moneyflow_cnt_ths | Part2 | 187,774 | 2024-09-10 ~ 2026-09-11 | OK |
| 47 | moneyflow_ind_ths | Part2 | 43,560 | 2024-09-10 ~ 2026-09-11 | OK |
| 48 | moneyflow_ths | Part2 | 2,139,387 | 2019-07-26 ~ 2026-09-11 | OK |
| 49 | news_sentiment | Part2 | 120,965 | 2026-06-19 ~ 2026-09-11 | OK |
| 50 | report_rc | Part2 | 2,896,330 | 2010-01-01 ~ 2026-09-10 | OK |
| 51 | rsshub_raw | Part2 | 41,281 | 2026-03-19 ~ 2026-09-11 | OK |
| 52 | sector_top300_daily | Part2 | 14,400 | 2026-07-02 ~ 2026-09-11 | OK |
| 53 | sentiment_factor_daily | Part2 | 15,488 | 2017-08-25 ~ 2026-09-12 | OK |
| 54 | slb_len | Part2 | 1,834 | 2018-01-02 ~ 2025-07-25 | OK |
| 55 | stk_auction | Part2 | 2,248,484 | 2025-01-16 ~ 2026-09-11 | OK |
| 56 | stk_factor_pro | Part2 | 14,636,865 | 2010-01-04 ~ 2026-09-11 | OK |
| 57 | stk_limit | Part2 | 16,978,511 | 2010-01-04 ~ 2026-09-11 | OK |
| 58 | stock_basic | Part1 | 5,222 | 1990-12-01 ~ 2026-07-30 | OK |
| 59 | stock_daily | Part1 | 9,742,665 | 2018-01-02 ~ 2026-09-11 | OK |
| 60 | stock_daily_valuation | Part1 | 9,679,815 | 2018-01-02 ~ 2026-09-11 | OK |
| 61 | stock_dividend | Part1 | 54,023 | 1991-02-26 ~ 2026-07-31 | OK |
| 62 | stock_float_share | Part1 | 744,067 | 2018-01-02 ~ 2026-08-13 | OK |
| 63 | stock_minutes | Part1 | 95,746,406 | 2026-01-05 ~ 2026-09-04 | OK |
| 64 | stock_namechange | Part1 | 7,451 | 1994-01-03 ~ 2026-07-31 | OK |
| 65 | style_cross_section_daily | Part2 | 4,055 | 2010-01-04 ~ 2026-09-11 | OK |
| 66 | survey_sentiment | Part2 | 189 | 2026-06-21 ~ 2026-09-11 | OK |
| 67 | sw_daily | Part2 | 1,515,236 | 2010-01-04 ~ 2026-09-11 | OK |
| 68 | sw_weight | Part2 | 4,346,929 | 2024-01-02 ~ 2026-09-11 | OK |
| 69 | tdx_theme_etf_overlay | Part2 | 1,138 | 2026-01-05 ~ 2026-09-11 | OK |
| 70 | tdx_theme_llm_scores | Part2 | 15,230 | 2026-07-21 ~ 2026-09-11 | OK |
| 71 | tdx_theme_news | Part2 | 15,993 | 2022-01-12 ~ 2026-09-11 | OK |
| 72 | tdx_theme_sentiment_daily | Part2 | 7,824 | 2022-01-13 ~ 2026-09-12 | OK |
| 73 | ths_daily | Part2 | 2,661,227 | 2018-01-02 ~ 2026-09-11 | OK |
| 74 | ths_hot | Part2 | 365,449 | 2023-08-21 ~ 2026-09-10 | OK |
| 75 | ths_index | Part2 | 1,434 | 2007-08-01 ~ 2026-07-31 | OK |
| 76 | ths_member | Part2 | 3,536,866 | - | OK |
| 77 | top_inst | Part2 | 85,513 | 2023-02-10 ~ 2026-09-10 | OK |
| 78 | top_list | Part2 | 104,522 | 2020-01-02 ~ 2026-09-10 | OK |
| 79 | trade_calendar | Part1 | 3,652 | 2017-01-01 ~ 2026-12-31 | OK |
| 80 | ws_asfund | Part2 | 42,974 | 2026-04-10 ~ 2026-06-23 | OK |
| 81 | ws_blocktrade | Part2 | 2,773 | 2026-06-02 ~ 2026-06-23 | OK |
| 82 | ws_etf_holders | Part2 | 3,132 | 2026-03-31 ~ 2026-06-18 | OK |
| 83 | ws_etf_holdings | Part2 | 12,356 | 2026-06-08 ~ 2026-06-19 | OK |
| 84 | ws_ipo | Part2 | 3,427 | 2010-01-01 ~ 2026-06-18 | OK |
| 85 | ws_lhb | Part2 | 8,496 | 2026-06-05 ~ 2026-06-26 | OK |
| 86 | ws_margintrade | Part2 | 14,361 | 2026-06-05 ~ 2026-06-26 | OK |
| 87 | ws_reserve | Part2 | 5,278 | 2025-12-31 ~ 2026-03-31 | OK |
| 88 | xueqiu_sentiment | Part1 | 1,133 | 2026-06-10 ~ 2026-07-29 | OK |

## 三、三轮验证汇总

| 验证 | 口径 | 结果 |
|---|---|---|
| Part 1 核验 | 主库 51 表 -> 包 逐表行数 | **51/51 一致** |
| V4 对账 | 包 vs 本地 QuestDB 逐表行数 | **62/64 精确** + 2 实时 feed 表时点差 |
| V2 对账 | 包 vs 云端（B3 口径）| **PASS 52 / DIFF 5 / EMPTY 7 (64 表全覆盖)**，对拍 21,430 行 |

## 四、覆盖与差异声明（随包）

- stock_minutes 覆盖至 2026-09-04（v1.2 裁定：分钟表不回填，现状如实声明）
- etf_minutes 覆盖至 2026-09-08（同上）
- 事件驱动滞后表按源内现状：ths_member 止 8/13、ws_* 族止 6 月、llm_text 三表止 5/7、slb_len 止 2025-07
- rsshub_raw / news_sentiment 为实时增长 feed 表：包为某时点快照，V4 差异=快照后新增行（非丢失）
- stock_daily / stock_daily_valuation 已补齐 9/4-9/11 缺口窗口（各 +27,747 行；2026-09-12 阶段 2 缩编补跑：先回拨水位至 9/3 修『水位前移吞窗口』再增量拉取）
- V2 5 表差异（非回填缺陷，源侧既有差异）：cninfo_first_rating, hm_list, llm_text_events_enriched, stk_factor_pro, ths_hot
- V2 不可对账：ths_member（in_date 本地全空；ingest_time 为本地生成列，不可作跨库日期锚）

## 五、qfq 语义声明

- 包内 qfq_* 表：14 张
- source_watermark 随包：True
- released 门语义：运行时 resolved aux 路径取决于 qfq_orchestrator released 门；released=false 时走 legacy qfq_aux.db

- 包状态：**定稿（待推送令）**