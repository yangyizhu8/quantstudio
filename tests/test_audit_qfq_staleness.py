"""tests/test_audit_qfq_staleness.py — QFQ 审计脚本专项测试（PR2 Commit 1 audit-fix + audit-fix2）。

覆盖评审 15 项 + audit-fix 7 阻断 + audit-fix2 4 阻断的精确断言。全部用 mock xtquant +
tmp_path DATA_ROOT + 临时 DuckDB/SQLite，不连接 live QMT，不碰正式库。

audit-fix 关键加强：
- stock_dividend 用正式 epoch-ms schema（非 YYYYMMDD）
- future LIMIT 挤占 today 的反例测试
- ETF changed/stable/no_record 三类分离精确断言
- ETF universe canonical ∪ xtquant union 精确断言
- NULL/numeric cells 单元格精确计数（四列同时 NULL mismatch = 4 cells）
- canonical/fresh/overlap earliest 元数据分开
- resolve_audit_window 纯函数测试
- factor_epsilon 小幅变化（1.0000 → 1.0005）

audit-fix2 关键加强（4 阻断精确反例）：
- 阻断 1：默认 run_audit 路径真正调用 xtquant ETF provider（非 canonical-only）
- 阻断 2：provider 返回 510050.SH/159919.SZ → universe 归一化为裸码去重
- 阻断 3：明确只支持 epoch-ms（删 mixed 兼容声明 + 修弱测试）
- 阻断 4：ETF adj_factor 两处 SQL 加 as-of 上界（future 不进 changed/no_record）

audit-fix3 勘误（阻断 1 根因）：
- _build_default_xtquant_etf_provider 旧实现误把 sources 当 list 遍历，
  真实 sources_config.json 的 "sources" 是 dict（key=源名），导致 .get() 失败、
  provider 永远为空 → 默认路径静默降级 canonical-only。
- 新增 hermetic 测试：真实 dict 形状 config + 真实 builder（不 monkeypatch builder 本体）
  + fake adapter（禁止连 live QMT），断言 provider 实际产出 ETF codes。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

BJ = timezone(timedelta(hours=8))


def _ms(year, month, day, hour=0):
    """构造 epoch ms（北京时区）。"""
    return int(datetime(year, month, day, hour, tzinfo=BJ).timestamp() * 1000)


@pytest.fixture
def tmp_data_root(monkeypatch, tmp_path):
    import quantstudio._paths as qp
    import scripts.audit_qfq_staleness as aud
    monkeypatch.setattr(qp, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(aud, "DATA_ROOT", tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ===========================================================================
# 1. 北京时区日期转换
# ===========================================================================

class TestMsToBj:
    def test_known_timestamp(self):
        from scripts.audit_qfq_staleness import ms_to_bj
        assert ms_to_bj(1784649600000) == "2026-07-22"

    def test_none(self):
        from scripts.audit_qfq_staleness import ms_to_bj
        assert ms_to_bj(None) == "None"


# ===========================================================================
# 2. ex_date 归一化（YYYYMMDD + epoch-ms 混合）
# ===========================================================================

class TestNormalizeExDate:
    def test_yyyymmdd_format(self):
        from scripts.audit_qfq_staleness import normalize_ex_date
        ms = normalize_ex_date(20260723)
        dt = datetime.fromtimestamp(ms / 1000, tz=BJ)
        assert dt.strftime("%Y-%m-%d") == "2026-07-23"

    def test_epoch_ms_passthrough(self):
        from scripts.audit_qfq_staleness import normalize_ex_date
        assert normalize_ex_date(1784736000000) == 1784736000000

    def test_none(self):
        from scripts.audit_qfq_staleness import normalize_ex_date
        assert normalize_ex_date(None) == 0


# ===========================================================================
# 3. past/today/future 分类
# ===========================================================================

class TestClassifyExDate:
    def test_past(self):
        from scripts.audit_qfq_staleness import classify_ex_date
        as_of = _ms(2026, 7, 23)
        assert classify_ex_date(_ms(2026, 7, 1), as_of) == "past"

    def test_today(self):
        from scripts.audit_qfq_staleness import classify_ex_date
        as_of = _ms(2026, 7, 23)
        assert classify_ex_date(_ms(2026, 7, 23, 12), as_of) == "today"

    def test_future(self):
        from scripts.audit_qfq_staleness import classify_ex_date
        as_of = _ms(2026, 7, 23)
        assert classify_ex_date(_ms(2026, 7, 24), as_of) == "future"


# ===========================================================================
# 4. stock candidate：epoch-ms + future LIMIT 反例（audit-fix 阻断 1）
# ===========================================================================

class TestStockCandidateSelection:
    def _make_stock_dividend_db(self, tmp_root, rows):
        import duckdb
        db = tmp_root / "quantstudio.db"
        conn = duckdb.connect(str(db))
        conn.execute("CREATE TABLE stock_dividend (code VARCHAR, ex_date BIGINT, cash_div DOUBLE, stk_div DOUBLE)")
        for r in rows:
            conn.execute("INSERT INTO stock_dividend VALUES (?,?,?,?)", list(r))
        conn.close()
        return db

    def test_epoch_ms_past_today_future(self, tmp_data_root):
        """正式 epoch-ms schema：past/today/future 分类正确。"""
        from scripts.audit_qfq_staleness import select_stock_candidates
        as_of = datetime(2026, 7, 23, tzinfo=BJ)
        self._make_stock_dividend_db(tmp_data_root, [
            ("600001", _ms(2026, 7, 1), 0.2, 0),    # past
            ("600002", _ms(2026, 7, 23), 0.2, 0),   # today
            ("600003", _ms(2026, 7, 24), 0.2, 0),   # future
        ])
        import duckdb
        conn = duckdb.connect(str(tmp_data_root / "quantstudio.db"))
        active, upcoming = select_stock_candidates(conn, as_of, n=10, days_back=365)
        conn.close()
        active_codes = [c[0] for c in active]
        upcoming_codes = [c[0] for c in upcoming]
        assert "600001" in active_codes
        assert "600002" in active_codes
        assert "600003" not in active_codes
        assert "600003" in upcoming_codes

    def test_future_does_not_crowd_out_today(self, tmp_data_root):
        """audit-fix 阻断 1 反例：1 today + 35 future，today 不能被 LIMIT 挤掉。"""
        from scripts.audit_qfq_staleness import select_stock_candidates
        as_of = datetime(2026, 7, 23, tzinfo=BJ)
        rows = [("600_today", _ms(2026, 7, 23), 0.2, 0)]
        # 35 个 future（用 timedelta 避免日期越界）
        for i in range(35):
            future_dt = datetime(2026, 7, 23, tzinfo=BJ) + timedelta(days=i + 1)
            rows.append(("60future_" + str(i),
                         int(future_dt.timestamp() * 1000), 0.2, 0))
        self._make_stock_dividend_db(tmp_data_root, rows)
        import duckdb
        conn = duckdb.connect(str(tmp_data_root / "quantstudio.db"))
        active, upcoming = select_stock_candidates(conn, as_of, n=10, days_back=365)
        conn.close()
        active_codes = [c[0] for c in active]
        # today 必须在 active，不能被 future 挤掉
        assert "600_today" in active_codes, "today 被 future LIMIT 挤掉了"

    def test_mixed_yyyymmdd_and_epoch_ms(self, tmp_data_root):
        """audit-fix2 阻断 3：明确只支持 epoch-ms（非 mixed 兼容）。

        生产口径 stock_dividend.ex_date 是 epoch ms。YYYYMMDD 整数（如 20260701≈2e7）
        远小于 cutoff_ms（≈1.78e12），会在 SQL 阶段被 epoch-ms 范围过滤掉，
        根本进不到 normalize_ex_date()。本测试精确断言这个已知局限：
        YYYYMMDD 旧数据**会漏选**（不声称支持 mixed），epoch-ms 数据正常选中。
        """
        from scripts.audit_qfq_staleness import select_stock_candidates
        as_of = datetime(2026, 7, 23, tzinfo=BJ)
        self._make_stock_dividend_db(tmp_data_root, [
            ("700001", 20260701, 0.2, 0),            # YYYYMMDD（旧数据，已知会漏选）
            ("700002", _ms(2026, 7, 15), 0.2, 0),    # epoch-ms（正式口径，正常选中）
        ])
        import duckdb
        conn = duckdb.connect(str(tmp_data_root / "quantstudio.db"))
        active, _ = select_stock_candidates(conn, as_of, n=10, days_back=365)
        conn.close()
        active_codes = [c[0] for c in active]
        # epoch-ms 正式口径数据正常选中
        assert "700002" in active_codes
        # 已知局限（非兼容声明）：YYYYMMDD 旧数据被 epoch-ms SQL cutoff 过滤 → 漏选。
        # 断言它确实没被选中，精确记录此局限；若未来需支持 YYYYMMDD 须重建库为 epoch ms。
        assert "700001" not in active_codes, "YYYYMMDD 旧数据不应被 epoch-ms SQL 选中（已知局限）"


# ===========================================================================
# 5. ETF adj_factor：changed/stable/no_record 三类 + LAG 边界 + epsilon（阻断 2/6）
# ===========================================================================

class TestEtfAdjFactor:
    def _make_qfq_aux(self, tmp_root, rows):
        db = tmp_root / "qfq_aux.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE adj_factor (code TEXT, time INTEGER, adj_factor REAL)")
        conn.executemany("INSERT INTO adj_factor VALUES (?,?,?)", rows)
        conn.commit()
        conn.close()

    def test_three_classes_separated(self, tmp_data_root):
        """阻断 2：changed / stable_with_record / no_record 三类精确分离。"""
        from scripts.audit_qfq_staleness import select_etf_candidates_from_adj_factor
        as_of = datetime(2026, 7, 23, tzinfo=BJ)
        rows = [
            # 510210 有变化
            ("510210", _ms(2026, 7, 15), 1.0),
            ("510210", _ms(2026, 7, 20), 1.05),
            # 159928 有记录但无变化（stable_with_record）
            ("159928", _ms(2026, 7, 15), 1.0),
            ("159928", _ms(2026, 7, 22), 1.0),
            # 510300 完全不在表里（no_record）
        ]
        self._make_qfq_aux(tmp_data_root, rows)
        changed, stable, no_record = select_etf_candidates_from_adj_factor(
            ["510210", "159928", "510300"], as_of, days_back=30)
        changed_codes = [c[0] for c in changed]
        assert "510210" in changed_codes
        assert "159928" in stable        # 有记录无变化，不是 no_record
        assert "510300" in no_record     # 完全无记录
        assert "159928" not in no_record  # 关键：有记录的不应被报为"无法判断"

    def test_stable_with_record_not_reported_as_no_record(self, tmp_data_root):
        """阻断 2 复现：510210 有两条因子=1.0，应报 stable 而非 no_record。"""
        from scripts.audit_qfq_staleness import select_etf_candidates_from_adj_factor
        as_of = datetime(2026, 7, 23, tzinfo=BJ)
        self._make_qfq_aux(tmp_data_root, [
            ("510210", _ms(2026, 7, 15), 1.0),
            ("510210", _ms(2026, 7, 22), 1.0),
        ])
        changed, stable, no_record = select_etf_candidates_from_adj_factor(
            ["510210"], as_of, days_back=30)
        assert changed == []
        assert "510210" in stable
        assert "510210" not in no_record

    def test_small_factor_change_detected_with_epsilon(self, tmp_data_root):
        """阻断 6：小幅因子变化（1.0000 → 1.0005）应被识别。"""
        from scripts.audit_qfq_staleness import select_etf_candidates_from_adj_factor
        as_of = datetime(2026, 7, 23, tzinfo=BJ)
        self._make_qfq_aux(tmp_data_root, [
            ("510210", _ms(2026, 7, 15), 1.0000),
            ("510210", _ms(2026, 7, 20), 1.0005),  # 变化 0.0005，> epsilon(1e-9)，< 0.001
        ])
        changed, _, _ = select_etf_candidates_from_adj_factor(
            ["510210"], as_of, days_back=30, factor_epsilon=1e-9)
        assert len(changed) == 1
        assert changed[0][0] == "510210"

    def test_lag_window_boundary(self, tmp_data_root):
        """评审 5: 窗口第一日因子变化不漏检。"""
        from scripts.audit_qfq_staleness import select_etf_candidates_from_adj_factor
        as_of = datetime(2026, 7, 23, tzinfo=BJ)
        cutoff = int((as_of - timedelta(days=30)).timestamp() * 1000)
        self._make_qfq_aux(tmp_data_root, [
            ("510210", cutoff - 86400000, 1.0),
            ("510210", cutoff + 1000, 1.1),
        ])
        changed, _, _ = select_etf_candidates_from_adj_factor(
            ["510210"], as_of, days_back=30)
        assert len(changed) == 1

    def test_future_adj_factor_change_not_in_changed(self, tmp_data_root):
        """audit-fix2 阻断 4：as_of=2026-07-23，2026-07-24 的未来因子变化不进 changed。

        复现评审反例：future_factor_changed=[('510210', 2026-07-24, 0.1)] 被旧实现报为 changed。
        修复后两处 SQL 加 time <= as_of_end_ms 上界，future 不进 changed。
        """
        from scripts.audit_qfq_staleness import select_etf_candidates_from_adj_factor
        as_of = datetime(2026, 7, 23, tzinfo=BJ)
        self._make_qfq_aux(tmp_data_root, [
            # 7-22 历史 anchor + 7-24 未来变化（跨 as_of）
            ("510210", _ms(2026, 7, 22), 1.0),
            ("510210", _ms(2026, 7, 24), 1.1),   # future 变化（> as_of 当天）
        ])
        changed, stable, no_record = select_etf_candidates_from_adj_factor(
            ["510210"], as_of, days_back=30)
        changed_codes = [c[0] for c in changed]
        # 关键：future 变化不进 changed
        assert "510210" not in changed_codes, "未来因子变化被报为 changed（缺 as-of 上界）"
        # 510210 在 as_of 时点有历史记录（7-22 <= as_of 当天 23:59），属 stable_with_record
        assert "510210" in stable
        assert "510210" not in no_record

    def test_future_only_code_treated_as_no_record(self, tmp_data_root):
        """audit-fix2 阻断 4：future-only 记录不被视为 as-of 时点已有记录（归入 no_record）。

        复现评审反例：codes_with_any_record 查询缺上界，future-only 记录被算成"已有记录"。
        修复后 recorded 查询加 time <= as_of_end_ms，future-only code 归入 no_record。
        """
        from scripts.audit_qfq_staleness import select_etf_candidates_from_adj_factor
        as_of = datetime(2026, 7, 23, tzinfo=BJ)
        self._make_qfq_aux(tmp_data_root, [
            # 510300 只有 2026-07-25 一条（future-only，> as_of）
            ("510300", _ms(2026, 7, 25), 1.0),
        ])
        changed, stable, no_record = select_etf_candidates_from_adj_factor(
            ["510300"], as_of, days_back=30)
        # 关键：future-only 不算 as-of 时点已记录 → 归入 no_record（非 stable）
        assert "510300" not in stable, "future-only 记录被误判为 as-of 时点已记录"
        assert "510300" in no_record
        assert changed == []

    def test_changed_candidates_deduped_by_code(self, tmp_data_root):
        """audit-fix2 建议顺手修复：同一 ETF 多次因子变化，changed 只报一只一次。"""
        from scripts.audit_qfq_staleness import select_etf_candidates_from_adj_factor
        as_of = datetime(2026, 7, 23, tzinfo=BJ)
        self._make_qfq_aux(tmp_data_root, [
            # 510210 在窗口内有 3 次变化（同一 ETF 多版本）
            ("510210", _ms(2026, 7, 10), 1.0),
            ("510210", _ms(2026, 7, 15), 1.05),
            ("510210", _ms(2026, 7, 20), 1.10),
        ])
        changed, _, _ = select_etf_candidates_from_adj_factor(
            ["510210"], as_of, days_back=30)
        changed_codes = [c[0] for c in changed]
        # 同一 code 只报一次（去重），不报 3 次
        assert changed_codes.count("510210") == 1, f"同一 ETF 重复报为多只: {changed_codes}"
        assert len(changed) == 1


# ===========================================================================
# 6. ETF universe：canonical ∪ xtquant union（阻断 3）
# ===========================================================================

class TestEtfUniverse:
    def test_union_canonical_and_xtquant(self, tmp_data_root):
        """阻断 3：canonical={A,B} ∪ xtquant={B,C} = {A,B,C}。"""
        from scripts.audit_qfq_staleness import _default_etf_universe
        import duckdb
        db = tmp_data_root / "quantstudio.db"
        conn = duckdb.connect(str(db))
        conn.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT)")
        conn.execute("INSERT INTO etf_daily VALUES ('A', 1), ('B', 2)")
        # xtquant provider 返回 {B, C}
        def provider():
            return ["B", "C"]
        codes = _default_etf_universe(conn, xtquant_etf_provider=provider)
        conn.close()
        assert set(codes) == {"A", "B", "C"}

    def test_canonical_only_when_provider_none(self, tmp_data_root):
        from scripts.audit_qfq_staleness import _default_etf_universe
        import duckdb
        db = tmp_data_root / "quantstudio.db"
        conn = duckdb.connect(str(db))
        conn.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT)")
        conn.execute("INSERT INTO etf_daily VALUES ('510210', 1), ('159928', 2), ('510210', 3)")
        codes = _default_etf_universe(conn, xtquant_etf_provider=None)
        conn.close()
        assert set(codes) == {"510210", "159928"}

    def test_provider_failure_degrades_to_canonical(self, tmp_data_root):
        """provider 抛异常时降级用 canonical。"""
        from scripts.audit_qfq_staleness import _default_etf_universe
        import duckdb
        db = tmp_data_root / "quantstudio.db"
        conn = duckdb.connect(str(db))
        conn.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT)")
        conn.execute("INSERT INTO etf_daily VALUES ('A', 1)")
        def bad_provider():
            raise RuntimeError("xtquant 未连接")
        codes = _default_etf_universe(conn, xtquant_etf_provider=bad_provider)
        conn.close()
        assert "A" in codes  # canonical 仍可用

    def test_suffixed_codes_normalized_to_bare_and_deduped(self, tmp_data_root):
        """audit-fix2 阻断 2：provider 返回 510050.SH/159919.SZ → universe 归一化裸码去重。

        复现评审反例：canonical=['510050'], xtquant=['510050.SH','159919.SZ']。
        旧实现直接 union 得 ['159919.SZ','510050','510050.SH']（未去重、带后缀）。
        修复后必须得去重后的裸码 ['510050','159919']。
        """
        from scripts.audit_qfq_staleness import _default_etf_universe
        import duckdb
        db = tmp_data_root / "quantstudio.db"
        conn = duckdb.connect(str(db))
        conn.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT)")
        conn.execute("INSERT INTO etf_daily VALUES ('510050', 1)")  # canonical 裸码
        def provider():
            return ["510050.SH", "159919.SZ"]  # xtquant 带后缀
        codes = _default_etf_universe(conn, xtquant_etf_provider=provider)
        conn.close()
        # 关键：去重后的裸码，无后缀，无重复
        assert codes == ["159919", "510050"], f"未归一化/去重: {codes}"
        # 反例校验：绝不能出现带后缀的 code 或重复
        assert "510050.SH" not in codes
        assert "159919.SZ" not in codes
        assert codes.count("510050") == 1

    def test_builder_parses_real_dict_config_and_yields_codes(self, tmp_data_root, monkeypatch, tmp_path):
        """audit-fix3 阻断 1（hermetic）：真实 dict 形状 sources_config.json 被正确解析。

        审计-fix2 的 builder 误把 sources 当 list 遍历（src.get("name")），而真实
        sources_config.json 的 "sources" 是 dict（key=源名）。旧实现对 dict key
        字符串调 .get() → 'str' object has no attribute 'get'，provider 永远为空 →
        默认路径静默降级 canonical-only，阻断 1 实际未关闭。

        本测试**不 monkeypatch builder 本体**，而是：
        1. 写一份真实 dict 形状的 sources_config.json（与 config/sources_config.json 一致）；
        2. 指向 aud._ROOT 到该临时根，让真实 _build_default_xtquant_etf_provider() 运行；
        3. 用 fake adapter（注入 sys.modules，禁止连 live QMT）替换真实 XtquantAdapter；
        4. 断言 builder 返回的 provider 实际产出 ETF codes（含 enabled/${ENV} 展开）。
        """
        import json
        import sys
        import types
        import scripts.audit_qfq_staleness as aud

        # 1. 真实 dict 形状 config（与项目 config/sources_config.json schema 一致）
        fake_root = tmp_path / "fakeroot"
        (fake_root / "config").mkdir(parents=True)
        (fake_root / "config" / "sources_config.json").write_text(json.dumps({
            "sources": {
                "tushare": {"enabled": True, "token": "${TUSHARE_TOKEN}"},
                "xtquant": {"enabled": True, "qmt_path": "${QMT_PATH}"}
            },
            "default_source_priority": ["xtquant", "tushare"]
        }), encoding="utf-8")
        monkeypatch.setattr(aud, "_ROOT", fake_root)

        # 2. fake XtquantAdapter（注入 sys.modules，禁止连 live QMT）
        captured = {}

        class FakeXtquantAdapter:
            def __init__(self, cfg):
                captured["cfg"] = dict(cfg)

            def get_etf_codes(self):
                return ["510050.SH", "159919.SZ"]

        fake_mod = types.ModuleType("quantstudio.pipeline.sources.xtquant_adapter")
        fake_mod.XtquantAdapter = FakeXtquantAdapter
        monkeypatch.setitem(sys.modules,
                            "quantstudio.pipeline.sources.xtquant_adapter", fake_mod)

        # 3. 调真实 builder（不 monkeypatch builder 本体）
        provider = aud._build_default_xtquant_etf_provider()
        result = provider()

        # 4. 关键断言：真实 dict config 被解析，provider 实际产出 ETF codes
        assert result == ["510050.SH", "159919.SZ"], \
            f"真实 dict config 下 provider 返回空（阻断 1 回归）: {result}"
        # cfg 已展开 ${ENV}、加 name=xtquant（与 daemon._get_adapter 一致）
        assert captured["cfg"]["name"] == "xtquant"
        assert captured["cfg"]["enabled"] is True
        assert captured["cfg"]["qmt_path"] == ""  # ${QMT_PATH} 未设环境变量 → 空串

    def test_builder_list_schema_compat(self, tmp_data_root, monkeypatch, tmp_path):
        """audit-fix3：builder 对历史 list schema 做防御兼容（不假定一定是 dict）。"""
        import json
        import sys
        import types
        import scripts.audit_qfq_staleness as aud

        fake_root = tmp_path / "fakeroot2"
        (fake_root / "config").mkdir(parents=True)
        (fake_root / "config" / "sources_config.json").write_text(json.dumps({
            "sources": [{"name": "tushare", "enabled": True},
                        {"name": "xtquant", "enabled": True, "qmt_path": "/x"}]
        }), encoding="utf-8")
        monkeypatch.setattr(aud, "_ROOT", fake_root)

        class FakeAdapter:
            def __init__(self, cfg):
                pass

            def get_etf_codes(self):
                return ["510050.SH"]

        fake_mod = types.ModuleType("quantstudio.pipeline.sources.xtquant_adapter")
        fake_mod.XtquantAdapter = FakeAdapter
        monkeypatch.setitem(sys.modules,
                            "quantstudio.pipeline.sources.xtquant_adapter", fake_mod)

        provider = aud._build_default_xtquant_etf_provider()
        assert provider() == ["510050.SH"]

    def test_builder_disabled_returns_empty(self, tmp_data_root, monkeypatch, tmp_path):
        """audit-fix3：xtquant enabled=false 时 provider 返回空（显式降级，非异常）。"""
        import json
        import sys
        import types
        import scripts.audit_qfq_staleness as aud

        fake_root = tmp_path / "fakeroot3"
        (fake_root / "config").mkdir(parents=True)
        (fake_root / "config" / "sources_config.json").write_text(json.dumps({
            "sources": {"xtquant": {"enabled": False}}
        }), encoding="utf-8")
        monkeypatch.setattr(aud, "_ROOT", fake_root)
        # 即使有真实 adapter，enabled=False 也不应构造
        constructed = []

        class FakeAdapter:
            def __init__(self, cfg):
                constructed.append(cfg)

        fake_mod = types.ModuleType("quantstudio.pipeline.sources.xtquant_adapter")
        fake_mod.XtquantAdapter = FakeAdapter
        monkeypatch.setitem(sys.modules,
                            "quantstudio.pipeline.sources.xtquant_adapter", fake_mod)

        provider = aud._build_default_xtquant_etf_provider()
        assert provider() == []
        assert constructed == [], "enabled=False 不应构造 adapter"

    def test_default_run_audit_uses_nonempty_provider(self, tmp_data_root, monkeypatch):
        """audit-fix2/3 阻断 1 wiring：默认 run_audit 路径把非 None provider 传给 universe。

        与 hermetic builder 测试互补：builder 本体不被替换，但 _ROOT 指向 enabled=true 的
        临时 config + fake adapter，验证 run_audit 默认路径端到端产出含 xtquant code 的 universe。
        """
        import json
        import sys
        import types
        import argparse
        import duckdb
        import pandas as pd
        import scripts.audit_qfq_staleness as aud

        # 真实 dict config（enabled=true）
        fake_root = tmp_data_root / "fakeroot"
        (fake_root / "config").mkdir(parents=True)
        (fake_root / "config" / "sources_config.json").write_text(json.dumps({
            "sources": {"xtquant": {"enabled": True, "qmt_path": "${QMT_PATH}"}}
        }), encoding="utf-8")
        monkeypatch.setattr(aud, "_ROOT", fake_root)

        class FakeAdapter:
            def __init__(self, cfg):
                pass

            def get_etf_codes(self):
                return ["510050.SH"]  # xtquant 带后缀（会被 bare_code 归一化）

        fake_mod = types.ModuleType("quantstudio.pipeline.sources.xtquant_adapter")
        fake_mod.XtquantAdapter = FakeAdapter
        monkeypatch.setitem(sys.modules,
                            "quantstudio.pipeline.sources.xtquant_adapter", fake_mod)

        # 最小 canonical DB
        db = tmp_data_root / "quantstudio.db"
        conn = duckdb.connect(str(db))
        conn.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT)")
        conn.execute("INSERT INTO etf_daily VALUES ('CANON_A', 1)")
        conn.execute("CREATE TABLE stock_dividend (code VARCHAR, ex_date BIGINT, cash_div DOUBLE, stk_div DOUBLE)")
        conn.execute("CREATE TABLE source_watermark (source VARCHAR, table_name VARCHAR, freq VARCHAR, last_date BIGINT)")
        conn.close()
        monkeypatch.setattr(aud, "db_path", lambda: str(db))
        # 不连接 live QMT
        monkeypatch.setattr(aud, "fetch_fresh_front_xtquant",
                            lambda *a, **k: (pd.DataFrame(), {"download_performed": False}))
        monkeypatch.setattr(aud, "read_canonical", lambda *a, **k: pd.DataFrame())

        # spy universe 捕获 provider + 返回值
        captured = {"provider": "NOT_CALLED", "universe": []}
        real_universe = aud._default_etf_universe

        def spy(conn, xtquant_etf_provider=None):
            captured["provider"] = xtquant_etf_provider
            res = real_universe(conn, xtquant_etf_provider=xtquant_etf_provider)
            captured["universe"] = res
            return res
        monkeypatch.setattr(aud, "_default_etf_universe", spy)

        args = argparse.Namespace(
            as_of_date="2026-07-23", stocks=None, etfs=None,
            full_history=False, no_download=True)
        aud.run_audit(args)

        # 关键断言：默认路径 provider 非 None，且 universe 含 xtquant 提供的裸码（归一化后）
        assert captured["provider"] is not None, "默认路径 provider 为 None（canonical-only 回归）"
        assert "510050" in captured["universe"], \
            f"默认路径 universe 未含 xtquant code（阻断 1 回归）: {captured['universe']}"


# ===========================================================================
# 7. resolve_audit_window 纯函数（阻断 7）
# ===========================================================================

class TestResolveAuditWindow:
    def test_rolling_window(self):
        from scripts.audit_qfq_staleness import resolve_audit_window
        as_of = datetime(2027, 1, 15, tzinfo=BJ)
        start, end, desc = resolve_audit_window(as_of, full_history=False)
        assert end == "20270115"
        # 2027-01-15 - 730 天 ≈ 2025-01-16
        assert start in ("20250115", "20250116")
        assert "滚动" in desc

    def test_full_history(self):
        from scripts.audit_qfq_staleness import resolve_audit_window
        as_of = datetime(2026, 7, 23, tzinfo=BJ)
        start, end, desc = resolve_audit_window(as_of, full_history=True)
        assert start == "20180101"
        assert end == "20260723"
        assert "完整历史" in desc


# ===========================================================================
# 8. time-key merge（顺序/缺行/重复/空段）
# ===========================================================================

class TestTimeKeyMerge:
    def _make_xtdata_mock(self, raw_df, front_df, back_df):
        xtdata = MagicMock()
        def make_return(df):
            if df is None or len(df) == 0:
                return {}
            df2 = df.set_index("time") if "time" in df.columns else df
            return {"600875.SH": df2}
        def get_market_data_ex(stock_list, period, start_time, end_time, dividend_type):
            if dividend_type == "none":
                return make_return(raw_df)
            if dividend_type == "front":
                return make_return(front_df)
            if dividend_type == "back":
                return make_return(back_df)
            return {}
        xtdata.get_market_data_ex = get_market_data_ex
        xtdata.download_history_data = MagicMock()
        return xtdata

    def test_merge_different_order(self, tmp_data_root):
        from scripts.audit_qfq_staleness import fetch_fresh_front_xtquant
        times = [1000, 2000, 3000]
        raw = pd.DataFrame({"time": times, "open": [10, 11, 12], "close": [10.5, 11.5, 12.5]})
        front = pd.DataFrame({"time": [3000, 2000, 1000], "close": [9.5, 10.5, 11.5]})
        back = pd.DataFrame({"time": times, "close": [15, 16, 17]})
        xtdata = self._make_xtdata_mock(raw, front, back)
        df, _ = fetch_fresh_front_xtquant(["600875"], "20240101", "20240105",
                                           "stock_daily", do_download=False, xtdata_client=xtdata)
        row_1000 = df[df["time"] == 1000].iloc[0]
        assert row_1000["close_front"] == 11.5  # 按 time 对齐，非位置

    def test_front_missing_day(self, tmp_data_root):
        from scripts.audit_qfq_staleness import fetch_fresh_front_xtquant
        times = [1000, 2000, 3000]
        raw = pd.DataFrame({"time": times, "close": [10, 11, 12]})
        front = pd.DataFrame({"time": [1000, 3000], "close": [9, 11]})
        back = pd.DataFrame({"time": times, "close": [15, 16, 17]})
        xtdata = self._make_xtdata_mock(raw, front, back)
        df, _ = fetch_fresh_front_xtquant(["600875"], "20240101", "20240105",
                                           "stock_daily", do_download=False, xtdata_client=xtdata)
        row_2000 = df[df["time"] == 2000].iloc[0]
        assert pd.isna(row_2000["close_front"])

    def test_back_duplicate_time_keep_last(self, tmp_data_root):
        from scripts.audit_qfq_staleness import fetch_fresh_front_xtquant
        raw = pd.DataFrame({"time": [1000], "close": [10]})
        front = pd.DataFrame({"time": [1000], "close": [9]})
        back = pd.DataFrame({"time": [1000, 1000], "close": [15, 99]})
        xtdata = self._make_xtdata_mock(raw, front, back)
        df, _ = fetch_fresh_front_xtquant(["600875"], "20240101", "20240105",
                                           "stock_daily", do_download=False, xtdata_client=xtdata)
        assert df.iloc[0]["close_back"] == 99

    def test_empty_segment(self, tmp_data_root):
        from scripts.audit_qfq_staleness import fetch_fresh_front_xtquant
        raw = pd.DataFrame({"time": [1000, 2000], "close": [10, 11]})
        xtdata = self._make_xtdata_mock(raw, pd.DataFrame(), pd.DataFrame())
        df, _ = fetch_fresh_front_xtquant(["600875"], "20240101", "20240105",
                                           "stock_daily", do_download=False, xtdata_client=xtdata)
        assert len(df) == 2


# ===========================================================================
# 9. NULL/numeric cells 精确计数（阻断 4）
# ===========================================================================

class TestNullMismatchCells:
    def test_four_cols_all_null_mismatch_is_four_cells(self, tmp_data_root):
        """阻断 4：四列同时 NULL mismatch 应=4 单元格，非 1。"""
        from scripts.audit_qfq_staleness import compare_front
        canon = pd.DataFrame({"code": ["A"], "time": [1000],
                              "open_front": [None], "high_front": [None],
                              "low_front": [None], "close_front": [None]})
        fresh = pd.DataFrame({"code": ["A"], "time": [1000],
                              "open_front": [10], "high_front": [11],
                              "low_front": [9], "close_front": [10.5]})
        diff = compare_front(canon, fresh, "stock_daily")
        assert diff["affected_unique_rows"] == 1   # 唯一行=1
        assert diff["affected_cells"] == 4          # 单元格=4
        assert diff["null_mismatch_cells"] == 4     # NULL mismatch 单元格=4（非 1）
        assert diff["null_mismatch_unique_rows"] == 1  # 唯一行=1

    def test_canon_null_fresh_valid(self):
        from scripts.audit_qfq_staleness import compare_front
        canon = pd.DataFrame({"code": ["A"], "time": [1000], "close_front": [None]})
        fresh = pd.DataFrame({"code": ["A"], "time": [1000], "close_front": [10.0]})
        diff = compare_front(canon, fresh, "stock_daily")
        assert diff["null_mismatch_cells"] == 1

    def test_four_numeric_cols_diff_not_multiplied_unique(self):
        """阻断 4：四列 numeric diff，唯一行=1，单元格=4。"""
        from scripts.audit_qfq_staleness import compare_front
        canon = pd.DataFrame({"code": ["A"], "time": [1000],
                              "open_front": [10], "high_front": [11],
                              "low_front": [9], "close_front": [10.5]})
        fresh = pd.DataFrame({"code": ["A"], "time": [1000],
                              "open_front": [9], "high_front": [10],
                              "low_front": [8], "close_front": [9.5]})
        diff = compare_front(canon, fresh, "stock_daily")
        assert diff["affected_unique_rows"] == 1
        assert diff["affected_cells"] == 4
        assert diff["numeric_diff_cells"] == 4


# ===========================================================================
# 10. SQL 参数化 + 表名白名单
# ===========================================================================

class TestSqlSafety:
    def test_table_whitelist_rejects_unknown(self, tmp_data_root):
        from scripts.audit_qfq_staleness import _validate_table
        with pytest.raises(ValueError):
            _validate_table("malicious; DROP TABLE")

    def test_empty_code_list_returns_empty(self, tmp_data_root):
        from scripts.audit_qfq_staleness import read_canonical
        import duckdb
        conn = duckdb.connect(":memory:")
        df = read_canonical(conn, [], "stock_daily")
        assert len(df) == 0
        conn.close()


# ===========================================================================
# 11. 市场代码（含北交所）
# ===========================================================================

class TestMarketCode:
    def test_sh_main_board(self):
        from scripts.audit_qfq_staleness import to_qmt_code
        assert to_qmt_code("600000") == "600000.SH"

    def test_sz_main_board(self):
        from scripts.audit_qfq_staleness import to_qmt_code
        assert to_qmt_code("000001") == "000001.SZ"

    def test_chinext(self):
        from scripts.audit_qfq_staleness import to_qmt_code
        assert to_qmt_code("300001") == "300001.SZ"

    def test_etf_sh(self):
        from scripts.audit_qfq_staleness import to_qmt_code
        assert to_qmt_code("510210") == "510210.SH"

    def test_etf_sz(self):
        from scripts.audit_qfq_staleness import to_qmt_code
        assert to_qmt_code("159928") == "159928.SZ"

    def test_bse_920_not_sh(self):
        from scripts.audit_qfq_staleness import to_qmt_code
        result = to_qmt_code("920001")
        assert result != "920001.SH"
        assert result.endswith(".BJ")


# ===========================================================================
# 12. canonical/fresh/overlap 元数据（阻断 5）
# ===========================================================================

class TestMetadataSeparation:
    def test_canonical_fresh_overlap_earliest_distinct(self):
        """阻断 5：canonical 从 2018，fresh 从 2024 → 三者不同。"""
        from scripts.audit_qfq_staleness import compare_front
        # canonical: 2018-2026
        canon_times = [_ms(2018, 1, 1), _ms(2024, 6, 1), _ms(2026, 7, 1)]
        # fresh: 2024-2026
        fresh_times = [_ms(2024, 6, 1), _ms(2026, 7, 1)]
        canon = pd.DataFrame({"code": ["A"]*3, "time": canon_times,
                              "close_front": [10, 11, 12]})
        fresh = pd.DataFrame({"code": ["A"]*2, "time": fresh_times,
                              "close_front": [10, 11]})  # 与 canon 一致，无 diff
        diff = compare_front(canon, fresh, "stock_daily")
        assert diff["canonical_earliest"] == _ms(2018, 1, 1)
        assert diff["fresh_earliest"] == _ms(2024, 6, 1)
        assert diff["overlap_earliest"] == _ms(2024, 6, 1)
        # 三者不再恒等
        assert diff["canonical_earliest"] != diff["fresh_earliest"]
        assert diff["canonical_earliest"] != diff["overlap_earliest"]
        assert diff["fresh_earliest"] == diff["overlap_earliest"]
        assert diff["canonical_rows"] == 3
        assert diff["fresh_rows"] == 2
        assert diff["overlap_rows"] == 2


# ===========================================================================
# 13. download mock + 副作用
# ===========================================================================

class TestDownloadMock:
    def test_mock_xtdata_no_live_connection(self, tmp_data_root):
        from scripts.audit_qfq_staleness import fetch_fresh_front_xtquant
        xtdata = MagicMock()
        xtdata.get_market_data_ex = MagicMock(return_value={})
        xtdata.download_history_data = MagicMock()
        df, meta = fetch_fresh_front_xtquant(["600000"], "20240101", "20240105",
                                              "stock_daily", do_download=True, xtdata_client=xtdata)
        assert xtdata.download_history_data.called
        assert meta["download_performed"] is True

    def test_no_download_flag(self, tmp_data_root):
        from scripts.audit_qfq_staleness import fetch_fresh_front_xtquant
        xtdata = MagicMock()
        xtdata.get_market_data_ex = MagicMock(return_value={})
        df, meta = fetch_fresh_front_xtquant(["600000"], "20240101", "20240105",
                                              "stock_daily", do_download=False, xtdata_client=xtdata)
        assert meta["download_performed"] is False
        assert not xtdata.download_history_data.called


# ===========================================================================
# 14. QFQ revision detection CLI 集成（PR2 Commit 2）
# ===========================================================================

class TestRevisionAuditCli:
    """audit-fix/Commit 2：默认 dry-run 零写入；--persist-revision-audit 端到端写。"""

    def _make_qfq_aux(self, tmp_root, adj_rows):
        import sqlite3
        db = tmp_root / "qfq_aux.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE IF NOT EXISTS adj_factor "
                     "(code TEXT, time INTEGER, adj_factor REAL, PRIMARY KEY(code, time))")
        conn.executemany("INSERT INTO adj_factor VALUES (?,?,?)", adj_rows)
        conn.commit()
        conn.close()
        return db

    def _args(self, **kw):
        import argparse
        base = dict(as_of_date="2026-07-23", stocks=None, etfs=None,
                    full_history=False, no_download=True,
                    persist_revision_audit=False, revision_epsilon=1e-9)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_dry_run_creates_no_revision_schema(self, tmp_data_root, capsys):
        """默认 dry-run：revision schema 不存在时运行后仍不存在、不写 qfq_aux.db。"""
        import scripts.audit_qfq_staleness as aud
        self._make_qfq_aux(tmp_data_root, [("510050", _ms(2026, 6, 20), 1.0)])
        aud.run_revision_audit(self._args(), ["510050"],
                                datetime(2026, 7, 23, tzinfo=BJ))
        out = capsys.readouterr().out
        assert "baseline_unavailable" in out
        assert "dry-run" in out
        # revision 表未创建
        import sqlite3
        conn = sqlite3.connect(str(tmp_data_root / "qfq_aux.db"))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert not ({"qfq_revision_run", "qfq_revision_observation", "qfq_revision_event"} & tables)

    def test_persist_end_to_end_seed_then_revision(self, tmp_data_root, capsys):
        """--persist-revision-audit：run1 seed → run2 revision，三表写入。"""
        import sqlite3
        import scripts.audit_qfq_staleness as aud
        self._make_qfq_aux(tmp_data_root, [("510050", _ms(2026, 6, 20), 1.0)])
        # run1 seed
        aud.run_revision_audit(self._args(persist_revision_audit=True), ["510050"],
                                datetime(2026, 7, 23, tzinfo=BJ))
        out1 = capsys.readouterr().out
        assert "baseline_seeded=True" in out1
        assert "正在写 qfq_aux.db" in out1
        # 改同键值 → run2 revision
        conn = sqlite3.connect(str(tmp_data_root / "qfq_aux.db"))
        conn.execute("UPDATE adj_factor SET adj_factor=1.0005 WHERE code='510050'")
        conn.commit()
        conn.close()
        aud.run_revision_audit(self._args(persist_revision_audit=True), ["510050"],
                                datetime(2026, 7, 23, tzinfo=BJ))
        out2 = capsys.readouterr().out
        assert "revised=1" in out2
        # 三表有数据
        conn = sqlite3.connect(str(tmp_data_root / "qfq_aux.db"))
        assert conn.execute("SELECT count(*) FROM qfq_revision_run WHERE status='completed'").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM qfq_revision_event").fetchone()[0] == 1
        ev = conn.execute("SELECT previous_factor,current_factor FROM qfq_revision_event").fetchone()
        assert ev == (1.0, 1.0005)
        conn.close()

    def test_persist_does_not_modify_adj_factor(self, tmp_data_root):
        """persist 后 adj_factor 行逐行不变（非 repair/write-back）。"""
        import sqlite3
        import scripts.audit_qfq_staleness as aud
        self._make_qfq_aux(tmp_data_root, [("510050", _ms(2026, 6, 20), 1.0),
                                            ("159919", _ms(2026, 6, 20), 2.0)])
        conn = sqlite3.connect(str(tmp_data_root / "qfq_aux.db"))
        before = conn.execute("SELECT code,time,adj_factor FROM adj_factor ORDER BY code").fetchall()
        conn.close()
        aud.run_revision_audit(self._args(persist_revision_audit=True),
                                ["510050", "159919"], datetime(2026, 7, 23, tzinfo=BJ))
        conn = sqlite3.connect(str(tmp_data_root / "qfq_aux.db"))
        after = conn.execute("SELECT code,time,adj_factor FROM adj_factor ORDER BY code").fetchall()
        conn.close()
        assert before == after  # persist 未动 adj_factor

    def test_revision_epsilon_printed_and_shared(self, tmp_data_root, capsys):
        """--revision-epsilon 打印且 dry-run/persist 共用，写入 run.epsilon。"""
        import sqlite3
        import scripts.audit_qfq_staleness as aud
        self._make_qfq_aux(tmp_data_root, [("510050", _ms(2026, 6, 20), 1.0)])
        aud.run_revision_audit(self._args(persist_revision_audit=True, revision_epsilon=0.01),
                                ["510050"], datetime(2026, 7, 23, tzinfo=BJ))
        out = capsys.readouterr().out
        assert "epsilon=0.01" in out
        conn = sqlite3.connect(str(tmp_data_root / "qfq_aux.db"))
        eps = conn.execute("SELECT epsilon FROM qfq_revision_run").fetchone()[0]
        conn.close()
        assert eps == 0.01

    def test_negative_epsilon_rejected_at_parse(self, tmp_data_root):
        """CLI parse 阶段拒绝负 epsilon（argparse parser.error → SystemExit）。"""
        import subprocess, sys
        proc = subprocess.run(
            [sys.executable, "scripts/audit_qfq_staleness.py", "--revision-epsilon", "-1"],
            cwd=str(_ROOT), capture_output=True, text=True)
        assert proc.returncode != 0
        assert "revision-epsilon" in proc.stderr or "revision-epsilon" in proc.stdout
