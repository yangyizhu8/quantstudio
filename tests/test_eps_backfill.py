"""P-A3：eps 跨表回补 + 同源复制列门禁测试（tests/test_eps_backfill.py）。

全部用例使用**临时 DuckDB 库**（`_make_temp_db` 模式），不触碰真实
data/quantstudio.db —— 硬约束 A（SNAP_003 create/verify/protect 期间禁主库写）。

覆盖（≥12 例）：
1. 回补命中（值=income basic_eps）
2. 幂等（二次执行 0 行）
3. 无缺口零行为（无 NULL 夹具：回补前后数据透出逐字节一致）
4. PIT 保守（ann_date=max；回测窗口早于 ic_ann 时回补值不可见）
5. 打标（backfill_eps_source 值正确；data_source 原值不变）
6. 禁区（income 无行 → 保持 NULL）
7. 多匹配确定性（(code,end_date) 多 ann_date 取最新公告版）
8. PK 冲突 0（ann_date 调整后仍无重复主键）
9. 门禁 gap 计数（缺口>0；免疫闭环=0）
10. 门禁错误触发（quality_audit EpsBackfillGap error）
11. diluted_eps 显式排除（income 无列 → 不参与回补）
12. 回补可逆（revert：打标行 eps→NULL）
13. CLI dry-run 不落库（scripts/backfill_eps_gap.py --backfill 无 --apply）
14. writers.py 写后自动回补 feature gate：默认关闭/显式开启/CLI 独立/fail-closed
15. 防线 3：_latest_by_code 最新有值行（策略 helper 语义）
"""
import os
import tempfile
import uuid

import pandas as pd
import pytest

from quantstudio.pipeline import eps_backfill
from quantstudio.pipeline.eps_backfill import (
    BACKFILL_SOURCE_COL,
    BACKFILL_SOURCE_MARK,
    BackfillResult,
    backfill_eps_gap,
    check_eps_backfill_gap,
    revert_backfill,
)


def _make_temp_db():
    """创建临时 DuckDB（test_financial_dividend_schema_migration 同款）"""
    import duckdb

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    conn = duckdb.connect(path)
    conn.close()
    return path


def _fresh_db():
    """建临时库 + 两张表（fin_indicator / income_statement）+ 打标列。"""
    import duckdb

    path = _make_temp_db()
    conn = duckdb.connect(path)
    conn.execute("""
        CREATE TABLE fin_indicator (
            code VARCHAR, ann_date BIGINT, end_date BIGINT,
            eps DOUBLE, diluted_eps DOUBLE, bps DOUBLE, roe DOUBLE,
            pe_ttm DOUBLE, pb DOUBLE, ps_ttm DOUBLE,
            np_yoy DOUBLE, or_yoy DOUBLE, tr_yoy DOUBLE,
            update_flag INTEGER, data_source VARCHAR,
            backfill_eps_source VARCHAR,
            PRIMARY KEY(code, end_date, ann_date)
        )
    """)
    conn.execute("""
        CREATE TABLE income_statement (
            code VARCHAR, end_date BIGINT, ann_date BIGINT,
            operating_revenue DOUBLE, operating_cost DOUBLE, operating_profit DOUBLE,
            total_profit DOUBLE, net_profit DOUBLE, np_parent_company_owners DOUBLE,
            sale_expense DOUBLE, manage_expense DOUBLE, finance_expense DOUBLE, rd_expense DOUBLE,
            income_tax DOUBLE, basic_eps DOUBLE,
            update_time VARCHAR, data_source VARCHAR,
            PRIMARY KEY(code, end_date, ann_date)
        )
    """)
    return conn, path


def _ms(date_str: str) -> int:
    """'YYYY-MM-DD' → 毫秒时间戳（与管线 time_to_ms 同口径）。"""
    return int(pd.Timestamp(date_str, tz="Asia/Shanghai").timestamp() * 1000)


