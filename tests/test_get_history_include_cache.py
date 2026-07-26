"""P1 正确性修复回归测试：get_history 缓存键遗漏 include（不依赖真实数据库）。

设计约束（对应 P1 隔离性要求）：
- 每个测试创建独立的 PtradeAPI 实例，不使用模块级全局 _api；
- 使用内存 FakeMarketDataProvider 返回确定性 DataFrame，不连 DuckDB；
- Fake Provider 记录调用次数、anchor_date、证券列表与参数；
- 核心测试绝不因数据库缺失而 skip（无数据库集成测试、无 pyproject 改动）；
- 用 pytest fixture 自动隔离 Provider，无测试顺序依赖、无未恢复副作用；
- 日线/分钟清缓存测试用轻量 FakeEngine 桩，不创建真实数据库连接。

覆盖：
1. False→True 两次查询及正确锚点（prev_date / current_date）；
2. True→False 两次查询及正确锚点；
3. 相同 include=False 重复调用只查询一次；
4. 相同 include=True 重复调用只查询一次；
5. 单证券 / 多证券 / is_dict=True/False 返回结构不变；
6. attach_day 后缓存失效（重查）；
7. attach_bar 后缓存失效（重查）；
8. 固定单一 include 路径结果稳定（缓存命中 + 返回契约）。
"""
import copy

import pandas as pd
import pytest

from quantstudio.backtest.ptrade_api import PtradeAPI, CodeDict


# ========== Fake Provider（内存、确定性）==========

class FakeMarketDataProvider:
    """轻量内存 Provider：记录 get_bars_by_count 的调用。

    get_bars_by_count(codes, count, end_date(anchor_date), start_date, fq, frequency, bar_cutoff_ms)
    位置参数：
        [0] codes(list[bare])
        [1] count
        [2] end_date(anchor_date)
        [3] start_date
        [4] fq
        [5] frequency
        [6] bar_cutoff_ms
    """

    def __init__(self):
        self.calls = []          # 每次调用的完整记录
        self._rows_per_code = {} # bare_code -> list[(time_ms, close)]

    def set_history(self, bare_code, closes, base_date="2026-04-28", day_ms=86_400_000):
        """构造确定性历史：closes 升序，最新一日 = base_date。"""
        self._rows_per_code[bare_code] = []
        for i, c in enumerate(reversed(closes)):  # 倒序先放最新
            t = _date_ms(base_date) - i * day_ms
            self._rows_per_code[bare_code].append((t, c))
        self._rows_per_code[bare_code].reverse()  # 恢复时间升序

    def get_bars_by_count(self, codes, count, end_date=None, start_date=None,
                          fq="pre", frequency="1d", bar_cutoff_ms=None):
        self.calls.append({
            "codes": list(codes),
            "count": count,
            "anchor_date": end_date,
            "fq": fq,
            "frequency": frequency,
        })
        result = {}
        for code in codes:
            rows = self._rows_per_code.get(code, [])
            selected = [r for r in rows if r[0] <= _date_ms(end_date)] if end_date else rows
            selected = selected[-count:] if count else selected
            df = pd.DataFrame({
                "time": [r[0] for r in selected],
                "close": [float(r[1]) for r in selected],
            })
            df.index = range(-len(df), 0)
            result[code] = df
        return result

    def preload(self, *a, **k):
        return None


def _date_ms(date_str):
    """'YYYY-MM-DD' -> 当日 00:00 毫秒（确定性比较用）。"""
    from datetime import datetime
    return int(datetime.strptime(date_str, "%Y-%m-%d").timestamp()) * 1000


# ========== Fixtures ==========

@pytest.fixture
def api():
    """独立 PtradeAPI 实例 + Fake Provider（自动隔离，无 DuckDB 连接）。"""
    inst = PtradeAPI()
    fake = FakeMarketDataProvider()
    fake.set_history("600519", [10.0, 10.1, 10.2, 10.3, 10.4, 10.5])
    fake.set_history("000001", [5.0, 5.1, 5.2, 5.3, 5.4, 5.5])
    inst._market = fake
    inst._engine = _FakeEngine()
    return inst


class _FakeEngine:
    """轻量引擎桩：仅提供 get_history 锚点判断所需的 engine_profile 与 config。"""
    engine_profile = "daily-bar-v1"
    config = None


@pytest.fixture
def day_api(api):
    """已 attach_day 的 api（日线 Profile，prev=2026-04-27, cur=2026-04-28）。"""
    api.attach_day(_FakeEngine(), None, None, "2026-04-28", "2026-04-27", {})
    api._market = api._market if api._market is not None else FakeMarketDataProvider()
    return api


@pytest.fixture
def bar_api(api):
    """已 attach_bar 的 api（分钟 Profile，每 bar 注入）。"""
    api.attach_bar(_FakeEngine(), None, "2026-04-28", "2026-04-27", {},
                   current_bar_ts="2026-04-28 09:35:00")
    api._market = api._market if api._market is not None else FakeMarketDataProvider()
    return api


