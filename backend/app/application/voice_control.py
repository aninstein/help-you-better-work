"""Independent endpoint, interruption and sequenced-media decisions."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from uuid import uuid4

from app.agent.transcript_refiner import disagreement

BACKCHANNELS = {
    "uh",
    "um",
    "hmm",
    "uh huh",
    "mm hmm",
    "yeah",
    "yes",
    "okay",
    "ok",
    "right",
    "sure",
    "i see",
    "got it",
    "thanks",
}


def words(text: str) -> str:
    return re.sub(r"[^a-z0-9' -]", "", text.lower()).strip()


def incomplete(text: str) -> bool:
    if text.rstrip().endswith(("-", "…", "...")):
        return True
    # A last-word list cannot determine English sentence completeness:
    # "I'd like to know what the figures are" is complete. Semantic gaps
    # belong to the shared primary LLM after the acoustic pause has settled.
    return not words(text)


@dataclass
class TurnController:
    key: tuple = ()
    attempts: int = 0
    prediction: dict = field(default_factory=dict)
    next_prediction_ms: int = 450
    decision_id: str = ""
    pause_history: list[float] = field(default_factory=list)

    def observe(self, key: tuple) -> None:
        if key != self.key:
            self.key = key
            self.attempts = 0
            self.prediction = {}
            self.next_prediction_ms = 450
            self.decision_id = uuid4().hex

    def decide(self, text: str, quiet_ms: float, final: dict | None = None) -> tuple[str, str]:
        text_words = words(text)
        # Deliberate thinking is different from an unfinished ID/amount.
        if re.search(
            r"\b(?:let me (?:think|finish)|give me a (?:second|moment)|"
            r"wait(?: a (?:second|moment))?|hold on)$",
            text_words,
        ):
            return "wait", "explicit_wait"
        final = final or {}
        evidence = final.get("text") or text
        if incomplete(text) or incomplete(evidence) or disagreement(text, final.get("text", "")):
            return (
                ("clarify", "unfinished_or_conflicting_slot")
                if quiet_ms >= 6000
                else ("wait", "unfinished")
            )
        prediction = self.prediction
        if prediction.get("status") in {"unavailable", "failed", "timeout"}:
            return (
                ("commit", "endpoint_fallback")
                if quiet_ms >= 3000
                else ("wait", "endpoint_fallback")
            )
        # Per-call hesitation statistics only adjust the floor, never force an early handoff.
        floor = min(
            900, max(450, sum(self.pause_history[-8:]) / max(1, len(self.pause_history[-8:])) * 0.5)
        )
        if quiet_ms >= floor and prediction.get("p_complete", 0) >= 0.5:
            return "commit", "semantic_complete"
        if quiet_ms >= 3000:
            quality = final.get("quality", {})
            if (
                final.get("status") == "ready"
                and quality.get("min_word_probability", 0) >= 0.55
                and quality.get("min_avg_logprob", -10) >= -0.45
            ):
                return "commit", "final_asr_complete"
            # Endpoint uncertainty describes whether speech has finished, not whether
            # its content is understandable. Hand complete text to the dialogue
            # model after a bounded pause; only genuinely unfinished slots above
            # need clarification.
            return (
                ("commit", "bounded_pause") if quiet_ms >= 6000 else ("wait", "semantic_uncertain")
            )
        return "candidate", "assessing_pause"


def interruption_decision(
    text: str,
    voiced: bool,
    speech_ms: float,
    expects_confirmation: bool = False,
    *,
    thinking: bool = False,
) -> str:
    text = words(text)
    if thinking and re.search(r"[a-z0-9]", text) and text not in {"uh", "um", "hmm", "mm hmm"}:
        # There is no spoken explanation to acknowledge. A short correction or
        # additional word is enough to return the floor to the caller.
        return "interrupt"
    if re.match(r"^(?:stop|wait|hold on|no stop|please stop|don't answer)\b", text):
        return "interrupt"
    if not voiced:
        if expects_confirmation and text in {"yes", "yeah", "no", "sure", "okay", "ok"}:
            return "interrupt"
        return "backchannel" if text in BACKCHANNELS else "uncertain"
    if text in BACKCHANNELS:
        return (
            "interrupt"
            if expects_confirmation and text in {"yes", "yeah", "no", "sure", "okay", "ok"}
            else "backchannel"
        )
    if text == "no":
        return "interrupt"  # Short rejection is meaningful; no duration minimum.
    if (
        text
        and speech_ms >= 240
        and (
            len(text.split()) >= 3
            or re.match(r"^(?:what|why|how|when|where|who|actually|instead|cancel|sorry)\b", text)
        )
    ):
        return "interrupt"
    return "candidate" if speech_ms >= 96 else "uncertain"


@dataclass
class RecognitionContext:
    version: int = 1
    terms: tuple[str, ...] = ("API", "DAU", "HTTP", "SQL", "Kokoro")

    def update(self, values: object) -> None:
        if not isinstance(values, list) or len(values) > 50:
            raise ValueError("最多50个明确授权的识别术语。")
        terms = tuple(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))
        if sum(map(len, terms)) > 1000 or any(
            len(t) > 64
            or len(t.split()) > 5
            or not re.fullmatch(r"[\w .+'/-]+", t)
            or t.isdecimal()
            or re.search(r"\d{5,}", t)
            for t in terms
        ):
            raise ValueError("请只填写短术语或产品名，不填写客户数据、数值或完整指令。")
        self.version += 1
        self.terms = terms


@dataclass
class MediaSequence:
    sequence: int | None = None
    sample: int | None = None

    def reset(self) -> None:
        self.sequence = self.sample = None

    def unpack(self, packet: bytes) -> tuple[bytes, bool]:
        if len(packet) != 1044 or packet[:4] != b"DVP3":
            raise ValueError("invalid_media_frame")
        sequence, sample, count, flags = struct.unpack_from("<IQHH", packet, 4)
        if count != 512 or flags != 0:
            raise ValueError("invalid_media_header")
        gap = self.sequence is not None and (sequence != self.sequence or sample != self.sample)
        self.sequence, self.sample = (sequence + 1) % (2**32), sample + count
        return packet[20:], gap
