# -*- coding: utf-8 -*-
"""D1 / a0 / D2 复验固件 —— 客户运维线 2026-10-16 六项复验（13 断言 + 2 负向对照）

来源：客户运维会话 2026-10-16 复验（自建隔离夹具，实测 13/13 PASS）
底稿：docs/handoff/d1-a0-d2-fixture-draft-20261016.py（总调度裁：与其余修复同批入库，避免双写）
入库：dev 线队列 1.2「条件 C」，2026-10-17

运行前置（重要）：**无需生产 DB / 无需 daemon** —— 全部使用临时 sqlite 与内存 DataFrame。

覆盖范围（本件是 D1/a0/D2 的**首次自动化固件**）：
  D1  五态：默认阈值=1% / 整列缺失硬 fail / ≤阈值跳过+登记 / >阈值硬 fail / 阈值=0 回退严格
  a0  两态：整码全缺登记 / 告警升级（不得被全局占比吸收）
  D2  六态：异常层级 / 顶格抛背压 / 消息含人工腾退指引 / 未顶格返回行数不变 /
              空批返回 0 / 负向对照：架空背压复现旧静默丢弃

D1-补1（2026-10-17 已实施）：隔离登记落盘到**新增独立表** qfq_d1_isolation_log
（qfq_aux.db；一码一行全量不截断，客户免库窗只读自查；方案件
docs/d1-isolation-log-design-20261017.md）。本文件尾部 6 条 test_d1_plus1_* 即其
验收固件；模块级 autouse 夹具 _tmp_aux_db 把落盘隔离到临时库（禁写生产
data/qfq_aux.db，含上方 13 条固件中触达隔离分支的用例）。
未闭合：口径文字更正（默认 1% 阈值 ⇒ 行为有意变化）—— 不属测试面。
"""
import os
import sqlite3
import tempfile

import pandas as pd
import pytest

from quantstudio.pipeline.quarantine import Quarantine, QuarantineFullError
from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter

ENV_RATIO = "QS_RESTORE_MISSING_FACTOR_MAX_RATIO"


# --------------------------------------------------------------------------- #
# 公共夹具
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolate_ratio_env(monkeypatch):
    """隔离阈值环境变量：确保「默认语义」用例不被外部 env 干扰。"""
    monkeypatch.delenv(ENV_RATIO, raising=False)


@pytest.fixture(autouse=True)
def _tmp_aux_db(tmp_path, monkeypatch):
    """D1-补1 落盘隔离（模块级 autouse）：落盘/查询一律走临时 qfq_aux.db。

    细化稿偏离①（裁定见文件尾 D1-补1 节注）：细化稿 AUX_REL 指「data/qfq_aux.db」
    生产库，而触达 D1 隔离分支的用例经 _restore_to_raw 会**写入**该库（把测试码
    999999/888888 写进客户可见的生产审计表）⇒ 改为 DATA_ROOT 重定向 tmp_path +
    init_sqlite_schema 建表；_aux_ro() 仍用 file:...?mode=ro 只读 URI 打开
    （保留「免库窗只读」语义）。「表不存在」失败面由用例 5 自行注入。
    """
    import quantstudio._paths as qs_paths
    from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema
    monkeypatch.setattr(qs_paths, "DATA_ROOT", tmp_path)
    aux = tmp_path / "qfq_aux.db"
    conn = sqlite3.connect(str(aux))
    try:
        init_sqlite_schema(conn)
        conn.commit()
        # DDL↔契约回读自检（照 test_qfq_reanchor_batch1.py 先例）：把「新表 DDL
        # 解析畸形/列类型/PK 顺序不符」从静默隐患变成夹具期响亮失败。
        from quantstudio.pipeline.qfq_reanchor_schema import _verify_sqlite_contract
        _verify_sqlite_contract(conn)
    finally:
        conn.close()
    return aux


def _mk_adapter(**cfg):
    base = {"enable_qfq_restore": True, "price_source": "mcp"}
    base.update(cfg)
    return MCPAdapter(base)


