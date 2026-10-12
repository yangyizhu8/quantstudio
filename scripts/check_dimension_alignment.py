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

收敛台账面（loop-ri-ni 件③，docs/loop-ri-ni-plan.md rev2；轮次口径=2026-10-12 用户裁定 A）：
  轮=案闭环（一次修复迭代），S1 对账跑=观测点非修复轮；N_i/R_i 锚定案闭环行，
  非闭环行留「—」不参与序列；「暂无≠0」（不 coerce 为 0，序列自首个有效记录起算）。
  ① 格式校验前置：S4 台账主表（knowledge/alignment-convergence.md）另立 10 列契约
    （不复用维度档案 TABLE_COLUMNS，P2-7）——表头逐列全等（含 N_i/R_i）；R_i 单元格
    文法 0 或 <n>(<案号>[|<归因>])（归因∈{缺条目, 回哺链断裂}）；N_i 纯数字/—/暂无；
    不合规 → 退出码 1（stderr 报行号）；
  ② 收敛达成提示：某策略案闭环序列连续 3 轮 R_i=0 且 N_i 单调不增且末轮 N_i=0；
  ③ 流程违规提示：任一轮 R_i>0 → 同坑复发，须先归因（知识库缺条目 or 回哺链断裂）
    并先修管线再修漏洞（回显该轮案号与归因字段）；
  ④ 台账面退出码：违规→3；收敛达成/序列不足→0；格式不合规→1；与维度宣布面综合
    取最高（1>3>0）。既有「维度对齐」输出段逐字不变（新判据以新增段追加输出）。

只读保证：本脚本零文件写入（台账/档案/产物均不动；可 grep 核验无写入路径，
对应测试 tests/test_check_dimension_alignment.py::test_read_only_no_write_paths）。

用法：
  python scripts/check_dimension_alignment.py [--archive <档案.md>]
                                              [--alignment-dir <目录|文件>]
                                              [--local <回测产物目录>]
                                              [--ledger <收敛台账.md>]
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

# 收敛台账面（loop-ri-ni 件③）：S4 台账主表另立 10 列契约（P2-7：不复用 TABLE_COLUMNS）
LEDGER_TABLE_COLUMNS = ("策略", "口径", "日期", "残差（末日）", "分歧组",
                        "verdict", "S3 路由", "产物", "N_i", "R_i")
CONV_ROUNDS = 3                                    # 收敛判据轮数：连续 3 轮（铁律口径）
NI_CELL_RE = re.compile(r"^(?:—|暂无|[0-9]+)$")     # N_i 单元格文法（纯数字/—/暂无）
RI_CELL_RE = re.compile(                           # R_i 单元格文法（0 或 <n>(<案号>[|<归因>])）
    r"^(?:—|暂无|0|[1-9]\d*\([^()|]+(?:\|(?:缺条目|回哺链断裂))?\))$")

REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DEFAULT = REPO_ROOT / "knowledge" / "contracts" / "dimension-alignment.md"
PRODUCT_ROOT_DEFAULT = REPO_ROOT / "output"
LEDGER_DEFAULT = REPO_ROOT / "knowledge" / "alignment-convergence.md"

SOURCE_JSON = "S1 JSON"
SOURCE_MANUAL = "人工清单"


class ArchiveFormatError(Exception):
    """维度档案格式不合规（要素③表格/④宣布状态等）→ 退出码 1。"""


class LedgerFormatError(Exception):
    """收敛台账主表格式不合规（10 列契约/N_i·R_i 文法）→ 台账面退出码 1。"""


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


# ---------- 收敛台账面（loop-ri-ni 件③：R_i/N_i 复发·收敛判据，只读+只提示） ----------

def _table_blocks(text):
    """Markdown 表块切分：围栏外连续「|」开头行各成一块 → [(行号, 行), ...] 列表。"""
    blocks, cur, in_fence = [], [], False
    for idx, raw in enumerate(text.splitlines()):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("|"):
            cur.append((idx + 1, stripped))
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def _ledger_row_cells(line):
    """主表行拆分：R_i 文法允许内嵌一道管道（<案号>|<归因>）→ 11 段时回并末两段。"""
    cells = _table_cells(line)
    if len(cells) == len(LEDGER_TABLE_COLUMNS) + 1:
        cells = cells[:-2] + [cells[-2] + "|" + cells[-1]]
    return cells


