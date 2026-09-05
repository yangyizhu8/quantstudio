# A2 回归基线钉死清单（2026-09-05，A2 开工前置）

> 用途：主线 A（F-DUCKDB-LOCK）验收门③「既有失败不变」的比较基准。钉死时点：HEAD d12be27 + 工作树在途漂移（见 §3）。

## 1. HEAD 基线白名单（run_contract_gate.py KNOWN_FAILS，机器强制，5 项）
1. tests/test_ptrade_public_signature_contract.py::test_slippage_signatures_match_ptrade_keyword_contract（slippage）
2. tests/test_qfq_schema_status.py::TestContractConsistency::test_duckdb_cols_matches_ddl_order（qfq DDL）
3. tests/test_strategy_name_chinese_contract.py::test_publish_writes_chinese_filename_and_front_blocks_collision（publish robustness 1/3）
4. tests/test_target_aware_strategy_skill.py::test_local_only_publish_generates_no_ptrade_placeholder（publish robustness 2/3）
5. tests/test_ptrade_profile_registered_stock_apis.py::test_registered_stock_api_source_publishes_identical_dual_targets（publish robustness 3/3）
（v87b 轨 1 在案：非本次引入，未立项修复前 gate 白名单放行。）

## 2. 钉死时点实测（2026-09-05，python scripts/run_contract_gate.py）
- **白名单 5 项本轮未触发**（当前 collect 范围内通过或未跑——gate 语义：白名单允许失败不拦截）。
- **在途漂移层新增失败 5 项**（不在白名单、gate 拦截）：
  - tests/test_fund_matrix_coverage.py::test_fm_rd1_seeds_no_score_semantics（L196: assert 1 == 0）
  - tests/test_fund_matrix_coverage.py::test_fm_list_multi_secs_contract（L352: list 批量单调用）
  - tests/test_ptrade_contract_compliance.py::test_p10_wrapper_gap_seed_shortcut_first_call（L1668: 种子短路应 0 次平台调用）
  - tests/test_ptrade_contract_compliance.py::test_p10_wrapper_gap_shortcut_single_alarm（L1719: gap 短路应 0 次平台调用）
  - tests/test_ptrade_contract_compliance.py::test_p10_wrapper_list_fallback_per_code（L1803: assert 3 == 2）
- **矩阵哈希门 FAILED**：wrapper 模板内容哈希 ≠ 矩阵 YAML 记录（提示 --check --reverify）。

## 3. 漂移归因（非 A2、非本会话引入）
工作树他方未提交在途改动（git status 实测 + diff -U2 只读核对）：
- quantstudio/strategy_compiler/source_import.py +42（§21 ROE 覆盖确认探针注入，2026-09-04 双端对齐裁定项③，模板串 L2214+ 区域）——missing 码场景额外 2 次 orig 调用 → 直接击穿 p10 系「单调用/零调用」契约断言与矩阵哈希门；
- quantstudio/backtest/ptrade_api.py ±4（D4 缓存键修复，08-28 注释）；
- tests/test_daemon_lifecycle.py ±2。
HEAD 对照：c9a20ab 时点 CONTRACT GATE PASS ×4 在案（pctchg-portability §9 验收链）。**结论：当前 gate 红 = 他方在途探针态，非稳定代码基线。**

## 4. A2 门③比较规则（钉死语义）
- A2 验收重跑时：**在途漂移层 5 项与矩阵红不计入 A2 放行/否决**（归属他方会话，按其提交后状态另行评价）；
- A2 通过标准 = 契约套件失败集 ⊆ {KNOWN_FAILS ∪ 在途漂移 5 项（若漂移仍在）}，**A2 改动引入的任何新失败 = 否决回退**；
- 白名单 5 项 + 漂移 5 项逐项断言文本已钉死（§1/§2），比对以此为准；
- 若漂移层已落地（他方提交+reverify），以届时新实测为补充钉记录（追加节，不改本节）。
- 回退点：stash@{0} = afbd4088a754...（a2-impl-baseline-20260905，refs/stash 持久）。

## 5. 漂移归因修正（2026-09-05 §21 收口核验裁定，DSH 确认）

§3 旧归因（"§21 ROE 探针 → 击穿 p10 系断言与矩阵哈希门"）**经 §21 收口实测修正**：

| 项 | 修正后归因 |
|---|---|
| **矩阵哈希红** | §21 ROE 探针注入贡献（source_import +42 改变 wrapper 内容哈希）——**探针已回滚还原**（工作树回 HEAD、QS_ROE_PROBE 归零、代码归档 §21.4a），矩阵哈希门自愈 rc=0 缺口 0 |
| **p10 五项失败** | **与 §21 探针无关**——真因 = §17 判型探针（**3d96530**，09-04 已提交已验收）在 wrapper 内加 +1 次判型调用（L1714 `orig(['600000.SS'],'valuation')`，无 g 时每上下文触发），而 p10 测试断言（09-01 f7dc3a6 时代）未同步——**测试陈旧**，非回归 |
| p10 处置 | 断言已按实测语义更新（fund_matrix 2 项：判型 probe 计入预期+形态断言；compliance 3 项：seed=1 次 probe 锁定 / gap=每请求 1 次现状锁定+**gap-range 登记失配回归另案登记** / list_fallback=逐码 2 次现状锁定+判型零外呼实证）——重跑 **118/118 全绿** |
| §21 ROE 定谳 | 探针未采到数据（平台运行条件未发生）——"永久 vs 期间"留档待再采数（采数条件见 ptrade-platform-absorptions §21.4a）；mini-方案证据不足暂不立项 |
