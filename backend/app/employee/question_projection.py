"""Conservative question deduplication within a single source revision.

The projection model merges semantic repetitions. This final guard handles exact
repeats, punctuation/case/spacing changes and polite lead-ins, including older
stored projections. It never rewrites the original transcript or joins turns.
"""

import re
import unicodedata

_LEAD_IN = re.compile(
    r"^(?:(?:嗯|呃|啊)[\s,，。.!！]*|请问[\s,，]*|"
    r"(?:我想知道|我想了解|帮我查一下|帮我看一下|帮我看看|请告诉我)[\s,，]*|"
    r"(?:please|i\s+(?:want|would\s+like)\s+to\s+know|can\s+you\s+tell\s+me|"
    r"could\s+you\s+tell\s+me|tell\s+me)\b[\s,]*)",
    re.IGNORECASE,
)


def question_key(text):
    value = unicodedata.normalize("NFKC", text).casefold().strip()
    while match := _LEAD_IN.match(value):
        value = value[match.end():].lstrip()
    # Keep numbers, signs, currencies, comparisons and date/time separators distinct.
    value = re.sub(r"(?<!\d)[.:]|[.:](?!\d)", "", value)
    return "".join(char for char in value if char.isalnum() or char in ".:+-/%$€¥<>=≤≥")


def deduplicate_questions(items):
    seen = set()
    result = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        normalized = question_key(item["text"])
        if not normalized:
            continue
        key = (item.get("source_record_id"), item.get("source_revision"), normalized)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
