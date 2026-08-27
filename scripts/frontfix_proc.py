# -*- coding: utf-8 -*-
"""查修复进程状态（PID 44088）。"""
import io
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ps = ("Get-Process -Id 44088 -ErrorAction SilentlyContinue | Select-Object Id,CPU,StartTime | Format-List")
out = subprocess.run(
    ["powershell.exe", "-NoProfile", "-Command", ps],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
print(out.stdout)
if not out.stdout.strip():
    print("进程 44088 已退出（修复已结束或崩溃）")
