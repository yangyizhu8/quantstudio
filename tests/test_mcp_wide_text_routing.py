# -*- coding: utf-8 -*-
"""D1 回归（批一，2026-09-17）：MCPAdapter 宽文本 passthrough 路由。

根因：fetch_table 的宽文本表路由判据读 self._config（export_wide_text 覆写），
而 MCPAdapter.__init__ 从未赋值 _config → _WIDE_TEXT_PASSTHROUGH 集合内 7 张表
100% 抛 AttributeError（客户B 日志 7/7 对应）。

本用例断言：
  1. 构造后 _config 存在且默认 export_wide_text=True；
  2. 7 张宽文本表逐一走 export Parquet 路径（不再 AttributeError）；
  3. export_wide_text=False 时回落 fetch_page 路径（配置覆写仍生效）；
  4. 非宽文本 passthrough 表路由不变（回归）。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.sources import mcp_adapter as ma

WIDE_TABLES = sorted(ma._WIDE_TEXT_PASSTHROUGH)
NON_WIDE_PASSTHROUGH = "sw_daily"


def _make_adapter(monkeypatch, cfg=None):
    a = ma.MCPAdapter(dict(cfg or {"name": "mcp"}))
    monkeypatch.setattr(a, "supports_task", lambda table, freq: (True, ""))
    calls = []
    monkeypatch.setattr(
        a, "_fetch_export_passthrough",
        lambda *args, **kw: (calls.append("export") or (pd.DataFrame(), {})))
    monkeypatch.setattr(
        a, "_fetch_passthrough",
        lambda *args, **kw: (calls.append("page") or (pd.DataFrame(), {})))
    return a, calls


def test_config_attribute_present_with_default(monkeypatch):
    """D1 直接回归：_config 必须存在，缺省 export_wide_text=True"""
    a, _ = _make_adapter(monkeypatch)
    assert hasattr(a, "_config"), "MCPAdapter._config 未定义（D1 回归）"
    assert a._config.get("export_wide_text", True) is True


@pytest.mark.parametrize("table", WIDE_TABLES)
def test_wide_text_table_routes_to_export(monkeypatch, table):
    """7 张宽文本表逐一走 export 路径（修复前此处抛 AttributeError）"""
    a, calls = _make_adapter(monkeypatch)
    raw, meta = a.fetch_table(table, "2026-01-01", "2026-01-02", freq="daily")
    assert calls == ["export"], f"{table} 未走 export 路径: {calls}"
    assert meta.get("passthrough") is True


def test_wide_text_config_override_falls_back_to_page(monkeypatch):
    """配置覆写 export_wide_text=False → 回落 fetch_page 路径"""
    a, calls = _make_adapter(monkeypatch, {"name": "mcp", "export_wide_text": False})
    a.fetch_table(WIDE_TABLES[0], "2026-01-01", "2026-01-02", freq="daily")
    assert calls == ["page"], f"覆写未生效: {calls}"


def test_non_wide_passthrough_still_uses_page(monkeypatch):
    """非宽文本 passthrough 表路由不变（回归）"""
    a, calls = _make_adapter(monkeypatch)
    a.fetch_table(NON_WIDE_PASSTHROUGH, "2026-01-01", "2026-01-02", freq="daily")
    assert calls == ["page"], f"非宽文本表路由变化: {calls}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
