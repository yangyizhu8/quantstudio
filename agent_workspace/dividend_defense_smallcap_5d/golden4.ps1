param([string]$ROOT, [string]$TMP, [string]$STRAT, [string]$TAG, [string]$WIN)
$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING='utf-8'
Set-Location $ROOT
$pre = Join-Path $TMP ("gold_pre_" + $TAG)
$post = Join-Path $TMP ("gold_post_" + $TAG)
function Snapshot($dest) {
  $latest = Get-ChildItem (Join-Path $ROOT 'output\backtest_results') -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  New-Item -ItemType Directory -Force -Path $dest | Out-Null
  Remove-Item (Join-Path $dest '*') -Force -ErrorAction SilentlyContinue
  Copy-Item (Join-Path $latest.FullName '*') $dest -Force
  $rows = Import-Csv (Join-Path $dest 'daily_stats.csv')
  $withPos = ($rows | Where-Object { [int]$_.positions -gt 0 }).Count
  Write-Output ("SNAP " + $dest + "  rows=" + $rows.Count + " rows_with_positions=" + $withPos)
}
$w = $WIN.Split(' ')
Write-Output '=== POST-FIX RUN ==='
python -m quantstudio.backtest.run_ptrade_strategy $STRAT $w[0] $w[1] 2>&1 | Select-Object -Last 2
Snapshot $post
Write-Output '=== REVERT ==='
git checkout HEAD -- quantstudio/backtest/ptrade_api.py quantstudio/backtest/providers/duckdb_provider.py quantstudio/backtest/providers/base.py
Write-Output ("   status=[" + (git status --porcelain -- quantstudio/backtest/ptrade_api.py quantstudio/backtest/providers/duckdb_provider.py quantstudio/backtest/providers/base.py) + "]")
Write-Output '=== PRE-FIX RUN ==='
python -m quantstudio.backtest.run_ptrade_strategy $STRAT $w[0] $w[1] 2>&1 | Select-Object -Last 2
Snapshot $pre
Write-Output '=== RESTORE ==='
Copy-Item (Join-Path $TMP 'mine\ptrade_api.py') (Join-Path $ROOT 'quantstudio\backtest\ptrade_api.py') -Force
Copy-Item (Join-Path $TMP 'mine\duckdb_provider.py') (Join-Path $ROOT 'quantstudio\backtest\providers\duckdb_provider.py') -Force
Copy-Item (Join-Path $TMP 'mine\base.py') (Join-Path $ROOT 'quantstudio\backtest\providers\base.py') -Force
Get-ChildItem (Join-Path $TMP 'mine') | Get-FileHash -Algorithm SHA256 | ForEach-Object { "   RESTORED " + $_.Hash.Substring(0,16) + "  " + (Split-Path $_.Path -Leaf) }
Write-Output '=== COMPARE ==='
foreach($f in @('config.csv','daily_stats.csv','trades.csv','round_trips.csv','ptrade_metrics.csv','ptrade_metrics.json','benchmark.csv')){
  $a = Join-Path $pre $f; $b = Join-Path $post $f
  if((Test-Path $a) -and (Test-Path $b)){
    $ha = (Get-FileHash $a -Algorithm SHA256).Hash; $hb = (Get-FileHash $b -Algorithm SHA256).Hash
    Write-Output ("   " + $f.PadRight(22) + " " + $ha.Substring(0,20) + " " + $hb.Substring(0,20) + " " + $(if($ha -eq $hb){'IDENTICAL'}else{'DIFF'}))
  } else { Write-Output ("   " + $f.PadRight(22) + " A=" + (Test-Path $a) + " B=" + (Test-Path $b)) }
}
Write-Output 'DONE'
