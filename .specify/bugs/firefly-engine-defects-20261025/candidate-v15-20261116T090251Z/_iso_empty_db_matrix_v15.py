# -*- coding: utf-8 -*-
"""SYN-20261116-01 rev2 · 空库判据合成验证矩阵（断言式，端到端）
用法: python _iso_empty_db_matrix_v15.py <EXPECT_VER> <WORK_ROOT>
退出码: 0=全 MATCH / 1=存在 DIFF 或 UNVERIFIED / 2=参数或前置缺失
       4=版本不符 / 5=B0 基线非 0（立即停止）/ 6=删除边界越界
       7=B0 基线不可探测（查询异常，立即停止）/ 8=PROBE hash 门拒绝（由 _prepare_probe.py 抛出）
写面: 仅 <WORK_ROOT>/runs/<RUN>/**（venv/脚本/日志见申请单 §3）
"""
import hashlib, json, os, shutil, subprocess, sys, time, uuid
import duckdb

EXPECT_VER = sys.argv[1] if len(sys.argv) > 1 else ""
WORK = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else ""
HERE = os.path.abspath(os.path.dirname(__file__))
PROBE = os.path.join(HERE, "restore_probe_v15.py")

def die(tag, msg, code):
    print(f"{tag}: {msg}"); print(f"EXIT {code}"); sys.exit(code)

# ---------- 前置与版本断言（V1）----------
if not EXPECT_VER: die("ARG", "缺少 EXPECT_VER(argv[1])", 2)
if not WORK or not os.path.isdir(WORK): die("ARG", f"WORK 根不存在: {WORK}", 2)
actual = duckdb.__version__
print(f"VER actual={actual} expect={EXPECT_VER}")
if actual != EXPECT_VER:
    die("VER_MISMATCH", "版本与目标不符，整轮中止（不出任何用例结论）", 4)
if not os.path.isfile(PROBE): die("PROBE", f"被测脚本缺失: {PROBE}", 2)

RUN = os.path.abspath(os.path.join(WORK, "runs",
        time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "_" + uuid.uuid4().hex[:4]))
os.makedirs(RUN, exist_ok=True)
PROBE_SHA = hashlib.sha256(open(PROBE, "rb").read()).hexdigest()
print(f"PROBE {PROBE} sha256={PROBE_SHA}")
print(f"RUN {RUN}")

# ---------- 子目标（V3：删除前核边界）----------
def sub(name):
    d = os.path.abspath(os.path.join(RUN, name))
    if not d.startswith(RUN + os.sep):                      # ①必须在 RUN 下
        die("SCOPE", f"子目标越界: {d}", 6)
    if d in (RUN, WORK):                                    # ②不得是 RUN / WORK
        die("SCOPE", f"拒绝删除根: {d}", 6)
    if os.path.isdir(d):
        shutil.rmtree(d)                                    # 仅删本 RUN 内的用例目录
    os.makedirs(d)
    return d

def mkdb(name, ddl=()):
    d = sub(name); p = os.path.join(d, "case.db")
    con = duckdb.connect(p)                                  # 只连本 RUN 下的新库
    try:
        for s in ddl: con.execute(s)
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return p

def surface(p):
    """诊断用：六源计数（断言不以它为准）。任一源异常向上抛 → 由 case() 记 UNVERIFIED。"""
    con = duckdb.connect(p, read_only=True)
    try:
        SYS = ('information_schema', 'pg_catalog')
        def q(sql): return [tuple(str(x) for x in r) for r in con.execute(sql).fetchall()]
        return {
            "tables_views": q("SELECT table_schema, table_name, table_type FROM information_schema.tables "
                              f"WHERE table_schema NOT IN {SYS}"),
            "sequences": q("SELECT schema_name, sequence_name FROM duckdb_sequences() "
                           f"WHERE schema_name NOT IN {SYS}"),
            "extra_schemas": q("SELECT schema_name FROM duckdb_schemas() "
                               "WHERE schema_name NOT IN ('main','information_schema','pg_catalog')"),
            "user_types": q("SELECT schema_name, type_name FROM duckdb_types() "
                            f"WHERE schema_name NOT IN {SYS} AND NOT internal"),
            "macros_scalar": q("SELECT schema_name, function_name FROM duckdb_functions() "
                               "WHERE function_type = 'macro' AND NOT internal "
                               "AND schema_name NOT IN "
                               "('information_schema','pg_catalog')"),
            "macros_table": q("SELECT schema_name, function_name FROM duckdb_functions() "
                              "WHERE function_type = 'table_macro' AND NOT internal "
                              "AND schema_name NOT IN "
                              "('information_schema','pg_catalog')"),
        }
    finally:
        con.close()

