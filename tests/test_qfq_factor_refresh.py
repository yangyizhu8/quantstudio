"""任务2.2：QFQFactorRefresher.refresh 单元测试（fake adapter，不发真实网络）。

覆盖（冲突点1 + C3 修复后）：
- 某资产类别全部逐码请求失败 → FactorRefreshError 冒泡 → degraded=True。
- 正常返回空数据（区间内无复权事件）→ degraded=False（不误报）。
- 部分码失败 → degraded=False，成功结果仍落库（本次选定契约）+ 失败码 WARNING。
- 空 codes → 返回 0 不抛（防 0==0 误抛回归）。
- 生产形态 RateLimiter（仅 acquire，无 __call__）不 TypeError。
- C3：裸码→ts_code 转换在 refresh 内完成；fake Tushare 严格拒绝裸码。
- C3：股票转换异常不破坏 ETF 隔离性（resolve_ts_codes 在各自 try 内）。

分工：daemon 接入层（水位推进/hold + detector_degraded）见 test_daemon_qfq_integration.py；
resolve_ts_codes 元数据/前缀契约见 test_qfq_ts_code_resolve.py。
"""

import logging
import re
import sqlite3

import pandas as pd
import pytest

from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema
from quantstudio.pipeline.qfq_factor_refresh import QFQFactorRefresher, RefreshResult
from quantstudio.pipeline.qfq_maintenance import FactorRefreshError, QFQMaintenance

_TS_CODE_RE = re.compile(r"\d{6}\.(SH|SZ|BJ)")


class _NullRateLimiter:
    """生产形态限流器：只有 .acquire()，**没有** __call__（匹配生产 RateLimiter）。"""
    def __init__(self):
        self.acquire_count = 0

    def acquire(self):
        self.acquire_count += 1


class _FakeTushareClient:
    """模拟 Tushare 客户端。

    C3：严格校验 ts_code 格式 ``\\d{6}\\.(SH|SZ|BJ)``，裸码/非法后缀一律 ValueError
    （模拟真实 Tushare 行为，防止测试再次掩盖裸码问题）。

    参数：
        fail_codes: set，这些 ts_code 调用时抛 RuntimeError（用带后缀格式）。
        empty: True 时正常返回空 DataFrame（模拟无复权数据）。
    """
    def __init__(self, fail_codes=None, empty=False):
        self._fail_codes = set(fail_codes or [])
        self._empty = empty

    def _dispatch(self, ts_code, api_name):
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", str(ts_code)):
            raise ValueError(
                f"fake Tushare 拒绝非 ts_code 格式的输入: {ts_code!r}")
        if ts_code in self._fail_codes:
            raise RuntimeError(f"simulated failure for {api_name} {ts_code}")
        if self._empty:
            return pd.DataFrame()
        return pd.DataFrame(
            [{"ts_code": ts_code, "trade_date": "20260105",
              "adj_factor": 1.05 if api_name == "adj_factor" else 1.02}])

    def adj_factor(self, ts_code, start_date, end_date):
        return self._dispatch(ts_code, "adj_factor")

    def fund_adj(self, ts_code, start_date, end_date):
        return self._dispatch(ts_code, "fund_adj")


class _FakeAdapter:
    def __init__(self, fail_codes=None, empty=False):
        self._client = _FakeTushareClient(fail_codes=fail_codes, empty=empty)
        self.rate_limiter = _NullRateLimiter()  # 生产形态：仅 acquire，无 __call__

    @property
    def acquire_count(self):
        return self.rate_limiter.acquire_count


@pytest.fixture
def aux_db(tmp_path):
    p = tmp_path / "qfq_aux.db"
    conn = sqlite3.connect(str(p), timeout=30)
    init_sqlite_schema(conn)
    conn.commit()
    conn.close()
    return str(p)


# ---------------------------------------------------------------------------
# 0. 正常成功写入 + 落库验证（正向覆盖；验证裸码输入经转换后正确落库裸码）
# ---------------------------------------------------------------------------
def test_refresh_writes_adj_factor_and_fund_adj(aux_db):
    """正常刷新：universe 传裸码，refresh 转换为 ts_code 后请求，落库仍为裸码。"""
    refresher = QFQFactorRefresher(aux_db=aux_db)
    res = refresher.refresh(
        _FakeAdapter(),
        stock_universe={"600000"},
        etf_universe={"510300"},
        overlap_days=1,
        lookback_days=365,
        rate_limiter=_NullRateLimiter(),
    )
    assert isinstance(res, RefreshResult)
    assert res.degraded is False
    assert res.error is None
    assert res.stock_refreshed > 0
    assert res.etf_refreshed > 0
    with sqlite3.connect(aux_db, timeout=30) as conn:
        adj_codes = {r[0] for r in conn.execute("SELECT code FROM adj_factor")}
        fund_codes = {r[0] for r in conn.execute("SELECT code FROM fund_adj")}
    # 落库为裸码（fetch_adj_factor 内部 normalize_code 转裸码）
    assert "600000" in adj_codes
    assert "510300" in fund_codes
    assert "600000" not in fund_codes
    assert "510300" not in adj_codes


