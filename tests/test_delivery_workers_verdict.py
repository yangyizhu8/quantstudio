# -*- coding: utf-8 -*-
"""N-子3/4/5 · D2 manifest 判定 / D2-b 结构化判据 / D6 skip 变体 / D3 四态汇总。

对应客户事故根因：界面「完成（7/73 拉取成功）」——真实是 60 个已写入任务被判失败、
2 个 skip 变体漏计，且失败数被「完成」主词掩盖。
"""
from __future__ import annotations

from quantstudio.gui.workers import classify_once_entry


# ── D2-b：结构化真源优先（manifest.verdict）────────────────────────────────
def test_manifest_verdict_global_only_is_ok_with_warn():
    m = {"nonce": "n1", "verdict": {
        "task_ok": True, "audit_global_fail": True,
        "audit_table_ok": True, "audit_global_ok": False,
        "audit_reason": "AUDIT_GLOBAL_FAIL(global-scope only)"}}
    e = classify_once_entry(m, "", "n1")
    assert e["ok"] is True, "数据写入成功 + 仅全局段审计告警 ⇒ 不得判失败"
    assert e["audit_global_warn"] is True
    assert e["verdict_source"] == "manifest"


def test_manifest_verdict_task_failure_is_failure():
    e = classify_once_entry({"nonce": "n1", "verdict": {"task_ok": False}}, "", "n1")
    assert e["ok"] is False
    assert e.get("audit_global_warn") is False


def test_nonce_mismatch_is_failure():
    e = classify_once_entry({"nonce": "n2", "verdict": {"task_ok": True}}, "", "n1")
    assert e["ok"] is False
    assert "nonce" in str(e.get("error"))


# ── D6：skip 文案变体（老 manifest 回退路径）──────────────────────────────
def test_legacy_skip_variant_is_counted_as_skipped():
    """客户实测变体：'no new data (streaming), skip' —— 旧精确串判据会漏计。"""
    e = classify_once_entry({"nonce": "n1"},
                            "[x] no new data (streaming), skip", "n1")
    assert e["ok"] is True
    assert e.get("skipped") is True
    assert e["verdict_source"] == "log-fallback"


def test_legacy_skip_exact_string_still_skipped():
    e = classify_once_entry({"nonce": "n1"}, "[x] no new data, skip", "n1")
    assert e["ok"] is True and e.get("skipped") is True


def test_legacy_once_done_is_success():
    e = classify_once_entry({"nonce": "n1"}, "[CLI] once 完成（task + audit 全部通过）", "n1")
    assert e["ok"] is True
    assert not e.get("skipped")


def test_legacy_without_marker_is_failure():
    e = classify_once_entry({"nonce": "n1"}, "[QualityAudit] quality audit failed", "n1")
    assert e["ok"] is False


# ── D3：四态计数口径（与 workers payload 一致）─────────────────────────────
def _counts(results):
    return {
        "ok": sum(1 for r in results if r.get("ok") and not r.get("skipped")
                  and not r.get("audit_global_warn")),
        "skipped": sum(1 for r in results if r.get("skipped")),
        "audit_global_warn": sum(1 for r in results if r.get("audit_global_warn")),
        "failed": sum(1 for r in results if not r.get("ok")),
    }


def test_four_state_counts_match_client_shape():
    """客户批次形态复现：60 已写入（全局告警）+ 7 跳过 + 6 失败 ⇒ 失败数必须可见。"""
    results = []
    results += [{"ok": True, "audit_global_warn": True} for _ in range(60)]
    results += [{"ok": True, "skipped": True} for _ in range(7)]
    results += [{"ok": False} for _ in range(6)]
    c = _counts(results)
    assert c == {"ok": 0, "skipped": 7, "audit_global_warn": 60, "failed": 6}
    assert c["failed"] == 6, "失败数必须显式存在（D3：不得被『完成』主词掩盖）"
