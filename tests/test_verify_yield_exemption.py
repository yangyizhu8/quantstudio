"""A-豁免验收用例（2026-08-25 总调度批准，verify-only yield 豁免）。

1. verify 豁免生效：置位 + ETL 写者命中 → 不 abort；
2. create 红线不变：默认（未置位）+ 同写者命中 → 仍 GuardAbort。
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import governance_snapshot as gs  # noqa: E402


_WRITER_HITS = [{"pid": 123, "cmd": "python ...get_tushare_data.py --periods 1d",
                 "matched_pattern": "get_tushare_data"}]


def test_verify_exempt_no_abort():
    """用例1：豁免置位（verify hash 段）+ 真实 ETL 写者命中 → 不抛。"""
    with mock.patch.object(gs, "_data_side_tasks_running", return_value=_WRITER_HITS), \
         mock.patch.object(gs, "_VERIFY_YIELD_EXEMPT", True):
        gs._yield_check_data_side()  # 不抛 GuardAbort


def test_create_redline_intact():
    """用例2：默认未置位（create 路径）+ 同写者命中 → 仍抛 GuardAbort（红线）。"""
    assert gs._VERIFY_YIELD_EXEMPT is False, "模块默认必须为 False（create 安全）"
    with mock.patch.object(gs, "_data_side_tasks_running", return_value=_WRITER_HITS):
        try:
            gs._yield_check_data_side()
            raise AssertionError("create 路径必须 GuardAbort")
        except gs.GuardAbort:
            pass
