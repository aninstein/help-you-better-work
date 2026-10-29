"""One streaming ASR session, with distinct call and editable dictation semantics."""

import asyncio
import io
import json
import struct
import time
import wave

import anyio
from fastapi import Request, WebSocket, WebSocketDisconnect

from . import auth
from . import repository as repo
from .db import transaction
from .input_preprocessing.rules import allows_partial_interruption
from .voice_cues import CueEchoGuard
from .voice_text import transcript_ignore_reason
from .volcengine_voice import SpeechError, VolcASR


def socket_request(websocket):
    return Request(
        {
            **websocket.scope,
            "type": "http",
            "method": "GET",
            "scheme": "https" if websocket.scope["scheme"] == "wss" else "http",
        }
    )


async def authenticate(websocket, *, thread_id=None, workspace_id=None):
    request = socket_request(websocket)
    if not request.headers.get("origin"):
        auth.fail(403, "CSRF_FAILED", "语音连接需要有效来源。")
    auth.origin_check(request)
    actor = await asyncio.to_thread(auth.session_user, request)

    def check():
        with transaction() as c:
            if thread_id:
                repo.thread_access(c, actor, thread_id, write=True)
            else:
                repo.workspace_access(c, actor, workspace_id, write=True)

    await asyncio.to_thread(check)
    return request, actor


class CaptureFrames:
    def __init__(self):
        self.sequence = self.sample = None

    def decode(self, packet):
        if len(packet) < 20 or packet[:4] != b"DVP3":
            raise SpeechError("AUDIO_PROTOCOL_ERROR", "录音格式无效，请刷新页面重试。")
        _, sequence, sample, count, _ = struct.unpack("<IIQHH", packet[:20])
        if count != 512 or len(packet) != 20 + count * 2:
            raise SpeechError("AUDIO_PROTOCOL_ERROR", "录音数据长度无效，请重试。")
        if self.sequence is not None and (sequence != self.sequence or sample != self.sample):
            raise SpeechError("AUDIO_GAP", "录音传输出现缺口，请重新录制；已确认的文字已保留。")
        self.sequence, self.sample = (sequence + 1) % 2**32, sample + count
        return packet[20:]


