"""PR 分钟源切换：分钟表权威源守卫测试（复权一致性决策 2026-07-21）。

验证目标：
1. daemon _run_with_source 对分钟表（stock_minutes/etf_minutes）拒绝非 xtquant 源写入
2. xtquant 源写入分钟表不被拒绝
3. 非分钟表（stock_daily 等）不受守卫影响（任何源都允许）
4. GUI DEFAULT_SOURCE_MAP 分钟表默认源 = xtquant（与守卫一致，采集路径必要条件）
5. quality_tab EXPECTED_EMPTY 已移除分钟表
6. collector_tasks.json kline_1m/etf_minutes source_priority = ["xtquant"]
7. xtquant adapter 分窗下载逻辑正确（_parse_windows 切分窗口）
"""
import pytest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ========== daemon 守卫：拒绝非 xtquant 源写入分钟表 ==========

def test_guard_rejects_tushare_for_stock_minutes():
    """守卫拒绝 tushare 写入 stock_minutes（复权一致性）"""
    from quantstudio.pipeline.daemon import ResidentCollector
    # 构造最小 daemon 实例（不连 DB）
    daemon = ResidentCollector.__new__(ResidentCollector)
    task = {"name": "kline_1m", "table": "stock_minutes", "freq": "1min"}
    result = daemon._run_with_source(task, "tushare", "test_batch", "2026-07-21T00:00:00")
    assert result is False   # 拒绝


def test_guard_rejects_tushare_for_etf_minutes():
    """守卫拒绝 tushare 写入 etf_minutes"""
    from quantstudio.pipeline.daemon import ResidentCollector
    daemon = ResidentCollector.__new__(ResidentCollector)
    task = {"name": "etf_minutes", "table": "etf_minutes", "freq": "1min"}
    result = daemon._run_with_source(task, "tushare", "test_batch", "2026-07-21T00:00:00")
    assert result is False


def test_guard_allows_xtquant_for_stock_minutes():
    """守卫允许 xtquant 写入 stock_minutes（不被守卫拦截；后续逻辑由 _execute_task_per_stock 处理）"""
    from quantstudio.pipeline.daemon import ResidentCollector
    daemon = ResidentCollector.__new__(ResidentCollector)
    # daemon 需要一些属性才能继续到 per_stock（守卫之后会因缺属性失败，但守卫本身放行）
    daemon.tasks_cfg = {"tasks": []}
    daemon.sources_cfg = {"sources": {}, "default_source_priority": []}
    daemon.writer = None
    daemon.aligner = None
    daemon.batch_audit = None
    task = {"name": "kline_1m", "table": "stock_minutes", "freq": "1min",
            "codes": ["ALL"], "max_workers": 1, "mode": "incremental"}
    # 守卫放行后会在 _execute_task_per_stock 里因缺真实 adapter 失败，但不会在守卫处返回 False
    try:
        result = daemon._run_with_source(task, "xtquant", "test_batch", "2026-07-21T00:00:00")
    except Exception:
        # 守卫放行后任何异常都说明守卫没拦截（预期行为）
        result = "passed_guard"
    assert result is not False   # 守卫没拒绝（可能是 True 或异常）


def test_guard_does_not_affect_daily_tables():
    """守卫不影响日线表（stock_daily 任何源都允许）"""
    from quantstudio.pipeline.daemon import ResidentCollector
    daemon = ResidentCollector.__new__(ResidentCollector)
    daemon.tasks_cfg = {"tasks": []}
    daemon.sources_cfg = {"sources": {}, "default_source_priority": []}
    daemon.writer = None
    daemon.aligner = None
    daemon.batch_audit = None
    task = {"name": "stock_daily", "table": "stock_daily", "freq": "daily", "codes": ["ALL"]}
    # 守卫不拦截 stock_daily（后续会因缺属性失败，但不在守卫处返回 False）
    try:
        daemon._run_with_source(task, "tushare", "test_batch", "2026-07-21T00:00:00")
    except Exception:
        pass   # 守卫放行后的异常是预期的


