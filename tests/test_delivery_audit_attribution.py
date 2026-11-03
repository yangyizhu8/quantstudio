# -*- coding: utf-8 -*-
"""N-子2 · D1 归因与退出码契约（节点⑳ ②审：audit_ok 保真 + 细分位归因 + 开关写死表）。

判据：
- 表级失败 ⇒ exit 1；仅全局段失败 ⇒ exit 0 + `AUDIT_GLOBAL_FAIL`；
- 开关优先级表：`QS_AUDIT_STRICT_FAIL` > `QS_AUDIT_SCOPE_ATTRIBUTION=legacy` > 默认新语义；
- `audit_ok` 仍为真值（审计跑完且无 error），不被细分位改写。
"""
from __future__ import annotations

import pytest

from quantstudio.pipeline import quality_audit as QA
from quantstudio.pipeline.daemon import (
    AUDIT_GLOBAL_FAIL_TOKEN,
    resolve_audit_exit,
)


def _report(*, table_error: bool = False, global_error: bool = False):
    """经 owner 路径（_add）构造 issue——scope 由 _add 按检查名判定。"""
    rep = QA.QualityReport()
    if table_error:
        QA.DataQualityAuditor._add(rep, "RequiredValueNull", "stock_daily", 3, "error", "x")
    if global_error:
        QA.DataQualityAuditor._add(rep, "QfqDeadLetter", "qfq_trigger_queue", 15177,
                                   "error", "dead_letter")
    return rep


# ── 归因：scope 打标与两向判定 ─────────────────────────────────────────────
def test_scope_assignment_global_vs_table():
    rep = QA.QualityReport()
    QA.DataQualityAuditor._add(rep, "QfqDeadLetter", "qfq_trigger_queue", 1, "error")
    QA.DataQualityAuditor._add(rep, "RequiredValueNull", "stock_daily", 1, "error")
    scopes = {i.check: i.scope for i in rep.issues}
    assert scopes["QfqDeadLetter"] == "global"
    assert scopes["RequiredValueNull"] == "table"


def test_global_only_error_keeps_table_ok():
    rep = _report(global_error=True)
    assert rep.table_ok is True, "全局段失败不得判表级失败"
    assert rep.global_ok is False
    assert rep.passed is False, "audit_ok 仍是真值（不因细分位改写）"


def test_table_error_fails_table_scope():
    rep = _report(table_error=True)
    assert rep.table_ok is False
    assert rep.global_ok is True
    assert rep.passed is False


def test_clean_report_all_ok():
    rep = _report()
    assert rep.table_ok is True and rep.global_ok is True and rep.passed is True


# ── 退出码契约 + 开关写死表（互斥用例）─────────────────────────────────────
def test_exit_zero_with_global_only_failure():
    code, reason = resolve_audit_exit(task_ok=True, audit_run=True, audit_ok=False,
                                      audit_table_ok=True, audit_global_ok=False, env={})
    assert code == 0
    assert reason.startswith(AUDIT_GLOBAL_FAIL_TOKEN)


def test_exit_one_on_table_failure():
    code, reason = resolve_audit_exit(task_ok=True, audit_run=True, audit_ok=False,
                                      audit_table_ok=False, audit_global_ok=True, env={})
    assert code == 1
    assert "table" in reason


def test_switch_table_strict_overrides_legacy_and_default():
    """① QS_AUDIT_STRICT_FAIL 优先级最高：任何 audit 失败 ⇒ 1。"""
    code, reason = resolve_audit_exit(task_ok=True, audit_run=True, audit_ok=False,
                                      audit_table_ok=True, audit_global_ok=False,
                                      env={"QS_AUDIT_STRICT_FAIL": "1"})
    assert code == 1 and "strict" in reason
    # 与 legacy 同开也不冲突（同为从严）
    code2, _ = resolve_audit_exit(task_ok=True, audit_run=True, audit_ok=False,
                                   audit_table_ok=True, audit_global_ok=False,
                                   env={"QS_AUDIT_STRICT_FAIL": "1",
                                        "QS_AUDIT_SCOPE_ATTRIBUTION": "legacy"})
    assert code2 == 1


def test_switch_table_legacy_attribute_counts_global_as_table():
    """② QS_AUDIT_SCOPE_ATTRIBUTION=legacy：全局段失败按表级处理 ⇒ 1。"""
    code, reason = resolve_audit_exit(task_ok=True, audit_run=True, audit_ok=False,
                                      audit_table_ok=True, audit_global_ok=False,
                                      env={"QS_AUDIT_SCOPE_ATTRIBUTION": "legacy"})
    assert code == 1 and "legacy" in reason


def test_task_failure_always_exit_one():
    code, _ = resolve_audit_exit(task_ok=False, audit_run=True, audit_ok=True,
                                 audit_table_ok=True, audit_global_ok=True, env={})
    assert code == 1


def test_no_audit_run_follows_task_only():
    code, reason = resolve_audit_exit(task_ok=True, audit_run=False, audit_ok=True,
                                      audit_table_ok=None, audit_global_ok=None, env={})
    assert code == 0 and reason == "task-only"


def test_switch_table_documented_in_source():
    """开关写死表必须与实现同源（防止文档漂移）。"""
    import inspect

    src = inspect.getsource(resolve_audit_exit)
    assert "QS_AUDIT_STRICT_FAIL" in src
    assert "QS_AUDIT_SCOPE_ATTRIBUTION" in src
    assert "legacy" in src
