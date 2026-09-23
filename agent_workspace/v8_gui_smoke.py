# -*- coding: utf-8 -*-
"""V8 GUI 冒烟（稳健版）：用 venv_quant_studio 起 GUI，按 Win32 窗口标题判定是否起来。

不依赖 Get-CimInstance / MainWindowTitle（本会话二者不稳）；用 ctypes.EnumWindows 枚举
可见窗口标题，找含 "QuantStudio" 的窗口。跑完自动清理 GUI 进程。
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

import psutil

VENV = r"D:\miniQMT策略实盘\_runtime\venv_quant_studio\Scripts\python.exe"
ROOT = r"D:\miniQMT策略实盘\QuantStudio"

user32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def visible_titles():
    out = []

    def _cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value.strip():
                    out.append(buf.value.strip())
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return out


def kill_gui():
    n = 0
    for pr in psutil.process_iter(["pid", "cmdline"]):
        cl = " ".join(pr.info["cmdline"] or [])
        if "main_gui.py" in cl:
            try:
                pr.kill()
                n += 1
            except Exception:
                pass
    return n


print(f"[1] 预清理残留 GUI 进程: {kill_gui()} 个")
time.sleep(2)

print(f"[2] 启动 GUI（解释器={VENV}）")
proc = subprocess.Popen([VENV, "main_gui.py"], cwd=ROOT)

t0 = time.time()
hit = None
while time.time() - t0 < 90:
    for t in visible_titles():
        # 精确匹配 GUI 主窗口标题（main_window.py: setWindowTitle("QuantStudio 数据管线控制台")）
        # —— 避免匹配到浏览器/编辑器标题里含 "QuantStudio" 的窗口（首次实测曾误报 Edge）
        if "数据管线控制台" in t:
            hit = t
            break
    if hit:
        break
    if proc.poll() is not None:
        print(f"[!] GUI 进程已退出，exit={proc.returncode}")
        break
    time.sleep(0.25)

elapsed = time.time() - t0
if hit:
    print(f"[3] **PASS**：窗口标题 = '{hit}'，出现耗时 = {elapsed:.2f} s")
else:
    print(f"[3] FAIL：{elapsed:.1f}s 内未见 QuantStudio 窗口（proc alive={proc.poll() is None}）")

print(f"[4] 清理: {kill_gui()} 个")
sys.exit(0 if hit else 1)
