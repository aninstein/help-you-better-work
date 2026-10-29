"""Pre-generated acknowledgement audio; no LLM or per-turn TTS on the critical path."""

import asyncio
import hashlib
import os
import re
import time
from pathlib import Path

from . import volcengine_voice as voice

PHRASES = {
    "zh": "好，我看一下。",
    "en": "Let me check.",
    "ja": "少々お待ちください。",
    "ko": "잠시만요.",
    "ru": "Сейчас посмотрю.",
    "ar": "لحظة من فضلك.",
}
_LOCKS = {}


def cue_for(text, reply_language=None):
    compact = re.sub(r"[\W_]+", "", text).casefold()
    if compact in {
        "你好",
        "您好",
        "谢谢",
        "再见",
        "好",
        "好的",
        "对",
        "是",
        "不",
        "不要",
        "停",
        "停一下",
        "停止",
        "暂停",
        "hello",
        "hi",
        "thanks",
        "thankyou",
        "yes",
        "no",
        "ok",
        "stop",
    }:
        return None
    from .input_preprocessing.language import detect_language

    language = reply_language or detect_language(text)
    if language not in PHRASES:
        return None  # Never acknowledge in an unrelated language.
    locale = language
    try:
        url = cue_url(locale)
    except voice.SpeechError:
        url = ""
    return {"locale": locale, "text": PHRASES[locale], "url": url}


def cue_path(locale):
    if locale not in PHRASES:
        raise ValueError("Unsupported cue")
    settings = [
        voice.provider("tts"),
        os.getenv("VOLCENGINE_TTS_RESOURCE_ID", "seed-tts-2.0"),
        os.getenv("VOLCENGINE_TTS_SPEAKER", "zh_female_vv_uranus_bigtts"),
        PHRASES[locale],
    ]
    version = hashlib.sha256(repr(settings).encode()).hexdigest()[:16]
    root = Path(os.getenv("DUET_DATA_DIR", ".runtime/employee-data")) / "voice-cues"
    return root / f"{locale}-{version}.mp3"


def cue_url(locale):
    return f"/api/v1/voice/cues/{locale}?v={cue_path(locale).stem}"


async def prepare(locale):
    path = cue_path(locale)
    if path.is_file():
        return path
    lock = _LOCKS.setdefault(locale, asyncio.Lock())
    async with lock:
        if not path.is_file():
            if voice.provider("tts") == "volcengine":
                content = await voice.synthesize(PHRASES[locale], "duet-acknowledgement")
            else:
                import edge_tts

                speaker = "zh-CN-XiaoxiaoNeural" if locale == "zh" else "en-US-AriaNeural"
                async with asyncio.timeout(12):
                    content = b"".join(
                        [
                            part["data"]
                            async for part in edge_tts.Communicate(
                                PHRASES[locale], speaker
                            ).stream()
                            if part["type"] == "audio"
                        ]
                    )
            if not content:
                raise ValueError("No cue audio")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".part")
            temporary.write_bytes(content)
            temporary.chmod(0o600)
            temporary.replace(path)
    return path


async def warm():
    # Two commonly used languages are ready before the first question. Other
    # scripts get their own cached phrase on first use.
    for locale in ("zh", "en"):
        try:
            await prepare(locale)
        except Exception:
            import logging

            logging.getLogger(__name__).warning("Acknowledgement audio unavailable: %s", locale)


class CueEchoGuard:
    def __init__(self):
        self.accepted = []
        self.text = ""
        self.until = 0

    def accept(self, run_id):
        self.accepted = [*self.accepted[-19:], run_id]

    def started(self, locale, run_id):
        if (
            isinstance(locale, str)
            and isinstance(run_id, str)
            and locale in PHRASES
            and run_id in self.accepted
        ):
            self.text = re.sub(r"[\W_]+", "", PHRASES[locale]).casefold()
            self.until = time.monotonic() + 3

    def is_echo(self, text):
        compact = re.sub(r"[\W_]+", "", text).casefold()
        return time.monotonic() < self.until and (
            compact == self.text or (len(compact) >= 3 and self.text.startswith(compact))
        )
