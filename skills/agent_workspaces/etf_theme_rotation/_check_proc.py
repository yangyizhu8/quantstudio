import subprocess
out = subprocess.run(
    ['powershell', '-NoProfile', '-Command',
     "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
     "Where-Object { $_.CommandLine -like '*r5_smoke*' } | "
     "Select-Object ProcessId,ParentProcessId,WorkingSetSize | Format-List"],
    capture_output=True, text=True, encoding='utf-8', errors='replace'
)
print("STDOUT:\n" + out.stdout)
print("STDERR:\n" + out.stderr)
