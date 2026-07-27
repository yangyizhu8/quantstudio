"""F4: 行业分类语义治理与历史 PIT 行业归属测试（任务书 §6.7）

契约：
- 正式能力 = industry_classification（分类定义）+ industry_membership（成员历史，
  effective_from/effective_to PIT 区间）；
- get_industry 查询语义：effective_from <= as_of AND (effective_to IS NULL OR effective_to >= as_of)，
  边界重叠时 fail-closed（抛 ReferenceDataCapabilityError）；
- 无有效历史归属返回 None，绝不使用最新行业、绝不读数据库全局最新快照；
- 正式表缺失时 fail-closed（ReferenceDataCapabilityError）；
- 禁止 stock_basic.industry 中文名匹配 + 伪 SW_行业名 代码。
"""
from __future__ import annotations

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from quantstudio.backtest.providers.base import ReferenceDataCapabilityError
from quantstudio.backtest.providers.duckdb_provider import DuckDBReferenceDataProvider
from quantstudio.backtest.ptrade_api import PtradeAPI


def _ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="Asia/Shanghai").timestamp() * 1000)


CLASSIFICATION_ROWS = [
    # system, version, industry_code, industry_name, level, parent, from, to, update_time, source
    ("SW", "SW2021", "801010", "农林牧渔", "L1", None, 0, None, "2026-01-01", "tushare"),
    ("SW", "SW2021", "801020", "基础化工", "L1", None, 0, None, "2026-01-01", "tushare"),
]

# 600000：2018-01-01→2020-06-30 属 801010；2020-07-01 起属 801020（迁移）
# 600519：边界重叠 —— A.effective_to = B.effective_from = 2020-07-01 → 重叠（官方无裁决规则 → fail-closed）
# 000001：无任何归属
MEMBERSHIP_ROWS = [
    ("SW", "SW2021", "L1", "801010", "600000", _ms("2018-01-01"), _ms("2020-06-30"), "2026-01-01", "tushare"),
    ("SW", "SW2021", "L1", "801020", "600000", _ms("2020-07-01"), None, "2026-01-01", "tushare"),
    ("SW", "SW2021", "L1", "801010", "600519", _ms("2018-01-01"), _ms("2020-07-01"), "2026-01-01", "tushare"),
    ("SW", "SW2021", "L1", "801020", "600519", _ms("2020-07-01"), None, "2026-01-01", "tushare"),
]


def _create_formal_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS industry_classification (
            classification_system VARCHAR, classification_version VARCHAR,
            industry_code VARCHAR, industry_name VARCHAR, industry_level VARCHAR,
            parent_industry_code VARCHAR, effective_from BIGINT, effective_to BIGINT,
            update_time VARCHAR, data_source VARCHAR,
            PRIMARY KEY (classification_system, classification_version,
                         industry_level, industry_code, effective_from))""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS industry_membership (
            classification_system VARCHAR, classification_version VARCHAR,
            industry_level VARCHAR, industry_code VARCHAR, code VARCHAR,
            effective_from BIGINT, effective_to BIGINT,
            update_time VARCHAR, data_source VARCHAR,
            PRIMARY KEY (classification_system, classification_version,
                         industry_level, industry_code, code, effective_from))""")


@pytest.fixture
def ind_db(tmp_path):
    db = tmp_path / "ind.duckdb"
    con = duckdb.connect(str(db))
    _create_formal_tables(con)
    con.executemany("INSERT INTO industry_classification VALUES (?,?,?,?,?,?,?,?,?,?)",
                    CLASSIFICATION_ROWS)
    con.executemany("INSERT INTO industry_membership VALUES (?,?,?,?,?,?,?,?,?)",
                    MEMBERSHIP_ROWS)
    con.close()
    return db


@pytest.fixture
def legacy_only_db(tmp_path):
    """只有旧 sw_industry 快照表，无正式表。"""
    db = tmp_path / "legacy.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE sw_industry (
            code VARCHAR, industry_code VARCHAR, industry_name VARCHAR,
            industry_level VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, industry_code))""")
    con.execute("INSERT INTO sw_industry VALUES ('600000', '801010', '农林牧渔', 'L1', '2026-01-01')")
    con.close()
    return db


@pytest.fixture
def provider(ind_db):
    return DuckDBReferenceDataProvider(ind_db)


# ---------- PIT 查询语义 ----------

def test_before_migration_returns_industry_a(provider):
    result = provider.get_industry("600000", "2019-06-01")
    assert result["sw_l1"]["industry_code"] == "801010"
    assert result["sw_l1"]["industry_name"] == "农林牧渔"


