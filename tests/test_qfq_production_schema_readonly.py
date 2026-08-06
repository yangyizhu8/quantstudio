"""tests/test_qfq_production_schema_readonly.py — 正式库只读 schema 审计（opt-in）。

本测试**默认不运行**（不进默认 pytest collection 的执行）。它只读打开正式生产库
``data/quantstudio.db``，验证 B-3a 状态机对其正确识别为 ``COMPLETE_2_0``，并断言
``assert_init_allowed`` 会 fail-fast。

启用方式（二选一）：
- 设置环境变量 ``QS_PRODUCTION_READONLY=1``；
- 用 marker 显式选择：``pytest -m prod_readonly``（需 marker 注册，见下方 skip 逻辑）。

**绝对只读**：连接以 ``read_only=True`` 打开，不发任何 DDL/DML。本测试本身不修改
正式库；它只是 B-3a 状态机对真实正式库的只读核验。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# 默认跳过：仅当 QS_PRODUCTION_READONLY=1 时执行
pytestmark = pytest.mark.skipif(
    os.environ.get("QS_PRODUCTION_READONLY", "") != "1",
    reason="正式库只读审计默认跳过；设置 QS_PRODUCTION_READONLY=1 启用（仅只读探测）",
)


def _production_db_path() -> Path:
    from quantstudio._paths import db_path
    return Path(db_path()).resolve()


def test_production_db_detected_as_complete_2_0():
    """正式生产库（当前 2.0）必须被识别为 COMPLETE_2_0（B-3a 阻断 1 核验）。"""
    import duckdb
    from quantstudio.pipeline.qfq_schema_status import (
        detect_schema_status, SchemaStatus)

    p = _production_db_path()
    if not p.exists():
        pytest.skip(f"正式库不存在: {p}")
    conn = duckdb.connect(str(p), read_only=True)  # 绝对只读
    try:
        status = detect_schema_status(conn)
        assert status == SchemaStatus.COMPLETE_2_0, (
            f"正式库应被识别为 COMPLETE_2_0（B-3a 阻断 1），实际 {status}")
    finally:
        conn.close()


def test_production_db_init_fail_fast():
    """正式库普通 init 必须 fail-fast（QfqSchemaMigrationRequired）。"""
    import duckdb
    from quantstudio.pipeline.qfq_schema_status import (
        assert_init_allowed, QfqSchemaMigrationRequired)

    p = _production_db_path()
    if not p.exists():
        pytest.skip(f"正式库不存在: {p}")
    conn = duckdb.connect(str(p), read_only=True)  # 绝对只读
    try:
        with pytest.raises(QfqSchemaMigrationRequired):
            assert_init_allowed(conn)
    finally:
        conn.close()
