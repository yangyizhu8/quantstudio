# -*- coding: utf-8 -*-
"""WP6.1/WP6.2 consume_whitelist 守卫单测（2026-08-20，计划 v2）。

模块 stdlib-only，测试直接按文件路径加载（不依赖 quantstudio 包安装），
与仓库其它测试共存但零导入耦合。

三类 fail-closed 场景（契约 §2）+ 放行场景 + 归一/表级校验。
运行：python -m pytest tests/test_consume_whitelist_guard.py -v
"""
import json
import sys
from pathlib import Path

import pytest

_SOURCES = Path(__file__).resolve().parents[1] / "quantstudio" / "pipeline" / "sources"
sys.path.insert(0, str(_SOURCES))

import consume_whitelist_guard as g  # noqa: E402
from consume_whitelist_guard import (  # noqa: E402
    ConsumeWhitelistError, assert_minute_consumable)


def _wl_entry(enabled=True, tables=("stock_minutes",), verdict="PASS"):
    return {
        "tables": list(tables),
        "enabled": enabled,
        "state": "RELEASED" if enabled else "ISOLATED",
        "probe": {"verdict": verdict,
                  "report": "data/probe_reports/x.json",
                  "probed_at": "2026-08-18T03:00:00+08:00",
                  "window": ["20250101", "20260818"]},
        "released_at": "2026-08-18T03:12:00+08:00" if enabled else None,
    }


def _wl_file(tmp_path, codes=None, residual=None, **override):
    doc = {
        "version": 1,
        "updated_at": "2026-08-18T03:12:00+08:00",
        "updated_by": "unit-test",
        "fail_closed": True,
        "semantics": "unit test fixture",
        "codes": codes if codes is not None else {
            "000858.SZ": _wl_entry(),
            "510300.SH": _wl_entry(tables=("etf_minutes",)),
        },
    }
    if residual is not None:
        doc["cloud_mixed_caliber"] = residual
    doc.update(override)
    p = tmp_path / "consume_whitelist.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


# ── fail-closed：文件级 ─────────────────────────────────────────

def test_missing_file_rejects_all(tmp_path):
    with pytest.raises(ConsumeWhitelistError, match="缺失"):
        assert_minute_consumable("stock_minutes", ["000858.SZ"],
                                 whitelist_path=tmp_path / "nope.json")


def test_invalid_json_rejects_all(tmp_path):
    p = tmp_path / "consume_whitelist.json"
    p.write_text("{broken", encoding="utf-8")
    with pytest.raises(ConsumeWhitelistError, match="JSON 非法"):
        assert_minute_consumable("stock_minutes", ["000858.SZ"], whitelist_path=p)


def test_unsupported_version_rejects(tmp_path):
    p = _wl_file(tmp_path, version=99)
    with pytest.raises(ConsumeWhitelistError, match="version"):
        assert_minute_consumable("stock_minutes", ["000858.SZ"], whitelist_path=p)


def test_fail_closed_false_rejects(tmp_path):
    p = _wl_file(tmp_path, fail_closed=False)
    with pytest.raises(ConsumeWhitelistError, match="fail_closed"):
        assert_minute_consumable("stock_minutes", ["000858.SZ"], whitelist_path=p)


def test_enabled_without_probe_pass_invalidates_whole_file(tmp_path):
    p = _wl_file(tmp_path, codes={"000858.SZ": _wl_entry(verdict="FAIL")})
    with pytest.raises(ConsumeWhitelistError, match="verdict"):
        assert_minute_consumable("stock_minutes", ["000858.SZ"], whitelist_path=p)


def test_bad_tables_value_invalidates(tmp_path):
    p = _wl_file(tmp_path, codes={"000858.SZ": _wl_entry(tables=("cyq_chips",))})
    with pytest.raises(ConsumeWhitelistError, match="tables"):
        assert_minute_consumable("stock_minutes", ["000858.SZ"], whitelist_path=p)


# ── 拒绝：消费级 ─────────────────────────────────────────────────

def test_all_market_rejected_in_pilot(tmp_path):
    p = _wl_file(tmp_path)
    for codes in (None, ["ALL"]):
        with pytest.raises(ConsumeWhitelistError, match="全市场"):
            assert_minute_consumable("stock_minutes", codes, whitelist_path=p)