def test_migration_boundary_from_side(provider):
    """effective_to 当日仍属原行业（inclusive 契约）。"""
    assert provider.get_industry("600000", "2020-06-30")["sw_l1"]["industry_code"] == "801010"


def test_after_migration_returns_industry_b(provider):
    assert provider.get_industry("600000", "2020-07-01")["sw_l1"]["industry_code"] == "801020"
    assert provider.get_industry("600000", "2021-01-01")["sw_l1"]["industry_code"] == "801020"


def test_boundary_overlap_is_ambiguous_fail_closed(provider):
    """A.effective_to == B.effective_from 共享边界日：源端两条区间当日冲突，
    官方契约无裁决规则 → fail-closed（绝不任意选一条）。"""
    with pytest.raises(ReferenceDataCapabilityError):
        provider.get_industry("600519", "2020-07-01")
    # 非歧义日期正常服务
    assert provider.get_industry("600519", "2020-06-30")["sw_l1"]["industry_code"] == "801010"
    assert provider.get_industry("600519", "2020-07-02")["sw_l1"]["industry_code"] == "801020"


def test_positive_overlap_ambiguous_fail_closed(ind_db):
    """正持续时间重叠段内任意日期 → fail-closed。"""
    con = duckdb.connect(str(ind_db))
    con.execute("INSERT INTO industry_membership VALUES (?,?,?,?,?,?,?,?,?)",
                ("SW", "SW2021", "L1", "801010", "600999",
                 _ms("2018-01-01"), None, "t", "tushare"))
    con.execute("INSERT INTO industry_membership VALUES (?,?,?,?,?,?,?,?,?)",
                ("SW", "SW2021", "L1", "801020", "600999",
                 _ms("2019-01-01"), None, "t", "tushare"))
    con.close()
    provider = DuckDBReferenceDataProvider(ind_db)
    with pytest.raises(ReferenceDataCapabilityError):
        provider.get_industry("600999", "2020-06-01")
    # 重叠前的唯一归属日期正常服务
    assert provider.get_industry("600999", "2018-06-01")["sw_l1"]["industry_code"] == "801010"
    # date=None（当前）多 current → fail-closed
    with pytest.raises(ReferenceDataCapabilityError):
        provider.get_industry("600999", None)


def test_future_industry_does_not_leak(provider):
    """归属生效前查询 → None（未来行业不泄漏）。"""
    assert provider.get_industry("600000", "2017-12-31") is None


def test_unclassified_stock_returns_none(provider):
    assert provider.get_industry("000001", "2021-01-01") is None


def test_no_pseudo_sw_name_codes(provider):
    """结果绝不出现伪 SW_行业名 代码。"""
    for date in ("2019-01-01", "2020-07-01", "2021-01-01"):
        result = provider.get_industry("600000", date)
        assert result is not None
        assert not str(result["sw_l1"]["industry_code"]).startswith("SW_")


def test_classification_system_and_version_in_result(provider):
    result = provider.get_industry("600000", "2021-01-01")
    assert result["sw_l1"]["classification_system"] == "SW"
    assert result["sw_l1"]["classification_version"] == "SW2021"


def test_provider_date_none_returns_current_membership(provider):
    """Provider 直接调用 date=None：返回当前有效归属（文档化兼容）。"""
    assert provider.get_industry("600000", None)["sw_l1"]["industry_code"] == "801020"


# ---------- fail-closed ----------

def test_missing_formal_tables_fail_closed(legacy_only_db):
    """正式表缺失 → fail-closed，绝不回退到旧 sw_industry 快照。"""
    provider = DuckDBReferenceDataProvider(legacy_only_db)
    with pytest.raises(ReferenceDataCapabilityError):
        provider.get_industry("600000", "2021-01-01")


def test_api_fail_closed_propagates(legacy_only_db):
    """API 层不得吞掉能力缺失错误。"""
    api = PtradeAPI(reference=DuckDBReferenceDataProvider(legacy_only_db))
    with pytest.raises(ReferenceDataCapabilityError):
        api.get_industry("600000")


# ---------- PTrade API 层（签名不变，自动注入回测日期） ----------

def test_api_uses_current_backtest_date(ind_db):
    """回测上下文：get_industry 用当前回测日期，历史策略不读最新快照。"""
    api = PtradeAPI(reference=DuckDBReferenceDataProvider(ind_db))
    api._current_date = "2019-06-01"
    result = api.get_industry("600000.SH")
    assert result["sw_l1"]["industry_code"] == "801010"  # 非最新的 801020
    api._current_date = "2021-01-01"
    assert api.get_industry("600000.SH")["sw_l1"]["industry_code"] == "801020"


