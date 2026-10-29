# -*- coding: utf-8 -*-
"""PT-01a 覆盖链主键保护测试（spec 002-duckdb-1.5-upgrade · FR-021/FR-022/FR-025）。

背景：passthrough 全量覆盖旧语义为「建临时表(CTAS，不继承约束)→灌数→DROP 原表→RENAME」，
**每次覆盖都会静默清空目标表主键**（1→0，不报错不告警）。读数见
`docs/evidence/pt01-f2-truncate-20261029.md` 四组 + 段A 十二组。

覆盖点：
  P1 F2 主路径（列集一致）⇒ TRUNCATE + INSERT：主键保持、冲突探测仍有效
  P2 旧语义对照（复刻 CTAS+DROP+RENAME）⇒ 主键 1→0（证明缺陷客观存在）
  P3 双写逐位一致：旧路径库 vs 新路径库 —— 列集 / 行数 / 内容哈希全等
  P4 列集不一致 ⇒ F1 重建：按 df 列集重建 + 显式重建同构主键
  P5 指纹校验表 ⇒ 拒绝列集变化（铁律 24：加列属契约变更，须走六件套）
  P6 lint 前瞻守卫：passthrough 指向带主键的表 ⇒ ERROR；现状任务集零告警
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from quantstudio.pipeline.config_lint import lint_configs
from quantstudio.pipeline.writers import DuckDBWriter

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- 工具

def _make_writer(tmp_path, name="t.duckdb"):
    return DuckDBWriter({"type": "duckdb", "path": str(tmp_path / name)})


def _pk_table(path, table="demo", cols=("code", "time", "v")):
    con = duckdb.connect(str(path))
    con.execute(
        f'CREATE TABLE "{table}" (code VARCHAR, time BIGINT, v DOUBLE, '
        f'PRIMARY KEY(code, time))')
    con.close()


def _pk_count(path, table="demo"):
    con = duckdb.connect(str(path))
    n = con.execute(
        "SELECT COUNT(*) FROM duckdb_constraints() "
        "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'", [table]).fetchone()[0]
    con.close()
    return n


def _cols(path, table="demo"):
    con = duckdb.connect(str(path))
    cols = [r[0] for r in con.execute(
        "SELECT column_name FROM duckdb_columns() WHERE table_name = ? "
        "ORDER BY column_index", [table]).fetchall()]
    con.close()
    return cols


def _fingerprint(path, table="demo"):
    """内容指纹：按全列排序后取 sha256（列集/行数/内容一次算清）。"""
    con = duckdb.connect(str(path))
    rows = con.execute(f'SELECT * FROM "{table}" ORDER BY ALL').fetchall()
    cols = [r[0] for r in con.execute(
        "SELECT column_name FROM duckdb_columns() WHERE table_name = ? "
        "ORDER BY column_index", [table]).fetchall()]
    con.close()
    payload = json.dumps([cols, [list(map(str, r)) for r in rows]],
                         ensure_ascii=False, sort_keys=True)
    return {"columns": cols, "rows": len(rows), "sha256": hashlib.sha256(payload.encode()).hexdigest()}


def _df():
    return pd.DataFrame({"code": ["A", "B", "A", "C"],
                         "time": [1, 2, 3, 4],
                         "v": [1.0, 2.0, 3.0, 4.0]})


# ---------------------------------------------------------------- P1 / P3 / P4

def test_p1_f2_preserves_pk_and_conflict_detection(tmp_path):
    """P1：F2 覆盖后主键保持为 1，且 ON CONFLICT 冲突探测仍有效。"""
    db = tmp_path / "t.duckdb"
    _pk_table(db)
    w = _make_writer(tmp_path)
    w._write_passthrough(_df(), "demo", "b1")
    assert _pk_count(db) == 1, "覆盖后主键必须保持（F2 保留表对象）"
    assert _cols(db) == ["code", "time", "v"]
    assert _fingerprint(db)["rows"] == 4
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO demo VALUES ('A', 1, 99.0) "
                "ON CONFLICT (code, time) DO UPDATE SET v = EXCLUDED.v")
    assert con.execute("SELECT COUNT(*) FROM demo").fetchone()[0] == 4, "重复键须被冲突探测拦下"
    con.close()


def test_p2_legacy_semantics_would_lose_pk(tmp_path):
    """P2：对照——旧语义（CTAS+DROP+RENAME）确实会把主键清成 0（缺陷客观存在）。"""
    db = tmp_path / "old.duckdb"
    _pk_table(db)
    con = duckdb.connect(str(db))
    con.register("_src", _df())
    con.execute('CREATE TABLE "_pt_tmp_demo" AS SELECT * FROM "demo" LIMIT 0')
    con.execute('INSERT INTO "_pt_tmp_demo" SELECT * FROM _src')
    con.execute('DROP TABLE IF EXISTS "demo"')
    con.execute('ALTER TABLE "_pt_tmp_demo" RENAME TO "demo"')
    con.close()
    assert _pk_count(db) == 0, "旧语义对照：主键应为 0（若为 1 则对照失真）"


def test_p3_dual_write_bitwise_identical(tmp_path):
    """P3：同一 df 经旧路径与新路径写入，列集/行数/内容哈希逐位一致。"""
    df = _df()
    old_db = tmp_path / "old.duckdb"
    new_db = tmp_path / "new.duckdb"
    for db in (old_db, new_db):
        _pk_table(db)
    # 旧路径
    con = duckdb.connect(str(old_db))
    con.register("_src", df)
    con.execute('CREATE TABLE "_pt_tmp_demo" AS SELECT * FROM "demo" LIMIT 0')
    con.execute('INSERT INTO "_pt_tmp_demo" SELECT * FROM _src')
    con.execute('DROP TABLE IF EXISTS "demo"')
    con.execute('ALTER TABLE "_pt_tmp_demo" RENAME TO "demo"')
    con.close()
    # 新路径（真实 writer）
    w = _make_writer(tmp_path, name="new.duckdb")
    w._write_passthrough(df, "demo", "b1")
    old_fp = _fingerprint(old_db)
    new_fp = _fingerprint(new_db)
    assert old_fp["columns"] == new_fp["columns"]
    assert old_fp["rows"] == new_fp["rows"] == 4
    assert old_fp["sha256"] == new_fp["sha256"], "内容哈希必须逐位一致"


def test_p4_column_mismatch_falls_back_to_f1_with_pk(tmp_path):
    """P4：列集不一致 ⇒ F1 重建（按 df 列集重建 + 显式重建同构主键）。"""
    db = tmp_path / "t.duckdb"
    _pk_table(db)
    w = _make_writer(tmp_path)
    df = _df()
    df["extra"] = [9.0, 9.0, 9.0, 9.0]          # 上游新增列 ⇒ 列集不一致
    w._write_passthrough(df, "demo", "b1")
    assert _cols(db) == ["code", "time", "v", "extra"], "应按 df 列集重建"
    assert _pk_count(db) == 1, "F1 重建必须显式恢复同构主键"
    assert _fingerprint(db)["rows"] == 4


def test_p5_fingerprint_table_rejects_column_change(tmp_path, monkeypatch):
    """P5：命中指纹校验表 ⇒ 拒绝列集变化（铁律 24：加列属契约变更）。"""
    db = tmp_path / "t.duckdb"
    _pk_table(db)
    w = _make_writer(tmp_path)
    monkeypatch.setattr(DuckDBWriter, "_pt_fingerprint_tables",
                        staticmethod(lambda: {"demo"}))
    df = _df()
    df["extra"] = [1.0, 1.0, 1.0, 1.0]
    with pytest.raises(RuntimeError) as ei:
        w._write_passthrough(df, "demo", "b1")
    assert "契约变更" in str(ei.value)
    # 拒绝后原表与主键应完好（未被半途改写）
    assert _pk_count(db) == 1
    assert _cols(db) == ["code", "time", "v"]


# ---------------------------------------------------------------- P6 lint 前瞻守卫

def _load_configs(profile="mcp_only"):
    base = REPO / "config" / "profiles" / profile
    def _j(name):
        with open(base / name, encoding="utf-8") as f:
            return json.load(f)
    return (_j("data_config.json"), _j("sources_config.json"),
            _j("collector_tasks.json"), _j("alignment_rules.json"))


def test_p6_lint_blocks_passthrough_to_pk_table():
    """P6 负向：passthrough 任务指向带主键的表 ⇒ lint 报 ERROR。"""
    data_cfg, sources_cfg, tasks_cfg, align_rules = _load_configs()
    tasks = tasks_cfg["tasks"]
    schemas = align_rules.get("schemas", {})
    pk_tables = [t for t, s in schemas.items() if (s or {}).get("primary_key")]
    assert pk_tables, "需要至少一张带主键的 schema 表做负向用例"
    target = pk_tables[0]
    tasks.append({"name": "__pt_negative_case__", "table": target,
                   "passthrough": True, "enabled": False,
                   "source": "tushare", "codes": ["ALL"]})
    errors, _warnings = lint_configs(data_cfg, sources_cfg, tasks_cfg, align_rules)
    hit = [e for e in errors if "__pt_negative_case__" in e and target in e]
    assert hit, "指向带主键表的 passthrough 任务必须被拦下"


def test_p6_lint_current_passthrough_tasks_zero_errors():
    """P6 现状：真实配置的 passthrough 任务集零告警（交集为空 ⇒ 前瞻守卫）。"""
    data_cfg, sources_cfg, tasks_cfg, align_rules = _load_configs()
    errors, _warnings = lint_configs(data_cfg, sources_cfg, tasks_cfg, align_rules)
    pt_errors = [e for e in errors if "passthrough" in e]
    assert not pt_errors, f"现状 passthrough 任务不应触发新守卫：{pt_errors}"
    n_pt = sum(1 for t in tasks_cfg["tasks"] if t.get("passthrough"))
    assert n_pt > 0, "配置中应存在 passthrough 任务"
