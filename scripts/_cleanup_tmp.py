# -*- coding: utf-8 -*-
"""清理报告更新临时脚本。"""
import os

p = "scripts/_report_update_v6751.py"
if os.path.exists(p):
    os.remove(p)
    print("removed")
else:
    print("already gone")
