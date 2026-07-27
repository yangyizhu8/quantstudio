"""F4a/F5：正式管线集成测试（审核返工）

- fetch_table 统一归一化 None/'ALL'/['ALL']（不得出现 ALL.SI 请求）；
- index_daily 正式动态宇宙 = 普通指数 + SW2021 L1（daemon per-stock 路径）；
- daemon full_range / incremental / 次日更新 / watermark 全链路集成（公共
  execute_task 入口，full/incremental/resident 共用同一 fetch→align→
  validate→write→watermark 路径）；
- index_constituents 写入后 snapshot_meta 完整性打点（partial/complete）。
"""
from __future__ import annotations

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from quantstudio.pipeline.sources.tushare_adapter import TushareAdapter
from quantstudio.pipeline.aligner import FieldAligner
from quantstudio.pipeline.validator import PreIngestValidator
from quantstudio.pipeline.writers import DuckDBWriter
from quantstudio.pipeline.quarantine import Quarantine
from quantstudio.pipeline.daemon import ResidentCollector, BatchAudit


def _ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="Asia/Shanghai").timestamp() * 1000)


TRADE_DATES = ["20260722", "20260723", "20260724"]

_CLASSIFY_L1 = pd.DataFrame([
    {"index_code": "801010.SI", "industry_name": "农林牧渔", "level": "L1",
     "industry_code": "110000", "is_pub": 1, "parent_code": "0", "src": "SW2021"},
    {"index_code": "801020.SI", "industry_name": "基础化工", "level": "L1",
     "industry_code": "220000", "is_pub": 1, "parent_code": "0", "src": "SW2021"},
] + [
    {"index_code": f"8011{i:02d}.SI", "industry_name": f"行业{i}", "level": "L1",
     "industry_code": f"11{i:03d}", "is_pub": 1, "parent_code": "0",
     "src": "SW2021"} for i in range(30, 59)
])  # 共 31 个 SW2021 L1 行业（满足宇宙完整性门控）

_SW31_CODES = [f"8011{i:02d}" for i in range(30, 59)]

_MEMBER_801010 = pd.DataFrame([
    {"index_code": "801010.SI", "con_code": "600000.SH",
     "in_date": "20180101", "out_date": None, "is_new": "Y"},
])
_MEMBER_801020 = pd.DataFrame([
    {"index_code": "801020.SI", "con_code": "000001.SZ",
     "in_date": "20180101", "out_date": None, "is_new": "Y"},
])


def _fake_pro(member_map=None, member_fail=None):
    calls = {"index_daily": [], "sw_daily": [], "index_member": [],
             "index_classify": 0}

    class FakePro:
        def index_classify(self, level=None, src=None):
            calls["index_classify"] += 1
            return _CLASSIFY_L1

        def _member_for(self, index_code):
            if member_map and index_code in member_map:
                return member_map[index_code]
            return pd.DataFrame([
                {"index_code": index_code, "con_code": "600000.SH",
                 "in_date": "20180101", "out_date": None, "is_new": "Y"}])

        def index_daily(self, ts_code=None, start_date=None, end_date=None):
            calls["index_daily"].append(ts_code)
            # 真实 tushare 对 000688.CSI 返回标准 .SH 代码
            out_code = "000688.SH" if str(ts_code).endswith(".CSI") else ts_code
            rows = [{"ts_code": out_code, "trade_date": d, "open": 100.0,
                     "high": 110.0, "low": 90.0, "close": 105.0,
                     "pre_close": 100.0, "change": 5.0, "pct_chg": 1.0,
                     "vol": 1000.0, "amount": 100000.0}
                    for d in TRADE_DATES if start_date <= d <= end_date]
            return pd.DataFrame(rows)

        def sw_daily(self, ts_code=None, start_date=None, end_date=None):
            calls["sw_daily"].append(ts_code)
            rows = [{"ts_code": ts_code, "trade_date": d, "name": "行业",
                     "open": 200.0, "low": 190.0, "high": 210.0, "close": 205.0,
                     "change": 5.0, "pct_change": 1.0, "vol": 10.0,
                     "amount": 100.0, "pe": 1.0, "pb": 1.0,
                     "float_mv": 1.0, "total_mv": 2.0}
                    for d in TRADE_DATES if start_date <= d <= end_date]
            return pd.DataFrame(rows)

        def index_member(self, index_code=None):
            calls["index_member"].append(index_code)
            if member_fail and index_code in member_fail:
                raise RuntimeError("simulated source failure")
            return self._member_for(index_code)

        def index_weight(self, index_code=None, start_date=None, end_date=None):
            # 250 只成分（expected=300 → partial 批次）
            return pd.DataFrame([
                {"index_code": "000300.SH", "con_code": f"60{i:04d}.SH",
                 "trade_date": "20200228", "weight": 0.4}
                for i in range(250)])

    return calls, FakePro