def parse_ledger_rounds(text, origin):
    """解析 S4 台账主表（10 列契约）→ 行字典列表（strategy/date/n_raw/r_raw/line_no）。

    格式校验前置（先校验后统计）：表头逐列全等（含 N_i/R_i）；分隔行单元格形态合规
    （列数从宽——既有台账分隔行为 9 格历史形态，不据此判死）；数据行列数与
    N_i/R_i 单元格文法逐行校验，不合规抛 LedgerFormatError（含行号）。
    """
    target = list(LEDGER_TABLE_COLUMNS)
    header = None
    for block in _table_blocks(text):
        if _table_cells(block[0][1]) == target:
            header = block
            break
    if header is None:
        raise LedgerFormatError(
            f"{origin}：未找到收敛台账主表（表头须逐列全等："
            f"｜{'｜'.join(LEDGER_TABLE_COLUMNS)}｜）")
    if len(header) < 2:
        raise LedgerFormatError(f"{origin}:{header[0][0]}：主表缺分隔行")
    sep = _table_cells(header[1][1])
    if not sep or not all(SEP_CELL_RE.match(c) for c in sep):
        raise LedgerFormatError(f"{origin}:{header[1][0]}：主表分隔行不合规")
    rows = []
    for line_no, line in header[2:]:
        cells = _ledger_row_cells(line)
        where = f"{origin}:{line_no}"
        if len(cells) != len(LEDGER_TABLE_COLUMNS):
            raise LedgerFormatError(
                f"{where}：主表数据行列数 {len(cells)}（应 {len(LEDGER_TABLE_COLUMNS)}）")
        n_raw, r_raw = cells[-2], cells[-1]
        if not NI_CELL_RE.match(n_raw):
            raise LedgerFormatError(
                f"{where}：N_i 单元格「{n_raw}」不合规（允许纯数字/—/暂无）")
        if not RI_CELL_RE.match(r_raw):
            raise LedgerFormatError(
                f"{where}：R_i 单元格「{r_raw}」不合规（文法 0 或 "
                f"<n>(<案号>[|<归因>])，归因∈{{缺条目, 回哺链断裂}}；或 —/暂无）")
        rows.append({"strategy": cells[0], "date": cells[2],
                     "n_raw": n_raw, "r_raw": r_raw, "line_no": line_no})
    return rows


def parse_ri_cell(cell):
    """R_i 单元格 → (复发数, 案号, 归因)。文法已在 parse_ledger_rounds 前置校验。"""
    if cell in ("—", "暂无", "0"):
        return 0, None, None
    m = re.match(r"^([1-9]\d*)\(([^()|]+)(?:\|([^()|]+))?\)$", cell)
    return int(m.group(1)), m.group(2), m.group(3)


def build_rounds(rows):
    """同策略行序列 → (数值轮列表, 暂无跳过数)。

    口径 A（2026-10-12 用户裁定）：轮=案闭环——「—」行（非案闭环/观测点）不参与
    序列；「暂无≠0」：暂无行跳过且中断其后连续性（未知轮不得计入 R_i=0 连击）；
    序列自首个有效记录起算（前置「暂无」行不阻断起算、不被 coerce 为 0）。
    """
    rounds, zanwu, gap_pending, started = [], 0, False, False
    for row in rows:
        n_raw, r_raw = row["n_raw"], row["r_raw"]
        if n_raw == "—" or r_raw == "—":
            continue
        if n_raw == "暂无" or r_raw == "暂无":
            zanwu += 1
            if started:
                gap_pending = True
            continue
        rcount, case, attr = parse_ri_cell(r_raw)
        rounds.append({"n": int(n_raw), "r": rcount, "case": case, "attr": attr,
                       "r_raw": r_raw, "date": row["date"], "line_no": row["line_no"],
                       "contig": not gap_pending})
        started = True
        gap_pending = False
    return rounds, zanwu


def find_convergence_window(rounds):
    """末个满足判据的连续 CONV_ROUNDS 轮窗口：R_i 全 0 且 N_i 单调不增且末轮 N_i=0。

    连续性以 contig 标记判定（跨「暂无」缺口的窗口不算连续，防伪造连击）。
    """
    best = None
    for i in range(len(rounds) - CONV_ROUNDS + 1):
        win = rounds[i:i + CONV_ROUNDS]
        if not all(w["contig"] for w in win[1:]):
            continue
        if any(w["r"] != 0 for w in win):
            continue
        ns = [w["n"] for w in win]
        if all(ns[k + 1] <= ns[k] for k in range(len(ns) - 1)) and ns[-1] == 0:
            best = win
    return best


