# -*- coding: utf-8 -*-
"""G-2 机检脚本单测：三态 + 口径边界（件 G rev2 输入契约，2026-10-09）。

三态退出码：0=无提示 / 3=候选提示或维护态告警 / 1=异常或解析失败（与提示严格区分）。
fixture 化：档案与 S1 JSON 全部落 tmp_path，不依赖真实台账/档案/output 树；
另覆盖：首跑去重、fail→pass 终判口径、连续重置、S1 JSON 源、--local 兼容、
只读 grep 核验（验收判据⑤）。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_dimension_alignment as cda

# ---------- 夹具构造 ----------

ARCHIVE_TMPL_HEAD = (
    "# 契约档案 · 维度对齐档案（测试夹具）\n\n"
    "## 维度条目模板（六要素）\n\n```\n### DIM-nn <维度名>\n- ③ 连续对齐计数（固定表格）\n```\n\n---\n\n"
)
ARCHIVE_TMPL_TAIL = "\n## 口径定义（脚本与档案共同依据）\n\n- 「首跑」定义：同策略 id 只计首次合格记录\n- N 阈值：3\n"


def dim_section(dim="DIM-01", name="ETF 日线策略域", status="候选",
                evolution="（空）——宣布后启用；当前无。", rows=None):
    """构造一个 ### DIM-nn 维度节（要素③固定表格 + ④宣布状态 + ⑤平台演化）。"""
    lines = [f"### {dim} {name}", "",
             "- **③ 连续对齐计数（机读面，固定表格）**：", "",
             "  | 策略 id | 首跑日期 | verdict | 证据指针 |",
             "  |---|---|---|---|"]
    for r in (rows if rows is not None else []):
        lines.append("  | " + " | ".join(str(c) for c in r) + " |")
    lines += ["",
              f"- **④ 宣布状态**：**{status}**（N=x / 阈值 3）。",
              f"- **⑤ 平台演化响应记录**：{evolution}",
              "- **⑥ 关联指针**：台账 §3（测试夹具）。", ""]
    return "\n".join(lines)


def build_archive(*sections):
    return ARCHIVE_TMPL_HEAD + "\n".join(sections) + ARCHIVE_TMPL_TAIL


def q(strategy, date, verdict="已对齐"):
    """一行合格首跑记录。"""
    return [strategy, date, verdict, f"ev-{strategy}"]


def run(capsys, tmp_path, archive_text, json_files=None, use_local=False, extra=None):
    """落盘夹具并执行 cda.main → (exit_code, stdout, stderr)。

    json_files=None 时给一个空的显式 alignment 目录（不触达真实 output/ 树，且正好走
    「命中 0 → 回退人工清单」降级路径）。
    """
    arch = tmp_path / "dimension-alignment.md"
    arch.write_text(archive_text, encoding="utf-8")
    args = ["--archive", str(arch)]
    if json_files is not None:
        adir = tmp_path / "products" / "alignment"
        adir.mkdir(parents=True, exist_ok=True)
        for i, payload in enumerate(json_files):
            (adir / f"s1_{i:02d}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        args += (["--local", str(tmp_path / "products")] if use_local
                 else ["--alignment-dir", str(adir)])
    else:
        adir = tmp_path / "alignment"
        adir.mkdir(exist_ok=True)
        args += ["--alignment-dir", str(adir)]
    if extra:
        args += extra
    code = cda.main(args)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def jrec(strategy, date, verdict="已对齐", dimension="DIM-01", **kw):
    payload = {"strategy_id": strategy, "dimension": dimension,
               "date": date, "verdict": verdict, "evidence": f"ev-{strategy}"}
    payload.update(kw)
    return payload


# ---------- 态①：退出码 0（无提示） ----------

def test_state0_no_hint_candidate_below_threshold(capsys, tmp_path):
    """N=1 < 阈值 3 且候选 → 无提示退出 0；零 JSON 回退人工清单且显式标注（不静默）。"""
    sec = dim_section(rows=[["四象限ETF轮动策略", "2026-10-07", "残差达标（0.013%）", "台账 §种子数据"]])
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 0
    assert "N=1" in out and "候选" in out
    assert "可宣布" not in out and "re-aligned" not in out
    assert "数据源=人工清单" in out          # 降级语义显式标注
    assert "回退档案要素③人工清单" in out
    assert "四象限ETF轮动策略" in out


def test_state0_announced_quiet_no_evolution(capsys, tmp_path):
    """已宣布·维护态且无新案/无平台演化 → 无告警退出 0（维护态只响应新案/演化）。"""
    sec = dim_section(status="已宣布·维护态", rows=[q("s1", "2026-10-01")])
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 0
    assert "已宣布·维护态" in out and "re-aligned" not in out


def test_state0_announced_unqualified_row_no_warning(capsys, tmp_path):
    """口径边界：已宣布维度普通未达标行（非立新案）不触发维护态告警（按 rev2 规则字面）。"""
    sec = dim_section(status="已宣布·维护态",
                      rows=[q("s1", "2026-10-01"), ["s2", "2026-10-02", "归因候选（委托分歧在先）", "ev-s2"]])
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 0
    assert "re-aligned" not in out
    assert "连续重置点" in out               # 未达标终判仍重置计数（信息性输出）


