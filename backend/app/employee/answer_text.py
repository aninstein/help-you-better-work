"""Public answer presentation; identifiers remain available in structured audit data."""

import re

_PREFIX = (
    r"(?:memory_hint_legacy_record|legacy_record|legacy_memory|memory_hint|record|memory|"
    r"assistance|run|thread|workspace|artifact|event|user)"
)
_ID = _PREFIX + r"_[0-9a-f]{32}"
_INTERNAL = re.compile(_ID, re.I)
_PARENTHETICAL = re.compile(
    r"[（(]\s*(?:编号|记录(?:编号)?|ID)?[：:\s]*" + _ID + r"(?:[\s,，、]+" + _ID + r")*\s*[）)]",
    re.I,
)
_PARTIAL = re.compile(
    r"(?:[（(](?:编号|记录|ID|id)?[：:\s]*)?" + _PREFIX + r"_[0-9a-f]{0,31}$", re.I
)


def public_answer(text, *, partial=False):
    text = re.sub(r"\[TOKEN BUDGET EXCEEDED\][^\n]*(?:\n|$)", "", text)
    # Hide an unfinished token across stream boundaries as well as complete IDs.
    if partial:
        text = _PARTIAL.sub("", text)
    text = _PARENTHETICAL.sub("", text)
    text = _INTERNAL.sub("", text)
    text = re.sub(r"[（(][\s,，、;；]*[）)]", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
