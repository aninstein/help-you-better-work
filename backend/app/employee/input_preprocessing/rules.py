"""Cheap speech checks shared by partial ASR and the final input pipeline."""

import re
import unicodedata

_FILLERS = re.compile(r"(?:[嗯呃额啊哦噢唔]|u+h+|u+m+|h+m+|m+h+m+)+\Z")
_REPETITION = re.compile(r"(.)\1{3,}")
_SHORT_MEANINGS = {
    "不",
    "否",
    "别",
    "停",
    "好",
    "对",
    "是",
    "行",
    "要",
    "不要",
    "不是",
    "不对",
    "等等",
    "停下",
    "停止",
    "暂停",
    "停一下",
    "等一下",
    "继续",
    "取消",
    "确认",
    "yes",
    "no",
    "ok",
    "okay",
    "stop",
    "wait",
    "go",
}


def transcript_ignore_reason(text):
    """Conservatively discard standalone ASR noise, never rewrite meaningful speech.

    Length below five triggers a filler check, not automatic rejection. Unknown
    short phrases are retained. Numbers, negations and stop commands take priority.
    Applies only to ASR/transcript input, never ordinary typed messages.
    """
    normalized = unicodedata.normalize("NFKC", text).casefold()
    compact = "".join(ch for ch in normalized if ch.isalnum())
    if not compact:
        return "empty_transcript"
    collapsed = re.sub(r"(.)\1+", r"\1", compact)
    if compact in _SHORT_MEANINGS or collapsed in _SHORT_MEANINGS:
        return None
    if compact.isdigit() or set(compact) <= set("零〇一二两三四五六七八九十百千万亿点"):
        return None
    if _FILLERS.fullmatch(compact):
        return "short_filler" if len(compact) < 5 else "filler_only"
    if compact in {"imean", "imeans", "youknow", "yousee", "我是说", "就是说", "怎么说呢"}:
        return "incomplete_lead_in"
    repeated = list(_REPETITION.finditer(compact))
    if repeated:
        remainder = _REPETITION.sub("", compact)
        repeated_length = sum(len(match[0]) for match in repeated)
        # Keep a real request after repetition (e.g. “啊啊啊啊救命”).
        if not remainder or (
            repeated_length / len(compact) >= 0.8
            and remainder not in _SHORT_MEANINGS
            and not remainder.isdigit()
            and _FILLERS.fullmatch(remainder)
        ):
            return "repeated_noise"
    return None


def needs_semantic_review(text):
    compact = "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum())
    if compact in _SHORT_MEANINGS or compact.isdigit():
        return False
    if compact and set(compact) <= set("零〇一二两三四五六七八九十百千万亿点"):
        return False
    cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", text))
    words = re.findall(r"[^\W_]+", text, re.UNICODE)
    return cjk <= 24 if cjk else len(words) <= 12 or len(compact) <= 80


def allows_partial_interruption(text):
    # Ambiguous short fragments wait for stable ASR and semantic validation.
    compact = re.sub(r"[\W_]+", "", text).casefold()
    stop = compact in {
        "停",
        "停下",
        "停一下",
        "停止",
        "等等",
        "等一下",
        "别说了",
        "暂停",
        "stop",
        "wait",
        "holdon",
    }
    return not transcript_ignore_reason(text) and (stop or not needs_semantic_review(text))
