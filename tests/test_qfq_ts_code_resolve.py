"""C3：裸码 → Tushare ts_code 转换与 resolve_ts_codes 契约测试。

覆盖：
- raw_to_tushare_ts_code 纯前缀规则（STOCK/ETF 各市场，幂等，.SS→.SH，非法输入抛错）。
- resolve_ts_codes 元数据优先级（hit 用表值 / null→fallback / 非法→fallback / 串码→warning+fallback）。
- resolve_ts_codes 顺序与数量守恒（混合裸码+带后缀，不重排、不丢码）。
- 元数据表缺失/异常 → 全量 fallback 不阻断。
"""

import re
import sqlite3

import duckdb
import pytest

from quantstudio.pipeline.aligner import raw_to_tushare_ts_code
from quantstudio.pipeline.qfq_maintenance import resolve_ts_codes

_TS_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


# ---------------------------------------------------------------------------
# 1. raw_to_tushare_ts_code 纯前缀规则
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code,asset,expected", [
    ("600000", "STOCK", "600000.SH"),
    ("000001", "STOCK", "000001.SZ"),
    ("300750", "STOCK", "300750.SZ"),  # 创业板 3 → SZ
    ("430047", "STOCK", "430047.BJ"),  # 北交所 4
    ("830799", "STOCK", "830799.BJ"),  # 北交所 8
    ("510300", "ETF", "510300.SH"),    # 沪市 ETF 5
    ("588000", "ETF", "588000.SH"),    # 科创 ETF 5（market_of_code 会误判 BJ）
    ("159915", "ETF", "159915.SZ"),    # 深市 ETF 1
])
def test_raw_to_tushare_ts_code_prefix_rules(code, asset, expected):
    assert raw_to_tushare_ts_code(code, asset) == expected


def test_raw_to_tushare_ts_code_idempotent():
    """已带合法后缀 → 幂等返回（大写）。"""
    assert raw_to_tushare_ts_code("600000.SH", "STOCK") == "600000.SH"
    assert raw_to_tushare_ts_code("510300.sh", "ETF") == "510300.SH"  # 小写规范化


def test_raw_to_tushare_ts_code_ss_to_sh():
    """兼容项目内部 .SS → Tushare .SH。"""
    assert raw_to_tushare_ts_code("510050.SS", "ETF") == "510050.SH"


def test_raw_to_tushare_ts_code_invalid_asset_type():
    with pytest.raises(ValueError, match="asset_type"):
        raw_to_tushare_ts_code("600000", "BOND")


def test_raw_to_tushare_ts_code_unknown_suffix_rejected():
    """未知后缀 → 拒绝（避免双后缀如 600000.BAD.SH）。"""
    with pytest.raises(ValueError, match="未知后缀"):
        raw_to_tushare_ts_code("600000.BAD", "STOCK")


def test_raw_to_tushare_ts_code_non_digit_bare_rejected():
    with pytest.raises(ValueError, match="6 位数字"):
        raw_to_tushare_ts_code("ABCDEF", "STOCK")


# ---------------------------------------------------------------------------
# 2. resolve_ts_codes 元数据优先级（用真实 DuckDB 临时库建 stock_basic/etf_basic）
# ---------------------------------------------------------------------------
@pytest.fixture
def main_db_with_metadata(tmp_path):
    """建临时 quantstudio.db，含 stock_basic/etf_basic 元数据（code 裸码 + ts_code 带后缀）。"""
    db = tmp_path / "quantstudio.db"
    with duckdb.connect(str(db)) as conn:
        conn.execute("""CREATE TABLE stock_basic (
            code VARCHAR, ts_code VARCHAR, name VARCHAR)""")
        conn.execute("""INSERT INTO stock_basic VALUES
            ('600000', '600000.SH', '浦发银行'),
            ('000001', '000001.SZ', '平安银行'),
            ('430047', '430047.BJ', '北交所样例'),
            ('600099', NULL, 'null ts_code'),
            ('600088', '600088.XX', '非法后缀'),
            ('600077', '600078.SH', '裸码不一致')""")
        conn.execute("""CREATE TABLE etf_basic (
            code VARCHAR, ts_code VARCHAR, name VARCHAR)""")
        conn.execute("""INSERT INTO etf_basic VALUES
            ('510300', '510300.SH', '沪深300ETF'),
            ('588000', '588000.SH', '科创50ETF'),
            ('159915', '159915.SZ', '创业板ETF')""")
    return str(db)


def test_resolve_metadata_hit_uses_table_ts_code(main_db_with_metadata):
    """元数据命中 → 用表内 ts_code（而非前缀结果）。"""
    out = resolve_ts_codes(["600000"], "STOCK", main_db_with_metadata)
    assert out == ["600000.SH"]


def test_resolve_metadata_null_falls_back(main_db_with_metadata):
    """元数据 ts_code 为 NULL → fallback 到前缀。"""
    out = resolve_ts_codes(["600099"], "STOCK", main_db_with_metadata)
    assert out == ["600099.SH"]  # 前缀 6 → SH


