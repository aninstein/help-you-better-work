"""Audio-time turn assembly. ASR segments are not conversational messages."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class TurnAssembler:
    adaptive: bool = False
    silence_ms: int = field(
        default_factory=lambda: int(os.getenv("CALL_TURN_END_SILENCE_MS", "3000"))
    )
    max_seconds: int = field(
        default_factory=lambda: int(os.getenv("CALL_MAX_TURN_AUDIO_SECONDS", "60"))
    )
    sample: int = 0
    id: str = ""
    revision: int = 0
    state: str = "idle"
    first_sample: int = 0
    last_voiced: int = 0
    voiced_run: int = 0
    max_voiced_run: int = 0
    candidate_run: int = 0
    low_run: int = 0
    speech_start_sample: int = 0
    speaking: bool = False
    audio: bytearray = field(default_factory=bytearray)
    pre_roll: bytearray = field(default_factory=bytearray)
    segments: list[str] = field(default_factory=list)
    partial: str = ""
    segment_revision: int = 0
    interrupted: bool = False
    last_audio_at: float = 0

    @property
    def key(self) -> tuple[str, int]:
        return self.id, self.revision

    @property
    def text(self) -> str:
        return " ".join([*self.segments, self.partial]).strip()

    @property
    def quiet_ms(self) -> float:
        return (self.sample - self.last_voiced) / 16 if self.id else 0

    def reset(self) -> None:
        # Preserve the call's monotonic sample clock; no old turn key can be reused.
        self.id = ""
        self.state = "idle"
        self.audio.clear()
        self.pre_roll.clear()
        self.segments.clear()
        self.partial = ""
        self.speaking = False
        self.candidate_run = self.voiced_run = 0
        self.max_voiced_run = self.low_run = 0
        self.interrupted = False
        self.segment_revision = 0

    def accept(self, pcm: bytes, probability: float) -> dict:
        count = len(pcm) // 2
        self.sample += count
        threshold = float(os.getenv("CALL_VAD_THRESHOLD", "0.6"))
        negative = float(os.getenv("CALL_VAD_NEG_THRESHOLD", "0.35"))
        voiced = probability >= (negative if self.speaking else threshold)
        self.candidate_run = self.candidate_run + count if voiced else 0
        self.low_run = 0 if voiced else self.low_run + count
        was_speaking = self.speaking
        self.speaking = (
            voiced
            and (
                was_speaking
                or self.candidate_run >= int(os.getenv("CALL_VAD_MIN_SPEECH_MS", "96")) * 16
            )
        ) or (was_speaking and self.low_run < 1600)
        self.voiced_run = (
            self.voiced_run + count
            if was_speaking and self.speaking
            else self.candidate_run
            if self.speaking
            else 0
        )
        self.max_voiced_run = max(self.max_voiced_run, self.voiced_run)
        result = {"started": False, "resumed": False, "ready": False, "limit": False}
        if self.state == "input_limit":
            if not voiced and self.quiet_ms >= self.silence_ms:
                self.reset()
                result["cancelled"] = True
            elif voiced:
                self.last_voiced = self.sample
            return result
        if not self.id:
            self.pre_roll.extend(pcm)
            del self.pre_roll[: -int(0.32 * 16000) * 2]
            if not self.speaking:
                return result
            self.id, self.revision = uuid4().hex, self.revision + 1
            self.state = "collecting"
            self.audio.extend(self.pre_roll)
            self.first_sample = self.sample - len(self.audio) // 2
            self.speech_start_sample = self.sample - self.candidate_run
            self.interrupted = False
            result["started"] = True
        elif self.speaking and self.state != "collecting":
            result["resumed_pause_ms"] = round(self.quiet_ms)
            self.revision += 1
            self.state = "collecting"
            result["resumed"] = True
        if self.id and voiced:
            self.last_voiced = self.sample
        if not result["started"]:
            self.audio.extend(pcm)
        if self.last_voiced - self.first_sample > self.max_seconds * 16000:
            self.state = "input_limit"
            del self.audio[self.max_seconds * 32000 :]
            result["limit"] = True
            return result
        if not self.speaking and self.state == "collecting":
            self.state = "waiting_pause"
        if not self.adaptive and self.state == "waiting_pause" and self.quiet_ms >= self.silence_ms:
            self.state = "finalizing"
            result["ready"] = True
        return result

    def transcript(self, text: str, final: bool) -> bool:
        changed = text != self.partial or final
        self.partial = text
        if changed:
            self.segment_revision += 1
        if final:
            if text:
                self.segments.append(text)
            self.partial = ""
        return changed

    def speech_audio(self) -> bytes:
        # Keep a phonetic tail, not three seconds of silence in second-pass inference.
        end = min(len(self.audio), max(0, self.last_voiced - self.first_sample + 3200) * 2)
        return bytes(self.audio[:end])
