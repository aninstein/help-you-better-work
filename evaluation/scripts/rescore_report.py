#!/usr/bin/env python3
"""Reapply the current catalog's deterministic rules to captured run evidence.

This is useful when a rubric evolves without changing the API/model execution.
It never invents a model response: it only uses the question and answer evidence
already present in a `run_live.py` JSON report.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from render_report import render


ROOT = Path(__file__).resolve().parents[1]


def contains_all(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return all(term.lower() in lowered for term in terms)


def catalog_cases() -> dict[tuple[str, str], dict[str, Any]]:
    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    return {
        (suite["id"], case["id"]): case
        for suite in catalog["suites"]
        for case in suite["cases"]
    }


def score_case(captured: dict[str, Any], current: dict[str, Any]) -> None:
    expected = current["expected"]
    observed = captured.get("observed", {})
    final_states = observed.get("final_states", [])
    question_text = "\n".join(observed.get("questions", []))
    answer = observed.get("answer_excerpt", "")
    state_ok = bool(final_states) and all(
        state == expected["question_state"] for state in final_states
    )
    count_ok = observed.get("question_count") == expected["question_count"]
    terms_ok = contains_all(question_text, expected["question_terms"])
    isolated = bool(observed.get("role_isolated"))
    if current["mode"] == "hinter_block":
        checks = [
            ("expected_block_state", state_ok, 35),
            ("no_generated_answer", observed.get("answer_state") is None or observed.get("answer_excerpt") == "Sorry, I dont know.", 30),
            ("public_friendly_refusal", bool(observed.get("public_refusal")), 15),
            ("decomposition", count_ok and terms_ok, 10),
            ("talker_role_isolation", isolated, 10),
        ]
    else:
        evidence_terms = expected.get("answer_terms", [])
        patterns = expected.get("forbidden_answer_patterns", [])
        checks = [
            ("expected_answered_state", state_ok, 25),
            ("decomposition_count", count_ok, 15),
            ("decomposition_terms", terms_ok, 10),
            ("answer_hint_evidence", not evidence_terms or contains_all(answer, evidence_terms), 15),
            ("nonempty_completed_answer", bool(answer.strip()) and observed.get("answer_state") == "completed", 15),
            (
                "no_unsupported_external_claim",
                not any(re.search(pattern, answer, flags=re.IGNORECASE) for pattern in patterns),
                10,
            ),
            ("talker_role_isolation", isolated, 10),
        ]
    captured["expected"] = expected
    captured["manual_rubric"] = current["manual_rubric"]
    captured["checks"] = [
        {"name": name, "passed": passed, "weight": weight} for name, passed, weight in checks
    ]
    captured["score"] = sum(weight for _, passed, weight in checks if passed)


def rescore(report: dict[str, Any]) -> dict[str, Any]:
    cases = catalog_cases()
    by_mode: dict[str, list[float]] = defaultdict(list)
    by_domain: dict[str, list[float]] = defaultdict(list)
    for suite in report["suites"]:
        for captured in suite["cases"]:
            current = cases[(suite["id"], captured["id"])]
            score_case(captured, current)
            by_mode[captured["mode"]].append(captured["score"])
        suite["atomic_score"] = round(
            sum(case["score"] for case in suite["cases"]) / len(suite["cases"]), 1
        )
        suite["automatic_score"] = round(
            suite["atomic_score"] * 0.9 + suite["memory"].get("score", 0) * 0.1, 1
        )
        by_domain[suite["domain"]].append(suite["automatic_score"])
    report["automatic_score"] = round(
        sum(suite["automatic_score"] for suite in report["suites"]) / len(report["suites"]), 1
    )
    report["by_mode"] = {
        name: round(sum(scores) / len(scores), 1) for name, scores in sorted(by_mode.items())
    }
    report["by_domain"] = {
        name: round(sum(scores) / len(scores), 1) for name, scores in sorted(by_domain.items())
    }
    report["score_rule_version"] = "1.1"
    report["rescored_with_current_catalog"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = json.loads(args.report.read_text(encoding="utf-8"))
    target = args.output or args.report.with_name(args.report.stem + "-rescored.json")
    updated = rescore(source)
    target.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    render(updated, target.with_suffix(".html"))
    print(f"自动证据分：{updated['automatic_score']}/100")
    print(f"JSON: {target}")
    print(f"图表: {target.with_suffix('.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
