# -*- coding: utf-8 -*-
"""PD-0011 闭环管线自测（含 ②审 2026-10-28 要求的两条负向对照）。

覆盖：
- 收敛机检：真实台账 PASS；合成 R>0 / N 非单调 → FAIL；空台账 → FAIL（fail-closed）
- 巡检退出码三态：健康 0 / 仅 WARN 2 / 硬缺口 1
- R1/R4 留痕缺失 → WARN
- **②审 a**：L2 缺 confirmed_by → 未确认；起草方自证 → 未确认；无报告引用 → 未确认
- **②审 b**：回哺读到条目但 trigger 与失败签名**不相关** → 判「未救」（硬缺口）
- kb_draft_from_deploy：从日志/报告生成起草命令，且**不落盘**
"""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import check_pd_convergence as conv  # noqa: E402
import check_deploy_closedloop as loop  # noqa: E402


# ------------------------------------------------------------ 收敛机检 --

def test_convergence_real_ledger_passes():
    text = (REPO / "docs/ptrade-deploy-convergence-ledger.md").read_text(encoding="utf-8")
    rounds, _ = conv.parse_rounds(text)
    assert rounds, "真实台账应解析出轮次"
    res = conv.judge(rounds)
    assert res["pass"] is True, res["reason"]
    assert res["n_seq"][-1] == 0
    assert res["tail_r_zero"] >= 3


def test_convergence_r_nonzero_fails():
    """负向：末尾 R>0 ⇒ 不得判 PASS。"""
    rounds = {1: {"N": 2, "R": 0}, 2: {"N": 1, "R": 0}, 3: {"N": 0, "R": 1}}
    res = conv.judge(rounds)
    assert res["pass"] is False


def test_convergence_n_not_zero_fails():
    """负向：末轮 N≠0 ⇒ FAIL。"""
    rounds = {1: {"N": 2, "R": 0}, 2: {"N": 1, "R": 0}, 3: {"N": 2, "R": 0}}
    assert conv.judge(rounds)["pass"] is False


def test_convergence_empty_ledger_fails_closed():
    res = conv.judge({})
    assert res["pass"] is False
    assert "格式异常" in res["reason"]


# ------------------------------------------------------------ R1/R4 --

def test_r1_r4_missing_warns():
    ok = loop.check_r1_r4("仅一段无留痕的台账")
    assert ok["ok"] is False and ok["r1"] is False and ok["r4"] is False


def test_r1_r4_present_passes():
    ok = loop.check_r1_r4("知识反哺：[命中] X（PD/D3/x.md，2026-10-28）\n本轮沉淀：PD-2026-0011")
    assert ok["ok"] is True


# --------------------------------------------------- L2（②审 a / b） --

ENTRY = '''---
id: PD-2026-9999
trigger: "P5 未知 API 门禁把局部变量方法调用报成未知 API"
---
正文
'''


def test_l2_without_confirmed_by_is_unconfirmed():
    """②审 a：无 confirmed_by ⇒ 未确认（不是未救，也不得算已救）。"""
    r = loop.judge_l2("P5 FAIL: 未知 API gates._local_names 局部方法调用",
                      "确认：已核", ENTRY)
    assert r["state"] == "未确认"
    assert "confirmed_by" in r["reason"]


def test_l2_drafter_self_confirmation_rejected():
    """②审 a：起草方自证 ⇒ 未确认。"""
    r = loop.judge_l2("P5 FAIL: 未知 API",
                      "confirmed_by=kb_draft 起草方\nreport=docs/evidence/x.md", ENTRY)
    assert r["state"] == "未确认"
    assert "自证" in r["reason"]


def test_l2_confirm_without_report_ref_rejected():
    r = loop.judge_l2("P5 FAIL: 未知 API", "confirmed_by=总调度", ENTRY)
    assert r["state"] == "未确认"
    assert "报告" in r["reason"]


def test_l2_irrelevant_entry_judged_unsaved():
    """②审 b：读到条目但内容不相关 ⇒ 判『未救』。"""
    report = "P5 FAIL: 定时任务累计超过 5 个（F4）"
    confirm = "confirmed_by=总调度\nreport=docs/evidence/run-20261028.md"
    r = loop.judge_l2(report, confirm, ENTRY)   # ENTRE 讲的是未知 API，与定时任务无关
    assert r["state"] == "未救"
    assert "不相关" in r["reason"]


def test_l2_relevant_entry_saved():
    report = "P5 FAIL: 未知 API（需人工确认）: 行 1203 filter_stock_by_status"
    confirm = "confirmed_by=总调度\nreport=docs/evidence/run-20261028.md"
    r = loop.judge_l2(report, confirm, ENTRY)
    assert r["state"] == "已救", r
    assert r["saved"] is True


