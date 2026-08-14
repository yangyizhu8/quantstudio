"""工作包 D：QFQ front 数据质量第三道防线测试（任务书 §4，8 例）。

覆盖：
1. 自洽通过（正确 front → bad==0）
2. 自洽抓错（front=raw 模拟 QFQ bug → bad>0 且命中该行）
3. 三类行跳过（native 直通 / NULL front / 无因子日 → skipped 不算 bad）
4. 相对容差（高价股小绝对偏差不算坏、低价股大绝对偏差算坏）
5. 分钟表交易日口径（bar_day 连接，非毫秒时间戳等值）
6. fail-fast 不回退（adj_latest_map 为空 → 全 skipped，不抛错）
7. 因子完整性扫描（缺日/异常跳变/单日突增命中 + 交叉源 mock 成功/失败两分支）
8. 口径 A 锚一致性（R3：两次加载之间因子变更 → 调用链传入旧锚校验 bad==0）
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quantstudio.pipeline.qfq_invariant import (  # noqa: E402
    REL_TOL, audit_factor_integrity, check_qfq_invariant,
    verify_reanchor_selfcheck,
)

# 上海时区毫秒时间戳 helper
def _ms(day: str, hhmm="15:00") -> int:
    return int(pd.Timestamp(f"{day} {hhmm}", tz="Asia/Shanghai").value // 10**6)


def _make_aux_conn(factors):
    """内存 qfq_aux：factors = [(code, day, adj_factor)]，写 adj_factor 表。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
    conn.execute("CREATE TABLE fund_adj (code TEXT, time INTEGER, adj_factor REAL)")
    for code, day, f in factors:
        conn.execute("INSERT INTO adj_factor VALUES (?,?,?)", [code, _ms(day), f])
    conn.commit()
    return conn


def _make_daily_df(rows):
    """rows = [(code, day, raw_close, front_close)]；OHLC 其余列同 close。"""
    data = []
    for code, day, raw, front in rows:
        t = _ms(day)
        data.append({"code": code, "time": t,
                     "open": raw, "high": raw, "low": raw, "close": raw,
                     "open_front": front, "high_front": front,
                     "low_front": front, "close_front": front})
    return pd.DataFrame(data)


# —————————————————————— 测试 1：自洽通过 ——————————————————————

def test_1_invariant_pass():
    # factor: 1.0（旧日）→ 2.0（最新锚）；front = raw × adj_i / 2.0
    aux = _make_aux_conn([("600000", "2026-06-01", 1.0),
                          ("600000", "2026-06-02", 1.0)])
    df = _make_daily_df([
        ("600000", "2026-06-01", 10.0, 10.0 * 1.0 / 2.0),
        ("600000", "2026-06-02", 10.5, 10.5 * 1.0 / 2.0)])
    r = check_qfq_invariant(df, "stock_daily", {"600000": 2.0}, aux_conn=aux,
                            source="mcp")
    assert r["bad"] == 0
    assert r["sampled"] == 2


# —————————————————————— 测试 2：自洽抓错（front=raw 模拟本 bug） ——————————————————————

def test_2_invariant_catches_front_eq_raw():
    aux = _make_aux_conn([("600000", "2026-06-01", 1.0),
                          ("600000", "2026-06-02", 1.0)])
    # 故意 front=raw（本 bug 破坏形态：raw × adj_i / adj_i = raw）
    df = _make_daily_df([
        ("600000", "2026-06-01", 10.0, 10.0),      # bad
        ("600000", "2026-06-02", 10.5, 10.5 * 0.5)])  # good
    r = check_qfq_invariant(df, "stock_daily", {"600000": 2.0}, aux_conn=aux,
                            source="mcp")
    assert r["bad"] > 0
    assert any(d["code"] == "600000" and d["day"] == "2026-06-01"
               for d in r["bad_detail"])


# —————————————————————— 测试 3：三类行跳过 ——————————————————————

def test_3_skip_three_kinds():
    aux = _make_aux_conn([("600000", "2026-06-02", 1.0)])  # 06-01 无因子日
    df = _make_daily_df([
        ("600000", "2026-06-01", 10.0, 5.0),   # 无因子日 → skipped
        ("600000", "2026-06-02", 10.5, None)])  # NULL front → 该列不判坏
    r = check_qfq_invariant(df, "stock_daily", {"600000": 2.0}, aux_conn=aux,
                            source="mcp")
    assert r["bad"] == 0
    assert r["missing_factor_rows"] >= 1
    # native 直通源：整批跳过
    r2 = check_qfq_invariant(df, "stock_daily", {"600000": 2.0}, aux_conn=aux,
                             source="xtquant")
    assert r2["sampled"] == 0 and r2["skipped"] == len(df)


