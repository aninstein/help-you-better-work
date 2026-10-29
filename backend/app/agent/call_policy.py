"""Small, editable call routing policy. Word matches are evidence, not verdicts."""

import re
import unicodedata
from pathlib import Path

import yaml

from app.agent.call_search import CallSearch
from app.agent.provided_evidence import external_verification_requested
from app.agent.tool_config import load_tool_config

ROOT = Path(__file__).parent


class CallPolicy:
    def __init__(self) -> None:
        self.config = yaml.safe_load((ROOT / "call_policy.yaml").read_text())
        self.words: dict[str, list[str]] = {}
        for language in ["en", "zh"]:
            path = ROOT / "lexicons" / language
            self.words[language] = path.read_text().splitlines() if path.exists() else []

    def signals(self, text: str) -> list[str]:
        value = unicodedata.normalize("NFKC", text).lower()
        found = []
        for language, words in self.words.items():
            for word in words:
                word = word.strip().lower()
                matched = (
                    word in value
                    if language == "zh"
                    else re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", value)
                )
                if word and matched:
                    found.append(word)
                    if len(found) >= 8:
                        return found
        return found

    def category(self, text: str, suggested: str) -> str:
        if suggested == "harmful":
            return suggested
        for category in ["data_lookup", "open_domain"]:
            if any(re.search(pattern, text, re.I) for pattern in self.config["signals"][category]):
                return category
        if (
            suggested == "data_lookup"
            and re.search(r"\b(?:define|meaning|explain|stand for|definition)\b", text, re.I)
            and re.search(r"\b(?:DAU|API|HTTP)\b", text, re.I)
            and not re.search(
                r"\b(?:our|my|actual|yesterday|today|query|fetch|retrieve|account|id)\b|\d",
                text,
                re.I,
            )
        ):
            # A request for a metric's meaning isn't a request to retrieve its value.
            return "routine"
        return suggested if suggested in self.config["actions"] else "ambiguous"

    def decide(self, plan: dict, constraints: str) -> tuple[str, str]:
        """Categories describe topics. Only explicit narrow exceptions require a human."""
        category = self.category(str(plan.get("text", "")), str(plan.get("category", "routine")))
        action = plan.get("action", "answer")
        matched = [str(rule) for rule in plan.get("matched_rule_ids", [])]
        if any(f"[{rule} / prohibition" in constraints for rule in matched):
            return "block", "operator_prohibition"
        if category == "harmful":
            return "refuse", "unsafe_assistance"
        if any(f"[{rule} / require_human" in constraints for rule in matched):
            return "await_human", "human_confirmation_required"
        text = str(plan.get("text", ""))
        if CallSearch.explicit_filing_request(text) and load_tool_config().web_search_enabled:
            return "lookup", "public_filing_source_required"
        # Ambiguity asks the caller; it never becomes a human evidence request
        # merely because the topic was classified as data_lookup.
        if action in ["clarify", "refuse"]:
            return action, action
        if (plan.get("evidence_source") == "provided" and plan.get("data_scope") == "public"
                and external_verification_requested(text)):
            return "lookup", "public_verification_requested"
        if CallSearch.needs_public_completion(plan):
            return "lookup", "public_evidence_incomplete"
        if plan.get("evidence_status") == "sufficient" or (
            plan.get("evidence_status") == "partial"
            and plan.get("evidence_source") == "provided"
            and plan.get("evidence_quotes")
        ):
            return "answer", "provided_evidence"
        if plan.get("data_scope") == "internal":
            if plan.get("use_tools") and load_tool_config().mcp_servers:
                # An explicitly selected, configured MCP connector is an
                # authorized source for internal records; public search is not.
                return "lookup", "authorized_mcp_lookup"
            return "await_human", "human_evidence_required"
        if plan.get("data_scope") == "unknown":
            return "clarify", "unclear_data_scope"
        supplied_answer = plan.get("evidence_status") == "sufficient" or (
            plan.get("evidence_status") is None and plan.get("uses_wiki_facts")
        )
        if (
            CallSearch.internal_data_request(text)
            and not CallSearch.metric_definition(text)
            and not (action == "answer" and supplied_answer)
        ):
            return "await_human", "human_evidence_required"
        if (
            not supplied_answer
            and CallSearch.plan_query(plan)
            and re.search(
                r"\b(?:weather|price|stock|news|today|current|latest)\b|天气",
                text,
                re.I,
            )
        ):
            return "lookup", "public_lookup"
        if action == "await_human" and category == "data_lookup":
            return "await_human", "human_evidence_required"
        if not supplied_answer and CallSearch.plan_query(plan):
            return "lookup", "public_lookup"
        if action in ["await_human", "lookup", "limited_answer"]:
            # Missing public evidence, failed tools and conflicting generic hints
            # cannot manufacture an operator approval requirement.
            return "limited_answer", "evidence_unavailable"
        return "answer", "ordinary"
