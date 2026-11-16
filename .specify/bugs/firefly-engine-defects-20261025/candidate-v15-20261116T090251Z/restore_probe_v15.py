# 隔离恢复验证（主路径 v12）——只打开「备份的隔离复制品」；原备份路径**不传入任何 connect**
# 用法：python restore_probe.py <probe_copy.db> <stop_time_fingerprints.csv>
import csv, os, re, sys, duckdb

probe, frozen_csv = sys.argv[1], sys.argv[2]
SYS_SCHEMAS = ("information_schema", "pg_catalog")

def die(msg):
    print(f"RESTORE_PROBE FAIL({msg})"); sys.exit(2)

def fp(con, t):
    q = ("SELECT count(*)::BIGINT, coalesce(md5(string_agg(h,'' ORDER BY h)),'EMPTY') "
         f'FROM (SELECT md5(CAST(t AS VARCHAR)) AS h FROM "{t}" AS t)')
    n, h = con.execute(q).fetchone()
    return int(n), h

# ============ 阶段 A：文字层解析（先摘标记；BOM 只去一次并**复用同一份 header**）============
try:
    raw = open(frozen_csv, encoding="utf-8-sig").read()   # ← utf-8-sig：读入即去 BOM（无 BOM 也安全）
except FileNotFoundError:
    die("FROZEN_CSV_NOT_FOUND")
lines = raw.splitlines()

marker_lines = [i + 1 for i, ln in enumerate(lines)
                if ln.strip().startswith("#") and "EMPTY_DB" in ln]
if len(marker_lines) > 1:
    die(f"MULTIPLE_EMPTY_DB_MARKERS{marker_lines}")
empty_declared = bool(marker_lines)
scope = "whole_db"
if empty_declared and "MAIN_ONLY" in lines[marker_lines[0] - 1]:
    scope = "main_base_only"
if empty_declared and re.search(r"scope\s*=\s*main", lines[marker_lines[0] - 1]):
    scope = "main_base_only"

body = [ln for i, ln in enumerate(lines) if (i + 1) not in marker_lines]
if not body or not body[0].strip():
    die("BAD_HEADER(empty)")

header = [c.strip().lstrip("\ufeff") for c in body[0].strip().split(",")]
if header != ["table", "rows", "fp"]:
    die(f"BAD_HEADER({header})")

# ⚠ 关键：DictReader 必须显式拿**同一份** header 作 fieldnames，不能再让它去读原始首行
frozen = {}
for ln_no, r in enumerate(csv.DictReader(body[1:], fieldnames=header), start=2):
    t = (r.get("table") or "").strip().lstrip("\ufeff")
    n = (r.get("rows") or "").strip()
    h = (r.get("fp") or "").strip()
    if not t or not n or not h:
        die(f"BAD_ROW(line={ln_no})")
    if not re.fullmatch(r"\d+", n):
        die(f"BAD_ROWS(line={ln_no}, value={n!r})")
    if not (re.fullmatch(r"[0-9a-f]{32}", h) or h == "EMPTY"):
        die(f"BAD_FP(line={ln_no}, value={h[:16]!r})")
    if t in frozen:
        die(f"DUPLICATE_TABLE({t})")
    frozen[t] = (int(n), h)

# ============ 阶段 B：对象范围（标记与数据行互斥；空清单≠空库）============
if empty_declared and frozen:
    die(f"EMPTY_DB_WITH_ROWS({len(frozen)})")
if not empty_declared and not frozen:
    die("EMPTY_FROZEN_LIST")

# ============ 阶段 C：副本必须存在且可开（空库分支也不得跳过）============
if not os.path.exists(probe):
    die("PROBE_NOT_FOUND")
try:
    con = duckdb.connect(probe, read_only=True)
except Exception as e:
    die(f"PROBE_OPEN_FAILED({type(e).__name__})")