def _seed_gap(conn, dry_run=False):
    """种子数据：A 码 eps 缺口（income 有 basic_eps）；B 码无缺口；C 码 income 无行（禁区）。"""
    # income_statement：A 码 2026Q1 basic_eps=0.27（公告 2026-04-25）
    conn.execute(
        "INSERT INTO income_statement (code, end_date, ann_date, basic_eps, data_source) VALUES "
        "(?, ?, ?, ?, 'mcp')",
        ["000063", _ms("2026-03-31"), _ms("2026-04-25"), 0.27],
    )
    # A 码 fin_indicator：eps NULL，or_yoy 有值（镜像根因）
    conn.execute(
        "INSERT INTO fin_indicator (code, ann_date, end_date, eps, or_yoy, data_source) VALUES "
        "(?, ?, ?, ?, ?, 'mcp')",
        ["000063", _ms("2026-04-25"), _ms("2026-03-31"), None, 6.1267],
    )
    # B 码：fin eps 有值（无缺口）
    conn.execute(
        "INSERT INTO income_statement (code, end_date, ann_date, basic_eps, data_source) VALUES "
        "(?, ?, ?, ?, 'mcp')",
        ["000001", _ms("2026-03-31"), _ms("2026-04-20"), 0.67],
    )
    conn.execute(
        "INSERT INTO fin_indicator (code, ann_date, end_date, eps, data_source) VALUES "
        "(?, ?, ?, ?, 'mcp')",
        ["000001", _ms("2026-04-20"), _ms("2026-03-31"), 0.67],
    )
    # C 码：fin eps NULL 但 income 无行（次新/禁区 → 不可回补）
    conn.execute(
        "INSERT INTO fin_indicator (code, ann_date, end_date, eps, or_yoy, data_source) VALUES "
        "(?, ?, ?, ?, ?, 'mcp')",
        ["001220", _ms("2026-04-29"), _ms("2025-03-31"), None, None],
    )


# ---------------------------------------------------------------------------
# 1. 回补命中
# ---------------------------------------------------------------------------
def test_backfill_hit_fills_eps_from_basic_eps():
    conn, path = _fresh_db()
    try:
        _seed_gap(conn)
        result = backfill_eps_gap(conn)
        assert isinstance(result, BackfillResult)
        assert result.rows_updated == 1, result.summary()
        assert "000063" in result.affected_codes
        row = conn.execute(
            "SELECT eps, backfill_eps_source, data_source FROM fin_indicator WHERE code='000063'"
        ).fetchone()
        assert row[0] == pytest.approx(0.27)  # eps = income basic_eps ✓
        assert row[1] == BACKFILL_SOURCE_MARK  # 打标 ✓
        assert row[2] == "mcp"  # data_source 原值不变 ✓
    finally:
        conn.close()
        os.unlink(path)


# ---------------------------------------------------------------------------
# 2. 幂等
# ---------------------------------------------------------------------------
def test_backfill_idempotent_second_run_zero_rows():
    conn, path = _fresh_db()
    try:
        _seed_gap(conn)
        first = backfill_eps_gap(conn)
        assert first.rows_updated == 1
        second = backfill_eps_gap(conn)
        assert second.rows_updated == 0, "二次执行必须 0 行（eps 已非 NULL 不命中）"
    finally:
        conn.close()
        os.unlink(path)


# ---------------------------------------------------------------------------
# 3. 无缺口零行为（纯增益核心：无 NULL 库回补后数据逐字节一致）
# ---------------------------------------------------------------------------
def test_backfill_no_gap_zero_behavior():
    conn, path = _fresh_db()
    try:
        # 无缺口夹具：全部 eps 有值
        conn.execute(
            "INSERT INTO income_statement (code, end_date, ann_date, basic_eps) VALUES "
            "(?, ?, ?, ?)",
            ["000001", _ms("2026-03-31"), _ms("2026-04-20"), 0.67],
        )
        conn.execute(
            "INSERT INTO fin_indicator (code, ann_date, end_date, eps, data_source) VALUES "
            "(?, ?, ?, ?, 'mcp')",
            ["000001", _ms("2026-04-20"), _ms("2026-03-31"), 0.67],
        )
        before = conn.execute(
            "SELECT * FROM fin_indicator ORDER BY code, end_date, ann_date"
        ).fetchdf()
        result = backfill_eps_gap(conn)
        assert result.rows_updated == 0
        after = conn.execute(
            "SELECT * FROM fin_indicator ORDER BY code, end_date, ann_date"
        ).fetchdf()
        pd.testing.assert_frame_equal(before, after)  # 逐字节一致 ✓
    finally:
        conn.close()
        os.unlink(path)