# ---------- 断言引擎（V1）----------
C = {"MATCH": 0, "DIFF": 0, "UNVERIFIED": 0}
R = []

def check(case, rc, out, expect_rc, must=(), must_not=()):
    missing = [m for m in must if m not in out]
    forbid = [m for m in must_not if m in out]
    if rc == expect_rc and not missing and not forbid:
        C["MATCH"] += 1; v = "MATCH"; det = ""
    else:
        C["DIFF"] += 1; v = "DIFF"
        det = f"rc={rc}(expect {expect_rc}) missing={missing} forbidden={forbid}"
    R.append({"case": case, "rc": rc, "expect_rc": expect_rc, "verdict": v, "detail": det})
    print(f"{case}: rc={rc} expect_rc={expect_rc} verdict={v}" + (f" {det}" if det else ""))
    return v == "MATCH"

def run_probe(case, dbp, csv_text, expect_rc, must=(), must_not=()):
    csv_p = os.path.join(os.path.dirname(dbp), case + ".csv")
    with open(csv_p, "w", encoding="utf-8", newline="\n") as f:
        f.write(csv_text)
    r = subprocess.run([sys.executable, PROBE, dbp, csv_p],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    return check(case, r.returncode, out, expect_rc, must, must_not)

def case(name, fn):
    try:
        fn()
    except SystemExit:
        raise
    except Exception as e:
        C["UNVERIFIED"] += 1
        R.append({"case": name, "verdict": "UNVERIFIED", "detail": f"{type(e).__name__}: {e}"})
        print(f"{name}: verdict=UNVERIFIED ({type(e).__name__}: {e})")

HDR = "table,rows,fp\n"

# ---------- B0 基线：非 0 或不可探测，都立即停本轮（V1′）----------
# 注意：B0 **不经过 case()** —— 通用 case() 会把 surface() 异常吞成 UNVERIFIED 并继续跑后续例，
#       而基线不成立时后续例无意义；故此处异常与非零**同权**，一律停并留 summary。
def b0_stop(reason, code, payload):
    with open(os.path.join(RUN, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"actual_ver": actual, "expect_ver": EXPECT_VER, "run": RUN,
                   "probe_sha256": PROBE_SHA, "stop_reason": reason,
                   "counts": C, "records": R, "b0": payload},
                  f, ensure_ascii=False, indent=2)
    print(f"SUMMARY_STOPPED reason={reason} match={C['MATCH']} "
          f"diff={C['DIFF']} unverified={C['UNVERIFIED']}")
    print(f"RUN {RUN}")
    print(f"EXIT {code}")
    sys.exit(code)

try:
    p_b0 = mkdb("B0")
    s_b0 = surface(p_b0)
except Exception as e:
    import traceback
    payload = {"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
    with open(os.path.join(RUN, "b0_surface_error.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"B0_UNPROBED {payload['error']}")
    print("B0_UNPROBED_BASELINE ⇒ 立即停止：基线不可探测时，后续用例的 PASS/FAIL 都不成立")
    b0_stop("B0_UNPROBED_BASELINE", 7, payload)

by = {k: len(v) for k, v in s_b0.items()}
total_b0 = sum(by.values())
print(f"B0_SURFACE by_source={by} total={total_b0}")
if total_b0 != 0:
    payload = {"by_source": by, "rows": {k: v[:50] for k, v in s_b0.items()}}
    with open(os.path.join(RUN, "b0_surface.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("B0_BASELINE_NONZERO ⇒ 立即停止：本次运行不产出用例结论，"
          "也不得在同一次运行内改过滤器重试（须人工改脚本 + 新 run）")
    b0_stop("B0_BASELINE_NONZERO", 5, payload)

C["MATCH"] += 1
R.append({"case": "B0", "verdict": "MATCH", "detail": "total=0"})
print("B0: total=0 verdict=MATCH")

# ---------- 10 个 probe 例：全部端到端（V2）；B0 已在上面单独完成，故合计 11 项 ----------
def c1():
    p = mkdb("C1"); run_probe("C1", p, "#EMPTY_DB\n" + HDR, 0,
        must=["PASS_EMPTY_DB", "scope=whole_db", "objects=0", "by_source=", "marker_line=1"],
        must_not=["FAIL("])
def c2():
    p = mkdb("C2"); run_probe("C2", p, HDR + "#EMPTY_DB\n", 0,
        must=["PASS_EMPTY_DB", "marker_line=2", "objects=0", "by_source="], must_not=["FAIL("])
def c7():
    # 与 C2 同口径：除「BOM 不产生假失败」外，还须核 objects=0 与 by_source（v14 rev2 只核了 marker_line）
    p = mkdb("C7"); run_probe("C7", p, "\ufeff" + HDR + "#EMPTY_DB\n", 0,
        must=["PASS_EMPTY_DB", "marker_line=2", "objects=0", "by_source="],
        must_not=["BAD_ROW", "FAIL("])
def c5c():
    p = mkdb("C5c", ["CREATE SEQUENCE s1"])
    run_probe("C5c", p, "#EMPTY_DB\n" + HDR, 2,
        must=["EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY", "objects=1", "'sequences': 1"])
def c5c2():
    p = mkdb("C5c2", ["CREATE SCHEMA sch2"])
    run_probe("C5c2", p, "#EMPTY_DB\n" + HDR, 2,
        must=["EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY", "objects=1", "'extra_schemas': 1"])
def c5c3():
    p = mkdb("C5c3", ["CREATE TYPE mood AS ENUM ('a','b')"])
    run_probe("C5c3", p, "#EMPTY_DB\n" + HDR, 2,
        must=["EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY", "objects=1", "'user_types': 1"])
def c5f():
    p = mkdb("C5f", ["CREATE MACRO m1(x) AS x + 1"])
    run_probe("C5f", p, "#EMPTY_DB\n" + HDR, 2,
        must=["EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY", "objects=1", "'macros_scalar': 1"])
def c5g():
    p = mkdb("C5g", ["CREATE MACRO tm1(x) AS TABLE SELECT x AS c"])
    run_probe("C5g", p, "#EMPTY_DB\n" + HDR, 2,
        must=["EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY", "objects=1", "'macros_table': 1"])
def c5e():
    p = mkdb("C5e", ["CREATE TABLE t1(i INT)"])
    run_probe("C5e", p, "#EMPTY_DB_MAIN_ONLY\n" + HDR, 2,
        must=["EMPTY_DB_MAIN_ONLY_CLAIM_BUT_MAIN_HAS_TABLES", "main.t1"])
def c5b():
    p = mkdb("C5b", ["CREATE SEQUENCE s1"])
    run_probe("C5b", p, "#EMPTY_DB_MAIN_ONLY\n" + HDR, 3,
        must=["PARTIAL_EMPTY_MAIN_ONLY", "main_base=0", "other_objects=1", "'sequences': 1"])

for nm, fn in (("C1", c1), ("C2", c2), ("C7", c7), ("C5c", c5c), ("C5c2", c5c2),
               ("C5c3", c5c3), ("C5f", c5f), ("C5g", c5g), ("C5e", c5e), ("C5b", c5b)):
    case(nm, fn)

# ---------- 汇总与退出码（V1）----------
total = sum(C.values())
print(f"SUMMARY total={total} match={C['MATCH']} diff={C['DIFF']} unverified={C['UNVERIFIED']}")
print(f"RUN {RUN}")
with open(os.path.join(RUN, "summary.json"), "w", encoding="utf-8") as f:
    json.dump({"actual_ver": actual, "expect_ver": EXPECT_VER, "run": RUN,
               "probe_sha256": PROBE_SHA, "counts": C, "records": R},
              f, ensure_ascii=False, indent=2)
code = 0 if (C["DIFF"] == 0 and C["UNVERIFIED"] == 0 and total == 11) else 1
print(f"EXIT {code}")
sys.exit(code)
