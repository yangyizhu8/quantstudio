"""quality_orchestrator v0.2 四缺陷回归钉（客户「李梓嘉不会章鱼杀」巡检案）。

方案：docs/case-liq-quality-orchestrator-four-defects-design.md
验证原则（用户裁定④）：**四缺陷各一条「构造故障 ⇒ 必显性」钉**。

D1 异常显性化：异常/未实现 ⇒ status=error，判定链不得给 PASS
D2 eps 判定契约化：空输出/崩溃/非契约退出 ⇒ error（**不得**被当成「检出」）
D3 规则库补齐：6 表规则带可机读阈值 + implemented 状态；未实现规则单列不计覆盖
D4 时机/通道：锁冲突 ⇒ error_kind=lock_conflict；写路径在 daemon 活跃期被拒
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MOD = ROOT / "scripts" / "quality_orchestrator.py"


@pytest.fixture(scope="module")
def qo():
    spec = importlib.util.spec_from_file_location("qo_under_test", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ===========================================================================
# D1 异常显性化 —— 「构造故障 ⇒ 必显性」
# ===========================================================================

def test_d1_internal_exception_becomes_error_not_pass(qo, monkeypatch):
    """构造内部异常（模拟 gap_heal 锁崩溃）⇒ status=error，且判定链不得 PASS。"""
    def boom(*a, **k):
        raise RuntimeError("模拟 gap_heal 锁崩溃")
    monkeypatch.setattr(qo, "_run_external", boom)
    r = qo.run_check("gap_heal")
    assert r["status"] == "error", r
    assert r["detected"] is False, "error 不得记成检出（那是另一类错误）"
    assert r["error_kind"] in ("internal_error", "tool_error", "lock_conflict"), r

    rep = json.loads(qo.generate_report([r]))
    assert rep["verdict"] == "FAIL", f"L1 规则 error ⇒ 必须 FAIL，实际 {rep['verdict']}"


def test_d1_non_l1_error_gives_warn_not_pass(qo, monkeypatch):
    """非 L1 规则 error ⇒ 至少 WARN（不得 PASS）。"""
    r = {"rule": "x_l2", "level": "L2", "detected": False, "status": "error",
         "error_kind": "tool_error", "details": "模拟"}
    rep = json.loads(qo.generate_report([r]))
    assert rep["verdict"] == "WARN", rep["verdict"]


def test_d1_all_ok_gives_pass(qo):
    """全 ok ⇒ PASS（回归钉：修复不得把正常态也判失败）。"""
    r = {"rule": "x_l1", "level": "L1", "detected": False, "status": "ok",
         "error_kind": None, "details": ""}
    rep = json.loads(qo.generate_report([r]))
    assert rep["verdict"] == "PASS", rep["verdict"]


def test_d1_unimplemented_rule_is_error_not_silent_ok(qo):
    """未实现规则 ⇒ error(not_implemented)，**不得**静默 ok（覆盖幻觉修复）。"""
    for rid in ("timestamp_normalize", "minute_front_rewrite"):
        r = qo.run_check(rid)
        assert r["status"] == "error", (rid, r)
        assert r["error_kind"] == "not_implemented", (rid, r)


# ===========================================================================
# D2 eps 判定契约化 —— 空输出不得被当成「检出」
# ===========================================================================

class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


@pytest.mark.parametrize("stdout,returncode,expect_status,expect_kind", [
    ("[EpsBackfill] check: gap=0 (OK（免疫闭环）)", 0, "ok", None),
    ("[EpsBackfill] check: gap=5 (GAP（回补未生效/源端变化）)", 1, "detected", None),
    ("", 2, "error", "parse_error"),                # 缺库（真实复现场景，无契约输出）
    ("", 0, "error", "parse_error"),                # 空输出/崩溃
    ("Traceback (most recent call last): ...", 1, "error", "parse_error"),
    ("[EpsBackfill] check: gap=0", 99, "error", "tool_error"),   # 异常退出码
    # 一致性硬校验（开发期自纠）：退出码与 gap 语义矛盾 ⇒ error
    ("[EpsBackfill] check: gap=0", 1, "error", "parse_error"),
    ("[EpsBackfill] check: gap=3", 0, "error", "parse_error"),
])
def test_d2_eps_contract_parsing(qo, monkeypatch, stdout, returncode,
                                 expect_status, expect_kind):
    monkeypatch.setattr(qo, "_run_external",
                        lambda *a, **k: (_FakeProc(stdout, "", returncode), None, ""))
    r = qo.run_check("eps_backfill")
    assert r["status"] == expect_status, (stdout, returncode, r)
    if expect_kind:
        assert r["error_kind"] == expect_kind, r
    # 核心反向钉：空输出**绝不能**被判成「检出」
    if stdout == "":
        assert r["detected"] is False, "空输出被当作检出 = v0.1 缺陷②原形"


def test_d2_no_reverse_containment_judgement_in_source(qo):
    """源码不得再现 v0.1 的反向包含判定（缺陷②原形）与字符串计数写法。"""
    src = MOD.read_text(encoding="utf-8")
    forbidden = 'not in output'
    assert forbidden not in src, "反向包含判定被重新引入（缺陷②原形）"
    assert "'gap=0' not in" not in src and '"gap=0" not in' not in src
    assert "gap=(\\d+)" in src, "应使用正则契约解析 gap=(\\d+)"
    assert "一致性硬校验" in src, "应保留退出码/gap 一致性校验"


def test_d2_lock_conflict_in_stderr_is_error_not_detected(qo, monkeypatch):
    """stderr 含锁冲突串族 ⇒ error(lock_conflict)，不得计入检出。"""
    monkeypatch.setattr(qo, "_run_external",
                        lambda *a, **k: (_FakeProc("", "Conflicting lock is held",
                                                   1), None, ""))
    r = qo.run_check("eps_backfill")
    assert r["status"] == "error" and r["error_kind"] == "lock_conflict", r
    assert r["detected"] is False


# ===========================================================================
# D3 规则库补齐
# ===========================================================================

def test_d3_high_risk_rules_exist_with_machine_readable_thresholds(qo):
    """6 表规则存在，且各带可机读阈值（dates_min 取自权威口径 = 3）。"""
    ref = qo._HIGH_RISK_REF
    assert "verify_v2_cloud_parity" in ref and "HIGH_RISK_DATES" in ref, ref
    for t in qo.HIGH_RISK_TABLES:
        rid = f"rowcount_watermark.{t['table']}"
        assert rid in qo.QUALITY_RULES, rid
        thr = qo.QUALITY_RULES[rid]["threshold"]
        assert thr["dates_min"] == 3, (rid, thr)      # 权威口径，非拍数
        assert "row_delta_tol_pct" in thr and "watermark_max_lag_days" in thr
        assert thr.get("_source"), "阈值须标明来源"


def test_d3_every_rule_has_threshold_field(qo):
    """v0.1 缺位：规则库须有可机读阈值字段（允许空 dict，但字段必须在）。"""
    missing = [rid for rid, r in qo.QUALITY_RULES.items() if "threshold" not in r]
    assert not missing, f"缺 threshold 字段: {missing}"


def test_d3_implemented_flag_and_report_separation(qo):
    """implemented 标记齐备；报告单列未实现规则，且不计入 implemented_rules。"""
    for rid, r in qo.QUALITY_RULES.items():
        assert "implemented" in r, rid
    # 用 run_all_checks 的真实结果（未实现规则由 run_check 置 error(not_implemented)；
    # 但 6 表规则会读真库——此处只统计「实现状态」维度，不求全 ok）
    results = [{"rule": rid, "level": r["level"],
                "detected": False, "status": "ok", "error_kind": None, "details": ""}
               for rid, r in qo.QUALITY_RULES.items()]
    rep = json.loads(qo.generate_report(results))
    assert rep["implemented_rules"] < rep["total_rules"], rep
    assert rep["implemented_rules"] == sum(
        1 for r in qo.QUALITY_RULES.values() if r.get("implemented", True))
    # 未实现规则确实会产出 not_implemented（不走真库，直接单规则调用）
    for rid in ("timestamp_normalize", "minute_front_rewrite"):
        assert qo.run_check(rid)["error_kind"] == "not_implemented"


def test_d3_unimplemented_counted_as_error_in_real_run(qo):
    """真实 run_all_checks 中，未实现规则以 error(not_implemented) 出现（覆盖到报告）。"""
    results = qo.run_all_checks()
    not_impl = [r for r in results if r.get("error_kind") == "not_implemented"]
    assert len(not_impl) == 2, [r["rule"] for r in not_impl]
    rep = json.loads(qo.generate_report(results))
    assert rep["not_implemented"] >= 2, rep
    # 未实现规则不得静默成 ok
    assert all(r["status"] == "error" for r in not_impl)


def test_d3_list_rules_shows_status_and_threshold():
    """CLI --list-rules 输出实现状态与阈值（可运维）。"""
    p = subprocess.run([sys.executable, str(MOD), "--list-rules"],
                       capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert p.returncode == 0, p.stderr[-300:]
    assert "未实现" in p.stdout, "未实现规则应显式标注"
    assert "阈值=" in p.stdout, "应输出可机读阈值"


# ===========================================================================
# D4 时机/通道（一期：探测 + 拒绝 + 提示）
# ===========================================================================

def test_d4_write_path_rejected_when_daemon_active(qo, monkeypatch):
    """写路径（--repair）在 daemon 活跃期被拒（退出码 3）+ 提示空档窗。"""
    monkeypatch.setattr(qo, "_daemon_active", lambda: (True, "daemon 活跃（pid=1）"))
    p = subprocess.run([sys.executable, str(MOD), "--repair", "L1"],
                       capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert p.returncode == 3, (p.returncode, p.stdout[-200:], p.stderr[-300:])
    assert "空档窗" in (p.stderr + p.stdout), "拒执行须提示空档窗路径"


def test_d4_lock_probe_propagates_lock_conflict(qo, monkeypatch, tmp_path):
    """锁探测：锁冲突 ⇒ (True, lock_conflict)；库不存在 ⇒ (True, basis_unavailable)。"""
    missing = tmp_path / "nope.db"
    locked, kind, _ = qo._probe_db_lock(missing)
    assert locked and kind == "basis_unavailable", (locked, kind)

    import duckdb
    db = tmp_path / "x.db"
    con = duckdb.connect(str(db)); con.execute("CREATE TABLE t(a INT)"); con.close()
    monkeypatch.setitem(qo.CONFIG, "lock_probe_retries", 0)

    class _Boom:
        def __init__(self, *a, **k): pass
        def execute(self, *a, **k):
            raise RuntimeError("Could not set lock on file: x.db")
        def close(self): pass
    monkeypatch.setattr(duckdb, "connect", lambda *a, **k: _Boom())
    locked, kind, msg = qo._probe_db_lock(db)
    assert locked and kind == "lock_conflict", (locked, kind, msg)


def test_d4_external_script_paths_are_configurable(qo):
    """外部脚本路径须可配（v0.1 硬编码 ⇒ 客户机静默报绿）。"""
    assert "cloud_parity_script" in qo.CONFIG and "gap_registry_script" in qo.CONFIG
    assert "QS_CLOUD_PARITY_SCRIPT" in MOD.read_text(encoding="utf-8")
    # 不可达 ⇒ error（不得 detected=False 静默）
    src = MOD.read_text(encoding="utf-8")
    assert "脚本不可达" in src


def test_d4_unreachable_external_script_is_error(qo, monkeypatch):
    """脚本不可达场景 ⇒ error(basis_unavailable)，不是静默 ok/False。"""
    monkeypatch.setitem(qo.CONFIG, "cloud_parity_script", "Z:/__no_such_script__.py")
    r = qo.run_check("cloud_parity")
    assert r["status"] == "error" and r["error_kind"] == "basis_unavailable", r
    assert r["detected"] is False