# ---------------------------------------------------------------------------
# 4. PIT 保守：ann_date = max(fi_ann, ic_ann)
# ---------------------------------------------------------------------------
def test_backfill_pit_ann_date_takes_max_and_not_visible_before():
    import duckdb

    conn, path = _fresh_db()
    try:
        # 场景：income 公告更晚（ic_ann > fi_ann）→ ann_date 应取 ic_ann
        conn.execute(
            "INSERT INTO income_statement (code, end_date, ann_date, basic_eps) VALUES (?, ?, ?, ?)",
            ["000063", _ms("2026-03-31"), _ms("2026-05-10"), 0.27],
        )
        conn.execute(
            "INSERT INTO fin_indicator (code, ann_date, end_date, eps, data_source) VALUES "
            "(?, ?, ?, ?, 'mcp')",
            ["000063", _ms("2026-04-25"), _ms("2026-03-31"), None],
        )
        backfill_eps_gap(conn)
        row = conn.execute(
            "SELECT ann_date, eps FROM fin_indicator WHERE code='000063'").fetchone()
        assert row[0] == _ms("2026-05-10")  # ann = max(04-25, 05-10) = 05-10 ✓
        # PIT：回测窗口 05-01（早于 ic_ann）→ 该行 ann_date>窗口 → 不可见（消费层 WHERE ann_date<=T）
        assert _ms("2026-05-01") < row[0]
    finally:
        conn.close()
        os.unlink(path)


# ---------------------------------------------------------------------------
# 5. 打标：backfill_eps_source 正确、data_source 不变
# ---------------------------------------------------------------------------
def test_backfill_marking_and_data_source_untouched():
    conn, path = _fresh_db()
    try:
        _seed_gap(conn)
        backfill_eps_gap(conn)
        rows = conn.execute(
            "SELECT code, backfill_eps_source, data_source FROM fin_indicator ORDER BY code"
        ).fetchall()
        by_code = {r[0]: r for r in rows}
        # 回补行：打标 + data_source 保持 mcp
        assert by_code["000063"][1] == BACKFILL_SOURCE_MARK
        assert by_code["000063"][2] == "mcp"
        # 原生有值行：打标 NULL
        assert by_code["000001"][1] is None
        # 禁区行：打标保留 NULL（未回补）
        assert by_code["001220"][1] is None
    finally:
        conn.close()
        os.unlink(path)


# ---------------------------------------------------------------------------
# 6. 禁区：income 无行 → 保持 NULL
# ---------------------------------------------------------------------------
def test_backfill_ipo_region_stays_null():
    conn, path = _fresh_db()
    try:
        _seed_gap(conn)
        result = backfill_eps_gap(conn)
        # C 码 001220（income 无行）不被回补
        row = conn.execute(
            "SELECT eps, backfill_eps_source FROM fin_indicator WHERE code='001220'").fetchone()
        assert row[0] is None
        assert row[1] is None
        assert "001220" not in result.affected_codes
    finally:
        conn.close()
        os.unlink(path)


