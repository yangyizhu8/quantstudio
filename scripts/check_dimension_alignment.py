#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""G-2 · 维度对齐收敛判据机检脚本（闭环件 G，docs/loop-g-plan.md rev2，2026-10-09）。

只读巡检：解析维度档案（knowledge/contracts/dimension-alignment.md）与 S1 终判 JSON，
计算每维度「连续对齐计数 N」与宣布状态，**只产提示、绝不自动宣布**——宣布=用户确认动作
（对齐 docs/strategy-compiler/alignment-lifecycle.md「aligned 唯一硬门」语义）。

数据源（rev2 输入契约，二选一、不合并）：
  ① S1 JSON（优先）：
     - 发现路径=显式参数：--alignment-dir <目录|文件>，或 --local <回测产物目录>
       （兼容 align_diff_report.py --local 语义，扫 <local>/alignment/*.json）；
     - 缺省：扫 <仓库>/output/ 树下所有 alignment/*.json（回测产物目录约定：
       output/generated_strategies/<策略名>/alignment/，与 validate_agent_strategy
       的 ALIGNMENT-GATE 同款路径形态）；
     - schema（随案归档的 S1 终判记录，非 align_diff_report.py 原始 dump）：
         {"strategy_id": str 必备, "dimension": str 必备, "date": "YYYY-MM-DD" 必备,
          "verdict": str 必备, "evidence": str 可选, "platform_evolution": bool 可选}
       dimension 需唯一归属到档案维度（DIM-nn 编号 / 全称 / 维度名）；
       无法归属=契约破坏→退出 1。
  ② 人工清单（降级回退）：磁盘零 S1 JSON 时回退读档案要素③固定表格（人工清单），
     输出显式标注「数据源=人工清单」（降级语义，不静默）。

判定口径（与档案「口径定义」节共同依据）：
  - 「首跑」：同策略 id 只计首次合格记录——合格=verdict 含「已对齐」或「残差达标」；
    重跑/参数寻优轮次不重复计数。操作化：策略终判=其最新记录；终判合格 → 计入其
    最早合格记录时点。S1 原始报告 triage.residual_ok 仅为「可直接判 aligned」的
    人核建议，本脚本不据此自动升级终判。
  - 「连续」重置：维度成员策略终判非合格（含「立新案」行）→ 自该时点计数清零重计。
  - N 阈值=3（模块常量 N_THRESHOLD，母计划原文 N≥3）。
  - 候选提示：N≥N_THRESHOLD 且状态=候选 → 「可宣布该维度对齐（待用户确认）」。
  - 维护态告警：已宣布维度出现新案行或平台演化标记（档案要素⑤非空 / JSON
    platform_evolution）→ 「需 re-aligned 复验」。

退出码（与提示严格区分，供 CI 判型）：
  0=无提示；3=有候选提示或维护态告警；1=异常/档案或 JSON 解析失败。

只读保证：本脚本零文件写入（台账/档案/产物均不动；可 grep 核验无写入路径，
对应测试 tests/test_check_dimension_alignment.py::test_read_only_no_write_paths）。

用法：
  python scripts/check_dimension_alignment.py [--archive <档案.md>]
                                              [--alignment-dir <目录|文件>]
                                              [--local <回测产物目录>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------- 常量（口径与档案「口径定义」节共同依据） ----------

N_THRESHOLD = 3                                   # N 阈值：连续 N≥3 即提示候选
QUALIFIED_KINDS = ("已对齐", "残差达标")             # 合格终判（首跑计数依据）
NEWCASE_KIND = "立新案"                            # 新案标记（重置 + 维护态告警依据）
TABLE_COLUMNS = ("策略 id", "首跑日期", "verdict", "证据指针")   # 要素③固定表格列契约
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DIM_HEADER_RE = re.compile(r"^###\s+(DIM-\d+)\s+(.+?)\s*$")
SEP_CELL_RE = re.compile(r"^:?-+:?$")

REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DEFAULT = REPO_ROOT / "knowledge" / "contracts" / "dimension-alignment.md"
PRODUCT_ROOT_DEFAULT = REPO_ROOT / "output"

SOURCE_JSON = "S1 JSON"
SOURCE_MANUAL = "人工清单"


class ArchiveFormatError(Exception):
    """维度档案格式不合规（要素③表格/④宣布状态等）→ 退出码 1。"""


# ---------- 终判分类 ----------

def classify_verdict(text):
    """终判分类。顺序：立新案 > 已对齐 > 残差达标 > 未达标。

    S1 原始三态文本（align_diff_report.py §5）不会误命中：「不可归因→立新案（…）」
    不含「已对齐」；「归因候选（…）」「混合形态（…）」落「未达标」。
    """
    t = (text or "").strip()
    if NEWCASE_KIND in t:
        return NEWCASE_KIND
    for mark in QUALIFIED_KINDS:
        if mark in t:
            return mark
    return "未达标"


def make_record(strategy, date, verdict_text, evidence, seq, origin):
    kind = classify_verdict(verdict_text)
    return {"strategy": strategy.strip(), "date": date.strip(),
            "verdict_text": verdict_text.strip(), "evidence": evidence.strip(),
            "kind": kind, "qualified": kind in QUALIFIED_KINDS,
            "seq": seq, "origin": origin}


# ---------- 档案解析（要素③固定表格 = 轻量机读面） ----------

def _table_cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _parse_fixed_table(dim, origin):
    tag = f"{dim['id']} {dim['name']}"
    lines = dim["table_lines"]
    if len(lines) < 2:
        raise ArchiveFormatError(f"{origin}：{tag} 要素③固定表格缺失或残缺（需表头+分隔行）")
    header = _table_cells(lines[0][1])
    if tuple(header) != TABLE_COLUMNS:
        raise ArchiveFormatError(
            f"{origin}:{lines[0][0]}：{tag} 要素③表头不合规——应为 "
            f"「| {' | '.join(TABLE_COLUMNS)} |」，实得「| {' | '.join(header)} |」")
    sep = _table_cells(lines[1][1])
    if len(sep) != len(TABLE_COLUMNS) or not all(SEP_CELL_RE.match(c) for c in sep):
        raise ArchiveFormatError(f"{origin}:{lines[1][0]}：{tag} 要素③分隔行不合规")
    records = []
    for line_no, line in lines[2:]:
        cells = _table_cells(line)
        where = f"{origin}:{line_no}"
        if len(cells) != len(TABLE_COLUMNS):
            raise ArchiveFormatError(
                f"{where}：{tag} 表格行列数 {len(cells)}（应 {len(TABLE_COLUMNS)}）")
        strategy, date, verdict, evidence = cells
        if not (strategy and date and verdict and evidence):
            raise ArchiveFormatError(f"{where}：{tag} 表格行存在空单元格")
        if not DATE_RE.match(date):
            raise ArchiveFormatError(f"{where}：{tag} 首跑日期「{date}」不合规（应 YYYY-MM-DD）")
        records.append(make_record(strategy, date, verdict, evidence,
                                   seq=len(records), origin=where))
    return records


def parse_archive(text, origin):
    """解析维度档案 → 维度列表（要素③表格记录、④状态、⑤演化标记）。

    只认 ``` 围栏外的 ### DIM-nn 维度节（## 级节即维度节结束）；要素③固定表格 =
    ③标记行与④标记行之间的 Markdown 表格（列契约固定四列）；不合规抛 ArchiveFormatError。
    """
    dims = {}
    order = []
    cur = None
    in_fence = False
    for idx, raw in enumerate(text.splitlines()):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("## ") and not stripped.startswith("###"):
            cur = None                       # 维度节结束（进入档案级节）
            continue
        m = DIM_HEADER_RE.match(stripped)
        if m:
            did = m.group(1)
            if did in dims:
                raise ArchiveFormatError(f"{origin}:{idx + 1}：维度条目 {did} 重复")
            cur = {"id": did, "name": m.group(2), "status": None, "evolution": False,
                   "table_lines": [], "elem3_seen": False, "elem4_seen": False}
            dims[did] = cur
            order.append(cur)
            continue
        if cur is None:
            continue
        if "**③" in stripped and "连续对齐计数" in stripped:
            cur["elem3_seen"] = True
        elif "**④" in stripped and "宣布状态" in stripped:
            cur["elem4_seen"] = True
            body = stripped.split("：", 1)[1] if "：" in stripped else ""
            if "已宣布" in body:
                cur["status"] = "announced"
            elif "候选" in body:
                cur["status"] = "candidate"
        elif "**⑤" in stripped and "平台演化" in stripped:
            body = stripped.split("：", 1)[1].strip() if "：" in stripped else ""
            cur["evolution"] = bool(body) and not body.startswith("（空）")
        elif cur["elem3_seen"] and not cur["elem4_seen"] and stripped.startswith("|"):
            cur["table_lines"].append((idx + 1, stripped))
    if not order:
        raise ArchiveFormatError(f"{origin}：未找到任何维度条目（### DIM-nn）")
    for cur in order:
        tag = f"{cur['id']} {cur['name']}"
        if not cur["elem3_seen"]:
            raise ArchiveFormatError(f"{origin}：{tag} 要素③（连续对齐计数固定表格标记）缺失")
        if cur["status"] is None:
            raise ArchiveFormatError(
                f"{origin}：{tag} 要素④宣布状态缺失或无法识别（应为「候选」或「已宣布」）")
        cur["records"] = _parse_fixed_table(cur, origin)
    return order


# ---------- S1 JSON 发现与载入 ----------

def discover_s1_json_paths(alignment_dir, local):
    """S1 JSON 发现（显式参数优先；缺省扫 output 树下 alignment/*.json）→ (paths, 描述)。"""
    if alignment_dir:
        p = Path(alignment_dir)
        if p.is_file():
            return [p], f"--alignment-dir={p}"
        if p.is_dir():
            return sorted(p.glob("*.json")), f"--alignment-dir={p}"
        return [], f"--alignment-dir={p}（路径不存在）"
    if local:
        p = Path(local) / "alignment"
        if p.is_dir():
            return sorted(p.glob("*.json")), f"--local={local} → {p}"
        return [], f"--local={local} → {p}（无 alignment/ 子目录）"
    root = PRODUCT_ROOT_DEFAULT
    if root.is_dir():
        hits = [q for q in root.rglob("*.json") if q.parent.name == "alignment"]
        return sorted(hits), f"缺省 {root} 树下 alignment/*.json"
    return [], f"缺省 {root}（不存在）"


def match_dimension(text, dims):
    """维度归属：精确（DIM-nn 编号 / 全称 / 维度名）→ 唯一包含式；失败返回 None。"""
    t = (text or "").strip()
    for d in dims:
        if t in (d["id"], d["name"], f"{d['id']} {d['name']}"):
            return d
    subs = [d for d in dims
            if t and (t in d["name"] or t in f"{d['id']} {d['name']}" or d["id"] in t)]
    return subs[0] if len(subs) == 1 else None


def load_s1_records(paths, dims):
    """载入 S1 终判 JSON → (按维度记录集, 按维度平台演化标记, 错误列表)。

    schema 违反 / 维度无法唯一归属 → 记入错误（main 转退出码 1），不降级不静默。
    """
    by_dim, evo, errors = {}, {}, []
    known = "、".join(f"{d['id']} {d['name']}" for d in dims)
    for seq, path in enumerate(paths):
        origin = str(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            errors.append(f"{origin}：JSON 读取/解析失败——{exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{origin}：顶层应为 JSON 对象（dict）")
            continue
        strategy = str(data.get("strategy_id") or data.get("strategy") or "").strip()
        dimension = str(data.get("dimension") or "").strip()
        date = str(data.get("date") or data.get("first_run_date") or "").strip()
        verdict = str(data.get("verdict") or "").strip()
        if not strategy:
            errors.append(f"{origin}：缺必备字段 strategy_id（S1 终判记录 schema）")
            continue
        if not dimension:
            errors.append(f"{origin}：缺必备字段 dimension")
            continue
        if not date or not DATE_RE.match(date):
            errors.append(f"{origin}：date「{date}」缺失或不合规（应 YYYY-MM-DD）")
            continue
        if not verdict:
            errors.append(f"{origin}：缺必备字段 verdict")
            continue
        dim = match_dimension(dimension, dims)
        if dim is None:
            errors.append(
                f"{origin}：dimension「{dimension}」无法唯一归属到档案维度（已知：{known}）")
            continue
        evidence = str(data.get("evidence") or "").strip() or "（未附证据指针）"
        by_dim.setdefault(dim["id"], []).append(
            make_record(strategy, date, verdict, evidence, seq=seq, origin=origin))
        if data.get("platform_evolution"):
            evo[dim["id"]] = True
    return by_dim, evo, errors


# ---------- 连续对齐计数（首跑口径 + 重置规则） ----------

def compute_streak(records):
    """→ (N, 当前连续段合格记录, 重置事件列表)。

    口径操作化：按策略 id 归并——终判（最新记录）合格 → 在其**最早合格记录**时点计 +1
    （同策略 id 只计首次合格，重跑/寻优不重复计数）；终判非合格（含立新案）→ 该时点
    计数清零重计。事件按（日期, 序号）时间线排序后重放。
    """
    by_strat = {}
    for r in records:
        by_strat.setdefault(r["strategy"], []).append(r)
    events = []
    for rs in by_strat.values():
        rs = sorted(rs, key=lambda r: (r["date"], r["seq"]))
        terminal = rs[-1]
        if terminal["qualified"]:
            first_q = next(r for r in rs if r["qualified"])
            events.append(first_q)
        else:
            events.append(terminal)
    events.sort(key=lambda r: (r["date"], r["seq"]))
    n, streak, resets = 0, [], []
    for rec in events:
        if rec["qualified"]:
            n += 1
            streak.append(rec)
        else:
            n, streak = 0, []
            resets.append(rec)
    return n, streak, resets


# ---------- CLI ----------

def _parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="check_dimension_alignment.py",
        description="G-2 维度对齐收敛判据机检（只读·只提示·不自动宣布；宣布=用户确认动作）",
        epilog=(f"退出码：0=无提示；3=有候选提示或维护态告警；1=异常/解析失败。"
                f"数据源：S1 JSON 优先（显式参数或缺省扫 output 树下 alignment/*.json），"
                f"零 JSON 时回退档案要素③人工清单（输出显式标注，不静默）。"
                f"N 阈值={N_THRESHOLD}（模块常量）。本脚本零文件写入。"))
    ap.add_argument("--archive",
                    help="维度档案路径（缺省 knowledge/contracts/dimension-alignment.md）")
    ap.add_argument("--alignment-dir",
                    help="S1 终判 JSON 显式路径（目录或单文件；目录扫 *.json；优先于 --local）")
    ap.add_argument("--local",
                    help="回测产物目录（兼容 align_diff_report.py --local：扫 <dir>/alignment/*.json）")
    return ap.parse_args(argv)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):        # Windows 控制台编码兼容（只调错误策略，零写入）
        try:
            sys.stdout.reconfigure(errors="replace")
            sys.stderr.reconfigure(errors="replace")
        except Exception:
            pass
    args = _parse_args(argv)
    archive_path = Path(args.archive) if args.archive else ARCHIVE_DEFAULT
    try:
        dims = parse_archive(archive_path.read_text(encoding="utf-8-sig"), str(archive_path))
    except (OSError, ArchiveFormatError) as exc:
        print(f"[异常·退出码1] 维度档案解析失败：{exc}", file=sys.stderr)
        return 1

    json_paths, discover_desc = discover_s1_json_paths(args.alignment_dir, args.local)
    if json_paths:
        by_dim, json_evo, errors = load_s1_records(json_paths, dims)
        if errors:
            for e in errors:
                print(f"[异常·退出码1] {e}", file=sys.stderr)
            return 1
        source = SOURCE_JSON
        src_note = f"S1 JSON 发现路径: {discover_desc}（命中 {len(json_paths)} 个）"
        src_tag = f"数据源={source}"
    else:
        by_dim = {d["id"]: d["records"] for d in dims}
        json_evo = {}
        source = SOURCE_MANUAL
        src_note = f"S1 JSON 发现路径: {discover_desc}（命中 0 个 → 回退档案要素③人工清单）"
        src_tag = (f"数据源={source}"
                   f"（降级语义：磁盘无 S1 JSON 持久化产物，消费档案要素③固定表格）")

    out = ["== 维度对齐收敛机检（G-2 · 只读 · 只提示 · 不自动宣布） ==",
           f"档案: {archive_path}",
           src_note,
           src_tag,
           ""]
    hints_cand = hints_maint = 0
    for d in dims:
        records = by_dim.get(d["id"], [])
        n, streak, resets = compute_streak(records)
        status_label = "已宣布·维护态" if d["status"] == "announced" else "候选"
        out.append(f"[{d['id']}] {d['name']}")
        out.append(f"  宣布状态: {status_label}")
        out.append(f"  连续对齐计数: N={n}（阈值 {N_THRESHOLD}）")
        if streak:
            out.append("  当前连续段首跑合格记录（同策略 id 只计首次合格；重跑/寻优不重复计数）:")
            for r in streak:
                out.append(f"    - {r['strategy']} | {r['date']} | {r['verdict_text']} | {r['evidence']}")
        else:
            out.append("  当前连续段首跑合格记录: （空——尚无合格首跑，或已被非合格终判/立新案重置清零）")
        for r in resets:
            out.append(f"  · 连续重置点: {r['strategy']} | {r['date']} | {r['verdict_text']}"
                       "（终判非合格/立新案 → 计数清零重计）")
        newcases = [r for r in records if r["kind"] == NEWCASE_KIND]
        if d["status"] == "announced":
            reasons = []
            if newcases:
                reasons.append("新案行：" + "；".join(f"{r['strategy']}@{r['date']}" for r in newcases))
            if d["evolution"]:
                reasons.append("档案要素⑤平台演化响应记录非空")
            if json_evo.get(d["id"]):
                reasons.append("S1 JSON platform_evolution 标记")
            if reasons:
                out.append("  ⚠ 维护态告警: 已宣布维度出现新案/平台演化标记 → 需 re-aligned 复验"
                           f"（{'；'.join(reasons)}）")
                hints_maint += 1
        elif n >= N_THRESHOLD:
            out.append(f"  ⚠ 候选提示: 连续 N={n} ≥ 阈值 {N_THRESHOLD} → 可宣布该维度对齐（待用户确认）")
            hints_cand += 1
        out.append("")

    code = 3 if (hints_cand or hints_maint) else 0
    kind = "有候选或告警" if code == 3 else "无提示"
    out.append(f"汇总: 维度 {len(dims)} 个｜候选提示 {hints_cand}｜维护态告警 {hints_maint}｜"
               f"数据源={source}｜退出码 {code}（{kind}）")
    print("\n".join(out))
    return code


if __name__ == "__main__":
    sys.exit(main())
