"""QFQ post-ingest 诊断插桩（方案：docs/qfq-post-ingest-profiling-instrument-design.md）。

**设计约束（用户裁定 H-1~H-4）**：
- ``QS_QFQ_PROFILE=1`` 才启用，默认关；未设/非 "1" 时调用方只付出**一次** ``enabled()`` 的
  ``os.environ.get`` 开销，不进入任何计时分支（零开销，P1 判据 <1ms/次且不产输出文件）。
- 永久保持门控 + 默认关（不摘除；如需摘除则单笔 revert）。
- 读数 schema 预定义（见 ``SCHEMA``），事后不改字段语义。

本模块**只做观测**：不改任何判据、分支、事务边界；写失败只记 warning 不抛穿（P6）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SCHEMA = 1


def enabled() -> bool:
    """门控：仅显式 "1" 开启（"0"/"true"/未设一律关，P1 负向判据）。"""
    return str(os.environ.get("QS_QFQ_PROFILE", "") or "").strip() == "1"


def out_path() -> Path:
    p = str(os.environ.get("QS_QFQ_PROFILE_OUT", "") or "").strip()
    if p:
        return Path(p)
    try:
        from quantstudio._paths import DATA_ROOT  # type: ignore
        return Path(DATA_ROOT) / "logs" / "qfq_profile.jsonl"
    except Exception:
        return Path("data") / "logs" / "qfq_profile.jsonl"


class PhaseTimer:
    """分组累计计时器。

    ``on=False`` 时**所有方法立即返回**（不建 dict、不取时钟）——这是「关闭时零开销」的实现点。
    """

    __slots__ = ("_on", "_t0", "_acc", "_cnt")

    def __init__(self, on: bool) -> None:
        self._on = bool(on)
        self._t0: Dict[str, float] = {}
        self._acc: Dict[str, float] = {}
        self._cnt: Dict[str, int] = {}

    def start(self, key: str) -> None:
        if not self._on:
            return
        self._t0[key] = time.perf_counter()

    def stop(self, key: str) -> None:
        if not self._on:
            return
        t0 = self._t0.pop(key, None)
        if t0 is not None:
            self._acc[key] = self._acc.get(key, 0.0) + (time.perf_counter() - t0)

    def bump(self, key: str, n: int = 1) -> None:
        if not self._on:
            return
        self._cnt[key] = self._cnt.get(key, 0) + n

    def add(self, key: str, seconds: float) -> None:
        """直接累加一段已测得的耗时（用于逐条循环内计时，避免嵌套 start/stop 同名冲突）。"""
        if not self._on:
            return
        self._acc[key] = self._acc.get(key, 0.0) + float(seconds)

    @property
    def on(self) -> bool:
        return self._on

    def phases(self) -> Dict[str, float]:
        return {k: round(v, 6) for k, v in self._acc.items()} if self._on else {}

    def queries(self) -> Dict[str, int]:
        return dict(self._cnt) if self._on else {}


def wal_stats(conn) -> Dict[str, Any]:
    """读取 SQLite WAL 字节数与页数（只读探测，P4 判据）。异常返回 {}。"""
    out: Dict[str, Any] = {}
    try:
        row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()  # (busy, log, checkpointed)
        if row:
            out["checkpoint_passive"] = {"busy": int(row[0] or 0),
                                        "log": int(row[1] or 0),
                                        "checkpointed": int(row[2] or 0)}
    except Exception:
        pass
    try:
        pc = conn.execute("PRAGMA page_count").fetchone()
        out["page_count"] = int(pc[0]) if pc else 0
    except Exception:
        pass
    try:
        ps = conn.execute("PRAGMA page_size").fetchone()
        out["page_size"] = int(ps[0]) if ps else 0
    except Exception:
        pass
    try:
        db = conn.execute("PRAGMA database_list").fetchone()
        if db and db[2]:
            main = Path(str(db[2]))
            out["size_bytes"] = main.stat().st_size if main.exists() else 0
            w = Path(str(db[2]) + "-wal")
            out["wal_size_bytes"] = w.stat().st_size if w.exists() else 0
    except Exception:
        pass
    return out


def percentiles(samples: List[float]) -> Dict[str, Any]:
    """P50/P95/max（毫秒）。空样本返回 0。"""
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0, "samples": 0}
    s = sorted(samples)
    n = len(s)

    def _pct(q: float) -> float:
        idx = min(n - 1, max(0, int(round(q * (n - 1)))))
        return round(s[idx], 3)

    return {"p50": _pct(0.50), "p95": _pct(0.95), "max": round(s[-1], 3), "samples": n}


def write_event(payload: Dict[str, Any]) -> None:
    """追加一行 JSONL。**任何异常只记 warning**，绝不阻断业务（P6）。"""
    try:
        path = out_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 — 观测失败不得影响主流程
        logger.warning("[qfq_profile] 写出失败（忽略）: %s", e)


def build_event(
    *,
    run_id: str,
    asset_type: str,
    n_observations: int,
    n_codes: int,
    n_future_excluded: int,
    phases: Dict[str, float],
    queries: Dict[str, int],
    wal_before: Dict[str, Any],
    wal_after: Dict[str, Any],
    roundtrip_ms: List[float],
    task: str = "",
) -> Dict[str, Any]:
    """按预定义 schema 组装一条事件（字段语义固定，见方案 §2.3）。"""
    wal = {
        "size_before_bytes": int(wal_before.get("wal_size_bytes", 0) or 0),
        "size_after_bytes": int(wal_after.get("wal_size_bytes", 0) or 0),
        "delta_bytes": int(wal_after.get("wal_size_bytes", 0) or 0)
                       - int(wal_before.get("wal_size_bytes", 0) or 0),
        "pages_before": int(wal_before.get("page_count", 0) or 0),
        "pages_after": int(wal_after.get("page_count", 0) or 0),
        "checkpoint_passive": wal_after.get("checkpoint_passive") or {},
    }
    return {
        "schema": SCHEMA,
        "event": "record_observations",
        "run_id": run_id,
        "asset_type": asset_type,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_observations": int(n_observations),
        "n_codes": int(n_codes),
        "n_future_excluded": int(n_future_excluded),
        "phase_s": phases,
        "queries": queries,
        "wal": wal,
        "roundtrip_ms": percentiles(roundtrip_ms),
        "db": {"size_bytes": int(wal_after.get("size_bytes", 0) or 0), "task": task},
    }