# ---------------------------------------------------------------------------
# 7. 多匹配确定性：同 (code,end_date) 多公告版取最新
# ---------------------------------------------------------------------------
def test_backfill_multi_ann_takes_latest_income_version():
    conn, path = _fresh_db()
    try:
        # income 同 (code,end_date) 两个公告版：04-20 basic_eps=1.00（初版）、05-10 basic_eps=1.50（修订）
        conn.execute(
            "INSERT INTO income_statement (code, end_date, ann_date, basic_eps) VALUES (?, ?, ?, ?)",
            ["000063", _ms("2026-03-31"), _ms("2026-04-20"), 1.00],
        )
        conn.execute(
            "INSERT INTO income_statement (code, end_date, ann_date, basic_eps) VALUES (?, ?, ?, ?)",
            ["000063", _ms("2026-03-31"), _ms("2026-05-10"), 1.50],
        )
        conn.execute(
            "INSERT INTO fin_indicator (code, ann_date, end_date, eps, data_source) VALUES "
            "(?, ?, ?, ?, 'mcp')",
            ["000063", _ms("2026-04-25"), _ms("2026-03-31"), None],
        )
        backfill_eps_gap(conn)
        row = conn.execute(
            "SELECT eps, ann_date FROM fin_indicator WHERE code='000063'").fetchone()
        assert row[0] == pytest.approx(1.50)  # 取最新公告版（rn=1）✓
        assert row[1] == _ms("2026-05-10")  # ann = max(04-25, 05-10) ✓
    finally:
        conn.close()
        os.unlink(path)


# ---------------------------------------------------------------------------
# 8. PK 冲突防御（极端场景：ann_date 调整撞已有行 → 逐码回退跳过，不炸整批）
# ---------------------------------------------------------------------------
def test_backfill_pk_conflict_skipped_not_crash():
    conn, path = _fresh_db()
    try:
        conn.execute(
            "INSERT INTO income_statement (code, end_date, ann_date, basic_eps) VALUES (?, ?, ?, ?)",
            ["000063", _ms("2026-03-31"), _ms("2026-05-10"), 0.27],
        )
        conn.execute(
            "INSERT INTO fin_indicator (code, ann_date, end_date, eps, data_source) VALUES "
            "(?, ?, ?, ?, 'mcp')",
            ["000063", _ms("2026-04-25"), _ms("2026-03-31"), None],
        )
        # 已存在 (code,end_date,ann=05-10) 的另一行（eps 有值）→ ann 调整后 PK 冲突
        conn.execute(
            "INSERT INTO fin_indicator (code, ann_date, end_date, eps, data_source) VALUES "
            "(?, ?, ?, ?, 'mcp')",
            ["000063", _ms("2026-05-10"), _ms("2026-03-31"), 0.30],
        )
        result = backfill_eps_gap(conn)  # 不应抛异常
        assert result.rows_updated == 0, "冲突行必须被跳过（逐码回退）"
        assert result.skipped_no_source >= 1, "冲突行计入 skipped（可审计）"
        assert conn.execute("SELECT COUNT(*) FROM fin_indicator").fetchone()[0] == 2  # 无新增行
    finally:
        conn.close()
        os.unlink(path)


# ---------------------------------------------------------------------------
# 9. 门禁 gap 计数
# ---------------------------------------------------------------------------
def test_check_gap_counts_and_closes_after_backfill():
    conn, path = _fresh_db()
    try:
        _seed_gap(conn)
        gap = check_eps_backfill_gap(conn)
        assert gap == 1  # 仅 000063（C 码 income 无行不计）
        backfill_eps_gap(conn)
        assert check_eps_backfill_gap(conn) == 0  # 免疫闭环 ✓
    finally:
        conn.close()
        os.unlink(path)


