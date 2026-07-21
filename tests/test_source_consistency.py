"""D10 数据源口径一致性校验测试（不依赖 Ptrade 真实样本）。

验证 EngineConfig.data_source 与 PtradeBaseline.source_id 的口径对齐断言：
1. 权威源默认 tushare
2. 同口径 → 一致放行
3. tushare↔juyuan 跨源 → 告警 + cross_source 标记（已知容差）
4. 口径未声明/未知 → 拒绝
5. 非白名单跨源 → 拒绝
6. strict=True 连白名单跨源也拒绝
"""
import pytest
import pandas as pd

from quantstudio.backtest.backtest_engine import EngineConfig
from quantstudio.backtest.ptrade_baseline import (
    PtradeBaseline,
    assert_source_consistency,
    SourceConsistencyError,
)
from quantstudio.backtest.fidelity_compare import FidelityComparator


# ========== EngineConfig 权威源 ==========

def test_engine_config_default_data_source_is_tushare():
    cfg = EngineConfig.default()
    assert cfg.data_source == "tushare"


def test_engine_config_explicit_data_source():
    cfg = EngineConfig(db_path="x.db", output_dir="o", research_dir="r", data_source="xtquant")
    assert cfg.data_source == "xtquant"


# ========== PtradeBaseline 口径 ==========

def test_ptrade_baseline_default_source_id_is_juyuan():
    bl = PtradeBaseline()
    assert bl.source_id == "juyuan"
    # check_source_consistency 是对 assert_source_consistency 的薄封装
    chk = bl.check_source_consistency("tushare")
    assert chk["cross_source"] is True
    assert chk["consistent"] is True


# ========== assert_source_consistency 规则 ==========

def test_same_source_consistent():
    chk = assert_source_consistency("tushare", "tushare")
    assert chk["consistent"] is True
    assert chk["cross_source"] is False


def test_tushare_juyuan_cross_source_allowed_and_flagged():
    chk = assert_source_consistency("tushare", "juyuan")
    assert chk["consistent"] is True
    assert chk["cross_source"] is True
    # 反向也允许
    chk2 = assert_source_consistency("juyuan", "tushare")
    assert chk2["cross_source"] is True


def test_unknown_engine_source_rejected():
    with pytest.raises(SourceConsistencyError):
        assert_source_consistency("", "juyuan")
    with pytest.raises(SourceConsistencyError):
        assert_source_consistency("unknown_src", "juyuan")


def test_unknown_baseline_source_rejected():
    with pytest.raises(SourceConsistencyError):
        assert_source_consistency("tushare", "")
    with pytest.raises(SourceConsistencyError):
        assert_source_consistency("tushare", "garbage")


def test_disallowed_cross_source_rejected():
    # tushare 引擎对 xtquant 基准：非白名单，拒绝
    with pytest.raises(SourceConsistencyError):
        assert_source_consistency("tushare", "xtquant")
    # xtquant 引擎对 juyuan 基准：非白名单，拒绝
    with pytest.raises(SourceConsistencyError):
        assert_source_consistency("xtquant", "juyuan")


def test_strict_rejects_whitelisted_cross_source():
    with pytest.raises(SourceConsistencyError):
        assert_source_consistency("tushare", "juyuan", strict=True)


# ========== FidelityComparator 集成 ==========

class _FakeBaseline:
    """无 source_id 的伪基准（兼容老测试构造），回退到引擎口径。"""
    def __init__(self, trades, nav, holdings=None):
        self.trades = trades
        self._nav = nav
        self.holdings = holdings

    @property
    def nav(self):
        return self._nav


def _make_nav(dates, values):
    import pandas as pd
    return pd.DataFrame({'date': pd.to_datetime(dates), 'nav': values}).set_index('date')


def test_comparator_records_same_source_check():
    """伪基准无 source_id → 回退引擎口径 → 一致、非跨源。"""
    nav = _make_nav(['2026-01-05', '2026-01-06'], [1.0, 1.01])
    trades = pd.DataFrame(columns=['date', 'code', 'action', 'volume', 'price', 'commission', 'tax'])
    bl = _FakeBaseline(pd.DataFrame(columns=['date', 'code', 'direction', 'volume', 'price', 'commission']), nav)
    report = FidelityComparator(nav.reset_index(), trades, bl, engine_data_source="tushare").compare()
    assert report.source_check["consistent"] is True
    assert report.source_check["cross_source"] is False


def test_comparator_records_cross_source_check():
    """真实 PtradeBaseline(juyuan) vs tushare 引擎 → 跨源标记。"""
    nav = _make_nav(['2026-01-05', '2026-01-06'], [1.0, 1.01])
    trades = pd.DataFrame(columns=['date', 'code', 'action', 'volume', 'price', 'commission', 'tax'])
    bl = PtradeBaseline()  # source_id 默认 juyuan
    bl.trades = pd.DataFrame(columns=['date', 'code', 'direction', 'volume', 'price', 'commission'])
    bl._nav = _make_nav(['2026-01-05', '2026-01-06'], [1.0, 1.01])  # 预置净值跳过反推
    report = FidelityComparator(nav.reset_index(), trades, bl, engine_data_source="tushare").compare()
    assert report.source_check["consistent"] is True
    assert report.source_check["cross_source"] is True
    assert report.to_dict()["source_check"] is not None


def test_comparator_strict_rejects_cross_source():
    """strict=True 时跨源对照被拒绝对照。"""
    nav = _make_nav(['2026-01-05', '2026-01-06'], [1.0, 1.01])
    trades = pd.DataFrame(columns=['date', 'code', 'action', 'volume', 'price', 'commission', 'tax'])
    bl = PtradeBaseline()
    bl.trades = pd.DataFrame(columns=['date', 'code', 'direction', 'volume', 'price', 'commission'])
    with pytest.raises(SourceConsistencyError):
        FidelityComparator(nav.reset_index(), trades, bl,
                           engine_data_source="tushare", strict=True).compare()
