import subprocess, sys
script = r"D:\miniQMT策略实盘\QuantStudio\skills\agent_workspaces\etf_theme_rotation\r5_smoke_diag.py"
log = r"D:\miniQMT策略实盘\QuantStudio\skills\agent_workspaces\etf_theme_rotation\r5_diag.log"
p = subprocess.Popen([sys.executable, script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     text=True, encoding="utf-8", errors="replace", cwd=r"D:\miniQMT策略实盘\QuantStudio")
with open(log, "w", encoding="utf-8") as f:
    for line in p.stdout:
        f.write(line); f.flush()
    rc = p.wait()
    f.write("\n=== EXIT CODE: %d ===\n" % rc)
