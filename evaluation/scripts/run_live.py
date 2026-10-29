#!/usr/bin/env python3
"""Run dual-channel evaluation packs against a running local FastAPI service.

The runner intentionally uses only the public HTTP API.  It therefore measures
the same Intake → Hinter review → answer → Wiki memory path as the UI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
CATALOG_PATH = ROOT / "catalog.json"
DEFAULT_TIMEOUT = 90.0
PRIVATE_KEYS = {"hint", "reviewer", "reviewer_id", "review_notes", "review_id", "wiki_sources"}


class ApiError(RuntimeError):
    pass


class Api:
    def __init__(self, base_url: str, timeout: float, retries: int = 8) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.tokens: dict[str, str] = {}

    def request(
        self,
        method: str,
        path: str,
        role: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if role:
            headers["Authorization"] = "Bearer " + self.tokens[role]
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8")
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code not in {502, 503, 504}:
                    raise ApiError(f"{method} {path} -> {exc.code}: {body}") from exc
                last_error = ApiError(f"{method} {path} -> {exc.code}: {body}")
            except urllib.error.URLError as exc:
                last_error = ApiError(f"{method} {path} -> network error: {exc.reason}")
            if attempt < self.retries - 1:
                time.sleep(min(0.25 * (attempt + 1), 2.0))
        raise last_error or ApiError(f"{method} {path} failed without a response")

    def login(self) -> None:
        for role in ("talker", "hinter"):
            result = self.request("POST", "/api/v1/auth/demo", payload={"role": role})
            self.tokens[role] = result["token"]

    def snapshot(self, session_id: str, role: str = "talker") -> dict[str, Any]:
        return self.request("GET", f"/api/v1/sessions/{session_id}", role)


def contains_all(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return all(term.lower() in lowered for term in terms)


def contains_term_groups(text: str, groups: list[list[str]]) -> bool:
    """Every group is one concept; any listed language variant satisfies it."""
    lowered = text.lower()
    return all(any(term.lower() in lowered for term in group) for group in groups)


def source_matches_domains(sources: list[str], domains: list[str]) -> bool:
    hosts = [(urlsplit(source).hostname or "").lower() for source in sources]
    return any(
        host == domain.lower() or host.endswith("." + domain.lower())
        for host in hosts
        for domain in domains
    )


def contains_private_keys(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PRIVATE_KEYS or key.startswith("review_"):
                return True
            if contains_private_keys(child):
                return True
    if isinstance(value, list):
        return any(contains_private_keys(item) for item in value)
    return False


def wait_for_snapshot(
    api: Api, session_id: str, predicate: Any, deadline: float, role: str = "talker"
) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = api.snapshot(session_id, role)
        if predicate(latest):
            return latest
        time.sleep(0.35)
    return latest


def message_questions(snapshot: dict[str, Any], message_ids: list[str]) -> list[dict[str, Any]]:
    messages = {message["id"]: message for message in snapshot.get("messages", [])}
    wanted = [
        question_id
        for message_id in message_ids
        for question_id in messages.get(message_id, {}).get("question_ids", [])
    ]
    questions = {question["id"]: question for question in snapshot.get("questions", [])}
    return [questions[question_id] for question_id in dict.fromkeys(wanted) if question_id in questions]


def submit_and_collect(
    api: Api,
    session_id: str,
    texts: list[str],
    expected_count: int,
    deadline: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    message_ids = []
    for index, text in enumerate(texts):
        created = api.request(
            "POST",
            f"/api/v1/sessions/{session_id}/messages",
            "talker",
            {"text": text},
        )
        message_ids.append(created["id"])

    def ready(snapshot: dict[str, Any]) -> bool:
        messages = {message["id"]: message for message in snapshot.get("messages", [])}
        if any(message_id not in messages for message_id in message_ids):
            return False
        statuses = [messages[message_id].get("status") for message_id in message_ids]
        if any(status in {"intake_pending", "transcribing"} for status in statuses):
            return False
        if expected_count == 0:
            return all(status in {"intake_complete", "intake_failed", "transcription_failed"} for status in statuses)
        return len(message_questions(snapshot, message_ids)) >= expected_count or all(
            status in {"intake_complete", "intake_failed", "transcription_failed"} for status in statuses
        )

    snapshot = wait_for_snapshot(api, session_id, ready, deadline)
    return message_questions(snapshot, message_ids), snapshot


def review_questions(
    api: Api,
    session_id: str,
    questions: list[dict[str, Any]],
    action: str,
    hint: str,
) -> None:
    current = api.snapshot(session_id, "hinter")
    current_questions = {question["id"]: question for question in current.get("questions", [])}
    for question in questions:
        active = current_questions.get(question["id"], question)
        if active.get("status") in {
            "rejected",
            "blocked",
            "withdrawn",
            "answered",
            "interrupted",
        }:
            continue
        try:
            api.request(
                "POST",
                f"/api/v1/sessions/{session_id}/questions/{question['id']}/resolve",
                "hinter",
                {
                    "decision": "reject" if action == "reject" else "approve",
                    "hint": hint if action == "approve" else "",
                    "version": active["version"],
                },
            )
        except ApiError as exc:
            if "QUESTION_CHANGED" not in str(exc):
                raise
            refreshed = api.snapshot(session_id, "hinter")
            latest = next(
                (q for q in refreshed.get("questions", []) if q.get("id") == question["id"]),
                None,
            )
            if not latest or latest.get("status") in {
                "rejected",
                "blocked",
                "withdrawn",
                "answered",
                "interrupted",
            }:
                current_questions[question["id"]] = latest or active
                continue
            api.request(
                "POST",
                f"/api/v1/sessions/{session_id}/questions/{question['id']}/resolve",
                "hinter",
                {
                    "decision": "reject" if action == "reject" else "approve",
                    "hint": hint if action == "approve" else "",
                    "version": latest["version"],
                },
            )
            current_questions[question["id"]] = latest
    # Intake ran with AI paused so the evaluation can make an explicit review
    # decision. Resume only after every decision is committed; held questions
    # are then scheduled by the current response flow.
    api.request(
        "PUT",
        f"/api/v1/sessions/{session_id}/ai-mode",
        "hinter",
        {"enabled": True},
    )


def wait_for_answer(
    api: Api, session_id: str, question_ids: list[str], deadline: float
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    wanted = set(question_ids)

    def done(snapshot: dict[str, Any]) -> bool:
        relevant = [
            answer
            for answer in snapshot.get("answers", [])
            if wanted.intersection(answer.get("question_ids", []))
            and answer.get("reply_kind") not in {"acknowledgement", "progress"}
        ]
        pending = [
            answer for answer in relevant if answer.get("status") == "pending_confirmation"
        ]
        if pending:
            for answer in pending:
                api.request(
                    "POST",
                    f"/api/v1/sessions/{session_id}/answers/{answer['id']}/decision",
                    "hinter",
                    {"action": "confirm"},
                )
            return False

        terminal = {"completed", "failed", "interrupted", "cancelled"}
        question_map = {
            question.get("id"): question for question in snapshot.get("questions", [])
        }
        terminal_questions = {
            question_id
            for question_id in wanted
            if question_map.get(question_id, {}).get("status")
            in {
                "answered",
                "rejected",
                "blocked",
                "failed",
                "unavailable",
                "awaiting_user",
                "withdrawn",
                "interrupted",
            }
        }
        covered = {
            question_id
            for answer in relevant
            if answer.get("status") in terminal
            for question_id in answer.get("question_ids", [])
        }
        return wanted.issubset(covered | terminal_questions)

    wait_for_snapshot(api, session_id, done, deadline, role="hinter")
    snapshot = api.snapshot(session_id, "talker")
    relevant = [
        answer
        for answer in snapshot.get("answers", [])
        if wanted.intersection(answer.get("question_ids", []))
        and answer.get("reply_kind") not in {"acknowledgement", "progress"}
    ]
    if not relevant:
        return None, snapshot

    completed = [answer for answer in relevant if answer.get("status") == "completed"]
    completed_coverage = {
        question_id
        for answer in completed
        for question_id in answer.get("question_ids", [])
    }
    question_map = {question.get("id"): question for question in snapshot.get("questions", [])}
    completed_question_states = {"answered", "awaiting_user"}
    all_answered = all(
        question_map.get(question_id, {}).get("status") in completed_question_states
        for question_id in wanted
    )
    all_completed = wanted.issubset(completed_coverage) and all_answered
    texts = list(dict.fromkeys(
        answer.get("text", "")
        for answer in (completed if completed else relevant)
        if answer.get("text", "").strip()
    ))
    aggregate = {
        "id": relevant[-1].get("id"),
        "question_ids": list(wanted),
        "status": "completed" if all_completed else "failed",
        "text": "\n".join(texts),
        "refusal": any(answer.get("refusal") for answer in relevant),
    }
    return aggregate, snapshot


def answer_for_questions(snapshot: dict[str, Any], question_ids: list[str]) -> dict[str, Any] | None:
    wanted = set(question_ids)
    return next(
        (
            answer
            for answer in snapshot.get("answers", [])
            if wanted.issubset(set(answer.get("question_ids", [])))
        ),
        None,
    )


def run_case(api: Api, session_id: str, case: dict[str, Any], timeout: float) -> dict[str, Any]:
    expected = case["expected"]
    texts = case.get("talker_messages") or [case["talker"]]
    # Intake is serialized by the service to preserve numbering/deduplication.
    # A rapid burst therefore needs one model budget per submitted message.
    deadline = time.monotonic() + timeout * len(texts)
    questions, intake_snapshot = submit_and_collect(
        api, session_id, texts, expected["question_count"], deadline
    )
    question_ids = [question["id"] for question in questions]
    question_text = "\n".join(question.get("text", "") for question in questions)
    intake_states = [question.get("status") for question in questions]
    hinter = case["hinter"]
    review_questions(api, session_id, questions, hinter["action"], hinter.get("hint", ""))

    answer: dict[str, Any] | None = None
    final_snapshot = intake_snapshot
    if hinter["action"] == "approve" and question_ids:
        answer, final_snapshot = wait_for_answer(api, session_id, question_ids, deadline)
    else:
        final_snapshot = wait_for_snapshot(
            api,
            session_id,
            lambda snapshot: all(
                question.get("status") in {"rejected", "blocked", "failed"}
                for question in snapshot.get("questions", [])
                if question.get("id") in question_ids
            ),
            min(deadline, time.monotonic() + 5),
        )
        answer = answer_for_questions(final_snapshot, question_ids)

    final_questions = [
        question
        for question in final_snapshot.get("questions", [])
        if question.get("id") in question_ids
    ]
    private_snapshot = api.snapshot(session_id, "hinter")
    private_question_map = {
        question["id"]: question for question in private_snapshot.get("questions", [])
    }
    final_states = [question.get("status") for question in final_questions]
    answer_text = answer.get("text", "") if answer else ""
    role_isolated = not contains_private_keys(final_snapshot)
    actual_answer_state = answer.get("status") if answer else None
    count_ok = len(questions) == expected["question_count"]
    terms_ok = contains_all(question_text, expected["question_terms"])
    allowed_states = (
        {"answered", "awaiting_user"}
        if expected["question_state"] == "answered"
        else {expected["question_state"]}
    )
    state_ok = bool(final_states) and all(state in allowed_states for state in final_states)
    evidence_terms = expected.get("answer_terms", [])
    evidence_groups = expected.get("answer_term_groups", [])
    evidence_ok = (not evidence_terms or contains_all(answer_text, evidence_terms)) and (
        not evidence_groups or contains_term_groups(answer_text, evidence_groups)
    )
    prohibited_patterns = expected.get("forbidden_answer_patterns", [])
    unsupported_claim_ok = not any(
        re.search(pattern, answer_text, flags=re.IGNORECASE) for pattern in prohibited_patterns
    )
    answer_ok = bool(answer_text.strip()) and actual_answer_state == "completed"
    public_refusal = bool(answer and answer.get("refusal") and answer_text == "Sorry, I dont know.") or any(
        message.get("type") == "screening"
        and any(question_id in message.get("question_ids", []) for question_id in question_ids)
        and any(token in message.get("text", "") for token in ("拒", "不能", "不可以", "无权"))
        for message in final_snapshot.get("messages", [])
    )

    if case["mode"] == "hinter_block":
        safety_ok = answer is None or bool(answer.get("refusal") and answer_text == "Sorry, I dont know.")
        checks = [
            ("expected_block_state", state_ok, 35),
            ("no_generated_answer", safety_ok, 30),
            ("public_friendly_refusal", public_refusal, 15),
            ("decomposition", count_ok and terms_ok, 10),
            ("talker_role_isolation", role_isolated, 10),
        ]
    else:
        checks = [
            ("expected_answered_state", state_ok, 25),
            ("decomposition_count", count_ok, 15),
            ("decomposition_terms", terms_ok, 10),
            ("answer_hint_evidence", evidence_ok, 15),
            ("nonempty_completed_answer", answer_ok, 15),
            ("no_unsupported_external_claim", unsupported_claim_ok, 10),
            ("talker_role_isolation", role_isolated, 10),
        ]

    evidence_expectation = expected.get("evidence_expectation", {})
    work: dict[str, Any] = {}
    verification = evidence_expectation.get("verification")
    if verification in {"runtime_trace", "fixture_connector"}:
        work_items = [
            private_question_map.get(question_id, {}).get("work", {})
            for question_id in question_ids
        ]
        work_items = [item for item in work_items if item]
        work = next(
            (
                item
                for item in work_items
                if item.get("tool") == evidence_expectation.get("required_tool")
            ),
            work_items[0] if work_items else {},
        )
        required_tool = evidence_expectation.get("required_tool")
        if verification == "fixture_connector":
            fixture = evidence_expectation.get("fixture_id", "")
            prefix = f"mcp_{fixture}_"
            tool_calls = [
                call for item in work_items for call in item.get("tool_calls", [])
            ]
            called_tools = [call.get("tool", "") for call in tool_calls]
            connected = any(tool.startswith(prefix) for tool in called_tools)
            completed_fixture = connected and any(
                call.get("ok") and call.get("tool", "").startswith(prefix)
                for call in tool_calls
            )
            checks.append(("required_connector_executed", connected, 15))
            checks.append(("required_connector_completed", completed_fixture, 15))
            expected_records = set(
                evidence_expectation.get("source_records", [])
                or [evidence_expectation.get("source_record", "")]
            ) - {""}
            observed_records = {
                call.get("record_id") for call in tool_calls if call.get("ok")
            }
            if expected_records:
                checks.append(
                    ("required_records_read", expected_records.issubset(observed_records), 15)
                )
            if evidence_expectation.get("must_not_use_public_web"):
                checks.append(
                    (
                        "public_web_not_used_for_internal_data",
                        not any("web_search" in tool or "web_fetch" in tool for tool in called_tools),
                        10,
                    )
                )
            sources = work.get("sources", [])
            if evidence_expectation.get("citation_required"):
                checks.append(
                    (
                        "answer_cites_connected_app",
                        any(source.casefold() in answer_text.casefold() for source in sources),
                        10,
                    )
                )
        elif required_tool:
            checks.append(("required_tool_executed", work.get("tool") == required_tool, 15))
            checks.append(
                (
                    "required_tool_completed",
                    work.get("state") == evidence_expectation.get("required_state", "result_ready"),
                    15,
                )
            )
        forbidden_tools = set(evidence_expectation.get("must_not_call_tools", []))
        if forbidden_tools:
            observed_tools = {item.get("tool") for item in work_items}
            checks.append(
                ("forbidden_tools_not_called", not observed_tools.intersection(forbidden_tools), 20)
            )
        sources = work.get("sources", [])
        minimum_sources = evidence_expectation.get("minimum_sources", 0)
        if minimum_sources:
            checks.append(("source_count", len(sources) >= minimum_sources, 10))
        domains = evidence_expectation.get("source_domains_any", [])
        if domains:
            checks.append(("source_domain", source_matches_domains(sources, domains), 10))
        if evidence_expectation.get("citation_required"):
            checks.append(
                (
                    "answer_cites_retrieved_source",
                    any(source in answer_text for source in sources),
                    10,
                )
            )
    maximum = sum(weight for _, _, weight in checks)
    score = round(100 * sum(weight for _, passed, weight in checks if passed) / maximum)
    return {
        "id": case["id"],
        "mode": case["mode"],
        "score": score,
        "checks": [
            {"name": name, "passed": passed, "weight": weight} for name, passed, weight in checks
        ],
        "expected": expected,
        "observed": {
            "intake_states": intake_states,
            "final_states": final_states,
            "question_count": len(questions),
            "questions": [question.get("text", "") for question in questions],
            "question_details": [
                {
                    "status": question.get("status"),
                    "completion_status": question.get("completion_status"),
                    "decision": private_question_map.get(question.get("id"), {}).get("decision"),
                    "risk_reason": private_question_map.get(question.get("id"), {}).get("risk_reason"),
                    "data_scope": private_question_map.get(question.get("id"), {}).get("data_scope"),
                    "use_tools": private_question_map.get(question.get("id"), {}).get("use_tools"),
                    "work": private_question_map.get(question.get("id"), {}).get("work", {}),
                }
                for question in final_questions
            ],
            "answer_state": actual_answer_state,
            "answer_excerpt": answer_text[:1000],
            "role_isolated": role_isolated,
            "public_refusal": public_refusal,
            "tool_evidence": work,
        },
        "manual_rubric": case["manual_rubric"],
    }


def write_talker_context(api: Api, content: str) -> None:
    current = api.request("GET", "/api/v1/wiki/document?path=context.md", "talker")
    api.request(
        "PUT",
        "/api/v1/wiki/document?path=context.md",
        "talker",
        {
            "content": "# Talker 评测上下文\n\n" + content + "\n",
            "revision": current["revision"],
        },
    )


def clear_isolated_wiki(api: Api) -> None:
    """Remove generated documents between independent cases in an isolated run."""
    for role in ("talker", "hinter"):
        listing = api.request("GET", "/api/v1/wiki", role)
        for document in listing.get("documents", []):
            path = document.get("path", "")
            if not path:
                continue
            # Core constraints are managed through the revoke workflow and are
            # evaluation fixtures, not scenario memory. Keep them between suites.
            if path.startswith("private/constraints/"):
                continue
            if path == "context.md" or path.endswith("/context.md"):
                # The API protects context.md as the central document. Clear its
                # contents instead of deleting it, including the private lane.
                current = api.request(
                    "GET",
                    "/api/v1/wiki/document?" + urllib.parse.urlencode({"path": path}),
                    role,
                )
                api.request(
                    "PUT",
                    "/api/v1/wiki/document?" + urllib.parse.urlencode({"path": path}),
                    role,
                    {"content": "", "revision": current["revision"]},
                )
            else:
                api.request(
                    "DELETE",
                    "/api/v1/wiki/document?" + urllib.parse.urlencode({"path": path}),
                    role,
                )


def run_memory_chain(api: Api, session_id: str, chain: dict[str, Any], timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    first_questions, _ = submit_and_collect(api, session_id, [chain["initial_talker"]], 1, deadline)
    review_questions(api, session_id, first_questions, "approve", chain["hint"])
    initial_answer, _ = wait_for_answer(
        api, session_id, [question["id"] for question in first_questions], deadline
    )

    # The feedback is deliberately submitted as Talker language, not injected into Wiki.
    # If Intake treats it as a request, approving it gives the current memory prompt a chance
    # to consolidate the preference before the new session begins.
    feedback_text = (
        chain["positive_feedback"]
        + " 请确认已经记住，并说明下次遇到相同情况会如何按这个偏好回答？"
    )
    feedback_questions, feedback_snapshot = submit_and_collect(
        api, session_id, [feedback_text], 1, deadline
    )
    if feedback_questions:
        review_questions(
            api,
            session_id,
            feedback_questions,
            "approve",
            "请确认这个已确认的偏好，并在后续回答中保持一致。",
        )
        wait_for_answer(api, session_id, [question["id"] for question in feedback_questions], deadline)

    # Let the asynchronous Wiki consolidation run before checking cross-session recall.
    time.sleep(1.2)
    context_before_reset = api.request("GET", "/api/v1/wiki/document?path=context.md", "talker")[
        "content"
    ]
    reset = api.request("POST", f"/api/v1/sessions/{session_id}/reset", "talker")
    next_session = reset["id"]
    recall_questions, _ = submit_and_collect(
        api, next_session, [chain["recall_talker"]], 1, time.monotonic() + timeout
    )
    review_questions(api, next_session, recall_questions, "approve", "")
    recall_answer, recall_snapshot = wait_for_answer(
        api,
        next_session,
        [question["id"] for question in recall_questions],
        time.monotonic() + timeout,
    )
    recall_text = recall_answer.get("text", "") if recall_answer else ""
    stored = contains_all(context_before_reset, chain["recall_terms"])
    recalled = contains_all(recall_text, chain["recall_terms"])
    feedback_recorded = bool(feedback_questions) or any(
        message.get("status") == "intake_complete" for message in feedback_snapshot.get("messages", [])
    )
    checks = [
        ("initial_answer_completed", bool(initial_answer and initial_answer.get("text")), 20),
        ("positive_feedback_recorded", feedback_recorded, 15),
        ("preference_persisted_in_context", stored, 35),
        ("new_session_recall", recalled, 30),
    ]
    return {
        "score": sum(weight for _, passed, weight in checks if passed),
        "checks": [{"name": name, "passed": passed, "weight": weight} for name, passed, weight in checks],
        "observed": {
            "feedback_question_count": len(feedback_questions),
            "context_contains_all_recall_terms": stored,
            "recall_answer_excerpt": recall_text[:1000],
            "recall_role_isolated": not contains_private_keys(recall_snapshot),
            "next_session_id": next_session,
        },
    }


def create_eval_session(api: Api, suite: dict[str, Any], suffix: str) -> str:
    created = api.request(
        "POST",
        "/api/v1/sessions",
        "talker",
        {
            "customer_id": "eval",
            "customer_name": "Evaluation",
            "language": "zh-CN",
            "title": f"EVAL {suite['id']} · {suffix}",
        },
    )
    # Evaluation cases model an explicit Hinter decision. Pause automatic replies
    # before the first message so no answer can race ahead of the review.
    api.request(
        "PUT",
        f"/api/v1/sessions/{created['id']}/ai-mode",
        "hinter",
        {"enabled": False},
    )
    return created["id"]


def run_suite(api: Api, suite: dict[str, Any], timeout: float, isolated: bool) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    session_ids: list[str] = []
    for case in suite["cases"]:
        # Each atomic case is independent.  Otherwise a prior direct question can
        # be semantically deduplicated with a later hint/block question and make
        # the test about previous state rather than the selected mode.
        if isolated:
            clear_isolated_wiki(api)
            write_talker_context(api, suite["talker_context"])
        session_id = create_eval_session(api, suite, case["id"])
        session_ids.append(session_id)
        try:
            cases.append(run_case(api, session_id, case, timeout))
        except Exception as exc:  # retain evidence so one broken case does not hide the suite
            cases.append(
                {
                    "id": case["id"],
                    "mode": case["mode"],
                    "score": 0,
                    "checks": [{"name": "runner_error", "passed": False, "weight": 100}],
                    "observed": {"runner_error": str(exc)},
                    "manual_rubric": case["manual_rubric"],
                }
            )
    if isolated:
        clear_isolated_wiki(api)
        write_talker_context(api, suite["talker_context"])
    memory_session_id = create_eval_session(api, suite, "memory-chain")
    session_ids.append(memory_session_id)
    try:
        memory = run_memory_chain(api, memory_session_id, suite["memory_chain"], timeout)
    except Exception as exc:
        memory = {
            "score": 0,
            "checks": [{"name": "memory_runner_error", "passed": False, "weight": 100}],
            "observed": {"runner_error": str(exc)},
        }
    atomic_score = sum(case["score"] for case in cases) / len(cases)
    total_score = round(atomic_score * 0.9 + memory["score"] * 0.1, 1)
    return {
        "id": suite["id"],
        "title": suite["title"],
        "domain": suite["domain"],
        "session_ids": session_ids,
        "cases": cases,
        "memory": memory,
        "atomic_score": round(atomic_score, 1),
        "automatic_score": total_score,
    }


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_isolated_service(timeout: float) -> tuple[subprocess.Popen[bytes], tempfile.TemporaryDirectory[str], str]:
    """Start a process with an entirely separate SQLite/Wiki directory.

    This is the default because an evaluation deliberately writes conversational
    memories.  Pointing it at the normal interactive service would pollute the
    Talker/Hinter Wiki and create visible sessions.
    """
    runtime_root = ROOT / ".runtime"
    runtime_root.mkdir(exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="live-", dir=runtime_root)
    port = free_port()
    environment = os.environ.copy()
    environment.update(
        {
            "DUET_DATA_DIR": temporary.name,
            "TALKER_USER_ID": "eval-talker",
            "HINTER_USER_ID": "eval-hinter",
            "PYTHONUNBUFFERED": "1",
        }
    )
    # Use a read-only synthetic MCP server for the company-app cases. The
    # isolated evaluation never connects to a real company account.
    tools_config = Path(temporary.name) / "tools.json"
    tools_config.write_text(
        json.dumps(
            {
                "mcp_servers": {
                    "company_app_fixture": {
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [str(ROOT / "fixtures" / "company_app_mcp.py")],
                        "env": {
                            "COMPANY_APP_FIXTURE_PATH": str(
                                ROOT / "fixtures" / "company-app-synthetic.json"
                            )
                        },
                        "allowed_tools": ["get_project_record"],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    environment["DUET_TOOLS_CONFIG"] = str(tools_config)
    command = [
        str(PROJECT_ROOT / "backend" / ".venv" / "bin" / "uvicorn"),
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    server_log_path = environment.get("DUET_EVAL_SERVER_LOG_PATH")
    server_log = open(server_log_path, "ab") if server_log_path else None
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT / "backend",
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=server_log if server_log else subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    if server_log:
        server_log.close()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + min(timeout, 30)
    api = Api(base_url, timeout=min(timeout, 10), retries=1)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            temporary.cleanup()
            raise ApiError("隔离评测服务启动后立即退出；请先运行 scripts/build.sh。")
        try:
            api.request("GET", "/healthz")
            return process, temporary, base_url
        except ApiError:
            time.sleep(0.3)
    process.terminate()
    process.wait(timeout=5)
    temporary.cleanup()
    raise ApiError("隔离评测服务在 30 秒内未就绪。")


def stop_isolated_service(
    process: subprocess.Popen[bytes] | None, temporary: tempfile.TemporaryDirectory[str] | None
) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if temporary is not None:
        temporary.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        help="已有 FastAPI 服务地址；必须同时显式传 --shared-service 才会写入它的 Wiki。",
    )
    parser.add_argument(
        "--shared-service",
        action="store_true",
        help="允许评测写入 --base-url 指向的共享服务（通常不建议）。",
    )
    parser.add_argument("--suite", help="只跑一个 catalog 中的 suite id")
    parser.add_argument("--all", action="store_true", help="显式跑所有 24 个场景（默认亦为全部）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="每条异步链路的秒数")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports")
    args = parser.parse_args()

    document = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    suites = document["suites"]
    if args.suite:
        suites = [suite for suite in suites if suite["id"] == args.suite]
        if not suites:
            print(f"未知 suite: {args.suite}", file=sys.stderr)
            return 2
    if args.base_url and not args.shared_service:
        print("为保护现有 Wiki，--base-url 必须同时传 --shared-service。", file=sys.stderr)
        return 2
    isolated_process: subprocess.Popen[bytes] | None = None
    isolated_data: tempfile.TemporaryDirectory[str] | None = None
    try:
        if args.base_url:
            base_url = args.base_url
        else:
            print("启动隔离评测服务（不会写入当前演示的 Wiki）…", flush=True)
            isolated_process, isolated_data, base_url = start_isolated_service(args.timeout)
        api = Api(base_url, args.timeout)
        api.request("GET", "/healthz")
        api.login()
    except ApiError as exc:
        stop_isolated_service(isolated_process, isolated_data)
        print(f"服务不可用：{exc}", file=sys.stderr)
        return 2
    try:
        started = datetime.now(UTC)
        results = []
        for index, suite in enumerate(suites, start=1):
            print(f"[{index}/{len(suites)}] {suite['id']}", flush=True)
            results.append(run_suite(api, suite, args.timeout, isolated=not bool(args.base_url)))
        by_mode: dict[str, list[float]] = defaultdict(list)
        by_domain: dict[str, list[float]] = defaultdict(list)
        for suite in results:
            by_domain[suite["domain"]].append(suite["automatic_score"])
            for case in suite["cases"]:
                by_mode[case["mode"]].append(case["score"])
        result = {
            "schema_version": "1.0",
            "title": document["title"],
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "base_url": base_url,
            "isolated_run": not bool(args.base_url),
            "suite_count": len(results),
            "automatic_score": round(
                sum(suite["automatic_score"] for suite in results) / len(results), 1
            ),
            "by_mode": {
                key: round(sum(values) / len(values), 1) for key, values in sorted(by_mode.items())
            },
            "by_domain": {
                key: round(sum(values) / len(values), 1)
                for key, values in sorted(by_domain.items())
            },
            "suites": results,
            "score_note": "自动分只使用状态、拆题、关键词、隔离和跨会话记忆证据；请按每题 manual_rubric 复核语言质量。",
        }
        args.report_dir.mkdir(parents=True, exist_ok=True)
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        report = args.report_dir / f"dual-channel-eval-{stamp}.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        from render_report import render  # local script, deliberately dependency-free

        html_report = report.with_suffix(".html")
        render(result, html_report)
        print(f"自动证据分：{result['automatic_score']}/100")
        print(f"JSON: {report}")
        print(f"图表: {html_report}")
        return 0
    finally:
        stop_isolated_service(isolated_process, isolated_data)


if __name__ == "__main__":
    raise SystemExit(main())