def run_ledger_face(ledger_path):
    """收敛台账面主流程 → (输出行列表, 台账面退出码)。

    只读只提示：R_i>0 违规→3；收敛达成/序列不足→0；台账缺失或格式不合规→1
    （stderr 报行号）。绝不写文件、绝不自动宣布（宣布/关单一律人工确认动作）。
    """
    try:
        text = ledger_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"[异常·退出码1] 收敛台账读取失败：{exc}", file=sys.stderr)
        return [], 1
    try:
        rows = parse_ledger_rounds(text, str(ledger_path))
    except LedgerFormatError as exc:
        print(f"[异常·退出码1] 收敛台账格式校验失败：{exc}", file=sys.stderr)
        return [], 1
    out = ["", "== 收敛台账 R_i/N_i 复发·收敛机检（loop-ri-ni 件③ · 只读 · 只提示） ==",
           f"台账: {ledger_path}"]
    by_strat = {}
    for row in rows:
        by_strat.setdefault(row["strategy"], []).append(row)
    if not by_strat:
        out.append("（主表无数据行——无有效轮次，序列不足不判收敛）")
    n_conv = n_viol = 0
    for strategy, srows in by_strat.items():
        rounds, zanwu = build_rounds(srows)
        out.append(f"[{strategy}] 案闭环有效轮次 {len(rounds)}"
                   "（轮=案闭环·2026-10-12 裁定 A；「—」非闭环行不参与；"
                   "「暂无≠0」自首个有效记录起算）")
        if zanwu:
            out.append(f"  · 跳过「暂无」轮 {zanwu} 行（暂无≠0，不计入序列、不计为 R_i=0）")
        if rounds:
            out.append("  N_i 序列: " + ",".join(str(r["n"]) for r in rounds))
            out.append("  R_i 序列: " + ",".join(str(r["r"]) for r in rounds))
            out.append("  轮明细: " + "｜".join(
                f"{r['date']} N={r['n']} R={r['r']}" for r in rounds))
        for r in rounds:
            if r["r"] > 0:
                n_viol += 1
                attr = f"归因={r['attr']}" if r["attr"] else "归因未附（须补归因落点）"
                out.append("  ⚠ 同坑复发：须先归因（知识库缺条目 or 回哺链断裂）"
                           "并先修管线再修漏洞")
                out.append(f"    · 轮（台账行{r['line_no']}，{r['date']}）: "
                           f"R_i={r['r_raw']} → 案号={r['case']}｜{attr}")
        window = find_convergence_window(rounds)
        if window:
            n_conv += 1
            wn = ",".join(str(r["n"]) for r in window)
            wr = ",".join(str(r["r"]) for r in window)
            out.append(f"  ✓ 收敛达成: 连续 {CONV_ROUNDS} 轮 R_i=0 且 N_i 单调不增"
                       f"且末轮 N_i=0（N={wn} / R={wr}）")
        elif len(rounds) < CONV_ROUNDS:
            out.append(f"  序列不足: 有效轮次 {len(rounds)} < {CONV_ROUNDS} → "
                       "不判收敛（不误报）")
        else:
            out.append("  未收敛: 有效轮次充足但判据未满足（连续 R_i=0／N_i 单调不增／"
                       "末轮 N_i=0 任一未达）")
    code = 3 if n_viol else 0
    out.append(f"汇总: 策略 {len(by_strat)} 个｜收敛达成 {n_conv}｜复发违规 {n_viol}｜"
               f"台账面退出码 {code}")
    return out, code


# ---------- CLI ----------

def _parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="check_dimension_alignment.py",
        description="G-2 维度对齐收敛判据机检（只读·只提示·不自动宣布；宣布=用户确认动作）",
        epilog=(f"退出码：0=无提示；3=有候选提示或维护态告警；1=异常/解析失败。"
                f"数据源：S1 JSON 优先（显式参数或缺省扫 output 树下 alignment/*.json），"
                f"零 JSON 时回退档案要素③人工清单（输出显式标注，不静默）。"
                f"N 阈值={N_THRESHOLD}（模块常量）。"
                f"收敛台账面（loop-ri-ni 件③）：R_i>0 违规→3；收敛达成/序列不足→0；"
                f"格式不合规→1；综合退出码两面取最高（1>3>0）。本脚本零文件写入。"))
    ap.add_argument("--archive",
                    help="维度档案路径（缺省 knowledge/contracts/dimension-alignment.md）")
    ap.add_argument("--alignment-dir",
                    help="S1 终判 JSON 显式路径（目录或单文件；目录扫 *.json；优先于 --local）")
    ap.add_argument("--local",
                    help="回测产物目录（兼容 align_diff_report.py --local：扫 <dir>/alignment/*.json）")
    ap.add_argument("--ledger",
                    help="S4 收敛台账路径（缺省 knowledge/alignment-convergence.md；"
                         "loop-ri-ni 件③ R_i/N_i 复发·收敛判据，只读只提示）")
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
    # ---- 收敛台账面（loop-ri-ni 件③）：以上既有「维度对齐」输出段逐字不变， ----
    # ---- 新判据（R_i/N_i 复发·收敛）以新增段追加；综合退出码两面取最高（1>3>0）。 ----
    ledger_path = Path(args.ledger) if args.ledger else LEDGER_DEFAULT
    ledger_out, ledger_code = run_ledger_face(ledger_path)
    if code == 1 or ledger_code == 1:
        combined = 1
    elif code == 3 or ledger_code == 3:
        combined = 3
    else:
        combined = 0
    ledger_out += ["", f"综合退出码: 维度面 {code}｜台账面 {ledger_code} → {combined}"
                       "（两面取最高 1>3>0）"]
    print("\n".join(ledger_out))
    return combined


if __name__ == "__main__":
    sys.exit(main())
