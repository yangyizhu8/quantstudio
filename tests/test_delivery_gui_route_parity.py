# -*- coding: utf-8 -*-
"""GUI 对等入口（双端对等铁律）：任务页「QFQ 路由诊断」与 CLI `qs-deliver cutover-diag` 同内核。

判据：同输入 ⇒ 同产出（诊断报告文本一致）；GUI 入口不得绕过只读语义。
"""
from __future__ import annotations

from quantstudio.gui.tabs.task_tab import format_route_diag_text


REPORT = {
    "ok": True,
    "error": None,
    "writes": 0,
    "records": [{
        "cutover_id": "old_1",
        "price_source": "mcp",
        "source_generation": "mcp-gen1",
        "status": "active",
        "aux_db_path": r"D:\miniQMT策略实盘\QuantStudio\data\qfq_aux_mcp_gen1.db",
        "migratable": False,
        "reasons": ["记录值为异平台绝对路径（迁移前遗留）"],
        "suggested": {"aux_db_path": "/Users/apple/data/qfq_aux_mcp_gen1.db",
                      "exists": True, "source": "qfq_aux_paths.json"},
    }],
}


def test_diag_text_contains_key_facts():
    text = format_route_diag_text(REPORT)
    assert "old_1" in text
    assert "不可迁移" in text
    assert "异平台绝对路径" in text
    assert "建议值" in text
    assert "写入次数：0" in text, "只读语义必须在界面上显式"


def test_diag_text_reports_error_case():
    text = format_route_diag_text({"error": "主库不可读（可能被采集占用）", "records": []})
    assert "诊断未完成" in text and "主库不可读" in text


def test_diag_text_handles_bad_input():
    assert "无报告" in format_route_diag_text(None)


def test_cli_and_gui_share_same_core():
    """双端同源：CLI 与 GUI 都调用 pipeline.qfq_cutover_tools.diagnose。"""
    from quantstudio.delivery import cli
    import inspect

    src_cli = inspect.getsource(cli._cmd_cutover_diag)
    assert "qfq_cutover_tools" in src_cli and "diagnose" in src_cli
    import quantstudio.gui.tabs.task_tab as TT
    src_gui = inspect.getsource(TT.TaskTab._qfq_route_diag)
    assert "qfq_cutover_tools" in src_gui and "diagnose" in src_gui
