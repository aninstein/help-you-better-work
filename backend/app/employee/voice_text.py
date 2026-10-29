"""Readable ASR segments; input decisions live in input_preprocessing."""

import re

from .input_preprocessing.rules import transcript_ignore_reason as transcript_ignore_reason


def _cjk(character):
    return bool(character) and (
        "\u3040" <= character <= "\u30ff"
        or "\u3400" <= character <= "\u9fff"
        or "\uac00" <= character <= "\ud7af"
    )


def format_transcript(segments):
    text = ""
    previous_end = None
    for segment in segments:
        part = segment.text.strip()
        if not part:
            continue
        gap = max(0, segment.start - previous_end) if previous_end is not None else 0
        if text:
            # Use the recognizer's punctuation and measured pauses, never guessed sentence intent.
            if text[-1] in "。！？!?" or gap >= 1.2:
                separator = "\n"
            elif _cjk(part[0]) and (_cjk(text[-1]) or text[-1] in "，、：；）』」”"):
                separator = ""
            else:
                separator = " "
            text += separator
        text += part
        previous_end = segment.end
    text = re.sub(r'([。！？]+[”’」』"]*)[ \t]*(?=[^\s。！？])', r"\1\n", text)
    return text.strip()