def test_refresh_empty_universe_noop(aux_db):
    refresher = QFQFactorRefresher(aux_db=aux_db)
    res = refresher.refresh(
        _FakeAdapter(), stock_universe=set(), etf_universe=set(),
        overlap_days=1, lookback_days=365, rate_limiter=_NullRateLimiter())
    assert res.stock_refreshed == 0
    assert res.etf_refreshed == 0
    assert res.degraded is False


# ---------------------------------------------------------------------------
# 1. 全失败 → degraded（冲突点1 核心修复）；fail_codes 用转换后的带后缀 ts_code
# ---------------------------------------------------------------------------
def test_refresh_stock_adapter_all_fail_degraded(aux_db):
    """股票全部逐码失败 → degraded=True；ETF 仍成功（跨资产类别隔离）。

    fail_codes 用带后缀 ts_code（refresh 把裸码 600000 转为 600000.SH 后再请求）。
    """
    refresher = QFQFactorRefresher(aux_db=aux_db)
    res = refresher.refresh(
        _FakeAdapter(fail_codes={"600000.SH"}),
        stock_universe={"600000"},
        etf_universe={"510300"},
        overlap_days=1, lookback_days=365, rate_limiter=_NullRateLimiter())
    assert res.degraded is True
    assert res.stock_failed == 1
    assert res.stock_refreshed == 0
    assert res.etf_refreshed > 0
    assert res.etf_failed == 0
    assert "adj_factor" in res.error
    assert "1/1" in res.error


def test_refresh_etf_adapter_all_fail_degraded(aux_db):
    refresher = QFQFactorRefresher(aux_db=aux_db)
    res = refresher.refresh(
        _FakeAdapter(fail_codes={"510300.SH"}),
        stock_universe={"600000"}, etf_universe={"510300"},
        overlap_days=1, lookback_days=365, rate_limiter=_NullRateLimiter())
    assert res.degraded is True
    assert res.etf_failed == 1
    assert res.etf_refreshed == 0
    assert res.stock_refreshed > 0
    assert "fund_adj" in res.error


# ---------------------------------------------------------------------------
# 2. 正常空数据 → 不降级（防误报）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("asset", ["stock", "etf"])
def test_refresh_normal_zero_rows_not_degraded(aux_db, asset):
    refresher = QFQFactorRefresher(aux_db=aux_db)
    stock_u = {"600000"} if asset == "stock" else set()
    etf_u = {"510300"} if asset == "etf" else set()
    res = refresher.refresh(
        _FakeAdapter(empty=True), stock_universe=stock_u, etf_universe=etf_u,
        overlap_days=1, lookback_days=365, rate_limiter=_NullRateLimiter())
    assert res.degraded is False
    assert res.error is None
    assert res.stock_refreshed == 0
    assert res.etf_refreshed == 0
    assert res.stock_failed == 0
    assert res.etf_failed == 0


# ---------------------------------------------------------------------------
# 3. fetch_adj_factor 直接测试：全失败抛异常（带后缀 ts_code 输入）
# ---------------------------------------------------------------------------
def test_fetch_adj_factor_all_fail_raises(aux_db):
    m = QFQMaintenance(db_path=aux_db)
    adapter = _FakeAdapter(fail_codes={"600000.SH", "600001.SH"})
    with pytest.raises(FactorRefreshError) as ei:
        m.fetch_adj_factor(adapter, ["600000.SH", "600001.SH"], "20260101", is_etf=False)
    msg = str(ei.value)
    assert "adj_factor" in msg and "2/2" in msg


def test_fetch_adj_factor_partial_fail_returns_success_rows(aux_db):
    m = QFQMaintenance(db_path=aux_db)
    adapter = _FakeAdapter(fail_codes={"600001.SH"})
    n = m.fetch_adj_factor(adapter, ["600000.SH", "600001.SH"], "20260101", is_etf=False)
    assert n == 1
    with sqlite3.connect(aux_db, timeout=30) as conn:
        codes = {r[0] for r in conn.execute("SELECT DISTINCT code FROM adj_factor")}
    assert "600000" in codes and "600001" not in codes


# ---------------------------------------------------------------------------
# 4. refresh 层部分失败 → 不降级 + WARNING 断言（锁定契约 + 可观测性）
# ---------------------------------------------------------------------------
def test_refresh_partial_fail_not_degraded(aux_db, caplog):
    """部分码失败 → degraded=False，成功结果落库，且失败码产生 WARNING。"""
    refresher = QFQFactorRefresher(aux_db=aux_db)
    with caplog.at_level(logging.WARNING, logger="quantstudio.pipeline.qfq_maintenance"):
        res = refresher.refresh(
            _FakeAdapter(fail_codes={"600001.SH"}),
            stock_universe={"600000", "600001"}, etf_universe={"510300"},
            overlap_days=1, lookback_days=365, rate_limiter=_NullRateLimiter())
    assert res.degraded is False
    assert res.error is None
    assert res.stock_refreshed == 1
    assert res.etf_refreshed > 0
    # 失败码有 WARNING，含转换后的 ts_code 与 failed 语义
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    warn_text = " ".join(r.getMessage() for r in warnings)
    assert "600001.SH" in warn_text
    assert "failed" in warn_text
    with sqlite3.connect(aux_db, timeout=30) as conn:
        stock_codes = {r[0] for r in conn.execute("SELECT DISTINCT code FROM adj_factor")}
    assert "600000" in stock_codes and "600001" not in stock_codes