# ---------- 态②：退出码 3（候选提示 / 维护态告警） ----------

def test_state3_candidate_hint_at_threshold(capsys, tmp_path):
    """候选维度连续 N=3 ≥ 阈值 → 「可宣布该维度对齐（待用户确认）」退出 3。"""
    sec = dim_section(rows=[q("s1", "2026-10-01"),
                            q("s2", "2026-10-02", "残差达标（0.02%）"),
                            q("s3", "2026-10-03")])
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 3
    assert "N=3" in out
    assert "可宣布该维度对齐（待用户确认）" in out


def test_state3_maintenance_warning_newcase_row(capsys, tmp_path):
    """已宣布维度出现立新案行 → 「需 re-aligned 复验」退出 3。"""
    sec = dim_section(status="已宣布·维护态",
                      rows=[q("s1", "2026-10-01"),
                            ["s2", "2026-10-05", "不可归因→立新案（DAT 候选）", "ev-s2"]])
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 3
    assert "需 re-aligned 复验" in out and "新案行" in out


def test_state3_maintenance_warning_archive_evolution(capsys, tmp_path):
    """已宣布维度档案要素⑤平台演化记录非空 → 维护态告警退出 3。"""
    sec = dim_section(status="已宣布·维护态",
                      evolution="2026-10-08 平台 5.2.3 升级 → graduated → re-aligned 复验中",
                      rows=[q("s1", "2026-10-01")])
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 3
    assert "需 re-aligned 复验" in out and "要素⑤" in out


def test_state3_maintenance_warning_json_platform_evolution(capsys, tmp_path):
    """已宣布维度 S1 JSON platform_evolution 标记 → 维护态告警退出 3。"""
    sec = dim_section(status="已宣布·维护态", rows=[q("s1", "2026-10-01")])
    code, out, err = run(capsys, tmp_path, build_archive(sec),
                         json_files=[jrec("s1", "2026-10-01", platform_evolution=True)])
    assert code == 3
    assert "需 re-aligned 复验" in out and "platform_evolution" in out


# ---------- 判定口径：首跑去重 / 连续重置 ----------

def test_first_run_dedupe_rerun_not_recounted(capsys, tmp_path):
    """同策略 id 重跑（寻优轮次）不重复计数：X 两行 + Y + Z → N=3 而非 4。"""
    sec = dim_section(rows=[q("X", "2026-10-01"), q("Y", "2026-10-02"),
                            q("Z", "2026-10-03"), q("X", "2026-10-05")])
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 3                        # 3 个不同策略凑满阈值
    assert "N=3" in out


def test_terminal_verdict_fail_then_pass_counts_once(capsys, tmp_path):
    """fail→pass：终判（最新记录）合格则在其最早合格记录时点计 1 次，中间失败不重置。"""
    sec = dim_section(rows=[["X", "2026-10-01", "混合形态（需人工仲裁）", "ev-X"],
                            q("X", "2026-10-02"), q("Y", "2026-10-03"), q("Z", "2026-10-04")])
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 3 and "N=3" in out
    assert "连续重置点" not in out


def test_reset_rule_nonqualified_clears_count(capsys, tmp_path):
    """连续重置：非合格终判出现在 2 次合格之后 → 清零重计，此后 1 次合格 → N=1。"""
    sec = dim_section(rows=[q("s1", "2026-10-01"), q("s2", "2026-10-02"),
                            ["s3", "2026-10-03", "归因候选（委托分歧在先）", "ev-s3"],
                            q("s4", "2026-10-04")])
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 0
    assert "N=1" in out
    assert "连续重置点" in out and "s3" in out


def test_reset_rule_newcase_row_clears_count(capsys, tmp_path):
    """连续重置：立新案行同样清零 → N=0（候选态无提示）。"""
    sec = dim_section(rows=[q("s1", "2026-10-01"), q("s2", "2026-10-02"),
                            ["s3", "2026-10-03", "不可归因→立新案（DAT 候选）", "ev-s3"]])
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 0
    assert "N=0" in out and "连续重置点" in out


# ---------- S1 JSON 源（优先数据源） ----------

def test_s1_json_source_candidate_hint(capsys, tmp_path):
    """S1 JSON 命中 → 数据源=S1 JSON（不回退）；3 策略凑满阈值 → 候选提示退出 3。"""
    sec = dim_section(rows=[])               # 档案表格为空（记录以 JSON 为准，不合并）
    code, out, err = run(capsys, tmp_path, build_archive(sec),
                         json_files=[jrec("etf_a", "2026-10-01"),
                                     jrec("etf_b", "2026-10-02", "残差达标（0.01%）"),
                                     jrec("etf_c", "2026-10-03")])
    assert code == 3
    assert "数据源=S1 JSON" in out
    assert "可宣布该维度对齐（待用户确认）" in out and "N=3" in out
    assert "etf_a" in out


