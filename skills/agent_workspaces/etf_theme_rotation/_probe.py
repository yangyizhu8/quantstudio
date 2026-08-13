import subprocess, time
ps = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "Where-Object { $_.CommandLine -like '*r5_smoke*' } | "
    "Select-Object ProcessId,UserModeTime,KernelModeTime,WorkingSetSize | Format-List"
)
def snap():
    r = subprocess.run(['powershell','-NoProfile','-Command',ps],
                       capture_output=True,text=True,encoding='utf-8',errors='replace')
    return r.stdout
a = snap(); time.sleep(5); b = snap()
print("SNAP A:\n"+a)
print("SNAP B (5s later):\n"+b)
