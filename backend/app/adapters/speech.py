"""Audio adapters: OpenAI-compatible STT and offline macOS speech synthesis."""

import asyncio
import hashlib
import logging
import os
import shutil
from functools import lru_cache
from pathlib import Path

from openai import AsyncOpenAI

from app.adapters.speech_text import spoken_text
from app.adapters.store import DATA_DIR
from app.domain.models import ModelSettings


class SpeechAdapter:
    @staticmethod
    @lru_cache(maxsize=2)
    def whisper_model(name: str):
        from faster_whisper import WhisperModel

        root = Path(os.getenv("DUET_VOICE_MODELS", str(DATA_DIR / "models/voice")))
        prepared = root / ("faster-whisper-" + name)
        model = str(prepared) if (prepared / "model.bin").exists() else name
        return WhisperModel(model, device="cpu", compute_type="int8")

    async def transcribe(self, path: Path, settings: ModelSettings) -> str:
        if settings.stt_provider == "local":
            return await asyncio.to_thread(self.local_transcription, path)
        client = AsyncOpenAI(
            api_key=settings.api_key or "local", base_url=settings.base_url, timeout=60
        )
        with path.open("rb") as audio:
            result = await client.audio.transcriptions.create(model=settings.stt_model, file=audio)
        return result.text

    @staticmethod
    def local_transcription(path: Path) -> str:
        model = SpeechAdapter.whisper_model(os.getenv("WHISPER_MODEL", "tiny"))
        segments, _ = model.transcribe(str(path), beam_size=1, vad_filter=True)
        text = " ".join(segment.text for segment in segments).strip()
        if not text:
            raise RuntimeError("No speech detected")
        return text

    async def synthesize(self, text: str, destination: Path, language: str = "en-US") -> dict:
        import edge_tts

        content = spoken_text(text)
        if not content:
            raise ValueError("No answer text to synthesize")
        metadata = {
            "language": "en-US",
            "spoken_text": content,
            "text_sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
        neural = destination.with_suffix(".mp3")
        voice = os.getenv("TTS_ENGLISH_VOICE", "en-US-AriaNeural")
        if not voice.startswith("en-"):
            voice = "en-US-AriaNeural"
        try:
            await asyncio.wait_for(
                edge_tts.Communicate(content, voice, rate=os.getenv("TTS_RATE", "-5%")).save(
                    str(neural)
                ),
                timeout=25,
            )
            if neural.stat().st_size < 100:
                raise ValueError("Empty neural audio")
            return {
                **metadata,
                "filename": neural.name,
                "content_type": "audio/mpeg",
                "voice": voice,
                "status": "ready",
            }
        except Exception as exc:
            neural.unlink(missing_ok=True)
            logging.getLogger(__name__).warning("neural_tts_fallback error=%s", type(exc).__name__)
        if shutil.which("say") and shutil.which("afconvert"):
            temporary = destination.with_suffix(".aiff")
            try:
                await self.command(
                    "say", "-v", "Samantha", "-r", "160", "-o", str(temporary), content
                )
                await self.command(
                    "afconvert", "-f", "WAVE", "-d", "LEI16", str(temporary), str(destination)
                )
                return {
                    **metadata,
                    "filename": destination.name,
                    "content_type": "audio/wav",
                    "voice": "Samantha",
                    "status": "ready",
                }
            except Exception:
                destination.unlink(missing_ok=True)
            finally:
                temporary.unlink(missing_ok=True)
        if shutil.which("espeak-ng"):
            try:
                await self.command(
                    "espeak-ng", "-v", "en-us", "-s", "155", "-w", str(destination), content
                )
                return {
                    **metadata,
                    "filename": destination.name,
                    "content_type": "audio/wav",
                    "voice": "en-us",
                    "status": "ready",
                }
            except Exception:
                destination.unlink(missing_ok=True)
        return {**metadata, "filename": None, "voice": "browser-en-US", "status": "browser"}

    @staticmethod
    async def command(*args: str) -> None:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            await asyncio.wait_for(process.communicate(), timeout=90)
            if process.returncode != 0:
                raise RuntimeError("Speech synthesis command failed")
        except TimeoutError:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            raise