def test_s1_json_local_dir_compat(capsys, tmp_path):
    """--local <回测产物目录> 兼容参数：扫 <local>/alignment/*.json。"""
    sec = dim_section(rows=[])
    code, out, err = run(capsys, tmp_path, build_archive(sec),
                         json_files=[jrec("etf_a", "2026-10-01")], use_local=True)
    assert code == 0
    assert "数据源=S1 JSON" in out and "etf_a" in out


# ---------- 态③：退出码 1（异常 / 解析失败，与提示严格区分） ----------

def test_state1_bad_table_header(capsys, tmp_path):
    sec = dim_section(rows=[["s1", "2026-10-01", "已对齐", "e1"]]).replace(
        "| 策略 id | 首跑日期 | verdict | 证据指针 |", "| 策略 | 日期 | verdict | 证据 |")
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 1 and "表头不合规" in err


def test_state1_unknown_status_text(capsys, tmp_path):
    sec = dim_section(status="未知态", rows=[q("s1", "2026-10-01")])
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 1 and "宣布状态" in err


def test_state1_bad_date_format(capsys, tmp_path):
    sec = dim_section(rows=[["s1", "2026/10/01", "已对齐", "e1"]])
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 1 and "不合规" in err and "2026/10/01" in err


def test_state1_no_dimension_entries(capsys, tmp_path):
    code, out, err = run(capsys, tmp_path,
                         ARCHIVE_TMPL_HEAD + ARCHIVE_TMPL_TAIL)   # 无任何 ### DIM-nn
    assert code == 1 and "未找到任何维度条目" in err


def test_state1_missing_elem3(capsys, tmp_path):
    sec = dim_section().replace("- **③ 连续对齐计数（机读面，固定表格）**：",
                                "- **要素三 连续对齐**：")   # 破坏 ③ 标记双条件
    code, out, err = run(capsys, tmp_path, build_archive(sec))
    assert code == 1 and "要素③" in err


def test_state1_archive_file_missing(capsys, tmp_path):
    """档案路径不存在 → OSError → 退出 1。"""
    code = cda.main(["--archive", str(tmp_path / "nope.md"),
                     "--alignment-dir", str(tmp_path)])
    cap = capsys.readouterr()
    assert code == 1 and "解析失败" in cap.err


def test_state1_malformed_json(capsys, tmp_path):
    adir = tmp_path / "alignment"
    adir.mkdir()
    (adir / "bad.json").write_text("{not-json", encoding="utf-8")
    sec = dim_section(rows=[q("s1", "2026-10-01")])
    arch = tmp_path / "dimension-alignment.md"
    arch.write_text(build_archive(sec), encoding="utf-8")
    code = cda.main(["--archive", str(arch), "--alignment-dir", str(adir)])
    cap = capsys.readouterr()
    assert code == 1 and "JSON 读取/解析失败" in cap.err


def test_state1_json_missing_strategy_id(capsys, tmp_path):
    sec = dim_section(rows=[])
    code, out, err = run(capsys, tmp_path, build_archive(sec),
                         json_files=[{"dimension": "DIM-01", "date": "2026-10-01", "verdict": "已对齐"}])
    assert code == 1 and "strategy_id" in err


def test_state1_json_unknown_dimension(capsys, tmp_path):
    sec = dim_section(rows=[])
    code, out, err = run(capsys, tmp_path, build_archive(sec),
                         json_files=[jrec("s1", "2026-10-01", dimension="DIM-99 未知域")])
    assert code == 1 and "无法唯一归属" in err


# ---------- 工程面 ----------

def test_help_smoke(capsys):
    with pytest.raises(SystemExit) as ei:
        cda.main(["--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "usage:" in out and "退出码" in out and "不自动宣布" in out


def test_threshold_constant():
    """N 阈值=3（模块常量，rev2 口径）。"""
    assert cda.N_THRESHOLD == 3


def test_read_only_no_write_paths():
    """验收判据⑤：grep 核验脚本无任何写入路径（台账/档案/产物零写入）。"""
    src = (ROOT / "scripts" / "check_dimension_alignment.py").read_text(encoding="utf-8")
    for token in ("write_text", "write_bytes", ".write(", "os.remove", "os.mkdir",
                  ".mkdir(", ".unlink(", "rmtree", ".rename(", "os.rmdir", "makedirs"):
        assert token not in src, f"只读核验失败：脚本含写入路径 {token}"


def test_classify_verdict_kinds():
    """终判分类：S1 原始三态文本不误命中合格档。"""
    assert cda.classify_verdict("已对齐") == "已对齐"
    assert cda.classify_verdict("残差达标（0.013%）") == "残差达标"
    assert cda.classify_verdict("不可归因→立新案（DAT 候选）") == "立新案"
    assert cda.classify_verdict("归因候选（委托分歧在先）") == "未达标"
    assert cda.classify_verdict("混合形态（需人工仲裁）") == "未达标"
