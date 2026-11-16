# -*- coding: utf-8 -*-
"""LAR-20261116-01 提取器：核已审源 → 原样提取被测件(A—E 整段) → 原样提取 rev4 矩阵脚本。
门 1 源 sha256 == 冻结值；门 2 已存在件 sha 不同即拒；门 3 打印身份供两版互核。
不手改任何被提取内容（逐字节写出）。
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
def _find_workspace_root(start):
    cur = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(cur, ".specify")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return ""
        cur = parent


ROOT = _find_workspace_root(HERE)          # r4：向上锚定 .specify（原 3 层推导在候选子目录内落错层）
if not ROOT:
    print("PREPARE FAIL(WORKSPACE_ROOT_NOT_FOUND) HERE=" + HERE)
    sys.exit(8)

SRC_PROC = os.path.join(ROOT, "docs", "handoff", "firefly-site-recovery-procedure-20261114.md")
SRC_REQ = os.path.join(ROOT, "docs", "evidence", "firefly-synthetic-validation-request-20261116.md")
OUT_PROBE = os.path.join(HERE, "restore_probe_v15.py")
OUT_MATRIX = os.path.join(HERE, "_iso_empty_db_matrix_v15.py")
LOG = os.path.join(HERE, "_iso_prepare_probe_v15.txt")

PROC_SHA = "7EBDE2EE60C6D387F2C9B7D2F5FCAA138FAEDFFC959F2750822386888CB06702"
PROC_BYTES = 90003
FENCE_FIRST = 115          # 1-based: ```python
FENCE_LAST = 277           # 1-based: ``` 结束
BODY_LINES = 161
# 体序号（围栏体第 N 行，1-based，不含 ``` 行）；对应文件绝对行 L132/L174/L180/L188/L251
STAGES = (("A", 17), ("B", 59), ("C", 65), ("D", 73), ("E", 136))
REQ_SHA = "A02CBD552539AE17E6B12E7E1FA7FFA8DB0B80514BED0E9573ADD54800579A6B"
REQ_BYTES = 32392

log_lines = []


def emit(s):
    print(s)
    log_lines.append(s)


def flush_log():
    with io.open(LOG, "a", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(log_lines) + "\n")


def die(code, msg):
    emit(f"PREPARE FAIL({msg})")
    flush_log()
    print(f"EXIT {code}")
    sys.exit(code)


def _indent_of(line):
    return line[: len(line) - len(line.lstrip())]


def patch_probe(lines):
    """v15：probe 两条宏源加 NOT internal（v14 形：单行合并）。list -> (list, hits)"""
    hits, out = [], []
    for ln in lines:
        done = False
        for ft in ("macro", "table_macro"):
            old = ('"WHERE function_type = \'%s\' AND schema_name NOT IN '
                   '(\'information_schema\',\'pg_catalog\')")' % ft)
            if old in ln:
                ind = _indent_of(ln)
                out.append(ln.replace(old, '"WHERE function_type = \'%s\' AND NOT internal "' % ft))
                out.append(ind + '"AND schema_name NOT IN (\'information_schema\',\'pg_catalog\')")')
                hits.append(("probe", ft))
                done = True
                break
        if not done:
            out.append(ln)
    return out, hits


def patch_matrix(lines):
    """v15：矩阵 surface() 两条宏源加 NOT internal（v14 形：跨行拼接）。list -> (list, hits)"""
    hits, out, i = [], [], 0
    while i < len(lines):
        ln = lines[i]
        m = re.search(r'"WHERE function_type = \'(macro|table_macro)\' AND schema_name NOT IN "$', ln)
        if m and i + 1 < len(lines) and "('information_schema','pg_catalog')" in lines[i + 1]:
            ft = m.group(1)
            ind = _indent_of(ln)
            out.append(ln.replace('AND schema_name NOT IN "', 'AND NOT internal "'))
            out.append(ind + '"AND schema_name NOT IN "')
            hits.append(("matrix", ft))
            i += 1
            continue
        out.append(ln)
        i += 1
    return out, hits


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest().upper()


def main():
    # ---------- 门 1：源文件身份 ----------
    if not os.path.isfile(SRC_PROC):
        die(8, f"SOURCE_NOT_FOUND {SRC_PROC}")
    raw = open(SRC_PROC, "rb").read()
    got_sha, got_len = sha_bytes(raw), len(raw)
    emit(f"SRC path={SRC_PROC}")
    emit(f"SRC bytes={got_len} sha256={got_sha}")
    emit(f"SRC frozen_bytes={PROC_BYTES} frozen_sha256={PROC_SHA}")
    if got_sha != PROC_SHA or got_len != PROC_BYTES:
        die(8, f"SOURCE_IDENTITY_MISMATCH(actual={got_sha}/{got_len})")

    text = raw.decode("utf-8-sig")
    lines = text.split("\n")
    emit(f"SRC total_lines={len(lines)}")

    # ---------- 门 1b：围栏范围 ----------
    if FENCE_FIRST - 1 >= len(lines) or not lines[FENCE_FIRST - 1].startswith("```python"):
        die(8, f"FENCE_START_MOVED(line {FENCE_FIRST})")
    if FENCE_LAST - 1 >= len(lines) or lines[FENCE_LAST - 1].strip() != "```":
        die(8, f"FENCE_END_MOVED(line {FENCE_LAST})")
    body = lines[FENCE_FIRST:FENCE_LAST - 1]
    emit(f"FENCE start=L{FENCE_FIRST} end=L{FENCE_LAST} body_lines={len(body)} frozen_body={BODY_LINES}")
    if len(body) != BODY_LINES:
        die(8, f"FENCE_BODY_LINES_MISMATCH({len(body)})")

    # ---------- 门 1c：五段序位 ----------
    for tag, off in STAGES:
        if off - 1 >= len(body):
            die(8, f"STAGE_{tag}_OUT_OF_RANGE")
        head = body[off - 1]
        if "阶段" not in head or tag not in head:
            die(8, f"STAGE_{tag}_MISMATCH(L{off}: {head[:40]})")
        emit(f"STAGE {tag} body_line={off} ok")

    # ---------- 门 2：已存在件 ----------
    body, hits = patch_probe(body)
    if sorted(hits) != [('probe', 'macro'), ('probe', 'table_macro')]:
        die(8, f'PROBE_PATCH_HITS={hits!r}')
    emit(f'PROBE_PATCH_HITS={len(hits)}')
    payload = ("\n".join(body) + "\n").encode("utf-8")
    new_sha = sha_bytes(payload)
    if os.path.isfile(OUT_PROBE):
        old_sha = sha_bytes(open(OUT_PROBE, "rb").read())
        emit(f"PROBE existing sha256={old_sha}")
        if old_sha != new_sha:
            die(8, f"PROBE_EXISTS_DIFFERENT(existing={old_sha[:12]} new={new_sha[:12]})")
        emit("PROBE exists and identical -> keep as is")
    else:
        with open(OUT_PROBE, "wb") as f:
            f.write(payload)
        emit(f"PROBE written {OUT_PROBE}")

    emit(f"PROBE_SHA256={new_sha}")
    emit(f"PROBE_LINES={len(body)}")
    emit(f"PROBE_SRC={os.path.relpath(SRC_PROC, ROOT).replace(os.sep, '/')}#L{FENCE_FIRST}-L{FENCE_LAST}")

    # ---------- 矩阵脚本：rev4 原样提取 ----------
    if not os.path.isfile(SRC_REQ):
        die(8, f"REQ_NOT_FOUND {SRC_REQ}")
    rraw = open(SRC_REQ, "rb").read()
    rsha, rlen = sha_bytes(rraw), len(rraw)
    emit(f"REQ bytes={rlen} sha256={rsha} frozen={REQ_BYTES}/{REQ_SHA}")
    if rsha != REQ_SHA or rlen != REQ_BYTES:
        die(8, f"REQ_IDENTITY_MISMATCH(actual={rsha}/{rlen})")
    rlines = rraw.decode("utf-8").split("\n")
    h = next((i for i, ln in enumerate(rlines) if ln.startswith("## 5. 脚本全文")), None)
    if h is None:
        die(8, "MATRIX_SECTION_NOT_FOUND")
    s = next((i for i in range(h, len(rlines)) if rlines[i].startswith("```python")), None)
    e = next((i for i in range(s + 1, len(rlines)) if rlines[i].strip() == "```"), None) if s is not None else None
    if s is None or e is None:
        die(8, "MATRIX_FENCE_NOT_FOUND")
    mbody, mhits = patch_matrix(rlines[s + 1:e])
    if sorted(mhits) != [('matrix', 'macro'), ('matrix', 'table_macro')]:
        die(8, f'MATRIX_PATCH_HITS={mhits!r}')
    emit(f'MATRIX_PATCH_HITS={len(mhits)}')
    mext = ("\n".join(mbody) + "\n").encode("utf-8")
    emit(f"MATRIX fence=L{s+1}-L{e+1} body_lines={len(mbody)} sha256={sha_bytes(mext)}")
    if os.path.isfile(OUT_MATRIX):
        old = sha_bytes(open(OUT_MATRIX, "rb").read())
        if old != sha_bytes(mext):
            die(8, f"MATRIX_EXISTS_DIFFERENT(existing={old[:12]})")
        emit("MATRIX exists and identical -> keep as is")
    else:
        with open(OUT_MATRIX, "wb") as f:
            f.write(mext)
        emit(f"MATRIX written {OUT_MATRIX}")
    emit(f"MATRIX_SHA256={sha_bytes(mext)}")
    emit(f"MATRIX_LINES={len(mbody)}")
    emit("PREPARE OK")
    flush_log()
    print("EXIT 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
