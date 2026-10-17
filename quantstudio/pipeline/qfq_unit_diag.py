# -*- coding: utf-8 -*-
"""applying 单元级 outcome 诊断输出

方案件：docs/applying-unit-diagnostics-design-20261013.md（②审通过 + 三裁定 + 实施令）
总调度 2026-10-13 三裁定：
  · 裁①：**日志 + 审计行**——审计行落**既有表** `qfq_aux.db:qfq_deep_audit_item`
    （既有 DDL 真相源；该库定位「纯检测辅助数据、不参与价格原子事务」），
    且该库**外部只读可开** ⇒ 解本案「主库独占锁致取证卡死」；
  · 裁②：**逐单元全量**（成功面基线是定位「系统性前置条件」的必要对照）；
  · 裁③：env `QS_QFQ_UNIT_DIAG` **默认开**；**审计表逐单元全量、日志行仅失败单元**（WARNING），
    保留既有聚合心跳（降噪与全量兼得）。

纪律（照仓内既有结论 adopted）：
  · 只**读取**队列结果后记录 ⇒ **不改判定/写路径/水位/队列状态机**；
  · 记录自身异常**一律兜住**（照 `code_contract`「拒绝⇒隔离计数+审计行，**不中断整批/整轮**」约定）；
  · **不触价格事务**：独立短连接写 SQLite 辅助库（同进程内多连接安全）。
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

LOG = logging.getLogger("quantstudio.pipeline.qfq_unit_diag")

UNIT_DIAG_ENV = "QS_QFQ_UNIT_DIAG"     # 默认 1=启用；0/false/off=关闭（等效旧行为）
_TABLE = "qfq_deep_audit_item"
_MAX_ERR = 300                         # last_error 截断（方案 §2 限幅）


def unit_diag_enabled() -> bool:
    v = str(os.environ.get(UNIT_DIAG_ENV, "1")).strip().lower()
    return v not in ("0", "false", "off", "no")


def _round_no(now: Optional[datetime] = None) -> int:
    """round_no = 业务日 YYYYMMDD（可读、按日唯一）。

    注：审计表 PK=(asset_type, code, round_no) ⇒ 同码同日 **UPSERT 保留最后一次**（方案 §2）。
    跨零点的周期可能落到相邻两日（已知边界，记录于方案）。
    """
    return int((now or datetime.now()).strftime("%Y%m%d"))


def _aux_path():
    from quantstudio._paths import db_path
    return db_path("qfq_aux.db")


def record_unit_outcome(*, asset_type: str, code: str, status: str,
                        attempt_count: int = 0,
                        last_error: Optional[str] = None,
                        round_no: Optional[int] = None,
                        log=LOG) -> bool:
    """记录一个 applying 单元的 outcome。返回是否成功记录（异常一律兜住，绝不抛出）。"""
    if not unit_diag_enabled():
        return False
    try:
        rn = _round_no() if round_no is None else int(round_no)
        err = (str(last_error) if last_error else None)
        if err and len(err) > _MAX_ERR:
            err = err[:_MAX_ERR] + "...(截断)"
        ts = datetime.now().isoformat(timespec="seconds")
        conn = sqlite3.connect(str(_aux_path()), timeout=5)
        try:
            conn.execute(
                "INSERT INTO %s (asset_type, code, status, round_no, attempt_count,"
                " last_error, updated_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(asset_type, code, round_no) DO UPDATE SET"
                " status=excluded.status, attempt_count=excluded.attempt_count,"
                " last_error=excluded.last_error, updated_at=excluded.updated_at"
                % _TABLE,
                [str(asset_type), str(code), str(status), rn, int(attempt_count or 0),
                 err, ts])
            conn.commit()
        finally:
            conn.close()
        # 裁③：日志仅失败单元（成功不刷日志行）
        if str(status) != "committed":
            log.warning("[qfq_orch] 单元诊断 %s/%s status=%s attempt=%s err=%s",
                        asset_type, code, status, attempt_count,
                        (err or "-")[:160])
        return True
    except Exception as e:      # 记录失败绝不影响 applying
        try:
            log.error("[qfq_orch] 单元诊断记录失败（不阻断）: %s", e)
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# D1-补1（2026-10-17）：D1/a0 隔离登记落盘（独立表 qfq_d1_isolation_log）
# 方案件：docs/d1-isolation-log-design-20261017.md（§5 fail-soft 三条 / §6 独立函数）
# ---------------------------------------------------------------------------

_D1_ISOLATION_TABLE = "qfq_d1_isolation_log"
_D1_ISOLATION_KINDS = ("row_skipped", "whole_code_missing")


def record_d1_isolation(*, asset_type: str, code: str, kind: str,
                        skipped_rows: int, total_rows: int,
                        missing_codes_total: int, listed_codes: int,
                        truncated: int, missing_ratio: Optional[float],
                        max_ratio: Optional[float], whole_code_missing: int,
                        batch_id: Optional[str] = None,
                        table_name: Optional[str] = None,
                        freq: Optional[str] = None,
                        round_no: Optional[int] = None,
                        log=LOG) -> bool:
    """把一条 D1/a0 隔离登记写入 qfq_aux.db:qfq_d1_isolation_log（一码一行）。

    与 record_unit_outcome 的区别（方案 §6）：**不受 QS_QFQ_UNIT_DIAG 开关约束**——
    D1 的免库窗核对是对客户的书面承诺，不应随内部诊断开关消失。

    fail-soft 三条（方案 §5，总调度 2026-10-17 必加项）：
      ① 绝不向取数路径抛出（异常一律兜住）；
      ② 失败必须 log.error（禁止无 ERROR 的静默 return False）；
      ③ 只写 qfq_aux.db 短连接（timeout=5 + PRAGMA busy_timeout），
        不碰主库、不进价格事务；**禁止**在热路径补跑全量 DDL
        （三类失败面：no such table / database is locked / 其他异常 ⇒ 均 ERROR +
         return False）。

    kind 值域断言：仅接受 'row_skipped' / 'whole_code_missing'，越域 raise
    ValueError（照 07-经验库「准入裁决函数唯一性」+ P8-001 同型；调用点自行兜住）。

    UPSERT 口径：同 (asset_type, code, round_no) 再命中 ⇒ hit_count = hit_count + 1，
    其余载荷取 excluded（计数累加保住「今天漏过几次」的证据，载荷取最后一次）。
    """
    if kind not in _D1_ISOLATION_KINDS:
        raise ValueError(
            f"非法 kind: {kind!r}（仅允许 {_D1_ISOLATION_KINDS}）")
    try:
        rn = _round_no() if round_no is None else int(round_no)
        ts = datetime.now().isoformat(timespec="seconds")
        conn = sqlite3.connect(str(_aux_path()), timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                "INSERT INTO %s (asset_type, code, round_no, batch_id, table_name,"
                " freq, kind, skipped_rows, total_rows, missing_codes_total,"
                " listed_codes, truncated, missing_ratio, max_ratio,"
                " whole_code_missing, hit_count, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(asset_type, code, round_no) DO UPDATE SET"
                " hit_count=hit_count+1, batch_id=excluded.batch_id,"
                " table_name=excluded.table_name, freq=excluded.freq,"
                " kind=excluded.kind, skipped_rows=excluded.skipped_rows,"
                " total_rows=excluded.total_rows,"
                " missing_codes_total=excluded.missing_codes_total,"
                " listed_codes=excluded.listed_codes, truncated=excluded.truncated,"
                " missing_ratio=excluded.missing_ratio, max_ratio=excluded.max_ratio,"
                " whole_code_missing=excluded.whole_code_missing,"
                " updated_at=excluded.updated_at" % _D1_ISOLATION_TABLE,
                [str(asset_type), str(code), rn, batch_id, table_name, freq,
                 str(kind), int(skipped_rows or 0), int(total_rows or 0),
                 int(missing_codes_total or 0), int(listed_codes or 0),
                 int(1 if truncated else 0),
                 (None if missing_ratio is None else float(missing_ratio)),
                 (None if max_ratio is None else float(max_ratio)),
                 int(1 if whole_code_missing else 0), 1, ts])
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:      # fail-soft ①：落盘失败绝不影响还原/取数
        try:
            log.error("[qfq_orch] D1 隔离登记落盘失败（不阻断）: %s/%s kind=%s: %s",
                      asset_type, code, kind, e)
        except Exception:
            pass
        return False


def mark_d1_isolation_truncated(*, asset_type: str, table_name: str, freq: str,
                                listed_codes: int, round_no: Optional[int] = None,
                                log=LOG) -> bool:
    """**部分写失败**时把该批已落盘行的 listed_codes 校正为实际值并置 truncated=1。

    ②审 2026-10-17 裁定（判据②缺口修法）：方案 §4 的列语义本就是
    「listed < missing_codes_total ⇒ truncated 置 1」；原实现「全量落盘无上限」
    使该列**恒为死列**（truncated 恒 0）。⇒ 改为：
      · `listed_codes` ＝ **实际成功落盘行数**；
      · `truncated` **写时计算** —— 个别行写失败即自然产生 1。
    本函数即该「写时计算」的落地：**只在失败路径被调用一次**，把本批已落盘行
    校正为真实计数（成功路径零额外开销）。

    fail-soft 契约与 record_d1_isolation 同（方案 §5 三条）：绝不抛出、失败必
    log.error、只写 qfq_aux.db 短连接、不碰主库、热路径不跑 DDL。
    """
    try:
        rn = _round_no() if round_no is None else int(round_no)
        ts = datetime.now().isoformat(timespec="seconds")
        conn = sqlite3.connect(str(_aux_path()), timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                "UPDATE %s SET listed_codes=?, truncated=1, updated_at=?"
                " WHERE asset_type=? AND table_name=? AND freq=? AND round_no=?"
                % _D1_ISOLATION_TABLE,
                [int(listed_codes or 0), ts, str(asset_type), str(table_name),
                 str(freq), rn])
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:      # fail-soft ①：校正失败绝不影响还原/取数
        try:
            log.error("[qfq_orch] D1 隔离登记 truncated 校正失败（不阻断）: "
                      "%s/%s/%s listed=%s: %s",
                      asset_type, table_name, freq, listed_codes, e)
        except Exception:
            pass
        return False
