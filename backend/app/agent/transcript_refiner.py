"""Interpret conversational input with the shared primary LLM, without tools."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.call_reasoner import json_object
from app.agent.model_router import ModelRouter

ACRONYMS = set(
    (
        "AI API DAU ID HTTP HTTPS URL CPU GPU SQL JSON XML USA UK KPI RAM DNS IP SDK UUID CSV PDF"
    ).split()
)
SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000}
ID_PATTERN = re.compile(r"\b(?:id|identifier)\s*:?\s+([\w-]+)", re.I)
NOT_IDS = {"is", "are", "of", "for", "and", "the", "information", "details", "number"}
NUMBERS = dict(
    zip(
        (
            "zero one two three four five six seven eight nine ten eleven twelve thirteen "
            "fourteen fifteen sixteen seventeen eighteen nineteen"
        ).split(),
        range(20),
        strict=True,
    )
) | dict(
    zip(
        "twenty thirty forty fifty sixty seventy eighty ninety".split(),
        range(20, 100, 10),
        strict=True,
    )
)

PROMPT = (Path(__file__).parent / "prompts" / "transcript_refinement.md").read_text()


def without_disfluency(source: str, fragments: list) -> str:
    """Only pure, slot-free discourse fragments can relax the negation-count guard."""
    filler = {
        "no",
        "um",
        "uh",
        "erm",
        "hmm",
        "murm",
        "i",
        "mean",
        "say",
        "well",
        "ok",
        "okay",
        "you",
        "know",
        "like",
        "so",
        "er",
        "ah",
    }
    for fragment in fragments:
        if not isinstance(fragment, str) or not fragment.strip():
            continue
        tokens = re.findall(r"[\w']+", fragment.casefold())
        if tokens and all(word in filler for word in tokens):
            pattern = (
                re.escape(fragment)
                if re.search(re.escape(fragment), source, re.I)
                else (r"\b" + r"\W+".join(map(re.escape, tokens)) + r"\b")
            )
            candidate = re.sub(pattern, " ", source, count=1, flags=re.I)
            if "no" in tokens:
                discourse_repair = len(re.findall(r"\bno\b", source, re.I)) >= 2 or re.search(
                    r"\bi\s+(?:mean|say)\b", source, re.I
                )
                independent_request = re.search(
                    r"\b(?:what|why|where|how|can|could|please|i want|i need)\b", candidate, re.I
                )
                if not (discourse_repair and independent_request):
                    continue
            source = candidate
    return source


def protected_text(source: str, removed: list) -> str:
    source = without_disfluency(source, removed)
    return re.sub(
        r"\b(do not|don't|not|never|cannot|no)(?:\s*,?\s*\1)+\b", r"\1", source, flags=re.I
    )


def explicit_repair(text: str) -> bool:
    numeric = "|".join(NUMBERS) + r"|\d+"
    return bool(
        re.search(
            rf"\b(?:{numeric})\b\s*,?\s*(?:no|sorry|i mean|rather)\s*,?\s*"
            rf"\b(?:{numeric})\b",
            text,
            re.I,
        )
    )


def readable(text: str) -> str:
    """Conservative display fallback; never title-case identifiers or change their value."""

    identifiers = {m[1] for m in ID_PATTERN.finditer(text) if m[1].lower() not in NOT_IDS}

    def word(match: re.Match) -> str:
        value = match[0]
        if value in ACRONYMS or value in identifiers or any(c.isdigit() for c in value):
            return value
        return value.lower() if value.isupper() else value

    text = re.sub(r"\b[\w'-]+\b", word, text)
    text = re.sub(r"\bi\b", "I", text)
    return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m[1] + m[2].upper(), text).strip()


def critical(text: str) -> dict:
    lowered = text.lower().replace("’", "'")

    # Accept spoken/written representations of the same small number.
    def number(match: re.Match) -> str:
        words = [w for w in match[0].replace("-", " ").split() if w != "and"]
        if len(words) > 1 and all(w in NUMBERS and NUMBERS[w] < 10 for w in words):
            return "".join(str(NUMBERS[w]) for w in words)
        total = current = 0
        for word in words:
            if word == "hundred":
                current = max(1, current) * 100
            elif word in SCALES:
                total += max(1, current) * SCALES[word]
                current = 0
            else:
                current += NUMBERS[word]
        return str(total + current)

    names = "|".join([*NUMBERS, *SCALES])
    lowered = re.sub(rf"\b(?:{names})(?:(?:[ -]|\s+and\s+)(?:{names}))*\b", number, lowered)
    lowered = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", lowered)
    lowered = re.sub(r"\b(?:minus|negative)\s+(?=\d)", "-", lowered)
    values = Counter(re.findall(r"(?<!\w)[+-]?\d+(?:[.,]\d+)*\b", lowered))
    identifiers = Counter(re.findall(r"\b(?=[\w-]*\d)(?=[\w-]*[a-zA-Z])[\w-]+\b", text))
    identifiers.update(
        m[1]
        for m in ID_PATTERN.finditer(text)
        if m[1].lower() not in NOT_IDS and not any(c.isdigit() for c in m[1])
    )
    negations = len(re.findall(r"\b(?:not|never|no|cannot|\w+n't)\b", lowered)) + len(
        re.findall(r"不要|不能|不准|不得|没有|不|没|别", lowered)
    )
    units = Counter(
        re.findall(
            r"\b(?:dollars?|euros?|pounds?|percent|kilograms?|meters?|minutes?|hours?)\b|[$€£%]",
            lowered,
        )
    )
    unit_names = {"$": "dollar", "€": "euro", "£": "pound", "%": "percent"}
    units = Counter({unit_names.get(k, k.rstrip("s")): v for k, v in units.items()})
    dates = Counter(
        re.findall(
            r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
            lowered,
        )
    )
    return {
        "numbers": values,
        "identifiers": identifiers,
        "negations": negations,
        "units": units,
        "dates": dates,
    }


def disagreement(raw: str, final: str) -> bool:
    if not raw or not final or explicit_repair(raw):
        return False  # Explicit repairs need the full utterance, not bag-of-numbers voting.
    a, b = critical(raw), critical(final)
    return any(a[k] != b[k] for k in a)


def clear_independent_requests(raw: str, final: str) -> tuple[str, list[str]]:
    """Recover only independently phrased, matching, slot-free questions.

    This is diagnostic partitioning, never a routing verdict. The primary LLM
    receives both recognitions and decides whether a substantive gap remains.
    """

    def split(text: str) -> list[str]:
        return [
            p.strip()
            for p in re.split(
                r"[?;.!]+\s*|\s+and\s+(?=(?:what|why|how|when|where|who)\b)",
                text,
                flags=re.I,
            )
            if p.strip()
        ]

    originals, finals = split(raw), split(final)
    clear, uncertain = [], []
    for clause in finals:
        lower = clause.lower()
        match = next(
            (
                part
                for part in originals
                if SequenceMatcher(
                    None,
                    part.lower(),
                    lower,
                ).ratio()
                >= 0.85
            ),
            "",
        )
        slots = critical(clause)
        if (
            match
            and re.match(r"^(?:what|why|how|when|where|who)\b", lower)
            and not re.search(r"\b(?:it|that|those|their|them|these|id|account|user)\b", lower)
            and not any(slots.values())
            and not disagreement(match, clause)
        ):
            clear.append(readable(clause) + "?")
        else:
            uncertain.append(clause)
    return " ".join(clear), uncertain


class TranscriptRefiner:
    def __init__(self, router: ModelRouter) -> None:
        self.settings, self.model = router.clients[0]
        self.timeout = max(12.0, float(os.getenv("CALL_TRANSCRIPT_TIMEOUT_MS", "12000")) / 1000)

    async def refine(
        self, raw: str, final_asr: dict, recent: list[str], glossary: tuple[str, ...] = ()
    ) -> dict:
        start = time.monotonic()
        final = final_asr.get("text", "")
        source = final or raw
        conflict = disagreement(raw, final)
        result = {
            "text": readable(source),
            "status": "degraded",
            "unclear_spans": [],
            "model": self.settings.model,
            "model_label": self.settings.model_label or self.settings.model,
            "route": "direct",
            "reason": "format_fallback",
            "stage": "semantic_cleanup",
        }
        # High ASR confidence proves recognition, not that filler has been removed.
        # Always run semantic cleanup, including when protected evidence conflicts;
        # guards below still prevent a model from choosing a disputed numeric value.
        try:
            async with asyncio.timeout(self.timeout):
                options = {"temperature": 0}
                options[
                    "num_predict"
                    if getattr(self.settings, "provider", "openai") == "ollama"
                    else "max_tokens"
                ] = 1024
                if "deepseek.com" in self.settings.base_url or "ark" in self.settings.base_url:
                    options["extra_body"] = {"thinking": {"type": "disabled"}}
                answer = await self.model.bind(**options).ainvoke(
                    [
                        SystemMessage(content=PROMPT),
                        HumanMessage(
                            content=json.dumps(
                                {
                                    "raw_transcript": raw,
                                    "final_asr": final,
                                    "asr_disagreement": conflict,
                                    "recent_caller_text": recent[-2:],
                                    "recognition_glossary": list(glossary),
                                    "input_kind": "typed"
                                    if final_asr.get("status") == "typed"
                                    else "speech",
                                },
                                ensure_ascii=False,
                            )
                        ),
                    ]
                )
                parsed = json_object(str(answer.content))
                text, status = parsed.get("text"), parsed.get("status")
                unclear = parsed.get("unclear_spans", [])
                if (
                    not isinstance(text, str)
                    or (not text.strip() and status != "no_content")
                    or len(text) > max(500, len(source) * 2)
                ):
                    raise ValueError("invalid_transcript")
                if status not in [
                    "corrected",
                    "needs_clarification",
                    "no_content",
                ] or not isinstance(unclear, list):
                    raise ValueError("invalid_transcript_status")
                removed = parsed.get("removed_spans", [])
                removed = removed if isinstance(removed, list) else []
                unclear = [
                    s for s in unclear if isinstance(s, str) and without_disfluency(s, [s]).strip()
                ]
                before, after = critical(protected_text(source, removed)), critical(text)
                protected_keys = ["numbers", "identifiers", "units", "dates", "negations"]
                conflict = bool(
                    raw
                    and final
                    and not explicit_repair(raw)
                    and any(
                        critical(protected_text(raw, removed))[k]
                        != critical(protected_text(final, removed))[k]
                        for k in protected_keys
                    )
                )
                protected_changed = before != after
                if not text.strip() and critical(source)["negations"]:
                    protected_changed = True
                safe_repair = (
                    explicit_repair(source)
                    and not (after["numbers"] - before["numbers"])
                    and before["identifiers"] == after["identifiers"]
                    and before["units"] == after["units"]
                    and before["dates"] == after["dates"]
                    and after["negations"] in [before["negations"], before["negations"] - 1]
                )
                if protected_changed and not safe_repair:
                    result.update(status="needs_clarification", reason="protected_content_changed")
                else:
                    result.update(
                        text=readable(text.strip()),
                        status="needs_clarification" if conflict or unclear else status,
                        unclear_spans=[s[:120] for s in unclear if isinstance(s, str)][:5],
                        reason="asr_disagreement"
                        if conflict
                        else "unclear_speech"
                        if unclear or status == "needs_clarification"
                        else "",
                        removed_spans=removed,
                        missing_slots=[
                            s
                            for s in parsed.get("missing_slots", [])
                            if isinstance(s, dict) and isinstance(s.get("slot"), str)
                        ][:5],
                    )
                result["protected_conflict"] = conflict or (protected_changed and not safe_repair)
                if conflict:
                    result.update(status="needs_clarification", reason="asr_disagreement")
                    clear, _ = clear_independent_requests(raw, final)
                    # Preserve the model's substantive uncertainty. Clause
                    # partitioning is not qualified to turn a polite request
                    # frame into a new unclear "term".
                    result.update(clear_text=clear)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result["reason"] = result["reason"] if conflict else type(exc).__name__
            if conflict:
                result.update(
                    status="needs_clarification", reason="asr_disagreement", protected_conflict=True
                )
        result["refiner_ms"] = round((time.monotonic() - start) * 1000)
        return result
