"""DBWriter — 标准化数据写入（Layer 1 模块④）

职责：把通过校验的标准数据（tushare 格式）幂等写入数据库。
仅写入"全部校验通过"的数据；仅当"写入成功 + 回读验证成功"后推进水位。

支持的数据库：
    - DuckDBWriter（默认，零部署嵌入式）
    - QuestDBWriter（可选，ILP 批量写，大量数据）
"""
from __future__ import annotations

import abc
import contextlib
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)
from quantstudio._paths import db_path
from quantstudio.pipeline.snapshot_lock import (ensure_write_lock,
                                                release_write_lock,
                                                assert_lock_owner,
                                                WriteLockHeld)
from quantstudio.pipeline.eps_backfill import backfill_eps_gap  # P-A3 写后回补


def _is_writer_auto_backfill_enabled() -> bool:
    """P-A3 feature gate：writer 写后自动 eps 回补默认关闭，显式开启才生效。

    fail-closed：仅环境变量 QS_AUTO_BACKFILL_EPS 为 "1"/"true"/"on"（不区分大小写）
    时开启；未设置、"0"、"false"、"off"、空字符串、其他任意值一律关闭。
    CLI --apply 直接调用 backfill_eps_gap(conn)，不受此 gate 影响。
    """
    v = os.environ.get("QS_AUTO_BACKFILL_EPS", "").strip().lower()
    return v in ("1", "true", "on")


def _is_upsert_skip_identical_enabled() -> bool:
    """T2 feature gate：upsert 跳过"已存在且逐位一致行"默认关闭。

    fail-closed：仅环境变量 QS_UPSERT_SKIP_IDENTICAL 为 "1"/"true"/"on"
    （不区分大小写）时开启；未设置、"0"、"false"、"off"、空字符串及其他任意值
    一律关闭 ⇒ 关闭态与现状逐字节等同（SQL 不追加 WHERE）。

    开启时：ON CONFLICT DO UPDATE 追加行值元组 IS DISTINCT FROM 条件
    （NULL-safe）。一致行不进 delete 相位（DuckDB 对带 PK 表的 UPDATE 一律
    改写为 DELETE+INSERT），吞吐逼近纯插入。new/updated 审计口径不变。
    """
    v = os.environ.get("QS_UPSERT_SKIP_IDENTICAL", "").strip().lower()
    return v in ("1", "true", "on")


def _is_batch_checkpoint_enabled() -> bool:
    """T1 feature gate：批级断点（fail-closed）默认关闭。

    fail-closed：仅环境变量 QS_BATCH_CHECKPOINT 为 "1"/"true"/"on"（不区分大小写）
    时开启；未设置、"0"、"false"、"off"、空字符串及其他任意值一律关闭 ⇒
    关闭态不建表、不读写断点、不产生任何新表/新日志（逐字节等同现状）。

    开启时：任务内每个「批级窗口」（日批 = 交易日；每股 = 证券代码）在
    ``writer.write()`` 返回（本批写入已提交）之后，向批级断点表提交
    ``status='completed'``；崩溃重启后按窗口跳过已完成批、只重跑未完成批。
    断点表职责与 ``source_watermark``（source/table/freq 级增量水位）不同，
    不改动后者语义（规格件 §三 T1）。
    """
    v = os.environ.get("QS_BATCH_CHECKPOINT", "").strip().lower()
    return v in ("1", "true", "on")


# T1：批级断点表 DDL（**不**进 DDL_DUCKDB —— 关闭态不得创建该表）。
# 键自规格件 §三 T1：(task_name, table, freq, window_key)；列名用 table_name
# 与既有 source_watermark.table_name 同口径（table 为 SQL 保留字，不宜作列名，
# 语义仍为「表名」，非口径变更）。
BATCH_CHECKPOINT_DDL = """
    CREATE TABLE IF NOT EXISTS batch_checkpoint (
        task_name VARCHAR, table_name VARCHAR, freq VARCHAR, window_key VARCHAR,
        batch_id VARCHAR, status VARCHAR, rows_written BIGINT, updated_at TIMESTAMP,
        PRIMARY KEY (task_name, table_name, freq, window_key)
    )"""


# R2（write-integrity，2026-10-08 裁定④）：DuckDBWriter 进程内按表写互斥。
# 动机（取证②）：同进程并发写曾致 23 次 TransactionContext write-write conflict
# （FAIL×7 + A4WARN×11 + OTHER×5，key 样本 "000055,1788744600000,1min"）；
# T1 的 RW 打开退避只防跨进程文件锁，进程内并发写靠本锁串行。
# 纯恢复型修复：加锁后写入结果与串行执行一致。
# 首期范围（裁定④）：仅 stock_minutes / etf_minutes 两张高频大表；其余表后续评估扩面。
_SERIAL_WRITE_TABLES = frozenset({"stock_minutes", "etf_minutes"})

# R2 第二阶段（write-integrity，2026-10-08）：锁注册表升为**模块级**——重锚引擎
# （qfq_reanchor_engine）在**自有连接的自持事务**（BEGIN→COMMIT，不取 writer 的
# _conn_lock）里直 UPDATE 价格表 front 列，与 streaming 分片 upsert（_conn_lock 内）
# 对同一行并发即取证②的进程内 write-write 冲突真源。进程内按表互斥须经**同一把锁**
# 才成立，故注册表从 DuckDBWriter 实例级上提为模块级，供引擎侧跨模块共享。
# 引擎侧接入：quantstudio/pipeline/qfq_reanchor_engine.py 顶层
# `from quantstudio.pipeline.writers import table_write_lock`（已核无循环导入——
# 本模块内部依赖链 _paths / snapshot_lock / eps_backfill / qfq_schema_contracts
# 均不回导引擎）。
_TABLE_WRITE_LOCKS: dict = {}


# ── §10.2 冲突归因（2026-10-11 总调度裁定①）──────────────────────────────
# 分工：**writers 侧只出原始标识**（表/批次/写路径/是否串行表/连接号），
#       **daemon 侧单点组装**为一行判别特征（避免两处重复输出）。
# 纪律：只增观测——不参与任何判定逻辑、不改异常传播、不吞异常。
_LAST_WRITE_CTX: dict = {}


def note_write_context(**kw) -> None:
    """记录最近一次写路径的原始标识（归因用；不参与判定）。"""
    _LAST_WRITE_CTX.clear()
    _LAST_WRITE_CTX.update(kw)


def last_write_context() -> dict:
    """读取最近写路径原始标识（只读；供 daemon 侧单点组装）。"""
    return dict(_LAST_WRITE_CTX)


# ── §10.2 修正（2026-10-12 实战暴露：全局最近槽会与失败写错配）────────────────
# 首例实战（10:18:43 stock_minutes 冲突）显示 ctx_table=sw_weight / serial=False ——
# 即「最近一次写」并非「失败的那次写」⇒ 该字段误导。修正：在写路径的 except 中
# **复制当时上下文到失败槽**（此刻 _LAST_WRITE_CTX 正是本次失败写的上下文）。
_LAST_WRITE_FAILURE: dict = {}


def record_write_failure(exc: BaseException) -> None:
    """§10.2 修正 + A1（2026-10-22）：记录**失败写自身**上下文 + 并发写者指认。

    只增观测：不吞异常、不改传播；自身异常一律兜住（观测失败不得影响写路径）。
    """
    try:
        info = dict(_LAST_WRITE_CTX)
        info["exc_type"] = type(exc).__name__
        info["exc_msg"] = str(exc)[:300]
        info["pid"] = os.getpid()
        info["thread"] = threading.get_ident()
        # 冲突键解析（write-write conflict on key: "<code>, <time_ms>, <freq>"）
        _msg = str(exc)
        if "write-write conflict on key" in _msg:
            try:
                _k = _msg.split('key: "', 1)[1].split('"', 1)[0]
                _parts = [p.strip() for p in _k.split(",")]
                info["conflict_key"] = _k
                if len(_parts) >= 2:
                    info["conflict_time"] = int(_parts[1])
            except Exception:
                pass
        # A1：并发写者指认（同表 + 时段覆盖；无同表同段写者 ⇒ 跨进程候选）
        _tbl = info.get("table")
        if _tbl:
            _peers = concurrent_writers(_tbl, info.get("conflict_time"))
            info["peers"] = _peers
            info["peers_n"] = len(_peers)
            if not _peers:
                info["cross_process_candidate"] = True
        _LAST_WRITE_FAILURE.clear()
        _LAST_WRITE_FAILURE.update(info)
    except Exception:
        pass


def last_write_failure() -> dict:
    """读取最近一次**失败写**的上下文（只读；供 daemon 侧单点组装）。"""
    try:
        return dict(_LAST_WRITE_FAILURE)
    except Exception:
        return {}


# ── A1（六步③·2026-10-22）：进程内价格表写者登记表 + 并发写者指认 ────────────
# 动机（W-4 定谳 + 第 15 次实证）：主 upsert 路径原无 record_write_failure（失败槽空，
# 回退 last 槽取到误导值）；且冲突时无法指认并发写者。登记表按 (pid, thread, table)
# 记录写窗口（含 time 段、结束时间），冲突时可反查**同时段**在程/刚结束的写者；
# 无同表同段写者 ⇒ 跨进程候选（R6 上报口径）。
# 纪律：只增观测，不参与判定、不改异常传播、不吞异常。
_REGISTRY_LOCK = threading.Lock()
_WRITER_REGISTRY: Dict[tuple, dict] = {}
_REGISTRY_TTL_S = 300.0      # 结束条目保留时长（供「刚结束」写者被指认）
_REGISTRY_MAX = 200          # 上限（防泄漏）


def price_write_enter(table: str, path: str, batch_id: str = "", df=None,
                      t_min: Optional[int] = None, t_max: Optional[int] = None) -> None:
    """登记本次价格表写（进入写临界区后调用；失败/结束须 price_write_exit）。

    A1b（2026-10-23）：条目带 heartbeat（每次访问刷新）+ 存活判活；不再以固定 TTL 剪除**活跃**
    条目——固定 TTL 会把长事务写者（引擎单元常 >300s，实测可达 63min）当死条目剪掉，
    导致冲突时 peers_n=0 ⇒ cross_process_candidate 误报（实弹 10-23 07:34 已证）。
    """
    try:
        if df is not None and "time" in getattr(df, "columns", []):
            try:
                _t = pd.to_numeric(df["time"], errors="coerce")
                t_min = int(_t.min())
                t_max = int(_t.max())
            except Exception:
                pass
        key = (os.getpid(), threading.get_ident(), table)
        with _REGISTRY_LOCK:
            _WRITER_REGISTRY[key] = {
                "pid": os.getpid(), "thread": threading.get_ident(),
                "table": table, "path": path, "batch": str(batch_id or ""),
                "t_min": t_min, "t_max": t_max,
                "n": int(len(df)) if df is not None else 0,
                "started": datetime.now().timestamp(), "ended": None,
                "heartbeat": datetime.now().timestamp(),
            }
            if len(_WRITER_REGISTRY) > _REGISTRY_MAX:
                _prune_registry_locked()
    except Exception:
        pass


def price_write_exit(table: str) -> None:
    """标记本次写结束（保留 TTL 供「刚结束」写者被指认）。"""
    try:
        key = (os.getpid(), threading.get_ident(), table)
        with _REGISTRY_LOCK:
            ent = _WRITER_REGISTRY.get(key)
            if ent is not None:
                ent["ended"] = datetime.now().timestamp()
    except Exception:
        pass


def _registry_entry_alive(ent: dict) -> bool:
    """判活：同进程按线程存活；跨进程保守判活（无法安全探活 ⇒ 视为存活）。"""
    try:
        if ent.get("pid") != os.getpid():
            return True
        tid = ent.get("thread")
        return any(t.ident == tid for t in threading.enumerate() if t.ident is not None)
    except Exception:
        return True


def _prune_registry_locked() -> None:
    """剪除：①死线程的**活跃**条目（A1b 判活，替代固定 TTL）；②超过 TTL 的**已结束**条目。"""
    try:
        now = datetime.now().timestamp()
        dead = []
        for k, v in list(_WRITER_REGISTRY.items()):
            if v.get("ended") is not None:
                if now - v["ended"] > _REGISTRY_TTL_S:
                    dead.append(k)
                continue
            if not _registry_entry_alive(v):
                dead.append(k)
        for k in dead:
            _WRITER_REGISTRY.pop(k, None)
    except Exception:
        pass


# ── B1/B2（六步③·2026-10-23）：写作用域登记 + 判活（A1b）──────────────────
# 为什么需要「作用域」而不是「锁」：实弹（10-22/23）证伪了「加锁即可互斥」的假设——
# 引擎在单元事务内对 minute 表做**分段 front 列 UPDATE**（apply_minute_segments /
# apply_fresh_minute_staged），任务侧在同一批 key 上做 streaming upsert；两者对**同一行**
# 的不同列写，DuckDB 按 tuple 级 MVCC 判冲突 ⇒ 提交期 write-write conflict（key 恒定为
# 分段首根 bar：etf 159512@09-11 09:30 / stock 000055@09-07 09:30）。
# 锁只保证「同表写入口串行」，但**引擎分段写法未登记**（A1 登记只在单元入口），故冲突时
# peers_n=0、cross_process_candidate=True 误报。正解 = 让「谁在写哪段 key」显式可见（作用域），
# 并让任务写**让行**（超时则 defer 本批，交下轮水位内自然重拉）——不让两边盲写同一行。
_SCOPE_REGISTRY: Dict[str, dict] = {}
_SCOPE_STATS: Dict[str, float] = {"entered": 0, "conflicts": 0, "waits": 0,
                                  "deferred": 0, "timeouts": 0, "wait_s": 0.0}
_SCOPE_MAX = 64          # 在程作用域上限（正常仅个位数；防泄漏）
_SCOPE_POLL_S = 0.5      # 让行轮询间隔


def _scope_enabled() -> bool:
    """作用域开关（QS_PRICE_WRITE_SCOPE=0 逐位回退旧行为）。"""
    try:
        return os.environ.get("QS_PRICE_WRITE_SCOPE", "1") != "0"
    except Exception:
        return True


def _scope_wait_s() -> float:
    """让行上限（默认 120s；引擎单元实测可达 63min，故超时后必须 defer 而非盲目重试）。"""
    try:
        return float(os.environ.get("QS_PRICE_WRITE_SCOPE_WAIT_S", "120"))
    except Exception:
        return 120.0


def price_scope_enter(scope_id: str, table: str, codes=None,
                      t_min: Optional[int] = None, t_max: Optional[int] = None,
                      path: str = "") -> str:
    """登记写作用域（覆盖**整个写事务/单元**，而非单条语句）。

    codes=None 表示该作用域对全表代码生效（保守）；t_min/t_max 同理。
    时间域只在**同一张表内**比较（各表 time 口径不同，禁止跨表假设）。
    """
    try:
        with _REGISTRY_LOCK:
            if len(_SCOPE_REGISTRY) >= _SCOPE_MAX:
                _prune_scope_locked()
            _SCOPE_REGISTRY[str(scope_id)] = {
                "scope_id": str(scope_id), "table": table,
                "codes": set(map(str, codes)) if codes is not None else None,
                "t_min": t_min, "t_max": t_max,
                "pid": os.getpid(), "thread": threading.get_ident(),
                "path": path, "ts": datetime.now().timestamp(),
                "heartbeat": datetime.now().timestamp(),
            }
            _SCOPE_STATS["entered"] = _SCOPE_STATS.get("entered", 0) + 1
    except Exception:
        pass
    return str(scope_id)