def _mk_df(nrows, bad_codes=(), good_code="000001"):
    """构造 stock_minutes/1min 形状的 DataFrame；bad_codes 里的 code 无因子。

    注意（历史教训）：good_code 必须**有因子历史**，且坏占比须在阈值内，
    否则会误触发 >阈值的硬 fail 分支而使夹具失真（此前三次夹具错误即源于此）。
    本件经由 adj_latest_map 直接注入因子映射，绕开 DB 查询，故 good_code 恒有因子。
    """
    rows = []
    for i in range(nrows):
        c = bad_codes[i] if i < len(bad_codes) else good_code
        rows.append({"ts_code": c, "adj_factor": 1.0, "open": 10.0,
                     "high": 11.0, "low": 9.0, "close": 10.5})
    return pd.DataFrame(rows)


def _amap_without(df, missing):
    """注入因子映射（模拟 qfq_aux 快照）：missing 中的 code 无因子。"""
    miss = set(missing)
    return {c: 1.0 for c in df["ts_code"].unique() if c not in miss}


def _restore(adapter, df, amap):
    return adapter._restore_to_raw(df, "stock_minutes", "1min", adj_latest_map=amap)


# --------------------------------------------------------------------------- #
# D1 · 三态判据（整列缺失 / ≤阈值 / >阈值）
# --------------------------------------------------------------------------- #
def test_d1_default_ratio_is_one_percent():
    """默认阈值 = 1%（裁①）——默认即阈值模式，非 strict（口径文字已按此更正）。"""
    assert MCPAdapter({"price_source": "mcp"}).restore_missing_factor_max_ratio == 0.01


def test_d1_missing_adj_factor_column_hard_fail():
    """整列缺失（云端契约变更）⇒ 保持硬 fail。"""
    df = _mk_df(10).drop(columns=["adj_factor"])
    with pytest.raises(ValueError):
        _restore(_mk_adapter(), df, {})


def test_d1_within_ratio_skips_bad_rows_and_registers():
    """占比 ≤ 阈值 ⇒ 按行隔离跳过 + 登记（不 raise）。"""
    df = _mk_df(100)
    df.loc[0, "ts_code"] = "999999"
    _, meta = _restore(_mk_adapter(), df, _amap_without(df, ["999999"]))
    assert meta["skipped_rows_no_factor"] == 1
    assert meta["missing_factor_codes"] == ["999999"]


def test_d1_over_ratio_hard_fail():
    """占比 > 阈值 ⇒ 硬 fail（安全网保留）。"""
    bad = ["9%05d" % i for i in range(5)]
    df = _mk_df(100, bad_codes=bad)
    with pytest.raises(ValueError):
        _restore(_mk_adapter(), df, _amap_without(df, bad))


def test_d1_rollback_ratio_zero_restores_strict():
    """回退：阈值置 0 ⇒ 回到旧严格语义（情形 2 应 raise）。"""
    df = _mk_df(100)
    df.loc[0, "ts_code"] = "999999"
    with pytest.raises(ValueError):
        _restore(_mk_adapter(restore_missing_factor_max_ratio=0), df,
                 _amap_without(df, ["999999"]))


# --------------------------------------------------------------------------- #
# a0 · 整码全缺不得被全局占比吸收
# --------------------------------------------------------------------------- #
def test_a0_whole_code_missing_registered(caplog):
    """整码全缺（1 code × 10 行 / 共 1000 行 = 1% ≤ 阈值）⇒ 不 raise + 明示登记。"""
    import logging
    caplog.set_level(logging.ERROR, logger="quantstudio.pipeline.sources.mcp_adapter")
    df = _mk_df(1000, bad_codes=["888888"] * 10)
    _, meta = _restore(_mk_adapter(), df, _amap_without(df, ["888888"]))
    assert meta["whole_code_missing"] == ["888888"]
    assert meta["whole_code_missing_count"] == 1


