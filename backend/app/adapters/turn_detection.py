"""Pinned Smart Turn CPU inference. Never sees hints, Wiki or another call's audio."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import numpy as np

MODEL = "smart-turn-v3.2-cpu.onnx"
REVISION = "f766f81d3cfdf7737ac64aad813d91bbfd56bf93"
SHA256 = "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"


class EndOfTurnAdapter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.session = None
        self.extractor = None
        self.error = "not_loaded"
        self.lock = threading.Lock()
        self.load_lock = threading.Lock()

    def prepare(self) -> None:
        with self.load_lock:
            if self.session is not None:
                return
            try:
                import onnxruntime as ort
                from transformers import WhisperFeatureExtractor

                path = self.root / MODEL
                if hashlib.sha256(path.read_bytes()).hexdigest() != SHA256:
                    raise ValueError("model_hash_mismatch")
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self.extractor = WhisperFeatureExtractor(chunk_length=8)
                self.session = ort.InferenceSession(
                    str(path), options, providers=["CPUExecutionProvider"]
                )
                self.predict(bytes(16000 * 2))  # Warm FFT, allocator and native inference.
                self.error = ""
            except Exception as exc:
                self.session = None
                self.error = type(exc).__name__

    def predict(self, pcm: bytes) -> dict:
        started = time.monotonic()
        if self.session is None or not self.lock.acquire(blocking=False):
            return {"status": "unavailable"}
        try:
            audio = np.frombuffer(pcm[-128000 * 2 :], dtype="<i2").astype(np.float32) / 32768
            audio = np.pad(audio, (max(0, 128000 - len(audio)), 0))
            # Match upstream: left pad to eight seconds BEFORE Whisper normalization.
            inputs = self.extractor(
                audio,
                sampling_rate=16000,
                return_tensors="np",
                padding="max_length",
                max_length=128000,
                truncation=True,
                do_normalize=True,
            )
            probability = self.session.run(
                None, {"input_features": inputs.input_features.astype(np.float32)}
            )[0].reshape(-1)[0]
            elapsed = (time.monotonic() - started) * 1000
            return {
                "status": "ready" if elapsed <= 200 else "timeout",
                "p_complete": float(probability),
                "duration_ms": round(elapsed),
                "model_revision": REVISION,
            }
        except Exception:
            return {"status": "failed"}
        finally:
            self.lock.release()
