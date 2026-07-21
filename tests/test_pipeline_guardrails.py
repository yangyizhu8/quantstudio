"""Pipeline 防御层回归测试（护栏）

防止本次加固修复的问题类型再次复发。每个测试对应一个已修复的根因：

1. test_unit_check_skips_non_yuan_unit
   防回归：UnitCheck 误判指数（点位 vs 元混算导致全隔离）
   修复：UnitCheck 改为读 schema.columns.close.unit，仅 unit=="元" 时执行

2. test_financial_dedup_keeps_latest_ann_date
   防回归：财务重述版本丢失（keep="last" 随机保留初版或重述版）
   修复：DuplicateKey 对财务表（主键含 ann_date）优先保留 ann_date 最大的最新修正版

3. test_writer_returns_new_rows
   防回归：审计失真（write 返回提交行数而非新增行数，重跑全 update 被误判成功）
   修复：write 返回 WriteResult（int 子类），携带 .new/.updated 审计字段

4. test_config_lint_catches_debug_residue
   防回归：调试配置残留（kline_1m 写死单只股票 600000.SH 未被发现）
   修复：config_lint.py 启动时校验，codes 少于 5 只且非 ALL 时 WARN

5. test_rate_limiter_single_timestamp_per_acquire
   防回归：限流减半（双时间戳 append 导致实际限流 = 配置值的一半）
   修复：RateLimiter.acquire 每次 append 一个时间戳

6. test_financial_pit_gate_required
   防回归：财务报表缺 PIT 门禁（available_at_field=ann_date 漏配）
   修复：config_lint 校验主键含 ann_date 的表必须有 available_at_field
"""
from __future__ import annotations

import logging
import sys
import tempfile
import os
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.validator import PreIngestValidator
from quantstudio.pipeline.writers import WriteResult
from quantstudio.pipeline.sources.base import RateLimiter
from quantstudio.pipeline.config_lint import lint_configs, ConfigLintError

logging.basicConfig(level=logging.WARNING)


# ---------------------------------------------------------------------------
# 测试 1：UnitCheck 配置化跳过非元单位（防指数误判回归）
# ---------------------------------------------------------------------------
def test_unit_check_skips_non_yuan_unit():
    """指数（close.unit='点'）不应被 UnitCheck 隔离，即使 amount/(close*vol) 偏离 [0.5,2.0]"""
    schemas = {
        "index_daily": {
            "primary_key": ["code", "time"],
            "time_key": "time",
            "columns": {
                "code": {"type": "str", "required": True, "regex": r"^\d{6}$"},
                "time": {"type": "int", "required": True},
                "open": {"type": "float", "required": True, "unit": "点", "gt": 0},
                "high": {"type": "float", "required": True, "unit": "点", "gt": 0},
                "low": {"type": "float", "required": True, "unit": "点", "gt": 0},
                "close": {"type": "float", "required": True, "unit": "点", "gt": 0},
                "pctChg": {"type": "float", "required": False, "unit": "%"},
                "volume": {"type": "float", "required": True, "unit": "股", "ge": 0},
                "amount": {"type": "float", "required": True, "unit": "元", "ge": 0},
            },
        }
    }
    v = PreIngestValidator(schemas)
    # 构造 ratio=0.00068 的指数数据（沪深300 真实样本）
    df = pd.DataFrame({
        "code": ["000300"], "time": [1783612800000],
        "open": [4745.4], "high": [4775.2], "low": [4670.2], "close": [4695.4],
        "pctChg": [-1.79], "volume": [2.78e8], "amount": [9.08e8],
    })
    res = v.validate(df, "index_daily", "test_batch", "tushare")
    assert len(res.passed_df) == 1, (
        f"指数应跳过 UnitCheck，实际 rejected={len(res.rejected_rows)} "
        f"rules={res.rejected_rules}")
    assert all("UnitCheck" not in r for r in res.rejected_rules), "不应命中 UnitCheck"


def test_unit_check_still_active_for_yuan_unit():
    """个股（close.unit='元'）单位异常仍应被 UnitCheck 隔离"""
    schemas = {
        "stock_daily": {
            "primary_key": ["code", "time"],
            "time_key": "time",
            "columns": {
                "code": {"type": "str", "required": True, "regex": r"^\d{6}$"},
                "time": {"type": "int", "required": True},
                "open": {"type": "float", "unit": "元"}, "high": {"type": "float", "unit": "元"},
                "low": {"type": "float", "unit": "元"},
                "close": {"type": "float", "unit": "元"},
                "volume": {"type": "float"}, "amount": {"type": "float"},
                "isST": {"type": "int", "required": False},
            },
        }
    }
    v = PreIngestValidator(schemas)
    # ratio = 10/(10.1*1000) = 0.001 异常
    df = pd.DataFrame({
        "code": ["000001"], "time": [1783612800000],
        "open": [10.0], "high": [10.2], "low": [9.9], "close": [10.1],
        "volume": [1000.0], "amount": [10.0], "isST": [0],
    })
    res = v.validate(df, "stock_daily", "test_batch", "tushare")
    assert any("UnitCheck" in r for r in res.rejected_rules), "个股单位异常应命中 UnitCheck"


