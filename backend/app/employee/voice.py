"""Speech is an IO adapter; semantic processing always enters a DeerFlow Run."""

import asyncio
import hashlib
import io
import os
import secrets
import shutil
import struct
import threading
import time
import wave
from functools import lru_cache
from pathlib import Path

import anyio
from fastapi import APIRouter, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, insert, select, update

from . import auth, voice_cues
from . import repository as repo
from . import volcengine_voice as cloud_voice
from .answer_text import public_answer
from .db import artifacts, now, records, threads, transaction, uid
from .voice_text import transcript_ignore_reason

router = APIRouter(prefix="/api/v1")
STT_LOCK = threading.Lock()
# Short-lived, single-use playback grants are bound to the authenticated session.
SPEECH_TICKETS = {}
CUE_WARM_TASK = None


@router.on_event("startup")
async def warm_voice_cues():
    global CUE_WARM_TASK
    CUE_WARM_TASK = asyncio.create_task(voice_cues.warm())


@router.on_event("shutdown")
async def stop_voice_cues():
    if CUE_WARM_TASK and not CUE_WARM_TASK.done():
        CUE_WARM_TASK.cancel()
        await asyncio.gather(CUE_WARM_TASK, return_exceptions=True)


@router.get("/voice/cues/{locale}")
async def voice_cue(locale: str, request: Request):
    await asyncio.to_thread(auth.session_user, request)
    if locale not in voice_cues.PHRASES:
        auth.fail(404, "NOT_FOUND", "语音片段不存在。")
    try:
        path = await voice_cues.prepare(locale)
    except Exception:
        auth.fail(503, "CUE_UNAVAILABLE", "简短语音暂不可用。")
    return FileResponse(
        path, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=86400"}
    )


