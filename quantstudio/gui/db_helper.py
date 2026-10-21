"""GUI 专用只读数据库查询封装。
DuckDB 连接用 read_only=True（避免与采集进程写入冲突）；SQLite 短连接。

v3 评审 4：daemon 采集期持有 DuckDB RW 连接（collector_run.lock 内），
此时 GUI 的 read_only 查询可能触发 "另一进程正在使用此文件" IOException。
所有 DuckDB 查询经 _safe_query 包装，捕获 IOException 后返回空 DataFrame
+ 警告日志，GUI 不崩溃（优雅降级）。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# A-prime (T3): 跨进程写锁冲突判据 —— 切换为共享中立实现（dev T1，提交 c8f062d）。
# 串表唯一来源 quantstudio/pipeline/db_lock_errors.py，pipeline 侧(writers 重试层)与本侧共用，
# 避免"各写一份 → 重试层只认 Windows 串 → macOS 上永不重试"的串表分叉。
from quantstudio.pipeline.db_lock_errors import is_db_lock_conflict  # noqa: E402

# A-prime (T3): GUI 只读打开快速重试 —— 针对源①(GUI 高频只读查询 vs daemon RW 打开)的跨进程锁冲突。
# 保守取值(1-2 次 / ~150-300ms): 只覆盖"采集收尾瞬间仍需短等待"的窗口,
# 不掩盖真正的配置性故障(重试耗尽仍按原契约优雅降级并记录忙态)。
READ_ONLY_RETRY_ATTEMPTS = 2
READ_ONLY_RETRY_INTERVAL_S = 0.2

# A4: UI 降级提示的默认观察窗口(秒)
BUSY_HINT_WINDOW_S = 30.0

# 笔2（2026-09-23 GUI 启动卡死案）：查询 deadline 与自动重试节奏。
# 背景：主库带大 WAL 时 duckdb.connect() 需先回放 WAL（生产实测 22.3 分钟），
# 属「慢的成功」——既不抛异常也不返回，既有忙态重试（2×0.2s）永不触发 →
# 构造期同步调用会永久阻塞。故新增 deadline：主调方最多等 QUERY_DEADLINE_S。
QUERY_DEADLINE_S = 5.0          # 单次查询主调方等待上限（可配）
AUTO_RETRY_INTERVAL_S = 30.0    # UI 侧降级后自动重试间隔（方案 §3-②）
STUCK_WARN_S = 120.0            # 后台尝试阻塞多久后升级为显式告警（只报一次）

# ---- firefly「水位线」案（2026-10-18，N4/FP-1·FP-2）：展示层只读辅助 ----------
# 语义区分（本案根因之一）：GUI 原仅一列「水位线」= source_watermark.last_date，
# 那是**下次增量起点（游标）**；库里真实数据边界是 max(time)。游标落后于数据边界
# 只表示"下轮会从旧起点幂等重拉"，**不代表数据缺失**。故新增两列：
#   ① 数据止点（本模块 get_data_bounds，只读 max(time)，60s TTL 缓存）
#   ② 水位状态（get_watermark_gate_status，只读聚合，不写库）
# 本区**只读**：任何函数都不得写 source_watermark / qfq_* 表。
DATA_BOUNDS_TTL_S = 60.0        # 数据止点缓存 TTL（大表 max(time) 实测 ≤0.27s，仍加缓存防抖动）
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")   # 表名白名单（防 SQL 拼接注入）
# QFQ 托管价格表：与 quantstudio/pipeline/qfq_orchestrator_types.py:427
# （COORDINATED_PRICE_TABLES = frozenset(PRICE_TABLES)）同源。此处本地声明，
# 避免 GUI 拉起 pipeline 依赖链；tests/test_gui_watermark_display.py 有一致性守卫。
QFQ_COORDINATED_TABLES = frozenset({"stock_daily", "stock_minutes", "etf_daily", "etf_minutes"})
WATERMARK_STATE_LABELS = {"normal": "正常", "deferred": "延迟提交", "held": "门控保持"}
# 周期活跃态（非终态）——与 qfq_resident_orchestrator.phase_of 的终态集互补
_CYCLE_ACTIVE_STATES = frozenset({"started", "recovering", "observing", "fetching",
                                  "applying", "gating"})


def _daemon_check_interval_seconds() -> Optional[int]:
    """读取 daemon 调度检查间隔(秒) —— A4 提示文案"预计等待"的唯一来源。

    来源: config/profiles/mcp_only/collector_tasks.json 的
    daemon_schedule.check_interval_sec (本机实测 300)。
    取不到配置时返回 None; 调用方不得编造数字(只显示不含秒数的提示)。
    """
    try:
        root = Path(__file__).resolve().parents[2]  # quantstudio/gui/db_helper.py -> 项目根
        cfg_path = root / "config" / "profiles" / "mcp_only" / "collector_tasks.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        val = int((data.get("daemon_schedule") or {}).get("check_interval_sec"))
        return val if val > 0 else None
    except Exception:
        return None


def _is_db_busy_error(exc: Exception) -> bool:
    """识别 DuckDB 跨进程写锁冲突（busy）—— 共享中立实现的向后兼容薄包装。

    实现已切换至 quantstudio/pipeline/db_lock_errors.is_db_lock_conflict
    （dev T1，提交 c8f062d；串表含 Windows 中/英 + POSIX 三串）。保留本函数名，
    既有调用点与用例零改动（re-export 形态）。

    **口径变化（如实记录）**：相较切换前的本地串表，判定**收窄且更精确**——
    移除了过宽的 `io error` 兜底与 `another process` / `used by another process` /
    中文短串，以免把"路径不存在/权限不足/库损坏"等**响亮失败**误判为 busy 而送入退避
    （共享模块 docstring 明确该语义）。真实冲突形态的判定覆盖率由 A6 验收轮实测
    （矩阵 record_3 归因命中 / record_4 降级提示）验证。
    """
    return is_db_lock_conflict(exc)


class DbHelper:
    """GUI 专用的只读数据库查询封装。"""

    def __init__(self, duckdb_path, quarantine_path, batch_audit_path):
        self.duckdb_path = Path(duckdb_path)
        self.quarantine_path = Path(quarantine_path)
        self.batch_audit_path = Path(batch_audit_path)
        # A4: 跨进程锁冲突的可观测状态(仅新增只读属性对外暴露; 不改任何既有返回契约)
        self._last_busy_at: Optional[float] = None
        self._busy_count: int = 0
        # 笔2：单槽后台尝试（daemon 线程，进程退出不阻塞）+ 降级/恢复态
        self._attempt_thread: Optional[threading.Thread] = None
        self._attempt_slot: Optional[dict] = None
        self._attempt_started_at: Optional[float] = None
        self._stuck_warned: bool = False
        self._recovering: bool = False
        # FP-1/FP-2（2026-10-18）：数据止点 TTL 缓存（纯读；每表一条 max(time)）
        self._bounds_cache: dict = {}
        self._bounds_at: dict = {}
        # 具备 time 列的表集合（元数据查询，300s 缓存）——无 time 列的表不适用数据止点
        self._time_tables: Optional[set] = None
        self._time_tables_at: float = 0.0

    def _mark_busy(self, exc: Exception) -> None:
        """A4：记录最近一次跨进程锁冲突（供 UI 提示）。不改变任何返回值。"""
        self._last_busy_at = time.time()
        self._busy_count += 1

    def _query_once(self, sql: str) -> pd.DataFrame:
        """单次同步查询（含 A-prime 既有快速重试）—— 由单槽工作线程执行，见 _safe_query。

        保持既有语义：锁冲突（busy）重试 READ_ONLY_RETRY_ATTEMPTS 次后返回空 DataFrame；
        非锁冲突异常（SQL 语法错等）**向上抛**。
        """
        import duckdb
        last_exc: Optional[Exception] = None
        for attempt in range(READ_ONLY_RETRY_ATTEMPTS + 1):
            try:
                with duckdb.connect(str(self.duckdb_path), read_only=True) as conn:
                    return conn.execute(sql).fetchdf()
            except duckdb.IOException as e:
                if not _is_db_busy_error(e):
                    raise
                last_exc = e
            except Exception as e:
                # 兼容某些 duckdb 版本将 IO 错误归为普通 Exception 的情况
                if not _is_db_busy_error(e):
                    raise
                last_exc = e
            if attempt < READ_ONLY_RETRY_ATTEMPTS:
                time.sleep(READ_ONLY_RETRY_INTERVAL_S)
        self._mark_busy(last_exc)
        logger.warning(
            f"[DbHelper] DuckDB 忙（daemon 采集中？），{READ_ONLY_RETRY_ATTEMPTS + 1} 次尝试后返回空结果: {last_exc}")
        return pd.DataFrame()

    def _start_attempt(self, sql: str) -> None:
        """起一次后台尝试（**daemon 线程**：进程退出不被阻塞，见 _safe_query 单槽策略）。"""
        slot: dict = {}

        def _run() -> None:
            try:
                slot["result"] = self._query_once(sql)
            except BaseException as e:      # noqa: BLE001 — 需原样带回主调方按契约处理
                slot["error"] = e
            finally:
                slot["done"] = True

        t = threading.Thread(target=_run, daemon=True, name="dbhelper-query")
        self._attempt_slot = slot
        self._attempt_thread = t
        self._attempt_started_at = time.time()
        self._stuck_warned = False
        t.start()

    def _harvest(self) -> pd.DataFrame:
        """收割已完成的尝试（不阻塞）。真故障上抛；锁冲突/超时返回空表 + 记录忙态。"""
        slot = self._attempt_slot or {}
        self._attempt_slot = None
        self._attempt_thread = None
        self._attempt_started_at = None
        self._stuck_warned = False
        if "error" in slot:
            err = slot["error"]
            self._recovering = False
            if not _is_db_busy_error(err):
                raise err                     # 真故障：保持既有「上抛」语义
            self._mark_busy(err)
            logger.warning(f"[DbHelper] 后台查询以锁冲突结束: {err}")
            return pd.DataFrame()
        self._recovering = False
        return slot.get("result", pd.DataFrame())

    def _safe_query(self, sql: str) -> pd.DataFrame:
        """v3 评审 4 + A-prime（T3）+ **笔2 超时降级**：DuckDB 查询统一包装。

        笔2 变更（2026-09-23 GUI 启动卡死案）：新增 deadline 保护。主库带大 WAL 时
        `duckdb.connect()` 需先回放 WAL（生产实测 22.3 分钟）——这是**「慢的成功」**：
        既不抛异常也不返回，既有 2×0.2s 忙态重试**永不触发**，调用方（GUI 构造期）永久阻塞。
        现改为：在工作线程内执行，主调方最多等 `QUERY_DEADLINE_S`；超时即返回空 DataFrame
        + 记录降级态（UI 可据 `busy_hint()` / `is_recovering` 提示「正在恢复」并自动重试）。

        **单槽策略（防 09-23 卡死进程重演）**：同一时刻至多 1 个在跑的尝试；若上次尝试仍在跑
        （例如 connect 阻塞在锁/WAL 回放上），本次**直接返回降级空表、不新起线程**——
        避免线程堆积成新的卡死源。该尝试完成后由下一次调用自动收割（即「降级后自动恢复」）。
        线程为 **daemon**：即使永久阻塞也不会拖住进程退出。

        契约不变：成功→DataFrame；忙/超时→空 DataFrame + 警告日志；
        真故障（SQL 语法错等非锁冲突异常）仍向上抛（含超时后在下次收割时上抛）。
        """
        # 1) 上次尝试仍在跑 → 保持降级，不新起线程（非阻塞）
        if self._attempt_thread is not None and not (self._attempt_slot or {}).get("done"):
            pending_s = time.time() - (self._attempt_started_at or time.time())
            if pending_s >= STUCK_WARN_S and not self._stuck_warned:
                self._stuck_warned = True
                logger.warning(
                    f"[DbHelper] 后台查询已阻塞 {pending_s:.0f}s（库被占用或正在回放 WAL）；"
                    f"本进程保持降级态且不新起线程，建议稍后刷新，必要时重启 GUI。")
            self._recovering = True
            self._mark_busy(RuntimeError("query still pending"))
            return pd.DataFrame()

        # 2) 上次尝试已完成 → 先收割结果（含「降级后自动恢复」路径）
        if self._attempt_thread is not None:
            return self._harvest()

        # 3) 空闲 → 新起一次尝试，最多等 QUERY_DEADLINE_S
        self._start_attempt(sql)
        self._attempt_thread.join(QUERY_DEADLINE_S)
        if (self._attempt_slot or {}).get("done"):
            return self._harvest()
        self._recovering = True
        self._mark_busy(RuntimeError(f"query timeout after {QUERY_DEADLINE_S}s"))
        logger.warning(
            f"[DbHelper] 查询超时（>{QUERY_DEADLINE_S}s，疑 WAL 回放或库被占用）→ 降级返回空表；"
            f"该尝试仍在后台继续，完成后由下次调用自动收割（不新起线程）。")
        return pd.DataFrame()

    # ---------------- A4：跨进程锁冲突的可观测与提示 ----------------
    @property
    def last_busy_at(self) -> Optional[float]:
        """最近一次跨进程锁冲突的时间戳（epoch 秒）；从未发生为 None。"""
        return self._last_busy_at

    @property
    def busy_count(self) -> int:
        """本实例累计遭遇跨进程锁冲突的次数。"""
        return self._busy_count

    def is_busy_recent(self, window_s: float = BUSY_HINT_WINDOW_S) -> bool:
        """最近 window_s 秒内是否遭遇过跨进程锁冲突（UI 据此显示降级提示）。"""
        if self._last_busy_at is None:
            return False
        return (time.time() - self._last_busy_at) <= window_s

    def busy_hint(self, window_s: float = BUSY_HINT_WINDOW_S) -> str:
        """锁冲突降级提示文案（空串 = 当前无冲突，调用方按原样显示常规信息）。

        预计等待时长来源：collector_tasks.json 的
        daemon_schedule.check_interval_sec（本机实测 300s）。
        取不到配置时不编造数字，只返回不含秒数的提示。
        """
        if not self.is_busy_recent(window_s):
            return ""
        base = "数据库采集中（守护进程正在写入），请稍后刷新"
        interval = _daemon_check_interval_seconds()
        if interval is None:
            return base + "。"
        return f"{base}（守护进程每 {interval} 秒检查一轮，最长约 {interval} 秒内自动恢复）。"

    # ---------------- 笔2：降级恢复态（UI 据此显示提示与自动重试） ----------------
    @property
    def is_recovering(self) -> bool:
        """是否处于「查询超时降级 / 后台尝试仍在跑」的恢复态（UI 显示恢复提示）。"""
        return bool(self._recovering or (
            self._attempt_thread is not None and not (self._attempt_slot or {}).get("done")))

    def recovering_hint(self) -> str:
        """恢复态提示文案（空串 = 未处于恢复态）。"""
        if not self.is_recovering:
            return ""
        return ("数据库正在恢复（可能正在回放 WAL 或守护进程写入中），已降级显示空数据；"
                f"每 {int(AUTO_RETRY_INTERVAL_S)} 秒自动重试，也可点「刷新」立即重试。")

    # ---------------- DuckDB（主库，只读）----------------
    def query_duckdb(self, sql: str) -> pd.DataFrame:
        """通用只读 SQL 查询（经 _safe_query 包装，DB 忙时返回空）。"""
        return self._safe_query(sql)

    def list_tables(self) -> list:
        """SHOW TABLES（A-prime：统一经 _safe_query —— 含快速重试与忙态记录；返回契约不变）"""
        try:
            df = self._safe_query("SHOW TABLES")
            if len(df) == 0:
                return []
            return [str(v) for v in df.iloc[:, 0].tolist()]
        except Exception:
            return []

    def table_rowcount(self, table: str) -> int:
        """表行数（A-prime：统一经 _safe_query；返回契约不变：失败/忙时返回 0）"""
        try:
            df = self._safe_query(f"SELECT COUNT(*) AS n FROM {table}")
            if len(df) == 0:
                return 0
            return int(df.iloc[0]["n"])
        except Exception:
            return 0

    def preview_table(self, table: str, limit: int = 100,
                      where: str = "", order_by: str = "") -> pd.DataFrame:
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        sql += f" LIMIT {limit}"
        return self.query_duckdb(sql)

    def get_watermarks(self) -> pd.DataFrame:
        try:
            return self.query_duckdb("SELECT * FROM source_watermark")
        except Exception:
            return pd.DataFrame()

    # ---------------- FP-1（2026-10-18）：数据止点（max(time)，只读）----------------
    def get_data_bounds(self, tables, ttl_s: float = DATA_BOUNDS_TTL_S) -> dict:
        """各表**真实数据止点**（``max(time)``）——只读、带 TTL 缓存、表名白名单校验。

        背景（firefly 水位线案）：任务页原只有「水位线」一列（= ``source_watermark
        .last_date``，即下次增量起点/游标）。游标被 QFQ 门控保持时，客户会把
        "游标落后"误读成"数据缺失"。本函数提供对账用的第二列。

        契约：``{table: {"max_ts": int | None, "max_date": str, "error": str}}``
          - 空表        → ``max_ts=None`` / ``max_date=""``（GUI 显示「—」）；
          - 表名非法    → ``error="invalid_identifier"``；
          - 查询异常/忙 → ``error=<异常类名>``（不抛，不影响任务页其余列渲染）。
        任何分支都**不写库**；命中缓存时不发起查询。
        """
        now = time.monotonic()
        out: dict = {}
        pending: list = []
        time_tables = self._tables_with_time()
        for raw in tables or ():
            name = str(raw or "").strip()
            if not name:
                continue
            if not _IDENT_RE.match(name):
                out[name] = {"max_ts": None, "max_date": "", "error": "invalid_identifier"}
                continue
            if time_tables is not None and name not in time_tables:
                # 无 time 列的表（如 stock_dividend 按 ex_date 存储）——不适用数据止点
                out[name] = {"max_ts": None, "max_date": "", "error": "no_time_column"}
                continue
            cached = self._bounds_cache.get(name)
            if cached is not None and (now - self._bounds_at.get(name, 0.0)) < float(ttl_s):
                out[name] = dict(cached)
                continue
            pending.append(name)
        got: dict = {}
        uniq = list(dict.fromkeys(pending))
        if uniq:
            # 单连接单查询（UNION ALL）——实测 4 表合计冷启 ~1.1s、热 ~0.1s；
            # 逐表查询需 4 次连接（每次 ~0.15s 连接/回放开销），实测 7.4s，故合并。
            sql = " UNION ALL ".join(
                f"SELECT '{name}' AS t, max(time) AS max_t FROM {name}" for name in uniq)
            try:
                df = self.query_duckdb(sql)
                got = {str(r["t"]): r["max_t"] for _, r in df.iterrows()}
            except Exception:                       # 含某表缺失（CatalogException）→
                got = {}                            # 降级逐表，保住可用表
                for name in uniq:
                    try:
                        d1 = self.query_duckdb(f"SELECT max(time) AS max_t FROM {name}")
                        got[name] = d1.iloc[0]["max_t"] if len(d1) else None
                    except Exception as e:
                        got[name] = e
        for name in pending:
            raw_val = got.get(name)
            entry = self._bounds_entry(raw_val, missing=(name not in got))
            # 只缓存**成功**结果：超时/降级（QUERY_DEADLINE_S 命中）不得被缓存，
            # 否则 60s 内每次刷新都显示「—」，把瞬时降级固化成"无数据"假象。
            if not entry["error"]:
                self._bounds_cache[name] = dict(entry)
                self._bounds_at[name] = time.monotonic()
            out[name] = dict(entry)
        return out

    def _tables_with_time(self, ttl_s: float = 300.0) -> Optional[set]:
        """具备 ``time`` 列的物理表集合（元数据查询，300s 缓存）。

        返回 ``None`` = 无法确定（查询失败）→ 调用方按「未知」处理，仍尝试查询，
        由 SQL 异常兜底；绝不因元数据不可用而阻断展示。
        """
        now = time.monotonic()
        if self._time_tables is not None and (now - self._time_tables_at) < float(ttl_s):
            return self._time_tables
        try:
            df = self.query_duckdb(
                "SELECT DISTINCT table_name FROM duckdb_columns() WHERE column_name='time'")
            names = {str(r["table_name"]) for _, r in df.iterrows()}
            # 空结果不得当权威（降级空表 vs 真"无 time 列表"无法区分）——不缓存，下次重查。
            # 否则一次库忙会把所有表判成 no_time_column，且 300s 内固化该误判。
            if not names:
                return None
            self._time_tables = names
            self._time_tables_at = time.monotonic()
            return self._time_tables
        except Exception:
            return None

    @staticmethod
    def _bounds_entry(max_t, missing: bool = False) -> dict:
        """把 max(time) 值规整为 {max_ts, max_date, error}（纯函数，便于单测）。"""
        entry = {"max_ts": None, "max_date": "", "error": ""}
        if isinstance(max_t, Exception):
            entry["error"] = type(max_t).__name__
            return entry
        if max_t is None or (not isinstance(max_t, str) and pd.isna(max_t)):
            entry["error"] = "" if not missing else "no_result"
            return entry
        try:
            ts = int(max_t)
        except Exception:
            entry["error"] = "bad_value"
            return entry
        import datetime
        entry["max_ts"] = ts
        entry["max_date"] = datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        return entry

    # ---------------- FP-2（2026-10-18）：水位门控状态（只读聚合）----------------
    def get_watermark_gate_status(self) -> dict:
        """QFQ 水位状态聚合（**只读**，不写任何表）。

        目标：把"数据已写入、水位被门控保持"这一事实常驻可见，而不是只出现在
        一次运行结束的弹窗里（``task_tab._qfq_warning_from_result`` 仅覆盖手动跑完）。

        返回：``{"cycle": {...}, "queue": {status: n}, "by_table": {(table, freq): {...}}}``
          ``by_table`` 值：``{"state": "normal|deferred|held", "label": "正常/延迟提交/门控保持",
          "reason": str}``；非托管表一律 ``normal``（与采集路径逐位一致）。

        判定顺序（只用既有数据，不改变任何门控决策）：
          ① 无 QFQ 编排（查不到 qfq_cycle_run / 非托管表）→ normal；
          ② 存在对应 (table, freq) 的 pending intent      → deferred（等周期门控）；
          ③ 队列 in_progress>0 或 dead_letter>0            → held（附计数为原因）；
          ④ 最新周期仍处活跃态（未 finalize）              → held（附 status/phase）。
        """
        result: dict = {"cycle": {}, "queue": {}, "by_table": {}}
        pending: set = set()
        # 单连接单查询聚合（队列计数 + 最新周期 + 当前周期 pending 意图）。
        # 手写串联可避免 3 次连接（实测每次 ~0.15s）；任何异常 → 全量降级为空。
        _latest = "SELECT cycle_id FROM qfq_cycle_run ORDER BY started_at DESC LIMIT 1"
        try:
            agg = self.query_duckdb(
                "SELECT "
                " (SELECT count(*) FROM qfq_trigger_queue WHERE status='in_progress') AS q_in_progress, "
                " (SELECT count(*) FROM qfq_trigger_queue WHERE status='dead_letter') AS q_dead_letter, "
                f" ({_latest}) AS cycle_id, "
                " (SELECT status FROM qfq_cycle_run ORDER BY started_at DESC LIMIT 1) AS cycle_status, "
                " (SELECT phase FROM qfq_cycle_run ORDER BY started_at DESC LIMIT 1) AS cycle_phase, "
                " (SELECT started_at FROM qfq_cycle_run ORDER BY started_at DESC LIMIT 1) AS cycle_started_at, "
                " (SELECT finished_at FROM qfq_cycle_run ORDER BY started_at DESC LIMIT 1) AS cycle_finished_at, "
                " (SELECT coalesce(string_agg(DISTINCT table_name || '|' || freq, ','), '') "
                "    FROM qfq_watermark_intent WHERE status='pending' "
                f"    AND cycle_id=({_latest})) AS pending_cur")
            if len(agg) > 0:
                a = agg.iloc[0]
                cid = a.get("cycle_id")
                if cid is not None and not pd.isna(cid):
                    result["cycle"] = {
                        "cycle_id": str(cid),
                        "status": str(a.get("cycle_status") or ""),
                        "phase": str(a.get("cycle_phase") or ""),
                        "started_at": a.get("cycle_started_at"),
                        "finished_at": a.get("cycle_finished_at"),
                    }
                result["queue"] = {
                    "in_progress": int(a.get("q_in_progress") or 0),
                    "dead_letter": int(a.get("q_dead_letter") or 0),
                }
                raw_pending = str(a.get("pending_cur") or "")
                for item in [x for x in raw_pending.split(",") if "|" in x]:
                    tname, _, pfreq = item.partition("|")
                    pending.add((tname, pfreq))
        except Exception:
            result["cycle"] = {}
            result["queue"] = {}


        cycle = result["cycle"]
        cycle_active = cycle.get("status") in _CYCLE_ACTIVE_STATES
        in_prog = int(result["queue"].get("in_progress", 0) or 0)
        dead = int(result["queue"].get("dead_letter", 0) or 0)

        for table in sorted(QFQ_COORDINATED_TABLES | {t for t, _ in pending}):
            freqs = [f for t, f in pending if t == table] or [None]
            for freq in freqs:
                state, reason = "normal", ""
                if table in QFQ_COORDINATED_TABLES:
                    if (table, freq) in pending or (freq is None and any(t == table for t, _ in pending)):
                        state = "deferred"
                        reason = (f"水位延迟提交：等待周期 {cycle.get('cycle_id') or '—'} "
                                  f"门控结果（候选水位已记录，未提交）")
                    if in_prog > 0 or dead > 0:
                        state, parts = "held", []
                        if in_prog > 0:
                            parts.append(f"in_progress={in_prog}")
                        if dead > 0:
                            parts.append(f"dead_letter={dead}")
                        if cycle_active:
                            parts.append(f"周期 {cycle.get('cycle_id') or '—'} 未 finalize"
                                         f"（{cycle.get('status')}/{cycle.get('phase')}）")
                        reason = "门控保持：" + "；".join(parts)
                    elif cycle_active and state == "normal":
                        state = "held"
                        reason = (f"门控保持：周期 {cycle.get('cycle_id') or '—'} 未 finalize"
                                  f"（{cycle.get('status')}/{cycle.get('phase')}）")
                result["by_table"][(table, freq)] = {
                    "state": state,
                    "label": WATERMARK_STATE_LABELS.get(state, state),
                    "reason": reason,
                }
        return result


    def get_table_columns(self, table: str) -> list:
        """表结构 [(列名, 类型)]（A-prime：统一经 _safe_query；返回契约不变）"""
        try:
            df = self._safe_query(f"DESCRIBE {table}")
            if len(df) == 0:
                return []
            cols = list(df.columns)
            name_col = "column_name" if "column_name" in cols else cols[0]
            type_col = "column_type" if "column_type" in cols else (cols[1] if len(cols) > 1 else cols[0])
            return [(str(r[name_col]), str(r[type_col])) for _, r in df.iterrows()]
        except Exception:
            return []

    def get_date_range(self, table: str, code: Optional[str] = None) -> dict:
        try:
            where = f"WHERE code='{code}'" if code else ""
            df = self.query_duckdb(
                f"SELECT MIN(time) as min_t, MAX(time) as max_t, COUNT(*) as cnt "
                f"FROM {table} {where}")
            if len(df) == 0:
                return {}
            row = df.iloc[0]
            import datetime
            min_d = datetime.datetime.fromtimestamp(row["min_t"]/1000).strftime("%Y-%m-%d") if row["min_t"] else ""
            max_d = datetime.datetime.fromtimestamp(row["max_t"]/1000).strftime("%Y-%m-%d") if row["max_t"] else ""
            return {"min_date": min_d, "max_date": max_d, "count": int(row["cnt"])}
        except Exception:
            return {}

    # ---------------- SQLite（隔离区 + 批次审计）----------------
    def query_quarantine(self, sql: str) -> pd.DataFrame:
        if not self.quarantine_path.exists():
            return pd.DataFrame()
        with sqlite3.connect(self.quarantine_path) as conn:
            return pd.read_sql_query(sql, conn)

    def quarantine_stats(self) -> dict:
        if not self.quarantine_path.exists():
            return {}
        try:
            with sqlite3.connect(self.quarantine_path) as conn:
                cur = conn.execute("SELECT status, COUNT(*) FROM quarantine GROUP BY status")
                return dict(cur.fetchall())
        except Exception:
            return {}

    def query_quarantine_all(self, status=None, table=None) -> pd.DataFrame:
        """查询隔离区全部状态的数据（不限 pending_repair）"""
        if not self.quarantine_path.exists():
            return pd.DataFrame()
        sql = "SELECT * FROM quarantine WHERE 1=1"
        params = []
        if status and status != "全部":
            sql += " AND status=?"
            params.append(status)
        if table:
            sql += " AND table_name=?"
            params.append(table)
        sql += " ORDER BY ingested_at DESC LIMIT 500"
        try:
            with sqlite3.connect(self.quarantine_path) as conn:
                return pd.read_sql_query(sql, conn, params=params)
        except Exception:
            return pd.DataFrame()

    def query_batch_audit(self, limit: int = 20) -> pd.DataFrame:
        if not self.batch_audit_path.exists():
            return pd.DataFrame()
        with sqlite3.connect(self.batch_audit_path) as conn:
            return pd.read_sql_query(
                f"SELECT * FROM batch_audit ORDER BY finished_at DESC LIMIT {limit}", conn)