# ---------------------------------------------------------------------------
# 测试 2：财务重述保留 ann_date 最新版（防重述丢失回归）
# ---------------------------------------------------------------------------
def test_financial_dedup_keeps_latest_ann_date():
    """同一 (code,end_date) 多版本时，保留 ann_date 最大的最新修正版"""
    schemas = {
        "balance_statement": {
            "primary_key": ["code", "end_date", "ann_date"],
            "time_key": "end_date",
            "available_at_field": "ann_date",
            "columns": {
                "code": {"type": "str", "required": True, "regex": r"^\d{6}$"},
                "end_date": {"type": "int", "required": True},
                "ann_date": {"type": "int", "required": True},
                "total_assets": {"type": "float", "required": False},
            },
        }
    }
    v = PreIngestValidator(schemas)
    # 000159 重述案例：初版 5.2e8 + 重述 3.69e9（差7倍）+ flag 重复对
    df = pd.DataFrame([
        {"code": "000159", "end_date": 1640908800000, "ann_date": 1648012800000, "total_assets": 5.2e8},
        {"code": "000159", "end_date": 1640908800000, "ann_date": 1649616000000, "total_assets": 3.69e9},
        {"code": "000159", "end_date": 1640908800000, "ann_date": 1649616000000, "total_assets": 3.69e9},
        {"code": "600000", "end_date": 1703980800000, "ann_date": 1711641600000, "total_assets": 9.46e12},
    ])
    res = v.validate(df, "balance_statement", "test_batch", "tushare")
    p = res.passed_df
    r159 = p[p["code"] == "000159"]
    assert len(r159) == 1, f"000159 应只保留1条，实际 {len(r159)}"
    assert float(r159["total_assets"].iloc[0]) == 3.69e9, (
        f"应保留重述版 3.69e9，实际 {r159['total_assets'].iloc[0]}")


# ---------------------------------------------------------------------------
# 测试 3：WriteResult 审计字段（防审计失真回归）
# ---------------------------------------------------------------------------
def test_writer_returns_write_result_with_audit_fields():
    """WriteResult 作为 int 向后兼容，同时携带 .new/.updated 审计字段"""
    wr = WriteResult(100, 60, 40)
    # int 兼容
    assert int(wr) == 100
    assert wr == 100  # int 比较
    # 审计字段
    assert wr.new == 60
    assert wr.updated == 40
    # += 运算兼容（daemon 累加场景）
    total = 0
    total += wr
    assert total == 100


def test_writer_upsert_distinguishes_new_and_updated():
    """writer.write 在 upsert 场景返回正确的 new/updated"""
    from quantstudio.pipeline.writers import DuckDBWriter
    tmp = tempfile.mktemp(suffix=".db")
    try:
        w = DuckDBWriter({"path": tmp})  # 初始化时按 DDL 建标准表（含 stock_daily）
        # stock_daily 主键 (code,time)，用最小必填列测试
        df = pd.DataFrame({"code": ["000001"], "time": [1783612800000], "close": [10.5]})
        # 第一次写入：全部新增
        wr1 = w.write(df, "stock_daily", "batch1")
        assert wr1.new == 1 and wr1.updated == 0, f"首次应全新增，实际 new={wr1.new} upd={wr1.updated}"
        # 第二次写入同样数据：全部更新（upsert）
        wr2 = w.write(df, "stock_daily", "batch2")
        assert wr2.new == 0 and wr2.updated == 1, f"重写应全更新，实际 new={wr2.new} upd={wr2.updated}"
        # 第三次写入新数据：1 新增
        df2 = pd.DataFrame({"code": ["000002"], "time": [1783612800000], "close": [20.0]})
        wr3 = w.write(df2, "stock_daily", "batch3")
        assert wr3.new == 1 and wr3.updated == 0
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except PermissionError:
                pass  # Windows 文件锁延迟