# ============ 阶段 D：空库判定——**五源全对象面**取证 ============
# 声明范围（缺一不可；information_schema.tables **不含 sequence**）：
#   ①表与视图  ②序列  ③非默认 schema  ④用户类型  ⑤scalar macro  ⑥table macro
#   （索引/约束依附于表，不单列为来源；表为空则它们不存在）
# 任取一源探测失败 ⇒ EMPTY_DB_SCOPE_UNVERIFIED（**拒绝未覆盖状态**，不猜「大概没有」）
def probe_surface(con):
    surf = {}
    def q(src, sql):
        try:
            surf[src] = [tuple(str(x) for x in r) for r in con.execute(sql).fetchall()]
        except Exception as e:
            die(f"EMPTY_DB_SCOPE_UNVERIFIED({src}: {type(e).__name__})")
    q("tables_views",
      "SELECT table_schema, table_name, table_type FROM information_schema.tables "
      f"WHERE table_schema NOT IN {SYS_SCHEMAS}")
    q("sequences",
      "SELECT schema_name, sequence_name FROM duckdb_sequences() "
      f"WHERE schema_name NOT IN {SYS_SCHEMAS}")
    q("extra_schemas",
      "SELECT schema_name FROM duckdb_schemas() "
      "WHERE schema_name NOT IN ('main','information_schema','pg_catalog')")
    q("user_types",
      "SELECT schema_name, type_name FROM duckdb_types() "
      f"WHERE schema_name NOT IN {SYS_SCHEMAS} AND NOT internal")
    q("macros_scalar",      # scalar macro
      "SELECT schema_name, function_name FROM duckdb_functions() "
      "WHERE function_type = 'macro' AND NOT internal "
      "AND schema_name NOT IN ('information_schema','pg_catalog')")
    q("macros_table",       # table macro（v13 只筛 'macro'，漏掉这一类）
      "SELECT schema_name, function_name FROM duckdb_functions() "
      "WHERE function_type = 'table_macro' AND NOT internal "
      "AND schema_name NOT IN ('information_schema','pg_catalog')")
    return surf

surf = probe_surface(con)
by_source = {k: len(v) for k, v in surf.items()}
total_objects = sum(by_source.values())
main_base = [o for o in surf["tables_views"] if o[0] == "main" and o[2].upper() == "BASE TABLE"]

if empty_declared:
    # ① 整库声明：五源合计必须为 0 —— **有任何对象即 FAIL，不降级**
    if scope == "whole_db":
        if total_objects:
            nonempty = {k: v for k, v in by_source.items() if v}
            die(f"EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY(objects={total_objects}, by_source={nonempty})")
        con.close()
        print(f"RESTORE_PROBE PASS_EMPTY_DB(marker_line={marker_lines[0]}, scope=whole_db, "
              f"objects=0, by_source={by_source})")
        sys.exit(0)

    # ② main_only 声明：**先核 main 基表确实为空**，再报 main_base=0（不得恒打印）
    if main_base:
        die(f"EMPTY_DB_MAIN_ONLY_CLAIM_BUT_MAIN_HAS_TABLES({len(main_base)}, "
            f"e.g. {[f'{s}.{t}' for s, t, _ in main_base[:8]]})")
    others = total_objects - len(main_base)          # 其它源 + main 的非基表对象
    if others == 0:
        con.close()
        print(f"RESTORE_PROBE PASS_EMPTY_DB(marker_line={marker_lines[0]}, "
              f"scope=main_base_only, objects=0, by_source={by_source})")
        sys.exit(0)
    con.close()
    print(f"RESTORE_PROBE PARTIAL_EMPTY_MAIN_ONLY(main_base=0, other_objects={others}, "
          f"by_source={ {k: v for k, v in by_source.items() if v} })")
    sys.exit(3)

# ============ 阶段 E：逐表核对与分级（清单范围 = main 基表，与阶段 1.4 一致）============
copy_tabs = {o[1] for o in main_base}
unproven = sorted({r[0] for r in con.execute(
    "SELECT table_name FROM information_schema.columns "
    "WHERE table_schema='main' AND upper(data_type) IN ('DOUBLE','REAL','FLOAT')").fetchall()})
missing = sorted(set(frozen) - copy_tabs)
extra = sorted(copy_tabs - set(frozen))
if missing: print(f"MISSING_IN_COPY: {missing}")
if extra:   print(f"EXTRA_IN_COPY: {extra}")
bad = []
for t, (n0, h0) in frozen.items():
    if t not in copy_tabs:
        bad.append(t); continue
    n1, h1 = fp(con, t)
    ok = (n1 == n0 and h1 == h0)
    print(f"{t}: rows {n0}->{n1} | fp {'MATCH' if ok else 'DIFF'} | frozen {h0[:12]} vs copy {h1[:12]}")
    if not ok: bad.append(t)
con.close()

print(f"UNPROVEN_TABLES: {unproven}")
if missing or extra or bad:
    die(f"MISMATCH missing={len(missing)} extra={len(extra)} bad={len(bad)}")
if unproven:
    print(f"RESTORE_PROBE PARTIAL_UNPROVEN({len(unproven)}: {','.join(unproven)})")
else:
    print("RESTORE_PROBE PASS")
