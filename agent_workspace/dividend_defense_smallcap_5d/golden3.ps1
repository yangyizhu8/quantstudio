param([string]$ROOT, [string]$TMP)
$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING='utf-8'
Set-Location $ROOT
Write-Output ("CWD=" + (Get-Location).Path)
$STRAT = 'quantstudio/backtest/strategies/smallcap_overnight_scalp_7_quantstudio.py'
function Snapshot($dest) {
  $latest = Get-ChildItem (Join-Path $ROOT 'output\backtest_results') -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  New-Item -ItemType Directory -Force -Path $dest | Out-Null
  Remove-Item (Join-Path $dest '*') -Force -ErrorAction SilentlyContinue
  Copy-Item (Join-Path $latest.FullName '*') $dest -Force
  $rows = Import-Csv (Join-Path $dest 'daily_stats.csv')
  $withPos = ($rows | Where-Object { [int]$_.positions -gt 0 }).Count
  Write-Output ("SNAP " + $dest + "  rows=" + $rows.Count + " rows_with_positions=" + $withPos)
}
Write-Output '=== STEP 1: POST-FIX RUN ==='
python -m quantstudio.backtest.run_ptrade_strategy $STRAT 2026-07-01 2026-07-31 2>&1 | Select-Object -Last 2
Snapshot (Join-Path $TMP 'gold_post_smallcap')
Write-Output '=== STEP 2: REVERT 3 FILES ==='
git checkout HEAD -- quantstudio/backtest/ptrade_api.py quantstudio/backtest/providers/duckdb_provider.py quantstudio/backtest/providers/base.py
Write-Output ("   status=[" + (git status --porcelain -- quantstudio/backtest/ptrade_api.py quantstudio/backtest/providers/duckdb_provider.py quantstudio/backtest/providers/base.py) + "]")
Write-Output '=== STEP 3: PRE-FIX RUN ==='
python -m quantstudio.backtest.run_ptrade_strategy $STRAT 2026-07-01 2026-07-31 2>&1 | Select-Object -Last 2
Snapshot (Join-Path $TMP 'gold_pre_smallcap')
Write-Output '=== STEP 4: RESTORE ==='
Copy-Item (Join-Path $TMP 'mine\ptrade_api.py') (Join-Path $ROOT 'quantstudio\backtest\ptrade_api.py') -Force
Copy-Item (Join-Path $TMP 'mine\duckdb_provider.py') (Join-Path $ROOT 'quantstudio\backtest\providers\duckdb_provider.py') -Force
Copy-Item (Join-Path $TMP 'mine\base.py') (Join-Path $ROOT 'quantstudio\backtest\providers\base.py') -Force
Get-ChildItem (Join-Path $TMP 'mine') | Get-FileHash -Algorithm SHA256 | ForEach-Object { "   RESTORED " + $_.Hash.Substring(0,16) + "  " + (Split-Path $_.Path -Leaf) }
Write-Output '=== STEP 5: COMPARE ==='
foreach($f in @('config.csv','daily_stats.csv','trades.csv','round_trips.csv','ptrade_metrics.csv','ptrade_metrics.json','benchmark.csv')){
  $a = Join-Path (Join-Path $TMP 'gold_pre_smallcap') $f; $b = Join-Path (Join-Path $TMP 'gold_post_smallcap') $f
  if((Test-Path $a) -and (Test-Path $b)){
    $ha = (Get-FileHash $a -Algorithm SHA256).Hash; $hb = (Get-FileHash $b -Algorithm SHA256).Hash
    Write-Output ("   " + $f.PadRight(22) + " " + $ha.Substring(0,20) + " " + $hb.Substring(0,20) + " " + $(if($ha -eq $hb){'IDENTICAL'}else{'DIFF'}))
  } else { Write-Output ("   " + $f.PadRight(22) + " A=" + (Test-Path $a) + " B=" + (Test-Path $b)) }
}
Write-Output 'DONE'