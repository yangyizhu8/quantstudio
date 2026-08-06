"""任务2.4：stock_dividend payload_hash 全量覆盖（自包含 fixture，不依赖外部数据/网络）。

覆盖：
- 新 schema（含 ann_date/end_date/cash_div_before_tax/cash_div_after_tax/stk_bo_rate/
  stk_co_rate 全字段）→ payload_hash 与磁盘真实算法一致（13 字段 + _norm_div_val）。
- 旧 schema（缺列）→ 缺列按 NULL → _norm_div_val(None)="" 参与哈希，不阻断扫描。
- NULL / NaN 经 _norm_div_val 规范化为 ""。
- 仅 update_time 变化 → 不产生新 trigger（update_time 不进 hash）。
- 业务字段 revision（cash_div 0.5 → 0.6）→ payload_hash 变 → trigger_id 变 → 新 trigger。
  （ann_date/end_date 规范化按磁盘真实算法：int(...) if not None else None）
"""

import duckdb
import pytest

from quantstudio.pipeline.qfq_event_discovery import _norm_div_val
from quantstudio.pipeline.qfq_orchestrator_types import (
    QFQOrchestratorConfig,
    payload_hash_of,
    trigger_id_of,
)
from quantstudio.pipeline.qfq_event_discovery import EventDiscovery
from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema


@pytest.fixture
def env(tmp_path):
    aux = tmp_path / "qfq_aux.db"
    dconn = duckdb.connect(":memory:")
    init_duckdb_schema(dconn)
    cfg = QFQOrchestratorConfig()
    disc = EventDiscovery(cfg, aux_db=str(aux))
    yield dconn, disc
    dconn.close()


def _make_dividend(dconn, columns):
    dconn.execute("DROP TABLE IF EXISTS stock_dividend")
    cols = ", ".join(f"{c} VARCHAR" for c in columns)
    dconn.execute(f"CREATE TABLE stock_dividend ({cols})")


def _insert_dividend(dconn, **kwargs):
    cols = list(kwargs.keys())
    placeholders = ", ".join("?" for _ in cols)
    dconn.execute(
        f"INSERT INTO stock_dividend ({', '.join(cols)}) VALUES ({placeholders})",
        [kwargs[c] for c in cols],
    )


def _expected_dividend_hash(
    code, ex_date, record_date, ann_date, end_date,
    cbt, cat, cd, sd, sbr, scr, dr, dp,
):
    """复刻扫描阶段的真实 13 字段算法（与磁盘一致）。"""
    return payload_hash_of([
        code, int(ex_date), record_date,
        int(ann_date) if ann_date is not None else None,
        int(end_date) if end_date is not None else None,
        _norm_div_val(cbt),
        _norm_div_val(cat),
        _norm_div_val(cd),
        _norm_div_val(sd),
        _norm_div_val(sbr),
        _norm_div_val(scr),
        _norm_div_val(dr),
        _norm_div_val(dp),
    ])


def _trigger_hashes(dconn):
    return dconn.execute(
        "SELECT trigger_id, payload_hash, code FROM qfq_trigger_queue"
    ).fetchall()


def test_new_schema_full_fields_hash_matches_disk_algorithm(env):
    dconn, disc = env
    columns = [
        "code", "ex_date", "record_date", "ann_date", "end_date",
        "cash_div_before_tax", "cash_div_after_tax", "cash_div",
        "stk_div", "stk_bo_rate", "stk_co_rate", "div_rat", "div_proc",
    ]
    _make_dividend(dconn, columns)
    _insert_dividend(
        dconn,
        code="600000", ex_date="20260108", record_date="20260107",
        ann_date="20251220", end_date="20260107",
        cash_div_before_tax="0.50", cash_div_after_tax="0.45", cash_div="0.50",
        stk_div="", stk_bo_rate="", stk_co_rate="", div_rat="", div_proc="实施",
    )
    disc.scan_stock_dividend(dconn, as_of_ms=9_999_999_999_999, run_id="r1")

    expected = _expected_dividend_hash(
        "600000", "20260108", "20260107", "20251220", "20260107",
        "0.50", "0.45", "0.50", "", "", "", "", "实施",
    )
    rows = _trigger_hashes(dconn)
    assert len(rows) == 1
    _, payload_hash, code = rows[0]
    assert payload_hash == expected
    # 磁盘 trigger_id 算法一致
    assert rows[0][0] == trigger_id_of("STOCK", code, 20260108, "stock_dividend", expected)