def test_l2_generic_cjk_words_only_not_saved():
    """收紧后的主判据：只命中通用中文词（嵌套/门禁/注入）而无标识符重叠 ⇒ 未救。

    实证来源：2026-10-28 对照 A —— 真实报告 notes 里的「嵌套/门禁/注入」曾让
    「未知 API 误报」条目对「set_limit_mode 未门控」失败被判『已救』（假阳性）。
    """
    report = "嵌套复合语句 门禁 注入 局部函数 语句"
    confirm = "confirmed_by=总调度\nreport=docs/evidence/run-20261028.md"
    r = loop.judge_l2(report, confirm, ENTRY)
    assert r["state"] == "未救", r
    assert "标识符" in r["reason"]


def test_signature_from_json_report_uses_fail_stage_only(tmp_path):
    """失败签名只取 FAIL 段，notes 不参与（防通用词造成假相关）。"""
    rep = tmp_path / "rep.json"
    rep.write_text(json.dumps({
        "status": "FAIL",
        "stages": {"P3_transform": {"status": "PASS"},
                   "P5_gates": {"status": "FAIL", "reason": "contract_full: set_limit_mode 未门控"}},
        "notes": ["P3 警告: 嵌套复合语句", "P5 未知 API: get_order"],
    }, ensure_ascii=False), encoding="utf-8")
    sig = loop.signature_from_report(str(rep))
    assert "set_limit_mode" in sig
    assert "未知 API" not in sig and "嵌套" not in sig, sig


# ------------------------------------------- kb_draft_from_deploy（G1） --

def test_kb_draft_from_log_generates_command_and_no_write(tmp_path):
    import kb_draft_from_deploy as g1
    log = tmp_path / "run.log"
    log.write_text("[P5_gates] FAIL — contract_full: ['行 942: set_limit_mode 交易环境不可用且未门控']\n"
                   "[note] P0 vault 回哺来源：trading-knowledge\n", encoding="utf-8")
    info = g1.from_log_text(log.read_text(encoding="utf-8"))
    assert info["failed"], "应解析出 FAIL 段"
    cmd, todo = g1.build_command(info)
    assert "scripts/kb_draft.py" in cmd and "knowledge/ptrade-deploy" in cmd
    assert any("trigger" in t for t in todo)
    # 不落盘：脚本只返回命令，族库文件数不变
    before = len(list((REPO / "knowledge/ptrade-deploy").glob("PD-*.md")))
    rc = g1.main(["--report-log", str(log)])
    after = len(list((REPO / "knowledge/ptrade-deploy").glob("PD-*.md")))
    assert rc == 0 and after == before


def test_kb_draft_from_report_dict_stage_names():
    import kb_draft_from_deploy as g1
    info = g1.from_report_dict({"status": "FAIL", "stages": {
        "P3_transform": {"status": "PASS"}, "P5_gates": {"status": "FAIL", "reason": "contract_full: x"}},
        "notes": ["P5 未知 API（需人工确认）: 行 1203 get_order"]})
    assert info["failed"] == [("P5_gates", "contract_full: x")]
    cmd, _ = g1.build_command(info)
    assert "contract_full" in cmd


# --------------------------------------------------- 巡检退出码三态 --

def _run_closedloop(tmp_path, extra=()):
    """在临时 cwd 不便（脚本用 REPO 常量），故直接调 main() 并读返回值。"""
    return loop.main(list(extra))


def test_closedloop_healthy_or_warn_is_0_or_2():
    """真实仓巡检：收敛/族机检/回哺均正常 ⇒ 退出码 0 或 2（L2 未提供材料算 WARN）。"""
    rc = loop.main(["--json"])
    assert rc in (0, 2), f"期望 0/2，实得 {rc}"


def test_closedloop_l2_unsaved_forces_hard_fail(tmp_path):
    """L2 判未救 ⇒ 巡检必须返回 1（硬缺口）。"""
    report = tmp_path / "r.txt"
    confirm = tmp_path / "c.txt"
    entry = tmp_path / "PD-2026-9999.md"
    report.write_text("P5 FAIL: 定时任务累计超过 5 个（F4）", encoding="utf-8")
    confirm.write_text("confirmed_by=总调度\nreport=docs/evidence/x.md", encoding="utf-8")
    entry.write_text(ENTRY, encoding="utf-8")
    rc = loop.main(["--l2-report", str(report), "--l2-confirm", str(confirm),
                    "--l2-entry", str(entry), "--json"])
    assert rc == 1
