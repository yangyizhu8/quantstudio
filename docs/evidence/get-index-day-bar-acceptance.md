# 验收证据：get_index_day_bar 框架修复（恐慌抄底指数读数能力）

- 日期：2026-09-08
- 方案：docs/get-index-day-bar-design.md（终审通过稿）
- 流水线状态：步骤3实施 ✅ → 步骤4验收（本文档）→ 待步骤5用户确认 → 待步骤6双仓库推送

## 一、实施内容（与方案 §3.2 逐项对应）

### repo 侧（六步流水线推送载体）
| 文件 | 改动 | 性质 |
|---|---|---|
| quantstudio/backtest/providers/duckdb_data_access.py | +34 行：新增 query_index_day_bars(code, count, before_ms) 专用查询 | 纯加法：显式钉表 FROM index_daily，不进 stock→etf fallback，绝不触发 INDEX_ETF_MAP ETF 代理替换；未动 query_bars_by_count_multi_table 任何既有代码 |
| quantstudio/backtest/providers/duckdb_provider.py | +10 行：get_index_day_bars 透传（直传 epoch-ms 上界） | 纯加法 |
| quantstudio/backtest/ptrade_api.py（共享核心） | +105 行：注入 get_index_day_bar(security, count=1, fields=None) | 纯加法：profile-aware 已完成上界（daily 含 T / minute 永不含 T / proxy 以回调上下文判定 15:00 时钟、不可判 fail-closed 不含 T）；count 越界 [1,250] 之外显性 ValueError；fields 白名单校验 ValueError；QS_INDEX_BAR 诊断日志；空数据 fail-closed |
| tests/test_get_index_day_bar.py（新增） | 19 用例 | 终审钉死断言清单全落实 |
| tests/test_get_index_day_bar_validator.py（新增） | 4 用例 | 双校验规则 + 注册生效验证 |

### skill 侧（SHA-256 记录，见 §四）
| 文件 | 改动 |
|---|---|
| skills/.../references/ptrade-api-signatures.json | get_index_day_bar API 条目（quantstudio_local_backtest / unsupported_on_ptrade / 完整 notes）+ local_only_symbols 登记 |
| skills/.../references/component-catalog.json | local_execution 与 local_backtest_execution 两组登记 |
| skills/.../scripts/validate_agent_strategy.py | 新增 PREOPEN-INDEX-BAR + MINUTE-PROFILE-INDEX-BAR 两 BLOCK 规则 |

## 二、终审 6 项钉死条款落实状态
1. count 越界 ValueError（禁静默截断）→ 落实：n<1 or n>250 raise ValueError；单测 test_count_out_of_range_raises ✅
2. proxy 时钟判定：引擎回调上下文（_proxy_intraday_bars 最后快照时刻 ≥15:00 判定，参照 ptrade_api.py:1159 completed-bar 先例）；不可判 fail-closed 不含 T → 落实 + test_proxy_undeterminable_conservative ✅
3. R1 补 000905 成分 meta 硬核验 → 落实：staging 库 index_constituents_snapshot_meta 存在，000905 共 71 快照（26 complete/45 partial），最新 complete=2026-08-31 n=500/500 dup=0 neg=0 blank=0；回测窗口内 get_index_stocks 严格 as-of 命中 complete 快照，无 DATA_BLOCKED（台账 R1.constituents_meta_check）✅
4. count=2 仅 1 行边界 → 落实：test_count2_with_single_row 返回实际存在行 ✅
5. QS_INDEX_BAR 诊断日志 → 落实：rows/date/mode 三态（daily_incl_T / minute_excl_T / proxy_incl_T / proxy_excl_T_conservative / unknown_profile_excl_T）✅
6. r5_deployment_invariants fail-soft 一致性 → 落实：设计 JSON r5_deployment_invariants.note 显式声明（全部涨停 → 低 exposure 合法，审计行 note=limit_up_skip_all_low_exposure_legal）+ implementation_notes 同步；探针项 D4 编号：D4 登记序列条目 = get_index_day_bar 平台等价物探针（get_history 指数支持 + 日线 include=True 含当日），转换门禁未过前拒绝转换 ✅

## 三、验收结果（方案 §3.5 逐项）

