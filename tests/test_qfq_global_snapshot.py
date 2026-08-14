"""QFQ 复权基准 bug 修复的单元测试（2026-08-14）。

覆盖 reasonix 审核要求的 3 条用例：
1. 分片不含最新因子：有快照时 front 用全局基准（非批次内）
2. 无快照 fail-fast：调用方未传快照时 aligner 必须 raise
3. per_stock 路径级：daemon align 调用必须传快照（_qfq_snapshot_kwargs 展开）
"""
import pytest
import pandas as pd
import numpy as np

from quantstudio.pipeline.aligner import FieldAligner


def _make_qfq_inputs():
    """构造：code X 因子 1.0（05月）→ 2.0（07月拆分），分片只含 05 月数据。"""
    # 价格数据（分片只含 2026-05，拆分前）
    df = pd.DataFrame({
        "code": ["X"] * 3,
        "time": [1747353600000, 1747440000000, 1747526400000],  # 2026-05-16/17/18
        "open": [10.0, 10.2, 10.1],
        "high": [10.5, 10.6, 10.4],
        "low": [9.8, 10.0, 9.9],
        "close": [10.2, 10.3, 10.1],
    })
    # 因子数据（分片窗口内：05 月因子=1.0；注意不含 07 月的 2.0）
    adj_df = pd.DataFrame({
        "code": ["X"] * 3,
        "time": [1747353600000, 1747440000000, 1747526400000],
        "adj_factor": [1.0, 1.0, 1.0],
    })
    return df, adj_df


class TestApplyQfqShardWithoutLatestFactor:
    """用例 1：分片不含最新因子时，front 必须用全局快照基准。"""

    def test_with_global_snapshot_front_uses_global_anchor(self):
        """分片因子=1.0，全局 adj_latest=2.0 → front = raw × (1.0/2.0) = raw/2。"""
        df, adj_df = _make_qfq_inputs()
        aligner = FieldAligner.__new__(FieldAligner)
        aligner.schemas = {"etf_daily": {"time_key": "time"}}

        out = aligner._apply_qfq(
            df.copy(), adj_df, "etf_daily",
            adj_latest_map={"X": 2.0},      # 全局最新因子（07 月拆分后）
            adj_earliest_map={"X": 1.0})

        # front = raw × adj_i / adj_latest = 10.2 × 1.0 / 2.0 = 5.1
        assert abs(out["close_front"].iloc[0] - 5.1) < 1e-9
        assert abs(out["open_front"].iloc[0] - 5.0) < 1e-9
        # back = raw × adj_i / adj_earliest = raw × 1.0
        assert abs(out["close_back"].iloc[0] - 10.2) < 1e-9

    def test_without_snapshot_raises_fail_fast(self):
        """用例 2：无快照时必须 raise（禁止批次内 groupby 基准）。"""
        df, adj_df = _make_qfq_inputs()
        aligner = FieldAligner.__new__(FieldAligner)
        aligner.schemas = {"etf_daily": {"time_key": "time"}}

        with pytest.raises(ValueError, match="adj_latest_map"):
            aligner._apply_qfq(df.copy(), adj_df, "etf_daily")

    def test_bug_scenario_front_equals_raw_without_snapshot(self):
        """回归验证（bug 场景重放）：批次内基准会让 front=raw——正是修复要防的。

        该测试证明旧逻辑（groupby last）在分片窗口内算出的 adj_latest=1.0，
        front = raw × 1.0/1.0 = raw（错误）。修复后该路径直接 raise。
        """
        df, adj_df = _make_qfq_inputs()
        # 模拟旧 bug：批次内 groupby 得 adj_latest=1.0（分片最后一天因子）
        # → front = 10.2 × 1.0/1.0 = 10.2 = raw ❌（这就是 1442 万行被破坏的模式）
        anchors = adj_df.sort_values("time").groupby("code")["adj_factor"].agg(
            adj_latest="last")
        batch_anchor = float(anchors.iloc[0])  # 唯一 code X 的批次内基准
        assert batch_anchor == 1.0  # 批次内基准确实是 1.0（不是全局 2.0）
        buggy_front = 10.2 * 1.0 / batch_anchor
        assert buggy_front == 10.2  # bug 值 = raw
        # 正确值（全局基准）应为 5.1
        assert buggy_front != 5.1


class TestDaemonSnapshotKwargs:
    """用例 3：daemon._qfq_snapshot_kwargs 路径级测试。"""

    def test_price_table_returns_snapshot_kwargs(self, tmp_path, monkeypatch):
        """价格表必须返回含 adj_latest_map 的 kwargs。"""
        import sqlite3
        from quantstudio.pipeline.daemon import ResidentCollector

        # 构造 mock qfq_aux.db
        aux = tmp_path / "qfq_aux.db"
        conn = sqlite3.connect(str(aux))
        conn.execute("CREATE TABLE fund_adj (code TEXT, time INTEGER, adj_factor REAL)")
        conn.execute("INSERT INTO fund_adj VALUES ('159995', 1, 1.9993)")
        conn.execute("INSERT INTO fund_adj VALUES ('510300', 1, 1.5)")
        conn.commit()
        conn.close()

        daemon = ResidentCollector.__new__(ResidentCollector)

        class FakeWriter:
            db_path = str(tmp_path / "quantstudio.db")

        daemon.writer = FakeWriter()
        monkeypatch.setattr(
            "quantstudio.pipeline.qfq_reanchor_schema.aux_db_path",
            lambda main_db: aux)

        kwargs = daemon._qfq_snapshot_kwargs("etf_daily", "test_batch")
        assert "adj_latest_map" in kwargs
        assert "adj_earliest_map" in kwargs
        assert kwargs["adj_latest_map"].get("159995") == pytest.approx(1.9993)

    def test_non_price_table_returns_empty(self, tmp_path):
        """非价格表返回空 dict（align 直通，不触发 QFQ）。"""
        from quantstudio.pipeline.daemon import ResidentCollector

        daemon = ResidentCollector.__new__(ResidentCollector)
        kwargs = daemon._qfq_snapshot_kwargs("etf_basic", "test_batch")
        assert kwargs == {}

    def test_aux_missing_returns_empty_maps_for_fail_fast(self, tmp_path, monkeypatch):
        """qfq_aux.db 不存在时返回空 map——aligner 将 fail-fast（防写坏）。"""
        from quantstudio.pipeline.daemon import ResidentCollector

        daemon = ResidentCollector.__new__(ResidentCollector)

        class FakeWriter:
            db_path = str(tmp_path / "quantstudio.db")

        daemon.writer = FakeWriter()
        monkeypatch.setattr(
            "quantstudio.pipeline.qfq_reanchor_schema.aux_db_path",
            lambda main_db: tmp_path / "nonexistent.db")

        kwargs = daemon._qfq_snapshot_kwargs("etf_daily", "test_batch")
        assert kwargs == {"adj_latest_map": {}, "adj_earliest_map": {}}