# ---------------------------------------------------------------------------
# 10. 门禁错误触发（quality_audit EpsBackfillGap）
# ---------------------------------------------------------------------------
def test_quality_audit_eps_backfill_gap_error():
    from quantstudio.pipeline.quality_audit import DataQualityAuditor

    conn, path = _fresh_db()
    try:
        _seed_gap(conn)
        conn.close()
        schemas = {
            "fin_indicator": {
                "columns": {"code": {"type": "str", "required": True},
                            "ann_date": {"type": "int", "required": True},
                            "end_date": {"type": "int", "required": True},
                            "eps": {"type": "float", "required": False}},
                "primary_key": ["code", "end_date", "ann_date"],
            }
        }
        auditor = DataQualityAuditor(path, schemas)
        report = auditor.run()
        issues = [i for i in report.issues if i.check == "EpsBackfillGap"]
        assert len(issues) == 1
        assert issues[0].count == 1
        assert issues[0].severity == "error"
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# 11. diluted_eps 显式排除（income 无列 → 不参与回补）
# ---------------------------------------------------------------------------
def test_diluted_eps_excluded_no_income_column():
    conn, path = _fresh_db()
    try:
        _seed_gap(conn)
        # diluted_eps 缺口不因回补而被填充（income 无 diluted_eps 列）
        conn.execute(
            "UPDATE fin_indicator SET diluted_eps = NULL WHERE code='000063'")
        conn.execute(
            "UPDATE fin_indicator SET diluted_eps = NULL WHERE code='000001'")
        backfill_eps_gap(conn)
        rows = conn.execute(
            "SELECT diluted_eps FROM fin_indicator WHERE code IN ('000063','000001')"
        ).fetchall()
        assert all(r[0] is None for r in rows)  # 显式排除：diluted 保持 NULL ✓
    finally:
        conn.close()
        os.unlink(path)


# ---------------------------------------------------------------------------
# 12. 回补可逆（revert）
# ---------------------------------------------------------------------------
def test_backfill_revert_restores_null():
    conn, path = _fresh_db()
    try:
        _seed_gap(conn)
        backfill_eps_gap(conn)
        assert conn.execute(
            "SELECT eps FROM fin_indicator WHERE code='000063'").fetchone()[0] == pytest.approx(0.27)
        n = revert_backfill(conn)
        assert n == 1
        row = conn.execute(
            "SELECT eps, backfill_eps_source FROM fin_indicator WHERE code='000063'").fetchone()
        assert row[0] is None  # eps 还原 ✓
        assert row[1] is None  # 打标清除 ✓
        # 原生有值行不受 revert 影响
        assert conn.execute(
            "SELECT eps FROM fin_indicator WHERE code='000001'").fetchone()[0] == pytest.approx(0.67)
    finally:
        conn.close()
        os.unlink(path)


# ---------------------------------------------------------------------------
# 13. CLI dry-run 不落库
# ---------------------------------------------------------------------------
def test_cli_dry_run_does_not_apply(tmp_path):
    import subprocess
    import sys

    conn, path = _fresh_db()
    try:
        _seed_gap(conn)
        conn.close()
        # 临时库路径传给 CLI（--db）；backfill 不带 --apply = dry-run
        result = subprocess.run(
            [sys.executable, "scripts/backfill_eps_gap.py",
             "--backfill", "--db", path],
            capture_output=True, text=True, cwd=".", timeout=60)
        assert result.returncode == 0, result.stderr
        assert "dry-run" in result.stdout
        import duckdb
        conn2 = duckdb.connect(path, read_only=True)
        try:
            # 库未被修改：eps 仍为 NULL
            assert conn2.execute(
                "SELECT eps FROM fin_indicator WHERE code='000063'").fetchone()[0] is None
        finally:
            conn2.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# 14. writers.py 写后自动回补 feature gate（P-A3 二期控制点）
# ---------------------------------------------------------------------------

def _writer_backfill_fixture():
    """返回 (writer, path) 并预置 income_statement 0.27 缺口的 fin_indicator 写入夹具。

    调用方负责 close writer + unlink path。写入尚未执行，供 gate 测试分别验证。
    """
    from quantstudio.pipeline.writers import DuckDBWriter

    conn, path = _fresh_db()
    conn.close()
    writer = DuckDBWriter({"path": path})
    wconn = writer._conn()
    try:
        wconn.execute(
            "INSERT INTO income_statement (code, end_date, ann_date, basic_eps, data_source) "
            "VALUES (?, ?, ?, ?, 'mcp')",
            ["000063", _ms("2026-03-31"), _ms("2026-04-25"), 0.27],
        )
    finally:
        wconn.close()

    df = pd.DataFrame({
        "code": ["000063"],
        "ann_date": [_ms("2026-04-25")],
        "end_date": [_ms("2026-03-31")],
        "eps": [None],
        "data_source": ["mcp"],
    })
    return writer, path, df