### 3.1 新增单测（19+4=23 用例）
python -m pytest tests/test_get_index_day_bar.py tests/test_get_index_day_bar_validator.py → **23 passed** ✅
- 000001.SS 返回指数数据（断言 close=4002.0 指数点位，非平安银行 12.0 股价）✅
- 600519.SS → 空；510300.SS → 空 ✅
- 000300.SS 返回 CSI300 指数行（断言未触发 510300 ETF 代理）✅
- daily 含 T / minute 永不含 T / proxy 15:00 含 T·09:31 不含·不可判保守侧 ✅
- fields 过滤 + 非法 fields ValueError + count 越界 ValueError + 后缀互通 + fail-closed（无 market / 无日期）✅
- 校验器：PREOPEN-INDEX-BAR BLOCK / MINUTE-PROFILE-INDEX-BAR BLOCK / quantstudio 通过无 MISSING_REUSABLE_API / PTrade 目标 TARGET-LOCAL-EXTENSION-BAN BLOCK ✅

### 3.2 回归与归因（等价性三证明）
1. **全量测试套件**：python -m pytest tests -q → 2793 passed / 84 failed / 4 skipped / 8 xfailed（17min19s）
2. **84 失败全归因为 HEAD 既有（非本次改动引入）**：
   - 方法：提取 84 个失败清单 → 将 3 个改动 repo 文件换回 HEAD 版本 → 重跑全部 31 个失败模块 → HEAD 状态同模块 **85 failed / 574 passed**
   - 差异：HEAD 多失败 1 个（tests/test_qfq_b5_generation.py::test_mcp_without_cutover_fails_closed_when_generation_is_explicit，本次改动后转 PASS 的波动项）；**当前失败集 ⊆ HEAD 失败集，零新增失败** ✅
   - 逐例核验：test_source_import 7 个 include 失败在 HEAD 基线 worktree 复现完全一致；lbdt_dalong BLOCK 4 规则（HARDFILTER-LIMIT 等，策略层历史问题）在 HEAD 校验器下完全一致
3. **黄金冒烟回测三证明**（策略：低流动性溢价换手尾部极值多头.py，窗口 2026-01-05~2026-01-30，副本库，close 撮合）：
   - 独立进程复现（G3.5）：run1/run2 三件套 SHA-256 逐位一致（config=bf4498cb… / daily_stats=8b02320b… / trades=c33c8659…）✅
   - **引擎零改动等价**：HEAD 状态同策略同窗口第三跑，三件套 SHA-256 与改动后两跑**逐位一致（EQUIVALENCE: PASS）** ✅
   - 12 笔成交、无异常、完整导出 ✅

### 3.3 canonical 6 策略横验证（R4 复验证，验收审核缺口①补齐）
按定稿方案 §3.5 口径：canonical 6 策略 = CANSLIM / fall_reversal / tech_etf_mvo_rotation / vol_regime_mom_rev / weekly_smallcap_growth / 周频小市值成长动量（三层止损）。逐个复验证结果：

| 策略（发布文件） | 验证方式 | 结果 | 备注 |
|---|---|---|---|
| CANSLIM突破成长选股策略.py | validate_agent_strategy（canslim_breakthrough design） | **PASS** ✅ | |
| fall_reversal_quantstudio.py | 无 agent design（pre-agent-first legacy）→ py_compile OK + api_portability 契约测试覆盖 | **通过** ✅ | 源码 get_index_day_bar 调用数=0，新校验规则不可能命中 |
| tech_etf_mvo_rotation_quantstudio.py | 同上 | **通过** ✅ | 同上 |
| vol_regime_mom_rev_quantstudio.py | 同上 | **通过** ✅ | 同上 |
| weekly_smallcap_growth_momentum_10_quantstudio.py | validate_agent_strategy（wsgm10 design） | **BLOCK（4 规则）＝ HEAD 校验器结果逐字一致** | DESIGN-CODE-API / HARDFILTER-LIMIT / PORTFOLIO-CASH-BUFFER-CONTRADICTION / PORTFOLIO-EXPOSURE-CONTRADICTION——**legacy 设计-文件漂移，HEAD 同结果，与本次改动无关** |
| 周频小市值成长动量（三层止损）.py | validate_agent_strategy（wsgm10v2 design） | **PASS** ✅ | |

