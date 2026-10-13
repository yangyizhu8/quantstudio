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