def test_api_unclassified_returns_none(ind_db):
    api = PtradeAPI(reference=DuckDBReferenceDataProvider(ind_db))
    api._current_date = "2021-01-01"
    assert api.get_industry("000001.SZ") is None


# ==================== 管线侧：adapter / aligner / writer ====================

def _make_adapter():
    """不触发网络：绕过 __init__ 构造，注入假 rate_limiter。"""
    from quantstudio.pipeline.sources.tushare_adapter import TushareAdapter

    adapter = object.__new__(TushareAdapter)
    adapter.token = "test-token"

    class _NoopLimiter:
        def acquire(self):
            return None

    adapter.rate_limiter = _NoopLimiter()
    return adapter


def _mock_pro(monkeypatch, classify_df=None, member_map=None, fail_on=None):
    """mock tushare.pro_api，返回假 index_classify / index_member。"""
    import tushare as ts

    class FakePro:
        def index_classify(self, level=None, src=None):
            assert level == "L1" and src == "SW2021"
            return classify_df

        def index_member(self, index_code=None):
            if fail_on and index_code in fail_on:
                raise RuntimeError("simulated source failure")
            return member_map[index_code]

    monkeypatch.setattr(ts, "pro_api", lambda *a, **k: FakePro())


_CLASSIFY_DF = pd.DataFrame([
    {"index_code": "801010.SI", "industry_name": "农林牧渔", "level": "L1",
     "industry_code": "110000", "is_pub": 1, "parent_code": "0", "src": "SW2021"},
    {"index_code": "801020.SI", "industry_name": "基础化工", "level": "L1",
     "industry_code": "220000", "is_pub": 1, "parent_code": "0", "src": "SW2021"},
])

_MEMBER_801010 = pd.DataFrame([
    {"index_code": "801010.SI", "con_code": "600000.SH",
     "in_date": "20180101", "out_date": "20200630", "is_new": "N"},
    {"index_code": "801010.SI", "con_code": "000001.SZ",
     "in_date": "20211213", "out_date": None, "is_new": "Y"},
])

_MEMBER_801020 = pd.DataFrame([
    {"index_code": "801020.SI", "con_code": "600000.SH",
     "in_date": "20200701", "out_date": None, "is_new": "Y"},
])


def test_adapter_classification_canonical(monkeypatch):
    """index_classify → industry_classification canonical 行（裸码、版本、无伪代码）。"""
    _mock_pro(monkeypatch, classify_df=_CLASSIFY_DF, member_map={})
    adapter = _make_adapter()
    df, meta = adapter._fetch_industry_classification("2018-01-01", "2026-07-24", None)
    assert len(df) == 2
    row = df[df["industry_code"] == "801010"].iloc[0]
    assert row["classification_system"] == "SW"
    assert row["classification_version"] == "SW2021"
    assert row["industry_level"] == "L1"
    assert row["industry_name"] == "农林牧渔"
    assert not df["industry_code"].astype(str).str.startswith("SW_").any()
    assert meta["table"] == "industry_classification"


def test_adapter_membership_pit_ranges(monkeypatch):
    """index_member → industry_membership PIT 区间（ms，裸码，out_date 空→NULL）。"""
    _mock_pro(monkeypatch, classify_df=_CLASSIFY_DF,
              member_map={"801010.SI": _MEMBER_801010, "801020.SI": _MEMBER_801020})
    adapter = _make_adapter()
    df, meta = adapter._fetch_industry_membership("2018-01-01", "2026-07-24", None)
    assert len(df) == 3
    out = df[(df["industry_code"] == "801010") & (df["code"] == "600000")].iloc[0]
    assert int(out["effective_from"]) == _ms("2018-01-01")
    assert int(out["effective_to"]) == _ms("2020-06-30")
    current = df[(df["industry_code"] == "801010") & (df["code"] == "000001")].iloc[0]
    assert pd.isna(current["effective_to"])
    assert (df["classification_version"] == "SW2021").all()
    assert meta["table"] == "industry_membership"


def test_adapter_membership_fail_closed_on_partial_failure(monkeypatch):
    """任一行业成员拉取失败 → all-or-nothing 返回空，不写部分快照。"""
    _mock_pro(monkeypatch, classify_df=_CLASSIFY_DF,
              member_map={"801010.SI": _MEMBER_801010, "801020.SI": _MEMBER_801020},
              fail_on={"801020.SI"})
    adapter = _make_adapter()
    df, meta = adapter._fetch_industry_membership("2018-01-01", "2026-07-24", None)
    assert df.empty


