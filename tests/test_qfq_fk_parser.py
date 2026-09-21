r"""D1 契约测试：FK constraint_text schema 限定名解析容错（2026-09-21）。

红态依据：旧正则 r"REFERENCES\s+([A-Za-z_][\w]*)\s*\(" 遇 DuckDB 渲染的
"REFERENCES main.tab(col)" 匹配失败 → 静默 continue → FK 漏报 → 安全闸误判
partial_or_mixed（daemon 每周期 writer init 被拒）。修前后形态必红。

三条覆盖（总调度 2026-09-21 批准）：
  ① 双形态解析（带/不带 main. 前缀）
  ② 多列 FK
  ③ 无 FK / 畸形文本（不崩、不误报；畸形走 warning 不静默）
"""
from __future__ import annotations

import pytest

from quantstudio.pipeline.qfq_schema_contracts import _table_foreign_keys


class FakeConn:
    """替身仅止于 DB 连接层：回放指定 constraint_text 行。"""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        class R:
            def __init__(self, rows): self._rows = rows
            def fetchall(self): return self._rows
        return R(self._rows)


def test_parse_with_schema_qualifier():
    """① 带 main. 前缀：必须解析出**裸表名**（group(1) 口径不变）。"""
    fks = _table_foreign_keys(
        FakeConn([(["cutover_id"], "FOREIGN KEY (cutover_id) REFERENCES main.qfq_source_cutover(cutover_id)")]),
        "qfq_active_cutover")
    assert len(fks) == 1, "带 main. 前缀的 FK 被漏报（红态）"
    assert fks[0]["referenced_table"] == "qfq_source_cutover", "必须剥掉 schema 前缀"
    assert fks[0]["referenced_columns"] == ["cutover_id"]
    assert fks[0]["columns"] == ["cutover_id"]


def test_parse_without_schema_qualifier():
    """① 不带前缀（既有形态）不得回归。"""
    fks = _table_foreign_keys(
        FakeConn([(["cutover_id"], "FOREIGN KEY (cutover_id) REFERENCES qfq_source_cutover(cutover_id)")]),
        "qfq_active_cutover")
    assert len(fks) == 1 and fks[0]["referenced_table"] == "qfq_source_cutover"


def test_parse_multi_column_fk():
    """② 多列 FK：columns / referenced_columns 均按序全取。"""
    fks = _table_foreign_keys(
        FakeConn([(["a", "b"], "FOREIGN KEY (a, b) REFERENCES main.tbl(x, y)")]), "t")
    assert len(fks) == 1
    assert fks[0]["columns"] == ["a", "b"]
    assert fks[0]["referenced_columns"] == ["x", "y"]


def test_no_fk_and_malformed_text():
    """③ 无 FK → 空；畸形文本 → 不崩、不误报（改走 warning，行为仍为跳过）。"""
    assert _table_foreign_keys(FakeConn([]), "t") == []
    assert _table_foreign_keys(FakeConn([(["c"], "FOREIGN KEY (c) GARBAGE")]), "t") == []


def test_negative_missing_fk_still_detected(tmp_path):
    """V4 负向：**真缺 FK** 时闸门仍须判否——防把容错扩成放行。

    构造 scratch 库：无 FK 的表 vs 期望有 FK 的指纹 → verify_fingerprint 必 False。
    """
    import duckdb
    from quantstudio.pipeline.qfq_schema_contracts import verify_fingerprint
    db = tmp_path / "scratch.db"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE qfq_source_cutover (cutover_id VARCHAR PRIMARY KEY)")
    con.execute("CREATE TABLE qfq_active_cutover (cutover_id VARCHAR PRIMARY KEY, activated_at TIMESTAMP)")
    fp = {"qfq_active_cutover": {
        "columns": [("cutover_id", "VARCHAR", True, None), ("activated_at", "TIMESTAMP", False, None)],
        "primary_key": ["cutover_id"], "unique": [],
        "foreign_keys": [{"columns": ["cutover_id"], "referenced_table": "qfq_source_cutover",
                          "referenced_columns": ["cutover_id"]}]}}
    assert verify_fingerprint(con, fp) is False, "真缺 FK 必须判否（容错不得变放行）"
    con.close()