def _run_writer_with_gate(writer, path, df):
    """执行 writer.write 并捕获全局写锁 skip；失败时清理并抛出。"""
    try:
        writer.write(df, "fin_indicator", "pa3-gate-test")
    except Exception as exc:
        if exc.__class__.__name__ in ("WriteLockHeld", "FileExistsError") or "write_lock" in str(exc):
            writer.close()
            if os.path.exists(path):
                os.unlink(path)
            pytest.skip("SNAP_003 全局写锁持有中（硬约束 A：写路径测试暂缓）")
        writer.close()
        if os.path.exists(path):
            os.unlink(path)
        raise


def _fetch_eps_and_source(writer):
    rconn = writer._conn()
    try:
        return rconn.execute(
            "SELECT eps, backfill_eps_source FROM fin_indicator WHERE code='000063'"
        ).fetchone()
    finally:
        rconn.close()


def test_writer_backfill_gate_default_off(monkeypatch):
    """默认关闭：未设置 QS_AUTO_BACKFILL_EPS 时，writer 不自动回补 eps。"""
    monkeypatch.delenv("QS_AUTO_BACKFILL_EPS", raising=False)

    writer, path, df = _writer_backfill_fixture()
    try:
        _run_writer_with_gate(writer, path, df)
        row = _fetch_eps_and_source(writer)
        assert row[0] is None
        assert row[1] is None
    finally:
        writer.close()
        if os.path.exists(path):
            os.unlink(path)


def test_writer_backfill_gate_explicit_on(monkeypatch):
    """显式开启：QS_AUTO_BACKFILL_EPS=1 时，writer 写 fin_indicator 后自动回补。"""
    monkeypatch.setenv("QS_AUTO_BACKFILL_EPS", "1")

    writer, path, df = _writer_backfill_fixture()
    try:
        _run_writer_with_gate(writer, path, df)
        row = _fetch_eps_and_source(writer)
        assert row[0] == pytest.approx(0.27)
        assert row[1] == BACKFILL_SOURCE_MARK
    finally:
        writer.close()
        if os.path.exists(path):
            os.unlink(path)


@pytest.mark.parametrize("env_value", ["", "0", "false", "False", "off", "OFF", "maybe"])
def test_writer_backfill_gate_fail_closed(monkeypatch, env_value):
    """fail-closed：缺失/0/false/off/任意非法值均不触发 writer 自动回补。"""
    monkeypatch.setenv("QS_AUTO_BACKFILL_EPS", env_value)

    writer, path, df = _writer_backfill_fixture()
    try:
        _run_writer_with_gate(writer, path, df)
        row = _fetch_eps_and_source(writer)
        assert row[0] is None
        assert row[1] is None
    finally:
        writer.close()
        if os.path.exists(path):
            os.unlink(path)


