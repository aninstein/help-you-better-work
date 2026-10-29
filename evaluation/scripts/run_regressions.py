#!/usr/bin/env python3
"""Execute the test references in the feedback catalog, without live model/network calls."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from regression_catalog import REPOSITORY, ROOT, load_catalog, validate_catalog


def score_cases(cases: list[dict[str, Any]], junit: Path) -> list[dict[str, Any]]:
    tests: dict[tuple[str, str], list[str]] = {}
    if junit.is_file():
        for item in ET.parse(junit).iter("testcase"):
            file = item.get("classname", "").split(".")[-1]
            function = item.get("name", "").split("[", 1)[0]
            status = "passed"
            if item.find("failure") is not None or item.find("error") is not None:
                status = "failed"
            elif item.find("skipped") is not None:
                status = "skipped"
            tests.setdefault((file, function), []).append(status)
    results = []
    for case in cases:
        checks = []
        for node in case["pytest_nodes"]:
            file, function = node.split("::")
            statuses = tests.get((Path(file).stem, function), [])
            status = (
                "not_run" if not statuses
                else "failed" if "failed" in statuses
                else "skipped" if "skipped" in statuses
                else "passed"
            )
            checks.append({"node": node, "status": status, "parameter_cases": len(statuses)})
        results.append({
            "id": case["id"], "group": case["group"], "title": case["title"],
            "status": "passed" if all(c["status"] == "passed" for c in checks) else "failed",
            "tests": checks,
            "live_status": "not_run",
            "manual_rubric_status": "not_run",
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", default=[], help="case ID; repeatable")
    parser.add_argument("--group", action="append", default=[], help="group ID; repeatable")
    parser.add_argument("--list", action="store_true", help="show selected cases without running")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports")
    args = parser.parse_args()
    document = load_catalog()
    errors = validate_catalog(document)
    if errors:
        parser.error("\n".join(errors))
    unknown_cases = set(args.case) - {c["id"] for c in document["cases"]}
    unknown_groups = set(args.group) - set(document["groups"])
    if unknown_cases or unknown_groups:
        parser.error(f"unknown cases: {sorted(unknown_cases)}; groups: {sorted(unknown_groups)}")
    cases = [
        c for c in document["cases"]
        if (not args.case or c["id"] in args.case)
        and (not args.group or c["group"] in args.group)
    ]
    if not cases:
        parser.error("no cases selected")
    if args.list:
        for case in cases:
            print(f"{case['id']} [{case['group']}] {case['title']}")
        return 0
    nodes = sorted({node for case in cases for node in case["pytest_nodes"]})
    with tempfile.TemporaryDirectory(prefix="duet-feedback-eval-") as directory:
        junit = Path(directory) / "pytest.xml"
        print(f"Running {len(cases)} scenarios / {len(nodes)} unique test functions", flush=True)
        process = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *nodes, f"--junitxml={junit}"],
            cwd=REPOSITORY / "backend", check=False,
        )
        results = score_cases(cases, junit)
    passed = sum(result["status"] == "passed" for result in results)
    status = "passed" if passed == len(results) and process.returncode == 0 else "failed"
    now = datetime.now(UTC)
    report = {
        "catalog": "regressions/user-feedback.json",
        "generated_at": now.isoformat(),
        "verification": "deterministic_pytest_references",
        "status": status,
        "scenario_count": len(results), "passed_scenarios": passed,
        "unique_test_functions": len(nodes), "pytest_exit_code": process.returncode,
        "live_status": "not_run", "manual_rubric_status": "not_run",
        "limitation": document["verification"]["deterministic"],
        "cases": results,
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    output = args.report_dir / f"user-feedback-{now.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{status}: {passed}/{len(results)} scenarios; live/manual not run; report: {output}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