def test_a0_whole_code_missing_escalates_to_error(caplog):
    """告警升级：logger.error 且消息含「整码全缺」+「不得被全局占比阈值吸收」。"""
    import logging
    caplog.set_level(logging.ERROR, logger="quantstudio.pipeline.sources.mcp_adapter")
    df = _mk_df(1000, bad_codes=["888888"] * 10)
    _restore(_mk_adapter(), df, _amap_without(df, ["888888"]))
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "整码全缺" in msgs
    assert "不得被全局占比阈值吸收" in msgs


# --------------------------------------------------------------------------- #
# D2 · 隔离区顶格背压（临时库，不触生产 DB）
# --------------------------------------------------------------------------- #
def _mk_quarantine(max_rows, fill=0):
    path = os.path.join(tempfile.mkdtemp(prefix="qs_d2_"), "q.db")
    q = Quarantine(db_path=path, max_rows=max_rows)
    for i in range(fill):
        Quarantine.write(q, batch_id="fill%d" % i, table="t", source="s",
                         rows=[{"a": i}], failed_rules=["UnitCheck"])
    return q


def test_d2_exception_is_runtimeerror():
    assert issubclass(QuarantineFullError, RuntimeError)


def test_d2_full_cap_raises_backpressure():
    """顶格 ⇒ 抛背压信号（不再静默 return 0）。"""
    q = _mk_quarantine(3, fill=3)
    with pytest.raises(QuarantineFullError):
        Quarantine.write(q, batch_id="B1", table="stock_minutes", source="mcp",
                         rows=[{"a": 1}, {"a": 2}], failed_rules=["UnitCheck"])


def test_d2_full_cap_message_contains_manual_release_guidance():
    q = _mk_quarantine(3, fill=3)
    with pytest.raises(QuarantineFullError) as ei:
        Quarantine.write(q, batch_id="B1", table="stock_minutes", source="mcp",
                         rows=[{"a": 1}], failed_rules=["UnitCheck"])
    msg = str(ei.value)
    assert "人工腾退" in msg
    assert ("archive_expired" in msg) or ("max_rows" in msg)


def test_d2_below_cap_returns_written_rows_unchanged():
    q = _mk_quarantine(1000)
    assert Quarantine.write(q, batch_id="B2", table="stock_minutes", source="mcp",
                            rows=[{"a": 1}, {"a": 2}, {"a": 3}],
                            failed_rules=["UnitCheck"]) == 3


def test_d2_empty_batch_returns_zero():
    q = _mk_quarantine(1000, fill=2)
    assert Quarantine.write(q, batch_id="B3", table="t", source="s",
                            rows=[], failed_rules=["UnitCheck"]) == 0


def test_d2_negative_control_legacy_silent_drop(monkeypatch):
    """负向对照：架空背压（旧实现 return 0）⇒ 静默丢弃可复现 ⇒ 证明防线真实生效。"""
    q = _mk_quarantine(3, fill=3)
    monkeypatch.setattr(Quarantine, "write", lambda self, *a, **k: 0)
    assert Quarantine.write(q, batch_id="B4", table="t", source="s",
                            rows=[{"a": 9}], failed_rules=["UnitCheck"]) == 0