@pytest.fixture
def pro_env(monkeypatch):
    import tushare as ts
    calls, fake_cls = _fake_pro(member_map={
        "801010.SI": _MEMBER_801010, "801020.SI": _MEMBER_801020})
    monkeypatch.setattr(ts, "pro_api", lambda *a, **k: fake_cls())
    return calls


@pytest.fixture
def adapter(pro_env):
    return TushareAdapter({"name": "tushare", "token": "test-token"})


# ---------- F4a：fetch_table ALL 归一化（公共入口） ----------

def test_fetch_table_normalizes_all_list(adapter, pro_env):
    df, meta = adapter.fetch_table("industry_membership", "2018-01-01",
                                   "2026-07-24", codes=["ALL"])
    assert len(pro_env["index_member"]) == 31
    assert "ALL.SI" not in pro_env["index_member"]
    assert len(df) == 31


def test_fetch_table_normalizes_all_string(adapter, pro_env):
    df, meta = adapter.fetch_table("industry_classification", "2018-01-01",
                                   "2026-07-24", codes="ALL")
    assert len(df) == 31  # SW2021 L1 分类定义 31 条
    df2, _ = adapter.fetch_table("industry_membership", "2018-01-01",
                                 "2026-07-24", codes=None)
    assert len(df2) == 31


def test_fetch_index_daily_normalizes_all(adapter, pro_env):
    df, meta = adapter.fetch_table("index_daily", "2026-07-22", "2026-07-23",
                                   codes=["ALL"])
    assert "ALL" not in pro_env["index_daily"]
    assert "ALL.SI" not in pro_env["sw_daily"]
    assert len(pro_env["sw_daily"]) == 31
    assert len(pro_env["index_daily"]) == 14  # 普通指数宇宙
    sw_rows = df[df["ts_code"].astype(str).str.endswith(".SI")]
    assert len(sw_rows) == 31 * 2  # 31 行业 × 2 交易日


def test_empty_single_industry_fail_closed_all_or_nothing(monkeypatch):
    """单行业成员空结果 → all-or-nothing 返回空，不写部分快照。"""
    import tushare as ts
    calls, fake_cls = _fake_pro(
        member_map={"801010.SI": _MEMBER_801010,
                    "801020.SI": _MEMBER_801020.iloc[0:0]})
    monkeypatch.setattr(ts, "pro_api", lambda *a, **k: fake_cls())
    adapter = TushareAdapter({"name": "tushare", "token": "test-token"})
    df, meta = adapter.fetch_table("industry_membership", "2018-01-01",
                                   "2026-07-24", codes=["ALL"])
    assert df.empty


# ---------- daemon 集成：正式 full/incremental/次日更新 ----------

@pytest.fixture
def daemon(tmp_path, adapter):
    writer = DuckDBWriter({"type": "duckdb", "path": str(tmp_path / "q.duckdb")})
    aligner = FieldAligner.from_config("config/alignment_rules.json")
    quarantine = Quarantine(str(tmp_path / "quarantine.db"))
    validator = PreIngestValidator.from_config("config/alignment_rules.json", quarantine)
    batch_audit = BatchAudit(tmp_path / "audit.db")
    tasks_cfg = {"tasks": [{
        "name": "index_daily", "enabled": True, "source": "tushare",
        "table": "index_daily", "freq": "daily", "codes": ["ALL"],
        "start_date": "2026-07-20", "end_date": "2026-07-23",
        "retry": {"max": 1, "backoff_sec": [1]},
        "rate_limit": {"calls_per_min": 600, "wait_on_429": False},
        "max_workers": 1, "source_priority": ["tushare"],
    }]}
    d = ResidentCollector({}, {"sources": {"tushare": {"enabled": True}}},
                          tasks_cfg, aligner, validator, writer,
                          quarantine, batch_audit)
    d._adapters["tushare"] = adapter
    yield d
    d.close()


def _index_task(daemon):
    return daemon.tasks_cfg["tasks"][0]


