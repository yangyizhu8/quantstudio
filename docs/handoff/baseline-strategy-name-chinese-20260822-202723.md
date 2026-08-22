# 工作区基线快照（strategy-name-chinese 实施前）

- 时间: 20260822-202723
- HEAD: 34ec800 docs(skill): sync SKILL.md platform-absorption clause with P-D9 coverage

## git status --porcelain
``n M AGENTS.md
 M README.md
 M config/alignment_rules.json
 M config/profiles/mcp_only/alignment_rules.json
 M config/profiles/mcp_only/collector_tasks.json
 M docs/mcp_migration/full_table_inventory.json
 M docs/prompt_engineering.md
 M docs/strategy-compiler/implementation-status.md
 M docs/strategy-compiler/ptrade-profile-contract.md
 M docs/strategy_toolbox.md
 M quantstudio/backtest/backtest_engine.py
 M quantstudio/backtest/events.py
 D quantstudio/backtest/strategies/first_board_pullback_daily__candidate_quantstudio.py
 D quantstudio/backtest/strategies/sw_industry_etf_rotation_8f__candidate_quantstudio.py
 M quantstudio/gui/tabs/config_editor_tab.py
 M quantstudio/gui/tabs/task_tab.py
 M quantstudio/pipeline/config_lint.py
 M quantstudio/pipeline/daemon.py
 M quantstudio/pipeline/qfq_aux_router.py
 M quantstudio/pipeline/qfq_calendar.py
 M quantstudio/pipeline/qfq_event_discovery.py
 M quantstudio/pipeline/qfq_formal_canary.py
 M quantstudio/pipeline/qfq_formal_cutover.py
 M quantstudio/pipeline/qfq_formal_cutover_cli.py
 M quantstudio/pipeline/qfq_formal_postcutever_audit.py
 M quantstudio/pipeline/qfq_invariant.py
 M quantstudio/pipeline/qfq_maintenance.py
 M quantstudio/pipeline/qfq_observation.py
 M quantstudio/pipeline/qfq_orchestrator_cli.py
 M quantstudio/pipeline/qfq_reanchor_schema.py
 M quantstudio/pipeline/qfq_resident_orchestrator.py
 M quantstudio/pipeline/qfq_revision.py
 M quantstudio/pipeline/qfq_schema_migration.py
 M quantstudio/pipeline/quality_audit.py
 M quantstudio/pipeline/source_capabilities.py
 M quantstudio/pipeline/sources/mcp_adapter.py
 M quantstudio/pipeline/writers.py
 M skills/quantstudio-strategy-compiler/SKILL.md
 M tests/test_agent_first_strategy_skill.py
 M tests/test_agent_portfolio_contract.py
 M tests/test_qfq_reanchor_batch1.py
 M tests/test_target_aware_strategy_skill.py
