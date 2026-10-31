"""方案②：factor_monotonicity 缓存 F1–F8 机检。

方案：docs/audit-global-section-cache-design.md（②审通过 + 两条件已入案）
- 键 = aux 文件 (mtime_ns,size) + WAL (mtime_ns,size) + 因子表集合（文件指纹键）
- 与 A2b 的「水位键」分立：见方案 §二·五 判定口诀
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


def _mk_aux(tmp: Path, *, bad: bool = True) -> Path:
    """合成 aux：adj_factor / fund_adj（含一处非单调）。"""
    db = tmp / "qfq_aux.db"
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
    con.execute("CREATE TABLE fund_adj (code TEXT, time INTEGER, adj_factor REAL)")
    rows = [("C1", 1000, 1.0), ("C1", 2000, 1.1)]
    if bad:
        rows.append(("C1", 3000, 0.9))          # 回落 ⇒ 非单调
    con.executemany("INSERT INTO adj_factor VALUES (?,?,?)", rows)
    con.executemany("INSERT INTO fund_adj VALUES (?,?,?)", rows)
    con.commit()
    con.close()
    return db


def _auditor(aux: Path, tables=("etf_daily",), cache_dir=None):
    if cache_dir is not None:
        os.environ["QS_AUDIT_FM_CACHE_DIR"] = str(cache_dir)
    return DataQualityAuditor("x.db", {}, qfq_aux_override=str(aux),
                              tables=list(tables) if tables is not None else None)


def _run(aud, aux):
    rep = QualityReport()
    db = duckdb.connect()          # 只为满足 _resolve_aux_path 的 conn 参数
    db.execute("CREATE TABLE IF NOT EXISTS _t (a INT)")
    try:
        t0 = time.perf_counter()
        aud._audit_factor_monotonicity(db, rep)
        return rep, time.perf_counter() - t0
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("QS_AUDIT_FM_CACHE", "QS_AUDIT_FM_CACHE_TTL_S", "QS_AUDIT_FM_CACHE_DIR"):
        monkeypatch.delenv(k, raising=False)
    yield


# ---------------------------------------------------------------- F1 键
def test_f1_key_stable(tmp_path, monkeypatch):
    aux = _mk_aux(tmp_path)
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(tmp_path / "c"))
    k1 = DataQualityAuditor._fm_cache_key(aux)
    k2 = DataQualityAuditor._fm_cache_key(aux)
    assert k1 == k2 and len(k1) == 32


def test_f1_key_changes_on_main_mtime(tmp_path, monkeypatch):
    aux = _mk_aux(tmp_path)
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(tmp_path / "c"))
    k1 = DataQualityAuditor._fm_cache_key(aux)
    os.utime(aux, None)                    # 改 mtime
    time.sleep(0.01)
    os.utime(aux, (time.time() + 10, time.time() + 10))
    k2 = DataQualityAuditor._fm_cache_key(aux)
    assert k1 != k2, "主库 mtime 变化必须使键变化"


def test_f1_key_changes_on_wal(tmp_path, monkeypatch):
    """写入落 WAL（主库 mtime 可能不变）⇒ 键必须变。"""
    aux = _mk_aux(tmp_path)
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(tmp_path / "c"))
    wal = Path(str(aux) + "-wal")
    if not wal.exists():
        wal.write_bytes(b"")              # 造出 -wal 以测键成分
    k1 = DataQualityAuditor._fm_cache_key(aux)
    wal.write_bytes(b"x" * 4096)
    k2 = DataQualityAuditor._fm_cache_key(aux)
    assert k1 != k2, "WAL 变化必须使键变化（否则脏命中）"


def test_f1_key_cost_below_5ms(tmp_path, monkeypatch):
    aux = _mk_aux(tmp_path)
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(tmp_path / "c"))
    t0 = time.perf_counter()
    for _ in range(5):
        DataQualityAuditor._fm_cache_key(aux)
    per = (time.perf_counter() - t0) / 5
    assert per < 0.005, f"键成本过高: {per*1000:.2f}ms"


def test_f1_key_ignores_unrelated_file(tmp_path, monkeypatch):
    aux = _mk_aux(tmp_path)
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(tmp_path / "c"))
    k1 = DataQualityAuditor._fm_cache_key(aux)
    (tmp_path / "batch_audit.db").write_bytes(b"noise")
    k2 = DataQualityAuditor._fm_cache_key(aux)
    assert k1 == k2, "无关文件变化不得使键失效"


# ---------------------------------------------------------------- F2/F3 冷热
def test_f2_cold_no_cache_entry(tmp_path, monkeypatch):
    aux = _mk_aux(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(cdir))
    aud = _auditor(aux, cache_dir=cdir)
    rep, _ = _run(aud, aux)
    assert "fm_cached" not in [i.check for i in rep.issues]
    assert "FactorMonotonicity" in [i.check for i in rep.issues], "合成数据应检出非单调"


def test_f3_warm_hits_and_identical(tmp_path, monkeypatch):
    """命中判据：`fm_cached` 出现 + 其余条目逐位一致。

    **不验耗时**：合成库仅 3 行，命中（读文件+三校验）与直算同量级，
    耗时差异只在真实库（4364 万行）才有意义 ⇒ 耗时验收放在真实验收（§5.2）。
    """
    aux = _mk_aux(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(cdir))
    aud = _auditor(aux, cache_dir=cdir)
    rep1, _ = _run(aud, aux)
    rep2, _ = _run(aud, aux)
    assert "fm_cached" in [i.check for i in rep2.issues]
    base1 = sorted((i.check, i.table, i.count, i.severity) for i in rep1.issues)
    base2 = sorted((i.check, i.table, i.count, i.severity)
                   for i in rep2.issues if i.check != "fm_cached")
    assert base1 == base2, "除 fm_cached 外条目必须逐位相同"


# ---------------------------------------------------------------- F4 full 域
def test_f4_full_scope_no_cache(tmp_path, monkeypatch):
    aux = _mk_aux(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(cdir))
    aud = _auditor(aux, tables=None, cache_dir=cdir)
    rep, _ = _run(aud, aux)
    assert "fm_cached" not in [i.check for i in rep.issues]
    assert not list(cdir.glob("*.json")), "full 域不得写缓存"


# ---------------------------------------------------------------- F5 三校验 + 投毒
def test_f5_checksum_tamper_is_miss(tmp_path, monkeypatch):
    aux = _mk_aux(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(cdir))
    aud = _auditor(aux, cache_dir=cdir)
    _run(aud, aux)
    f = next(cdir.glob("*.json"))
    doc = json.loads(f.read_text(encoding="utf-8"))
    if not doc["payload"].get("issues"):
        doc["payload"]["issues"] = [{"check": "FactorMonotonicity", "table": "adj_factor",
                                     "count": 999, "severity": "warning", "detail": "x"}]
    else:
        for iss in doc["payload"]["issues"]:
            iss["count"] = 999
    f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")   # checksum 未更新
    rep2, _ = _run(aud, aux)
    assert "fm_cached" not in [i.check for i in rep2.issues], "checksum 不符必须 miss"
    assert not [i for i in rep2.issues if i.count == 999]


def test_f5_key_mismatch_is_miss(tmp_path, monkeypatch):
    """文件名与内容 key 漂移（手工挪错）⇒ miss。"""
    aux = _mk_aux(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(cdir))
    aud = _auditor(aux, cache_dir=cdir)
    _run(aud, aux)
    f = next(cdir.glob("*.json"))
    doc = json.loads(f.read_text(encoding="utf-8"))
    doc["key"] = "0" * 32
    doc["payload"]["key"] = "0" * 32
    doc["checksum"] = hashlib.sha256(
        json.dumps(doc["payload"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rep2, _ = _run(aud, aux)
    assert "fm_cached" not in [i.check for i in rep2.issues], "value.key 不符必须 miss"


def test_f5_truncated_file_is_miss(tmp_path, monkeypatch):
    aux = _mk_aux(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(cdir))
    aud = _auditor(aux, cache_dir=cdir)
    _run(aud, aux)
    next(cdir.glob("*.json")).write_text("{bad json", encoding="utf-8")
    rep2, _ = _run(aud, aux)          # 不得崩
    assert "fm_cached" not in [i.check for i in rep2.issues]


# ---------------------------------------------------------------- F6 回退
def test_f6_disable_switch(tmp_path, monkeypatch):
    aux = _mk_aux(tmp_path)
    cdir = tmp_path / "c"
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(cdir))
    monkeypatch.setenv("QS_AUDIT_FM_CACHE", "0")
    aud = _auditor(aux, cache_dir=cdir)
    _run(aud, aux)
    rep2, _ = _run(aud, aux)
    assert "fm_cached" not in [i.check for i in rep2.issues]
    assert not list(cdir.glob("*.json")), "禁用时不得写缓存"


# ---------------------------------------------------------------- F7 降级
def test_f7_unwritable_dir_degrades(tmp_path, monkeypatch):
    aux = _mk_aux(tmp_path)
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(blocker))
    aud = _auditor(aux, cache_dir=blocker)
    rep, _ = _run(aud, aux)           # 不得抛穿
    assert rep is not None


# ---------------------------------------------------------------- F8 边界
def test_f8_aux_without_factor_tables(tmp_path, monkeypatch):
    db = tmp_path / "empty_aux.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE other (a INT)")
    con.commit()
    con.close()
    monkeypatch.setenv("QS_AUDIT_FM_CACHE_DIR", str(tmp_path / "c"))
    aud = _auditor(db, cache_dir=tmp_path / "c")
    rep, _ = _run(aud, db)            # 无 adj_factor/fund_adj ⇒ 不报错
    assert not [i for i in rep.issues if i.check == "FactorMonotonicity"]