def test_old_schema_missing_columns_null_normalized(env):
    dconn, disc = env
    # 旧 schema：仅 code/ex_date/record_date/cash_div/div_proc，缺其余 8 列
    columns = ["code", "ex_date", "record_date", "cash_div", "div_proc"]
    _make_dividend(dconn, columns)
    _insert_dividend(
        dconn,
        code="600000", ex_date="20260108", record_date="20260107",
        cash_div="0.50", div_proc="实施",
    )
    # 缺列不阻断扫描（不抛异常）
    disc.scan_stock_dividend(dconn, as_of_ms=9_999_999_999_999, run_id="r1")

    # 旧 schema 仅 cash_div 有值（位置=cd，即第 8 个业务列）；其余缺列按 NULL → "" 参与哈希
    expected = _expected_dividend_hash(
        "600000", "20260108", "20260107", None, None,
        None, None, "0.50", None, None, None, None, "实施",
    )
    rows = _trigger_hashes(dconn)
    assert len(rows) == 1
    assert rows[0][1] == expected


def test_norm_div_val_normalizes_null_and_nan():
    assert _norm_div_val(None) == ""
    assert _norm_div_val("") == ""
    assert _norm_div_val(float("nan")) == ""
    assert _norm_div_val("0.5") == "0.5"
    assert _norm_div_val("0.50") == "0.50"
    assert _norm_div_val(1.0) == "1.0"


def test_only_update_time_change_no_new_trigger(env):
    dconn, disc = env
    columns = [
        "code", "ex_date", "record_date", "cash_div", "div_proc", "update_time",
    ]
    _make_dividend(dconn, columns)
    # 业务字段完全相同，仅 update_time 不同
    _insert_dividend(
        dconn, code="600000", ex_date="20260108", record_date="20260107",
        cash_div="0.50", div_proc="实施", update_time="2026-01-01 09:00:00",
    )
    _insert_dividend(
        dconn, code="600000", ex_date="20260108", record_date="20260107",
        cash_div="0.50", div_proc="实施", update_time="2026-01-02 09:00:00",
    )
    disc.scan_stock_dividend(dconn, as_of_ms=9_999_999_999_999, run_id="r1")

    # update_time 不进 hash → 同一 trigger_id（同一 payload_hash）→ 仅 1 条
    rows = _trigger_hashes(dconn)
    assert len(rows) == 1


def test_business_revision_changes_trigger_id(env):
    dconn, disc = env
    columns = [
        "code", "ex_date", "record_date", "cash_div", "div_proc",
    ]
    _make_dividend(dconn, columns)
    # 批次1：cash_div 0.5
    _insert_dividend(
        dconn, code="600000", ex_date="20260108", record_date="20260107",
        cash_div="0.5", div_proc="实施",
    )
    disc.scan_stock_dividend(dconn, as_of_ms=9_999_999_999_999, run_id="r1")

    # 批次2：同 code/ex_date，cash_div 0.6（业务字段修订）→ 新 trigger_id
    _insert_dividend(
        dconn, code="600000", ex_date="20260108", record_date="20260107",
        cash_div="0.6", div_proc="实施",
    )
    disc.scan_stock_dividend(dconn, as_of_ms=9_999_999_999_999, run_id="r2")

    rows = _trigger_hashes(dconn)
    assert len(rows) == 2
    hashes = {r[1] for r in rows}
    assert len(hashes) == 2  # payload_hash 不同 → 两个不同 trigger


# ---------------- v2.4 B-1：旧手写算法 vs 新共享函数黄金一致性 ----------------

from quantstudio.pipeline.qfq_dividend_payload import (
    dividend_payload_hash as _shared_hash, norm_div_val,
)


