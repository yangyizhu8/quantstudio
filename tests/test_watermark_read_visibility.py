"""W5（2026-10-04）：get_last_date 读失败可见化 —— 不再与「无水位行」同形。

施工依据：docs/wm-freeze-fix-design.md §W5（2026-10-04 ②审计 PASS）
验收判据 V1：构造读失败 ⇒ 断言 logger.warning 含『异常类型 + key』，且返回值仍为 None（语义不变）。

背景：原 except 静默 return None，使『读失败』与『本表无水位行』不可区分；
取证时实测 10-03 曾出现 last_watermark=None（float_share/etf_minutes），而同一代际内
1 秒前的 stock_namechange 读值正常——按表分叉未解，本仪器使下次复现自动留证。
"""
import logging

from quantstudio.pipeline.writers import DuckDBWriter


class _BoomConn:
    """execute 必抛的连接替身——模拟读失败（锁冲突/表缺失/连接失效）。"""

    def execute(self, *a, **kw):
        raise RuntimeError("simulated read failure (write-write conflict)")

    def close(self):
        pass


def test_get_last_date_logs_on_read_failure(tmp_path, caplog, monkeypatch):
    w = DuckDBWriter({"path": str(tmp_path / "t.db")})
    monkeypatch.setattr(w, "_conn", lambda: _BoomConn())
    with caplog.at_level(logging.WARNING):
        v = w.get_last_date("mcp", "etf_minutes", "1min")
    assert v is None, "返回语义必须保持 None（仍按无水位处理）"
    msgs = [r.getMessage() for r in caplog.records]
    hit = [m for m in msgs if "get_last_date" in m and "读失败" in m]
    assert hit, "读失败应打 warning，实际记录=%r" % (msgs,)
    m = hit[0]
    assert "RuntimeError" in m, "warning 须含异常类型，实际=%r" % m
    assert "mcp/etf_minutes/1min" in m, "warning 须含 key，实际=%r" % m


def test_get_last_date_still_returns_value_on_success(tmp_path):
    """对照组：正常路径语义不变（建表→写行→读到）。"""
    w = DuckDBWriter({"path": str(tmp_path / "t2.db")})
    # 用真实推进 API 造数据（source_watermark 由 writer 初始化，8 列显式 INSERT）
    w.advance_watermark("mcp", "etf_minutes", "1min", "1789023600000", "w5_control")
    assert w.get_last_date("mcp", "etf_minutes", "1min") == "1789023600000"
    assert w.get_last_date("mcp", "etf_minutes", "nonexistent_freq") is None