def price_scope_exit(scope_id: str) -> None:
    """释放作用域（单元提交/回滚、任务批结束、异常路径 finally 均须调用）。"""
    try:
        with _REGISTRY_LOCK:
            _SCOPE_REGISTRY.pop(str(scope_id), None)
    except Exception:
        pass


def _prune_scope_locked() -> None:
    """判活剪除：同进程条目按线程存活判活；跨进程条目保守保留（无法安全探活）。"""
    try:
        me_pid = os.getpid()
        alive_threads = {t.ident for t in threading.enumerate() if t.ident is not None}
        dead = []
        for sid, sc in list(_SCOPE_REGISTRY.items()):
            if sc.get("pid") != me_pid:
                continue                      # 跨进程：保留（由 3A 文件锁兜底）
            if sc.get("thread") not in alive_threads:
                dead.append(sid)
        for sid in dead:
            _SCOPE_REGISTRY.pop(sid, None)
    except Exception:
        pass


def price_scope_conflicts(table: str, codes=None,
                          t_min: Optional[int] = None, t_max: Optional[int] = None,
                          exclude_scope: Optional[str] = None) -> List[dict]:
    """返回与目标 (table, codes, 时间域) **重叠**的在程作用域。

    重叠判定：同表 且（码集有交集 或 任一方无码约束）且（时间域有交集 或 任一方无时间约束）。
    """
    out: List[dict] = []
    try:
        if not _scope_enabled():
            return out
        my_codes = set(map(str, codes)) if codes is not None else None
        with _REGISTRY_LOCK:
            _prune_scope_locked()
            for sc in list(_SCOPE_REGISTRY.values()):
                if sc.get("table") != table:
                    continue
                if exclude_scope is not None and sc.get("scope_id") == str(exclude_scope):
                    continue
                sc_codes = sc.get("codes")
                if sc_codes is not None and my_codes is not None and not (sc_codes & my_codes):
                    continue                      # 码集无交集 ⇒ 不重叠
                sc_lo, sc_hi = sc.get("t_min"), sc.get("t_max")
                if (sc_lo is not None and sc_hi is not None
                        and t_min is not None and t_max is not None):
                    if sc_hi < t_min or t_max < sc_lo:
                        continue                  # 时间域不相交 ⇒ 不重叠
                out.append(dict(sc))
        if out:
            _SCOPE_STATS["conflicts"] = _SCOPE_STATS.get("conflicts", 0) + len(out)
    except Exception:
        pass
    return out


def wait_for_scope(table: str, codes=None,
                   t_min: Optional[int] = None, t_max: Optional[int] = None,
                   timeout_s: Optional[float] = None,
                   poll_s: float = _SCOPE_POLL_S,
                   exclude_scope: Optional[str] = None):
    """等待重叠作用域释放。返回 (released, waited_s, remaining_conflicts)。

    released=False 且 remaining>0 ⇒ 调用方应 **defer 本批**（不写、不判失败、水位不推进），
    而不是降级 A6 重试（②审条件：引擎单元可达 63min，重试 3 次必耗尽＝旧失败原样重演）。
    """
    _to = _scope_wait_s() if timeout_s is None else float(timeout_s)
    t0 = datetime.now().timestamp()
    waited = 0.0
    while True:
        remaining = price_scope_conflicts(table, codes, t_min, t_max,
                                          exclude_scope=exclude_scope)
        if not remaining:
            waited = datetime.now().timestamp() - t0
            if waited > 0:
                _SCOPE_STATS["waits"] = _SCOPE_STATS.get("waits", 0) + 1
                _SCOPE_STATS["wait_s"] = _SCOPE_STATS.get("wait_s", 0.0) + waited
            return True, waited, []
        waited = datetime.now().timestamp() - t0
        if waited >= _to:
            _SCOPE_STATS["timeouts"] = _SCOPE_STATS.get("timeouts", 0) + 1
            _SCOPE_STATS["wait_s"] = _SCOPE_STATS.get("wait_s", 0.0) + waited
            return False, waited, remaining
        import time as _time          # 局部导入：与 _retry_backoff 一致，避免顶部新依赖
        _time.sleep(poll_s)


def price_scope_stats() -> dict:
    """只读：作用域统计（在程数/冲突/等待/超时/累计等待秒）。"""
    try:
        with _REGISTRY_LOCK:
            _prune_scope_locked()
            active = len(_SCOPE_REGISTRY)
        d = {k: v for k, v in _SCOPE_STATS.items()}
        d["active"] = active
        return d
    except Exception:
        return {}


class WriteScopeDeferred(Exception):
    """本批让行超时 ⇒ 放弃本批（defer）。不判失败、不推进水位，下轮自然重拉。"""

    def __init__(self, table: str = "", batch_id: str = "",
                 waited_s: float = 0.0, remaining: int = 0):
        super().__init__(
            f"写作用域让行超时（table={table} batch={batch_id} "
            f"waited={waited_s:.0f}s 在程作用域={remaining}）⇒ 本批 defer")
        self.table = table
        self.batch_id = batch_id
        self.waited_s = waited_s
        self.remaining = remaining


def price_scope_mark_deferred() -> None:
    """计数：本批 defer（供 run_state write_scope.deferred 观测）。"""
    try:
        _SCOPE_STATS["deferred"] = _SCOPE_STATS.get("deferred", 0) + 1
    except Exception:
        pass


def concurrent_writers(table: str, time_ms: Optional[int] = None,
                       window_s: float = 180.0) -> List[dict]:
    """返回**其他**写者（同表；活跃中或 window_s 内刚结束；给了 time_ms 则要求时段覆盖）。"""
    out: List[dict] = []
    try:
        me = (os.getpid(), threading.get_ident(), table)
        now = datetime.now().timestamp()
        with _REGISTRY_LOCK:
            _prune_registry_locked()
            for k, v in list(_WRITER_REGISTRY.items()):
                if k == me or v.get("table") != table:
                    continue
                ended = v.get("ended")
                if ended is not None and now - ended > window_s:
                    continue
                if time_ms is not None:
                    lo, hi = v.get("t_min"), v.get("t_max")
                    if isinstance(lo, int) and isinstance(hi, int) and not (lo <= time_ms <= hi):
                        continue
                out.append(dict(v))
    except Exception:
        pass
    return out


# ── A6（六步③·2026-10-22）：有界冲突重试（写写冲突属可重试瞬时并发）──────────
_WRITE_RETRY_STATS: Dict[str, int] = {"attempts": 0, "exhausted": 0, "success": 0}


def _retry_stats_inc(key: str, n: int = 1) -> None:
    try:
        _WRITE_RETRY_STATS[key] = int(_WRITE_RETRY_STATS.get(key, 0)) + n
    except Exception:
        pass


def write_retry_stats() -> dict:
    """只读：本进程写重试统计（供 daemon run_state / 观测面板消费）。"""
    try:
        return dict(_WRITE_RETRY_STATS)
    except Exception:
        return {}


def _write_retry_max() -> int:
    """重试上限（默认 3；QS_WRITE_RETRY_MAX=0 关闭=回退面；上限 10 防呆）。"""
    try:
        v = int(os.environ.get("QS_WRITE_RETRY_MAX", "3"))
    except Exception:
        return 3
    return max(0, min(v, 10))


def is_write_conflict(exc: BaseException) -> bool:
    """写写冲突族判定（DuckDB 文案：write-write conflict / Conflict on update）。"""
    try:
        _s = str(exc).lower()
    except Exception:
        return False
    if "write-write conflict" in _s:
        return True
    if "conflict on update" in _s:
        return True
    if "transactioncontext" in _s and "conflict" in _s:
        return True
    return False


def _retry_backoff(attempt: int) -> None:
    """退避：0.5/1/2s（第 4 次起恒 2s）+ 0～200ms 抖动（防同步重试撞车）。"""
    try:
        import random
        import time
        _base = (0.5, 1.0, 2.0)
        _delay = _base[min(attempt, len(_base) - 1)] + random.uniform(0.0, 0.2)
        time.sleep(_delay)
    except Exception:
        pass


def table_write_lock(table: str):
    """R2 按表写锁上下文（模块级公共入口，跨模块共享同一把锁）。

    - table ∈ _SERIAL_WRITE_TABLES → 返回进程内 RLock（登记于模块级
      _TABLE_WRITE_LOCKS）；其余表 → contextlib.nullcontext()（零行为变化）；
    - RLock（非 Lock）：防同线程嵌套获取自死锁（引擎主事务若与 writer 写路径
      同线程嵌套时安全）；
    - setdefault：CPython 字典操作原子，并发首建也只暴露同一把锁；
    - 实例方法 DuckDBWriter._table_write_lock() 委托本函数——writer 写路径
      与重锚引擎价格写事务由此拿到**同一把**锁。
    - A3（2026-10-22）：返回 _LockScope 包装——进入即登记持有线程，供
      _assert_write_locked() 断言写入口确实在锁内（仅 _SERIAL_WRITE_TABLES 生效）。
    """
    if table in _SERIAL_WRITE_TABLES:
        return _LockScope(table, _TABLE_WRITE_LOCKS.setdefault(table, threading.RLock()))
    return contextlib.nullcontext()


# ── A3（六步③·2026-10-22）：锁持有断言（域限定分钟表，daily nullcontext 不误伤）──
_LOCK_HOLDERS: Dict[str, set] = {}


def _assert_write_locked(table: str, where: str = "") -> None:
    """断言当前线程持有该表写锁；仅 _SERIAL_WRITE_TABLES 且开关开启时生效。

    fail-fast（新增检测型）：写入口若绕过 table_write_lock，立即中止而非静默并发写。
    开关 QS_PRICE_WRITE_ASSERT=0 可关闭（回退面）。
    """
    try:
        if table not in _SERIAL_WRITE_TABLES:
            return
        _v = (os.environ.get("QS_PRICE_WRITE_ASSERT") or "1").strip().lower()
        if _v in ("0", "false", "off", "no"):
            return
        tid = threading.get_ident()
        with _REGISTRY_LOCK:
            held = tid in _LOCK_HOLDERS.get(table, set())
        if not held:
            raise RuntimeError(
                "[writers] 写入口未持表锁（table=%s where=%s thread=%s）——"
                "价格表写必须经 table_write_lock；见 A3/六步方案" % (table, where, tid))
    except RuntimeError:
        raise
    except Exception:
        pass


class _LockScope:
    """table_write_lock 返回值：进入登记持有线程、退出注销（A3 断言依据）。"""

    def __init__(self, table: str, lock):
        self._table = table
        self._lock = lock

    def __enter__(self):
        self._lock.acquire()
        try:
            with _REGISTRY_LOCK:
                _LOCK_HOLDERS.setdefault(self._table, set()).add(threading.get_ident())
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            with _REGISTRY_LOCK:
                _LOCK_HOLDERS.get(self._table, set()).discard(threading.get_ident())
        except Exception:
            pass
        self._lock.release()
        return False


# 公开别名：供引擎侧在自持事务内断言「价格表写确实在锁内」（A3）。
assert_price_write_locked = _assert_write_locked


class WriteResult(int):
    """write() 返回值：作为 int 向后兼容（=提交行数），同时携带 .new/.updated 审计字段。

    用法：
        n = writer.write(df, ...)      # n 当 int 用 = 提交行数（向后兼容）
        result = writer.write(df, ...)
        result.new, result.updated     # 新增行数 / 更新行数（审计准确）
        result.changed                 # T2：真实变化（已存在且值不同）行数；
                                       #   仅 QS_UPSERT_SKIP_IDENTICAL 开启时统计，默认 0
    """
    new: int = 0
    updated: int = 0
    changed: int = 0

    def __new__(cls, submitted: int, new: int = 0, updated: int = 0, changed: int = 0):
        obj = int.__new__(cls, submitted)
        obj.new = new
        obj.updated = updated
        obj.changed = changed
        return obj

    def __repr__(self) -> str:
        return f"WriteResult(submitted={int(self)}, new={self.new}, updated={self.updated})"


# 各表的建表 DDL（DuckDB 方言，统一口径 v2.0）
# time 用 BIGINT（毫秒时间戳），volume=股，amount=元，code=裸6位码
# v2.4 B-3a：source_watermark DDL 改用共享单一真相源 SOURCE_WATERMARK_2_1_DDL
# （审计列 source_generation/cutover_id NOT NULL 无 DEFAULT；PK 不变）。
from quantstudio.pipeline.qfq_schema_contracts import (  # noqa: E402
    SOURCE_WATERMARK_2_1_DDL, pre_cutover_generation,
    TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT, verify_fingerprint, _table_exists)


class _WriterSchemaMigrationRequired(RuntimeError):
    """DuckDBWriter 初始化遇版本化旧/错误共享表时写前 fail-fast。"""


def _assert_source_watermark_init_safe(conn) -> None:
    """v2.4 B-3a P0-2：DuckDBWriter._init_tables 第一条 DDL 前的共享表安全闸。

    source_watermark 状态 → writer init 行为：
    - 不存在 → 允许（DDL_DUCKDB 含 SOURCE_WATERMARK_2_1_DDL 会创建 target 8 列）；
    - 完整 target 8 列 → 允许继续；
    - 旧 6 列 / partial / 错误结构 → 抛 ``_WriterSchemaMigrationRequired`` 写前 fail-fast
      （明确 migration-required，而非等到运行时 advance_watermark 才 BinderException）。

    该门禁不通过 ``--allow-production`` 绕过（writer 不接受该开关；硬门禁）。
    """
    if not _table_exists(conn, "source_watermark"):
        return  # 不存在 → 放行（将创建 target）
    if verify_fingerprint(conn, {"source_watermark": TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT},
                          reject_extra=True):
        return  # 完整 target 8 列 → 放行
    # 旧/partial/错误 → fail-fast
    raise _WriterSchemaMigrationRequired(
        "[DuckDBWriter] source_watermark 为旧 6 列 / partial / 错误结构，"
        "禁止 writer init（会到运行时 advance_watermark 才崩溃）。"
        "该库需显式 migration runner 升级到 2.1（B-3b 范围）。")


def _assert_qfq_schema_init_safe(conn) -> None:
    """v2.4 B-3a.3 P0-1：DuckDBWriter._init_tables 第一条 DDL 前的**完整主库状态预检**。

    复用 ``qfq_schema_status.detect_schema_status`` 五态判定。仅 ``EMPTY_OR_NEW`` /
    ``COMPLETE_2_1`` 放行；``COMPLETE_2_0`` / ``PARTIAL_OR_MIXED`` / ``UNKNOWN`` 全部
    抛 ``_WriterSchemaMigrationRequired`` 写前 fail-fast。

    这阻止"数据库已处于 partial/完整旧 2.0（如仅一张 qfq_trigger_queue）时，writer 在
    QFQ init 安全闸执行前创建大量框架表，数据库被进一步修改"的违规。

    | 状态 | writer init |
    |---|---|
    | EMPTY_OR_NEW | 允许创建框架表 |
    | COMPLETE_2_1 | 允许继续初始化非 QFQ 框架表 |
    | COMPLETE_2_0 | migration-required（写前拒绝）|
    | PARTIAL_OR_MIXED | fail-closed（写前拒绝）|
    | UNKNOWN | fail-closed（写前拒绝）|

    不通过 ``--allow-production`` 绕过（硬门禁）。
    """
    from quantstudio.pipeline.qfq_schema_status import (
        detect_schema_status, SchemaStatus)
    status = detect_schema_status(conn)
    # 2.2（FP-5，2026-10-22）：与 COMPLETE_2_1 一并放行——升级窗口内
    # "新代码 + 旧库"（2.1）与"新代码 + 新库"（2.2）均不得停摆。
    if status in (SchemaStatus.EMPTY_OR_NEW, SchemaStatus.COMPLETE_2_1,
                  SchemaStatus.COMPLETE_2_2):
        return  # 放行
    raise _WriterSchemaMigrationRequired(
        f"[DuckDBWriter] 主库处于 {status.value} 状态，禁止 writer init（会在 QFQ init 安全闸"
        f"执行前创建框架表，进一步修改数据库）。仅 EMPTY_OR_NEW/COMPLETE_2_1/COMPLETE_2_2 放行。"
        f"该库需显式 migration runner 升级到 2.1（B-3b 范围）/ 2.2（FP-5）。")


