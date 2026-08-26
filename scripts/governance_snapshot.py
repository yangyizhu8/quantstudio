# -*- coding: utf-8 -*-
"""数据快照版本机制 CLI（治理方案第 3B 步，设计 docs/governance-snapshot-design.md v3+）

子命令：
  create  [--source-task <id>]   创建快照（四阶段全在锁协议下，三重源校验，原子提交）
  verify  <SNAP_ID>              重算逻辑 hash 对照 manifest（流式）
  list                          列出 index.json
  prune   [--keep 3]             滚动保留（基线引用保护）
  bind    <SNAP_ID> <result_dir> 外挂 snapshot_meta.json（不改框架产物）
  unprotect <ID> --reason <text> 用户批准的受控解除保护（写审计日志；禁止普通 --force）

退出码：2=写锁被持 3=磁盘不足 4=一致性失败 5=registry 准入未过
       6=数据侧任务窗口守卫拒绝（H3 错峰调度，2026-08-19）

数据侧任务窗口守卫（2026-08-19 H3）：
  create/verify 启动前 fail-closed 检查（任一命中拒绝启动，退出码 6，写 guard_refused.log）：
    ① 数据侧任务进程在跑（ETL/云同步/repair/补拉/巡检等，见 DATA_SIDE_PATTERNS）
    ② 物理可用内存 < 10GB（verify 峰值提交 ~25GB，须独占内存窗口）
    ③ 周一~五 09:15~15:05（盘中实盘框架运行，禁止重载；节假日误拒可接受）
  运行中逐表让路：hash 计算期间数据侧任务启动 → GuardAbort 安全中止
  （verify 幂等可重跑；create 走既有 finally 清理半成品）。
  背景：03:28 / 10:00 两次 QuestDB 崩溃均与 verify 同数据侧任务并发直接相关。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAP_DIR = ROOT / "data" / "snapshots"
INDEX = SNAP_DIR / "index.json"
SORT_KEYS = SNAP_DIR / "sort_keys.json"
REGISTRY = SNAP_DIR / "write_path_registry.json"
UNPROTECT_LOG = SNAP_DIR / "unprotect.log"
PROTECT_LOG = SNAP_DIR / "protect.log"
PROTECT_JOURNAL = SNAP_DIR / "protect.pending.json"
AUX_DB = ROOT / "data" / "qfq_aux.db"
MAIN_DB = ROOT / "data" / "quantstudio.db"
DATA_CONFIG = ROOT / "config" / "data_config.json"

BJ_TZ = timezone(timedelta(hours=8))
NULL_SENTINEL = "\\N"
SEP_COL = "\x1f"
SEP_ROW = "\n"
BATCH_ROWS = 8192  # 审计后内存修正：口径不变，仅减小 Arrow 流式批次
DISK_FORMULA_FACTOR = 1.05  # 预估系数（设计 §6）
KEEP_DEFAULT = 3


# ---------------- canonical 编码（设计 §4，fixtures 固化） ----------------

def encode_value(v):
    if v is None:
        return NULL_SENTINEL
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v != v:
            return "NaN"
        if v == float("inf"):
            return "Inf"
        if v == float("-inf"):
            return "-Inf"
        return repr(v)  # 含 -0.0 区分
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "0x" + bytes(v).hex()
    s = str(v)
    # VARCHAR 转义：控制字符（含两个分隔符）→ \uXX；反斜杠自身 → \\
    out = []
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif o < 0x20 or o == 0x7F:
            out.append("\\u%02x" % o)
        else:
            out.append(ch)
    return "".join(out)


def encode_row(row):
    return SEP_COL.join(encode_value(v) for v in row)


def file_sha256_stream(path: Path, chunk_bytes: int = 8 * 1024 * 1024) -> str:
    """文件 SHA256 分块流式计算（3B 审计修正：禁止 26GB 一次性 read）。"""
    h = hashlib.sha256()
    with io.open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_bytes)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---------------- 18 表流式逻辑 hash ----------------

def _sort_key_expr(table: str, keys_cfg: dict) -> str:
    spec = keys_cfg["tables"].get(table)
    if spec is None:
        raise ValueError(f"sort_keys.json 缺表: {table}")
    if spec.startswith("__FULL_COLUMN__"):
        return None  # 调用方处理全列
    return spec


SHARD_ROW_TARGET = 5_000_000  # 超过此行数的表启用分片


def _compute_shard_boundaries(conn, table: str, key_col: str) -> list:
    """按第一列累计行数等量切分。返回边界值列表（不含 NULL——O3 跳过，NULL 归最后片）。"""
    rows = conn.execute(f'''
        SELECT "{key_col}", COUNT(*) as n
        FROM "{table}"
        WHERE "{key_col}" IS NOT NULL
        GROUP BY 1 ORDER BY 1
    ''').fetchall()
    boundaries = []
    cumulative = 0
    for key_val, n in rows:
        if key_val is None:
            continue  # O3：跳过 NULL 组（防 append None 导致 key < None 恒假丢行）
        cumulative += n
        if cumulative >= SHARD_ROW_TARGET:
            boundaries.append(key_val)
            cumulative = 0
    return boundaries


def table_hash(conn_factory, table: str, keys_cfg: dict, schema_cols=None) -> tuple:
    """流式计算单表逻辑 hash（v4 分片版）。返回 (sha256hex, rows)。

    对超过 SHARD_ROW_TARGET 的表，按 sort key 第一列自适应分片：
    每片独立 ORDER BY + 流式 hash，按分片序更新同一 sha256 对象（不重置）。
    NULL 归最后片（DuckDB 默认 NULLS LAST，v4 方向修正）。
    结果与全表 ORDER BY + 流式 hash 完全等价（等价性证明见规格 §2.2）。
    """
    keys = _sort_key_expr(table, keys_cfg)
    if keys is None:
        # 全列 canonical：按 information_schema 列序（小表不分片）
        cols = schema_cols or []
        if not cols:
            raise ValueError(f"{table} 全列排序需要 schema_cols")
        order_by = ", ".join(f'"{c}"' for c in cols)
        select_list = ", ".join(f'"{c}"' for c in cols)
        h = hashlib.sha256()
        rows = 0
        conn = conn_factory()
        try:
            res = conn.execute(f'SELECT {select_list} FROM "{table}" ORDER BY {order_by}')
            batch = res.fetch_record_batch(BATCH_ROWS)
            while True:
                try:
                    tbl = batch.read_next_batch()
                except StopIteration:
                    break
                if tbl.num_rows == 0:
                    continue
                for row in tbl.to_pylist():
                    h.update(encode_row(tuple(row.values())).encode("utf-8"))
                    h.update(b"\x0a")
                    rows += 1
        finally:
            conn.close()
        return h.hexdigest(), rows

    # 有排序键的表：检查是否需要分片
    first_col = keys.split(",")[0].strip().strip('"')
    conn = conn_factory()
    total_rows = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    if total_rows <= SHARD_ROW_TARGET:
        # 小表：单查询（与旧版行为一致）
        conn.close()
        h = hashlib.sha256()
        rows = 0
        conn = conn_factory()
        try:
            res = conn.execute(f'SELECT * FROM "{table}" ORDER BY {keys}')
            batch = res.fetch_record_batch(BATCH_ROWS)
            while True:
                try:
                    tbl = batch.read_next_batch()
                except StopIteration:
                    break
                if tbl.num_rows == 0:
                    continue
                for row in tbl.to_pylist():
                    h.update(encode_row(tuple(row.values())).encode("utf-8"))
                    h.update(b"\x0a")
                    rows += 1
        finally:
            conn.close()
        return h.hexdigest(), rows

    # 大表：分片 hash（v4 核心）
    boundaries = _compute_shard_boundaries(conn, table, first_col)
    conn.close()

    h = hashlib.sha256()
    rows = 0

    # 构造分片范围列表：(lo, hi)，hi=None 表示最后片（含 NULL）
    shard_ranges = []
    if boundaries:
        shard_ranges.append((None, boundaries[0]))  # 首片：key < b[0]，不含 NULL
        for i in range(len(boundaries) - 1):
            shard_ranges.append((boundaries[i], boundaries[i + 1]))
        shard_ranges.append((boundaries[-1], None))  # 末片：key >= b[-1] OR IS NULL
    else:
        shard_ranges.append((None, None))  # 无边界=单片全表

    for lo, hi in shard_ranges:
        # 构造 WHERE 子句
        if hi is not None and lo is not None:
            where = f'"{first_col}" >= ? AND "{first_col}" < ?'
            params = [lo, hi]
        elif hi is not None:
            # 首片：不含 NULL（NULL 归最后片）
            where = f'"{first_col}" < ?'
            params = [hi]
        elif lo is not None:
            # 末片：含 NULL（v4：NULLS LAST 对齐）
            where = f'"{first_col}" >= ? OR "{first_col}" IS NULL'
            params = [lo]
        else:
            where = "TRUE"
            params = []

        conn = conn_factory()
        try:
            if params:
                res = conn.execute(
                    f'SELECT * FROM "{table}" WHERE {where} ORDER BY {keys}', params)
            else:
                res = conn.execute(
                    f'SELECT * FROM "{table}" ORDER BY {keys}')
            batch = res.fetch_record_batch(BATCH_ROWS)
            while True:
                try:
                    tbl = batch.read_next_batch()
                except StopIteration:
                    break
                if tbl.num_rows == 0:
                    continue
                for row in tbl.to_pylist():
                    h.update(encode_row(tuple(row.values())).encode("utf-8"))
                    h.update(b"\x0a")
                    rows += 1
        finally:
            conn.close()

    # 行数守恒断言（R4）
    assert rows == total_rows, \
        f"分片行数不守恒: counted={rows} != total={total_rows} (table={table})"

    return h.hexdigest(), rows


def all_tables_hash(main_path: Path, keys_cfg: dict, aux_path: Path = None):
    """主库 18 表 + qfq_aux 两表（adj_factor/fund_adj）逻辑 hash。
    返回 (total_sha, {table: {hash, rows}})。"""
    import duckdb
    tables = sorted(keys_cfg["tables"].keys())
    per = {}

    def conn_main():
        conn = duckdb.connect(str(main_path), read_only=True)
        # 内存等价优化：排序/hash语义不变；单线程+磁盘spill防大表Arrow OOM。
        conn.execute("SET threads=1")
        conn.execute("SET preserve_insertion_order=false")
        temp_dir = SNAP_DIR / "hash_spill"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_sql = str(temp_dir).replace("'", "''")
        conn.execute(f"SET temp_directory='{temp_sql}'")
        return conn

    # schema 列（strategy_events 全列用）
    schemas = {}
    mc = conn_main()
    try:
        for t, cls in mc.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema='main' ORDER BY table_name, ordinal_position").fetchall():
            schemas.setdefault(t, []).append(cls)
    finally:
        mc.close()

    parts = []
    for t in tables:
        _yield_check_data_side()  # H3 运行中让路（逐表检查，~100ms/次可忽略）
        if t not in schemas:
            per[t] = {"hash": None, "rows": 0, "note": "主库无此表（跳过）"}
            parts.append(f"{t}:absent")
            continue
        hh, rr = table_hash(conn_main, t, keys_cfg, schemas.get(t))
        per[t] = {"hash": hh, "rows": rr}
        parts.append(f"{t}:{hh}")
    if aux_path and aux_path.exists():
        import sqlite3
        for t in ("adj_factor", "fund_adj"):
            h = hashlib.sha256()
            rows = 0
            ac = sqlite3.connect(f"file:{aux_path}?mode=ro", uri=True)
            try:
                for row in ac.execute(f"SELECT * FROM {t} ORDER BY code, time"):
                    h.update(encode_row(row).encode("utf-8")); h.update(b"\x0a"); rows += 1
            finally:
                ac.close()
            per[f"aux:{t}"] = {"hash": h.hexdigest(), "rows": rows}
            parts.append(f"aux:{t}:{h.hexdigest()}")
    total = hashlib.sha256(SEP_ROW.join(parts).encode("utf-8")).hexdigest()
    return total, per


# ---------------- 数据侧任务窗口守卫（H3 错峰调度，2026-08-19） ----------------

DATA_SIDE_PATTERNS = (
    "get_tushare_data",        # 每日 ETL + 定向自愈重拉（16:00~21:30）
    "cyq_chips_fill",          # cyq_chips 晚间填充（22:30 后）
    "run_sync_now",            # 云同步（03:00 增量 / 全量 / repair 通道）
    "run_sync_full_now",
    "push_repair_window",      # 定向修复推送
    "_start_all_incremental",  # ETL 一键启动器
    "gap_stage8",              # 阶段8 巡检
    "run_etf_adj_evening",     # 21:45 ETF 因子补拉
    "qfq_maintenance",         # repair 脚本族
    "repair_legacy_qfq",
    "fix_questdb_qfq",
    "repair_stock_daily",
    "refresh_etf_daily",
    # ★ 2026-08-19 P0 盲区补齐：ps1 包装器 + 09:00 巡检/备份（SYSTEM 运行）
    "run_daily_etl_with_health_check",   # 每日 ETL 任务计划 ps1 包装器
    "run_cloud_sync",                    # 增量/全量云同步 ps1 包装器
    "check_etl_integrity",               # 09:00 巡检（6 阶段完整性检查）
    "qdb_snapshot_backup",               # 09:00 在线备份（18 表 CTAS，重载）
)
GUARD_MIN_FREE_MB = 10240      # verify 峰值提交 ~25GB；物理可用 <10GB 启动必压垮共享负载
GUARD_LOG = SNAP_DIR / "guard_refused.log"

# ★ 2026-08-19 P0 盲区修复：数据侧任务由任务计划以 SYSTEM 账户运行，非提权会话
#   psutil 读不到其 cmdline（AccessDenied → ad_value=None），原实现只做 pattern
#   匹配 → 对 SYSTEM python 全盲（SNAP003 create 15:56:47 抢跑启动，16:00:02 ETL
#   起并发 4.5h 守卫无感；实测 PIDs 31500/32552/35324 cmdline 均不可读）。
#   处置：cmdline 不可读的 python 进程按 fail-closed 计为疑似数据侧任务
#   （本机 SYSTEM 账户 python 即数据侧任务链：ETL/同步/repair/补拉/巡检；
#   误拒代价 = 快照延迟启动，漏检代价 = QDB commit 内存压垮，两害取保守）。
#   注意：powershell 不做 fail-closed —— QuestDB 看门狗（每 5min，SYSTEM
#   powershell）会造成常驻误报；powershell 侧靠 pattern 匹配覆盖。
SUSPECT_PROC_NAMES = {"python.exe", "pythonw.exe"}
# shell 族进程仅当 cmdline 含 pat+".ps1" / pat+".py" 才命中（扩展名锚定），
# 防止监控/巡检命令的内联文本自指误报（2026-08-20 DSH 审计修正）。
SHELL_PROC_NAMES = {"powershell.exe", "pwsh.exe", "cmd.exe"}


class GuardAbort(Exception):
    """运行中让路：数据侧任务在 hash 计算期间启动。verify 幂等可重跑。"""


def _attribute_qdb_domain(pid, markers, now_ts):
    """v1.1：SYSTEM 不可读进程的 QDB 域归因（父链 pid 判据，不依赖 cmdline）。
    marker 的 pid = 包装器 ps1 的 $PID；被归因进程须为其直系后代（自身或祖先链
    上溯 ≤4 层内命中 marker pid）→ 强归因。其余情况维持 fail_closed（红线）。
    返回 "qdb_domain:<task>" 或 None。"""
    import psutil
    alive = {(t, p_): m for (t, p_), m in markers.items() if psutil.pid_exists(p_)}
    if not alive:
        return None
    # 收集祖先 pid 链（cmdline 不可读不影响 ppid 读取）
    ancestors = {pid}
    cur = pid
    for _ in range(4):
        try:
            cur = psutil.Process(cur).ppid()
        except Exception:
            break
        if cur == 0:
            break
        ancestors.add(cur)
    for (task, mpid), mtime in alive.items():
        if mpid in ancestors:
            return f"qdb_domain:{task}"
    return None


def _data_side_tasks_running() -> list:
    """扫描数据侧任务进程（psutil 缺失时降级为空，仅保留内存/时段守卫）。"""
    try:
        import psutil
    except ImportError:
        return []
    import os
    import time as _time
    hits = []
    me = os.getpid()
    now_ts = _time.time()
    markers = _qdb_domain_markers()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["pid"] == me:
                continue
            cl = " ".join(p.info["cmdline"] or [])
            pname = (p.info["name"] or "").lower()
            if cl:
                if pname in SHELL_PROC_NAMES:
                    # shell 族：扩展名锚定（防内联文本自指误报）
                    matched = next(
                        (pat for pat in DATA_SIDE_PATTERNS
                         if (pat + ".ps1") in cl or (pat + ".py") in cl), None)
                else:
                    # python 等：原子串匹配（数据侧任务本体）
                    matched = next(
                        (pat for pat in DATA_SIDE_PATTERNS if pat in cl), None)
                if matched:
                    hits.append({"pid": p.info["pid"], "cmd": cl[:120],
                                 "matched_pattern": matched})
            elif not cl and pname in SUSPECT_PROC_NAMES:
                # cmdline 不可读（SYSTEM 账户进程）→ fail-closed 疑似数据侧任务
                # v1.1：先做 QDB 域 marker 归因（成功→重分类；失败→维持 fail_closed 红线）
                attributed = _attribute_qdb_domain(p.info["pid"], markers, now_ts)
                hits.append({"pid": p.info["pid"],
                             "cmd": "<UNREADABLE-CMDLINE %s> %s" % (
                                 p.info["name"],
                                 "QDB域归因:" + attributed if attributed
                                 else "fail-closed 疑为数据侧任务(SYSTEM)"),
                             "matched_pattern": attributed or "fail_closed"})
        except Exception:
            continue
    return hits


def _free_phys_mb() -> int:
    try:
        import psutil
        return psutil.virtual_memory().available // (1024 * 1024)
    except ImportError:
        pass
    try:  # ctypes 降级（Windows GlobalMemoryStatusEx）
        import ctypes
        from ctypes import wintypes

        class _MSE(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_size_t), ("ullAvailPhys", ctypes.c_size_t),
                        ("ullTotalPageFile", ctypes.c_size_t), ("ullAvailPageFile", ctypes.c_size_t),
                        ("ullTotalVirtual", ctypes.c_size_t), ("ullAvailVirtual", ctypes.c_size_t),
                        ("ullAvailExtendedVirtual", ctypes.c_size_t)]

        mse = _MSE()
        mse.dwLength = ctypes.sizeof(_MSE)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mse)):
            return mse.ullAvailPhys // (1024 * 1024)
    except Exception:
        pass
    return 1 << 30  # 查询失败按充裕处理（守卫自身故障不阻断业务）


def _in_trading_hours() -> bool:
    """周一~五 09:15~15:05（北京时间）：盘中实盘框架运行，禁止重载。"""
    now = datetime.now(BJ_TZ)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= hm <= 15 * 60 + 5


def _guard_log(msg: str):
    try:
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        with io.open(GUARD_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(BJ_TZ).isoformat()} {msg}\n")
    except Exception:
        pass


def data_side_guard(action: str) -> int:
    """create/verify 启动守卫：0=放行 6=拒绝（fail-closed）。
    v1.1 S1（总调度批准）：marker 归因成功的 QDB 域命中豁免启动拒绝
    （QDB 域不写快照源，三重 hash 为最终防线）；其余命中维持拒绝。"""
    import time as _time
    try:
        QDB_DOMAIN_MARKER_DIR.mkdir(parents=True, exist_ok=True)
        n = _cleanup_stale_qdb_markers(_time.time())
        if n:
            _guard_log(f"startup cleaned {n} stale qdb_domain marker(s)")
    except Exception:
        pass
    hits = _data_side_tasks_running()
    blocking = [h for h in hits
                if not str(h.get("matched_pattern", "")).startswith("qdb_domain:")]
    if blocking:
        msg = f"{action} REFUSED: {len(blocking)} data-side task(s) running" \
              f" (qdb_domain exempted: {len(hits) - len(blocking)})"
        print(f"[snapshot][guard] {msg}: {blocking[:3]}（退出码 6）", file=sys.stderr)
        _guard_log(msg + f" :: {blocking[:3]}")
        return 6
    free_mb = _free_phys_mb()
    if free_mb < GUARD_MIN_FREE_MB:
        msg = f"{action} REFUSED: free phys {free_mb}MB < {GUARD_MIN_FREE_MB}MB"
        print(f"[snapshot][guard] {msg}（退出码 6）", file=sys.stderr)
        _guard_log(msg)
        return 6
    if _in_trading_hours():
        msg = f"{action} REFUSED: trading hours (Mon-Fri 09:15-15:05)"
        print(f"[snapshot][guard] {msg}（退出码 6）", file=sys.stderr)
        _guard_log(msg)
        return 6
    return 0


# QDB 侧只读/备份任务：不写 DuckDB 主库与 qfq_aux → yield 检查（hash 期间）不 abort。
# 启动前 guard 仍会拦截（保证干净起点）；三重 hash 是数据完整性最终防线。
# 2026-08-20：SNAP_003 R1 因 check_etl_integrity 触发 yield abort（10h 浪费）后增补。
# 2026-08-24 v1.1（docs/governance-guard-system-proc-design.md）：扩展无歧义 QDB 域名。
#   ⚠ qfq_maintenance 双实现同名（trading-battle-back QDB 域 vs QuantStudio pipeline 写
#   qfq_aux 快照源）——歧义名禁入白名单，维持 abort。
QDB_READ_ONLY_PATTERNS = {
    "check_etl_integrity",      # 09:00 巡检（只读 QDB）
    "qdb_snapshot_backup",      # 09:00 在线备份（QDB CTAS，不写 DuckDB）
    "run_cloud_sync",           # ps1 锚定：增量/全量云同步包装器（QDB 读→云写）
    "run_sync_now",             # 增量同步 python 本体（可读时）
    "run_sync_full_now",        # 全量同步 python 本体（可读时）
    "reconcile_routine",        # 全量后对账（QDB 域）
    "gap_cloud_push",           # 缺口云端推送（QDB 域）
    "push_repair_window",       # 定向修复推送（QDB 域）
    "repair_minutes_loop",      # 分钟修复 loop（QDB ILP 写）
    "run_repair_minutes_scheduled",  # 分钟修复 ps1 包装器
    "repair_legacy_qfq",        # 修复脚本族（QDB 域，questdb.ingress）
}

# ---- SYSTEM 不可读进程 QDB 域归因（v1.1 marker 自声明机制）----
# wrapper ps1（run_cloud_sync / run_cloud_sync_full / run_repair_minutes_scheduled）
# 在固定路径写逐进程 marker：<task>_<pid>.json，finally 删自身。
# guard 对 fail_closed 命中做归因：marker 存在 + task ∈ QDB_DOMAIN_TASKS + pid 存活
#   → 重分类 matched_pattern="qdb_domain:<task>"（yield 白名单过滤 + S1 启动豁免）；
# 否则维持 fail_closed（红线：其余不可读进程语义不变）。
QDB_DOMAIN_MARKER_DIR = Path(r"D:\miniQMT策略实盘\trading-battle-back\data\qdb_domain_markers")
QDB_DOMAIN_TASKS = {
    "run_cloud_sync",           # 增量/全量同步包装器共用 task 名
    "run_repair_minutes",       # 分钟修复包装器
}
QDB_DOMAIN_MARKER_STALE_MIN = 15   # pid 死亡但 mtime 新鲜的宽限（防竞态误判）


def _qdb_domain_markers() -> dict:
    """读取 QDB 域 marker：{(task, pid): mtime}。损坏/异常文件按不存在处理。"""
    out = {}
    try:
        for f in QDB_DOMAIN_MARKER_DIR.glob("*.json"):
            try:
                info = json.loads(io.open(f, encoding="utf-8").read())
                task, pid = info.get("task"), info.get("pid")
                if task in QDB_DOMAIN_TASKS and isinstance(pid, int):
                    out[(task, pid)] = f.stat().st_mtime
            except Exception:
                continue
    except Exception:
        pass
    return out


def _cleanup_stale_qdb_markers(now: float) -> int:
    """create/verify 启动时清扫孤儿 marker（pid 死 + mtime 超宽限）。返回清除数。"""
    import psutil
    removed = 0
    for f in QDB_DOMAIN_MARKER_DIR.glob("*.json"):
        try:
            age_min = (now - f.stat().st_mtime) / 60
            info = json.loads(io.open(f, encoding="utf-8").read())
            pid = info.get("pid")
            if isinstance(pid, int) and not psutil.pid_exists(pid) \
                    and age_min > QDB_DOMAIN_MARKER_STALE_MIN:
                f.unlink(missing_ok=True)
                removed += 1
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return removed


_VERIFY_YIELD_EXEMPT = False  # 【A-豁免 2026-08-25 总调度批准】verify 只读不可变快照
                               # 副本无撕裂路径；仅 cmd_verify 内置位，create 路径永不置位。


def _yield_check_data_side():
    """运行中让路检查（逐表调用）：DuckDB/qfq_aux 写者启动 → GuardAbort。
    QDB 只读任务（备份/巡检）及 marker 归因的 QDB 域任务（v1.1）不触发——
    三重 hash 保证数据完整性不受影响。"""
    if _VERIFY_YIELD_EXEMPT:   # verify-only 豁免（启动门禁不在豁免范围）
        return
    hits = _data_side_tasks_running()
    # 过滤 QDB 域任务（不写快照源）：白名单 pattern 或 qdb_domain:* 归因
    writers = [h for h in hits
               if h.get("matched_pattern") not in QDB_READ_ONLY_PATTERNS
               and not str(h.get("matched_pattern", "")).startswith("qdb_domain:")]
    if writers:
        raise GuardAbort(f"data-side writer(s) started during hash: {writers[:3]}")


# ---------------- 准入 / 磁盘 ----------------

def admission_check() -> int:
    """registry 准入 + 写锁空闲。返回退出码或 0。"""
    if not REGISTRY.exists():
        print("[snapshot] registry 不存在，先运行 governance_write_conn_scan.py", file=sys.stderr)
        return 5
    reg = json.loads(io.open(REGISTRY, encoding="utf-8").read())
    bad = [w for w in reg.get("main_db_writers", []) + reg.get("aux_db_writers", [])
           if not w.get("locked")]
    if bad:
        print(f"[snapshot] registry 存在未锁定写路径（{len(bad)}），create 拒绝（退出码 5）", file=sys.stderr)
        return 5
    return 0


def disk_admission(extra_bytes: int) -> int:
    import shutil as _sh
    free = _sh.disk_usage(str(SNAP_DIR)).free
    # 公式预算（设计 §6）：以既有快照总量 + 本次预估 ≤ 可用空间
    existing = sum(f.stat().st_size for f in SNAP_DIR.glob("SNAP_*/**") if f.is_file())
    need = int(extra_bytes * DISK_FORMULA_FACTOR)
    if existing + need > free:
        print(f"[snapshot] 磁盘不足：已有快照 {existing/1e9:.1f}G + 需 {need/1e9:.1f}G > 可用 {free/1e9:.1f}G（退出码 3）",
              file=sys.stderr)
        return 3
    return 0


# ---------------- 原子 index ----------------

def load_index() -> dict:
    if INDEX.exists():
        return json.loads(io.open(INDEX, encoding="utf-8").read())
    return {"snapshots": []}


def detect_orphans(index=None):
    """识别 final 目录存在但 index 缺项的孤儿状态（不自动删除，移入 orphans 隔离）。"""
    idx = index or load_index()
    known = {s["snapshot_id"] for s in idx.get("snapshots", [])}
    out = []
    for d in SNAP_DIR.glob("SNAP_*"):
        if not d.is_dir() or d.name.endswith(".tmp") or d.name in known:
            continue
        out.append(d.name)
    return out


def save_index_atomic(idx: dict):
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_suffix(".json.tmp")
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, INDEX)


def _atomic_write_json(path: Path, obj: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def enforce_no_orphans() -> int:
    """启动流程 fail-closed：发现 final 目录存在但 index 缺项即拒绝并记录。"""
    orphans = detect_orphans()
    if not orphans:
        return 0
    msg = f"[snapshot] 发现孤儿正式目录（index 缺项）: {orphans}；请人工审计后隔离，fail-closed（退出码4）"
    print(msg, file=sys.stderr)
    with io.open(SNAP_DIR / "orphan.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(BJ_TZ).isoformat()} {msg}\n")
    return 4


# ---------------- create ----------------

def cmd_create(source_task: str = "manual") -> int:
    rc = data_side_guard("create")  # H3 启动守卫
    if rc:
        return rc
    rc = protect_journal_check()
    if rc:
        return rc
    rc = enforce_no_orphans()
    if rc:
        return rc
    rc = admission_check()
    if rc:
        return rc
    from quantstudio.pipeline.snapshot_lock import acquire_write_lock, WriteLockHeld
    try:
        lk = acquire_write_lock(f"snapshot:create:{source_task}", timeout_s=30)
    except WriteLockHeld as e:
        print(f"[snapshot] {e}（退出码 2：写任务进行中，create fail-closed）", file=sys.stderr)
        return 2

    keys_cfg = json.loads(io.open(SORT_KEYS, encoding="utf-8").read())
    est = (MAIN_DB.stat().st_size if MAIN_DB.exists() else 0) + \
          (AUX_DB.stat().st_size if AUX_DB.exists() else 0)
    rc = disk_admission(est)
    if rc:
        lk.release()
        return rc

    t0 = time.time()
    tmp_dir = None
    try:
        # 阶段① source_hash_pre（快速探针：行数+文件 stat）
        pre_stat = {p.name: (p.stat().st_size, p.stat().st_mtime)
                    for p in (MAIN_DB, AUX_DB, DATA_CONFIG) if p.exists()}
        # 阶段② 复制（主库/配置完整复制；aux VACUUM INTO）
        total_pre, per_pre = all_tables_hash(MAIN_DB, keys_cfg, AUX_DB)

        ts = datetime.now(BJ_TZ).strftime("%Y%m%d")
        seq = len(load_index()["snapshots"]) + 1
        snap_id_tmp = f"SNAP_{ts}_{seq:03d}_pending"
        tmp_dir = SNAP_DIR / (snap_id_tmp + ".tmp")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)
        shutil.copy2(MAIN_DB, tmp_dir / MAIN_DB.name)
        shutil.copy2(DATA_CONFIG, tmp_dir / DATA_CONFIG.name)
        # SQLite：VACUUM INTO（禁止裸复制）
        import sqlite3
        aux_copy = tmp_dir / AUX_DB.name
        src = sqlite3.connect(f"file:{AUX_DB}?mode=ro", uri=True)
        try:
            src.execute("VACUUM INTO ?", [str(aux_copy)])
        finally:
            src.close()
        chk = sqlite3.connect(f"file:{aux_copy}?mode=ro", uri=True)
        try:
            integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"aux 副本 integrity_check 失败: {integrity}")
        finally:
            chk.close()

        # 阶段③ copy_hash + source_hash_post（三重校验：pre == post == copy）
        total_copy, per_copy = all_tables_hash(tmp_dir / MAIN_DB.name, keys_cfg, aux_copy)
        total_post, per_post = all_tables_hash(MAIN_DB, keys_cfg, AUX_DB)
        if not (total_pre == total_post == total_copy):
            print(f"[snapshot] 三重校验失败 pre={total_pre[:8]} post={total_post[:8]} copy={total_copy[:8]}"
                  f"（退出码 4；若 pre≠post=检测到锁外写入）", file=sys.stderr)
            return 4

        snap_id = f"SNAP_{ts}_{seq:03d}_{total_pre[:8]}"
        final_dir = SNAP_DIR / snap_id
        # 真实峰值工作集（Windows PeakWorkingSetSize；非当前时点 RSS）
        peak_rss_mb = None
        try:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                class _PMC(ctypes.Structure):
                    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
                pmc = _PMC(); pmc.cb = ctypes.sizeof(_PMC)
                if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),
                                                            ctypes.byref(pmc), pmc.cb):
                    peak_rss_mb = round(pmc.PeakWorkingSetSize / (1024 * 1024), 2)
            else:
                import resource
                raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                peak_rss_mb = round((raw * 1024 if sys.platform != "darwin" else raw) / (1024 * 1024), 2)
        except Exception:
            peak_rss_mb = None
        manifest = {
            "snapshot_id": snap_id,
            "created_at": datetime.now(BJ_TZ).isoformat(),
            "source_task": source_task,
            "files": {p.name: {"bytes": p.stat().st_size,
                               "sha256": file_sha256_stream(p)}
                      for p in sorted(tmp_dir.iterdir()) if p.is_file() and p.name != "manifest.json"},
            "logical_total_sha256": total_pre,
            "source_hash_pre": total_pre,
            "source_hash_post": total_post,
            "copy_hash": total_copy,
            "tables": per_pre,
            "pre_stat": {k: list(v) for k, v in pre_stat.items()},
            "duration_s": round(time.time() - t0, 1),
            "peak_rss_mb": peak_rss_mb,
            "verify_status": "pending",
            "verified_at": None,
            "protected": False,
            "lock_protocol": "3A-v1（34 点 + 硬约束）",
        }
        # 3B 审计修正：tmp 内 manifest 原子提交+fsync → rename final → index 原子更新
        _atomic_write_json(tmp_dir / "manifest.json", manifest)
        os.rename(tmp_dir, final_dir)
        tmp_dir = None
        idx = load_index()
        idx["snapshots"].append({"snapshot_id": snap_id, "created_at": manifest["created_at"],
                                 "logical_total_sha256": total_pre, "protected": False})
        save_index_atomic(idx)
        print(f"[snapshot] created {snap_id} ({manifest['duration_s']}s, tables={len(per_pre)})")
        return 0
    except GuardAbort as e:
        print(f"[snapshot][guard] {e}（退出码 6；tmp 已由 finally 清理）", file=sys.stderr)
        _guard_log(f"create ABORTED: {e}")
        return 6
    finally:
        if tmp_dir is not None and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)  # 半成品防护
            _log_failed(source_task)
        lk.release()


def _log_failed(source_task):
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    with io.open(SNAP_DIR / "failed.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(BJ_TZ).isoformat()} task={source_task} create 失败，tmp 已清理\n")


# ---------------- verify / list / prune / bind / unprotect ----------------

def cmd_verify(snap_id: str) -> int:
    rc = data_side_guard("verify")  # H3 启动守卫
    if rc:
        return rc
    rc = enforce_no_orphans()
    if rc:
        return rc
    d = SNAP_DIR / snap_id
    if not d.exists():
        print(f"[snapshot] 不存在 {snap_id}", file=sys.stderr); return 4
    man = json.loads(io.open(d / "manifest.json", encoding="utf-8").read())
    keys_cfg = json.loads(io.open(SORT_KEYS, encoding="utf-8").read())
    try:
        globals()["_VERIFY_YIELD_EXEMPT"] = True   # A-豁免：仅 verify hash 段生效
        total, per = all_tables_hash(d / MAIN_DB.name, keys_cfg, d / AUX_DB.name)
    except GuardAbort as e:
        print(f"[snapshot][guard] {e}（退出码 6；verify 幂等可重跑，manifest 未改动）",
              file=sys.stderr)
        _guard_log(f"verify {snap_id} ABORTED: {e}")
        return 6
    finally:
        globals()["_VERIFY_YIELD_EXEMPT"] = False   # A-豁免复位（含异常/早退路径）
    ok = total == man["logical_total_sha256"]
    man["verify_status"] = "PASS" if ok else "FAIL"
    man["verified_at"] = datetime.now(BJ_TZ).isoformat()
    man["verify_recomputed_sha256"] = total
    _atomic_write_json(d / "manifest.json", man)
    print(f"verify {snap_id}: {'PASS' if ok else 'FAIL'} (now={total[:12]} manifest={man['logical_total_sha256'][:12]})")
    return 0 if ok else 4


def cmd_list() -> int:
    rc = enforce_no_orphans()
    if rc:
        return rc
    for s in load_index()["snapshots"]:
        print(f"{s['snapshot_id']}  {s['created_at']}  hash={s['logical_total_sha256'][:12]}"
              f"{'  [PROTECTED]' if s.get('protected') else ''}")
    return 0


def protect_journal_check() -> int:
    """检查/恢复 protect 事务 journal；不一致时 fail-closed。"""
    if not PROTECT_JOURNAL.exists():
        return 0
    try:
        journal = json.loads(io.open(PROTECT_JOURNAL, encoding="utf-8").read())
        snap_id = journal["snapshot_id"]
        manifest_path = SNAP_DIR / snap_id / "manifest.json"
        manifest = json.loads(io.open(manifest_path, encoding="utf-8").read())
        index = load_index()
        entry = next((item for item in index["snapshots"]
                      if item["snapshot_id"] == snap_id), None)
        if manifest.get("protected") is True and entry and entry.get("protected") is True:
            with io.open(PROTECT_LOG, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now(BJ_TZ).isoformat()} recover-complete "
                        f"{snap_id} journal stale-clean\n")
            PROTECT_JOURNAL.unlink(missing_ok=True)
            return 0
        print(f"[snapshot] protect journal 状态不一致 sid={snap_id}；"
              "fail-closed，须人工恢复（退出码4）", file=sys.stderr)
        return 4
    except Exception as exc:
        print(f"[snapshot] protect journal 不可恢复: {exc}；"
              "fail-closed（退出码4）", file=sys.stderr)
        return 4


def snapshot_protection_state(entry: dict) -> tuple:
    """双源读取保护状态；缺失或不一致一律拒绝 prune。"""
    snap_id = entry["snapshot_id"]
    manifest_path = SNAP_DIR / snap_id / "manifest.json"
    if not manifest_path.exists():
        print(f"[snapshot] prune 拒绝：{snap_id} manifest 缺失（fail-closed）",
              file=sys.stderr)
        return False, True
    try:
        manifest = json.loads(io.open(manifest_path, encoding="utf-8").read())
    except Exception as exc:
        print(f"[snapshot] prune 拒绝：{snap_id} manifest 不可读: {exc}",
              file=sys.stderr)
        return False, True
    index_protected = bool(entry.get("protected"))
    manifest_protected = bool(manifest.get("protected"))
    if index_protected != manifest_protected:
        with io.open(SNAP_DIR / "protection_mismatch.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(BJ_TZ).isoformat()} {snap_id} "
                    f"index={index_protected} manifest={manifest_protected} "
                    "prune fail-closed\n")
        print(f"[snapshot] prune 拒绝：{snap_id} 保护状态分裂 "
              f"index={index_protected} manifest={manifest_protected}",
              file=sys.stderr)
        return False, True
    return True, index_protected or manifest_protected


def cmd_prune(keep: int) -> int:
    rc = protect_journal_check()
    if rc:
        return rc
    idx = load_index()
    protected = []
    normal = []
    for entry in idx["snapshots"]:
        ok, is_protected = snapshot_protection_state(entry)
        if not ok:
            return 4
        (protected if is_protected else normal).append(entry)
    doomed = normal[:-keep] if len(normal) > keep else []
    for entry in doomed:
        d = SNAP_DIR / entry["snapshot_id"]
        if d.exists():
            shutil.rmtree(d)
        print(f"pruned {entry['snapshot_id']}")
    kept_normal = normal[len(normal)-keep:] if len(normal) > keep else normal
    idx["snapshots"] = protected + kept_normal
    save_index_atomic(idx)
    print(f"保留 {keep}，保护 {len(protected)} 项不被删除")
    return 0


def cmd_bind(snap_id: str, result_dir: str, protect: bool = False) -> int:
    rc = protect_journal_check()
    if rc:
        return rc
    d = SNAP_DIR / snap_id
    if not d.exists():
        print(f"[snapshot] 不存在 {snap_id}", file=sys.stderr); return 4
    man = json.loads(io.open(d / "manifest.json", encoding="utf-8").read())
    if man.get("verify_status") != "PASS":
        print(f"[snapshot] bind 拒绝：{snap_id} verify_status={man.get('verify_status')!r}，必须 PASS（退出码4）", file=sys.stderr)
        return 4
    if protect:
        idx = load_index()
        found = next((x for x in idx["snapshots"] if x["snapshot_id"] == snap_id), None)
        if found is None:
            print(f"[snapshot] index 缺少 {snap_id}，protect 拒绝", file=sys.stderr); return 4
        # 跨文件 protect 事务：journal → manifest → index → audit → 清 journal。
        # 任一步中断后 protect_journal_check 将恢复或 fail-closed。
        _atomic_write_json(PROTECT_JOURNAL, {
            "snapshot_id": snap_id,
            "started_at": datetime.now(BJ_TZ).isoformat(),
            "target": "protected=true",
        })
        man["protected"] = True
        man["protected_at"] = datetime.now(BJ_TZ).isoformat()
        _atomic_write_json(d / "manifest.json", man)
        found["protected"] = True
        save_index_atomic(idx)
        with io.open(PROTECT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(BJ_TZ).isoformat()} protect {snap_id} "
                    "bind --protect user_approved\n")
        PROTECT_JOURNAL.unlink(missing_ok=True)
    out = Path(result_dir) / "snapshot_meta.json"
    _atomic_write_json(out, {"snapshot_id": snap_id,
                             "logical_total_sha256": man["logical_total_sha256"],
                             "verify_status": man["verify_status"],
                             "protected": bool(man.get("protected")),
                             "bound_at": datetime.now(BJ_TZ).isoformat()})
    print(f"bound → {out}" + (" [PROTECTED]" if protect else ""))
    return 0


def cmd_unprotect(snap_id: str, reason: str) -> int:
    rc = protect_journal_check()
    if rc:
        return rc
    if not reason or reason.strip() == "":
        print("[snapshot] unprotect 必须携带 --reason（用户批准场景）", file=sys.stderr); return 2
    idx = load_index()
    for s in idx["snapshots"]:
        if s["snapshot_id"] == snap_id:
            s["protected"] = False
            break
    else:
        print(f"[snapshot] 不存在 {snap_id}", file=sys.stderr); return 4
    save_index_atomic(idx)
    d = SNAP_DIR / snap_id / "manifest.json"
    if d.exists():
        man = json.loads(io.open(d, encoding="utf-8").read())
        man["protected"] = False
        man["unprotect_reason"] = reason
        _atomic_write_json(d, man)
    with io.open(UNPROTECT_LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(BJ_TZ).isoformat()} unprotect {snap_id} reason={reason!r} user_approved\n")
    print(f"unprotected {snap_id}（审计日志已记录）")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="数据快照版本机制（治理方案 3B）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("--source-task", default="manual")
    v = sub.add_parser("verify"); v.add_argument("snap_id")
    sub.add_parser("list")
    p = sub.add_parser("prune"); p.add_argument("--keep", type=int, default=KEEP_DEFAULT)
    b = sub.add_parser("bind"); b.add_argument("snap_id"); b.add_argument("result_dir"); b.add_argument("--protect", action="store_true")
    u = sub.add_parser("unprotect"); u.add_argument("snap_id"); u.add_argument("--reason", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "create":
        return cmd_create(args.source_task)
    if args.cmd == "verify":
        return cmd_verify(args.snap_id)
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "prune":
        return cmd_prune(args.keep)
    if args.cmd == "bind":
        return cmd_bind(args.snap_id, args.result_dir, args.protect)
    if args.cmd == "unprotect":
        return cmd_unprotect(args.snap_id, args.reason)
    return 2


if __name__ == "__main__":
    sys.exit(main())