def test_daemon_full_range_includes_sw_universe(daemon):
    """full_range：正式宇宙含普通指数 + SW L1；801xxx 进入 index_daily。"""
    ok = daemon.execute_task(_index_task(daemon), mode="full_range",
                             run_quality_audit=False)
    assert ok
    rows = daemon.writer.execute_read(
        "SELECT DISTINCT code FROM index_daily ORDER BY code")
    codes = [r[0] for r in rows]
    assert "801010" in codes and "801020" in codes
    assert "000300" in codes
    n = daemon.writer.execute_read(
        "SELECT COUNT(*) FROM index_daily WHERE code LIKE '801%'")[0][0]
    assert n == 31 * 2  # 31 行业 × 2 交易日（full_range ∩ mock 日历）
    wm = daemon.writer.get_last_date("tushare", "index_daily", "daily")
    assert wm is not None


def test_daemon_incremental_next_day_update(daemon):
    """次日增量：watermark+1 起拉，仅新增新交易日，水位推进，重跑幂等。"""
    assert daemon.execute_task(_index_task(daemon), mode="full_range",
                               run_quality_audit=False)
    wm1 = daemon.writer.get_last_date("tushare", "index_daily", "daily")
    before = daemon.writer.execute_read("SELECT COUNT(*) FROM index_daily")[0][0]

    assert daemon.execute_task(_index_task(daemon), mode="incremental",
                               run_quality_audit=False)
    wm2 = daemon.writer.get_last_date("tushare", "index_daily", "daily")
    after = daemon.writer.execute_read("SELECT COUNT(*) FROM index_daily")[0][0]
    assert int(wm2) > int(wm1)                     # 水位推进
    # 宇宙 45 个代码（14 普通 + 31 SW），000688.SH/.CSI 归一后为同一裸码 → 44 个新行
    assert after - before == 44
    latest = daemon.writer.execute_read(
        "SELECT COUNT(DISTINCT code) FROM index_daily WHERE time = ?", [int(wm2)])[0][0]
    assert latest == 44

    # 重跑幂等：upsert 不产生重复
    assert daemon.execute_task(_index_task(daemon), mode="incremental",
                               run_quality_audit=False)
    assert daemon.writer.execute_read("SELECT COUNT(*) FROM index_daily")[0][0] == after


def test_daemon_resident_entry_same_path(daemon):
    """resident/CLI 公共入口 run_once 与手动执行共享同一管线。"""
    daemon.run_once(task_name="index_daily", mode="full_range")
    codes = [r[0] for r in daemon.writer.execute_read(
        "SELECT DISTINCT code FROM index_daily WHERE code LIKE '801%' "
        "ORDER BY code")]
    assert codes == ["801010", "801020"] + _SW31_CODES


# ---------- F5 审核返工：SW 宇宙门控失败 → 任务失败且水位不变 ----------

def _make_daemon(tmp_path, adapter, table="index_daily"):
    writer = DuckDBWriter({"type": "duckdb", "path": str(tmp_path / "q3.duckdb")})
    aligner = FieldAligner.from_config("config/alignment_rules.json")
    quarantine = Quarantine(str(tmp_path / "q3q.db"))
    validator = PreIngestValidator.from_config("config/alignment_rules.json", quarantine)
    tasks_cfg = {"tasks": [{
        "name": table, "enabled": True, "source": "tushare",
        "table": table, "freq": "daily", "codes": ["ALL"],
        "start_date": "2026-07-20", "end_date": "2026-07-23",
        "retry": {"max": 1, "backoff_sec": [1]},
        "rate_limit": {"calls_per_min": 600, "wait_on_429": False},
        "max_workers": 1, "source_priority": ["tushare"],
    }]}
    d = ResidentCollector({}, {"sources": {"tushare": {"enabled": True}}},
                          tasks_cfg, aligner, validator, writer,
                          quarantine, BatchAudit(tmp_path / "audit3.db"))
    d._adapters["tushare"] = adapter
    return d


def _adapter_with_classify(monkeypatch, classify_df):
    import tushare as ts
    calls, fake_cls = _fake_pro()
    cls = _CLASSIFY_L1 if classify_df is None else classify_df

    class Pro(fake_cls):
        def index_classify(self, level=None, src=None):
            return cls

    monkeypatch.setattr(ts, "pro_api", lambda *a, **k: Pro())
    return TushareAdapter({"name": "tushare", "token": "test-token"})


def _full_classify(n=31):
    return pd.DataFrame([
        {"index_code": f"801{i:03d}.SI", "industry_name": f"行业{i}",
         "level": "L1", "industry_code": str(i), "is_pub": 1,
         "parent_code": "0", "src": "SW2021"} for i in range(10, 10 + n)])


