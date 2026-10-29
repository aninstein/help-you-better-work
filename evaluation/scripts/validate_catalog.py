#!/usr/bin/env python3
"""Validate the portable dual-channel evaluation catalog without an LLM or API."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from regression_catalog import load_catalog, summary, validate_catalog

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog.json"
REPORT = ROOT / "precheck-report" / "catalog-validation.json"
MODES = {"direct", "hinter_hint", "hinter_block", "multi_question", "rapid_burst"}
EXPECTED_SUITE_COUNT = 24


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_case(suite_id: str, case: dict[str, Any], errors: list[str]) -> None:
    label = f"{suite_id}/{case.get('id', '<missing-id>')}"
    if not nonempty(case.get("id")):
        fail(errors, f"{label}: 缺少 case id")
    if case.get("mode") not in MODES:
        fail(errors, f"{label}: mode 必须属于 {sorted(MODES)}")
    talker = case.get("talker")
    burst = case.get("talker_messages")
    if case.get("mode") == "rapid_burst":
        if not isinstance(burst, list) or len(burst) < 2 or not all(nonempty(x) for x in burst):
            fail(errors, f"{label}: rapid_burst 需要至少两条非空 talker_messages")
    elif not nonempty(talker):
        fail(errors, f"{label}: 需要非空 talker 输入")
    hinter = case.get("hinter")
    if not isinstance(hinter, dict) or hinter.get("action") not in {"approve", "reject"}:
        fail(errors, f"{label}: hinter.action 必须为 approve 或 reject")
    if case.get("mode") == "hinter_block" and hinter and hinter.get("action") != "reject":
        fail(errors, f"{label}: 阻断题必须使用 reject")
    if case.get("mode") != "hinter_block" and hinter and hinter.get("action") != "approve":
        fail(errors, f"{label}: 非阻断题必须使用 approve")
    expected = case.get("expected")
    if not isinstance(expected, dict):
        fail(errors, f"{label}: 缺少 expected")
        return
    count = expected.get("question_count")
    if not isinstance(count, int) or count < 1:
        fail(errors, f"{label}: expected.question_count 必须为正整数")
    if not isinstance(expected.get("question_terms"), list):
        fail(errors, f"{label}: 缺少 question_terms")
    answer_term_groups = expected.get("answer_term_groups")
    if answer_term_groups is not None and (
        not isinstance(answer_term_groups, list)
        or any(
            not isinstance(group, list)
            or not group
            or not all(nonempty(term) for term in group)
            for group in answer_term_groups
        )
    ):
        fail(errors, f"{label}: answer_term_groups 必须是非空字符串组的列表")
    evidence = expected.get("evidence_expectation")
    if evidence is not None:
        if not isinstance(evidence, dict):
            fail(errors, f"{label}: evidence_expectation 必须是对象")
        else:
            for field in ("source_kind", "verification"):
                if not nonempty(evidence.get(field)):
                    fail(errors, f"{label}: evidence_expectation 缺少 {field}")
            if (
                evidence.get("verification") == "runtime_trace"
                and not evidence.get("required_tool")
                and not evidence.get("must_not_call_tools")
            ):
                fail(errors, f"{label}: runtime_trace 需要 required_tool 或 must_not_call_tools")
            if evidence.get("source_kind") == "authorized_company_app" and not nonempty(
                evidence.get("fixture_id")
            ):
                fail(errors, f"{label}: 公司 APP 用例必须指定 fixture_id")
    patterns = expected.get("forbidden_answer_patterns", [])
    if not isinstance(patterns, list) or not all(nonempty(pattern) for pattern in patterns):
        fail(errors, f"{label}: forbidden_answer_patterns 必须是非空字符串列表")
    if case.get("mode") == "hinter_block":
        if expected.get("question_state") != "rejected":
            fail(errors, f"{label}: 阻断题期望状态必须是 rejected")
    elif expected.get("question_state") != "answered":
        fail(errors, f"{label}: 批准题期望状态必须是 answered")
    if not isinstance(case.get("manual_rubric"), list) or len(case["manual_rubric"]) < 3:
        fail(errors, f"{label}: 至少提供 3 条 manual_rubric")


def main() -> int:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    errors: list[str] = []
    suites = document.get("suites")
    if not isinstance(suites, list) or len(suites) != EXPECTED_SUITE_COUNT:
        fail(errors, f"目录必须恰好包含 {EXPECTED_SUITE_COUNT} 个场景包")
        suites = suites if isinstance(suites, list) else []
    suite_ids = [suite.get("id") for suite in suites if isinstance(suite, dict)]
    duplicates = [key for key, amount in Counter(suite_ids).items() if amount > 1]
    if duplicates:
        fail(errors, f"场景 ID 重复：{duplicates}")
    modes = Counter()
    domains = Counter()
    atomic_cases = 0
    evidence_cases = 0
    for suite in suites:
        if not isinstance(suite, dict):
            fail(errors, "场景必须是对象")
            continue
        suite_id = suite.get("id", "<missing-suite-id>")
        for field in ("id", "title", "domain", "business_value", "talker_context"):
            if not nonempty(suite.get(field)):
                fail(errors, f"{suite_id}: 缺少 {field}")
        domains[str(suite.get("domain", "unknown"))] += 1
        cases = suite.get("cases")
        if not isinstance(cases, list) or len(cases) != 5:
            fail(errors, f"{suite_id}: 必须恰好有 5 条主链路用例")
            cases = cases if isinstance(cases, list) else []
        local_modes = [case.get("mode") for case in cases if isinstance(case, dict)]
        if set(local_modes) != MODES or len(local_modes) != len(set(local_modes)):
            fail(errors, f"{suite_id}: 必须各有且仅有一条五种 mode")
        for case in cases:
            if isinstance(case, dict):
                validate_case(suite_id, case, errors)
                modes[str(case.get("mode"))] += 1
                atomic_cases += 1
                if case.get("expected", {}).get("evidence_expectation"):
                    evidence_cases += 1
        memory = suite.get("memory_chain")
        if not isinstance(memory, dict):
            fail(errors, f"{suite_id}: 缺少 memory_chain")
        else:
            for field in ("initial_talker", "hint", "positive_feedback", "recall_talker"):
                if not nonempty(memory.get(field)):
                    fail(errors, f"{suite_id}/memory_chain: 缺少 {field}")
            if not isinstance(memory.get("recall_terms"), list) or not memory["recall_terms"]:
                fail(errors, f"{suite_id}/memory_chain: 缺少 recall_terms")
    result = {
        "catalog": str(CATALOG.relative_to(ROOT)),
        "suite_count": len(suites),
        "atomic_case_count": atomic_cases,
        "memory_chain_count": len(suites),
        "evidence_case_count": evidence_cases,
        "modes": dict(sorted(modes.items())),
        "domains": dict(sorted(domains.items())),
    }
    feedback = load_catalog()
    feedback_errors = validate_catalog(feedback)
    errors.extend(feedback_errors)
    if not feedback_errors:
        result["feedback_regressions"] = summary(feedback)
    if modes != Counter({mode: EXPECTED_SUITE_COUNT for mode in MODES}):
        fail(errors, f"每种模式必须覆盖 {EXPECTED_SUITE_COUNT} 次")
    result.update(status="passed" if not errors else "failed", errors=errors)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
