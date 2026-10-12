# -*- coding: utf-8 -*-
"""QuantStudio 按需只读导出（qs_export）

方案件：docs/qs-export-ondemand-design-20261012.md（②审通过 + 三项裁定 + ③实施令）
判型：**纯增益·新增导出能力** —— 写路径零改动、**不新增连接**、不触 DuckDB 单写者锁、
      不扰动周期（导出只在既有 writer/shared_conn 可用时发生）。

裁定落位（总调度 2026-10-12）：
  · 触发位置 = **仅周期内消费**（不破「轻量循环无 conn」不变量；周期外空档由周期收口后自然空闲覆盖）
  · 落点 = **独立 data/qs_export/**（免与 FM 名义混淆）
  · PyQt 入口**豁免** + 登记依据（消费方=会话脚本非 GUI 用户；将来 GUI 需求出现再补）

知识反哺采纳（方案件已留痕）：
  · 07-经验库「清理清单须由部署函数同名生成禁止手写」⇒ 轮转清理清单**复用同一命名函数**
    `artifact_name()`，禁另行手写（防新增格式漏清理致遗留堆积）；
  · fm-export 两工件规约（命名/落点/失败不覆盖旧件/_status.json/轮转 N）；
  · stop.request 三函数模式（path/read/consume_if_matched(token)）；
  · 落裁②「固定空闲窗依赖节律不可靠，已实证不采」⇒ 本模块**不引入任何空闲窗依赖**。

边界与限幅：单次导出**超时 120s**、行数上限、**同刻仅一件**（进程内单飞）；
窗口谓词 `WHERE <col> BETWEEN ? AND ?`（`time` 为 BIGINT ms；日期列走 ISO 串）；
格式 CSV（新增）/ parquet（复用 pyarrow）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

LOG = logging.getLogger("quantstudio.pipeline.qs_export")

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

# ── 可调项（env 覆盖；与 fm_export 同风格）──────────────────────────────
QS_EXPORT_ENV = "QS_ONDEMAND_EXPORT"          # 默认 1=启用；0/false/off=关闭
QS_EXPORT_DIR_ENV = "QS_ONDEMAND_EXPORT_DIR"  # 落点覆盖（测试隔离用）
QS_EXPORT_KEEP_ENV = "QS_ONDEMAND_EXPORT_KEEP"  # 保留份数（默认 10）
DEFAULT_KEEP = 10
DEFAULT_TIMEOUT_S = 120.0                     # 裁定：单次导出限幅 120s
DEFAULT_MAX_ROWS = 2_000_000                  # 行数上限（超限拒绝，不静默截断）


def qs_export_enabled() -> bool:
    v = str(os.environ.get(QS_EXPORT_ENV, "1")).strip().lower()
    return v not in ("0", "false", "off", "no")


def qs_export_dir() -> Path:
    p = os.environ.get(QS_EXPORT_DIR_ENV)
    return Path(p) if p else (DATA / "qs_export")


def qs_export_keep() -> int:
    try:
        return max(1, int(os.environ.get(QS_EXPORT_KEEP_ENV, DEFAULT_KEEP)))
    except Exception:
        return DEFAULT_KEEP


def qs_export_request_path() -> Path:
    """请求件路径（形态照 daemon_stop.request：外部只写、daemon 只读消费）。"""
    return DATA / "qs_export_request.json"


def qs_export_status_path() -> Path:
    """响应件路径（request_id 贯穿；消费方据此判 done/failed）。"""
    return DATA / "qs_export_status.json"


# ── 命名函数（**清理清单的唯一来源**：KB 采纳点）────────────────────────
def artifact_name(table: str, ext: str, stamp: str) -> str:
    """工件命名规约（与 fm-export 对齐）：`qs_<table>_<stamp>.<ext>`。

    ⚠ 本函数是**导出与轮转清理的同一命名函数**——清理清单必须由它派生，
    禁止在清理处另行手写文件名（否则新增格式会被漏清理）。
    """
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(table))
    return f"qs_{safe}_{stamp}.{ext}"


def artifact_glob() -> str:
    """清理用 glob——**派生自同一命名规约**（改命名函数即两处同步生效）。"""
    return "qs_*"


# ── 请求/响应件 ─────────────────────────────────────────────────────
def read_qs_export_request() -> Optional[Dict]:
    p = qs_export_request_path()
    try:
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        LOG.error("[qs_export] 请求件解析失败（忽略本件，不误消费）: %s", e)
        return None


def consume_qs_export_request_if_matched(token: str) -> bool:
    """token 匹配才消费（照 stop.request 语义）；不匹配则保留并留痕。"""
    req = read_qs_export_request()
    if not req:
        return False
    if str(req.get("token")) != str(token):
        LOG.warning("[qs_export] 请求件 token 不匹配 ⇒ 拒绝消费（保留原件）")
        return False
    try:
        qs_export_request_path().unlink(missing_ok=True)
    except Exception as e:
        LOG.warning("[qs_export] 请求件删除失败（下次仍会看到）: %s", e)
    return True


def write_status(**fields) -> None:
    """写响应件（失败必留痕；写失败不抛——观测不得影响导出收尾）。"""
    try:
        payload = dict(fields)
        payload.setdefault("at", datetime.now().isoformat(timespec="seconds"))
        p = qs_export_status_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        LOG.error("[qs_export] 响应件写入失败: %s", e)


# ── 轮转清理（复用命名函数）──────────────────────────────────────────
def cleanup_old(keep: Optional[int] = None, log=LOG) -> List[str]:
    """保留最近 N 件，删除更旧件。清单**必经 artifact_name 命名规约**。"""
    d = qs_export_dir()
    if not d.exists():
        return []
    n = keep if keep is not None else qs_export_keep()
    files = sorted((f for f in d.glob(artifact_glob()) if f.is_file()),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    removed = []
    for f in files[n:]:
        try:
            f.unlink()
            removed.append(f.name)
        except Exception as e:
            log.warning("[qs_export] 清理失败 %s: %s", f.name, e)
    return removed


# ── 执行 ────────────────────────────────────────────────────────────
def _validate(req: Dict) -> Tuple[Optional[Dict], Optional[str]]:
    """请求校验——不合法即拒（不静默降级）。"""
    if not isinstance(req, dict):
        return None, "请求非对象"
    tables = req.get("tables")
    if not tables or not isinstance(tables, list):
        return None, "tables 缺失或非列表"
    fmt = str(req.get("format", "csv")).lower()
    if fmt not in ("csv", "parquet"):
        return None, f"format 不支持: {fmt}"
    win = req.get("window") or {}
    col = win.get("col")
    if not col:
        return None, "window.col 缺失"
    if win.get("start") is None or win.get("end") is None:
        return None, "window.start/end 缺失"
    # ⚠ 显式判 None：**不可用 `or`**——`0 or 默认` 会把 0 静默替换成默认值，
    # 令「limit=0 应拒绝」变成「静默用满上限」、「timeout_s=0 应即超时」变成「用默认 120s」。
    _lim = req.get("limit")
    limit = DEFAULT_MAX_ROWS if _lim is None else int(_lim)
    if limit <= 0 or limit > DEFAULT_MAX_ROWS:
        return None, f"limit 超限（1..{DEFAULT_MAX_ROWS}）"
    return {"tables": tables, "format": fmt, "col": str(col),
            "start": win["start"], "end": win["end"], "limit": limit,
            "columns": req.get("columns") or "*",
            "timeout_s": (DEFAULT_TIMEOUT_S if req.get("timeout_s") is None
                          else float(req.get("timeout_s")))}, None


def run_qs_export(conn, req: Dict, log=LOG) -> Dict:
    """执行一次按需导出。返回 {ok, request_id, artifacts[], rows, error}。

    纪律：**失败不覆盖旧件**（先写临时件、成功才改名）+ 失败必留痕；不改任何写路径。
    """
    t0 = time.time()
    rid = str(req.get("request_id") or "-")
    spec, err = _validate(req)
    if err:
        log.error("[qs_export] 请求 %s 校验失败: %s", rid, err)
        return {"ok": False, "request_id": rid, "error": err}

    out = qs_export_dir()
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    artifacts, total = [], 0

    for table in spec["tables"]:
        if time.time() - t0 > spec["timeout_s"]:
            msg = f"超时（>{spec['timeout_s']:.0f}s）——已导出 {len(artifacts)} 表后中止"
            log.error("[qs_export] %s: %s", rid, msg)
            return {"ok": False, "request_id": rid, "artifacts": artifacts,
                    "rows": total, "error": msg}
        try:
            cols = spec["columns"]
            collist = "*" if cols == "*" else ", ".join(f'"{c}"' for c in cols)
            sql = (f'SELECT {collist} FROM "{table}" '
                   f'WHERE "{spec["col"]}" BETWEEN ? AND ? LIMIT ?')
            rows = conn.execute(sql, [spec["start"], spec["end"], spec["limit"]]).fetchall()
            colnames = [d[0] for d in conn.description] if conn.description else []
            total += len(rows)

            name = artifact_name(table, spec["format"], stamp)
            dst = out / name
            tmp = out / (name + ".tmp")
            if spec["format"] == "csv":
                import csv as _csv
                with open(tmp, "w", newline="", encoding="utf-8") as fh:
                    w = _csv.writer(fh)
                    w.writerow(colnames)
                    w.writerows(rows)
            else:
                import pyarrow as pa
                import pyarrow.parquet as pq
                tbl = pa.table({c: [r[i] for r in rows] for i, c in enumerate(colnames)}) if colnames else pa.table({})
                pq.write_table(tbl, str(tmp))
            tmp.replace(dst)          # 成功才落位（失败不覆盖旧件）
            artifacts.append({"table": table, "path": str(dst), "rows": len(rows),
                              "columns": colnames})
            log.info("[qs_export] %s: %s → %s (%d 行)", rid, table, name, len(rows))
        except Exception as e:
            log.error("[qs_export] %s 表 %s 导出失败: %s", rid, table, e, exc_info=True)
            return {"ok": False, "request_id": rid, "artifacts": artifacts,
                    "rows": total, "error": f"{table}: {type(e).__name__}: {e}"}

    removed = cleanup_old(log=log)
    if removed:
        log.info("[qs_export] 轮转清理 %d 件: %s", len(removed), removed[:5])
    return {"ok": True, "request_id": rid, "artifacts": artifacts, "rows": total,
            "removed": removed, "elapsed_s": round(time.time() - t0, 2)}


# ── daemon 侧入口（**周期内消费**：调用方须已持 writer/shared_conn）────────
_SINGLE_FLIGHT = {"running": False}


def serve_request_if_any(conn, token: str, log=LOG) -> Optional[Dict]:
    """周期内被调用：有请求件且 token 匹配 ⇒ 消费并导出（同刻仅一件）。

    返回 None 表示「无请求/未启用/已在跑」；否则返回导出结果。
    **不新增连接**——conn 由调用方提供（daemon 既有 writer.shared_conn()）。
    """
    if not qs_export_enabled():
        return None
    if _SINGLE_FLIGHT["running"]:
        log.warning("[qs_export] 已有导出在跑 ⇒ 本次跳过（同刻仅一件）")
        return None
    req = read_qs_export_request()          # 先读内容，再消费（消费会删件）
    if not req:
        return None
    if str(req.get("token")) != str(token):
        log.warning("[qs_export] 请求件 token 不匹配 ⇒ 拒绝消费（保留原件）")
        return None
    try:
        qs_export_request_path().unlink(missing_ok=True)   # 消费
    except Exception as e:
        log.warning("[qs_export] 请求件删除失败（下次仍会看到）: %s", e)
    _SINGLE_FLIGHT["running"] = True
    try:
        res = run_qs_export(conn, req, log=log)
        write_status(state="done" if res.get("ok") else "failed", **res)
        return res
    except Exception as e:
        log.error("[qs_export] 导出异常: %s", e, exc_info=True)
        write_status(request_id=str(req.get("request_id") or "-"),
                     state="failed", ok=False, error=str(e))
        return {"ok": False, "request_id": req.get("request_id"), "error": str(e)}
    finally:
        _SINGLE_FLIGHT["running"] = False