# --------------------------------------------------------------------------- #
# D1-补1 · 隔离登记落盘 qfq_d1_isolation_log（独立表 · 免库窗只读自查）
# 细化稿：docs/handoff/d1-plus1-skipcase-refined-20261017.py（6 用例）
# 我方裁定的两处细化稿偏离：
#   1) 临时库隔离：细化稿 AUX_REL 指「data/qfq_aux.db」**生产库**，而本组用例经
#      _restore_to_raw 会写入该库 ⇒ 会把测试码 999999/888888 写进客户可见的
#      生产审计表。改为 DATA_ROOT 重定向 tmp_path + init_sqlite_schema 建表
#      （见模块级 autouse 夹具 _tmp_aux_db）；_aux_ro() 仍用 file:...?mode=ro
#      只读 URI（保留「免库窗只读」语义）。
#   2) 用例 1 夹具修正：细化稿「3 bad 行 / 100 行 = 3%」> 默认阈值 1% ⇒ 会误触
#      硬 fail（其自头注亦警告此坑）；且该码全部行皆 bad ⇒ 按 §5.3 分类实为
#      whole_code_missing 而非 row_skipped。改为 1000 行中该码 10 行、其中 3 行
#      adj_factor=NaN（该码在 amap **有**因子）⇒ 真正的「部分行按行跳过」：
#      3/1000 = 0.3% ≤ 1%，kind='row_skipped'。
# --------------------------------------------------------------------------- #
TABLE = "qfq_d1_isolation_log"


def _aux_ro(aux_path):
    """免库窗读取：只读 URI 连接 qfq_aux.db（与方案 §4.1 客户口径同款）。"""
    uri = "file:%s?mode=ro" % str(aux_path).replace("\\", "/")
    return sqlite3.connect(uri, uri=True)


# --------------------------------------------------------------------------- #
# 1 · 按行跳过：skipped_rows 列承载行数（原「行数 vs 逐 code 粒度」语义点就此消解）
# --------------------------------------------------------------------------- #
def test_d1_plus1_row_skipped_carries_skipped_rows(_tmp_aux_db):
    """3 行/1 码按行跳过 ⇒ 该码一行 kind='row_skipped'，skipped_rows 与 meta 一致。

    夹具修正见上方节注偏离②：1000 行中 999999 共 10 行、3 行 adj_factor=NaN，
    该码在 amap 有因子 ⇒ 非整码全缺，kind 才是 row_skipped。
    """
    df = _mk_df(1000)
    _idx = list(range(10))
    df.loc[_idx, "ts_code"] = "999999"
    df.loc[_idx[:3], "adj_factor"] = float("nan")
    _, meta = _mk_adapter()._restore_to_raw(df, "stock_minutes", "1min",
                                            adj_latest_map=_amap_without(df, []))
    assert meta["skipped_rows_no_factor"] == 3
    assert meta["missing_factor_codes"] == ["999999"]
    with _aux_ro(_tmp_aux_db) as c:
        row = c.execute(
            "SELECT kind, skipped_rows, whole_code_missing, max_ratio, total_rows "
            "FROM %s WHERE code=? AND kind='row_skipped'" % TABLE, ("999999",)).fetchone()
    assert row is not None, "按行跳过未登记"
    kind, skipped_rows, whole_flag, max_ratio, total_rows = row
    assert kind == "row_skipped"
    assert skipped_rows == meta["skipped_rows_no_factor"] == 3
    assert whole_flag == 0
    assert max_ratio == 0.01
    assert total_rows == 1000


# --------------------------------------------------------------------------- #
# 2 · 整码全缺：逐码一行 + 计数与 meta 一致
# --------------------------------------------------------------------------- #
def test_d1_plus1_whole_code_kind_and_count(_tmp_aux_db):
    df = _mk_df(1000, bad_codes=["888888"] * 10)
    _, meta = _mk_adapter()._restore_to_raw(df, "stock_minutes", "1min",
                                            adj_latest_map=_amap_without(df, ["888888"]))
    with _aux_ro(_tmp_aux_db) as c:
        n = c.execute("SELECT COUNT(*) FROM %s WHERE kind='whole_code_missing'" % TABLE).fetchone()[0]
        row = c.execute("SELECT whole_code_missing FROM %s WHERE code=?" % TABLE,
                        ("888888",)).fetchone()
    assert n == meta["whole_code_missing_count"] == 1
    assert row is not None and row[0] == 1