def test_code_not_in_whitelist_rejected(tmp_path):
    p = _wl_file(tmp_path)
    with pytest.raises(ConsumeWhitelistError, match="不在白名单"):
        assert_minute_consumable("stock_minutes", ["300750.SZ"], whitelist_path=p)


def test_disabled_code_rejected(tmp_path):
    p = _wl_file(tmp_path, codes={"000858.SZ": _wl_entry(enabled=False)})
    with pytest.raises(ConsumeWhitelistError, match="enabled=false"):
        assert_minute_consumable("stock_minutes", ["000858.SZ"], whitelist_path=p)


def test_table_not_granted_rejected(tmp_path):
    p = _wl_file(tmp_path)  # 000858 仅 stock_minutes
    with pytest.raises(ConsumeWhitelistError, match="未对 etf_minutes 放行"):
        assert_minute_consumable("etf_minutes", ["000858.SZ"], whitelist_path=p)


def test_any_unlisted_code_in_batch_rejects(tmp_path):
    p = _wl_file(tmp_path)
    with pytest.raises(ConsumeWhitelistError, match="不在白名单"):
        assert_minute_consumable(
            "stock_minutes", ["000858.SZ", "600519.SH"], whitelist_path=p)


# ── 放行场景 ────────────────────────────────────────────────────

def test_whitelisted_code_allowed_with_dirty_residual(tmp_path):
    """WP6.2：残留>0 时白名单 code 照常放行（probe 证据是 code 级的）。"""
    p = _wl_file(tmp_path, residual={
        "stock_minutes": {"rows": 37993409, "codes": 1460},
        "etf_minutes": {"rows": 2486879, "codes": 34},
        "as_of": "2026-08-20"})
    d = assert_minute_consumable("stock_minutes", ["000858.SZ"], whitelist_path=p)
    assert d["status"] == "allowed"
    assert d["residual_rows"] == 37993409
    assert d["residual_dirty"] is True


def test_whitelisted_code_allowed_with_zero_residual(tmp_path):
    p = _wl_file(tmp_path, residual={
        "stock_minutes": {"rows": 0, "codes": 0}, "as_of": "2026-08-25"})
    d = assert_minute_consumable("stock_minutes", ["000858.SZ"], whitelist_path=p)
    assert d["status"] == "allowed"
    assert d["residual_dirty"] is False


def test_residual_field_absent_treated_as_dirty(tmp_path):
    """残留指标缺失 = 状态未知 → 按脏处理（信息进 decision，code 级仍放行）。"""
    p = _wl_file(tmp_path)
    d = assert_minute_consumable("etf_minutes", ["510300.SH"], whitelist_path=p)
    assert d["status"] == "allowed"
    assert d["residual_rows"] is None
    assert d["residual_dirty"] is True


def test_bare_code_normalized_both_directions(tmp_path):
    """daemon 传裸码 / 白名单键 ts_code → 双向归一匹配。"""
    p = _wl_file(tmp_path)
    d = assert_minute_consumable("stock_minutes", ["000858"], whitelist_path=p)
    assert d["status"] == "allowed"


def test_non_minute_table_noop(tmp_path):
    p = _wl_file(tmp_path)
    d = assert_minute_consumable("stock_daily", ["ALL"], whitelist_path=p)
    assert d["status"] == "skipped_non_minute"


def test_multiple_whitelisted_codes_allowed(tmp_path):
    p = _wl_file(tmp_path, codes={
        "000858.SZ": _wl_entry(),
        "600519.SH": _wl_entry(),
    })
    d = assert_minute_consumable(
        "stock_minutes", ["000858.SZ", "600519"], whitelist_path=p)
    assert d["status"] == "allowed"
    assert set(d["codes"]) == {"000858.SZ", "600519"}


# ── 守卫与真实数据仓白名单的冒烟（存在则只验加载，不消费）──────────

def test_real_repo_whitelist_loads_or_clean_missing():
    """真实 data/consume_whitelist.json：要么合法加载，要么以明确异常缺失。"""
    path = g.resolve_whitelist_path()
    try:
        wl = g.load_whitelist(path)
        assert wl["fail_closed"] is True
    except ConsumeWhitelistError as exc:
        # 真实文件缺失/不合法也必须是一等公民的 fail-closed 消息
        assert any(k in str(exc) for k in ("缺失", "非法", "version", "fail_closed"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
