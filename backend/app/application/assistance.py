"""Ground short human replies in the caller's request and the requested assistance."""

from __future__ import annotations

import json
import re

PENDING_STATES = {"held", "expired", "unavailable", "legacy_pending", "awaiting_user"}
RESPONSE_INSTRUCTION = (
    "结合原始提问和人工补充，用简洁自然的英文直接回答；保留原值、单位、范围及否定。"
    "人工补充未提供的数量级、时间或统计口径不得自行添加；禁止内容应转为遵守限制的回答。"
)


def is_short_value(text: str) -> bool:
    """Only infer a target for a clearly standalone value, never a general instruction."""
    return bool(
        re.fullmatch(
            r"[+-]?\s*[$€£¥]?\s*\d[\d,.]*(?:\s*(?:%|percent|million|billion|thousand|"
            r"[kmb]|万|亿|个|人|元|倍))?",
            text.strip(),
            re.I,
        )
    )


def assistance_request(question: dict, plan: dict | None = None) -> dict:
    proposed = (plan or question).get("assistance_request")
    proposed = proposed if isinstance(proposed, dict) else {}
    text = question["text"]
    return {
        "question_summary": str(proposed.get("question_summary") or text).strip()[:1200],
        "information_needed": str(
            proposed.get("information_needed")
            or (
                f"请提供「{text}」所询问的数据或事实，可直接填写数值及已有单位。"
                if question.get("risk_reason") == "human_evidence_required"
                else f"请为「{text}」补充所需事实、回答方向或适用限制。"
            )
        ).strip()[:1200],
        # The composition contract is server owned, not an answer invented by the planner.
        "response_instruction": RESPONSE_INSTRUCTION,
    }


def question_context(state: dict, question: dict) -> dict:
    sources = [
        {"message_id": m["id"], "text": m["text"]}
        for m in state["messages"]
        if m["id"] in question.get("source_message_ids", [])
    ]
    return {
        "question_id": question["id"],
        "original_messages": sources,
        "extracted_question": question["text"],
        "assistance_request": assistance_request(question),
    }


def render_guidance(record: dict) -> str:
    if not record.get("binding") and not record.get("context_snapshot"):
        return record["text"]
    context = record.get("context_snapshot")
    return (
        record["text"]
        + "\nHINTER INPUT WITH CONVERSATION CONTEXT:\n"
        + json.dumps(
            {
                **(record.get("binding") or {}),
                **({"conversation_context": context} if context else {}),
                "hinter_input": record["text"],
                "input_kind": record["kind"],
                "applies_to": "question" if record.get("question_id") else record["scope"],
            },
            ensure_ascii=False,
        )
    )