def test_adapter_membership_fail_closed_on_field_drift(monkeypatch):
    """SDK 字段漂移（缺 out_date）→ fail-closed 返回空。"""
    drifted = _MEMBER_801010.drop(columns=["out_date"])
    _mock_pro(monkeypatch, classify_df=_CLASSIFY_DF,
              member_map={"801010.SI": drifted, "801020.SI": _MEMBER_801020})
    adapter = _make_adapter()
    df, meta = adapter._fetch_industry_membership("2018-01-01", "2026-07-24", None)
    assert df.empty


def test_adapter_supports_new_formal_tasks():
    adapter = _make_adapter()
    assert adapter.supports_task("industry_classification", "daily") == (True, "")
    assert adapter.supports_task("industry_membership", "daily") == (True, "")


def test_adapter_sw_industry_name_matching_removed():
    """错误的 index_classify+stock_basic 名称匹配路径必须移除，
    tushare 不再宣称 sw_industry 能力。"""
    from quantstudio.pipeline.sources.tushare_adapter import TushareAdapter
    assert not hasattr(TushareAdapter, "_fetch_sw_industry")
    adapter = _make_adapter()
    ok, _ = adapter.supports_task("sw_industry", "daily")
    assert ok is False


def test_writer_ddl_and_upsert(tmp_path):
    """两张正式表 DDL + 幂等 upsert（重放不重复，changed-row 更新）。"""
    from quantstudio.pipeline.writers import DuckDBWriter

    writer = DuckDBWriter({"type": "duckdb", "path": str(tmp_path / "w.duckdb")})
    cls = pd.DataFrame(CLASSIFICATION_ROWS, columns=[
        "classification_system", "classification_version", "industry_code",
        "industry_name", "industry_level", "parent_industry_code",
        "effective_from", "effective_to", "update_time", "data_source"])
    mem = pd.DataFrame(MEMBERSHIP_ROWS, columns=[
        "classification_system", "classification_version", "industry_level",
        "industry_code", "code", "effective_from", "effective_to",
        "update_time", "data_source"])
    n1 = writer.write(cls, "industry_classification", "b1")
    n2 = writer.write(mem, "industry_membership", "b1")
    assert int(n1) == 2 and int(n2) == 4
    # 重放幂等
    writer.write(cls, "industry_classification", "b1-replay")
    writer.write(mem, "industry_membership", "b1-replay")
    rows = writer.execute_read("SELECT COUNT(*) FROM industry_classification")
    assert rows[0][0] == 2
    rows = writer.execute_read("SELECT COUNT(*) FROM industry_membership")
    assert rows[0][0] == 4
    # changed-row upsert：行业名更新
    cls2 = cls.copy()
    cls2.loc[cls2["industry_code"] == "801010", "industry_name"] = "农林牧渔(新)"
    writer.write(cls2, "industry_classification", "b2")
    rows = writer.execute_read(
        "SELECT industry_name FROM industry_classification WHERE industry_code='801010'")
    assert rows[0][0] == "农林牧渔(新)"


def test_aligner_identity_mapping(tmp_path):
    """alignment_rules.json 中 tushare 对两张正式表的映射可用且产出 canonical 列。"""
    from quantstudio.pipeline.aligner import FieldAligner

    aligner = FieldAligner.from_config("config/alignment_rules.json")
    mem = pd.DataFrame(MEMBERSHIP_ROWS, columns=[
        "classification_system", "classification_version", "industry_level",
        "industry_code", "code", "effective_from", "effective_to",
        "update_time", "data_source"]).drop(columns=["data_source", "update_time"])
    std, meta = aligner.align(mem, "industry_membership", "tushare")
    for col in ("classification_system", "classification_version", "industry_level",
                "industry_code", "code", "effective_from", "effective_to",
                "update_time"):
        assert col in std.columns, col
    cls = pd.DataFrame(CLASSIFICATION_ROWS, columns=[
        "classification_system", "classification_version", "industry_code",
        "industry_name", "industry_level", "parent_industry_code",
        "effective_from", "effective_to", "update_time", "data_source"]).drop(
        columns=["data_source", "update_time"])
    std2, _ = aligner.align(cls, "industry_classification", "tushare")
    assert "industry_name" in std2.columns and "update_time" in std2.columns

