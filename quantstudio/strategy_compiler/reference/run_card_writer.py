"""Attach the optional `reference_closure` section to a run_card (reviewer option 1).

run_card.schema.json gains an OPTIONAL reference_closure field; old run_cards
without it still validate. run_card carries only closure metadata / path pointers /
digests — NOT runtime multi-instance basket data (that lives in independent artifacts).
"""
from __future__ import annotations

import copy
from typing import Any, Dict

REFERENCE_CLOSURE_SCHEMA_VERSION = "1.0"


def attach_reference_closure(
    run_card: Dict[str, Any],
    *,
    oracle_source_digest: str,
    data_digest: str | None,
    config_digest: str,
    signals_path: str,
    orders_path: str,
    nav_path: str,
    source_digest_path: str,
    trigger_reason_taxonomy=None,
) -> Dict[str, Any]:
    """Return a copy of run_card with the optional reference_closure section added.

    Does not mutate the input. Only adds the reference_closure key; all other
    run_card fields are preserved. The data_digest is taken from source_digest
    (may be None when data_digest_status == 'blocked').
    """
    rc = copy.deepcopy(run_card)
    rc["reference_closure"] = {
        "schema_version": REFERENCE_CLOSURE_SCHEMA_VERSION,
        "oracle_source_digest": oracle_source_digest,
        "data_digest": data_digest,
        "config_digest": config_digest,
        "signals_path": signals_path,
        "orders_path": orders_path,
        "nav_path": nav_path,
        "source_digest_path": source_digest_path,
        "trigger_reason_taxonomy": sorted(trigger_reason_taxonomy) if trigger_reason_taxonomy else [],
    }
    return rc