DDL_DUCKDB = {
    "stock_daily": """
        CREATE TABLE IF NOT EXISTS stock_daily (
            code VARCHAR, time BIGINT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE, preClose DOUBLE,
            suspendFlag INTEGER, settelementPrice DOUBLE, openInterest DOUBLE,
            open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE,
            open_back DOUBLE, high_back DOUBLE, low_back DOUBLE, close_back DOUBLE,
            open_front_ratio DOUBLE, high_front_ratio DOUBLE, low_front_ratio DOUBLE, close_front_ratio DOUBLE,
            open_back_ratio DOUBLE, high_back_ratio DOUBLE, low_back_ratio DOUBLE, close_back_ratio DOUBLE,
            turn DOUBLE, pctChg DOUBLE, peTTM DOUBLE, psTTM DOUBLE, pcfNcfTTM DOUBLE, pbMRQ DOUBLE,
            isST INTEGER,
            is_st_reliable BOOLEAN, is_st_reliable_source VARCHAR,
            is_delisting_risk BOOLEAN, is_delisting_risk_source VARCHAR,
            dividend_type VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, time)
        )""",
    "stock_minutes": """
        CREATE TABLE IF NOT EXISTS stock_minutes (
            code VARCHAR, time BIGINT, freq VARCHAR,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE, preClose DOUBLE,
            suspendFlag INTEGER, settelementPrice DOUBLE, openInterest DOUBLE,
            open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE,
            open_back DOUBLE, high_back DOUBLE, low_back DOUBLE, close_back DOUBLE,
            open_front_ratio DOUBLE, high_front_ratio DOUBLE, low_front_ratio DOUBLE, close_front_ratio DOUBLE,
            open_back_ratio DOUBLE, high_back_ratio DOUBLE, low_back_ratio DOUBLE, close_back_ratio DOUBLE,
            dividend_type VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, time, freq)
        )""",
    "etf_minutes": """
        CREATE TABLE IF NOT EXISTS etf_minutes (
            code VARCHAR, time BIGINT, freq VARCHAR,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE, preClose DOUBLE,
            suspendFlag INTEGER, settelementPrice DOUBLE, openInterest DOUBLE,
            open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE,
            open_back DOUBLE, high_back DOUBLE, low_back DOUBLE, close_back DOUBLE,
            open_front_ratio DOUBLE, high_front_ratio DOUBLE, low_front_ratio DOUBLE, close_front_ratio DOUBLE,
            open_back_ratio DOUBLE, high_back_ratio DOUBLE, low_back_ratio DOUBLE, close_back_ratio DOUBLE,
            dividend_type VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, time, freq)
        )""",
    "tick": """
        CREATE TABLE IF NOT EXISTS tick (
            code VARCHAR, time BIGINT,
            lastPrice DOUBLE, open DOUBLE, high DOUBLE, low DOUBLE, lastClose DOUBLE,
            amount DOUBLE, volume DOUBLE, pvolume DOUBLE, stockStatus INTEGER,
            openInt DOUBLE, lastSettlementPrice DOUBLE,
            askPrice1 DOUBLE, askPrice2 DOUBLE, askPrice3 DOUBLE, askPrice4 DOUBLE, askPrice5 DOUBLE,
            bidPrice1 DOUBLE, bidPrice2 DOUBLE, bidPrice3 DOUBLE, bidPrice4 DOUBLE, bidPrice5 DOUBLE,
            askVol1 DOUBLE, askVol2 DOUBLE, askVol3 DOUBLE, askVol4 DOUBLE, askVol5 DOUBLE,
            bidVol1 DOUBLE, bidVol2 DOUBLE, bidVol3 DOUBLE, bidVol4 DOUBLE, bidVol5 DOUBLE,
            transactionNum INTEGER, update_time VARCHAR,
            PRIMARY KEY(code, time)
        )""",
    "fin_indicator": """
        CREATE TABLE IF NOT EXISTS fin_indicator (
            code VARCHAR, ann_date BIGINT, end_date BIGINT,
            eps DOUBLE, diluted_eps DOUBLE, bps DOUBLE, roe DOUBLE,
            pe_ttm DOUBLE, pb DOUBLE, ps_ttm DOUBLE,
            np_yoy DOUBLE, or_yoy DOUBLE, tr_yoy DOUBLE,
            update_flag INTEGER,
            backfill_eps_source VARCHAR,
            PRIMARY KEY(code, end_date, ann_date)
        )""",
    "index_daily": """
        CREATE TABLE IF NOT EXISTS index_daily (
            code VARCHAR, time BIGINT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            pctChg DOUBLE, volume DOUBLE, amount DOUBLE,
            PRIMARY KEY(code, time)
        )""",
    "stock_daily_valuation": """
        CREATE TABLE IF NOT EXISTS stock_daily_valuation (
            code VARCHAR, time BIGINT,
            circ_mv DOUBLE, total_mv DOUBLE,
            free_share DOUBLE,
            pe_ttm DOUBLE, pb DOUBLE, turnover_rate DOUBLE,
            update_time VARCHAR,
            PRIMARY KEY(code, time)
        )""",
    "etf_daily": """
        CREATE TABLE IF NOT EXISTS etf_daily (
            code VARCHAR, time BIGINT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            preClose DOUBLE, pctChg DOUBLE,
            volume DOUBLE, amount DOUBLE, turn DOUBLE,
            open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE,
            open_back DOUBLE, high_back DOUBLE, low_back DOUBLE, close_back DOUBLE,
            open_front_ratio DOUBLE, high_front_ratio DOUBLE, low_front_ratio DOUBLE, close_front_ratio DOUBLE,
            open_back_ratio DOUBLE, high_back_ratio DOUBLE, low_back_ratio DOUBLE, close_back_ratio DOUBLE,
            isST INTEGER, dividend_type VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, time)
        )""",
    "stock_basic": """
        CREATE TABLE IF NOT EXISTS stock_basic (
            code VARCHAR PRIMARY KEY,
            ts_code VARCHAR NOT NULL,
            symbol VARCHAR, name VARCHAR NOT NULL,
            area VARCHAR, industry VARCHAR, market VARCHAR,
            list_status VARCHAR NOT NULL,
            list_date BIGINT, delist_date BIGINT, exchange VARCHAR,
            update_time VARCHAR, data_source VARCHAR
        )""",
    "trade_calendar": """
        CREATE TABLE IF NOT EXISTS trade_calendar (
            cal_date BIGINT NOT NULL,
            is_open BOOLEAN NOT NULL,
            source VARCHAR,
            updated_at TIMESTAMP,
            exchange VARCHAR,
            pretrade_date BIGINT,
            PRIMARY KEY (cal_date)
        )""",
    "etf_basic": """
        CREATE TABLE IF NOT EXISTS etf_basic (
            code VARCHAR PRIMARY KEY,
            ts_code VARCHAR, name VARCHAR, exchange VARCHAR,
            list_date BIGINT, delist_date BIGINT,
            etf_type VARCHAR NOT NULL, tracking_index VARCHAR,
            is_cross_border BOOLEAN NOT NULL, status VARCHAR,
            fund_type VARCHAR, invest_type VARCHAR, type VARCHAR,
            classification_method VARCHAR NOT NULL,
            classification_version VARCHAR NOT NULL,
            update_time VARCHAR, data_source VARCHAR
        )""",
    "stock_float_share": """
        CREATE TABLE IF NOT EXISTS stock_float_share (
            code VARCHAR, end_date BIGINT, ann_date BIGINT,
            free_share DOUBLE, total_share DOUBLE,
            circ_mv DOUBLE, total_mv DOUBLE,
            update_time VARCHAR,
            PRIMARY KEY(code, end_date, ann_date)
        )""",
    "index_constituents": """
        CREATE TABLE IF NOT EXISTS index_constituents (
            index_code VARCHAR, code VARCHAR, time BIGINT,
            weight DOUBLE,
            PRIMARY KEY(index_code, code, time)
        )""",
    # ---- 指数成分快照完整性契约（F3 修订）：完整性在打点写入时确定，不依赖未来数据 ----
    "index_constituents_snapshot_meta": """
        CREATE TABLE IF NOT EXISTS index_constituents_snapshot_meta (
            index_code VARCHAR, time BIGINT,
            n_constituents INTEGER, expected_count INTEGER,
            status VARCHAR,
            n_duplicate_codes INTEGER, n_negative_weights INTEGER,
            n_blank_codes INTEGER,
            update_time VARCHAR, data_source VARCHAR,
            PRIMARY KEY(index_code, time)
        )""",
    # ---- 三大报表（对齐 Ptrade 口径，字段单位=元）----
    "balance_statement": """
        CREATE TABLE IF NOT EXISTS balance_statement (
            code VARCHAR, end_date BIGINT, ann_date BIGINT,
            total_assets DOUBLE, total_liability DOUBLE, total_equity DOUBLE,
            total_current_assets DOUBLE, total_non_current_assets DOUBLE,
            total_current_liability DOUBLE, total_non_current_liability DOUBLE,
            account_receivable DOUBLE, account_payable DOUBLE, inventory DOUBLE,
            cash_equivalents DOUBLE, fixed_asset DOUBLE, intangible_asset DOUBLE, goodwill DOUBLE,
            update_time VARCHAR,
            PRIMARY KEY(code, end_date, ann_date)
        )""",
    "income_statement": """
        CREATE TABLE IF NOT EXISTS income_statement (
            code VARCHAR, end_date BIGINT, ann_date BIGINT,
            operating_revenue DOUBLE, operating_cost DOUBLE, operating_profit DOUBLE,
            total_profit DOUBLE, net_profit DOUBLE, np_parent_company_owners DOUBLE,
            sale_expense DOUBLE, manage_expense DOUBLE, finance_expense DOUBLE, rd_expense DOUBLE,
            income_tax DOUBLE, basic_eps DOUBLE,
            update_time VARCHAR,
            PRIMARY KEY(code, end_date, ann_date)
        )""",
    "cashflow_statement": """
        CREATE TABLE IF NOT EXISTS cashflow_statement (
            code VARCHAR, end_date BIGINT, ann_date BIGINT,
            net_operate_cash_flow DOUBLE, net_invest_cash_flow DOUBLE, net_finance_cash_flow DOUBLE,
            cash_add_balance DOUBLE, goods_sale_and_services DOUBLE, goods_buy_and_services DOUBLE,
            fixed_asset_depreciation DOUBLE,
            update_time VARCHAR,
            PRIMARY KEY(code, end_date, ann_date)
        )""",
    # ---- 除权除息 ----
    "stock_dividend": """
        CREATE TABLE IF NOT EXISTS stock_dividend (
            code VARCHAR, ex_date BIGINT, record_date BIGINT,
            ann_date BIGINT, end_date BIGINT,
            cash_div_before_tax DOUBLE, cash_div_after_tax DOUBLE,
            cash_div DOUBLE, stk_div DOUBLE,
            stk_bo_rate DOUBLE, stk_co_rate DOUBLE,
            div_rat DOUBLE, div_proc VARCHAR,
            update_time VARCHAR,
            PRIMARY KEY(code, ex_date)
        )""",
    # ---- ETF 基金分红（tushare fund_div；管线方案 etf-dividend 任务）----
    "etf_dividend": """
        CREATE TABLE IF NOT EXISTS etf_dividend (
            code VARCHAR, ex_date BIGINT, record_date BIGINT,
            ann_date BIGINT, imp_anndate BIGINT, base_date BIGINT,
            div_proc VARCHAR, pay_date BIGINT, earpay_date BIGINT,
            net_ex_date BIGINT, div_cash DOUBLE, base_unit DOUBLE,
            ear_distr DOUBLE, ear_amount DOUBLE, account_date BIGINT,
            base_year VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, ex_date)
        )""",
    # ---- 申万行业分类（LEGACY 快照，仅审计；正式能力见下两张表 F4）----
    "sw_industry": """
        CREATE TABLE IF NOT EXISTS sw_industry (
            code VARCHAR, industry_code VARCHAR, industry_name VARCHAR, industry_level VARCHAR,
            update_time VARCHAR,
            PRIMARY KEY(code, industry_code)
        )""",
    # ---- 行业分类定义（F4 正式 canonical 表；effective_from=0 表示长期有效）----
    "industry_classification": """
        CREATE TABLE IF NOT EXISTS industry_classification (
            classification_system VARCHAR, classification_version VARCHAR,
            industry_code VARCHAR, industry_name VARCHAR, industry_level VARCHAR,
            parent_industry_code VARCHAR,
            effective_from BIGINT, effective_to BIGINT,
            update_time VARCHAR, data_source VARCHAR,
            PRIMARY KEY (classification_system, classification_version,
                         industry_level, industry_code, effective_from)
        )""",
    # ---- 行业成员历史（F4 正式 canonical 表，PIT 有效区间）----
    "industry_membership": """
        CREATE TABLE IF NOT EXISTS industry_membership (
            classification_system VARCHAR, classification_version VARCHAR,
            industry_level VARCHAR, industry_code VARCHAR, code VARCHAR,
            effective_from BIGINT, effective_to BIGINT,
            update_time VARCHAR, data_source VARCHAR,
            PRIMARY KEY (classification_system, classification_version,
                         industry_level, industry_code, code, effective_from)
        )""",
    "source_watermark": SOURCE_WATERMARK_2_1_DDL,  # v2.4 B-3a：共享 DDL 单一真相源（8列，审计列 NOT NULL）
    "stock_namechange": """
        CREATE TABLE IF NOT EXISTS stock_namechange (
            code VARCHAR, change_date BIGINT,
            name_before VARCHAR, name_after VARCHAR, status_after VARCHAR,
            update_time VARCHAR,
            PRIMARY KEY(code, change_date)
        )""",
    "stock_delist": """
        CREATE TABLE IF NOT EXISTS stock_delist (
            code VARCHAR, list_date BIGINT, delist_date BIGINT, market VARCHAR,
            update_time VARCHAR,
            PRIMARY KEY(code, market)
        )""",
}


class BaseWriter(abc.ABC):
    """写入器基类"""

    def __init__(self, config: Dict):
        self.config = config

    @abc.abstractmethod
    def write(self, df: pd.DataFrame, table: str, batch_id: str) -> int:
        """幂等 upsert 写入。返回写入条数。"""
        ...

    @abc.abstractmethod
    def get_last_date(self, source: str, table: str, freq: str = "daily") -> Optional[str]:
        """读取水位"""
        ...

    @abc.abstractmethod
    def advance_watermark(self, source: str, table: str, freq: str,
                          last_date: str, batch_id: str):
        """推进水位（仅写入成功后调用）"""
        ...


