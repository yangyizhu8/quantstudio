"""PR0 contract validators for QuantStudio Strategy Compiler."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import fastjsonschema

SCHEMA_DIR = Path(__file__).with_name("schemas")
EXAMPLE_DIR = Path(__file__).with_name("examples")


class ContractValidationError(ValueError):
    """Raised when an artifact violates a frozen compiler contract."""


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Unknown Strategy Compiler schema: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


@lru_cache(maxsize=None)
def _compiled_schema(name: str):
    return fastjsonschema.compile(load_schema(name))


def load_example(name: str) -> dict[str, Any]:
    path = EXAMPLE_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Unknown Strategy Compiler example: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _validate_schema(schema_name: str, payload: Mapping[str, Any]) -> None:
    try:
        _compiled_schema(schema_name)(dict(payload))
    except fastjsonschema.JsonSchemaException as exc:
        raise ContractValidationError(str(exc)) from exc


def validate_strategy_spec(payload: Mapping[str, Any]) -> None:
    """Validate Strategy Spec v1 plus cross-field timing semantics."""
    _validate_schema("strategy_spec.schema.json", payload)
    time_model = payload["time_model"]
    engine = payload["engine_profile"]
    execution = payload["execution"]

    if engine["event_type"] == "bar":
        if time_model["market_data_frequency"] != engine["bar_frequency"]:
            raise ContractValidationError(
                "market_data_frequency must equal bar_frequency for bar profiles"
            )
    elif time_model["market_data_frequency"] != "tick":
        raise ContractValidationError(
            "tick profiles require market_data_frequency='tick'"
        )

    ranks = {"tick": 0, "1m": 1, "5m": 2, "15m": 3,
             "30m": 4, "60m": 5, "1d": 6}
    market_rank = ranks[time_model["market_data_frequency"]]
    for field in ("factor_frequency", "signal_frequency",
                  "portfolio_valuation_frequency"):
        if ranks[time_model[field]] < market_rank:
            raise ContractValidationError(
                f"{field} cannot be finer than market_data_frequency"
            )

    if (execution["match_price_mode"] == "next_open"
            and time_model["execution_clock"] != "next_open"):
        raise ContractValidationError(
            "next_open matching requires execution_clock='next_open'"
        )

    mode = execution["mode"]
    if mode != "native" and not any(
        item["mode"] == mode for item in payload["approximations"]
    ):
        raise ContractValidationError(
            f"{mode} requires a confirmed approximation record"
        )


def validate_capability_report(payload: Mapping[str, Any]) -> None:
    """Validate capability dimensions and forbid false READY claims."""
    _validate_schema("capability_report.schema.json", payload)
    ready_values = {"AVAILABLE", "READY"}
    required_blockers: list[str] = []
    dimensions = ("schema_status", "data_status", "adapter_status",
                  "provider_status", "engine_status", "platform_status")
    for item in payload["capabilities"]:
        if item["execution_status"] == "READY":
            bad = [name for name in dimensions if item[name] not in ready_values]
            if bad:
                raise ContractValidationError(
                    f"{item['capability']} cannot be READY; non-ready: {bad}"
                )
        if item["required"] and item["execution_status"] != "READY":
            required_blockers.append(item["capability"])

    overall = payload["overall_execution_status"]
    if required_blockers and overall == "READY":
        raise ContractValidationError(
            "overall status cannot be READY with required blockers: "
            + ", ".join(required_blockers)
        )
    if not required_blockers and overall != "READY":
        raise ContractValidationError(
            "overall status must be READY when all required capabilities are READY"
        )


def validate_run_card(payload: Mapping[str, Any]) -> None:
    """Validate Run Card v1."""
    _validate_schema("run_card.schema.json", payload)

