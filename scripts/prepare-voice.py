#!/usr/bin/env python3
"""Prepare streaming ASR, Silero, complete-turn English ASR and local TTS."""

import concurrent.futures
import hashlib
import json
import os
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = Path(
    os.getenv("DUET_VOICE_MODELS")
    or str(
        Path(os.getenv("DUET_DATA_DIR", str(ROOT / "backend/data"))) / "models/voice"
    )
)
ASR = "sherpa-onnx-streaming-zipformer-en-2023-06-26"
TTS = "kokoro-int8-multi-lang-v1_1"


def prepare(name: str, release: str) -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    target = MODELS / name
    if (target / ".ready.json").exists():
        print(f"Ready: {name}", flush=True)
        return
    url = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/{release}/{name}.tar.bz2"
    archive = MODELS / (name + ".tar.bz2.part")
    print(f"Downloading {name} …", flush=True)
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as out:
        while block := response.read(1024 * 1024):
            digest.update(block)
            out.write(block)
    with tarfile.open(archive) as bundle:
        bundle.extractall(MODELS, filter="data")
    (target / ".ready.json").write_text(
        json.dumps(
            {
                "source": url,
                "sha256": digest.hexdigest(),
                "runtime": "sherpa-onnx==1.13.8",
                "license_note": "Model license and bundled notices are retained in this directory.",
            },
            indent=2,
        )
    )
    archive.unlink()
    print(f"Ready: {name}", flush=True)


def prepare_vad() -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    target = MODELS / "silero_vad.onnx"
    if target.exists():
        print("Ready: Silero VAD", flush=True)
        return
    url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
    temporary = target.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=60) as response:
        temporary.write_bytes(response.read())
    temporary.replace(target)
    (MODELS / "silero_vad.source.json").write_text(
        json.dumps(
            {
                "source": url,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "license": "MIT, https://github.com/snakers4/silero-vad/blob/master/LICENSE",
            },
            indent=2,
        )
    )
    print("Ready: Silero VAD", flush=True)


def prepare_final_asr() -> None:
    from faster_whisper.utils import download_model

    names = [os.getenv("CALL_FINAL_ASR_MODEL", "small.en")]
    if os.getenv("STT_PROVIDER", "local") == "local":
        names.append(os.getenv("WHISPER_MODEL", "tiny"))
    for name in dict.fromkeys(names):
        target = MODELS / ("faster-whisper-" + name)
        if not (target / "model.bin").exists():
            print(f"Downloading speech recognition model {name} …", flush=True)
            download_model(name, output_dir=str(target))
        print(f"Ready: speech recognition model {name}", flush=True)


def prepare_endpoint() -> None:
    # Immutable upstream artifact; reject a changed/corrupt download before loading.
    revision = "f766f81d3cfdf7737ac64aad813d91bbfd56bf93"
    digest = "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"
    target = MODELS / "smart-turn-v3.2-cpu.onnx"
    url = f"https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/{revision}/{target.name}"
    MODELS.mkdir(parents=True, exist_ok=True)
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        temporary = target.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=60) as response:
            temporary.write_bytes(response.read())
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
            temporary.unlink()
            raise RuntimeError("Smart Turn model SHA256 mismatch")
        temporary.replace(target)
    (MODELS / "smart-turn.source.json").write_text(
        json.dumps(
            {
                "source": url,
                "revision": revision,
                "sha256": digest,
                "license": "BSD-2-Clause; https://github.com/pipecat-ai/smart-turn/blob/main/LICENSE",
                "preprocessing": "transformers==4.57.6 WhisperFeatureExtractor(chunk_length=8), left pad then normalize",
            },
            indent=2,
        )
    )
    print("Ready: Smart Turn v3.2 CPU", flush=True)


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for future in [
            pool.submit(prepare, ASR, "asr-models"),
            pool.submit(prepare, TTS, "tts-models"),
            pool.submit(prepare_vad),
            pool.submit(prepare_final_asr),
            pool.submit(prepare_endpoint),
        ]:
            future.result()