归因铁证：5 个复验证策略源码中 get_index_day_bar 调用数 = **0**（Select-String 实测）——本次校验器改动只新增以该 API 为键的规则，不可能影响任一 canonical 策略的验证结果；wsgm10 的 BLOCK 经 HEAD 校验器对照完全一致，确证为 legacy 漂移。api_portability 契约测试模块（test_ptrade_contract_compliance / test_source_import / test_batch_apis / test_agent_first_strategy_skill）在 2793 passed 全量套件内覆盖上述策略，失败项均为既有/环境性（主库被采集 daemon 独占锁定，IO Error），与本次改动无关。

## 四、skill 双副本 SHA-256 对照表（变更后，验收审核缺口②补齐）

> **权威副本声明**：用户级副本 C:\Users\Administrator\.agents\skills\quantstudio-strategy-compiler 为 ZCode 等会话实际加载的注册表副本（R1 校验/校验器均以其为准）；项目内副本 skills/quantstudio-strategy-compiler/ 为 git 跟踪的开发副本。**同步机制**：本项目内技能 5 文件改动后必须同步至用户级副本（本次已双向核验逐位一致）；任何单副本分叉均属契约分叉，禁止静默留存。skills/ 目录在 git 仓库内被跟踪（审计纠正 §3.6 原文「skill 侧 git 仓库外、无推送载体」不成立），本 5 文件必须进入本次 commit。

| 文件 | 项目副本 SHA-256 | 用户级副本 SHA-256 | 一致 |
|---|---|---|---|
| SKILL.md | 704caabf716e8acb | 704caabf716e8acb | MATCH |
| scripts/validate_agent_strategy.py | 6f1efeba9d28dbdc | 6f1efeba9d28dbdc | MATCH |
| references/ptrade-api-signatures.json | 1b4b11ff4ac6e033 | 1b4b11ff4ac6e033 | MATCH |
| references/component-catalog.json | ca4de586af2d1991 | ca4de586af2d1991 | MATCH |
| references/api-capability-matrix.md | 4003ed200a512e86 | 4003ed200a512e86 | MATCH |

全 5 文件 SHA-256 逐位一致（ALL_SYNCED: True）。

## 五、策略管线续跑（下一阶段）
R3：实现 panic_bottom_fishing strategy.py（信号 get_index_day_bar('000001.SS')）→ R4 校验 → R5 副本库回测（2026-01-01~2026-09-04、100万、G3.5 复现）→ R5.5 EXEMPTED（C3 verbatim 证据已入台账）→ R6 发布

## 六、回退条件确认
- 引擎核心（backtest_engine / 撮合 / 估值 / 快照 / 复权）零改动——git diff 仅含 3 个新增方法文件 ✅
- 回退：git revert 3 repo 文件 + 删除 2 测试文件 + skill 侧按 SHA-256 恢复；黄金冒烟已证明回退前后引擎行为逐位一致
## 七、验收审核补记（非阻断项随缺口一并落）

1. **黄金冒烟 result_dir 绝对路径**（双进程 G3.5 + HEAD 第三跑等价证明所用）：
   - run1: D:/miniQMT策略实盘/QuantStudio/output/backtest_results/20260908_161833_低流动性溢价换手尾部极值多头
   - run2: D:/miniQMT策略实盘/QuantStudio/output/backtest_results/20260908_161928_低流动性溢价换手尾部极值多头
   - HEAD 第三跑: D:/miniQMT策略实盘/QuantStudio/output/backtest_results/20260908_162038_低流动性溢价换手尾部极值多头
   - 三件套 SHA-256：config=bf4498cb9d32dcca / daily_stats=8b02320bd35f5762 / trades=c33c865934e7877f（三跑逐位一致）
2. **test_mcp_without_cutover_fails_closed_when_generation_is_explicit 波动性质**：验收审核定向隔离复跑 FAILED（模块 2 failed/8 passed），证实为**顺序污染型波动项**（全量套件内转 PASS、隔离复跑 FAILED），与本次改动无关——归因时不得将其计入任何修复/回归结论。
3. **fields=['trade_date'] 行为注记（代码 nit）**：get_index_day_bar 固定 set_index('trade_date') 后，单独请求 fields=['trade_date'] 将返回空列帧（信息在 index 中）。契约语义：trade_date 恒为索引，fields 过滤作用于数据列；策略若需日期列应直接读 df.index。已由 test_fields_filter 断言（columns=['close','pctChg'] 且 index.name='trade_date'）。
