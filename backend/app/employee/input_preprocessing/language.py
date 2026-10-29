"""Dominant request language, independent of Hint and previous assistant language."""

import re

LANGUAGES = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "ar": "Arabic",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
    "hi": "Hindi",
    "und": "the user's current request language",
}


def detect_language(text, previous="und"):
    if re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", text):
        return "ko"
    for pattern, language in (
        (r"[\u0400-\u04ff]", "ru"),
        (r"[\u0600-\u06ff]", "ar"),
        (r"[\u0900-\u097f]", "hi"),
    ):
        if re.search(pattern, text):
            return language
    chinese = len(re.findall(r"[\u3400-\u9fff]", text))
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)
    if chinese:
        # A couple of Chinese nouns inside an English sentence do not switch its language.
        return "en" if chinese <= 2 and len(words) >= 4 else "zh"
    if words:
        return "en"
    return previous if previous in LANGUAGES else "und"


def mixed_language(text):
    return bool(re.search(r"[\u3400-\u9fff]", text) and re.search(r"[A-Za-z]", text))


def language_instruction(language):
    name = LANGUAGES.get(language, LANGUAGES["und"])
    return (
        f"\nApplication input preprocessing selected the reply language: {name}. "
        "Write the final answer and all user-facing progress text in this language. "
        "Chinese requests with English names/technical terms use Chinese; English sentences "
        "with one or two Chinese words use English. Previous replies, retrieved sources and "
        "Hinter guidance must not change this selection. Keep names, code and quotations intact. "
        "An explicit request to translate or use another output language takes precedence.\n"
    )
