$ErrorActionPreference = "Stop"
$files = @(
  "AGENTS.md",
  "quantstudio/backtest/strategies/沪深300均值回归超跌反弹.py",
  "quantstudio/strategy_compiler/design_metadata.py",
  "quantstudio/backtest/ptrade_api.py",
  "tests/test_design_metadata.py",
  "tests/test_fill_audit_noop_counter.py",
  "skills/quantstudio-strategy-compiler/SKILL.md",
  "skills/quantstudio-strategy-compiler/references/no-lookahead-rules.md",
  "docs/design-metadata-open-match-price-design.md",
  "docs/qs-fill-audit-delta-below-one-lot-design.md",
  "docs/evidence/f3a-design-metadata-open-acceptance-20260923.md",
  "docs/evidence/f2a-fill-audit-noop-counter-acceptance-20260923.md"
)
git add -- $files
git add -- "agent_workspace/ou_reversal_csi300_10"
git add -- "agent_workspace/ou_reversal"
Write-Output "---- staged files ----"
git diff --staged --name-only | Measure-Object -Line
Write-Output "---- forbidden check (should be empty) ----"
git diff --staged --name-only | Select-String -Pattern "backtest_readonly|__pycache__|\.log$|fall_reversal"
Write-Output "---- staged stat (tail) ----"
git diff --staged --stat | Select-Object -Last 30
