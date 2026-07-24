#!/usr/bin/env python3
"""Shared helpers for the agent-first strategy Skill."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_catalog() -> dict[str, Any]:
    return load_json(skill_root() / "references" / "component-catalog.json")


def validate_design(design: dict[str, Any]) -> list[str]:
    schema_path = skill_root() / "schemas" / "agent_strategy_design.schema.json"
    schema = load_json(schema_path)
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema is required to validate agent_strategy_design.json"]
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(design), key=lambda e: list(e.absolute_path)):
        location = ".".join(str(item) for item in error.absolute_path) or "$"
        errors.append(f"{location}: {error.message}")
    return errors


def confirmation_errors(design: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    confirmations = design.get("user_confirmations", {})
    for key in ("strategy_semantics", "execution_approximations", "component_plan"):
        if confirmations.get(key) is not True:
            errors.append(f"user_confirmations.{key} must be true before code generation")
    open_questions = design.get("open_questions", [])
    if open_questions:
        errors.append(f"open_questions must be empty before code generation: {open_questions}")
    unconfirmed = [item.get("id", "<unnamed>") for item in design.get("approximations", [])
                   if item.get("confirmed") is not True]
    if unconfirmed:
        errors.append(f"all approximations must be confirmed before code generation: {unconfirmed}")
    return errors


def function_map(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in getattr(tree, "body", [])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def constant_value(node: ast.AST) -> Any:
    return node.value if isinstance(node, ast.Constant) else None


def is_placeholder_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if not body:
        return True
    if len(body) == 1 and isinstance(body[0], ast.Pass):
        return True
    if len(body) == 1 and isinstance(body[0], ast.Raise):
        exc = body[0].exc
        if isinstance(exc, ast.Call) and call_name(exc) == "NotImplementedError":
            return True
        if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
            return True
    return False