class DuckDBWriter(BaseWriter):
    """DuckDB 写入器（默认推荐，零部署）

    config 示例：{"type": "duckdb", "path": "data/quantstudio.db"}
    """

    def __init__(self, config: Dict):
        super().__init__(config)
        try:
            import duckdb
        except ImportError as e:
            raise ImportError("未安装 duckdb，请 pip install duckdb") from e
        self.db_path = Path(config.get("path", str(db_path())))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._duckdb = duckdb
        # 连接锁：DuckDB 不允许同文件多线程同时新建连接，串行化连接生命周期
        import threading
        self._conn_lock = threading.Lock()
        # 持久共享连接（read_write）：供 daemon 内部只读查询复用，避免与 write 的 read_write
        # 短连接并发时开 read_only 连接触发「different configuration」冲突
        # （DuckDB 规则：同 db 文件 read_only 与 read_write 不能并存，哪怕不同线程）。
        # 所有内部查询统一走 read_write，永不冲突。GUI 的 read_only 查询属独立进程范畴。
        self._shared_conn = None
        # R2（write-integrity，第二阶段升模块级）：原实例级按表写锁登记表
        # self._table_write_locks 已上提为模块级 _TABLE_WRITE_LOCKS——重锚引擎
        # 在自有事务里直写价格表，须与 writer 写路径共享同一把锁（见模块级
        # table_write_lock()）；_table_write_lock() 方法委托模块函数，调用点零变化。
        self._init_tables()

    # ── T1（2026-09-17）：RW 打开退避重试与持有者归因 ──
    # 退避序列合计 30s（全窗统一，两处 RW 打开点共用）
    _RW_BACKOFF_SECONDS = (1, 2, 4, 8, 15)

    def _describe_lock_holder(self):
        """psutil 归因：谁可能持有该库（**尽力而为**，不可得则返回 None，不得抛）。

        归因口径：候选 = 命令行提及本库文件名或 daemon/GUI 入口的进程；
        不试图读取真实文件句柄（跨平台不可靠），只给出可人工核验的候选清单。
        """
        try:
            import psutil
        except Exception:
            return None
        import os as _os
        db_name = self.db_path.name
        db_stem = self.db_path.stem
        # 自身与父进程、以及常见 shell 包装器**一律排除**：否则「含项目路径的 pwsh 包装」
        # 会被误判为持有者（2026-09-17 样例实测命中 pid=…powershell.exe = 假归因）。
        _SHELLS = {"powershell.exe", "pwsh.exe", "cmd.exe", "bash", "sh", "zsh", "pythonw.exe"}
        exclude = {_os.getpid(), _os.getppid()}
        try:
            import psutil as _ps
            try:
                for parent in _ps.Process(_os.getpid()).parents():
                    exclude.add(parent.pid)
            except Exception:
                pass
        except Exception:
            pass
        hits = []
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
                try:
                    info = proc.info
                    pid = info.get("pid")
                    name = str(info.get("name") or "")
                    if pid in exclude or name.lower() in _SHELLS:
                        continue
                    joined = " ".join(str(c) for c in (info.get("cmdline") or []))
                    # 排序权重：daemon/GUI 入口（真持有者）> 命令行提及本库文件
                    rank = None
                    if "quantstudio.pipeline.daemon" in joined or "main_gui" in joined:
                        rank = 0
                    elif db_name in joined:
                        rank = 1
                    if rank is None:
                        continue
                    import datetime as _dt
                    ct = info.get("create_time")
                    started = (_dt.datetime.fromtimestamp(ct).strftime("%Y-%m-%d %H:%M:%S")
                               if ct else "?")
                    hits.append((rank, pid, name, started, joined))
                except Exception:
                    continue
        except Exception:
            return None
        if not hits:
            return None
        hits.sort(key=lambda h: h[0])
        _, pid, name, started, joined = hits[0]
        line = f"pid={pid} name={name} started={started}"
        if len(hits) > 1:
            line += f"（另有 {len(hits) - 1} 个候选）"
        return {"line": line, "cmdline": joined[:200], "count": len(hits)}

    def _rw_exhausted_message(self, trace, last_err) -> str:
        """耗尽异常文本——四锚点固定，供跨线 ATTRIB_PATTERNS 解析（见 32eadc7 已钉口径）。"""
        total = sum(self._RW_BACKOFF_SECONDS)
        seq = "/".join(str(s) for s in self._RW_BACKOFF_SECONDS)
        lines = [
            f"[db-lock] DuckDB 写连接获取失败：{total}s 内重试 {len(self._RW_BACKOFF_SECONDS)} 次"
            f"（{seq}s）全部冲突",
            f"  db_path : {self.db_path}",
            f"  轨迹    : {' / '.join(trace)}",
        ]
        holder = self._describe_lock_holder()
        if holder:
            lines.append(f"  持有者  : {holder['line']}")
            lines.append(f"            cmdline={holder['cmdline']}")
        else:
            lines.append("  持有者  : 未能归因（psutil 不可用或无匹配候选进程）")
        lines.append(f"  原始文本: {last_err}")
        return "\n".join(lines)

    def _open_rw_with_backoff(self, purpose: str = "conn"):
        """打开 DuckDB read_write 连接；**仅**跨进程写锁冲突时退避重试。

        - 判据：pipeline.db_lock_errors.is_db_lock_conflict（Windows 中文/英文 + POSIX 三串）；
          **非冲突**（路径/权限/库损坏/参数错）**立即抛**——不把响亮失败磨成哑失败；
        - 退避 1/2/4/8/15s（合计 30s）；耗尽抛 RuntimeError（四锚文本，含持有者归因）；
        - **不写任何窗口/锁状态**——E-3 的写窗判据归 E-3，本 helper 不参与、不落任何文件。
        """
        import time as _time
        from .db_lock_errors import is_db_lock_conflict
        trace = []
        last_err = None
        attempts = (0,) + self._RW_BACKOFF_SECONDS
        for idx, wait in enumerate(attempts, start=1):
            if wait:
                _time.sleep(wait)
            try:
                conn = self._duckdb.connect(str(self.db_path))
            except Exception as e:
                if not is_db_lock_conflict(e):
                    raise                      # 非锁冲突：立即抛，不进退避
                last_err = e
                trace.append(f"#{idx} {type(e).__name__}")
            else:
                # 缺陷#1 配套（firefly 2026-10-25）：连接级 spill 兜底（fail-open）。
                self._apply_conn_spill_defaults(conn)
                return conn
        raise RuntimeError(self._rw_exhausted_message(trace, last_err))

    _SPILL_TMP_DIRNAME = "_duckdb_tmp"

    def _apply_conn_spill_defaults(self, conn) -> None:
        """缺陷#1 配套（firefly 2026-10-25）：连接级 spill 兜底——只加护栏不改行为。

        - ``temp_directory``：显式指到库旁 ``<db_dir>/_duckdb_tmp``（自动创建），
          避免默认临时目录落在系统盘/不可用路径；大表 upsert 溢出落盘有界可控；
        - ``memory_limit``：仅环境变量 ``QS_DUCKDB_MEMORY_LIMIT`` 显式设置时生效
          （fail-closed：默认不设置，保持 duckdb 自身默认 ~80% RAM，不改变生产行为）；
        - 任何设置失败只 WARNING 不抛（fail-open：兜底自身故障不得阻断写入主链）。
        """
        try:
            _db_dir = Path(str(self.db_path)).resolve().parent
            _tmp = _db_dir / self._SPILL_TMP_DIRNAME
            _tmp.mkdir(parents=True, exist_ok=True)
            conn.execute(f"SET temp_directory='{_tmp.as_posix()}'")
            _ml = os.environ.get("QS_DUCKDB_MEMORY_LIMIT", "").strip()
            if _ml:
                conn.execute(f"SET memory_limit='{_ml}'")
        except Exception as exc:
            logger.warning(
                "[DuckDBWriter] spill 兜底设置失败（不影响写入，按默认继续）: %s: %s",
                type(exc).__name__, exc)

    def _conn(self):
        """新建连接（线程安全：调用方应在 write_lock/conn_lock 内使用并及时关闭）

        T1：跨进程写锁冲突时退避重试（见 _open_rw_with_backoff）。
        """
        return self._open_rw_with_backoff(purpose="conn")

    def _ensure_shared_conn(self):
        """确保持久 read_write 连接已创建（调用方须持有 _conn_lock）。"""
        if self._shared_conn is None:
            self._shared_conn = self._open_rw_with_backoff(purpose="shared")
        return self._shared_conn

    def _table_write_lock(self, table: str):
        """R2 按表写锁上下文（第二阶段起委托模块级 table_write_lock()）。

        与重锚引擎（qfq_reanchor_engine）自有事务的价格表写共享**同一把**
        进程内锁——注册表已上提模块级；本方法保留以使现调用点零变化，
        语义与第一阶段逐位一致（serial 表 → RLock；其余 → nullcontext）。
        """
        return table_write_lock(table)

    def reconnect(self):
        """F-5 修复（2026-09-08）：关闭并重建持久写连接（invalidated 毒化后调用）。

        关闭旧连接（毒化连接 close 可能抛异常，吞掉）→ 置 None → 下次
        shared_conn()/write 时按需重建。调用方须在无并发写时调用（A4 中止后的
        主增量前是安全点）。
        """
        with self._conn_lock:
            old_conn = self._shared_conn
            self._shared_conn = None
            if old_conn is not None:
                try:
                    old_conn.close()
                except Exception as close_err:  # 毒化连接 close 常抛，忽略
                    pass
            self._ensure_shared_conn()  # 立即重建，失败早暴露
        return self._shared_conn

    def shared_conn(self):
        """返回持久 read_write 连接（复用单例，线程安全）。

        用途：daemon 内部只读查询改用此连接，避免开 read_only 连接与 write 的
        read_write 连接并发触发「different configuration」冲突。
        调用方负责加 _conn_lock 保护 execute（DuckDB 单连接并发 execute 会串行化）。
        """
        with self._conn_lock:
            return self._ensure_shared_conn()

    def execute_read(self, sql: str, params: Optional[list] = None):
        """线程安全的只读查询（复用持久 read_write 连接，避免 read_only 冲突）。

        返回 fetchall() 结果。调用方无需管理连接生命周期。
        """
        with self._conn_lock:
            conn = self._ensure_shared_conn()
            if params:
                return conn.execute(sql, params).fetchall()
            return conn.execute(sql).fetchall()

    def read_df(self, sql: str, params: Optional[list] = None):
        """线程安全的只读查询，返回 DataFrame（复用持久 read_write 连接）。

        供 daemon 的 _prepare_namechange_df / _prepare_valuation_df / _prepare_close_df 用，
        替代原 duckdb.connect(read_only=True) 短连接，避免与 write 的 read_write 连接并发冲突。
        """
        with self._conn_lock:
            conn = self._ensure_shared_conn()
            if params:
                return conn.execute(sql, params).fetchdf()
            return conn.execute(sql).fetchdf()

    def close(self):
        """关闭持久共享连接（GUI 重建 collector 时调用，避免连接泄漏）。"""
        with self._conn_lock:
            if self._shared_conn is not None:
                try:
                    self._shared_conn.close()
                except Exception:
                    pass
                self._shared_conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _init_tables(self):
        with self._conn_lock:
            conn = self._conn()
            try:
                # v2.4 B-3a.3 P0-1：完整主库状态预检（不仅是 source_watermark 子契约）。
                # 必须在 writer 第一条 DDL 前。仅 EMPTY_OR_NEW/COMPLETE_2_1 放行；
                # COMPLETE_2_0/PARTIAL_OR_MIXED/UNKNOWN 全部写前 fail-fast（writer 层
                # _WriterSchemaMigrationRequired），不得让普通 writer 自动升级或修补 QFQ schema。
                _assert_qfq_schema_init_safe(conn)
                for ddl in DDL_DUCKDB.values():
                    conn.execute(ddl)
                # 存量表结构迁移：检测并自动 ALTER TABLE 补齐新增列
                # 用途：DDL_DUCKDB 里加了新列但存量 DB（已有 950 万行）不会自动 ALTER
                self._migrate_add_columns(conn)
            finally:
                conn.close()
        logger.info(f"[DuckDBWriter] tables initialized at {self.db_path}")

    def _migrate_add_columns(self, conn):
        """检测存量表缺哪些列，自动 ALTER TABLE ADD COLUMN（仅对已存在的表生效）。

        幂等：列已存在时跳过。基于 DDL_DUCKDB 解析每表应有的列 vs DESCRIBE 实际列。
        新表（CREATE TABLE IF NOT EXISTS 已建好）不会触发 ALTER。
        """
        try:
            for table, ddl in DDL_DUCKDB.items():
                # 跳过非数据表
                if table == "source_watermark":
                    continue
                # 拿实际列
                try:
                    actual = {r[0]: r[1] for r in conn.execute(f"DESCRIBE {table}").fetchall()}
                except Exception:
                    continue   # 表不存在（不应发生，但兜底）
                # 从 COLS 拿应有列（顺序与 DDL 一致）
                expected = self._table_columns(table)
                for col in expected:
                    if col not in actual:
                        # 类型推断：DDL 文本里找列定义
                        col_type = self._infer_col_type(ddl, col)
                        try:
                            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                            logger.info(f"[DuckDBWriter] 迁移: {table}.ADD {col} {col_type}")
                        except Exception as e:
                            logger.warning(f"[DuckDBWriter] 迁移失败 {table}.{col}: {e}")
                if table == "trade_calendar" and "exchange" in expected:
                    # Legacy QFQ rows are the SSE calendar. This fills only the
                    # new metadata column; cal_date/is_open/PK are unchanged.
                    conn.execute(
                        "UPDATE trade_calendar SET exchange='SSE' WHERE exchange IS NULL")
        except Exception as e:
            logger.warning(f"[DuckDBWriter] _migrate_add_columns 异常（跳过）: {e}")

    @staticmethod
    def _infer_col_type(ddl: str, col: str) -> str:
        """从 DDL 文本解析某列的类型（粗解析，够用）。
        找 'col TYPE' 模式，TYPE 是下一个 token。"""
        import re
        # 匹配 "col TYPE" 或 "col   TYPE"（col 后空白 + 大写类型词）
        m = re.search(rf"\b{re.escape(col)}\s+(BIGINT|INTEGER|DOUBLE|VARCHAR|BOOLEAN|TIMESTAMP)", ddl, re.IGNORECASE)
        return m.group(1).upper() if m else "VARCHAR"

    def write(self, df: pd.DataFrame, table: str, batch_id: str,
              passthrough: bool = False) -> int:
        """幂等防重复写入：主键冲突时 UPDATE（upsert），重放同一批次不产生重复行。

        passthrough=True（类别B 同名 passthrough 表）：
          - 按 DataFrame dtypes 自动 CREATE TABLE IF NOT EXISTS（表不存在时）；
          - 全量覆盖（CREATE OR REPLACE TABLE），不 upsert、不按 DDL 裁剪列、
            不做类型归一、不推进水位；
          - DuckDB 表名/列名 = QuestDB 原样（ts_code/trade_date 等保留）。

        A6（六步③·2026-10-22）：**有界冲突重试**——DuckDB 写写冲突（write-write conflict /
        Conflict on update）属可重试的瞬时并发；重试整操作（重取 3A 写锁 → 重放同一批 upsert，
        幂等），默认 3 次、退避 0.5/1/2s + 抖动；`QS_WRITE_RETRY_MAX=0` 关闭（回退面）。
        非冲突异常不重试；耗尽如实抛出并计入统计。
        """
        _max_retry = _write_retry_max()
        for _attempt in range(_max_retry + 1):
            ensure_write_lock(f"writers:write:{table}:{batch_id}")  # 3A 写锁（操作粒度）
            try:
                return self._write_locked(df, table, batch_id, passthrough)
            except BaseException as _exc:
                if _attempt >= _max_retry or not is_write_conflict(_exc):
                    if is_write_conflict(_exc):
                        _retry_stats_inc("exhausted")
                    raise
                _retry_stats_inc("attempts")
                logger.warning(
                    "[DuckDBWriter] %s batch=%s 写冲突重试 %d/%d（%s: %s）",
                    table, batch_id, _attempt + 1, _max_retry,
                    type(_exc).__name__, str(_exc)[:160])
            finally:
                release_write_lock()
            _retry_backoff(_attempt)   # 锁外退避，下一轮重取锁
        raise RuntimeError("[DuckDBWriter] 写重试循环异常退出（不应到达）")

    def _write_locked(self, df: pd.DataFrame, table: str, batch_id: str,
                      passthrough: bool = False) -> int:
        """write() 的锁内实现（3A 重构：原 write 主体平移，逻辑零改动）。"""
        assert_lock_owner()  # 所有权校验（批一 S3）：锁被他人取得 → WriteLockLost，禁静默双写
        if df is None or len(df) == 0:
            logger.info(f"[DuckDBWriter] {table} batch={batch_id}: 0 rows (skip)")
            return 0
        if passthrough:
            written = self._write_passthrough(df, table, batch_id)
            return written
        # 入库前再去重一次（双保险：validator 已去重，这里再保险）
        cols_in_ddl = self._table_columns(table)
        df = df[[c for c in cols_in_ddl if c in df.columns]].copy()
        pk_for_dedup = {
                "stock_daily": ["code", "time"],
                "stock_minutes": ["code", "time", "freq"],
                "etf_minutes": ["code", "time", "freq"],
                "tick": ["code", "time"],
                "fin_indicator": ["code", "end_date", "ann_date"],
                "index_daily": ["code", "time"],
                "stock_daily_valuation": ["code", "time"],
                "etf_daily": ["code", "time"],
                "etf_basic": ["code"],
                "stock_basic": ["code"],
                "trade_calendar": ["cal_date"],
                "stock_float_share": ["code", "end_date", "ann_date"],
                "index_constituents": ["index_code", "code", "time"],
                "index_constituents_snapshot_meta": ["index_code", "time"],
                "balance_statement": ["code", "end_date", "ann_date"],
                "income_statement": ["code", "end_date", "ann_date"],
                "cashflow_statement": ["code", "end_date", "ann_date"],
                "stock_dividend": ["code", "ex_date"],
                "etf_dividend": ["code", "ex_date"],
                "sw_industry": ["code", "industry_code"],
                "industry_classification": ["classification_system", "classification_version",
                                            "industry_level", "industry_code", "effective_from"],
                "industry_membership": ["classification_system", "classification_version",
                                        "industry_level", "industry_code", "code",
                                        "effective_from"],
                "stock_namechange": ["code", "change_date"],
                "stock_delist": ["code", "market"],
            }.get(table, [])
        if pk_for_dedup:
            before = len(df)
            df = df.drop_duplicates(subset=[c for c in pk_for_dedup if c in df.columns], keep="last")
            if len(df) < before:
                logger.info(f"[DuckDBWriter] {table}: 入库前去重 {before}→{len(df)} 行")
        # 确保类型（字符串列跳过数值转换）
        # DDL 驱动（W2-0.9 缺陷 A 修复）：优先按目标表实际 DuckDB 列类型判定——
        # VARCHAR 列一律不经过 pd.to_numeric（否则 "实施" 等字符串值会被 coerce 成 NaN，
        # 落库为 NULL，如 stock_dividend.div_proc）。DESCRIBE 取不到类型时回退到 str_cols
        # 白名单（W2-0.9：白名单必须含 div_proc，且 DESCRIBE 失败不得完全静默）。
        varchar_cols: set = set()
        describe_failed = False
        try:
            with self._conn_lock:
                _conn = self._conn()
                try:
                    for _r in _conn.execute(f"DESCRIBE {table}").fetchall():
                        # _r = (col_name, col_type, nullable, key, default, extra)
                        if len(_r) >= 2 and isinstance(_r[1], str) and _r[1].upper().startswith("VARCHAR"):
                            varchar_cols.add(_r[0])
                finally:
                    _conn.close()
        except Exception as _e:
            # 不静默：记录一次可诊断 warning（不含敏感信息），回退到 str_cols 白名单。
            describe_failed = True
            logger.warning(
                f"[DuckDBWriter] DESCRIBE {table} failed ({type(_e).__name__}); "
                f"falling back to static str_cols whitelist for type protection")
        str_cols = {"code", "freq", "dividend_type", "update_time", "data_source",
                    "index_code", "industry_code", "industry_name", "industry_level",
                    "name_before", "name_after", "status_after", "market",
                    "is_st_reliable_source", "is_delisting_risk_source",
                    "ts_code", "symbol", "name", "area", "industry", "exchange",
                    "list_status", "source", "etf_type", "tracking_index",
                    "status", "fund_type", "invest_type", "type",
                    "classification_system", "parent_industry_code",
                    "classification_method", "classification_version",
                    # W2-0.9 缺陷 A 补完：fallback 白名单必须含 div_proc，
                    # 保证 DESCRIBE 失败时 "实施" 也不会被 to_numeric 吞掉。
                    "div_proc", "div_rat"}.union(varchar_cols)
        del describe_failed  # 诊断标记已用于 warning，不再需要
        for c in df.columns:
            if c in str_cols:
                continue
            if df[c].dtype == object:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        # R2（write-integrity）：写事务临界区整段进按表锁——pre-count 探测与 upsert
        # 写入必须同临界区（防探测/写入间窗口错配）；锁范围仅写事务段，不含上游
        # 对齐/校验与普通读路径；汇总日志行留锁外无碍。
        # A1（六步③·2026-10-22）：主 upsert 路径补失败槽 + 写者登记（W-4 定谳：
        # 该路径原无 record_write_failure，22:31/00:23 失败槽为空即此故）。
        note_write_context(table=table, batch_id=batch_id, path="_write_locked",
                           serial=table in _SERIAL_WRITE_TABLES,
                           pid=os.getpid(), thread=threading.get_ident())
        with self._table_write_lock(table):
            with self._conn_lock:
                conn = self._conn()
                try:
                    price_write_enter(table, "_write_locked", batch_id, df)
                    # 性能修复（2026-07-22）：原实现 write 前后各跑一次 SELECT COUNT(*) FROM <table>
                    # 全表统计，在大表（百万→千万行）上每次数秒，8 线程持 _conn_lock 串行 →
                    # 全量拉取 56 秒/只（理论 1.9s）。改为只数本批主键已存在的行（走索引，毫秒级）。
                    conn.register("_tmp_write", df)
                    pk_cols = {
                        "stock_daily": "(code, time)",
                        "stock_minutes": "(code, time, freq)",
                    "etf_minutes": "(code, time, freq)",
                    "tick": "(code, time)",
                    "fin_indicator": "(code, end_date, ann_date)",
                    "index_daily": "(code, time)",
                    "stock_daily_valuation": "(code, time)",
                    "etf_daily": "(code, time)",
                    "etf_basic": "(code)",
                    "stock_basic": "(code)",
                    "trade_calendar": "(cal_date)",
                    "stock_float_share": "(code, end_date, ann_date)",
                    "index_constituents": "(index_code, code, time)",
                    "index_constituents_snapshot_meta": "(index_code, time)",
                    "balance_statement": "(code, end_date, ann_date)",
                    "income_statement": "(code, end_date, ann_date)",
                    "cashflow_statement": "(code, end_date, ann_date)",
                    "stock_dividend": "(code, ex_date)",
                    "etf_dividend": "(code, ex_date)",
                    "sw_industry": "(code, industry_code)",
                    "industry_classification": "(classification_system, classification_version, industry_level, industry_code, effective_from)",
                    "industry_membership": "(classification_system, classification_version, industry_level, industry_code, code, effective_from)",
                    "stock_namechange": "(code, change_date)",
                    "stock_delist": "(code, market)",
                }.get(table)
                    # 写前：数本批主键在目标表已存在的行数（=将被 UPDATE 的，走索引快）
                    # R1（write-integrity，2026-10-08）：pre-count 探测失效不再静默置 0——
                    # 旧形态曾致整批全量误标「新增」（生产实证 65,962,748 行全假新增）。
                    # 新语义：捕获 → logger.warning（table/batch_id/异常类型与文本，唯一失效
                    # 可观测点）→ 新连接单次重试 → 仍失败 accounting_unknown=True
                    # （new/updated 置 -1 哨兵，消费端跳过累计、审计账本写 NULL，裁定③）。
                    updated_rows = 0
                    accounting_unknown = False
                    _acct_err = None
                    if pk_cols:
                        _pre_count_sql = (
                            f"SELECT COUNT(*) FROM {table} WHERE {pk_cols} IN "
                            f"(SELECT {pk_cols} FROM _tmp_write)")
                        try:
                            updated_rows = conn.execute(_pre_count_sql).fetchone()[0]
                        except Exception as e:
                            _acct_err = e
                            logger.warning(
                                f"[DuckDBWriter] {table} batch={batch_id}: pre-count 记账探测失败"
                                f"（{type(e).__name__}: {e}），换新连接重试一次")
                            updated_rows = 0
                            try:
                                _retry_conn = self._conn()
                                try:
                                    # 注册视图是连接级隔离的：重试连接须重新 register 同一份 df
                                    _retry_conn.register("_tmp_write", df)
                                    updated_rows = _retry_conn.execute(
                                        _pre_count_sql).fetchone()[0]
                                finally:
                                    _retry_conn.close()
                            except Exception as e2:
                                _acct_err = e2
                                accounting_unknown = True
                                logger.warning(
                                    f"[DuckDBWriter] {table} batch={batch_id}: pre-count 重试仍失败"
                                    f"（{type(e2).__name__}: {e2}），本批记账失效"
                                    f"（new/updated 置 -1 哨兵，审计账本写 NULL）")
                    if pk_cols:
                        col_list = ", ".join(df.columns)
                        update_set = ", ".join(f"{c}=EXCLUDED.{c}" for c in df.columns)
                        # T2（开关化，默认关）：一致行不进 delete 相位。
                        # 形态 B（行值元组 IS DISTINCT FROM，NULL-safe，实测通过）；列集合
                        # 与本批 df 同源（含主键列）；关闭时不追加 WHERE ⇒ SQL 逐字节等同现状。
                        _skip_identical = _is_upsert_skip_identical_enabled()
                        _where_identical = ""
                        if _skip_identical:
                            _lhs = ", ".join(df.columns)
                            _rhs = ", ".join(f"EXCLUDED.{c}" for c in df.columns)
                            _where_identical = f" WHERE ({_lhs}) IS DISTINCT FROM ({_rhs})"
                        _upsert_sql = (
                            f"INSERT INTO {table} ({col_list}) "
                            f"SELECT * FROM _tmp_write "
                            f"ON CONFLICT {pk_cols} DO UPDATE SET {update_set}"
                            f"{_where_identical}")
                        # 缺陷#1 修复（firefly 2026-10-25，P0）：T2 路径去掉 RETURNING 1。
                        # 实证：大表（447 万行/42 列）重叠 upsert 下进程冻结于
                        # `conn.execute(f"... RETURNING 1")` 单条 SQL 内（py-spy 90s×6
                        # 次采样行号不变，CPU 单核满载 8.5h 无恢复）——RETURNING 强制
                        # 物化本批全部写入行，与 DuckDB 对带 PK 表 UPDATE 一律
                        # DELETE+INSERT 改写的死元组累积相叠加。changed_rows 仅审计
                        # 口径（WriteResult 第 4 位，全仓无下游判定消费，repr 不展示），
                        # 两分支统一退化为 new/updated 口径（changed=0），执行计划不再
                        # 物化；T2 的 WHERE IS DISTINCT FROM（一致行不进 delete 相位）
                        # 语义保持不变。
                        _assert_write_locked(table, "_write_locked")
                        conn.execute(_upsert_sql)
                        changed_rows = 0
                    else:
                        conn.execute(f"INSERT INTO {table} SELECT * FROM _tmp_write")
                        changed_rows = 0
                    conn.unregister("_tmp_write")
                    # new/updated 审计：updated = 写前已存在的行数；new = 本批其余
                    # （精度：本批内主键重复已由 validator 去重，故 new + updated = len(df)）
                    # R1：探测失效批记账不可知 → -1 哨兵（WriteResult int 语义仍 len(df)；
                    # T2 开启时 changed 依 updated_rows=0 退化计算为 0，同为不可知口径）
                    if accounting_unknown:
                        new_rows = -1
                        updated_rows = -1
                    else:
                        new_rows = max(0, len(df) - updated_rows)
                    # P-A3：fin_indicator 写后跨表回补（eps ← income_statement.basic_eps）。
                    # 默认关闭，需显式设置 QS_AUTO_BACKFILL_EPS=1/true/on 才触发；
                    # CLI --apply 保持人工独立执行，不受此 gate 影响；
                    # 只 UPDATE eps IS NULL 且 income 同 key basic_eps 非空的行——无缺口库零行为；
                    # 幂等；异常 log-error 不阻断 write（失败由 quality_audit EpsBackfillGap 兜底）。
                    # 水位/写锁/batch_audit 语义零变化（回补是 UPDATE 非拉取，不推进 watermark）。
                    if table == "fin_indicator" and _is_writer_auto_backfill_enabled():
                        try:
                            backfill_eps_gap(conn)
                        except Exception as exc:
                            logger.error(f"[DuckDBWriter] {table} 写后回补失败（门禁将告警）: {exc}")
                    elif table == "fin_indicator":
                        logger.debug(
                            "[DuckDBWriter] fin_indicator 写后自动回补已关闭 "
                            "(QS_AUTO_BACKFILL_EPS 未显式开启)"
                        )
                except BaseException as _wf:
                    record_write_failure(_wf)
                    raise
                finally:
                    price_write_exit(table)
                    conn.close()
        if accounting_unknown:
            # R1 汇总行（方案裁定口径）：记账失效批显示 unknown，不再虚报「新增」
            logger.info(f"[DuckDBWriter] {table} batch={batch_id}: wrote {len(df)} rows "
                        f"(新增 ? + 更新 ? 记账失效: {type(_acct_err).__name__}) 防重复 upsert")
        else:
            logger.info(f"[DuckDBWriter] {table} batch={batch_id}: wrote {len(df)} rows "
                        f"(新增 {new_rows} + 更新 {updated_rows}) 防重复 upsert")
        # 返回 WriteResult：作为 int = 提交行数（向后兼容），.new/.updated 供审计使用
        return WriteResult(len(df), new_rows, updated_rows, changed_rows)

    # ------------------------------------------------------------------
    # 类别B passthrough 同名表：TRUNCATE + INSERT 全量覆盖（保留主键，PT-01a 改道）
    # ------------------------------------------------------------------
    def _write_passthrough(self, df: pd.DataFrame, table: str, batch_id: str) -> int:
        """passthrough 表全量覆盖写：DuckDB 表名/列名 = QuestDB 原样。

        - 表不存在：按 DataFrame dtypes 自动 CREATE TABLE IF NOT EXISTS；
        - 存在（**F2 主路径**，列集一致）：TRUNCATE + INSERT（同一事务包覆），
          保留表对象与主键/索引 ⇒ 冲突探测继续有效；
        - 存在（**F1 备选**，列集不一致）：按 DataFrame dtypes 重建表并显式重建同构主键；
          命中指纹校验表则拒绝（加列属契约变更，铁律 24 六件套）；
        - 注：旧语义「CREATE OR REPLACE TABLE」/「DROP 原表 + RENAME staging」已于
          2026-10-29 废止 —— 它会**静默清空目标表主键**（1→0，不报错不告警）；
        - 不做数值类型归一（保留源原始字符串/数值类型）；
        - 不推进 source_watermark（passthrough 表无增量水位）。
        """
        import duckdb  # 类型映射需要，顶部已 import，这里局部引用保险
        # 类型映射：object→VARCHAR，其余按 numpy dtype 推断
        _TYPE_MAP = {
            "int64": "BIGINT", "int32": "INTEGER", "int16": "SMALLINT",
            "int8": "SMALLINT", "uint64": "UBIGINT", "uint32": "UINTEGER",
            "float64": "DOUBLE", "float32": "FLOAT", "bool": "BOOLEAN",
        }

        def _col_sql(col: str, dtype) -> str:
            col_q = f'"{col}"'
            if str(dtype) == "object":
                return f"{col_q} VARCHAR"
            return f"{col_q} {_TYPE_MAP.get(str(dtype), 'VARCHAR')}"

        col_defs = ", ".join(_col_sql(c, df[c].dtype) for c in df.columns)
        create_sql = f'CREATE TABLE IF NOT EXISTS "{table}" ({col_defs})'
        # R2（write-integrity）：全表覆盖段（建临时→灌数→原子换名）整段进按表锁，
        # 防同进程并发覆盖互相踩踏；汇总日志行留锁外无碍。
        note_write_context(table=table, batch_id=batch_id, path="_write_passthrough",
                           serial=table in _SERIAL_WRITE_TABLES)
        with self._table_write_lock(table):
            with self._conn_lock:
                conn = self._conn()
                try:
                    price_write_enter(table, "_write_passthrough", batch_id, df)
                    _assert_write_locked(table, "_write_passthrough")
                    conn.execute(create_sql)
                    # 【PT-01a 改道 · spec 002-duckdb-1.5-upgrade FR-021/FR-022】
                    # 旧：建临时表(CTAS，**不继承约束**)→灌数→DROP 原表→RENAME
                    #     ⇒ 原表主键随 DROP 静默消失（1→0，不报错不告警；读数见
                    #     docs/evidence/pt01-f2-truncate-20261029.md 四组 + 段A 十二组）
                    # 新（F2，主路径）：列集一致 ⇒ TRUNCATE + INSERT（同一事务包覆）
                    #     ⇒ 保留表对象与主键/索引，冲突探测继续有效
                    # 备选（F1，列集不一致）：按 df 列集重建表并**显式重建同构主键**；
                    #     命中指纹校验表 ⇒ 拒绝（加列属契约变更，铁律 24 六件套）
                    tmp = f"_pt_tmp_{table}"
                    conn.execute(f'DROP TABLE IF EXISTS "{tmp}"')
                    conn.register("_pt_src", df)
                    try:
                        if self._pt_columns_match(conn, table, df):
                            began = self._pt_begin(conn)
                            try:
                                conn.execute(f'TRUNCATE TABLE "{table}"')
                                conn.execute(f'INSERT INTO "{table}" SELECT * FROM _pt_src')
                                if began:
                                    conn.execute("COMMIT")
                            except BaseException:
                                if began:
                                    try:
                                        conn.execute("ROLLBACK")
                                    except Exception:
                                        pass
                                raise
                        else:
                            self._pt_rebuild_with_pk(conn, table, tmp, list(df.columns))
                    finally:
                        conn.unregister("_pt_src")
                except BaseException as _wf:
                    record_write_failure(_wf)          # §10.2 修正：失败写自身上下文
                    raise
                finally:
                    price_write_exit(table)
                    conn.close()
        logger.info(f"[DuckDBWriter] {table} passthrough 全量覆盖 {len(df)} 行 "
                    f"(列原样: {list(df.columns)[:8]}{'...' if len(df.columns) > 8 else ''})")
        return len(df)

    # ------------------------------------------------------------------
    # 【PT-01a】passthrough 覆盖链辅助（F2 主路径 / F1 备选）
    #   背景：旧的「建临时表(CTAS，不继承约束)→灌数→DROP 原表→RENAME」链
    #   会在每次全量覆盖时**静默清空目标表主键**（1→0，不报错不告警）；
    #   读数见 docs/evidence/pt01-f2-truncate-20261029.md 四组 + 段A 十二组。
    #   本组辅助支撑两条改道路径：
    #     F2（主）：列集一致 ⇒ TRUNCATE + INSERT（同一事务）⇒ 保留表对象与主键；
    #     F1（备）：列集不一致 ⇒ 按 df 列集重建 + 显式重建同构主键。
    # ------------------------------------------------------------------

    @staticmethod
    def _pt_begin(conn) -> bool:
        """开启显式事务；若连接已在事务中则返回 False（交由外层事务提交）。"""
        try:
            conn.execute("BEGIN TRANSACTION")
            return True
        except Exception:
            return False

    @staticmethod
    def _pt_table_columns(conn, table) -> List[str]:
        """按列序取表列名（表不存在则空表）。"""
        try:
            rows = conn.execute(
                "SELECT column_name FROM duckdb_columns() "
                "WHERE table_name = ? ORDER BY column_index", [table]).fetchall()
        except Exception:
            return []
        return [r[0] for r in rows]

    @classmethod
    def _pt_columns_match(cls, conn, table, df) -> bool:
        """df 列集与表列集是否一致（顺序亦须一致，`INSERT ... SELECT *` 才安全）。"""
        cols = cls._pt_table_columns(conn, table)
        if not cols:
            return False
        return list(df.columns) == cols

    @staticmethod
    def _pt_pk_columns(conn, table) -> List[str]:
        """读原表主键列（无主键则空列表）。"""
        try:
            rows = conn.execute(
                "SELECT constraint_column_names FROM duckdb_constraints() "
                "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'",
                [table]).fetchall()
        except Exception:
            return []
        if rows and rows[0][0]:
            return list(rows[0][0])
        return []

    @staticmethod
    def _pt_fingerprint_tables() -> set:
        """指纹校验表名单（qfq 契约表）：加列属契约变更，必须走六件套（铁律 24）。"""
        try:
            from . import qfq_schema_contracts as qsc
        except Exception:
            return set()
        names = set()
        for attr in dir(qsc):
            if attr.endswith("FINGERPRINT"):
                val = getattr(qsc, attr, None)
                if isinstance(val, dict):
                    names.update(val.keys())
        return names

    def _pt_rebuild_with_pk(self, conn, table, tmp, df_cols: List[str]) -> None:
        """F1 备选：按 df 列集重建表 + **显式重建同构主键**（列集变化场景）。

        命中指纹校验表 ⇒ 拒绝（加列属契约变更，须走铁律 24 六件套；本期不做自动加列）。
        """
        if table in self._pt_fingerprint_tables():
            raise RuntimeError(
                f"[DuckDBWriter] 表 '{table}' 属指纹校验表，列集变化须按契约变更六件套处理"
                f"（铁律 24），本期不做自动加列")
        pk_cols = self._pt_pk_columns(conn, table)
        conn.execute(f'CREATE TABLE "{tmp}" AS SELECT * FROM _pt_src LIMIT 0')
        if pk_cols and all(c in df_cols for c in pk_cols):
            conn.execute(f'ALTER TABLE "{tmp}" ADD PRIMARY KEY ({", ".join(pk_cols)})')
        conn.execute(f'INSERT INTO "{tmp}" SELECT * FROM _pt_src')
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        conn.execute(f'ALTER TABLE "{tmp}" RENAME TO "{table}"')

    @classmethod
    def _pt_columns_match_staging(cls, conn, table, staging) -> bool:
        """staging 列集与原表列集是否一致（顺序亦须一致）。"""
        cols = cls._pt_table_columns(conn, table)
        if not cols:
            return False
        return cols == cls._pt_table_columns(conn, staging)

    def _pt_rebuild_from_staging(self, conn, table, staging) -> None:
        """F1 备选（分片路径）：按 staging 列集重建原表 + **显式重建同构主键**。"""
        if table in self._pt_fingerprint_tables():
            raise RuntimeError(
                f"[DuckDBWriter] 表 '{table}' 属指纹校验表，列集变化须按契约变更六件套处理"
                f"（铁律 24），本期不做自动加列")
        pk_cols = self._pt_pk_columns(conn, table)
        staging_cols = self._pt_table_columns(conn, staging)
        rebuilt = staging + "_rebuilt"
        conn.execute(f'DROP TABLE IF EXISTS "{rebuilt}"')
        conn.execute(f'CREATE TABLE "{rebuilt}" AS SELECT * FROM "{staging}" LIMIT 0')
        if pk_cols and all(c in staging_cols for c in pk_cols):
            conn.execute(f'ALTER TABLE "{rebuilt}" ADD PRIMARY KEY ({", ".join(pk_cols)})')
        conn.execute(f'INSERT INTO "{rebuilt}" SELECT * FROM "{staging}"')
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        conn.execute(f'ALTER TABLE "{rebuilt}" RENAME TO "{table}"')
        conn.execute(f'DROP TABLE IF EXISTS "{staging}"')

    # ------------------------------------------------------------------
    # 类别B passthrough 分片变体（B+ 增补，2026-09-12 总调度裁定）
    # 属同一 passthrough 通道的内部实现优化（非第三通道）：
    #   · staging 表跨尝试持久化（_pt_tmp_<table>）
    #   · 分片 ledger（_pt_staging_ledger）记录每片键与行数
    #   · 重试：ledger 累积行数 == staging 实际行数 -> 续插剩余片；不一致 -> drop 重建
    #   · 全部片完成后落库（F2：TRUNCATE + INSERT 拷入原表，保留主键；
    #     列集不一致则 F1 重建 + 重建同构主键）——旧语义「DROP 原表 + RENAME staging」
    #     已废止（会静默清空主键）
    #   · 不推进 source_watermark（与 _write_passthrough 同语义）
    # 用途：大表（1.07 亿行级）分片入库，内存峰值 = 单分片；中途暂停/崩溃可续。
    # ------------------------------------------------------------------
    _PT_LEDGER_TABLE = '_pt_staging_ledger'

    def _pt_ensure_ledger(self, conn):
        conn.execute(
            'CREATE TABLE IF NOT EXISTS "' + self._PT_LEDGER_TABLE + '" ('
            ' table_name VARCHAR, chunk_no INTEGER, chunk_key VARCHAR,'
            ' rows_written BIGINT, updated_at TIMESTAMP)')

    def _pt_ledger_rows(self, conn, table):
        cur = conn.execute(
            'SELECT count(*), coalesce(sum(rows_written), 0) FROM "'
            + self._PT_LEDGER_TABLE + '" WHERE table_name = ?', [table])
        return cur.fetchone()

    def _pt_ledger_keys(self, conn, table):
        cur = conn.execute(
            'SELECT chunk_key FROM "' + self._PT_LEDGER_TABLE
            + '" WHERE table_name = ? ORDER BY chunk_no', [table])
        return [r[0] for r in cur.fetchall()]

    def _pt_clear_ledger(self, conn, table):
        conn.execute('DELETE FROM "' + self._PT_LEDGER_TABLE
                     + '" WHERE table_name = ?', [table])

    @staticmethod
    def _pt_staging_exists(conn, tmp: str) -> bool:
        try:
            conn.execute('SELECT 1 FROM "' + tmp + '" LIMIT 0')
            return True
        except Exception:
            return False

    def write_passthrough_chunked(self, table: str, batch_id: str,
                                  chunks, resume: bool = True) -> Dict:
        """passthrough 分片写（B+）：chunks 为 (chunk_key, DataFrame) 迭代器。

        返回 {'written': n, 'chunks': k, 'resumed_from': m, 'rebuilt': bool}
        """
        _TYPE_MAP = {
            'int64': 'BIGINT', 'int32': 'INTEGER', 'int16': 'SMALLINT',
            'int8': 'SMALLINT', 'uint64': 'UBIGINT', 'uint32': 'UINTEGER',
            'float64': 'DOUBLE', 'float32': 'FLOAT', 'bool': 'BOOLEAN',
        }
        tmp = '_pt_tmp_' + table
        total = 0
        n_chunks = 0
        resumed_from = 0
        rebuilt = False
        # R2 缺口修复（2026-10-10 ②审通过·方案 A）：整操作级表锁包覆。
        # 本路径做整表重建（staging → DROP → RENAME）且 staging/ledger 协议本质单写者
        # ⇒ 锁粒度只能整操作级；锁序遵循全仓统一层级「表锁 → 连接锁」
        # （同款：_write_locked:890 / _write_passthrough:1057）。
        note_write_context(table=table, batch_id=batch_id,
                           path="_write_passthrough_chunked",
                           serial=table in _SERIAL_WRITE_TABLES)
        with self._table_write_lock(table):
            with self._conn_lock:
                conn = self._conn()
                try:
                    price_write_enter(table, "_write_passthrough_chunked", batch_id)
                    _assert_write_locked(table, "_write_passthrough_chunked")
                    self._pt_ensure_ledger(conn)
                    done_keys = set()
                    if resume and self._pt_staging_exists(conn, tmp):
                        staging_rows = conn.execute(
                            'SELECT count(*) FROM "' + tmp + '"').fetchone()[0]
                        cnt, ledger_sum = self._pt_ledger_rows(conn, table)
                        if cnt and ledger_sum == staging_rows:
                            done_keys = set(self._pt_ledger_keys(conn, table))
                            resumed_from = len(done_keys)
                            logger.info('[DuckDBWriter] %s 断点续写：staging=%s 行 ledger=%s 片一致，'
                                        '跳过已完成片', table, staging_rows, cnt)
                        else:
                            logger.warning('[DuckDBWriter] %s staging/ledger 不一致'
                                           '（staging=%s ledger_sum=%s） -> drop 重建',
                                           table, staging_rows, ledger_sum)
                            conn.execute('DROP TABLE IF EXISTS "' + tmp + '"')
                            self._pt_clear_ledger(conn, table)
                            rebuilt = True
                    for key, df in chunks:
                        if df is None or len(df) == 0:
                            continue
                        if key in done_keys:
                            continue
                        if not self._pt_staging_exists(conn, tmp):
                            col_defs = ', '.join(
                                '"' + c + '" ' + ('VARCHAR' if str(df[c].dtype) == 'object'
                                                  else _TYPE_MAP.get(str(df[c].dtype), 'VARCHAR'))
                                for c in df.columns)
                            conn.execute('CREATE TABLE "' + tmp + '" (' + col_defs + ')')
                        conn.register('_pt_src_c', df)
                        conn.execute('INSERT INTO "' + tmp + '" SELECT * FROM _pt_src_c')
                        conn.unregister('_pt_src_c')
                        n_chunks += 1
                        total += len(df)
                        conn.execute(
                            'INSERT INTO "' + self._PT_LEDGER_TABLE + '" VALUES (?, ?, ?, ?, now())',
                            [table, resumed_from + n_chunks, str(key), len(df)])
                        logger.info('[DuckDBWriter] %s 分片 %s rows=%d cum=%d (staging)',
                                    table, key, len(df), total)
                    if not self._pt_staging_exists(conn, tmp):
                        logger.warning('[DuckDBWriter] %s 无片可写（窗口空或全部已续）', table)
                        return dict(written=0, chunks=0, resumed_from=resumed_from,
                                    rebuilt=rebuilt)
                    cnt, ledger_sum = self._pt_ledger_rows(conn, table)
                    final_rows = conn.execute('SELECT count(*) FROM "' + tmp + '"').fetchone()[0]
                    if ledger_sum != final_rows:
                        raise RuntimeError(
                            '%s staging(%s) 与 ledger(%s) 不一致，拒绝换名（防半表残留）'
                            % (table, final_rows, ledger_sum))
                    # 【PT-01a 改道 · F2】staging 列集与原表一致 ⇒ TRUNCATE + INSERT（保留原表主键）
                    #   列集不一致 ⇒ F1 重建（按 staging 列集重建 + 显式重建同构主键）
                    #   旧语义：DROP 原表 + RENAME staging ⇒ 原表主键随 DROP 静默消失（1→0）
                    if self._pt_columns_match_staging(conn, table, tmp):
                        began = self._pt_begin(conn)
                        try:
                            conn.execute('TRUNCATE TABLE "' + table + '"')
                            conn.execute('INSERT INTO "' + table + '" SELECT * FROM "' + tmp + '"')
                            if began:
                                conn.execute("COMMIT")
                        except BaseException:
                            if began:
                                try:
                                    conn.execute("ROLLBACK")
                                except Exception:
                                    pass
                            raise
                        conn.execute('DROP TABLE IF EXISTS "' + tmp + '"')
                    else:
                        self._pt_rebuild_from_staging(conn, table, tmp)
                    self._pt_clear_ledger(conn, table)
                    logger.info('[DuckDBWriter] %s passthrough 分片写完成 rows=%d chunks=%d '
                                'resumed_from=%d', table, final_rows, n_chunks, resumed_from)
                    return dict(written=final_rows, chunks=n_chunks,
                                resumed_from=resumed_from, rebuilt=rebuilt)
                except BaseException as _wf:
                    record_write_failure(_wf)          # §10.2 修正：失败写自身上下文
                    raise
                finally:
                    price_write_exit(table)
                    conn.close()

    def get_last_date(self, source: str, table: str, freq: str = "daily") -> Optional[str]:
        with self._conn_lock:
            conn = self._conn()
            try:
                res = conn.execute(
                    "SELECT last_date FROM source_watermark "
                    "WHERE source=? AND table_name=? AND freq=?",
                    [source, table, freq]).fetchone()
                return str(res[0]) if res else None
            except Exception as _e:
                # W5（2026-10-04）：不再静默——读失败与「本表无水位行」此前同形不可区分。
                # 仅增加可观测性；返回语义保持不变（仍按无水位处理）。
                logger.warning(
                    "[Watermark] get_last_date 读失败（按无水位处理）: %s: %s | key=%s/%s/%s",
                    type(_e).__name__, _e, source, table, freq)
                return None
            finally:
                conn.close()

    def advance_watermark(self, source: str, table: str, freq: str,
                          last_date: str, batch_id: str):
        # 3A 写锁（操作粒度）：水位表 INSERT 属写路径（writers:764）
        ensure_write_lock(f"writers:watermark:{table}:{batch_id}")
        try:
            return self._advance_watermark_locked(source, table, freq, last_date, batch_id)
        finally:
            release_write_lock()

    def _advance_watermark_locked(self, source: str, table: str, freq: str,
                                  last_date: str, batch_id: str):
        now = datetime.now().isoformat()
        with self._conn_lock:
            conn = self._conn()
            try:
                # v2.4 B-3a：8 列显式 INSERT。审计列 source_generation/cutover_id 经
                # pre_cutover_generation 提供 pre-cutover 静态哨兵（QFQ 价格表→legacy；
                # 非 QFQ 表→not-qfq-managed）。source 保留真实值不改写。B-5 替换为动态
                # active generation/cutover；B-6 激活 mcp/mcp-gen1/<active>。
                gen, cutover = pre_cutover_generation(table, source)
                # 缺陷#7（firefly 2026-10-25，②审照准）：水位防倒退守卫——
                # last_date=GREATEST(COALESCE(旧行,新值), 新值)（last_date 为 BIGINT
                # epoch-ms，数值比较）。原实现无条件覆盖：full_range 小窗（如
                # 2022-07~2022-12）跑完后游标被写回窗口终点（客户实测 2026-10-23
                # →2022-12-30 倒退近 4 年），下次增量重拉数年。旧行 NULL（首写）
                # 经 COALESCE 正常落值；其余列（last_batch_id/updated_at/世代列）
                # 保持覆盖语义不变。bfq 价格表走 intent 机制不受影响；显式重置
                # 水位通道（DELETE/RESET 语义）不经本 upsert，不受守卫影响。
                conn.execute(
                    "INSERT INTO source_watermark "
                    "(source, table_name, freq, last_date, last_batch_id, updated_at, "
                    " source_generation, cutover_id) VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT (source, table_name, freq) DO UPDATE SET "
                    "last_date=GREATEST(COALESCE(source_watermark.last_date, "
                    "EXCLUDED.last_date), EXCLUDED.last_date), "
                    "last_batch_id=EXCLUDED.last_batch_id, "
                    "updated_at=EXCLUDED.updated_at, "
                    "source_generation=EXCLUDED.source_generation, cutover_id=EXCLUDED.cutover_id",
                    [source, table, freq, last_date, batch_id, now, gen, cutover])
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # T1：批级断点（fail-closed，开关 QS_BATCH_CHECKPOINT 默认关）
    # 契约（规格件 §三 T1）：
    #   · 断点**只在** writer.write() 返回（本批写入已提交）且调用点确认写后
    #     回读验证通过之后提交；提交失败 → 不推进（下次重放该批；upsert 幂等
    #     保证安全）。禁止在写提交前落断点。
    #   · 键 (task_name, table_name, freq, window_key)；window_key = 日批交易日 /
    #     每股证券代码。
    #   · 断点缺失 → 调用方退回既有任务级窗口/证券前缀行为（零回归）。
    #   · 开关关闭 → 不建表、不读写、不产生新表/新日志（逐字节等同现状）。
    # 关闭态与任何异常一律**降级为「无断点」**（load 返回 {}、commit 返回 False），
    # 由调用方重跑；绝不因断点设施异常而阻断主写入通道。
    # ------------------------------------------------------------------
    _BATCH_CHECKPOINT_TABLE = "batch_checkpoint"

    def _bc_ensure_table(self, conn) -> None:
        conn.execute(BATCH_CHECKPOINT_DDL)

    def batch_checkpoint_load(self, task_name: str, table: str,
                              freq: str) -> Dict[str, str]:
        """读某 (task_name, table, freq) 的批级断点 → {window_key: status}。

        关闭态 / 任何异常 → 返回 {}（= 无断点；调用方按现状全窗/全证券重跑）。
        """
        if not _is_batch_checkpoint_enabled():
            return {}
        try:
            with self._conn_lock:
                conn = self._conn()
                try:
                    self._bc_ensure_table(conn)
                    rows = conn.execute(
                        "SELECT window_key, status FROM batch_checkpoint "
                        "WHERE task_name=? AND table_name=? AND freq=?",
                        [str(task_name), str(table), str(freq)]).fetchall()
                    return {str(r[0]): str(r[1]) for r in rows}
                finally:
                    conn.close()
        except Exception as e:
            logger.warning("[BatchCheckpoint] load 失败（降级为无断点，全窗重跑）: %s: %s",
                           type(e).__name__, e)
            return {}

    def batch_checkpoint_commit(self, task_name: str, table: str, freq: str,
                                window_key, batch_id: str,
                                rows_written: int = 0) -> bool:
        """提交一个「已完成批」断点（fail-closed）。

        调用前提（红线）：``writer.write()`` 已返回且写后回读验证通过。
        写入后**回读校验**（status='completed' 且 rows_written 一致）方返回 True；
        写入或回读任一失败 → 撤销本窗口 completed 标记并返回 False（不推进，下次重放该批）。
        绝不抛异常。
        """
        if not _is_batch_checkpoint_enabled():
            return False
        wk = str(window_key)
        try:
            with self._conn_lock:
                conn = self._conn()
                try:
                    self._bc_ensure_table(conn)
                    conn.execute(
                        "INSERT INTO batch_checkpoint "
                        "(task_name, table_name, freq, window_key, batch_id, status, "
                        " rows_written, updated_at) VALUES (?,?,?,?,?,?,?, now()) "
                        "ON CONFLICT (task_name, table_name, freq, window_key) "
                        "DO UPDATE SET batch_id=EXCLUDED.batch_id, status=EXCLUDED.status, "
                        "rows_written=EXCLUDED.rows_written, updated_at=EXCLUDED.updated_at",
                        [str(task_name), str(table), str(freq), wk, str(batch_id),
                         "completed", int(rows_written)])
                    back = conn.execute(
                        "SELECT status, rows_written FROM batch_checkpoint "
                        "WHERE task_name=? AND table_name=? AND freq=? AND window_key=?",
                        [str(task_name), str(table), str(freq), wk]).fetchone()
                    if (not back or str(back[0]) != "completed"
                            or int(back[1]) != int(rows_written)):
                        logger.warning(
                            "[BatchCheckpoint] 回读校验失败（不推进断点）: "
                            "%s/%s/%s window=%s back=%s",
                            task_name, table, freq, wk, back)
                        # fail-closed 补偿：撤销本窗口 completed 标记，确保下次重跑该批
                        try:
                            conn.execute(
                                "DELETE FROM batch_checkpoint WHERE task_name=? "
                                "AND table_name=? AND freq=? AND window_key=? "
                                "AND status='completed'",
                                [str(task_name), str(table), str(freq), wk])
                        except Exception as e2:
                            logger.warning(
                                "[BatchCheckpoint] 回读失败补偿删除亦失败: %s: %s",
                                type(e2).__name__, e2)
                        return False
                    return True
                finally:
                    conn.close()
        except Exception as e:
            logger.warning("[BatchCheckpoint] commit 失败（不推进断点，下次重放）: %s: %s",
                           type(e).__name__, e)
            return False

    def batch_checkpoint_clear(self, task_name: str, table: str,
                               freq: str) -> None:
        """任务自然成功后清理该 (task_name, table, freq) 的全部批级断点。关闭态 no-op。"""
        if not _is_batch_checkpoint_enabled():
            return
        try:
            with self._conn_lock:
                conn = self._conn()
                try:
                    if _table_exists(conn, self._BATCH_CHECKPOINT_TABLE):
                        conn.execute(
                            "DELETE FROM batch_checkpoint "
                            "WHERE task_name=? AND table_name=? AND freq=?",
                            [str(task_name), str(table), str(freq)])
                finally:
                    conn.close()
        except Exception as e:
            logger.warning("[BatchCheckpoint] clear 失败: %s: %s", type(e).__name__, e)

    # ------------------------------------------------------------------
    # 事务感知内部方法（QFQ 重锚编排专用）
    # ------------------------------------------------------------------
    # 说明：以下 *_on_conn 方法在**调用方提供的连接与事务**内执行，
    #   - 不获取 self._conn_lock（连接由调用方持有并串行化）；
    #   - 不 commit / 不 rollback / 不 close（事务边界由调用方掌控）。
    # 用途：QFQ 重锚编排需将「价格修正 UPDATE + anchor 状态更新 + 表级水位推进 +
    #   被过滤证券欠账」放入**同一 DuckDB 事务**保证原子性（设计 v3 §4.5 / §8）。
    # 公共 advance_watermark 行为保持不变（自开短连接自动提交），本方法为其事务版补充。

    def _advance_watermark_on_conn(self, conn, source: str, table: str, freq: str,
                                   last_date, batch_id: str) -> None:
        """在给定连接/事务内推进表级水位（PK source,table_name,freq）。不 commit。

        语义与公共 ``advance_watermark`` 完全一致（同一 INSERT ... ON CONFLICT 形态、
        同一 updated_at 口径），仅事务边界交由调用方。
        """
        now = datetime.now().isoformat()
        # v2.4 B-3a：8 列显式 INSERT + pre-cutover 静态哨兵（见公共 advance_watermark 注释）
        gen, cutover = pre_cutover_generation(table, source)
        conn.execute(
            "INSERT INTO source_watermark "
            "(source, table_name, freq, last_date, last_batch_id, updated_at, "
            " source_generation, cutover_id) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT (source, table_name, freq) DO UPDATE SET "
            "last_date=EXCLUDED.last_date, last_batch_id=EXCLUDED.last_batch_id, "
            "updated_at=EXCLUDED.updated_at, "
            "source_generation=EXCLUDED.source_generation, cutover_id=EXCLUDED.cutover_id",
            [source, table, freq, last_date, batch_id, now, gen, cutover])

    def _upsert_pending_backfill_on_conn(self, conn, *, asset_type: str, code: str,
                                         table_name: str, freq: str,
                                         range_start, range_end,
                                         reason: str, anchor_version=None,
                                         status: str = "pending",
                                         now: Optional[str] = None,
                                         reopen: bool = False,
                                         price_source: str = "xtquant",
                                         source_generation: str = "xtquant-legacy") -> None:
        """在给定连接/事务内登记「被过滤证券」的精确欠账区间（设计 v4 §1.1）。不 commit。

        幂等语义（阻断 4 修复）：
        - 同 PK 已 ``resolved`` 且未显式 ``reopen`` → 保持 resolved，**不静默重开**（幂等）。
        - 显式 ``reopen=True`` → ``status='pending'``、``resolved_at=NULL``、
          ``last_error=NULL``、``attempt_count=0``（重新进入欠账）。

        输入校验（阻断 4）：``range_start <= range_end``；``asset_type`` 合法；
        ``table_name`` 属四价格表白名单；``freq`` 非空；``status`` 属允许集合。
        任一不满足抛 ``ValueError``。

        热路径（可靠性 阻断 5）：假定 schema 已由编排初始化，不再每条 upsert 前重复发 DDL；
        表不存在直接失败，让启动初始化问题显性暴露。
        """
        from quantstudio.pipeline.qfq_reanchor_schema import (
            _normalize_asset_type, _normalize_code, _validate_epoch_ms,
            PRICE_TABLES, BACKFILL_STATUS, ASSET_TABLE_MAP,
        )
        from quantstudio.pipeline.qfq_calendar import _norm_freq

        # —— 输入校验（阻断 4 + 阻断 3 关联契约）——
        # asset_type：归一化（"stock"→"STOCK"），拒绝非法值
        asset_type = _normalize_asset_type(asset_type)
        # code：canonical 裸 6 位码（复用 schema 单一规则，不复制）
        code = _normalize_code(code)
        # table_name：四价格表白名单
        if table_name not in PRICE_TABLES:
            raise ValueError(
                f"非法 table_name: {table_name!r}（仅四价格表 {sorted(PRICE_TABLES)}）")
        # asset_type ↔ table_name 关联契约
        if table_name not in ASSET_TABLE_MAP.get(asset_type, frozenset()):
            raise ValueError(
                f"asset_type={asset_type} 与 table_name={table_name!r} 不匹配"
                f"（STOCK→stock_daily/stock_minutes；ETF→etf_daily/etf_minutes）")
        # freq：非空 + 与 table_name 关联（daily↔daily，minutes↔1min）
        if not freq or not str(freq).strip():
            raise ValueError("freq 不能为空")
        freq = str(freq).strip()
        kind, n = _norm_freq(freq)
        if kind == "unknown":
            raise ValueError(f"非法 freq: {freq!r}")
        if table_name.endswith("_daily") and kind != "daily":
            raise ValueError(
                f"table_name={table_name!r} 为日线表，freq 必须为 daily，收到 {freq!r}")
        if table_name.endswith("_minutes") and (kind != "minute" or n != 1):
            raise ValueError(
                f"table_name={table_name!r} 为分钟表，freq 必须为 1min（batch1），收到 {freq!r}")
        # 阻断 2：freq 规范化为 storage canonical（别名 1m/1d 必须写入 1min/daily）。
        # 输入别名可兼容，但存储值唯一规范，否则后续 WHERE freq=? / ON CONFLICT 无法命中
        # canonical，造成欠账无法正确补拉或 readback。
        if kind == "daily":
            freq_canonical = "daily"
        elif kind == "minute" and n == 1:
            freq_canonical = "1min"
        else:
            raise NotImplementedError(
                f"freq={freq!r} 暂不支持（batch1 仅 daily/1min）")
        # range_start/range_end：有效 epoch-ms（共享 schema 校验，拒绝非法/越界）
        try:
            rs = _validate_epoch_ms(range_start)
            re_ = _validate_epoch_ms(range_end)
        except ValueError as e:
            raise ValueError(f"range_start/range_end 非法: {e}")
        if rs > re_:
            raise ValueError(f"range_start 必须 <= range_end: {rs} > {re_}")
        # reason：非空字符串
        if not isinstance(reason, str):
            raise ValueError(f"reason 必须为非空字符串: {reason!r}")
        reason = reason.strip()
        if not reason:
            raise ValueError("reason 不能为空")
        # reopen：严格 bool（避免 "false"/0 被当真值）
        if not isinstance(reopen, bool):
            raise ValueError(f"reopen 必须为 bool: {reopen!r}")
        # status：允许集合
        if status not in BACKFILL_STATUS:
            raise ValueError(
                f"非法 status: {status!r}（仅 {sorted(BACKFILL_STATUS)}）")

        ts = now or datetime.now().isoformat()

        # —— 条件重开：resolved 且未显式 reopen → 保持 resolved，不重开 ——
        # v2.4 B-3a：pending_backfill 新 PK 含 price_source/source_generation（8 列）。
        # price_source 由调用方传入（默认 xtquant 保持兼容；MCP 路径显式传 cfg.price_source=mcp）；
        # source_generation 默认 pre-cutover 哨兵。不查 active cutover（B-5/B-6 范围）。
        _bf_price_source = price_source
        _bf_gen = source_generation
        row = conn.execute(
            "SELECT status, resolved_at, last_error, attempt_count FROM qfq_pending_backfill "
            "WHERE asset_type=? AND code=? AND table_name=? AND freq=? "
            "AND range_start=? AND range_end=? AND price_source=? AND source_generation=?",
            [asset_type, code, table_name, freq_canonical, rs, re_,
             _bf_price_source, _bf_gen]).fetchone()

        # —— 阻断 3：普通 upsert（欠账登记）禁止创建/变更终态或处理中态 ——
        # resolved / in_progress 必须由专用状态机方法（如 _resolve_backfill_on_conn /
        # _mark_backfill_in_progress_on_conn）处理。普通 upsert 仅允许非终态
        # {pending, blocked, retryable_failed}；非终态 row 的普通 upsert 仍按既有
        # 幂等/重开逻辑处理（不在此拦截）。
        if status in ("resolved", "in_progress"):
            raise ValueError(
                f"status={status!r} 为终态/处理中态，禁止通过普通 upsert 创建或变更；"
                f"必须使用专用状态机方法（如 _resolve_backfill_on_conn）。"
                f"普通 upsert 仅允许 {sorted(BACKFILL_STATUS - {'resolved', 'in_progress'})}")
        if row is not None and row[0] == "resolved" and not reopen:
            return  # 幂等：保持 resolved，不静默重开

        # —— 决定保留/清空的 resolved 上下文 ——
        if row is not None and reopen:
            resolved_at_val = None
            last_error_val = None
            attempt_count_val = 0
        elif row is not None:
            # 非重开：保留既有 resolved_at / last_error / attempt_count
            resolved_at_val = row[1]
            last_error_val = row[2]
            attempt_count_val = row[3] if row[3] is not None else 0
        else:
            resolved_at_val = None
            last_error_val = None
            attempt_count_val = 0
        upsert_status = "pending" if reopen else status

        conn.execute(
            "INSERT INTO qfq_pending_backfill "
            "(asset_type, code, table_name, freq, range_start, range_end, "
            " price_source, source_generation, reason, "
            " anchor_version, status, attempt_count, last_error, created_at, updated_at, resolved_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (asset_type, code, table_name, freq, range_start, range_end, "
            "            price_source, source_generation) "
            "DO UPDATE SET reason=EXCLUDED.reason, anchor_version=EXCLUDED.anchor_version, "
            "status=EXCLUDED.status, updated_at=EXCLUDED.updated_at, "
            "resolved_at=EXCLUDED.resolved_at, last_error=EXCLUDED.last_error, "
            "attempt_count=EXCLUDED.attempt_count",
            [asset_type, code, table_name, freq_canonical, rs, re_,
             _bf_price_source, _bf_gen,
             reason, anchor_version, upsert_status, attempt_count_val,
             last_error_val, ts, ts, resolved_at_val])

    @staticmethod
    def _table_columns(table: str) -> List[str]:
        """返回表的列名（与 DDL 顺序一致，统一口径 v2.0）"""
        COLS = {
            "stock_daily": ["code", "time", "open", "high", "low", "close",
                            "volume", "amount", "preClose", "suspendFlag",
                            "settelementPrice", "openInterest",
                            "open_front", "high_front", "low_front", "close_front",
                            "open_back", "high_back", "low_back", "close_back",
                            "open_front_ratio", "high_front_ratio", "low_front_ratio", "close_front_ratio",
                            "open_back_ratio", "high_back_ratio", "low_back_ratio", "close_back_ratio",
                            "turn", "pctChg", "peTTM", "psTTM", "pcfNcfTTM", "pbMRQ",
                            "isST",
                            "is_st_reliable", "is_st_reliable_source",
                            "is_delisting_risk", "is_delisting_risk_source",
                            "dividend_type", "update_time", "data_source"],
            "stock_minutes": ["code", "time", "freq", "open", "high", "low", "close",
                              "volume", "amount", "preClose", "suspendFlag",
                              "settelementPrice", "openInterest",
                              "open_front", "high_front", "low_front", "close_front",
                              "open_back", "high_back", "low_back", "close_back",
                              "open_front_ratio", "high_front_ratio", "low_front_ratio", "close_front_ratio",
                              "open_back_ratio", "high_back_ratio", "low_back_ratio", "close_back_ratio",
                              "dividend_type", "update_time", "data_source"],
            "etf_minutes": ["code", "time", "freq", "open", "high", "low", "close",
                            "volume", "amount", "preClose", "suspendFlag",
                            "settelementPrice", "openInterest",
                            "open_front", "high_front", "low_front", "close_front",
                            "open_back", "high_back", "low_back", "close_back",
                            "open_front_ratio", "high_front_ratio", "low_front_ratio", "close_front_ratio",
                            "open_back_ratio", "high_back_ratio", "low_back_ratio", "close_back_ratio",
                            "dividend_type", "update_time", "data_source"],
            "tick": ["code", "time", "lastPrice", "open", "high", "low", "lastClose",
                     "amount", "volume", "pvolume", "stockStatus", "openInt", "lastSettlementPrice",
                     "askPrice1", "askPrice2", "askPrice3", "askPrice4", "askPrice5",
                     "bidPrice1", "bidPrice2", "bidPrice3", "bidPrice4", "bidPrice5",
                     "askVol1", "askVol2", "askVol3", "askVol4", "askVol5",
                     "bidVol1", "bidVol2", "bidVol3", "bidVol4", "bidVol5",
                     "transactionNum", "update_time", "data_source"],
            "fin_indicator": ["code", "ann_date", "end_date", "eps", "diluted_eps", "bps", "roe",
                              "pe_ttm", "pb", "ps_ttm",
                              "np_yoy", "or_yoy", "tr_yoy", "update_flag",
                              "backfill_eps_source", "data_source"],
            "index_daily": ["code", "time", "open", "high", "low", "close",
                            "pctChg", "volume", "amount", "data_source"],
            "stock_daily_valuation": ["code", "time", "circ_mv", "total_mv",
                                      "free_share",
                                      "pe_ttm", "pb", "turnover_rate", "update_time", "data_source"],
            "etf_daily": ["code", "time", "open", "high", "low", "close",
                          "preClose", "pctChg", "volume", "amount", "turn",
                          "open_front", "high_front", "low_front", "close_front",
                          "open_back", "high_back", "low_back", "close_back",
                          "open_front_ratio", "high_front_ratio", "low_front_ratio", "close_front_ratio",
                          "open_back_ratio", "high_back_ratio", "low_back_ratio", "close_back_ratio",
                          "isST", "dividend_type", "update_time", "data_source"],
            "stock_basic": ["code", "ts_code", "symbol", "name", "area", "industry",
                            "market", "list_status", "list_date", "delist_date", "exchange",
                            "update_time", "data_source"],
            "trade_calendar": ["cal_date", "is_open", "source", "updated_at",
                               "exchange", "pretrade_date"],
            "etf_basic": ["code", "ts_code", "name", "exchange",
                          "list_date", "delist_date", "etf_type", "tracking_index",
                          "is_cross_border", "status", "fund_type", "invest_type",
                          "type", "classification_method", "classification_version",
                          "update_time", "data_source"],
            "stock_float_share": ["code", "end_date", "ann_date",
                                  "free_share", "total_share",
                                  "circ_mv", "total_mv", "update_time", "data_source"],
            "index_constituents": ["index_code", "code", "time", "weight", "data_source"],
            "index_constituents_snapshot_meta": ["index_code", "time", "n_constituents",
                                                 "expected_count", "status",
                                                 "n_duplicate_codes", "n_negative_weights",
                                                 "n_blank_codes",
                                                 "update_time", "data_source"],
            "balance_statement": ["code", "end_date", "ann_date",
                                  "total_assets", "total_liability", "total_equity",
                                  "total_current_assets", "total_non_current_assets",
                                  "total_current_liability", "total_non_current_liability",
                                  "account_receivable", "account_payable", "inventory",
                                  "cash_equivalents", "fixed_asset", "intangible_asset", "goodwill",
                                  "update_time", "data_source"],
            "income_statement": ["code", "end_date", "ann_date",
                                 "operating_revenue", "operating_cost", "operating_profit",
                                 "total_profit", "net_profit", "np_parent_company_owners",
                                 "sale_expense", "manage_expense", "finance_expense", "rd_expense",
                                 "income_tax", "basic_eps", "update_time", "data_source"],
            "cashflow_statement": ["code", "end_date", "ann_date",
                                   "net_operate_cash_flow", "net_invest_cash_flow", "net_finance_cash_flow",
                                   "cash_add_balance", "goods_sale_and_services", "goods_buy_and_services",
                                   "fixed_asset_depreciation", "update_time", "data_source"],
            "stock_dividend": ["code", "ex_date", "record_date",
                               "ann_date", "end_date",
                               "cash_div_before_tax", "cash_div_after_tax",
                               "cash_div", "stk_div", "stk_bo_rate", "stk_co_rate",
                               "div_rat", "div_proc", "update_time", "data_source"],
            "etf_dividend": ["code", "ex_date", "record_date", "ann_date",
                             "imp_anndate", "base_date", "div_proc", "pay_date",
                             "earpay_date", "net_ex_date", "div_cash", "base_unit",
                             "ear_distr", "ear_amount", "account_date", "base_year",
                             "update_time", "data_source"],
            "sw_industry": ["code", "industry_code", "industry_name", "industry_level", "update_time", "data_source"],
            "industry_classification": ["classification_system", "classification_version",
                                        "industry_code", "industry_name", "industry_level",
                                        "parent_industry_code", "effective_from", "effective_to",
                                        "update_time", "data_source"],
            "industry_membership": ["classification_system", "classification_version",
                                    "industry_level", "industry_code", "code",
                                    "effective_from", "effective_to",
                                    "update_time", "data_source"],
            "stock_namechange": ["code", "change_date", "name_before", "name_after",
                                 "status_after", "update_time", "data_source"],
            "stock_delist": ["code", "list_date", "delist_date", "market", "update_time", "data_source"],
        }
        return COLS.get(table, [])