# --------------------------------------------------------------------------- #
# 3 · ★ 51 码无截断机检（对接原 51-code 断言；列为 listed_codes / truncated）
# --------------------------------------------------------------------------- #
def test_d1_plus1_51_codes_listed_full_and_not_truncated(_tmp_aux_db):
    """51 个整码全缺码 ⇒ 行集/listed_codes/missing_codes_total = 51，truncated=0；
    meta 侧列表仍截断为 50（对照）。"""
    bad = ["%06d" % (900000 + i) for i in range(51)]
    df = _mk_df(2000, bad_codes=bad)
    # 51/2000=2.55% > 默认 1% ⇒ 显式放宽阈值以进入隔离分支
    adapter = _mk_adapter(restore_missing_factor_max_ratio=0.05)
    _, meta = adapter._restore_to_raw(df, "stock_minutes", "1min",
                                      adj_latest_map=_amap_without(df, bad))
    with _aux_ro(_tmp_aux_db) as c:
        n_rows = c.execute("SELECT COUNT(*) FROM %s WHERE kind='whole_code_missing'" % TABLE).fetchone()[0]
        agg = c.execute("SELECT MAX(listed_codes), MAX(missing_codes_total), MAX(truncated), "
                        "MAX(total_rows) FROM %s" % TABLE).fetchone()
    listed, total_codes, trunc, total_rows = agg
    assert n_rows == 51
    assert listed == 51 and total_codes == 51
    assert trunc == 0
    assert total_rows == 2000
    assert len(meta["whole_code_missing"]) == 50          # meta 侧截断（对照，不变）
    assert meta["whole_code_missing_count"] == 51         # meta 计数为完整值


# --------------------------------------------------------------------------- #
# 4 · hit_count：同日同码再次命中 ⇒ +1（★DDL 设计「防最后写胜覆盖」）
# --------------------------------------------------------------------------- #
def test_d1_plus1_hit_count_increments_on_repeat(_tmp_aux_db):
    df = _mk_df(1000, bad_codes=["888888"] * 10)
    a = _mk_adapter()
    amap = _amap_without(df, ["888888"])
    a._restore_to_raw(df, "stock_minutes", "1min", adj_latest_map=amap)
    a._restore_to_raw(df, "stock_minutes", "1min", adj_latest_map=amap)
    with _aux_ro(_tmp_aux_db) as c:
        hit, upd = c.execute("SELECT hit_count, updated_at FROM %s WHERE code=?" % TABLE,
                             ("888888",)).fetchone()
    assert hit == 2
    assert upd


# --------------------------------------------------------------------------- #
# 5 · fail-soft：表不存在（§5 失败面 1）⇒ 不抛 + ERROR；允许 return False 但禁静默
# --------------------------------------------------------------------------- #
def test_d1_plus1_fail_soft_table_absent_but_error_logged(monkeypatch, caplog):
    """DEV-FILL 落实：注入点 = qfq_unit_diag._aux_path（§6 独立函数复用它）。

    断言（§5 义务①②）：
      · 落盘失败**不向取数路径抛出**（主流程完成、不中断本批）；
      · 必须有 **ERROR 审计行**；**允许 return False，但禁止「无 ERROR 的 False」**。
    断言收紧到 qfq_unit_diag 记录器：a0 整码全缺本身恒有 ERROR（mcp_adapter 侧），
    不收紧则无法证明「落盘失败的 ERROR」真实存在。
    """
    import logging
    import tempfile
    from quantstudio.pipeline import qfq_unit_diag as diag

    empty = os.path.join(tempfile.mkdtemp(prefix="qs_empty_aux_"), "empty.db")
    monkeypatch.setattr(diag, "_aux_path", lambda: empty, raising=False)
    caplog.set_level(logging.ERROR)

    df = _mk_df(1000, bad_codes=["888888"] * 10)
    _, meta = _mk_adapter()._restore_to_raw(df, "stock_minutes", "1min",
                                            adj_latest_map=_amap_without(df, ["888888"]))
    assert meta["restore_isolated"] is True      # 取数不抛、隔离照常完成（义务①）
    assert any(r.levelno >= logging.ERROR
               and r.name == "quantstudio.pipeline.qfq_unit_diag"
               for r in caplog.records), "静默失败（落盘失败无 ERROR 审计行）"


