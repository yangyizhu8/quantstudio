# -*- coding: utf-8 -*-
"""guard 扩展名锚定匹配修复测试（DSH U1-U5）。

U1 shell 内联裸提及 pattern 不命中（自指误报修复）
U2 python xxx.py 命中
U3 powershell -File xxx.ps1 命中
U4 不可读 python cmdline 命中（fail-closed）
U5 既有测试全过
"""
import io
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scripts.governance_snapshot as gs


def _fake_proc(pid, name, cmdline):
    p = MagicMock()
    p.info = {"pid": pid, "name": name, "cmdline": cmdline}
    return p


class TestU1ShellInlineNoMatch:
    """U1: shell 进程 cmdline 内联裸提及 pattern 字面量 → 不命中（自指修复）"""

    def test_powershell_inline_mention(self):
        # 监控命令 cmdline 含 pattern 裸文本（如检查进程是否在跑）
        procs = [_fake_proc(999, "powershell.exe",
                            ["powershell.exe", "-Command",
                             "Get-Process | Where-Object {$_.Name -match 'get_tushare_data'}"])]
        with patch("psutil.process_iter", return_value=procs):
            hits = gs._data_side_tasks_running()
        assert hits == [], f"shell 内联裸提及不应命中: {hits}"

    def test_powershell_monitor_with_pattern_text(self):
        # ZCode 监控包装（含 -NonInteractive 前缀）+ 内联文本
        procs = [_fake_proc(998, "powershell.exe",
                            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                             "-Command", "[Console]::OutputE...Get-CimInstance Win32_Process"])]
        with patch("psutil.process_iter", return_value=procs):
            hits = gs._data_side_tasks_running()
        assert hits == [], f"shell 监控命令不应命中: {hits}"

    def test_cmd_inline_mention(self):
        procs = [_fake_proc(997, "cmd.exe", ["cmd.exe", "/c", "echo run_sync_now"])]
        with patch("psutil.process_iter", return_value=procs):
            hits = gs._data_side_tasks_running()
        assert hits == [], f"cmd 内联裸提及不应命中: {hits}"


class TestU2PythonScriptMatch:
    """U2: python 进程 cmdline 含 pattern → 命中"""

    def test_python_script(self):
        procs = [_fake_proc(801, "python.exe",
                            ["python.exe", "get_tushare_data.py", "--mode", "daily"])]
        with patch("psutil.process_iter", return_value=procs):
            hits = gs._data_side_tasks_running()
        assert len(hits) == 1 and hits[0]["matched_pattern"] == "get_tushare_data"

    def test_python_no_extension(self):
        procs = [_fake_proc(802, "python.exe", ["python.exe", "-m", "qfq_maintenance"])]
        with patch("psutil.process_iter", return_value=procs):
            hits = gs._data_side_tasks_running()
        assert len(hits) == 1 and hits[0]["matched_pattern"] == "qfq_maintenance"


class TestU3ShellFileInvocationMatch:
    """U3: shell 进程 -File xxx.ps1 → 命中（实际调用数据侧脚本文件）"""

    def test_powershell_ps1(self):
        procs = [_fake_proc(803, "powershell.exe",
                            ["powershell.exe", "-File",
                             "run_daily_etl_with_health_check.ps1"])]
        with patch("psutil.process_iter", return_value=procs):
            hits = gs._data_side_tasks_running()
        assert len(hits) == 1 and hits[0]["matched_pattern"] == "run_daily_etl_with_health_check"

    def test_powershell_py_invoke(self):
        # shell 调 python 脚本文件 → cmdline 含 .py → 命中
        procs = [_fake_proc(804, "powershell.exe",
                            ["powershell.exe", "-Command",
                             "& python run_sync_now.py --full"])]
        with patch("psutil.process_iter", return_value=procs):
            hits = gs._data_side_tasks_running()
        assert len(hits) == 1 and hits[0]["matched_pattern"] == "run_sync_now"

    def test_pwsh_ps1(self):
        procs = [_fake_proc(805, "pwsh.exe",
                            ["pwsh.exe", "-File", "check_etl_integrity.ps1"])]
        with patch("psutil.process_iter", return_value=procs):
            hits = gs._data_side_tasks_running()
        assert len(hits) == 1


class TestU4UnreadablePython:
    """U4: python cmdline 不可读 → fail-closed 命中"""

    def test_unreadable_python(self):
        procs = [_fake_proc(806, "python.exe", [])]  # 空 cmdline = 不可读
        with patch("psutil.process_iter", return_value=procs):
            hits = gs._data_side_tasks_running()
        assert len(hits) == 1 and hits[0]["matched_pattern"] == "fail_closed"


class TestU5MatchedPatternField:
    """U5 附加：所有 hits 必含 matched_pattern 字段"""

    def test_matched_pattern_present(self):
        procs = [_fake_proc(807, "python.exe", ["python.exe", "get_tushare_data.py"])]
        with patch("psutil.process_iter", return_value=procs):
            hits = gs._data_side_tasks_running()
        assert all("matched_pattern" in h for h in hits)


class TestQDBReadOnlyWhitelist:
    """DSH 追认条件：QDB_READ_ONLY_PATTERNS 白名单行为验证"""

    def test_qdb_readonly_no_abort(self):
        """QDB 只读任务（如 check_etl_integrity.ps1）命中 → yield 检查不 abort"""
        from unittest.mock import patch
        hits = [{"pid": 1, "cmd": "check_etl_integrity.ps1", "matched_pattern": "check_etl_integrity"}]
        with patch.object(gs, "_data_side_tasks_running", return_value=hits):
            # _yield_check_data_side 不应抛 GuardAbort
            gs._yield_check_data_side()
            # 到这里没异常 = 通过

    def test_duckdb_writer_still_aborts(self):
        """DuckDB 写者（如 get_tushare_data.py）命中 → yield 检查仍 abort"""
        from unittest.mock import patch
        import pytest
        hits = [{"pid": 2, "cmd": "get_tushare_data.py", "matched_pattern": "get_tushare_data"}]
        with patch.object(gs, "_data_side_tasks_running", return_value=hits):
            with pytest.raises(gs.GuardAbort):
                gs._yield_check_data_side()