def test_resolve_metadata_invalid_falls_back(main_db_with_metadata):
    """元数据 ts_code 非法（600088.XX）→ fallback 到前缀。"""
    out = resolve_ts_codes(["600088"], "STOCK", main_db_with_metadata)
    assert out == ["600088.SH"]


def test_resolve_metadata_mismatch_warns_and_falls_back(main_db_with_metadata, caplog):
    """元数据 ts_code 裸码与 code 不一致（600077→600078.SH）→ warning + fallback。"""
    import logging
    with caplog.at_level(logging.WARNING, logger="quantstudio.pipeline.qfq_maintenance"):
        out = resolve_ts_codes(["600077"], "STOCK", main_db_with_metadata)
    assert out == ["600077.SH"]  # fallback 到 600077 自己的前缀
    assert any("串码" in r.getMessage() for r in caplog.records)


def test_resolve_etf_metadata_hit(main_db_with_metadata):
    """ETF 元数据命中（588000 科创 ETF，market_of_code 会误判但元数据正确）。"""
    out = resolve_ts_codes(["588000", "159915"], "ETF", main_db_with_metadata)
    assert out == ["588000.SH", "159915.SZ"]


# ---------------------------------------------------------------------------
# 2b. 对抗测试：用冲突元数据真正区分"metadata 优先" vs "前缀 fallback"
#     （前缀结果与 metadata 不同，才能证明优先级生效）
# ---------------------------------------------------------------------------
@pytest.fixture
def main_db_conflict_metadata(tmp_path):
    """冲突元数据：600000 的 ts_code=600000.SZ（前缀会得 .SH，元数据得 .SZ）。

    用于区分：裸码输入应得元数据 .SZ；显式 .SH 输入应幂等保留 .SH。
    """
    db = tmp_path / "quantstudio.db"
    with duckdb.connect(str(db)) as conn:
        conn.execute("CREATE TABLE stock_basic (code VARCHAR, ts_code VARCHAR)")
        conn.execute("INSERT INTO stock_basic VALUES ('600000', '600000.SZ')")
    return str(db)


def test_resolve_bare_code_metadata_priority_over_prefix(main_db_conflict_metadata):
    """裸码输入 → 元数据优先（600000 前缀应得 .SH，但元数据是 .SZ，实际应得 .SZ）。

    这才能真正证明"裸码命中元数据时使用元数据"——而非与前缀结果相同而巧合通过。
    """
    out = resolve_ts_codes(["600000"], "STOCK", main_db_conflict_metadata)
    assert out == ["600000.SZ"]  # 元数据优先，不是前缀的 .SH


def test_resolve_explicit_suffix_idempotent_not_overridden(main_db_conflict_metadata):
    """显式合法后缀输入 → 幂等保留，绝不被元数据覆盖。

    元数据 600000.SZ 与调用者显式提供的 600000.SH 冲突，但显式值优先。
    """
    out = resolve_ts_codes(["600000.SH"], "STOCK", main_db_conflict_metadata)
    assert out == ["600000.SH"]  # 显式后缀幂等，不被元数据 600000.SZ 覆盖


def test_resolve_explicit_and_bare_mixed(main_db_conflict_metadata):
    """混合输入：显式后缀幂等 + 裸码走元数据，各自正确、顺序/数量守恒。"""
    out = resolve_ts_codes(["600000.SH", "600000"], "STOCK", main_db_conflict_metadata)
    assert out == ["600000.SH", "600000.SZ"]


def test_resolve_miss_uses_prefix_no_drop(main_db_with_metadata):
    """元数据未收录的码 → 前缀 fallback，不丢弃。"""
    out = resolve_ts_codes(["600000", "603999"], "STOCK", main_db_with_metadata)
    # 600000 元数据命中，603999 miss → 前缀
    assert out == ["600000.SH", "603999.SH"]


# ---------------------------------------------------------------------------
# 3. resolve_ts_codes 顺序与数量守恒（混合裸码+带后缀）
# ---------------------------------------------------------------------------
def test_resolve_order_and_count_preserved_stock(main_db_with_metadata):
    """STOCK：混合裸码+带后缀输入，输出顺序/长度一致，不丢码。"""
    inp = ["600000", "000001.SZ", "430047", "603999"]
    out = resolve_ts_codes(inp, "STOCK", main_db_with_metadata)
    assert len(out) == len(inp)
    # 顺序与输入一致
    assert out == ["600000.SH", "000001.SZ", "430047.BJ", "603999.SH"]


def test_resolve_order_and_count_preserved_etf(main_db_with_metadata):
    inp = ["510300", "588000.SH", "159915", "510050"]
    out = resolve_ts_codes(inp, "ETF", main_db_with_metadata)
    assert len(out) == len(inp)
    assert out == ["510300.SH", "588000.SH", "159915.SZ", "510050.SH"]


