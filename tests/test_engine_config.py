"""A1 同步测试：EngineConfig 外部注入 + 19 处硬编码清除验证。

验证目标（对应方案 v2.1 Phase A1）：
1. EngineConfig 可由外部显式构造注入，不做路径推导
2. EngineConfig.default() 兜底工厂正常工作
3. BacktestEngine 优先级：config > db_path > default
4. ptrade_api.py 的 PtradeAPI 通过 _cfg 消费路径（不再有硬编码）
5. 源码可移植：grep 不到 D:/miniQMT 绝对路径
"""
from pathlib import Path

import pytest

# 项目根（测试入口用，这是允许推导的兜底位置）
ROOT = Path(__file__).resolve().parent.parent
from quantstudio._paths import db_path


# ========== EngineConfig 数据类 ==========

def test_engine_config_is_dataclass_with_three_fields():
    """EngineConfig 是 dataclass，含 db_path / output_dir / research_dir 三个字段"""
    from quantstudio.backtest.backtest_engine import EngineConfig
    cfg = EngineConfig(
        db_path=Path("/tmp/test.db"),
        output_dir=Path("/tmp/out"),
        research_dir=Path("/tmp/research"),
    )
    assert cfg.db_path == Path("/tmp/test.db")
    assert cfg.output_dir == Path("/tmp/out")
    assert cfg.research_dir == Path("/tmp/research")


def test_engine_config_default_uses_project_root():
    """default() 兜底工厂指向项目根的 data/output 目录"""
    from quantstudio.backtest.backtest_engine import EngineConfig
    cfg = EngineConfig.default()
    assert cfg.db_path == db_path()
    assert cfg.output_dir == ROOT / "output"
    assert cfg.research_dir == ROOT / "output" / "research"


def test_engine_config_default_returns_consistent_instance():
    """default() 每次返回等价配置（值相同，实例独立）"""
    from quantstudio.backtest.backtest_engine import EngineConfig
    a = EngineConfig.default()
    b = EngineConfig.default()
    assert a.db_path == b.db_path
    assert a is not b  # 独立实例


# ========== BacktestEngine 优先级 ==========

def test_backtest_engine_uses_explicit_config():
    """config 参数优先：传入 config 时，engine.config 用它"""
    from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
    custom = EngineConfig(
        db_path=Path("/custom/path.db"),
        output_dir=Path("/custom/out"),
        research_dir=Path("/custom/research"),
    )
    engine = BacktestEngine(
        db_path="ignored.db",  # config 优先级更高，应被忽略
        strategy={},
        start="2026-01-01", end="2026-01-02",
        config=custom,
    )
    assert engine.config is custom
    assert engine.config.db_path == Path("/custom/path.db")


def test_backtest_engine_falls_back_to_db_path():
    """仅传 db_path 时：db_path 用传入值，output/research 用 default 兜底"""
    from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
    engine = BacktestEngine(
        db_path="/some/other.db",
        strategy={},
        start="2026-01-01", end="2026-01-02",
    )
    assert engine.config.db_path == Path("/some/other.db")
    # output/research 走 default
    default_cfg = EngineConfig.default()
    assert engine.config.output_dir == default_cfg.output_dir
    assert engine.config.research_dir == default_cfg.research_dir
    # db_path 属性保留为字符串（向后兼容）；跨平台用 Path 比较
    assert Path(engine.db_path) == Path("/some/other.db")


# ========== 硬编码清除 ==========

def test_no_hardcoded_absolute_paths_in_ptrade_api():
    """A1 核心验收：ptrade_api.py 不含 D:/miniQMT 硬编码"""
    ptrade_api_file = ROOT / "quantstudio" / "backtest" / "ptrade_api.py"
    content = ptrade_api_file.read_text(encoding="utf-8")
    assert "D:/miniQMT策略实盘" not in content, \
        "ptrade_api.py 仍含硬编码绝对路径，A1 未完成"


def test_no_hardcoded_absolute_paths_in_backtest_engine():
    """A1：backtest_engine.py 不含 D:/miniQMT 硬编码（default 兜底除外）"""
    engine_file = ROOT / "quantstudio" / "backtest" / "backtest_engine.py"
    content = engine_file.read_text(encoding="utf-8")
    assert "D:/miniQMT策略实盘" not in content, \
        "backtest_engine.py 仍含硬编码绝对路径"


# ========== PtradeAPI 通过 _cfg 消费路径 ==========

def test_ptrade_api_receives_config_on_attach():
    """attach() 后，PtradeAPI._cfg 指向 engine.config"""
    from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
    from quantstudio.backtest.ptrade_api import _api

    custom = EngineConfig(
        db_path=Path("/attach/test.db"),
        output_dir=Path("/attach/out"),
        research_dir=Path("/attach/research"),
    )
    engine = BacktestEngine(
        db_path="ignored.db",
        strategy={},
        start="2026-01-01", end="2026-01-02",
        config=custom,
    )
    # attach 需要引擎数据，用 None 占位（测试只验证 _cfg 注入）
    _api.attach(engine, None, None, "2026-01-01", "2025-12-31", {})
    assert _api._cfg is custom
    assert _api._cfg.db_path == Path("/attach/test.db")


def test_get_research_path_uses_config():
    """get_research_path() 返回 config.research_dir 而非硬编码字符串"""
    from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
    from quantstudio.backtest.ptrade_api import _api

    custom = EngineConfig(
        db_path=Path("/tmp/x.db"),
        output_dir=Path("/tmp/out"),
        research_dir=Path("/my/custom/research"),
    )
    engine = BacktestEngine(
        db_path="ignored.db", strategy={}, start="2026-01-01", end="2026-01-02",
        config=custom,
    )
    _api.attach(engine, None, None, "2026-01-01", "2025-12-31", {})
    # 跨平台：Windows 下 Path 转 \，用 Path 对象比较
    assert Path(_api.get_research_path()) == Path("/my/custom/research")