# --------------------------------------------------------------------------- #
# 6 · 客户自查口径可用性机检：§4.1 的 Q1/Q2/Q3 在只读连接上可执行
# --------------------------------------------------------------------------- #
def test_d1_plus1_customer_query_contract_runnable_ro(_tmp_aux_db):
    """把「客户可直接跑」变成可测项：§4.1 三条查询在只读连接上均可执行。"""
    # 注：SQL 内 strftime 的 % 需对外层 % TABLE 转义为 %%（细化稿原稿未转义，
    # 会 TypeError: not enough arguments for format string——移植修正）。
    q1 = ("SELECT code, kind, whole_code_missing, skipped_rows, hit_count, updated_at "
          "FROM %s WHERE round_no = CAST(strftime('%%Y%%m%%d','now','+8 hours') AS INTEGER) "
          "ORDER BY whole_code_missing DESC, skipped_rows DESC" % TABLE)
    q2 = ("SELECT round_no, listed_codes, missing_codes_total, truncated "
          "FROM %s WHERE truncated = 1 GROUP BY round_no" % TABLE)
    q3 = ("SELECT table_name, freq, total_rows, missing_ratio, max_ratio, truncated "
          "FROM %s WHERE round_no = CAST(strftime('%%Y%%m%%d','now','+8 hours') AS INTEGER) "
          "LIMIT 1" % TABLE)
    with _aux_ro(_tmp_aux_db) as c:
        for q in (q1, q2, q3):
            c.execute(q).fetchall()


# --------------------------------------------------------------------------- #
# 7 · ★ 判据②闭合（②审 2026-10-17 裁定）：部分写失败 ⇒ listed_codes=实际落盘数、truncated==1
# --------------------------------------------------------------------------- #
def test_d1_plus1_partial_write_failure_sets_truncated(_tmp_aux_db, monkeypatch):
    """部分行写失败 ⇒ `truncated` **写时计算**为 1，`listed_codes` 校正为实际成功落盘数。

    背景：原实现「全量落盘无上限」使 `truncated` **恒为死列**（恒 0）。②审裁定按
    方案 §4 列语义（`listed < missing_codes_total` ⇒ 置 1）改为写时计算——修的是
    **落地方案自己的契约**而非改契约。本用例即该列的机检（判据②就此完整闭合）。
    """
    from quantstudio.pipeline import qfq_unit_diag as diag

    real = diag.record_d1_isolation
    _state = {"n": 0}

    def _flaky(**kw):
        _state["n"] += 1
        if _state["n"] > 2:          # 第 3 次起写失败（模拟个别行落盘失败）
            return False
        return real(**kw)

    monkeypatch.setattr(diag, "record_d1_isolation", _flaky)

    bad = ["%06d" % (900000 + i) for i in range(5)]
    df = _mk_df(2000, bad_codes=bad)
    adapter = _mk_adapter(restore_missing_factor_max_ratio=0.05)   # 5/2000=0.25% ≤ 5%
    _, meta = adapter._restore_to_raw(df, "stock_minutes", "1min",
                                      adj_latest_map=_amap_without(df, bad))
    assert meta["restore_isolated"] is True          # 取数不抛（fail-soft 义务①）

    with _aux_ro(_tmp_aux_db) as c:
        n_rows = c.execute("SELECT COUNT(*) FROM %s" % TABLE).fetchone()[0]
        agg = c.execute("SELECT MAX(listed_codes), MAX(missing_codes_total), "
                        "MAX(truncated) FROM %s" % TABLE).fetchone()
    assert n_rows == 2                     # 仅前 2 行落盘成功
    assert agg[0] == 2                     # listed_codes = 实际成功落盘行数（已校正）
    assert agg[1] == 5                     # missing_codes_total 仍为全量码数
    assert agg[2] == 1                     # ★ truncated 写时计算为 1（判据②闭合）