# ---------------------------------------------------------------------------
# 5. 空 codes → 返回 0 不抛
# ---------------------------------------------------------------------------
def test_fetch_adj_factor_empty_codes_returns_zero(aux_db):
    m = QFQMaintenance(db_path=aux_db)
    n = m.fetch_adj_factor(_FakeAdapter(), [], "20260101", is_etf=False)
    assert n == 0
    with sqlite3.connect(aux_db, timeout=30) as conn:
        assert conn.execute("SELECT COUNT(*) FROM adj_factor").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 6. 生产形态 RateLimiter → 不 TypeError，每码仅一次 acquire
# ---------------------------------------------------------------------------
def test_refresh_accepts_production_shaped_rate_limiter(aux_db):
    adapter = _FakeAdapter()
    refresher = QFQFactorRefresher(aux_db=aux_db)
    res = refresher.refresh(
        adapter, stock_universe={"600000", "600001"}, etf_universe={"510300"},
        overlap_days=1, lookback_days=365, rate_limiter=_NullRateLimiter())
    assert res.degraded is False
    # 股票 2 码 + ETF 1 码 = 3 次请求 → 3 次 acquire（转换不增加请求）
    assert adapter.acquire_count == 3


def test_refresh_rate_limiter_acquire_count_with_failures(aux_db):
    adapter = _FakeAdapter(fail_codes={"600000.SH"})
    refresher = QFQFactorRefresher(aux_db=aux_db)
    res = refresher.refresh(
        adapter, stock_universe={"600000"}, etf_universe=set(),
        overlap_days=1, lookback_days=365, rate_limiter=_NullRateLimiter())
    assert res.degraded is True
    assert adapter.acquire_count == 1


# ---------------------------------------------------------------------------
# 7. C3：转换异常不破坏跨资产类别隔离（resolve_ts_codes 在各自 try 内）
# ---------------------------------------------------------------------------
class _ConvertFailAdapter(_FakeAdapter):
    """股票 resolve_ts_codes 正常，但通过 monkeypatch 让 resolve_ts_codes 对股票抛错。

    直接验证：refresh 内 resolve_ts_codes 异常被各资产 try 捕获 → 仅该资产 degraded。
    """
    pass


def test_refresh_stock_convert_failure_not_break_etf(aux_db, monkeypatch):
    """股票 resolve_ts_codes 抛异常 → 股票 degraded，ETF 仍成功刷新。"""
    from quantstudio.pipeline.aligner import raw_to_tushare_ts_code
    from quantstudio.pipeline import qfq_maintenance as mmaint

    # ETF 分支直接用纯前缀函数（避免再次 import 已被 patch 的 resolve_ts_codes）
    def _boom(codes, asset_type, main_db):
        if asset_type == "STOCK":
            raise RuntimeError("simulated convert failure for STOCK")
        return [raw_to_tushare_ts_code(c, "ETF") for c in codes]

    monkeypatch.setattr(mmaint, "resolve_ts_codes", _boom)

    refresher = QFQFactorRefresher(aux_db=aux_db)
    res = refresher.refresh(
        _FakeAdapter(), stock_universe={"600000"}, etf_universe={"510300"},
        overlap_days=1, lookback_days=365, rate_limiter=_NullRateLimiter())
    # 股票转换异常 → degraded
    assert res.degraded is True
    assert res.stock_failed == 1
    assert "stock" in (res.error or "")
    # ETF 未受影响，仍成功
    assert res.etf_refreshed > 0
    assert res.etf_failed == 0


def test_refresh_etf_convert_failure_not_break_stock(aux_db, monkeypatch):
    """ETF resolve_ts_codes 抛异常 → ETF degraded，股票仍成功刷新（反向隔离）。"""
    from quantstudio.pipeline.aligner import raw_to_tushare_ts_code
    from quantstudio.pipeline import qfq_maintenance as mmaint

    def _boom(codes, asset_type, main_db):
        if asset_type == "ETF":
            raise RuntimeError("simulated convert failure for ETF")
        return [raw_to_tushare_ts_code(c, "STOCK") for c in codes]

    monkeypatch.setattr(mmaint, "resolve_ts_codes", _boom)
    refresher = QFQFactorRefresher(aux_db=aux_db)
    res = refresher.refresh(
        _FakeAdapter(), stock_universe={"600000"}, etf_universe={"510300"},
        overlap_days=1, lookback_days=365, rate_limiter=_NullRateLimiter())
    assert res.degraded is True
    assert res.etf_failed == 1
    assert "etf" in (res.error or "")
    assert res.stock_refreshed > 0
    assert res.stock_failed == 0
