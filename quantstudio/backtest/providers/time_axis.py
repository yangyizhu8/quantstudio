"""时间轴唯一真相源（CST 日界）。

背景（2026-09-18 修复）：
    `preload_daily_snapshots` 曾用 `time // 86_400_000 * 86_400_000`（UTC 日界截断）
    生成缓存键，而查询侧 `query_daily_snapshot` 用的是 `_start_ms(date)`
    （Asia/Shanghai 日界，mod 86_400_000 == 57_600_000）——两个键空间交集为空，
    预取结果 100% 未被消费，每个交易日退化为一次全市场窗口扫描。

    该缺陷的根因是**日界语义在仓内被重复表达**（一处 UTC、一处 CST）。
    本模块把 CST 日界的定义收敛到唯一一处：预取键、查询键、防御判据全部
    由这里的函数派生，从结构上消除同类漂移。

约束：
    - 本模块只依赖 pandas，**不得**反向依赖 duckdb_provider / duckdb_data_access
      （防循环导入）。
    - start_ms 的实现逐字符保持原 duckdb_provider._start_ms：
      `pd.Timestamp(date, tz='Asia/Shanghai')`，**不带任何 [:10] 截断**——
      截断留在原调用点（duckdb_provider.preload），带时刻入参行为零变化。
"""
from __future__ import annotations

import pandas as pd

DAY_MS = 86_400_000
CST_OFFSET_MS = 28_800_000  # Asia/Shanghai = UTC+8


def start_ms(date: str) -> int:
    """日期 → 该日 CST（Asia/Shanghai）零点的 epoch ms。

    实现与签名**逐字符等价**于原 `duckdb_provider._start_ms`（含类型标注）。
    """
    return int(pd.Timestamp(date, tz='Asia/Shanghai').timestamp() * 1000)


def end_ms(date: str) -> int:
    """日期 → 该日 CST 最后一个毫秒（当日 23:59:59.999）的 epoch ms。"""
    return start_ms(date) + DAY_MS - 1


def day_start_ms(ms):
    """任意 epoch ms（标量 int 或 pandas Series）→ 其所属 CST 自然日的零点 epoch ms。

    **标量与向量共用同一表达式**，保证 DataFrame 列归一（预取路径）与单值判据
    （防御分支）两条路径语义恒等，且日界算式在本仓只此一处。

    性质（由 tests/test_daily_snapshot_cache_key.py::T-1 锁定）：
        对 CST 日界值幂等：day_start_ms(start_ms(d)) == start_ms(d)；
        对日内非零点归并：day_start_ms(start_ms(d) + 8h) == start_ms(d)。
    """
    return (ms + CST_OFFSET_MS) // DAY_MS * DAY_MS - CST_OFFSET_MS


def is_day_start_ms(ms) -> bool:
    """`ms` 本身是否恰为某个 CST 日界（标量）。"""
    return int(ms) == int(day_start_ms(int(ms)))