def file_root():
    root = Path(os.getenv("DUET_DATA_DIR", ".runtime/employee-data")).resolve() / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def publish_file(thread_id, content, media_type, suffix, source_record_id=None):
    artifact_id = uid("artifact")
    relative = f"{thread_id}/{artifact_id}{suffix}"
    path = file_root() / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o600)
    with transaction() as c:
        if source_record_id:
            c.execute(select(threads.c.id).where(threads.c.id == thread_id).with_for_update()).one()
            record = (
                c.execute(
                    select(records)
                    .where(records.c.id == source_record_id, records.c.thread_id == thread_id)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            c.execute(
                update(records)
                .where(records.c.id == source_record_id)
                .values(
                    metadata={
                        **record["metadata"],
                        "artifact_ids": [*record["metadata"].get("artifact_ids", []), artifact_id],
                    }
                )
            )
        c.execute(
            insert(artifacts).values(
                id=artifact_id,
                thread_id=thread_id,
                path=relative,
                media_type=media_type,
                checksum=hashlib.sha256(content).hexdigest(),
                size=len(content),
                created_at=now(),
            )
        )
    return artifact_id


@lru_cache
def recognizer():
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from faster_whisper import WhisperModel

    model = os.getenv("WHISPER_MODEL", "tiny")
    cache = Path(os.getenv("WHISPER_MODEL_CACHE", str(file_root().parent / "models/whisper")))
    prepared = cache / ("faster-whisper-" + model)
    if (prepared / "model.bin").is_file():
        model = str(prepared)
    return WhisperModel(
        model,
        device="cpu",
        compute_type="int8",
        download_root=str(cache),
    )


def decode_pcm(content):
    import av
    import numpy as np

    chunks = []
    sample_count = 0
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(io.BytesIO(content), mode="r") as source:
        for frame in source.decode(audio=0):
            frame.pts = None
            for converted in resampler.resample(frame):
                chunk = converted.to_ndarray().reshape(-1)
                sample_count += len(chunk)
                if sample_count > 16000 * 300:
                    raise ValueError("AUDIO_TOO_LONG")
                chunks.append(chunk)
        chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    return np.concatenate(chunks).astype("<i2").tobytes() if chunks else b""


async def transcribe_cloud_file(content, user_id):
    pcm = await asyncio.to_thread(decode_pcm, content)
    if not pcm:
        return ""
    output = []
    # Reuse streaming auth/resource selection for the compatibility upload route.
    for start in range(0, len(pcm), 16000 * 2 * 150):
        stream = cloud_voice.VolcASR(user_id)
        latest = ""

        async def collect():
            nonlocal latest
            async for packet, _ in stream.results():
                result = packet.get("result") or {}
                if isinstance(result, list):
                    result = result[0] if result else {}
                if result.get("text"):
                    latest = result["text"].strip()

        reader = None
        try:
            await stream.open()
            reader = asyncio.create_task(collect())
            part = pcm[start : start + 16000 * 2 * 150]
            for position in range(0, len(part), 6400):
                await stream.send(part[position : position + 6400])
                await asyncio.sleep(0.2)
            await stream.finish()
            await asyncio.wait_for(reader, 12)
            if latest:
                output.append(latest)
        finally:
            if reader and not reader.done():
                reader.cancel()
            if reader:
                await asyncio.gather(reader, return_exceptions=True)
            await stream.close()
    return "\n".join(output)


def transcribe_bytes(content):
    if not STT_LOCK.acquire(blocking=False):
        raise RuntimeError("STT_BUSY")
    try:
        import numpy as np

        pcm = decode_pcm(content)
        if not pcm:
            return ""
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        from .voice_text import format_transcript

        beam_size = min(5, max(1, int(os.getenv("WHISPER_BEAM_SIZE", "1"))))
        segments, _ = recognizer().transcribe(
            audio,
            beam_size=beam_size,
            vad_filter=True,
            task="transcribe",
            language=None,
        )
        return format_transcript(segments)
    finally:
        STT_LOCK.release()


@router.get("/voice/config")
def voice_config(request: Request):
    auth.session_user(request)
    try:
        asr = cloud_voice.provider()
        tts = cloud_voice.provider("tts")
        streaming = asr == "volcengine" and cloud_voice.configured()
    except cloud_voice.SpeechError as exc:
        auth.fail(503, exc.code, exc.message)
    return {
        "asr_provider": asr,
        "tts_provider": tts,
        "streaming": streaming,
        "cues": {
            locale: voice_cues.cue_url(locale)
            for locale in voice_cues.PHRASES
            if voice_cues.cue_path(locale).is_file()
        },
    }


@router.websocket("/voice/dictation")
async def dictation_socket(websocket: WebSocket, workspace_id: str):
    from .voice_stream import authenticate, cloud_session

    try:
        request, actor = await authenticate(websocket, workspace_id=workspace_id)
    except Exception:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    if cloud_voice.provider() != "volcengine":
        await websocket.send_json(
            {
                "type": "error",
                "fatal": True,
                "code": "VOICE_NOT_CONFIGURED",
                "message": "请先在服务端配置火山语音 API Key，再使用实时转写。",
            }
        )
        await websocket.close()
        return
    await cloud_session(websocket, request, actor)


@router.post("/voice/transcribe")
async def transcribe(
    request: Request, audio: UploadFile = File(...), workspace_id: str = Form(...)
):
    actor = await asyncio.to_thread(auth.session_user, request)

    def check():
        with transaction() as c:
            repo.workspace_access(c, actor, workspace_id, write=True)

    await asyncio.to_thread(check)
    content = await audio.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        auth.fail(413, "AUDIO_TOO_LARGE", "录音不能超过 20 MiB，请分段发送。")
    try:
        if cloud_voice.provider() == "volcengine":
            text = await asyncio.wait_for(transcribe_cloud_file(content, actor["id"]), timeout=330)
        else:
            text = await asyncio.wait_for(asyncio.to_thread(transcribe_bytes, content), timeout=60)
    except cloud_voice.SpeechError as exc:
        auth.fail(503, exc.code, exc.message)
    except Exception:
        auth.fail(503, "STT_UNAVAILABLE", "语音转写暂时不可用，请改用文字或检查语音模型。")
    ignored = transcript_ignore_reason(text)
    return {
        "text": "" if ignored else text,
        "kind": "transcript",
        "confirmed": False,
        "ignored": bool(ignored),
        "reason": ignored,
    }


@router.post("/records/{record_id}/speech")
async def speech(record_id: str, request: Request, stream: bool = False):
    actor = await asyncio.to_thread(auth.session_user, request)

    def load():
        with transaction() as c:
            record = c.execute(select(records).where(records.c.id == record_id)).mappings().first()
            if not record:
                auth.fail(404, "NOT_FOUND", "记录不存在。")
            _, workspace = repo.thread_access(c, actor, record["thread_id"])
            from .answer_policy import match

            if match(c, workspace["id"], record["text"]):
                auth.fail(403, "ANSWER_RESTRICTED", "该内容目前不支持朗读。")
            if record["visibility"] != "user_shared" or record["withdrawn"]:
                auth.fail(404, "NOT_FOUND", "记录不可播放。")
            return dict(record)

    record = await asyncio.to_thread(load)
    if stream and cloud_voice.provider("tts") == "volcengine":
        for key, value in list(SPEECH_TICKETS.items()):
            if value["expires"] < time.monotonic():
                SPEECH_TICKETS.pop(key, None)
        if len(SPEECH_TICKETS) >= 256:
            auth.fail(429, "TTS_BUSY", "语音请求较多，请稍后重试。")
        ticket = secrets.token_urlsafe(24)
        SPEECH_TICKETS[ticket] = {
            "user_id": actor["id"],
            "session": actor["session_digest"],
            "record_id": record_id,
            "revision": record["revision"],
            "expires": time.monotonic() + 60,
        }
        return {
            "url": f"/api/v1/records/{record_id}/speech/stream?ticket={ticket}",
            "provider": "volcengine",
            "delivery": "stream",
        }
    import edge_tts

    text = (
        public_answer(record["text"])[:10000]
        if record["role"] == "assistant"
        else record["text"][:10000]
    )
    chinese = any("\u4e00" <= ch <= "\u9fff" for ch in text)
    voice = (
        os.getenv("TTS_CHINESE_VOICE", "zh-CN-XiaoxiaoNeural")
        if chinese
        else os.getenv("TTS_ENGLISH_VOICE", "en-US-AriaNeural")
    )
    if cloud_voice.provider("tts") == "volcengine":
        try:
            output = await cloud_voice.synthesize(text, actor["id"])
        except cloud_voice.SpeechError as exc:
            auth.fail(503, exc.code, exc.message)
        fresh = await asyncio.to_thread(load)
        if fresh["revision"] != record["revision"]:
            auth.fail(409, "VERSION_CONFLICT", "文字已修订，请重新生成语音。")
        artifact_id = await asyncio.to_thread(
            publish_file, record["thread_id"], output, "audio/mpeg", ".mp3", record_id
        )
        return {
            "artifact_id": artifact_id,
            "url": f"/api/v1/artifacts/{artifact_id}",
            "provider": "volcengine",
        }
    try:
        output = bytearray()
        media_type, suffix = "audio/mpeg", ".mp3"
        async with asyncio.timeout(12):
            async for chunk in edge_tts.Communicate(text, voice).stream():
                if chunk["type"] == "audio":
                    output.extend(chunk["data"])
        if not output:
            raise ValueError("Empty audio")
    except Exception:
        if not shutil.which("espeak-ng"):
            auth.fail(503, "TTS_UNAVAILABLE", "语音合成暂时不可用，文字回复已保留。")
        process = await asyncio.create_subprocess_exec(
            "espeak-ng",
            "-v",
            "cmn" if chinese else "en-us",
            "-s",
            "165",
            "--stdout",
            text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
            if process.returncode or len(output) < 100:
                raise RuntimeError("EMPTY_LOCAL_SPEECH")
        except Exception:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            auth.fail(503, "TTS_UNAVAILABLE", "离线朗读暂时不可用，文字回复已保留。")
        media_type, suffix = "audio/wav", ".wav"
    # Recheck after a potentially long upstream operation.
    fresh = await asyncio.to_thread(load)
    if fresh["revision"] != record["revision"]:
        auth.fail(409, "VERSION_CONFLICT", "文字已修订，请重新生成语音。")
    artifact_id = await asyncio.to_thread(
        publish_file, record["thread_id"], bytes(output), media_type, suffix, record_id
    )
    return {"artifact_id": artifact_id, "url": f"/api/v1/artifacts/{artifact_id}"}


@router.get("/records/{record_id}/speech/stream")
async def speech_stream(record_id: str, request: Request, ticket: str):
    actor = await asyncio.to_thread(auth.session_user, request)
    grant = SPEECH_TICKETS.pop(ticket, None)
    if (
        not grant
        or grant["expires"] < time.monotonic()
        or grant["user_id"] != actor["id"]
        or grant["session"] != actor["session_digest"]
        or grant["record_id"] != record_id
    ):
        auth.fail(403, "SPEECH_EXPIRED", "朗读请求已过期，请重新播放。")

    def load():
        fresh_actor = auth.session_user(request)
        with transaction() as c:
            record = c.execute(select(records).where(records.c.id == record_id)).mappings().first()
            if not record or record["withdrawn"] or record["visibility"] != "user_shared":
                auth.fail(404, "NOT_FOUND", "记录不可播放。")
            _, workspace = repo.thread_access(c, fresh_actor, record["thread_id"])
            from .answer_policy import match

            if match(c, workspace["id"], record["text"]):
                auth.fail(403, "ANSWER_RESTRICTED", "该内容目前不支持朗读。")
            if record["revision"] != grant["revision"]:
                auth.fail(409, "VERSION_CONFLICT", "文字已修订，请重新生成语音。")
            return dict(record)

    record = await asyncio.to_thread(load)
    source = cloud_voice.synthesize_stream(public_answer(record["text"])[:10000], actor["id"])
    try:
        first = await anext(source)
        await asyncio.to_thread(load)
    except cloud_voice.SpeechError as exc:
        await source.aclose()
        auth.fail(503, exc.code, exc.message)
    except BaseException:
        await source.aclose()
        raise

    async def output():
        content = bytearray(first)
        checked = time.monotonic()
        try:
            yield first
            async for chunk in source:
                if time.monotonic() - checked >= 3:
                    await asyncio.to_thread(load)
                    checked = time.monotonic()
                content.extend(chunk)
                yield chunk
            await asyncio.to_thread(load)
            await asyncio.to_thread(
                publish_file, record["thread_id"], bytes(content), "audio/mpeg", ".mp3", record_id
            )
        finally:
            await source.aclose()

    return StreamingResponse(
        output(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "private, no-store",
            "X-Accel-Buffering": "no",
            "Accept-Ranges": "none",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/artifacts/{artifact_id}")
def artifact(artifact_id: str, request: Request):
    actor = auth.session_user(request)
    with transaction() as c:
        item = c.execute(select(artifacts).where(artifacts.c.id == artifact_id)).mappings().first()
        if not item:
            auth.fail(404, "NOT_FOUND", "文件不存在。")
        repo.thread_access(c, actor, item["thread_id"])
        import json

        linked = list(
            c.execute(
                select(records).where(
                    records.c.thread_id == item["thread_id"],
                    func.JSON_CONTAINS(
                        records.c.metadata, json.dumps(artifact_id), "$.artifact_ids"
                    )
                    == 1,
                )
            ).mappings()
        )
        if linked and not any(
            not r["withdrawn"]
            and (r["visibility"] == "user_shared" or "hinter_admin" in actor["roles"])
            for r in linked
        ):
            auth.fail(404, "NOT_FOUND", "文件已撤回或不可访问。")
    path = (file_root() / item["path"]).resolve()
    if not path.is_relative_to(file_root()) or not path.is_file():
        auth.fail(404, "NOT_FOUND", "文件不存在。")
    return FileResponse(
        path, media_type=item["media_type"], headers={"Cache-Control": "private, no-store"}
    )


@router.websocket("/threads/{thread_id}/voice")
async def voice_socket(websocket: WebSocket, thread_id: str):
    # Client sends confirmed utterance frames; disconnect never cancels a Run.
    request = Request(
        {
            **websocket.scope,
            "type": "http",
            "method": "GET",
            "scheme": "https" if websocket.scope["scheme"] == "wss" else "http",
        }
    )
    try:
        if not request.headers.get("origin"):
            auth.fail(403, "CSRF_FAILED", "语音连接需要有效来源。")
        auth.origin_check(request)
        actor = await asyncio.to_thread(auth.session_user, request)
        with transaction() as c:
            repo.thread_access(c, actor, thread_id, write=True)
    except Exception:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    if cloud_voice.provider() == "volcengine":
        from .voice_stream import cloud_session

        await cloud_session(websocket, request, actor, thread_id=thread_id)
        return
    await websocket.send_json({"type": "voice.ready", "provider": "local", "streaming": False})
    import array
    import math
    import time
    from collections import deque

    pending = bytearray()
    lead = deque(maxlen=6)
    speaking = False
    silent_samples = 0
    voiced_samples = 0
    last_auth = 0.0
    expected_sequence = expected_sample = None
    damaged = False
    cue_echo = voice_cues.CueEchoGuard()

    jobs = asyncio.Queue(maxsize=8)
    closing = False
    processing = False
    end_silence_samples = 16 * min(3000, max(600, int(os.getenv("VOICE_END_SILENCE_MS", "1200"))))

    async def send(message):
        if closing:
            return
        try:
            await websocket.send_json({"pending": jobs.qsize() + int(processing), **message})
        except (RuntimeError, OSError, WebSocketDisconnect):
            pass

    async def process_audio(content):
        await send({"type": "transcript.processing"})
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(content)
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(transcribe_bytes, buffer.getvalue()), timeout=60
            )
        except Exception:
            await send(
                {
                    "type": "error",
                    "message": "转写暂时不可用，请检查语音模型或改用文字。",
                    "pending": jobs.qsize(),
                }
            )
            return
        ignored = (
            "acknowledgement_echo" if cue_echo.is_echo(text) else transcript_ignore_reason(text)
        )
        if ignored:
            await send({"type": "transcript.ignored", "reason": ignored, "pending": jobs.qsize()})
            return
        fresh_actor = await asyncio.to_thread(auth.session_user, request)
        from .web import Input

        body = Input.model_validate({"input": {"kind": "transcript", "text": text}})
        result = await asyncio.to_thread(
            repo.submit_input, fresh_actor, thread_id, body, uid("voice")
        )
        if result.get("ignored"):
            await send(
                {
                    "type": "transcript.ignored",
                    "reason": result["reason"],
                    "retryable": result.get("retryable", False),
                    "message": result.get("message", ""),
                    "pending": jobs.qsize(),
                }
            )
            return
        cue_echo.accept(result["run_id"])
        await send({"type": "transcript.final", "text": text, "pending": jobs.qsize(), **result})
        try:
            await asyncio.to_thread(
                publish_file, thread_id, buffer.getvalue(), "audio/wav", ".wav", result["record_id"]
            )
        except Exception:
            await send({"type": "warning", "message": "文字已记录，原始音频附件未能保存。"})

    async def process_jobs():
        nonlocal processing
        while True:
            content = await jobs.get()
            processing = True
            try:
                await process_audio(content)
            except Exception:
                await send(
                    {
                        "type": "error",
                        "message": "这段语音未能提交，请检查登录和服务状态。",
                        "pending": jobs.qsize(),
                    }
                )
            finally:
                processing = False
                jobs.task_done()

    async def commit_audio(notify=True):
        nonlocal pending, speaking, silent_samples, voiced_samples
        content = bytes(pending)
        pending.clear()
        lead.clear()
        speaking = False
        silent_samples = voiced_samples = 0
        if len(content) < 8000:
            return
        if jobs.full():
            await send(
                {"type": "error", "message": "语音转写队列较忙，请暂停片刻；已收集的分句正在排队。"}
            )
        await jobs.put(content)
        if notify:
            await send({"type": "transcript.queued"})

    processor = asyncio.create_task(process_jobs())
    try:
        while True:
            frame = await websocket.receive()
            if frame["type"] == "websocket.disconnect":
                return
            if time.monotonic() - last_auth > 1:
                actor = await asyncio.to_thread(auth.session_user, request)
                last_auth = time.monotonic()
            if frame.get("bytes") is not None:
                pcm = frame["bytes"]
                if pcm[:4] == b"DVP3":
                    if len(pcm) < 20:
                        raise ValueError("Invalid capture header")
                    _, sequence, sample_start, count, _ = struct.unpack("<IIQHH", pcm[:20])
                    pcm = pcm[20:]
                    if len(pcm) != count * 2 or count != 512:
                        raise ValueError("Invalid capture sample count")
                    if expected_sequence is not None and (
                        sequence != expected_sequence or sample_start != expected_sample
                    ):
                        pending.clear()
                        lead.clear()
                        speaking = False
                        silent_samples = voiced_samples = 0
                        damaged = True
                        await websocket.send_json(
                            {"type": "error", "message": "音频传输有缺口，请停顿后重说这一句。"}
                        )
                    expected_sequence = (sequence + 1) % (2**32)
                    expected_sample = sample_start + count
                if len(pcm) > 65536 or len(pcm) % 2:
                    raise ValueError("Invalid PCM frame")
                samples = array.array("h", pcm)
                rms = math.sqrt(
                    sum((sample / 32768) ** 2 for sample in samples) / max(len(samples), 1)
                )
                voiced = rms > 0.012
                if damaged:
                    silent_samples = 0 if voiced else silent_samples + len(samples)
                    if silent_samples >= 12800:
                        damaged = False
                        silent_samples = 0
                    continue
                if not speaking:
                    lead.append(pcm)
                    if not voiced:
                        continue
                    pending.extend(b"".join(lead))
                    lead.clear()
                    speaking = True
                else:
                    pending.extend(pcm)
                if voiced:
                    voiced_samples += len(samples)
                    silent_samples = 0
                else:
                    silent_samples += len(samples)
                if (silent_samples >= end_silence_samples and voiced_samples >= 4000) or len(
                    pending
                ) >= 960000:
                    await commit_audio()
                elif silent_samples >= 32000:
                    pending.clear()
                    speaking = False
                    silent_samples = voiced_samples = 0
                continue
            message = __import__("json").loads(frame.get("text") or "{}")
            if message.get("type") == "audio.cue":
                cue_echo.started(message.get("locale"), message.get("run_id"))
            elif message.get("type") == "audio.interrupt":
                await websocket.send_json({"type": "audio.interrupted", "runs_cancelled": False})
            elif message.get("type") in ("audio.commit", "audio.finish"):
                await commit_audio()
            elif message.get("type") == "input.confirmed":
                from .web import Input

                body = Input.model_validate(
                    {
                        "input": {
                            "kind": "transcript",
                            "text": message.get("text", ""),
                            "source_record_id": message.get("source_record_id"),
                        }
                    }
                )
                result = await asyncio.to_thread(
                    repo.submit_input, actor, thread_id, body, message.get("idempotency_key")
                )
                await websocket.send_json({"type": "input.accepted", **result})
            elif message.get("type") in ("ping", "audio.start"):
                await websocket.send_json({"type": "pong"})
    except Exception:
        try:
            await websocket.close(code=4403)
        except RuntimeError:
            pass
    finally:
        closing = True
        # Drain accepted turns in order. STT never blocks receipt of subsequent PCM.
        with anyio.CancelScope(shield=True):
            await commit_audio(notify=False)
            await jobs.join()
            processor.cancel()
            await asyncio.gather(processor, return_exceptions=True)
