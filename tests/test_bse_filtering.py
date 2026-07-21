"""PR1 BSE and delisted/old-third-board boundary regression tests."""

import ast
import json
from pathlib import Path

import pytest

from quantstudio.backtest.libs.security_code_rules import is_bse_market
from quantstudio.backtest.libs.shared_ashare_rules import get_price_limit_pct


@pytest.mark.parametrize("code", [
    "920017",       # current BSE 920 range
    "830779",       # official legacy 83x mapping
    "872931",       # official legacy 87x mapping
    "430017",       # official legacy 430 mapping
    "920017.BJ",
    "430017.XBJ",
])
def test_bse_current_and_official_legacy_codes_are_recognized(code):
    assert is_bse_market(code)
    assert get_price_limit_pct(code) == pytest.approx(0.30)


@pytest.mark.parametrize("code", [
    "400001",       # delisted-board range, not BSE by prefix alone
    "420001",       # old-third-board boundary
    "832317",       # project DB historical NEEQ sample, absent from official mapping
    "833874",       # project DB historical NEEQ sample, absent from official mapping
    "839999",       # arbitrary 8xx code absent from official mapping
    "800001",
])
def test_four_and_eight_prefixes_are_not_blanket_bse(code):
    assert not is_bse_market(code)
    assert get_price_limit_pct(code) == pytest.approx(0.10)


def test_shared_ashare_rules_delegates_market_classification():
    from quantstudio.backtest.libs import security_code_rules as authority
    from quantstudio.backtest.libs import shared_ashare_rules as shared

    for code in ("688981", "300750", "920017", "430017", "400001"):
        assert shared.is_star_market(code) == authority.is_star_market(code)
        assert shared.is_chinext_market(code) == authority.is_chinext_market(code)
        assert shared.is_bse_market(code) == authority.is_bse_market(code)
    assert shared.is_st_stock("600000", "*ST ??")
    assert not shared.is_st_stock("600000", "????")


def test_ptrade_api_has_no_independent_numeric_startswith_market_rules():
    path = Path(__file__).resolve().parents[1] / "quantstudio" / "backtest" / "ptrade_api.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "startswith" or not node.args:
            continue
        arg = node.args[0]
        values = []
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            values = [arg.value]
        elif isinstance(arg, ast.Tuple):
            values = [e.value for e in arg.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if any(value and value[0].isdigit() for value in values):
            offenders.append((node.lineno, values))
    assert offenders == []


def test_packaged_mapping_matches_archived_official_snapshot():
    from quantstudio.backtest.libs.security_code_rules import BSE_LEGACY_TO_920

    root = Path(__file__).resolve().parents[1]
    archived = json.loads((
        root / "docs" / "strategy-compiler" / "sources" /
        "bse-official-code-mapping-20260720.json").read_text(encoding="utf-8"))
    archived_mapping = {
        row["old_code"]: row["new_code"] for row in archived["mappings"]
    }
    assert BSE_LEGACY_TO_920 == archived_mapping


def test_generated_security_code_document_matches_runtime_authority():
    from quantstudio.backtest.libs.security_code_rules import (
        render_security_code_rules_markdown,
    )

    root = Path(__file__).resolve().parents[1]
    document = root / "docs" / "strategy-compiler" / "security-code-rules.md"
    assert document.read_text(encoding="utf-8") == render_security_code_rules_markdown()