def test_resolve_does_not_mutate_input(main_db_with_metadata):
    """不修改调用者传入的列表。"""
    inp = ["600000", "000001"]
    inp_copy = list(inp)
    resolve_ts_codes(inp, "STOCK", main_db_with_metadata)
    assert inp == inp_copy


# ---------------------------------------------------------------------------
# 4. 元数据表缺失/异常 → 全量 fallback 不阻断
# ---------------------------------------------------------------------------
def test_resolve_missing_table_all_fallback(tmp_path):
    """main_db 不含 stock_basic → 全量前缀 fallback，不抛。"""
    db = tmp_path / "quantstudio.db"
    with duckdb.connect(str(db)) as conn:
        conn.execute("CREATE TABLE other(x VARCHAR)")  # 无 stock_basic/etf_basic
    # STOCK asset_type 配股票码（不混用 asset_type）
    out = resolve_ts_codes(["600000", "000001"], "STOCK", str(db))
    assert out == ["600000.SH", "000001.SZ"]


def test_resolve_missing_etf_table_all_fallback(tmp_path):
    """main_db 不含 etf_basic → ETF 全量前缀 fallback，不抛。"""
    db = tmp_path / "quantstudio.db"
    with duckdb.connect(str(db)) as conn:
        conn.execute("CREATE TABLE other(x VARCHAR)")
    out = resolve_ts_codes(["510300", "159915"], "ETF", str(db))
    assert out == ["510300.SH", "159915.SZ"]


def test_resolve_nonexistent_db_all_fallback(tmp_path):
    """main_db 文件不存在 → 全量前缀 fallback，不抛、不创建文件。"""
    fake_db = str(tmp_path / "nonexistent.db")
    # ETF asset_type 配 ETF 码（不混用）
    out = resolve_ts_codes(["510300", "159915"], "ETF", fake_db)
    assert out == ["510300.SH", "159915.SZ"]
    # 不创建文件
    import os
    assert not os.path.exists(fake_db)


def test_resolve_empty_input_returns_empty(main_db_with_metadata):
    assert resolve_ts_codes([], "STOCK", main_db_with_metadata) == []


# ---------------------------------------------------------------------------
# 5. 未知前缀的防御性 fallback → 聚合 WARNING（可观测性）
# ---------------------------------------------------------------------------
def test_resolve_unknown_etf_prefix_warns(tmp_path, caplog):
    """ETF 未知首位（260000，首位 2 不属于 ETF 合法 5/1）→ 防御性 fallback .BJ + WARNING。

    普通元数据 miss（合法首位但元数据无记录）保持 INFO；只有未知前缀才 WARNING。
    """
    import logging
    db = tmp_path / "quantstudio.db"
    with duckdb.connect(str(db)) as conn:
        conn.execute("CREATE TABLE etf_basic (code VARCHAR, ts_code VARCHAR)")
        # 不插入 260000（元数据 miss）——但首位 2 是未知 ETF 前缀
    with caplog.at_level(logging.WARNING, logger="quantstudio.pipeline.qfq_maintenance"):
        out = resolve_ts_codes(["260000"], "ETF", str(db))
    # 输出仍为 .BJ（不丢码，防御性 fallback）
    assert out == ["260000.BJ"]
    # WARNING（含资产类型、未知前缀、defensive/fallback 语义、代码样本）
    warn_text = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "ETF" in warn_text
    assert "260000" in warn_text
    assert "未知前缀" in warn_text or "fallback" in warn_text.lower() or "defensive" in warn_text.lower()


def test_resolve_unknown_stock_prefix_warns(tmp_path, caplog):
    """STOCK 未知首位（290000，首位 9 不属于 STOCK 合法 6/0/3/4/8）→ 防御性 .BJ + WARNING。"""
    import logging
    db = tmp_path / "quantstudio.db"
    with duckdb.connect(str(db)) as conn:
        conn.execute("CREATE TABLE stock_basic (code VARCHAR, ts_code VARCHAR)")
    with caplog.at_level(logging.WARNING, logger="quantstudio.pipeline.qfq_maintenance"):
        out = resolve_ts_codes(["290000"], "STOCK", str(db))
    assert out == ["290000.BJ"]
    warn_text = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "STOCK" in warn_text and "290000" in warn_text


def test_resolve_legal_prefix_miss_no_warning_only_info(main_db_with_metadata, caplog):
    """合法首位但元数据 miss → 仅 INFO，不 WARNING（603999 首位 6 合法，元数据无记录）。"""
    import logging
    with caplog.at_level(logging.INFO, logger="quantstudio.pipeline.qfq_maintenance"):
        out = resolve_ts_codes(["603999"], "STOCK", main_db_with_metadata)
    assert out == ["603999.SH"]  # 合法前缀 fallback，不是 .BJ
    # 不应有未知前缀 WARNING
    warn_text = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "未知前缀" not in warn_text
    # 但应有 miss INFO
    info_text = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.INFO)
    assert "miss" in info_text.lower() or "603999" in info_text