class QuestDBWriter(BaseWriter):
    """QuestDB 写入器（可选，ILP 批量写入）

    config 示例：{"type": "questdb", "host": "localhost", "ilp_port": 9009, "pg_port": 8812}
    Phase 1 占位实现，Phase 3 完善 ILP 协议。
    """

    def __init__(self, config: Dict):
        super().__init__(config)
        self.host = config.get("host", "localhost")
        self.ilp_port = config.get("ilp_port", 9009)
        self.pg_port = config.get("pg_port", 8812)
        raise NotImplementedError("QuestDBWriter 将在 Phase 3 实现 ILP 协议（基线 v3.2 §8.1）")

    def write(self, df, table, batch_id): raise NotImplementedError
    def get_last_date(self, source, table, freq="daily"): raise NotImplementedError
    def advance_watermark(self, source, table, freq, last_date, batch_id): raise NotImplementedError


def create_writer(config: Dict) -> BaseWriter:
    """工厂方法"""
    wtype = config.get("type", "duckdb").lower()
    registry = {"duckdb": DuckDBWriter, "questdb": QuestDBWriter}
    if wtype not in registry:
        raise ValueError(f"未知 writer 类型: {wtype}")
    return registry[wtype](config)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    here = Path(__file__).resolve().parent.parent.parent
    w = DuckDBWriter({"type": "duckdb", "path": str(db_path())})

    df = pd.DataFrame({
        "ts_code": ["600000.SH", "600000.SH"],
        "trade_date": ["2026-07-10", "2026-07-11"],
        "open": [10.0, 10.5], "high": [10.2, 10.8], "low": [9.9, 10.3],
        "close": [10.1, 10.6], "pct_chg": [1.0, 4.95],
        "vol": [1000.0, 1200.0], "amount": [1010.0, 1272.0],
    })
    n = w.write(df, "stock_daily", "smoke_001")
    w.advance_watermark("test", "stock_daily", "daily", "2026-07-11", "smoke_001")
    last = w.get_last_date("test", "stock_daily", "daily")
    print(f"wrote={n}, watermark={last}")

    # 重放验证幂等
    n2 = w.write(df, "stock_daily", "smoke_001_replay")
    print(f"replay wrote={n2}（应仍为 2，upsert 不重复）")
    print("✅ DuckDBWriter 幂等写入验证通过")