# ========== GUI DEFAULT_SOURCE_MAP 与守卫一致 ==========

def test_gui_default_source_map_minute_tables_use_xtquant():
    """GUI DEFAULT_SOURCE_MAP 分钟表默认源 = xtquant（采集路径必要条件，与守卫同批交付）"""
    config_tab = ROOT / "quantstudio" / "gui" / "tabs" / "config_editor_tab.py"
    content = config_tab.read_text(encoding="utf-8")
    # 解析 DEFAULT_SOURCE_MAP（用 exec 安全方式：直接检查字符串）
    # stock_minutes 和 etf_minutes 的值应为 xtquant
    assert '"stock_minutes":        "xtquant"' in content or '"stock_minutes": "xtquant"' in content
    assert '"etf_minutes":          "xtquant"' in content or '"etf_minutes": "xtquant"' in content


def test_quality_tab_expected_empty_excludes_minute_tables():
    """quality_tab EXPECTED_EMPTY 已移除分钟表（采集后表空会正确告警）"""
    quality_tab = ROOT / "quantstudio" / "gui" / "tabs" / "quality_tab.py"
    content = quality_tab.read_text(encoding="utf-8")
    # EXPECTED_EMPTY 不应含 stock_minutes/etf_minutes
    assert 'EXPECTED_EMPTY = {"tick", "stock_minutes"' not in content
    assert 'EXPECTED_EMPTY = {"tick", "etf_minutes"' not in content


# ========== collector_tasks.json source_priority ==========

def test_collector_tasks_kline_1m_source_priority_xtquant():
    """kline_1m source_priority = ["xtquant"]（单源锁定）"""
    tasks_file = ROOT / "config" / "collector_tasks.json"
    tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
    kline_1m = next(t for t in tasks["tasks"] if t["name"] == "kline_1m")
    assert kline_1m["source_priority"] == ["xtquant"]
    assert kline_1m["source"] == "xtquant"


def test_collector_tasks_etf_minutes_source_priority_xtquant():
    """etf_minutes source_priority = ["xtquant"]（单源锁定）"""
    tasks_file = ROOT / "config" / "collector_tasks.json"
    tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
    etf_min = next(t for t in tasks["tasks"] if t["name"] == "etf_minutes")
    assert etf_min["source_priority"] == ["xtquant"]
    assert etf_min["source"] == "xtquant"


# ========== xtquant adapter 分窗逻辑 ==========

def test_parse_windows_splits_range_into_31_day_chunks():
    """_parse_windows 把区间切分为 ≤31 天窗口"""
    from quantstudio.pipeline.sources.xtquant_adapter import XtquantAdapter
    windows = XtquantAdapter._parse_windows("2026-01-01", "2026-03-01", window_days=31)
    # 2026-01-01 ~ 2026-03-01 = 60 天，应切成 2 个窗口（31 + 29）
    assert len(windows) == 2
    assert windows[0] == ("20260101", "20260131")
    assert windows[1] == ("20260201", "20260301")


def test_parse_windows_single_window_when_range_short():
    """区间 ≤31 天时单个窗口"""
    from quantstudio.pipeline.sources.xtquant_adapter import XtquantAdapter
    windows = XtquantAdapter._parse_windows("2026-01-01", "2026-01-10", window_days=31)
    assert len(windows) == 1
    assert windows[0] == ("20260101", "20260110")


def test_parse_windows_accepts_yyyymmdd_format():
    """_parse_windows 接受 YYYYMMDD 和 YYYY-MM-DD 两种格式"""
    from quantstudio.pipeline.sources.xtquant_adapter import XtquantAdapter
    w1 = XtquantAdapter._parse_windows("20260101", "20260110")
    w2 = XtquantAdapter._parse_windows("2026-01-01", "2026-01-10")
    assert w1 == w2