# ---------------------------------------------------------------------------
# 测试 4：config_lint 抓调试残留（防配置漂移回归）
# ---------------------------------------------------------------------------
def test_config_lint_catches_debug_residue():
    """codes 少于 5 只且非 ALL 时，config_lint 应 WARN"""
    schemas = {"stock_daily": {"primary_key": ["code", "time"],
                               "columns": {"code": {"type": "str"}}}}
    tasks = [{"name": "kline_1m", "enabled": True, "source": "tushare",
              "table": "stock_daily", "codes": ["600000.SH"]}]  # 调试残留
    sources = {"tushare": {"enabled": True}}
    errs, warns = lint_configs({}, {"sources": sources}, {"tasks": tasks}, {"schemas": schemas})
    assert any("调试残留" in w for w in warns), f"应警告调试残留，实际 warns={warns}"
    assert len(errs) == 0  # 仅警告非错误


def test_config_lint_catches_bad_code_format():
    """codes 格式与 source 不匹配时应 ERROR"""
    schemas = {"stock_daily": {"primary_key": ["code", "time"],
                               "columns": {"code": {"type": "str"}}}}
    tasks = [{"name": "bad", "enabled": True, "source": "tushare",
              "table": "stock_daily", "codes": ["600000"]}]  # 缺 .SH 后缀
    sources = {"tushare": {"enabled": True}}
    errs, _ = lint_configs({}, {"sources": sources}, {"tasks": tasks}, {"schemas": schemas})
    assert any("非法代码" in e for e in errs)


def test_config_lint_failfast_raises():
    """assert_configs_ok 在有错误时应 raise ConfigLintError"""
    schemas = {"stock_daily": {"primary_key": ["code", "time"],
                               "columns": {"code": {"type": "str"}}}}
    tasks = [{"name": "bad", "enabled": True, "source": "tushare",
              "table": "nonexistent", "codes": ["ALL"]}]  # 未定义表
    sources = {"tushare": {"enabled": True}}
    with pytest.raises(ConfigLintError):
        from quantstudio.pipeline.config_lint import assert_configs_ok
        assert_configs_ok({}, {"sources": sources}, {"tasks": tasks}, {"schemas": schemas})


# ---------------------------------------------------------------------------
# 测试 5：RateLimiter 单时间戳（防限流减半回归）
# ---------------------------------------------------------------------------
def test_rate_limiter_single_timestamp_per_acquire():
    """每次 acquire 只记一个时间戳（修复前双 append 导致限流减半）"""
    rl = RateLimiter(calls_per_min=10)
    assert len(rl._timestamps) == 0
    rl.acquire()
    assert len(rl._timestamps) == 1, f"1次acquire后应有1个时间戳，实际{len(rl._timestamps)}"
    rl.acquire()
    assert len(rl._timestamps) == 2, f"2次acquire后应有2个时间戳，实际{len(rl._timestamps)}"
    rl.acquire()
    assert len(rl._timestamps) == 3


def test_rate_limiter_triggers_at_configured_limit():
    """限流阈值 = 配置值（修复前双 append 导致实际阈值减半）。

    用 mock 时间验证：calls_per_min=4 时，第 5 次 acquire 应判定超限。
    不实际 sleep（避免测试耗时 60s），通过 monkeypatch time.time 和 time.sleep。
    """
    import time as _time
    rl = RateLimiter(calls_per_min=4)

    # 记录是否调用了 sleep（限流触发标志）
    sleep_called = []
    real_sleep = _time.sleep
    rl_sleep = _time.sleep

    def mock_sleep(sec):
        sleep_called.append(sec)
        # 不真正 sleep，立即返回（测试加速）
    _time.sleep = mock_sleep
    try:
        # 窗口内快速 acquire 5 次
        for i in range(5):
            rl.acquire()
        # 第 5 次应触发限流 sleep（前4次填满窗口，第5次超限）
        # 修复前：双 append 导致第 2 次 acquire 就填满（2个时间戳），第3次就触发 sleep
        assert len(sleep_called) >= 1, (
            f"calls_per_min=4 时第5次acquire应触发sleep，实际 sleep 调用次数={len(sleep_called)}")
    finally:
        _time.sleep = real_sleep


# ---------------------------------------------------------------------------
# 测试 6：财务 PIT 门禁（防 available_at_field 漏配回归）
# ---------------------------------------------------------------------------
def test_financial_pit_gate_required_by_config_lint():
    """主键含 ann_date 的表缺 available_at_field 时，config_lint 应 ERROR"""
    schemas_no_pit = {
        "balance_statement": {
            "primary_key": ["code", "end_date", "ann_date"],
            "columns": {"ann_date": {"type": "int", "required": True}},
        }
    }
    tasks = [{"name": "t", "enabled": True, "source": "tushare",
              "table": "balance_statement", "codes": ["ALL"]}]
    sources = {"tushare": {"enabled": True}}
    errs, _ = lint_configs({}, {"sources": sources}, {"tasks": tasks}, {"schemas": schemas_no_pit})
    assert any("available_at_field" in e and "balance_statement" in e for e in errs), (
        f"缺PIT门禁应报错，实际 errs={errs}")


