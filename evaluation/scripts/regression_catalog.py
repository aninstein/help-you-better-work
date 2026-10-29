"""Load feedback scenarios and validate their executable regression-test references."""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent
CATALOG = ROOT / "regressions" / "user-feedback.json"


def load_catalog(path: Path = CATALOG) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_catalog(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    groups = document.get("groups", {})
    cases = document.get("cases", [])
    if not isinstance(groups, dict) or not groups:
        errors.append("feedback: groups must be a nonempty object")
        groups = {}
    if not isinstance(cases, list) or not cases:
        return errors + ["feedback: cases must be a nonempty list"]
    ids: list[str] = []
    covered: set[str] = set()
    functions: dict[Path, set[str]] = {}
    for case in cases:
        if not isinstance(case, dict):
            errors.append("feedback: case must be an object")
            continue
        label = case.get("id", "<missing>")
        for field in ("id", "group", "title", "reported_failure", "context"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                errors.append(f"{label}: missing {field}")
        if isinstance(label, str):
            ids.append(label)
        group = case.get("group")
        if not isinstance(group, str) or group not in groups:
            errors.append(f"{label}: unknown group")
        else:
            covered.add(group)
        steps = case.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"{label}: steps must be nonempty")
        else:
            for step in steps:
                if (
                    not isinstance(step, dict)
                    or step.get("actor") not in {"talker", "hinter", "system"}
                    or not isinstance(step.get("text"), str)
                    or not step["text"].strip()
                ):
                    errors.append(f"{label}: invalid step")
        expected = case.get("expected")
        if (
            not isinstance(expected, dict)
            or len(expected) < 2
            or not all(isinstance(v, str) and v.strip() for v in expected.values())
        ):
            errors.append(f"{label}: expected needs at least two explicit assertions")
        rubric = case.get("manual_rubric")
        if (
            not isinstance(rubric, list)
            or len(rubric) < 2
            or not all(isinstance(v, str) and v.strip() for v in rubric)
        ):
            errors.append(f"{label}: manual_rubric needs at least two checks")
        nodes = case.get("pytest_nodes")
        if not isinstance(nodes, list) or not nodes:
            errors.append(f"{label}: pytest_nodes must be nonempty")
            continue
        for node in nodes:
            if not isinstance(node, str) or node.count("::") != 1:
                errors.append(f"{label}: invalid test reference {node!r}")
                continue
            file, function = node.split("::")
            path = (REPOSITORY / "backend" / file).resolve()
            if (
                not path.is_relative_to(REPOSITORY / "backend" / "tests")
                or path.suffix != ".py"
                or not path.is_file()
            ):
                errors.append(f"{label}: invalid test path {file}")
                continue
            if path not in functions:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                functions[path] = {
                    item.name for item in tree.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
            if not function.startswith("test_") or function not in functions[path]:
                errors.append(f"{label}: missing test function {node}")
    for id, count in Counter(ids).items():
        if count > 1:
            errors.append(f"feedback: duplicate case id {id}")
    for group in set(groups) - covered:
        errors.append(f"feedback: uncovered group {group}")
    live = document.get("live_acceptance_sequence", {})
    if not isinstance(live, dict) or not live.get("setup") or not live.get("checks"):
        errors.append("feedback: missing live acceptance protocol")
    else:
        for step in live.get("steps", []):
            if not isinstance(step, dict) or step.get("case_id") not in ids:
                errors.append("feedback: live acceptance references unknown case")
    return errors


def summary(document: dict[str, Any]) -> dict[str, Any]:
    cases = document["cases"]
    return {
        "catalog": str(CATALOG.relative_to(ROOT)),
        "case_count": len(cases),
        "groups": dict(sorted(Counter(c["group"] for c in cases).items())),
        "unique_test_functions": len({n for c in cases for n in c["pytest_nodes"]}),
        "live_acceptance_steps": len(document["live_acceptance_sequence"]["steps"]),
    }