def test_sw_universe_30_codes_task_fails_watermark_unchanged(tmp_path, monkeypatch):
    """SW 宇宙 30 个（缺 1）→ 整个任务失败，水位不变，不写任何行。"""
    adapter = _adapter_with_classify(monkeypatch, _full_classify(30))
    d = _make_daemon(tmp_path, adapter)
    try:
        ok = d.execute_task(d.tasks_cfg["tasks"][0], mode="full_range",
                            run_quality_audit=False)
        assert ok is False
        assert d.writer.get_last_date("tushare", "index_daily", "daily") is None
        assert d.writer.execute_read("SELECT COUNT(*) FROM index_daily")[0][0] == 0
    finally:
        d.close()


def test_sw_universe_probe_exception_task_fails(tmp_path, monkeypatch):
    """probe 异常 → 任务失败，水位不变。"""
    import tushare as ts
    calls, fake_cls = _fake_pro()

    class Pro(fake_cls):
        def index_classify(self, level=None, src=None):
            raise RuntimeError("network down")

    monkeypatch.setattr(ts, "pro_api", lambda *a, **k: Pro())
    adapter = TushareAdapter({"name": "tushare", "token": "test-token"})
    d = _make_daemon(tmp_path, adapter)
    try:
        ok = d.execute_task(d.tasks_cfg["tasks"][0], mode="full_range",
                            run_quality_audit=False)
        assert ok is False
        assert d.writer.get_last_date("tushare", "index_daily", "daily") is None
    finally:
        d.close()


def test_sw_universe_duplicate_codes_task_fails(tmp_path, monkeypatch):
    """SW 宇宙含重复代码 → 任务失败。"""
    dup = pd.concat([_full_classify(31),
                     _full_classify(31).iloc[[0]]], ignore_index=True)
    adapter = _adapter_with_classify(monkeypatch, dup)
    d = _make_daemon(tmp_path, adapter)
    try:
        ok = d.execute_task(d.tasks_cfg["tasks"][0], mode="full_range",
                            run_quality_audit=False)
        assert ok is False
        assert d.writer.get_last_date("tushare", "index_daily", "daily") is None
    finally:
        d.close()


def test_sw_universe_bad_format_task_fails(tmp_path, monkeypatch):
    """SW 宇宙格式非法（缺 .SI / 非 6 位）→ 任务失败。"""
    bad = _full_classify(31).copy()
    bad.loc[0, "index_code"] = "80101.SI"   # 5 位
    adapter = _adapter_with_classify(monkeypatch, bad)
    d = _make_daemon(tmp_path, adapter)
    try:
        ok = d.execute_task(d.tasks_cfg["tasks"][0], mode="full_range",
                            run_quality_audit=False)
        assert ok is False
    finally:
        d.close()


def test_daemon_index_constituents_writes_snapshot_meta(tmp_path, monkeypatch):
    """index_constituents 写入后打 snapshot_meta（250/300 → partial 契约）。"""
    import tushare as ts
    calls, fake_cls = _fake_pro()
    monkeypatch.setattr(ts, "pro_api", lambda *a, **k: fake_cls())
    adapter = TushareAdapter({"name": "tushare", "token": "test-token"})
    writer = DuckDBWriter({"type": "duckdb", "path": str(tmp_path / "q2.duckdb")})
    aligner = FieldAligner.from_config("config/alignment_rules.json")
    quarantine = Quarantine(str(tmp_path / "quarantine2.db"))
    validator = PreIngestValidator.from_config("config/alignment_rules.json", quarantine)
    tasks_cfg = {"tasks": [{
        "name": "index_constituents", "enabled": True, "source": "tushare",
        "table": "index_constituents", "freq": "daily", "codes": ["000300"],
        "start_date": "2020-02-01", "retry": {"max": 1, "backoff_sec": [1]},
        "rate_limit": {"calls_per_min": 600, "wait_on_429": False},
        "max_workers": 1, "source_priority": ["tushare"],
    }]}
    d = ResidentCollector({}, {"sources": {"tushare": {"enabled": True}}},
                          tasks_cfg, aligner, validator, writer,
                          quarantine, BatchAudit(tmp_path / "audit2.db"))
    d._adapters["tushare"] = adapter
    try:
        ok = d.execute_task(tasks_cfg["tasks"][0], mode="full_range",
                            run_quality_audit=False)
        assert ok
        meta = d.writer.execute_read(
            "SELECT n_constituents, expected_count, status "
            "FROM index_constituents_snapshot_meta WHERE index_code='000300'")
        assert meta == [(250, 300, "partial")]
    finally:
        d.close()
