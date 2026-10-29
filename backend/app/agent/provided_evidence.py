"""Separate current supplied facts from contextual conversation/audit snapshots."""

from __future__ import annotations

import json
import re

CONTEXT_MARKER = "\nHINTER INPUT WITH CONVERSATION CONTEXT:\n"
RULE_HEADER = re.compile(r"^\[([^\]\n]+) / (\w+) / ([^\]\n]+)\] ", re.M)


def guidance_blocks(constraints: str) -> list[tuple[str, str, str, str]]:
    matches = list(RULE_HEADER.finditer(constraints))
    return [
        (*match.groups(), constraints[match.end():matches[i + 1].start()
                                      if i + 1 < len(matches) else len(constraints)].strip())
        for i, match in enumerate(matches)
    ]


def evidence_material(wiki: str, constraints: str) -> str:
    """Only actual supplied text can support a quote, never old AI/status/context."""
    parts = [wiki]
    for rule_id, kind, _, body in guidance_blocks(constraints):
        if kind in {"instruction", "authorized_fact"}:
            parts.append(f"HINTER SUPPLIED DATA [{rule_id}]:\n" + body.split(CONTEXT_MARKER)[0])
    return "\n\n".join(parts).strip()


def routing_constraints(constraints: str) -> str:
    """Keep scope/binding, but don't replay historical AI answers as new evidence."""
    blocks = guidance_blocks(constraints)
    if not blocks:
        return constraints
    # Preserve any unstructured prefix for legacy constraints.
    result = [constraints[:RULE_HEADER.search(constraints).start()].strip()]
    for rule_id, kind, scope, body in blocks:
        text, _, encoded = body.partition(CONTEXT_MARKER)
        binding = {}
        if encoded:
            try:
                context = json.loads(encoded)
                binding = {k: context[k] for k in
                           ("question_id", "extracted_question", "applies_to", "target_part") if k in context}
            except (ValueError, TypeError):
                pass
        result.append(f"[{rule_id} / {kind} / {scope}] {text}" + (
            "\nBinding (context only): " + json.dumps(binding, ensure_ascii=False) if binding else ""
        ))
    return "\n\n".join(part for part in result if part)


def external_verification_requested(text: str) -> bool:
    return bool(re.search(
        r"\b(?:search|browse|web search|look up|verify|verification|official sources?|"
        r"cite sources?|source links?)\b|联网|搜索|核实|核验|官方来源|来源链接|查证",
        text, re.I,
    ))
