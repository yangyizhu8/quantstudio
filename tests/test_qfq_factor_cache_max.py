# -*- coding: utf-8 -*-
"""tests/test_qfq_factor_cache_max.py

因子查询缓存容量 env 化（2026-11-11 节点⑳·总调度裁甲）的单元测试。

背景：`_QFQ_FACTOR_CACHE_MAX` 原硬编码 8，但实测一轮 13 shard × 5 表 = 65 个不同 key
（key 含 lo_ms/hi_ms 时间窗）⇒ 容量 8 < 需求 65 ⇒ 写入即 evict ⇒ hit_rate 恒 0%
（日志：hit=0 miss=65 evict=57 hit_rate=0.0% size=8）。

本测试只验证 **容量解析** 这一段（纯函数级，不构造 ResidentCollector 全实例）：
  V1 默认（env 未设）→ 512
  V2 env=8 → 8（可退回改动前行为）
  V3 env=65 → 65（恰好覆盖一轮需求）
  V4 env 非法（非数字）→ 回落 512（fail-safe）
  V5 env=0 / 负数 → 下限 1（不出现 0 容量导致的无限 evict）
  V6 env 空串 → 512
"""
import pytest

from quantstudio.pipeline.daemon import ResidentCollector

ENV = "QS_QFQ_FACTOR_CACHE_MAX"


@pytest.fixture
def rc():
    """不跑 __init__（避免读配置/连库），只取纯方法。"""
    return object.__new__(ResidentCollector)


def test_v1_default_512(rc, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert rc._qfq_factor_cache_max() == 512


def test_v2_env_8_reverts(rc, monkeypatch):
    """回退路径：置 8 = 改动前行为。"""
    monkeypatch.setenv(ENV, "8")
    assert rc._qfq_factor_cache_max() == 8


def test_v3_env_65_covers_one_round(rc, monkeypatch):
    """一轮 13 shard × 5 表 = 65 个 key，容量 65 恰好够。"""
    monkeypatch.setenv(ENV, "65")
    assert rc._qfq_factor_cache_max() == 65


def test_v4_invalid_falls_back(rc, monkeypatch):
    """非法值 fail-safe 回落默认，不得抛异常。"""
    monkeypatch.setenv(ENV, "abc")
    assert rc._qfq_factor_cache_max() == 512


def test_v5_zero_or_negative_floor_one(rc, monkeypatch):
    """0/负数 → 下限 1（容量 0 会导致每次写入即 evict，比 8 更糟）。"""
    monkeypatch.setenv(ENV, "0")
    assert rc._qfq_factor_cache_max() == 1
    monkeypatch.setenv(ENV, "-5")
    assert rc._qfq_factor_cache_max() == 1


def test_v6_empty_string_uses_default(rc, monkeypatch):
    monkeypatch.setenv(ENV, "   ")
    assert rc._qfq_factor_cache_max() == 512


def test_v7_old_constant_preserved(rc):
    """旧常量保留（回退语义/外部引用兼容），值仍为 8。"""
    assert ResidentCollector._QFQ_FACTOR_CACHE_MAX == 8
    assert ResidentCollector._QFQ_FACTOR_CACHE_MAX_DEFAULT == 512