?? .dsh-vision-toolkit/
?? .reasonix/
?? =
?? _blobs_tmp.txt
?? _blobs_tmp2.txt
?? _cand1.py
?? _cand2.py
?? _check_etf_basic.py
?? _commit_msg1.txt
?? _commit_msg2.txt
?? _daemon3.err
?? _mcp_with_3a.bak
?? _orc_3a.bak
?? _patch_evidence.py
?? _probe_split.py
?? _qs_cat_enum.txt
?? _qs_probe2.txt
?? _qs_r1_probe.py
?? _qs_static_pool_draft.md
?? _r1_probe_db.py
?? _r4_out.txt
?? _r5_out.txt
?? _r6_out.txt
?? _ras_3a.bak
?? _res_3a.bak
?? _scan_ok.bak
?? _sync_skill_check.py
?? agent_workspace/
?? config/profiles/mcp_only/qfq_rebase_admissible_securities.json
?? config/profiles/mcp_only/qfq_resume_1897.json
?? config/profiles/mcp_only/qfq_resume_1975.json
?? data/minute_fix_ratio_etf_minutes.csv
?? data/minute_fix_ratio_stock_minutes.csv
?? data/snapshots/
?? data/wp2_release/
?? docs/backtest-align-diagnosability-design.md
?? docs/cloud-source-quality-audit-plan-20260817.md
?? docs/cloud-source-repair-plan-20260817.md
?? docs/evidence/backtest-align-golden-20260817.md
?? docs/evidence/cloud-source-quality-audit-20260817.md
?? docs/evidence/disk_cleanup_manifest_20260819.md
?? docs/evidence/dual-end-alignment-20260818.md
?? docs/evidence/final-snapshot-20260822-briefing.md
?? docs/evidence/g2a-candidates-20260816.txt
?? docs/evidence/g2a-prefetch-failure-20260817.md
?? docs/evidence/g2a-tiering-20260817.txt
?? docs/evidence/g2a-tiering-final-20260817.md
?? docs/evidence/g2a-tiering-final-pass-20260817.txt
?? docs/evidence/mcp-minute-anchor-g1-20260816.md
?? docs/evidence/mcp-minute-anchor-g2a-preflight-20260816.md
?? docs/evidence/session_transcript_extract.md
?? docs/governance-3a-write-lock-design.md
?? docs/governance-qfq-invariant-param-spec.md
?? docs/governance-sharded-hash-spec.md
?? docs/governance-snapshot-design.md
?? docs/governance-step1-callchain.md
?? docs/governance-step2-audit.md
?? docs/governance-step2-gates.md
?? docs/governance-step3-audit.md
?? docs/handoff/20260817_qfq_canary_pipeline_session.md
?? docs/handoff/_session_ws_baseline_20260817.txt
?? docs/handoff/backtest-align-20260817-handoff.md
?? docs/handoff/baseline_20260821_0040_dispatch.md
?? docs/handoff/draft_mcp_progress_addendum_20260821.md
?? docs/mcp-minute-caliber-audit-20260816.md
?? docs/mcp-minute-front-anchor-design.md
?? docs/mcp_migration/engine-v2final-execution-prompt.md
?? docs/mcp_migration/etf-split-factor-derived-fix-plan.md
?? docs/mcp_migration/etf-split-factor-derived-fix-plan.review.md
?? docs/mcp_migration/pipeline-etf-dividend-integration-plan.md
?? docs/mcp_migration/questdb-etf-dividend-supplement-task.md
?? docs/project-stabilization-plan.md
?? docs/ptrade-conversion-tab-spec.md
?? probe_sig_tmp.py
?? ptrade/fq_compare_probe_ptrade.py
?? ptrade/match_price_probe_minute_ptrade.py
?? ptrade/match_price_probe_ptrade.py
?? ptrade/probe_commission_ptrade.py
?? ptrade/probe_order_limit_ptrade.py
?? "ptrade/ptrade\346\265\213\350\257\225\346\227\245\345\277\227.md"
?? ptrade/smallcap_diff_probe_ptrade.py
?? ptrade/smallcap_diff_probe_v2_ptrade.py
?? "ptrade/\346\222\256\345\220\210\346\234\272\345\210\266\345\256\236\350\257\201\344\270\216\344\277\256\345\244\215\346\226\271\346\241\210.md"
?? "ptrade/\346\222\256\345\220\210\346\234\272\345\210\266\345\256\236\350\257\201\344\270\216\344\277\256\345\244\215\346\226\271\346\241\210_v2.md"
?? quantstudio/pipeline/snapshot_lock.py
?? quantstudio/pipeline/sources/consume_whitelist_guard.py
?? quantstudio/test_n8n.py
?? reasonix.toml
?? scripts/_c4resume_stop_watcher.py
?? scripts/_check_qfq_tables_tmp.py
?? scripts/_cleanup_tmp.py
?? scripts/_dbg_600519_0618.py
?? scripts/_dbg_601628.py
?? scripts/_dbg_aux_factor.py
?? scripts/_dbg_cache.py
?? scripts/_dbg_cache601628.py
?? scripts/_dbg_cache_vs_db.py
?? scripts/_dbg_canary215.py
?? scripts/_dbg_canary_state.py
?? scripts/_dbg_compare_601628.py
?? scripts/_dbg_deadletter.py
?? scripts/_dbg_dl118.py
?? scripts/_dbg_etf510500.py
?? scripts/_dbg_export.py
?? scripts/_dbg_export2.py
?? scripts/_dbg_factor.py
?? scripts/_dbg_fresh_601628.py
?? scripts/_dbg_fresh_full.py
?? scripts/_dbg_front_cols.py
?? scripts/_dbg_lastbar.py
?? scripts/_dbg_overwrite_link.py
?? scripts/_dbg_overwrite_small.py
?? scripts/_dbg_pattern.py
?? scripts/_dbg_pattern2.py
?? scripts/_dbg_qfq_state.py
?? scripts/_dbg_qfq_state2.py
?? scripts/_dbg_retry5.py
?? scripts/_dbg_scope.py
?? scripts/_dbg_time_format.py
?? scripts/_dbg_weird.py
?? scripts/_float_share_backfill.py
?? scripts/_float_share_backfill_2018.py
?? scripts/_fullrun_watcher.py
?? scripts/_git_add2.txt
?? scripts/_git_commit.txt
?? scripts/_git_push.txt
?? scripts/_git_status.txt
?? scripts/_p0_out.txt
?? scripts/_phase4_golden_verify.py
?? scripts/_probe2_tmp.py
?? scripts/_probe3_tmp.py
?? scripts/_probe_duckdb_tmp.py
?? scripts/_probe_qdb2_tmp.py
?? scripts/_probe_qdb3_tmp.py
?? scripts/_probe_qdb_tmp.py
?? scripts/_qdb2_out.txt
?? scripts/_qdb3_out.txt
?? scripts/_qdb_out.txt
?? scripts/_qdb_tables.txt
?? scripts/_rewrite_runstate_tmp.py
?? scripts/_scan_front_corruption.py
?? scripts/_smallcap_diff_probe.py
?? scripts/_tdd2_migrate_gen1.py
?? scripts/_tdd2_probe.py
?? scripts/_verify_canary_front.py
?? scripts/_verify_factor_lookup_equiv.py
?? scripts/_verify_front_fix.py
?? scripts/_verify_minutes_fix.py
?? scripts/_verify_qfq_caliber.py
?? scripts/audit_etf_corporate_actions.py
?? scripts/batch1_reanchor_stale_daily_front.py
?? scripts/batch2_sync_recovery.py
?? scripts/c4_merge_staging_to_main.py
?? scripts/etf_minute_reanchor.py
?? scripts/final_snapshot_orchestrator.py
?? scripts/fix_minutes_pollution.py
?? scripts/frontfix.py
?? scripts/frontfix_backup.py
?? scripts/frontfix_check_etfm.py
?? scripts/frontfix_check_pollution.py
?? scripts/frontfix_csvrow.py
?? scripts/frontfix_diag.py
?? scripts/frontfix_final_verify.py
?? scripts/frontfix_idxprobe.py
?? scripts/frontfix_missdiag.py
?? scripts/frontfix_missdiag2.py
?? scripts/frontfix_monitor.py
?? scripts/frontfix_preflight.py
?? scripts/frontfix_proc.py
?? scripts/frontfix_progress.py
?? scripts/frontfix_reverify.py
?? scripts/frontfix_timeprobe.py
?? scripts/frontfix_verify_etf.py
?? scripts/governance_d2_gate.py
?? scripts/governance_snapshot.py
?? scripts/governance_snapshot.py.bak_h3_20260819
?? scripts/governance_write_conn_scan.py
?? scripts/overwrite_minutes_from_cloud.py
?? scripts/purge_stock_minutes_cache.py
?? scripts/reopen_deadletter.py
?? scripts/restore_minutes_frontback.py
?? scripts/restore_minutes_raw.py
?? scripts/verify_pipeline_after_pull.py
?? tests/test_3a_equivalence.py
?? tests/test_consume_whitelist_guard.py
?? tests/test_fill_audit.py
?? tests/test_governance_d2_gate_boundary.py
?? tests/test_governance_snapshot.py
?? tests/test_governance_snapshot_audit_fixes.py
?? tests/test_governance_snapshot_protect_transaction.py
?? tests/test_governance_snapshot_terminal_fixes.py
?? tests/test_governance_snapshot_unprotect_journal.py
?? tests/test_guard_extension_anchor.py
?? tests/test_quality_audit_anchor.py
?? tests/test_sharded_hash_v4.py
?? tests/test_snapshot_lock.py
?? "\351\230\273\346\226\255 adj_factor \351\231\215\347\272\247\345\206\231\345\205\245 1.0 \342\200\224 \345\256\236\346\226\275\346\226\271\346\241\210.md"
`

## git diff --stat
``ngit : warning: in the working copy of 'docs/prompt_engineering.md', LF will be replaced by CRLF the next time Git touch
es it
At line:1 char:292
+ ... s --porcelain 2>&1 | Out-String; $diff = git diff --stat 2>&1 | Out-S ...
+                                              ~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : NotSpecified: (warning: in the... Git touches it:String) [], RemoteException
    + FullyQualifiedErrorId : NativeCommandError
 
warning: in the working copy of 'docs/strategy_toolbox.md', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'quantstudio/pipeline/quality_audit.py', LF will be replaced by CRLF the next time Git 
touches it
 AGENTS.md                                          |   75 +-
 README.md                                          | 1209 ++++++++++----------
 config/alignment_rules.json                        |  137 ++-
 config/profiles/mcp_only/alignment_rules.json      |  152 ++-
 config/profiles/mcp_only/collector_tasks.json      |   59 +-
 docs/mcp_migration/full_table_inventory.json       |   44 +-
 docs/prompt_engineering.md                         |    6 +-
 docs/strategy-compiler/implementation-status.md    |    9 +
 docs/strategy-compiler/ptrade-profile-contract.md  |    7 +-
 docs/strategy_toolbox.md                           |   20 +-
 quantstudio/backtest/backtest_engine.py            |  162 ++-
 quantstudio/backtest/events.py                     |   21 +
 ..._board_pullback_daily__candidate_quantstudio.py | 1054 -----------------
 ...ustry_etf_rotation_8f__candidate_quantstudio.py |  519 ---------
 quantstudio/gui/tabs/config_editor_tab.py          |    5 +-
 quantstudio/gui/tabs/task_tab.py                   |   12 +
 quantstudio/pipeline/config_lint.py                |    1 +
 quantstudio/pipeline/daemon.py                     |   10 +-
 quantstudio/pipeline/qfq_aux_router.py             |   26 +-
 quantstudio/pipeline/qfq_calendar.py               |   14 +-
 quantstudio/pipeline/qfq_event_discovery.py        |    6 +-
 quantstudio/pipeline/qfq_formal_canary.py          |   10 +-
 quantstudio/pipeline/qfq_formal_cutover.py         |   28 +-
 quantstudio/pipeline/qfq_formal_cutover_cli.py     |   14 +
 .../pipeline/qfq_formal_postcutever_audit.py       |    6 +-
 quantstudio/pipeline/qfq_invariant.py              |   26 +-
 quantstudio/pipeline/qfq_maintenance.py            |   14 +-
 quantstudio/pipeline/qfq_observation.py            |   22 +-
 quantstudio/pipeline/qfq_orchestrator_cli.py       |   15 +
 quantstudio/pipeline/qfq_reanchor_schema.py        |    6 +-
 quantstudio/pipeline/qfq_resident_orchestrator.py  |   14 +-
 quantstudio/pipeline/qfq_revision.py               |   14 +-
 quantstudio/pipeline/qfq_schema_migration.py       |   14 +
 quantstudio/pipeline/quality_audit.py              |  242 +++-
 quantstudio/pipeline/source_capabilities.py        |    1 +
 quantstudio/pipeline/sources/mcp_adapter.py        |   82 +-
 quantstudio/pipeline/writers.py                    |   39 +
 skills/quantstudio-strategy-compiler/SKILL.md      |    1 +
 tests/test_agent_first_strategy_skill.py           |    8 +-
 tests/test_agent_portfolio_contract.py             |    2 +-
 tests/test_qfq_reanchor_batch1.py                  |   21 +-
 tests/test_target_aware_strategy_skill.py          |    2 +-
 42 files changed, 1797 insertions(+), 2332 deletions(-)
`


## 推送前回退点
- git stash create hash: 0a176352f02f96ed4aeeef88bb0360fdc96c71a9（零副作用，未动工作区）