# —————————————————————— 测试 4：相对容差 ——————————————————————

def test_4_relative_tolerance():
    aux = _make_aux_conn([("600000", "2026-06-01", 1.0)])
    # 高价股 100 元：1e-5 绝对偏差（相对 1e-7 < 1e-6）→ 不算坏
    df_hi = _make_daily_df([("600000", "2026-06-01", 100.0, 100.0 * 0.5 + 1e-5)])
    # 低价股 1 元：0.5 绝对偏差（相对 1.0 > 1e-6）→ 算坏
    df_lo = _make_daily_df([("600000", "2026-06-01", 1.0, 1.0 * 0.5 + 0.5)])
    m = {"600000": 2.0}
    assert check_qfq_invariant(df_hi, "stock_daily", m, aux_conn=aux,
                               source="mcp")["bad"] == 0
    assert check_qfq_invariant(df_lo, "stock_daily", m, aux_conn=aux,
                               source="mcp")["bad"] > 0


# —————————————————————— 测试 5：分钟表交易日口径 ——————————————————————

def test_5_minute_bar_day_join():
    # 分钟表：同日 09:31 / 09:32 / 14:57 三根 bar；因子按交易日（06-01）连接
    aux = _make_aux_conn([("600000", "2026-06-01", 1.0)])
    rows = []
    for hhmm in ("09:31", "09:32", "14:57"):
        raw = 10.0
        rows.append((("600000",) + (pd.Timestamp(f"2026-06-01 {hhmm}", tz="Asia/Shanghai").value // 10**6, raw, raw * 0.5),))
    data = []
    for (code, t, raw, front) in [r[0] for r in rows]:
        data.append({"code": code, "time": t, "open": raw, "high": raw,
                     "low": raw, "close": raw, "open_front": front,
                     "high_front": front, "low_front": front, "close_front": front})
    df = pd.DataFrame(data)
    r = check_qfq_invariant(df, "stock_minutes", {"600000": 2.0}, aux_conn=aux,
                            source="mcp")
    # 三根分钟 bar 全部按 bar_day=2026-06-01 取到同一因子 → 全部通过（非毫秒等值
    # 会因 09:31/14:57 ≠ 15:00 查不到因子而全 skipped）
    assert r["sampled"] == 3
    assert r["bad"] == 0
    # 再验抓错：一根 bar front=raw → bad
    df.loc[1, "close_front"] = df.loc[1, "close"]
    r2 = check_qfq_invariant(df, "stock_minutes", {"600000": 2.0}, aux_conn=aux,
                             source="mcp")
    assert r2["bad"] > 0


# —————————————————————— 测试 6：fail-fast 不回退（无锚全跳过） ——————————————————————

def test_6_no_anchor_skip_all():
    aux = _make_aux_conn([("600000", "2026-06-01", 1.0)])
    df = _make_daily_df([("600000", "2026-06-01", 10.0, 5.0)])
    r = check_qfq_invariant(df, "stock_daily", {}, aux_conn=aux, source="mcp")
    assert r["sampled"] == 0 and r["skipped"] == 1 and r["bad"] == 0
    r_none = check_qfq_invariant(df, "stock_daily", None, aux_conn=aux, source="mcp")
    assert r_none["sampled"] == 0 and r_none["skipped"] == 1


# —————————————————————— 测试 7：因子完整性扫描 ——————————————————————

def _aux_for_audit():
    """构造：正常 2 code（无缺日）+ 跳变 + 缺日 + 单日突增数据集。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
    conn.execute("CREATE TABLE fund_adj (code TEXT, time INTEGER, adj_factor REAL)")
    days = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]
    rows = []
    # 正常两 code：因子平稳
    for c in ("600000", "600001"):
        for d in days:
            rows.append((c, _ms(d), 1.0))
    # 跳变 code：06-03 因子 1.0 → 3.0（>2×）
    for d in days:
        f = 1.0 if d < "2026-06-03" else 3.0
        rows.append(("600002", _ms(d), f))
    # 缺日 code：06-01 有、06-05 有（中间 3 个交易日缺因子）
    rows.append(("600003", _ms("2026-06-01"), 1.0))
    rows.append(("600003", _ms("2026-06-05"), 1.0))
    conn.executemany("INSERT INTO adj_factor VALUES (?,?,?)", rows)
    conn.commit()
    return conn


def _cal_conn():
    import duckdb
    conn = duckdb.connect()
    conn.execute("CREATE TABLE trade_calendar (trade_date DATE)")
    for d in ("2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"):
        conn.execute("INSERT INTO trade_calendar VALUES (?)", [d])
    return conn


def test_7_factor_integrity_scan():
    aux = _aux_for_audit()
    cal = _cal_conn()
    r = audit_factor_integrity(aux, cal)
    joined = "\n".join(r["warnings"])
    # 异常跳变命中（600002 06-03 比值 3.0×）
    assert "跳变" in joined and "600002" in joined
    # 缺日命中（600003 06-01~06-05 间 3 个交易日无因子）
    assert "缺日" in joined and "600003" in joined
    cal.close()

    # 交叉源 mock 成功：官方值一致 → 无 error
    aux2 = _aux_for_audit()
    r_ok = audit_factor_integrity(
        aux2, None, cross_source_fn=lambda code: 1.0 if code != "600002" else 3.0,
        cross_sample_n=4)
    assert r_ok["errors"] == []
    # 交叉源 mock 失败（返回 None）→ 降 warning 不制造 error
    r_net = audit_factor_integrity(aux2, None, cross_source_fn=lambda code: None,
                                   cross_sample_n=4)
    assert r_net["errors"] == []
    assert any("无返回" in w for w in r_net["warnings"])
    # 交叉源发现偏离 → error
    r_bad = audit_factor_integrity(
        aux2, None, cross_source_fn=lambda code: 999.0, cross_sample_n=4)
    assert len(r_bad["errors"]) > 0
    aux.close(); aux2.close()


def test_7b_single_day_spike_detection():
    """单日多 code 突增：构造 06-03 全市场 100% code 因子变更 → 超 5% 阈值命中。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
    conn.execute("CREATE TABLE fund_adj (code TEXT, time INTEGER, adj_factor REAL)")
    days = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]
    codes = [f"6000{i:02d}" for i in range(20)]
    for c in codes:
        for d in days:
            f = 1.0 if d < "2026-06-03" else 2.0  # 06-03 全部 20 只同时翻倍
            conn.execute("INSERT INTO adj_factor VALUES (?,?,?)", [c, _ms(d), f])
    conn.commit()
    r = audit_factor_integrity(conn, None)
    assert any("单日因子突增" in w for w in r["warnings"])
    conn.close()


# —————————————————————— 测试 8：口径 A 锚一致性（R3 验收） ——————————————————————

def test_8_anchor_a_consistency_r3():
    """两次加载之间因子变更：自检必须用调用链传入的旧锚（与本批写入一致），
    而非重新加载的新锚 → 对本批（旧锚算出的正确 front）bad==0。"""
    # 场景：daemon 在 T0 加载快照（600000 锚=2.0）→ align 用锚 2.0 算出 front
    # → 期间 qfq_aux.db 落地新因子（锚演进为 4.0）→ 自检点
    # R3：_stamp_and_write 使用调用链传入的旧锚 2.0 校验 → bad==0；
    #     若（错误地）重新加载新锚 4.0 → 会把正确行误判为坏。
    aux = _make_aux_conn([("600000", "2026-06-01", 1.0)])  # 写入时快照：锚=1.0
    # align 用锚 1.0 计算 front（正确）
    df = _make_daily_df([("600000", "2026-06-01", 10.0, 10.0 * 1.0 / 1.0)])
    # 两次加载之间：新除权落地，qfq_aux 追加因子 → 新锚=2.0
    aux.execute("INSERT INTO adj_factor VALUES (?,?,?)",
                ["600000", _ms("2026-06-05"), 2.0])
    aux.commit()
    # 自检用调用链传入的旧锚（写入时锚 1.0）→ 正确通过
    r = check_qfq_invariant(df, "stock_daily", {"600000": 1.0}, aux_conn=aux,
                            source="mcp")
    assert r["bad"] == 0
    # 反证：若误用重新加载的新锚 2.0 校验同批数据 → 会误报（证明口径 A 必要性）
    r_wrong = check_qfq_invariant(df, "stock_daily", {"600000": 2.0}, aux_conn=aux,
                                  source="mcp")
    assert r_wrong["bad"] > 0


# —————————————————————— 附：口径 B 重锚后自洽（verify_reanchor_selfcheck） ——————————————————————

def test_verify_reanchor_selfcheck_pass_and_catch():
    """口径 B：重锚后全历史自洽——正确通过 / 漏重锚一行被抓。"""
    import duckdb
    main = duckdb.connect()
    main.execute("""CREATE TABLE etf_daily (
        code TEXT, time BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
        open_front DOUBLE, high_front DOUBLE, low_front DOUBLE, close_front DOUBLE)""")
    aux = sqlite3.connect(":memory:")
    aux.execute("CREATE TABLE fund_adj (code TEXT, time INTEGER, adj_factor REAL)")
    aux.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
    # 因子：05-26 1.0，05-27 2.0（除权）→ 新锚 = 2.0
    aux.execute("INSERT INTO fund_adj VALUES ('159995', ?, 1.0)", [_ms("2026-05-26")])
    aux.execute("INSERT INTO fund_adj VALUES ('159995', ?, 2.0)", [_ms("2026-05-27")])
    aux.commit()
    # 正确重锚：全历史 front = raw × adj_i / 2.0
    rows = [
        ("159995", _ms("2026-05-26"), 2.707, 2.707 * 1.0 / 2.0),  # 1.3535
        ("159995", _ms("2026-05-27"), 2.631, 2.631 * 2.0 / 2.0),  # 2.631
    ]
    for code, t, raw, front in rows:
        main.execute(
            "INSERT INTO etf_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
            [code, t, raw, raw, raw, raw, front, front, front, front])
    r = verify_reanchor_selfcheck(main, aux, code="159995", table="etf_daily",
                                  adj_latest_new=2.0)
    assert r["bad"] == 0 and r["rows"] == 2
    # 漏重锚：05-26 仍是旧锚值（front=raw×1.0/1.0）→ 被口径 B 抓住
    main.execute("UPDATE etf_daily SET close_front = close, open_front = open, "
                 "high_front = high, low_front = low WHERE time = ?",
                 [_ms("2026-05-26")])
    r2 = verify_reanchor_selfcheck(main, aux, code="159995", table="etf_daily",
                                   adj_latest_new=2.0)
    assert r2["bad"] > 0
    assert any(d["day"] == "2026-05-26" for d in r2["bad_detail"])
    main.close(); aux.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# —————————————————————— 复审补测 B：save/refresh_golden_rows（S2 刷新无 NameError） ——————————————————————

def test_b2_save_and_refresh_golden_rows(tmp_path):
    """B 修复回归：save_golden_rows 此前 ensure_ascii 裸名 NameError（审核阻断 B）。
    验证：save/load 往返 + S2 刷新正确重算期望值 + anchor_version 尾号递增。"""
    import duckdb
    from quantstudio.pipeline.qfq_invariant import (
        save_golden_rows, load_golden_rows, refresh_golden_rows_for_code)
    gpath = tmp_path / "g.json"
    golden = [{"code": "600000", "table": "stock_daily", "date": "2026-06-01",
               "close_front_expected": 9.9, "anchor_version": "v6.7.52"}]
    save_golden_rows(golden, gpath)          # B 修复前此处 NameError
    assert load_golden_rows(gpath)[0]["close_front_expected"] == 9.9

    main = duckdb.connect()
    main.execute("""CREATE TABLE stock_daily (
        code TEXT, time BIGINT, close DOUBLE)""")
    main.execute("INSERT INTO stock_daily VALUES ('600000', ?, 10.0)",
                 [_ms("2026-06-01")])
    aux = sqlite3.connect(":memory:")
    aux.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
    aux.execute("CREATE TABLE fund_adj (code TEXT, time INTEGER, adj_factor REAL)")
    # 因子：06-01 = 1.0；最新 = 2.0（新锚）→ 刷新后期望 = 10 × 1.0 / 2.0 = 5.0
    aux.execute("INSERT INTO adj_factor VALUES ('600000', ?, 1.0)", [_ms("2026-06-01")])
    aux.execute("INSERT INTO adj_factor VALUES ('600000', ?, 2.0)", [_ms("2026-06-05")])
    aux.commit()
    r = refresh_golden_rows_for_code("600000", "stock_daily", main, aux,
                                     golden_path=gpath)
    assert r is not None and r["refreshed"] == 1
    g = load_golden_rows(gpath)[0]
    assert abs(g["close_front_expected"] - 5.0) < 1e-9
    assert g["anchor_version"] == "v6.7.52-2"      # 尾号递增
    main.close(); aux.close()


# —————————————————————— 复审补测 C：交叉源生产配置接线 ——————————————————————

def test_c_cross_source_wiring_real_config():
    """C 修复回归：真实 sources_config.json 结构（sources.tushare 嵌套 + enabled=false）
    下 _make_tushare_cross_fn 必须返回 None（不炸、有告警路径），而非读顶层错位
    静默 None；enabled=true + token 时可构造。"""
    import json as _json
    from quantstudio.pipeline.daemon import ResidentCollector
    cfg_path = Path(__file__).resolve().parent.parent / "config" / "profiles" / \
        "mcp_only" / "sources_config.json"
    if not cfg_path.exists():
        pytest.skip("mcp_only sources_config 不存在")
    c = ResidentCollector.__new__(ResidentCollector)
    c.sources_cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
    # mcp_only：tushare enabled=false → 明确 None（不走错位顶层读取）
    assert c._make_tushare_cross_fn() is None
    # enabled=true + token → 函数可构造（不实际调 tushare API）
    c.sources_cfg = {"sources": {"tushare": {"enabled": True, "token": "X"}}}
    fn = c._make_tushare_cross_fn()
    assert fn is not None


def test_c_orchestrator_config_field_wired():
    """C 修复回归：factor_cross_check_enabled 进入 QFQOrchestratorConfig 字段表
    + from_dict 接线（此前恒 False 死代码）。"""
    from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
    assert QFQOrchestratorConfig.from_dict(
        {"factor_cross_check_enabled": True}).factor_cross_check_enabled is True
    assert QFQOrchestratorConfig.from_dict(
        {}).factor_cross_check_enabled is False
    assert "factor_cross_check_enabled" in QFQOrchestratorConfig.__dataclass_fields__


# —————————————————————— 复审补测 D：streak 按 distinct batch_id + 自动解除 ——————————————————————

def _fake_collector_for_invariant(tmp_path):
    import types
    from quantstudio.pipeline.daemon import ResidentCollector
    c = ResidentCollector.__new__(ResidentCollector)
    c.data_cfg = {"batch_audit_path": str(tmp_path / "audit.db")}
    c.writer = types.SimpleNamespace(db_path=str(tmp_path / "main.duckdb"))
    # 防线1 aux 同源路径 = main.duckdb 同目录 qfq_aux.db → 造因子文件
    aux = sqlite3.connect(tmp_path / "qfq_aux.db")
    aux.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
    aux.execute("CREATE TABLE fund_adj (code TEXT, time INTEGER, adj_factor REAL)")
    aux.execute("INSERT INTO adj_factor VALUES ('600000', ?, 1.0)", [_ms("2026-06-01")])
    aux.commit(); aux.close()
    return c


def test_d_streak_distinct_batch_and_auto_unblock(tmp_path):
    """D 修复回归：同一坏批散到 3 次 _stamp_and_write 调用（同 batch_id）不触发
    阻断；3 个不同坏批次才阻断；good 批自动解除（无需重启）。
    坏批偏离率须 ≤5%（任务书 §1.4：单批 >5% 立即阻断）——20 行采样 1 行坏
    （rate=5%），验证的是"连续 N 批"路径而非立即阻断路径。"""
    c = _fake_collector_for_invariant(tmp_path)
    rows = [("600000", "2026-06-01", 10.0, 5.0)] * 19 + \
           [("600000", "2026-06-01", 10.0, 10.0)]      # 19 好 + 1 坏（front=raw）
    bad_df = _make_daily_df(rows)
    good_df = _make_daily_df([("600000", "2026-06-01", 10.0, 5.0)])
    m = {"600000": 2.0}
    # 同一坏批次 3 次调用（流式 3 分片）→ 不阻断
    for _ in range(3):
        c._qfq_invariant_after_align(bad_df, "stock_daily", "b0", "mcp",
                                     adj_latest_map=m)
    assert not c.qfq_invariant_should_block("stock_daily")
    # 3 个不同坏批次 → 阻断
    for bid in ("b1", "b2", "b3"):
        c._qfq_invariant_after_align(bad_df, "stock_daily", bid, "mcp",
                                     adj_latest_map=m)
    assert c.qfq_invariant_should_block("stock_daily")
    # good 批 → 自动解除
    c._qfq_invariant_after_align(good_df, "stock_daily", "b4", "mcp",
                                 adj_latest_map=m)
    assert not c.qfq_invariant_should_block("stock_daily")


# —————————————————————— 复审补测 E：分页全量读（无隐式截断） ——————————————————————

def test_e_paged_full_read_no_truncation():
    """E 修复回归：per_table_page_size=3 时 7 行因子全部读入（跨页 keyset 分页，
    此前 ORDER BY LIMIT 2M 会隐式截断大表）。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
    conn.execute("CREATE TABLE fund_adj (code TEXT, time INTEGER, adj_factor REAL)")
    days = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]
    for code in ("600000", "600001"):        # 2 code × 4 日 = 8 行 > page 3
        for d in days:
            conn.execute("INSERT INTO adj_factor VALUES (?,?,?)",
                         [code, _ms(d), 1.0])
    conn.commit()
    r = audit_factor_integrity(conn, None, per_table_page_size=3)
    assert r["stats"]["tables"]["adj_factor"]["rows"] == 8   # 全量，无截断
    conn.close()