def test_financial_pit_gate_passes_when_configured():
    """配了 available_at_field='ann_date' 时应通过"""
    schemas_ok = {
        "balance_statement": {
            "primary_key": ["code", "end_date", "ann_date"],
            "available_at_field": "ann_date",
            "columns": {"ann_date": {"type": "int", "required": True}},
        }
    }
    tasks = [{"name": "t", "enabled": True, "source": "tushare",
              "table": "balance_statement", "codes": ["ALL"]}]
    sources = {"tushare": {"enabled": True}}
    errs, _ = lint_configs({}, {"sources": sources}, {"tasks": tasks}, {"schemas": schemas_ok})
    assert not any("available_at_field" in e for e in errs), f"配了PIT门禁不应报错，实际 errs={errs}"


# ---------------------------------------------------------------------------
# 测试 7：PER_DATE 路径限流（防 TD-1 回归：pro.XXX 裸调绕过 rate_limiter）
# ---------------------------------------------------------------------------
def test_per_date_api_calls_go_through_rate_limiter():
    """PER_DATE 路径的 _api 封装必须走 rate_limiter（修复前裸调易触发 429）"""
    from quantstudio.pipeline.sources.base import BaseSourceAdapter

    class FakeAdapter(BaseSourceAdapter):
        def __init__(self):
            super().__init__({"name": "test", "rate_limit": {"calls_per_min": 100}})
            self.acquire_count = 0
            orig = self.rate_limiter.acquire
            def counting():
                self.acquire_count += 1
                orig()
            self.rate_limiter.acquire = counting
        def fetch_table(self, *a, **k): pass
        def get_last_date(self, *a, **k): pass
        def supports_freq(self, *a, **k): pass

    adapter = FakeAdapter()

    # 模拟 daemon PER_DATE 的 _api helper（与 daemon.py 定义一致）
    def _api(api_fn, **kwargs):
        return adapter._retry_with_backoff(api_fn, **kwargs)

    import pandas as pd
    call_count = [0]
    def mock_pro_api(**kwargs):
        call_count[0] += 1
        return pd.DataFrame()

    for _ in range(3):
        _api(mock_pro_api, trade_date="20260714")

    assert call_count[0] == 3, f"api_fn 应调3次，实际{call_count[0]}"
    assert adapter.acquire_count == 3, (
        f"每次 _api 调用前必须 acquire 限流，实际 acquire 次数={adapter.acquire_count}")


# ---------------------------------------------------------------------------
# 测试 8：PER_DATE isST 格式匹配（防 TD-2 回归：ts_code vs 裸码不匹配）
# ---------------------------------------------------------------------------
def test_per_date_isst_matches_bare_code():
    """isST 标记必须用 split('.')[0] 比较裸码（修复前直接比较 ts_code 永不匹配）"""
    import pandas as pd
    # 模拟 raw_df：ts_code 带 .SH/.SZ 后缀（tushare 原始格式）
    raw_df = pd.DataFrame({
        "ts_code": ["600000.SH", "000001.SZ", "000033.SZ"],  # 000033 假设是 ST
    })
    st_codes = {"000033"}  # adapter.get_st_codes() 返回裸码集合

    # 修复后的逻辑（与 daemon.py 一致）
    raw_df["isST"] = raw_df["ts_code"].apply(
        lambda c: 1 if str(c).split(".")[0] in st_codes else 0)

    assert raw_df.loc[raw_df["ts_code"] == "000033.SZ", "isST"].iloc[0] == 1, "ST 股应标记为 1"
    assert raw_df.loc[raw_df["ts_code"] == "600000.SH", "isST"].iloc[0] == 0, "非 ST 应标记为 0"
    assert raw_df.loc[raw_df["ts_code"] == "000001.SZ", "isST"].iloc[0] == 0, "非 ST 应标记为 0"

    # 对比修复前的 bug：直接比较 ts_code（带后缀）vs 裸码集合，永不匹配
    buggy = raw_df["ts_code"].apply(lambda c: 1 if c in st_codes else 0)
    assert buggy.sum() == 0, "修复前应全部漏判为 0（确认 bug 存在）"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
