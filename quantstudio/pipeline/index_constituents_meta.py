"""指数成分快照完整性 meta 打点（F3 修订 v2，审核返工）。

契约：index_constituents_snapshot_meta 是**正式批次契约** —— 每个
(index_code, time) 快照在写入/迁移打点时确定完整性状态，一经写入不再因
未来数据改变。历史 PIT 查询只消费 status='complete' 的快照。

状态语义（打点即定，不依赖未来数据）：
- 'complete'：无质量违规，且
  - 固定成分指数（expectations 登记）：COUNT(DISTINCT code) >= expected_count；
  - 可变成分指数（variable_indices 显式登记）：COUNT(DISTINCT code) > 0；
- 'partial'：无质量违规但成分数不足（固定指数）或为 0（可变指数）；
- 'invalid'：重大质量违规（重复代码 / 负权重 / 空或非法代码）——永远不得
  被当作完整 PIT 快照；
- 'unknown'：指数未在 expectations / variable_indices 登记 —— fail-closed，
  Provider 不服务（可变成分绝不默认 count>0 即完整）。

配置缺失：load_expectations 抛 ExpectationsConfigError（fail-closed），
绝不静默返回空配置放行。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = (Path(__file__).resolve().parents[2]
                   / "config" / "index_constituents_expectations.json")

#: 合法证券代码（裸 6 位数字）
import re as _re
_CODE_RE = _re.compile(r"^\d{6}$")


class ExpectationsConfigError(RuntimeError):
    """expectations 配置缺失/非法（fail-closed）。"""


def load_expectations(config_path: Optional[str | Path] = None) -> Dict:
    """加载指数完整性契约配置。文件缺失/非法 → ExpectationsConfigError。"""
    path = Path(config_path) if config_path else _DEFAULT_CONFIG
    if not path.exists():
        raise ExpectationsConfigError(
            f"index constituents expectations config missing: {path} "
            f"(fail-closed: no snapshot may be marked complete without the "
            f"formal contract)")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        expectations = {str(k): int(v)
                        for k, v in payload.get("expectations", {}).items()}
        variable = [str(c) for c in payload.get("variable_indices", [])]
    except (ValueError, TypeError, AttributeError) as e:
        raise ExpectationsConfigError(
            f"index constituents expectations config invalid: {path}: {e}") from e
    overlap = set(expectations) & set(variable)
    if overlap:
        raise ExpectationsConfigError(
            f"indices registered both fixed and variable: {sorted(overlap)}")
    return {"expectations": expectations, "variable_indices": variable}


def compute_snapshot_status(n_distinct: int, expected_count: Optional[int],
                            is_variable: bool, registered: bool,
                            has_violations: bool) -> str:
    """完整性判定（写入时一次确定，不依赖未来数据）。"""
    if has_violations:
        return "invalid"
    if not registered:
        return "unknown"
    if expected_count is not None:
        return "complete" if n_distinct >= expected_count else "partial"
    if is_variable:
        return "complete" if n_distinct > 0 else "partial"
    return "unknown"


def refresh_snapshot_meta(conn, index_codes=None,
                          expectations: Optional[Dict] = None,
                          config_path: Optional[str | Path] = None,
                          data_source: str = "tushare",
                          now: Optional[str] = None) -> int:
    """按 index_constituents 实际批次重建/补齐 snapshot_meta（幂等 upsert）。

    统计 COUNT(DISTINCT code) 与质量违规（重复/负权重/空代码），按契约
    判定 status。返回打点行数。只基于当前已写入批次，绝不使用未来快照。
    """
    if expectations is None:
        expectations = load_expectations(config_path)
    fixed = expectations.get("expectations", {})
    variable = set(expectations.get("variable_indices", []))
    ts = now or datetime.now().isoformat()
    where = ""
    params: list = []
    if index_codes:
        placeholders = ", ".join("?" for _ in index_codes)
        where = f"WHERE index_code IN ({placeholders})"
        params = [str(c) for c in index_codes]
    stats = conn.execute(f"""
        SELECT index_code, time,
               COUNT(DISTINCT code) AS n_distinct,
               COUNT(*) - COUNT(DISTINCT code) AS n_dup,
               SUM(CASE WHEN weight < 0 THEN 1 ELSE 0 END) AS n_neg,
               SUM(CASE WHEN code IS NULL OR TRIM(code) = ''
                         OR NOT REGEXP_MATCHES(code, '^[0-9]{{6}}$')
                        THEN 1 ELSE 0 END) AS n_blank
        FROM index_constituents {where}
        GROUP BY index_code, time
    """, params).fetchall()
    rows = []
    for index_code, time_ms, n_distinct, n_dup, n_neg, n_blank in stats:
        idx = str(index_code)
        expected = fixed.get(idx)
        registered = idx in fixed or idx in variable
        has_violations = bool(n_dup or n_neg or n_blank)
        status = compute_snapshot_status(
            int(n_distinct), expected, idx in variable, registered, has_violations)
        rows.append((idx, int(time_ms), int(n_distinct), expected, status,
                     int(n_dup or 0), int(n_neg or 0), int(n_blank or 0),
                     ts, data_source))
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO index_constituents_snapshot_meta "
        "(index_code, time, n_constituents, expected_count, status, "
        " n_duplicate_codes, n_negative_weights, n_blank_codes, "
        " update_time, data_source) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (index_code, time) DO UPDATE SET "
        "n_constituents=EXCLUDED.n_constituents, "
        "expected_count=EXCLUDED.expected_count, "
        "status=EXCLUDED.status, "
        "n_duplicate_codes=EXCLUDED.n_duplicate_codes, "
        "n_negative_weights=EXCLUDED.n_negative_weights, "
        "n_blank_codes=EXCLUDED.n_blank_codes, "
        "update_time=EXCLUDED.update_time, "
        "data_source=EXCLUDED.data_source",
        rows)
    logger.info(f"[snapshot-meta] refreshed {len(rows)} snapshot meta rows")
    return len(rows)