def _legacy_handwritten_hash(code, ex_date, record_date, ann_date, end_date,
                              cash_div_before_tax, cash_div_after_tax, cash_div,
                              stk_div, stk_bo_rate, stk_co_rate, div_rat, div_proc):
    """复刻 scan_stock_dividend 旧手写算法（payload_hash_of + _norm_div_val 13 字段），
    作为黄金基准，与新共享函数逐项比对。"""
    return payload_hash_of([
        code, ex_date, record_date,
        int(ann_date) if ann_date is not None else None,
        int(end_date) if end_date is not None else None,
        norm_div_val(cash_div_before_tax),
        norm_div_val(cash_div_after_tax),
        norm_div_val(cash_div),
        norm_div_val(stk_div),
        norm_div_val(stk_bo_rate),
        norm_div_val(stk_co_rate),
        norm_div_val(div_rat),
        norm_div_val(div_proc),
    ])


@pytest.mark.parametrize("label,kwargs", [
    ("全字段正常", dict(code="600000", ex_date=20260108, record_date="20260107",
                        ann_date="20260105", end_date="20251231",
                        cash_div_before_tax="0.5", cash_div_after_tax="0.425",
                        cash_div="0.5", stk_div="0", stk_bo_rate="0", stk_co_rate="0",
                        div_rat="1.0", div_proc="实施")),
    ("None/NaN 规范化", dict(code="000001", ex_date=20260108, record_date=None,
                             ann_date=None, end_date=None,
                             cash_div_before_tax=None, cash_div_after_tax=float("nan"),
                             cash_div="", stk_div=None, stk_bo_rate=None, stk_co_rate=None,
                             div_rat=None, div_proc="实施")),
    ("字符串数字 strip", dict(code="510050", ex_date=20260108, record_date=" 20260107 ",
                              ann_date=" 20260105 ", end_date=" 20251231 ",
                              cash_div_before_tax=" 0.5 ", cash_div_after_tax=" 0.425 ",
                              cash_div=" 0.5 ", stk_div=" 0 ", stk_bo_rate=" 0 ",
                              stk_co_rate=" 0 ", div_rat=" 1.0 ", div_proc=" 实施 ")),
])
def test_shared_hash_equals_legacy_handwritten(label, kwargs):
    """新共享函数与旧手写算法对相同输入产生相同 hash（防口径漂移）。"""
    legacy = _legacy_handwritten_hash(**kwargs)
    shared = _shared_hash(**kwargs)
    assert legacy == shared, f"[{label}] hash 不一致: legacy={legacy} shared={shared}"


def test_field_order_matters():
    """字段顺序敏感：交换 cash_div_before_tax 与 cash_div_after_tax 应改变 hash。"""
    base = dict(code="600000", ex_date=20260108, record_date=None, ann_date=None,
                end_date=None, cash_div="0.5", stk_div="0", stk_bo_rate="0",
                stk_co_rate="0", div_rat="1.0", div_proc="实施")
    h1 = _shared_hash(cash_div_before_tax="0.5", cash_div_after_tax="0.425", **base)
    h2 = _shared_hash(cash_div_before_tax="0.425", cash_div_after_tax="0.5", **base)
    assert h1 != h2


def test_update_time_not_in_hash():
    """update_time 不进 hash（非业务字段）—— 共享函数签名不含 update_time。"""
    import inspect
    sig = inspect.signature(_shared_hash)
    assert "update_time" not in sig.parameters


def test_no_circular_import():
    """确认无循环 import：qfq_dividend_payload 不反向依赖 qfq_event_discovery。

    检查 import 语句（非全文文本，因 docstring 描述来源会提到该名）。
    """
    import ast
    import quantstudio.pipeline.qfq_dividend_payload as mod
    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            for n in node.names:
                imported.add(n.name)
    assert not any("qfq_event_discovery" in m for m in imported), \
        f"qfq_dividend_payload 不得 import qfq_event_discovery（循环依赖），实际 import: {imported}"
    # 唯一允许的上游依赖是 qfq_orchestrator_types（底层）
    qfq_deps = {m for m in imported if m.startswith("quantstudio.pipeline.qfq")}
    assert qfq_deps == {"quantstudio.pipeline.qfq_orchestrator_types"}, \
        f"qfq_dividend_payload 只允许依赖 qfq_orchestrator_types，实际: {qfq_deps}"
