"""Continuous PCM recognition and local English speech; models are loaded once."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from app.adapters.store import DATA_DIR

ASR_NAME = "sherpa-onnx-streaming-zipformer-en-2023-06-26"
TTS_NAME = "kokoro-int8-multi-lang-v1_1"
logger = logging.getLogger(__name__)


def speech_chunks(text: str) -> list[str]:
    """Synthesize a short first phrase after the WHOLE answer has passed Wiki.

    Prefer clause boundaries, preserve every original character, and prefetch
    subsequent phrases in the existing player instead of waiting for a long sentence.
    """
    chunks = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        while len(sentence.split()) > 10:
            tokens = list(re.finditer(r"\S+", sentence))
            boundaries = [
                m.end()
                for m in re.finditer(
                    r"[,;:]\s+|\s+(?=(?:that|which|because|while|and|but)\b)", sentence
                )
                if 4 <= len(sentence[: m.end()].split()) <= 8
                and len(sentence[m.end() :].split()) >= 4
            ]
            boundary = boundaries[-1] if boundaries else tokens[min(8, len(tokens) - 4)].start()
            chunks.append(sentence[:boundary].strip())
            sentence = sentence[boundary:].strip()
        if sentence:
            chunks.append(sentence)
    return chunks


class SileroStream:
    """One recurrent state per call; the inference session is shared and read-only."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.reset()

    def reset(self) -> None:
        self.h = np.zeros((2, 1, 64), dtype=np.float32)
        self.c = np.zeros((2, 1, 64), dtype=np.float32)

    def accept(self, samples: np.ndarray) -> float:
        probability, self.h, self.c = self.session.run(
            None, {"x": samples.reshape(1, 512), "h": self.h, "c": self.c}
        )
        return float(probability.reshape(-1)[0])


