"""Ordered input stages: rules -> language -> optional semantic review.

Runs before record/Run creation and outside database locks. Future stages should
return metadata here rather than duplicating policy in ASR, UI or Agent code.
"""

import logging

from .classifier import classify
from .language import detect_language, mixed_language
from .rules import needs_semantic_review, transcript_ignore_reason
from .types import InputDecision

log = logging.getLogger(__name__)


def preprocess(text, kind="text", context=()):
    previous = next(
        (
            item.get("reply_language", "und")
            for item in reversed(context)
            if item.get("role") == "user" and item.get("reply_language", "und") != "und"
        ),
        "und",
    )
    language = detect_language(text, previous)
    if kind == "transcript":
        reason = transcript_ignore_reason(text)
        if reason:
            return InputDecision(False, language, reason)
    speech_review = kind == "transcript" and needs_semantic_review(text)
    language_review = mixed_language(text) or language == "und"
    if not speech_review and not language_review:
        return InputDecision(True, language)
    try:
        result = classify(text, kind, context, language)
    except Exception as exc:
        # Keep ambiguous speech out of the conversation on classifier failures.
        # Do not log raw input, provider responses or credentials.
        log.warning("Input classification unavailable: %s", type(exc).__name__)
        if speech_review:
            return InputDecision(
                False,
                language,
                "input_assessment_unavailable",
                "model",
                confidence=0,
                retryable=True,
            )
        return InputDecision(True, language, "language_rule_fallback")
    language = result["reply_language"] if result["reply_language"] != "und" else language
    if kind != "transcript":
        return InputDecision(True, language, reviewer="model", confidence=result["confidence"])
    valid = result["validity"] == "valid" and result["confidence"] >= 0.8
    reason = "meaningful_input" if valid else "semantic_" + result["validity"]
    if result["validity"] == "valid" and not valid:
        reason = "semantic_uncertain"
    return InputDecision(valid, language, reason, "model", result["confidence"])