# ========== 1) 混合 include 必须各查一次 ==========

def test_include_false_then_true_executes_two_queries(day_api):
    """P1-1：同交易日 include=False 后 include=True，底层查询 2 次，锚点 prev/current。"""
    api = day_api
    api._query_cache = {}
    api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=False)
    api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=True)
    assert len(api._market.calls) == 2
    assert api._market.calls[0]["anchor_date"] == "2026-04-27"  # include=False
    assert api._market.calls[1]["anchor_date"] == "2026-04-28"  # include=True


def test_include_true_then_false_executes_two_queries(day_api):
    """P1-2：反向顺序 include=True 再 include=False，底层查询 2 次且锚点仍正确。"""
    api = day_api
    api._query_cache = {}
    api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=True)
    api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=False)
    assert len(api._market.calls) == 2
    assert api._market.calls[0]["anchor_date"] == "2026-04-28"  # include=True
    assert api._market.calls[1]["anchor_date"] == "2026-04-27"  # include=False


# ========== 3/4) 同 include 重复调用只查一次 ==========

def test_same_include_false_repeated_only_one_query(day_api):
    """P1-3：相同 include=False 重复调用，底层只查一次（缓存性能不退化）。"""
    api = day_api
    api._query_cache = {}
    r1 = api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=False)
    r2 = api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=False)
    assert len(api._market.calls) == 1
    pd.testing.assert_frame_equal(r1, r2)


def test_same_include_true_repeated_only_one_query(day_api):
    """P1-4：相同 include=True 重复调用，底层只查一次。"""
    api = day_api
    api._query_cache = {}
    r1 = api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=True)
    r2 = api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=True)
    assert len(api._market.calls) == 1
    pd.testing.assert_frame_equal(r1, r2)


# ========== 5) 返回结构不变 ==========

def test_return_structure_single_multi_isdict(day_api):
    """P1-5：单/多证券、is_dict True/False 返回类型与修复前一致。"""
    api = day_api
    api._query_cache = {}

    single = api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=False)
    assert isinstance(single, pd.DataFrame)
    assert not isinstance(single, CodeDict)

    multi_false = api.get_history(["600519.SH", "000001.SZ"], 5, "1d",
                                  ["close"], fq="pre", include=False, is_dict=False)
    assert isinstance(multi_false, pd.DataFrame)
    assert not isinstance(multi_false, CodeDict)

    multi_true = api.get_history(["600519.SH", "000001.SZ"], 5, "1d",
                                 ["close"], fq="pre", include=False, is_dict=True)
    assert isinstance(multi_true, CodeDict)
    bare_keys = {str(k).split(".")[0] for k in multi_true.keys()}
    assert bare_keys >= {"600519", "000001"}


# ========== 6) 日线不跨日复用 ==========

def test_daily_no_cross_day_cache(day_api):
    """P1-6：attach_day（每日清缓存）后相同参数会重新查询。"""
    api = day_api
    api._query_cache = {}
    api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=False)
    n_after_first = len(api._market.calls)
    api.attach_day(_FakeEngine(), None, None, "2026-04-29", "2026-04-28", {})
    api._market = api._market if api._market is not None else FakeMarketDataProvider()
    api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=False)
    assert len(api._market.calls) == n_after_first + 1


# ========== 7) 分钟不跨 bar 复用 ==========

def test_minute_no_cross_bar_cache(bar_api):
    """P1-7：attach_bar（每 bar 清缓存）后相同参数会重新查询。"""
    api = bar_api
    api._query_cache = {}
    api.get_history("600519.SH", 5, "1m", ["close"], fq="pre", include=False)
    n_after_first = len(api._market.calls)
    api.attach_bar(_FakeEngine(), None, "2026-04-28", "2026-04-27", {},
                   current_bar_ts="2026-04-28 09:36:00")
    api._market = api._market if api._market is not None else FakeMarketDataProvider()
    api.get_history("600519.SH", 5, "1m", ["close"], fq="pre", include=False)
    assert len(api._market.calls) == n_after_first + 1


# ========== 8) 单 include 路径结果稳定 ==========

def test_single_include_path_result_stable(day_api):
    """P1-8：仅用固定 include 的结果稳定（缓存命中 + 索引/字段契约）。

    说明：本测试验证修复后固定 include 的重复执行结果一致及返回契约，
    不构成修复前/后双版本黄金对比（项目尚无正式黄金回测快照）。
    """
    api = day_api
    api._query_cache = {}
    r1 = api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=False)
    r2 = copy.deepcopy(api.get_history("600519.SH", 5, "1d", ["close"], fq="pre", include=False))
    pd.testing.assert_frame_equal(r1, r2)
    assert list(r1.index) == [-5, -4, -3, -2, -1]
    assert "close" in r1.columns