class RealtimeSpeech:
    capabilities = {
        "provider": "kokoro-local",
        "supports_text_stream": False,
        "supports_audio_stream": False,
        "supports_cancel": False,
        "delivery": "prefetched_sentences",
        "native_concurrency": 1,
    }

    def __init__(self) -> None:
        self.root = Path(os.getenv("DUET_VOICE_MODELS", str(DATA_DIR / "models/voice")))
        self.recognizer: Any = None
        self.tts: Any = None
        self.vad_session: Any = None
        self.final_model: Any = None
        self.final_error = ""
        self.final_name = os.getenv("CALL_FINAL_ASR_MODEL", "small.en")
        self.final_lock = threading.Lock()
        self.load_lock = asyncio.Lock()
        self.tts_lock = asyncio.Lock()
        self.cue_cache: dict[str, tuple[bytes, int]] = {}

    def available(self) -> bool:
        return (
            (self.root / "silero_vad.onnx").exists()
            and (self.root / ASR_NAME / "tokens.txt").exists()
            and (self.root / TTS_NAME / "model.int8.onnx").exists()
        )

    def load(self) -> None:
        import sherpa_onnx as sherpa

        if not self.available():
            raise RuntimeError(
                "通话模型尚未准备好，请运行 backend/.venv/bin/python scripts/prepare-voice.py"
            )
        if self.recognizer is None:
            base = self.root / ASR_NAME
            self.recognizer = sherpa.OnlineRecognizer.from_transducer(
                tokens=str(base / "tokens.txt"),
                encoder=str(base / "encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx"),
                decoder=str(base / "decoder-epoch-99-avg-1-chunk-16-left-128.onnx"),
                joiner=str(base / "joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx"),
                num_threads=2,
                enable_endpoint_detection=True,
                rule1_min_trailing_silence=1.5,
                rule2_min_trailing_silence=float(os.getenv("CALL_END_SILENCE", "0.65")),
                rule3_min_utterance_length=18,
            )
        if self.vad_session is None:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            self.vad_session = ort.InferenceSession(
                str(self.root / "silero_vad.onnx"), options, providers=["CPUExecutionProvider"]
            )
        if self.tts is None:
            base = self.root / TTS_NAME
            kokoro = sherpa.OfflineTtsKokoroModelConfig(
                model=str(base / "model.int8.onnx"),
                voices=str(base / "voices.bin"),
                tokens=str(base / "tokens.txt"),
                data_dir=str(base / "espeak-ng-data"),
                lexicon=str(base / "lexicon-us-en.txt"),
                lang="en-us",
            )
            self.tts = sherpa.OfflineTts(
                sherpa.OfflineTtsConfig(
                    model=sherpa.OfflineTtsModelConfig(kokoro=kokoro, num_threads=2),
                    max_num_sentences=1,
                )
            )

    async def prepare(self) -> None:
        async with self.load_lock:
            await asyncio.to_thread(self.load)

    async def prepare_feedback(self) -> None:
        await self.prepare()
        for text in ["One moment.", "Let me check that."]:
            await self.synthesize(text)

    def prepare_final(self) -> None:
        from faster_whisper import WhisperModel

        if self.final_model is not None:
            return
        try:
            path = self.root / ("faster-whisper-" + self.final_name)
            self.final_model = WhisperModel(
                str(path) if path.exists() else self.final_name,
                device="cpu",
                compute_type="int8",
                cpu_threads=4,
                local_files_only=True,
            )
            self.final_error = ""
        except Exception as exc:
            self.final_error = type(exc).__name__
            logger.warning("final_asr_not_ready error=%s", self.final_error)

    async def transcribe_turn(self, pcm: bytes, glossary: tuple[str, ...] = ()) -> dict:
        # A cancelled speculative pass can still be inside native inference.
        # Give the latest utterance a brief, cancellable chance to use the model
        # instead of immediately losing the whole second recognition pass.
        available_until = time.monotonic() + 0.75
        while self.final_model is not None and self.final_lock.locked():
            if time.monotonic() >= available_until:
                return {
                    "status": "unavailable",
                    "text": "",
                    "model": self.final_name,
                    "reason": "recognizer_busy",
                }
            await asyncio.sleep(0.025)

        def run() -> dict:
            started = time.monotonic()
            if self.final_model is None or not self.final_lock.acquire(blocking=False):
                return {"status": "unavailable", "text": "", "model": self.final_name}
            try:
                samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
                segments, _ = self.final_model.transcribe(
                    samples,
                    language="en",
                    task="transcribe",
                    beam_size=5,
                    condition_on_previous_text=False,
                    word_timestamps=True,
                    vad_filter=False,
                    hotwords=", ".join(glossary) or None,
                )
                pieces = [
                    s for s in segments if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1)
                ]
                probabilities = [w.probability for s in pieces for w in (s.words or [])]
                return {
                    "status": "ready",
                    "text": " ".join(s.text.strip() for s in pieces),
                    "model": self.final_name,
                    "low_quality": bool(pieces) and any(s.avg_logprob < -1 for s in pieces),
                    "quality": {
                        "evidence": "whisper_segment_and_word_scores",
                        "min_word_probability": min(probabilities, default=0),
                        "min_avg_logprob": min((s.avg_logprob for s in pieces), default=-10),
                        "max_no_speech_prob": max((s.no_speech_prob for s in pieces), default=1),
                        "max_compression_ratio": max(
                            (s.compression_ratio for s in pieces), default=10
                        ),
                    },
                    "final_asr_ms": round((time.monotonic() - started) * 1000),
                }
            except Exception as exc:
                logger.warning("final_asr_failed error=%s", type(exc).__name__)
                return {"status": "failed", "text": "", "model": self.final_name}
            finally:
                self.final_lock.release()

        return await asyncio.to_thread(run)

    def accept(self, stream: Any, pcm: bytes) -> tuple[str, bool]:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        stream.accept_waveform(16000, samples)
        while self.recognizer.is_ready(stream):
            self.recognizer.decode_stream(stream)
        text = self.recognizer.get_result(stream).strip()
        final = self.recognizer.is_endpoint(stream)
        if final:
            self.recognizer.reset(stream)
        return text, final

    async def synthesize(self, text: str) -> tuple[bytes, int]:
        # Response-level streaming: complete speech units are synthesized ahead of playback.
        if text in self.cue_cache:
            return self.cue_cache[text]
        async with self.tts_lock:
            task = asyncio.create_task(asyncio.to_thread(self.tts.generate, text, 3, 1.08))
            try:
                audio = await asyncio.shield(task)
            except asyncio.CancelledError:
                # Keep the model locked until its non-cancellable native inference has exited.
                await task
                raise
        if not len(audio.samples) or not np.isfinite(audio.samples).all():
            raise RuntimeError("tts_invalid_audio")
        pcm = (np.clip(audio.samples, -1, 1) * 32767).astype("<i2").tobytes()
        if text in {
            "One moment.",
            "Let me check that.",
            "I'm still checking the available sources.",
        }:
            self.cue_cache[text] = (pcm, audio.sample_rate)
        return pcm, audio.sample_rate
