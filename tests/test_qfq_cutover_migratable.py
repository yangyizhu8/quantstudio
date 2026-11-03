# -*- coding: utf-8 -*-
"""D5-c 验收：activation 侧拒激活（异平台绝对路径 / 本机不存在）+ 引导 S2。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from quantstudio.pipeline import qfq_cutover_activation as A
from quantstudio.pipeline.qfq_cutover import CutoverError


def _foreign_abs() -> str:
    """取一个对当前宿主真正异平台的绝对路径。"""
    return ("/Users/apple/data/qfq_aux_mcp_gen1.db" if os.name == "nt"
            else r"D:\miniQMT策略实盘\QuantStudio\data\qfq_aux_mcp_gen1.db")


def test_foreign_absolute_is_rejected_with_guidance():
    with pytest.raises(CutoverError) as ei:
        A._assert_migratable_aux(_foreign_abs(), cutover_id="c1")
    msg = str(ei.value)
    assert "异平台" in msg
    assert "cutover-diag" in msg and "cutover-rehost" in msg, "必须给出可执行指引"


def test_missing_file_is_rejected_with_guidance():
    missing = str(Path(os.environ.get("TEMP", ".")) / "definitely_missing_qfq_aux.db")
    with pytest.raises(CutoverError) as ei:
        A._assert_migratable_aux(missing, cutover_id="c1")
    msg = str(ei.value)
    assert "不存在" in msg
    assert "cutover-diag" in msg or "cutover-rehost" in msg


def test_empty_path_is_rejected():
    with pytest.raises(CutoverError) as ei:
        A._assert_migratable_aux("", cutover_id="c1")
    assert "为空" in str(ei.value)


def test_valid_local_path_passes(tmp_path):
    aux = tmp_path / "qfq_aux.db"
    aux.write_bytes(b"x")
    A._assert_migratable_aux(str(aux), cutover_id="c1")   # 不抛即通过
