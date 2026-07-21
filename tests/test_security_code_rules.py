"""PR1 authoritative security classification tests."""

import pytest

from quantstudio.backtest.libs.security_code_rules import (
    BSE_LEGACY_TO_920,
    classify_security,
    is_bse_market,
    is_chinext,
    is_chinext_market,
    is_convertible_bond,
    is_etf,
    is_index,
    is_main_board,
    is_star_market,
    normalize_security_code,
    normalize_to_ptrade,
    normalize_to_qmt,
)


def test_classifies_supported_equity_boards():
    assert classify_security("600000.SH") == "main_board"
    assert classify_security("000001.SZ") == "main_board"
    assert classify_security("300750.XSHE") == "chinext"
    assert classify_security("688981.XSHG") == "star_market"
    assert classify_security("920017.BJ") == "bse"
    assert is_main_board("601318")
    assert is_chinext("301269") and is_chinext_market("301269")
    assert is_star_market("688001")
    assert is_bse_market("920001")


def test_funds_bonds_and_indices_are_not_misclassified_as_stocks():
    for code in ("510300.SH", "588000.XSHG", "159915.SZ", "160105.XSHE"):
        assert is_etf(code)
        assert classify_security(code) == "etf"
        assert not is_main_board(code)
    for code in ("110059.SH", "113001.XSHG", "123001.SZ", "127001.XSHE"):
        assert is_convertible_bond(code)
        assert classify_security(code) == "convertible_bond"
    for code in ("000300.XSHG", "399006.XSHE", "899050.BJ"):
        assert is_index(code)
        assert classify_security(code) == "index"


def test_normalizes_supported_exchange_aliases_both_directions():
    aliases = {
        "600000": ("600000.SH", "600000.SS"),
        "600000.SS": ("600000.SH", "600000.SS"),
        "600000.XSHG": ("600000.SH", "600000.SS"),
        "000001": ("000001.SZ", "000001.SZ"),
        "000001.XSHE": ("000001.SZ", "000001.SZ"),
        "920017": ("920017.BJ", "920017.BJ"),
        "920017.XBJ": ("920017.BJ", "920017.BJ"),
        "920017.XBSE": ("920017.BJ", "920017.BJ"),
        "920017.BJ": ("920017.BJ", "920017.BJ"),
    }
    for source, (qmt, ptrade) in aliases.items():
        assert normalize_to_qmt(source) == qmt
        assert normalize_to_ptrade(source) == ptrade
        assert normalize_security_code(source, "bare") == source.split(".")[0]


def test_invalid_or_unknown_target_is_rejected():
    with pytest.raises(ValueError):
        normalize_security_code("600000", "wind")


def test_official_legacy_mapping_is_exact_not_prefix_wide():
    assert BSE_LEGACY_TO_920["430017"] == "920017"
    assert BSE_LEGACY_TO_920["830779"] == "920779"
    assert BSE_LEGACY_TO_920["872931"] == "920931"
    assert len(BSE_LEGACY_TO_920) == 248


def test_bse_membership_overrides_incorrect_exchange_alias():
    assert normalize_to_qmt("920017.XSHG") == "920017.BJ"
    assert normalize_to_ptrade("430017.XSHE") == "430017.BJ"