async def cloud_session(websocket: WebSocket, request, actor, *, thread_id=None):
    """Accepted socket. Calls publish stable utterances; dictation only returns a draft."""
    from .voice import publish_file
    from .web import Input

    send_lock = asyncio.Lock()
    detached = False
    stream, receiver = None, None
    frames = CaptureFrames()
    audio = bytearray()
    outgoing = bytearray()
    stable = {}
    latest = ""
    dictation_parts = []
    cue_echo = CueEchoGuard()
    started = last_auth = time.monotonic()

    async def send(message):
        nonlocal detached
        if detached:
            return
        try:
            async with send_lock:
                await asyncio.wait_for(websocket.send_json(message), 5)
        except (RuntimeError, OSError, TimeoutError, WebSocketDisconnect):
            detached = True

    async def check_access():
        fresh_actor = await asyncio.to_thread(auth.session_user, request)
        if fresh_actor["auth_version"] != actor["auth_version"]:
            raise PermissionError("Authorization changed")
        return fresh_actor

    jobs = asyncio.Queue(maxsize=8)

    async def publish_transcript(text, clip, command_key):
        fresh_actor = await check_access()
        body = Input.model_validate({"input": {"kind": "transcript", "text": text}})
        result_ids = await asyncio.to_thread(
            repo.submit_input,
            fresh_actor,
            thread_id,
            body,
            command_key,
        )
        if result_ids.get("ignored"):
            await send(
                {
                    "type": "transcript.ignored",
                    "reason": result_ids["reason"],
                    "retryable": result_ids.get("retryable", False),
                    "message": result_ids.get("message", ""),
                    "pending": jobs.qsize(),
                }
            )
            return
        cue_echo.accept(result_ids["run_id"])
        await send(
            {
                "type": "transcript.final",
                "text": text,
                "pending": jobs.qsize(),
                "provider": "volcengine",
                **result_ids,
            }
        )
        if clip:
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(clip)
            try:
                await asyncio.to_thread(
                    publish_file,
                    thread_id,
                    buffer.getvalue(),
                    "audio/wav",
                    ".wav",
                    result_ids["record_id"],
                )
            except Exception:
                await send({"type": "warning", "message": "文字已记录，原始音频附件未能保存。"})

    async def process_jobs():
        while True:
            job = await jobs.get()
            try:
                await publish_transcript(*job)
            except Exception:
                await send({"type": "warning", "message": "这段语音未能确认，请重试或改用文字。"})
            finally:
                jobs.task_done()

    processor = asyncio.create_task(process_jobs())

    async def consume():
        nonlocal latest
        async for packet, last in stream.results():
            result = packet.get("result") or {}
            if isinstance(result, list):
                result = result[0] if result else {}
            utterances = result.get("utterances") or []
            # Full-result packets repeat earlier utterances. Only new recognition
            # can confirm an interruption; an old cumulative caption cannot.
            active_utterance = next(
                (
                    u
                    for u in reversed(utterances)
                    if (int(u.get("start_time", 0)), int(u.get("end_time", 0))) not in stable
                    and u.get("text", "").strip()
                    and not transcript_ignore_reason(u["text"])
                    and not cue_echo.is_echo(u["text"])
                    and allows_partial_interruption(u["text"])
                ),
                None,
            )
            activity = None
            if active_utterance:
                start = int(active_utterance.get("start_time", 0))
                end = int(active_utterance.get("end_time", 0))
                activity = {
                    "id": f"{stream.session_id}:{start}",
                    "text": active_utterance["text"].strip(),
                    "duration_ms": max(0, end - start),
                }
            if result.get("text"):
                latest = result["text"].strip()
                await send(
                    {
                        "type": "transcript.partial",
                        "text": ("\n".join([*dictation_parts, latest]) if not thread_id else latest)
                        if not transcript_ignore_reason(latest)
                        else "",
                        "provider": "volcengine",
                        "activity": activity,
                    }
                )
            if last and not utterances and latest and not any(stable.values()):
                utterances = [
                    {
                        "text": latest,
                        "start_time": 0,
                        "end_time": len(audio) // 32,
                        "definite": True,
                    }
                ]
            for utterance in utterances:
                if not (utterance.get("definite") or last) or not utterance.get("text", "").strip():
                    continue
                start, end = int(utterance.get("start_time", 0)), int(utterance.get("end_time", 0))
                key = (start, end)
                if key in stable:
                    continue
                text = utterance["text"].strip()
                ignored = (
                    "acknowledgement_echo"
                    if cue_echo.is_echo(text)
                    else transcript_ignore_reason(text)
                )
                if ignored:
                    stable[key] = ""
                    if thread_id:
                        await send({"type": "transcript.ignored", "reason": ignored, "pending": 0})
                    continue
                # A stable utterance is immutable. Partial revisions never enter records/Run queues.
                if thread_id:
                    stable[key] = text
                    if jobs.full():
                        await send({"type": "warning", "message": "语音正在确认中，请稍候再说。"})
                        continue
                    clip = bytes(audio[max(0, start * 32) : max(0, end * 32)])
                    jobs.put_nowait((text, clip, f"asr:{stream.session_id}:{start}:{end}"))
                    await send({"type": "transcript.processing"})
                else:
                    stable[key] = text

    async def finish_segment():
        await stream.finish(bytes(outgoing))
        outgoing.clear()
        await asyncio.wait_for(asyncio.shield(receiver), 12)
        await asyncio.wait_for(asyncio.shield(jobs.join()), 90)

    try:
        while True:
            stream = VolcASR(actor["id"])
            await stream.open()
            receiver = asyncio.create_task(consume())
            await send({"type": "voice.ready", "provider": "volcengine", "streaming": True})
            segment_start = time.monotonic()
            rotate = False
            while True:
                if receiver.done():
                    receiver.result()
                    raise SpeechError(
                        "ASR_DISCONNECTED", "火山转写连接提前结束，请重试；已确认文字已保留。"
                    )
                if detached:
                    break
                try:
                    frame = await asyncio.wait_for(websocket.receive(), 1)
                except TimeoutError:
                    if not thread_id and time.monotonic() - started >= 180:
                        break
                    continue
                if frame["type"] == "websocket.disconnect":
                    detached = True
                    break
                if time.monotonic() - last_auth > 5:
                    await check_access()
                    last_auth = time.monotonic()
                if frame.get("bytes") is not None:
                    pcm = frames.decode(frame["bytes"])
                    audio.extend(pcm)
                    outgoing.extend(pcm)
                    if len(outgoing) >= 3200:
                        await stream.send(bytes(outgoing))
                        outgoing.clear()
                    if time.monotonic() - segment_start >= 180 or len(audio) >= 16000 * 2 * 180:
                        rotate = bool(thread_id)
                        break
                else:
                    message = json.loads(frame.get("text") or "{}")
                    if message.get("type") == "audio.cue":
                        cue_echo.started(message.get("locale"), message.get("run_id"))
                    if message.get("type") in ("audio.finish", "audio.commit"):
                        await send({"type": "transcript.processing"})
                        break
                    if message.get("type") == "audio.interrupt":
                        await send({"type": "audio.interrupted", "runs_cancelled": False})
                    if message.get("type") == "ping":
                        await send({"type": "pong"})
            # Client hangup doesn't discard an already captured final sentence.
            with anyio.CancelScope(shield=True):
                await finish_segment()
            if not thread_id:
                final_text = (
                    "\n".join(part for part in stable.values() if part) if stable else latest
                )
                if transcript_ignore_reason(final_text):
                    final_text = ""
                if final_text:
                    dictation_parts.append(final_text)
                await send(
                    {
                        "type": "dictation.final",
                        "text": "\n".join(dictation_parts),
                        "provider": "volcengine",
                    }
                )
            await stream.close()
            if not rotate or detached:
                break
            audio.clear()
            stable.clear()
            latest = ""
    except SpeechError as exc:
        await send({"type": "error", "code": exc.code, "message": exc.message, "fatal": True})
    except PermissionError:
        await send(
            {
                "type": "error",
                "code": "AUTHORIZATION_REVOKED",
                "fatal": True,
                "message": "登录或权限已变化，请重新登录后使用语音。",
            }
        )
    except Exception:
        await send(
            {
                "type": "error",
                "code": "VOICE_STREAM_FAILED",
                "fatal": True,
                "message": "实时转写中断，请检查网络、Key 与产品配置；已确认的文字已保留。",
            }
        )
    finally:
        if receiver and not receiver.done():
            receiver.cancel()
        if receiver:
            await asyncio.gather(receiver, return_exceptions=True)
        with anyio.CancelScope(shield=True):
            try:
                await asyncio.wait_for(jobs.join(), 90)
            except TimeoutError:
                pass
            processor.cancel()
            await asyncio.gather(processor, return_exceptions=True)
        if stream:
            await stream.close()
        if not detached:
            try:
                await websocket.close()
            except RuntimeError:
                pass
