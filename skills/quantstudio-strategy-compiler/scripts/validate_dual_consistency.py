#!/usr/bin/env python3
"""Compare generated QuantStudio and PTrade strategy semantics after generation."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from agent_skill_common import call_name, function_map, load_json, write_json


LIFECYCLE = {"initialize", "before_trading_start", "handle_data", "after_trading_end"}


class _SemanticNormalizer(ast.NodeTransformer):
    """Remove explicitly allowed platform metadata while preserving business logic."""

    def visit_Module(self, node: ast.Module):
        node = self.generic_visit(node)
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            body = body[1:]
        node.body = [stmt for stmt in body if not self._platform_assignment(stmt)]
        return node

    @staticmethod
    def _platform_assignment(stmt: ast.stmt) -> bool:
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            return False
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        return any(isinstance(target, ast.Name) and target.id in {"TARGET_PLATFORM", "PLATFORM_PROFILE"}
                   for target in targets)


def _semantic_dump(source: str) -> str:
    tree = ast.parse(source)
    normalized = _SemanticNormalizer().visit(tree)
    ast.fix_missing_locations(normalized)
    return ast.dump(normalized, annotate_fields=True, include_attributes=False)


def _schedules(source: str) -> list[tuple[str | None, str | None]]:
    tree = ast.parse(source)
    result = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or call_name(node) != "run_daily":
            continue
        callback = node.args[1].id if len(node.args) > 1 and isinstance(node.args[1], ast.Name) else None
        schedule_time = None
        for kw in node.keywords:
            if kw.arg == "time" and isinstance(kw.value, ast.Constant):
                schedule_time = kw.value.value
        result.append((callback, schedule_time))
    return sorted(result)


def _api_calls(source: str) -> Counter:
    tree = ast.parse(source)
    local = set(function_map(tree))
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = call_name(node)
            if name and name not in local and not isinstance(node.func, ast.Attribute):
                calls.append(name)
    return Counter(calls)


def compare_sources(local_source: str, ptrade_source: str, design: dict[str, Any] | None = None) -> dict[str, Any]:
    issues = []
    try:
        local_tree = ast.parse(local_source)
        ptrade_tree = ast.parse(ptrade_source)
    except SyntaxError as exc:
        return {"report_version": "1.0", "status": "BLOCKED", "issues": [{"rule_id": "SYNTAX", "message": str(exc)}]}

    local_functions = set(function_map(local_tree))
    ptrade_functions = set(function_map(ptrade_tree))
    if local_functions != ptrade_functions:
        issues.append({"rule_id": "FUNCTION-SET", "message": f"function sets differ: local-only={sorted(local_functions-ptrade_functions)}, ptrade-only={sorted(ptrade_functions-local_functions)}"})
    if _schedules(local_source) != _schedules(ptrade_source):
        issues.append({"rule_id": "SCHEDULES", "message": "run_daily callback/time registrations differ"})
    if _api_calls(local_source) != _api_calls(ptrade_source):
        issues.append({"rule_id": "API-CALLS", "message": "public API call multisets differ"})

    local_semantic = _semantic_dump(local_source)
    ptrade_semantic = _semantic_dump(ptrade_source)
    if local_semantic != ptrade_semantic:
        issues.append({"rule_id": "SEMANTIC-AST", "message": "normalized strategy AST differs between targets"})

    local_hash = hashlib.sha256(local_source.encode("utf-8")).hexdigest()
    ptrade_hash = hashlib.sha256(ptrade_source.encode("utf-8")).hexdigest()
    return {
        "report_version": "1.0",
        "strategy_id": (design or {}).get("strategy_id"),
        "status": "PASS" if not issues else "BLOCKED",
        "exact_source_match": local_source == ptrade_source,
        "local_sha256": local_hash,
        "ptrade_sha256": ptrade_hash,
        "semantic_ast_match": local_semantic == ptrade_semantic,
        "lifecycle_functions": sorted((local_functions | ptrade_functions) & LIFECYCLE),
        "schedules": _schedules(local_source),
        "api_calls": dict(sorted(_api_calls(local_source).items())),
        "issues": issues,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate QuantStudio/PTrade semantic consistency")
    parser.add_argument("local")
    parser.add_argument("ptrade")
    parser.add_argument("--design")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    local_source = Path(args.local).read_text(encoding="utf-8-sig")
    ptrade_source = Path(args.ptrade).read_text(encoding="utf-8-sig")
    design = load_json(args.design) if args.design else None
    report = compare_sources(local_source, ptrade_source, design)
    if args.out:
        write_json(args.out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
