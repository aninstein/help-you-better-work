"""Keep dependent attributes of one request in one public answer."""

from __future__ import annotations

import re

from app.agent.call_policy import CallPolicy


def _boundary(plan: dict, constraints: str) -> tuple:
    action, _ = CallPolicy().decide(plan, constraints)
    # Privacy, explicit approval and clarification remain independent decisions.
    special = action if action in {"block", "refuse", "await_human", "clarify"} else "automatic"
    return special, plan.get("data_scope"), tuple(sorted(plan.get("matched_rule_ids", [])))


def coalesce_requests(text: str, plans: list[dict], constraints: str) -> list[dict]:
    if len(plans) < 2:
        return plans
    groups: list[list[tuple[int, dict]]] = []
    for index, plan in enumerate(plans):
        if not groups:
            groups.append([(index, plan)])
            continue
        previous = groups[-1][-1][1]
        group_id, previous_id = plan.get("answer_group"), previous.get("answer_group")
        explicit_group = bool(group_id and group_id == previous_id)
        different_entity = (plan.get("entity_key") and previous.get("entity_key")
                            and plan["entity_key"].casefold() != previous["entity_key"].casefold())
        start = text.find(previous["text"])
        end = text.find(plan["text"], max(0, start + len(previous["text"])))
        gap = text[start + len(previous["text"]):end] if start >= 0 and end >= 0 else "."
        # Backward compatibility with routers omitting grouping metadata: an
        # attribute fragment in a connected list inherits the preceding subject.
        # Full independent questions, sentences and record identifiers stay apart.
        fragment = (
            not group_id and not previous_id and not different_entity
            and previous.get("category") == plan.get("category") == "data_lookup"
            and bool(re.fullmatch(r"[\s,，、]*(?:and|以及|和|及|与)?[\s,，、]*", gap, re.I))
            and not re.search(r"[?.!。？！;；]", previous["text"])
            and not re.match(
                r"(?:what|where|when|why|how|who|which|is|are|can|could|would|do|does|"
                r"i\b|we\b|you\b|please|search|fetch|query|read|show|tell|explain|"
                r"查询|搜索|请|如何|怎么|什么|读取)", plan["text"].strip(), re.I,
            )
            and not re.fullmatch(r"[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*", plan["text"].strip())
        )
        same_boundary = _boundary(previous, constraints) == _boundary(plan, constraints)
        if (explicit_group or fragment) and same_boundary and not different_entity:
            groups[-1].append((index, plan))
        else:
            groups.append([(index, plan)])

    remap = {old: new for new, group in enumerate(groups) for old, _ in group}
    result = []
    for new, group in enumerate(groups):
        merged = dict(group[0][1])
        if len(group) > 1:
            members = [plan for _, plan in group]
            start = text.find(members[0]["text"])
            last_start = text.rfind(members[-1]["text"])
            merged["text"] = (text if len(groups) == 1 else
                              text[start:last_start + len(members[-1]["text"])])
            merged["subrequests"] = [plan["text"] for plan in members]
            quotes = list(dict.fromkeys(quote for plan in members
                                        for quote in plan.get("evidence_quotes", []) or []))
            merged["evidence_quotes"] = quotes
            merged["evidence_gap"] = " ".join(dict.fromkeys(
                str(plan["evidence_gap"]) for plan in members if plan.get("evidence_gap")
            ))
            supported = any(plan.get("evidence_source") == "provided"
                            and plan.get("evidence_quotes") for plan in members)
            merged["evidence_status"] = (
                "sufficient" if all(plan.get("evidence_status") == "sufficient" for plan in members)
                else "partial" if supported else "missing"
            )
            if supported:
                merged.update(evidence_source="provided", uses_wiki_facts=True)
            merged["use_tools"] = any(plan.get("use_tools") for plan in members)
            # The policy makes the final choice, with all supplied facts/gaps in
            # one plan. A failed field cannot create an extra terminal fallback.
            merged["action"] = "lookup" if any(plan.get("action") == "lookup" for plan in members) else merged.get("action", "answer")
            action, _ = CallPolicy().decide(merged, constraints)
            merged["action"] = action
            merged["web_query"] = merged["text"] if action == "lookup" else ""
            if action == "answer" and supported:
                merged["use_tools"] = False
        merged["depends_on"] = sorted({
            remap[d] for _, plan in group for d in plan.get("depends_on", [])
            if d in remap and remap[d] < new
        })
        result.append(merged)
    return result
