"""A2b（2026-10-31）drift 结果缓存 —— B1–B8 机检。

方案：docs/audit-drift-cache-design.md（②审通过，含补充条件：三项校验）
- 缓存仅 task 域启用；键 = 候选集 + 分钟表水位 + 除权表水位；
- 跨进程落盘 + 原子写；schema/checksum/value.key 三重校验。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import duckdb
import pytest

from quantstudio.pipeline.quality_audit import DataQualityAuditor, QualityReport


def _mk_db(tmp: Path, *, n_codes: int = 5, watermark: str = "2026-10-30"):
    """合成主库（含 source_watermark / stock_minutes / stock_dividend）+ aux 因子库。"""
    aux = tmp / "qfq_aux.db"
    now = int(time.time() * 1000)
    con = sqlite3.connect(str(aux))
    con.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
    con.executemany("INSERT INTO adj_factor VALUES (?,?,?)",
                    [(f"S{i:04d}", now - 60 * 86400000, 1.0) for i in range(n_codes)]
                    + [(f"S{i:04d}", now - 20 * 86400000, 1.1) for i in range(n_codes)])
    con.commit()
    con.close()

    db = duckdb.connect()
    db.execute("CREATE TABLE stock_minutes (code VARCHAR, time BIGINT, "
               "close DOUBLE, close_front DOUBLE)")
    db.execute("CREATE TABLE stock_dividend (code VARCHAR, ex_date BIGINT)")
    db.execute("CREATE TABLE source_watermark (source VARCHAR, freq VARCHAR, last_date VARCHAR)")
    bars = [(f"S{i:04d}", now - d * 86400000, 10.0, 10.0)
            for i in range(n_codes) for d in range(25)]
    db.executemany("INSERT INTO stock_minutes VALUES (?,?,?,?)", bars)
    db.executemany("INSERT INTO stock_dividend VALUES (?,?)",
                   [(f"S{i:04d}", now - 10 * 86400000) for i in range(n_codes)])
    db.execute("INSERT INTO source_watermark VALUES ('mcp','1min',?)", [watermark])
    return db, aux


def _auditor(aux, tables=("stock_minutes",), cache_dir=None):
    if cache_dir is not None:
        os.environ["QS_AUDIT_DRIFT_CACHE_DIR"] = str(cache_dir)
    return DataQualityAuditor("x.db", {}, qfq_aux_override=str(aux),
                              tables=list(tables) if tables is not None else None)


def _run(db, aud):
    rep = QualityReport()
    t0 = time.perf_counter()
    aud._audit_minute_anchor_drift(db, rep, {"stock_minutes", "stock_dividend"})
    return rep, time.perf_counter() - t0


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("QS_AUDIT_DRIFT_CACHE", "QS_AUDIT_DRIFT_CACHE_TTL_S",
              "QS_AUDIT_DRIFT_CACHE_DIR", "QS_AUDIT_DRIFT_MAX_CODES"):
        monkeypatch.delenv(k, raising=False)
    yield


# ---------------------------------------------------------------- B1 键
def test_b1_key_stable_for_same_state(tmp_path, monkeypatch):
    db, aux = _mk_db(tmp_path)
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(tmp_path / "c"))
    aud = _auditor(aux, cache_dir=tmp_path / "c")
    k1 = aud._drift_cache_key(db)
    k2 = aud._drift_cache_key(db)
    assert k1 == k2 and len(k1) == 32
    db.close()


def test_b1_key_changes_on_watermark_advance(tmp_path, monkeypatch):
    db, aux = _mk_db(tmp_path)
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(tmp_path / "c"))
    aud = _auditor(aux, cache_dir=tmp_path / "c")
    k1 = aud._drift_cache_key(db)
    db.execute("UPDATE source_watermark SET last_date='2026-10-31'")
    k2 = aud._drift_cache_key(db)
    assert k1 != k2, "分钟表水位推进后键必须变化（否则脏命中）"
    db.close()


def test_b1_key_unchanged_by_unrelated_field(tmp_path, monkeypatch):
    """负向：无关数据变化不得使键失效（防过度失效）。"""
    db, aux = _mk_db(tmp_path)
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(tmp_path / "c"))
    aud = _auditor(aux, cache_dir=tmp_path / "c")
    k1 = aud._drift_cache_key(db)
    db.execute("UPDATE stock_minutes SET close = close + 1")   # 数据变了但水位没动
    k2 = aud._drift_cache_key(db)
    # 键只看水位与候选集：分钟表内容变化但水位未推 ⇒ 键不变（契约：水位是唯一权威）
    assert k1 == k2
    db.close()


# ---------------------------------------------------------------- B2 命中
def test_b2_second_run_hits_cache(tmp_path, monkeypatch):
    db, aux = _mk_db(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(cdir))
    aud = _auditor(aux, cache_dir=cdir)
    rep1, t1 = _run(db, aud)
    rep2, t2 = _run(db, aud)
    checks2 = [i.check for i in rep2.issues]
    assert "drift_cached" in checks2, "第二次必须命中缓存并留痕"
    assert t2 < max(0.05, t1), f"命中应显著更快: t1={t1:.3f}s t2={t2:.3f}s"
    # 结论逐位一致（除 drift_cached 外）
    base1 = sorted((i.check, i.table, i.count, i.severity) for i in rep1.issues)
    base2 = sorted((i.check, i.table, i.count, i.severity)
                   for i in rep2.issues if i.check != "drift_cached")
    assert base1 == base2
    db.close()


def test_b2_no_cache_entry_when_cold(tmp_path, monkeypatch):
    """负向：首次（无缓存）不得出现 drift_cached。"""
    db, aux = _mk_db(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(cdir))
    aud = _auditor(aux, cache_dir=cdir)
    rep, _ = _run(db, aud)
    assert "drift_cached" not in [i.check for i in rep.issues]
    db.close()


# ---------------------------------------------------------------- B3 原子写 + 损坏
def test_b3_cache_file_is_valid_json_with_checksum(tmp_path, monkeypatch):
    db, aux = _mk_db(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(cdir))
    aud = _auditor(aux, cache_dir=cdir)
    _run(db, aud)
    files = list(cdir.glob("*.json"))
    assert len(files) == 1
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    expect = hashlib.sha256(json.dumps(doc["payload"], sort_keys=True,
                                       ensure_ascii=False).encode()).hexdigest()
    assert doc["checksum"] == expect
    assert doc["schema"] == 1 and doc["key"] == doc["payload"]["key"]
    db.close()


def test_b3_truncated_file_is_treated_as_miss(tmp_path, monkeypatch):
    db, aux = _mk_db(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(cdir))
    aud = _auditor(aux, cache_dir=cdir)
    _run(db, aud)
    f = next(cdir.glob("*.json"))
    f.write_text('{"schema":1,"payload":{', encoding="utf-8")
    rep2, _ = _run(db, aud)              # 不得崩溃
    assert "drift_cached" not in [i.check for i in rep2.issues]
    db.close()


# ---------------------------------------------------------------- B4 full 域不缓存
def test_b4_full_scope_does_not_cache(tmp_path, monkeypatch):
    db, aux = _mk_db(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(cdir))
    aud = _auditor(aux, tables=None, cache_dir=cdir)   # full 域
    rep, _ = _run(db, aud)
    assert "drift_cached" not in [i.check for i in rep.issues]
    assert not list(cdir.glob("*.json")), "full 域不得写缓存"
    db.close()


# ---------------------------------------------------------------- B5 投毒
def test_b5_payload_tamper_without_checksum_is_corrupt(tmp_path, monkeypatch):
    db, aux = _mk_db(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(cdir))
    aud = _auditor(aux, cache_dir=cdir)
    _run(db, aud)
    f = next(cdir.glob("*.json"))
    doc = json.loads(f.read_text(encoding="utf-8"))
    # 合成数据无漂移 ⇒ issues 可能为空；为空时手动植入一条伪造 issue。
    # 关键：**不更新 checksum** ⇒ 必须被判 corrupt ⇒ miss。
    if not doc["payload"].get("issues"):
        doc["payload"]["issues"] = [{"check": "AdjustmentAnchorDrift",
                                     "table": "stock_minutes", "count": 999,
                                     "severity": "error", "detail": "tampered"}]
    else:
        for iss in doc["payload"]["issues"]:
            iss["count"] = 999
    f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rep2, _ = _run(db, aud)
    assert "drift_cached" not in [i.check for i in rep2.issues], "checksum 不符必须视为 miss"
    assert not [i for i in rep2.issues if i.count == 999]
    db.close()


def test_b5_both_tampered_hits_by_contract(tmp_path, monkeypatch):
    """契约边界：payload 与 checksum 同时伪造 ⇒ 允许命中（checksum 防意外不防恶意）。"""
    db, aux = _mk_db(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(cdir))
    aud = _auditor(aux, cache_dir=cdir)
    _run(db, aud)
    f = next(cdir.glob("*.json"))
    doc = json.loads(f.read_text(encoding="utf-8"))
    doc["payload"]["issues"] = [{"check": "AdjustmentAnchorDrift", "table": "stock_minutes",
                                 "count": 777, "severity": "warning", "detail": "forged"}]
    doc["checksum"] = hashlib.sha256(json.dumps(doc["payload"], sort_keys=True,
                                                ensure_ascii=False).encode()).hexdigest()
    f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rep2, _ = _run(db, aud)
    assert "drift_cached" in [i.check for i in rep2.issues]
    assert [i.count for i in rep2.issues if i.check == "AdjustmentAnchorDrift"] == [777]
    db.close()


# ---------------------------------------------------------------- B6 TTL
def test_b6_ttl_expiry_causes_miss(tmp_path, monkeypatch):
    db, aux = _mk_db(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(cdir))
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_TTL_S", "0.2")
    aud = _auditor(aux, cache_dir=cdir)
    _run(db, aud)
    time.sleep(0.35)
    rep2, _ = _run(db, aud)
    assert "drift_cached" not in [i.check for i in rep2.issues]
    db.close()


def test_b6_ttl_zero_means_no_time_expiry(tmp_path, monkeypatch):
    db, aux = _mk_db(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(cdir))
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_TTL_S", "0")
    aud = _auditor(aux, cache_dir=cdir)
    _run(db, aud)
    time.sleep(0.15)
    rep2, _ = _run(db, aud)
    assert "drift_cached" in [i.check for i in rep2.issues]
    db.close()


# ---------------------------------------------------------------- B7 回退开关
def test_b7_disable_switch_no_read_no_write(tmp_path, monkeypatch):
    db, aux = _mk_db(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(cdir))
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE", "0")
    aud = _auditor(aux, cache_dir=cdir)
    rep1, _ = _run(db, aud)
    rep2, _ = _run(db, aud)
    assert "drift_cached" not in [i.check for i in rep2.issues]
    assert not list(cdir.glob("*.json")), "禁用时不得写缓存"
    db.close()


# ---------------------------------------------------------------- B8 逃逸不崩
def test_b8_unwritable_cache_dir_degrades_silently(tmp_path, monkeypatch):
    db, aux = _mk_db(tmp_path)
    bad = tmp_path / "cache_is_a_file"
    bad.write_text("x", encoding="utf-8")        # 目录位置被普通文件占用
    monkeypatch.setenv("QS_AUDIT_DRIFT_CACHE_DIR", str(bad))
    aud = _auditor(aux, cache_dir=bad)
    rep, _ = _run(db, aud)                       # 不得抛异常
    assert rep is not None
    db.close()