def test_writer_backfill_cli_independent_of_gate(monkeypatch):
    """CLI 独立：writer gate 关闭时，直接调用 backfill_eps_gap(conn) 仍可回补。

    等价于 scripts/backfill_eps_gap.py --apply 的人工独立执行路径。
    """
    monkeypatch.delenv("QS_AUTO_BACKFILL_EPS", raising=False)

    writer, path, df = _writer_backfill_fixture()
    try:
        _run_writer_with_gate(writer, path, df)
        row = _fetch_eps_and_source(writer)
        # writer gate 关闭，eps 未被自动回补
        assert row[0] is None
        assert row[1] is None

        # 模拟 CLI --apply：直接调用回补函数，不受 writer gate 限制
        wconn = writer._conn()
        try:
            result = backfill_eps_gap(wconn)
            assert result.rows_updated == 1
        finally:
            wconn.close()

        row = _fetch_eps_and_source(writer)
        assert row[0] == pytest.approx(0.27)
        assert row[1] == BACKFILL_SOURCE_MARK
    finally:
        writer.close()
        if os.path.exists(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# 15. 防线 3：_latest_by_code 最新有值行（策略 helper 语义）
# ---------------------------------------------------------------------------
def test_latest_by_code_latest_valued_row_semantics():
    # 直接 import 策略源码中的纯函数（与 skill 生成产物同构——用 quantstudio 版）
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "wsg10_mod",
        "quantstudio/backtest/strategies/weekly_smallcap_growth_momentum_10_quantstudio.py",
    )
    # 该策略文件在 import 时会执行模块顶层（含 initialize 等但无副作用 run）——
    # 避免副作用：仅用 ast 提取 _latest_by_code 源码后 exec 在一个隔离命名空间。
    import ast

    src = open(
        "quantstudio/backtest/strategies/weekly_smallcap_growth_momentum_10_quantstudio.py",
        encoding="utf-8").read()
    tree = ast.parse(src)
    fn_node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "_latest_by_code")
    mod_ast = ast.Module(body=[fn_node], type_ignores=[])
    ns = {"np": __import__("numpy"), "pd": pd}
    exec(compile(mod_ast, "<latest_by_code>", "exec"), ns)
    latest_by_code = ns["_latest_by_code"]

    # 构造：码 A 最新行 NULL（回退到上一有值期）；码 B 最新行有值
    import numpy as np
    df = pd.DataFrame(
        {
            "code": ["A", "A", "B", "B"],
            "end_date": [20250331, 20241231, 20250331, 20241231],
            "publ_date": [20260425, 20250420, 20260425, 20250420],
            "value": [np.nan, 0.5, 1.5, 1.2],
        }
    ).set_index("code")
    result = latest_by_code(df, "value")
    assert result["A"] == pytest.approx(0.5)  # NULL 最新行回退到上一有值期 ✓
    assert result["B"] == pytest.approx(1.5)  # 最新行有值不变 ✓
    # 纯 NULL 输入 → 空 dict（无任何有值期的码被剔除）
    df2 = pd.DataFrame(
        {"code": ["C"], "end_date": [20250331], "publ_date": [20260425], "value": [np.nan]}
    ).set_index("code")
    assert latest_by_code(df2, "value") == {}


# ---------------------------------------------------------------------------
# 附加：dry_run 参数不落库（API 级）
# ---------------------------------------------------------------------------
def test_backfill_dry_run_api_no_apply():
    conn, path = _fresh_db()
    try:
        _seed_gap(conn)
        result = backfill_eps_gap(conn, dry_run=True)
        assert result.rows_updated == 1  # dry-run 统计到 1 行
        row = conn.execute(
            "SELECT eps FROM fin_indicator WHERE code='000063'").fetchone()
        assert row[0] is None  # 但未落库 ✓
    finally:
        conn.close()
        os.unlink(path)


# ---------------------------------------------------------------------------
# 16. 门禁降级：income_statement 表缺失时 check 跳过而非报错（方案 §3 防线 2 承诺）
# ---------------------------------------------------------------------------
def test_check_gap_missing_source_table_skips_not_crash():
    import duckdb

    path = _make_temp_db()
    conn = duckdb.connect(path)
    try:
        # 只有 fin_indicator，无 income_statement（源表缺失场景）
        conn.execute("""
            CREATE TABLE fin_indicator (
                code VARCHAR, ann_date BIGINT, end_date BIGINT,
                eps DOUBLE, data_source VARCHAR,
                backfill_eps_source VARCHAR,
                PRIMARY KEY(code, end_date, ann_date)
            )
        """)
        conn.execute(
            "INSERT INTO fin_indicator (code, ann_date, end_date, eps, data_source) VALUES "
            "(?, ?, ?, ?, 'mcp')",
            ["000063", _ms("2026-04-25"), _ms("2026-03-31"), None],
        )
        gap = check_eps_backfill_gap(conn)
        assert gap == 0  # 源表缺失 → 跳过（不报错、不假报 gap）✓
    finally:
        conn.close()
        os.unlink(path)