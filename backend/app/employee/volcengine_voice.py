"""Official Doubao ASR v3 binary WebSocket and TTS v3 HTTP adapters.

Credentials only leave the server for the fixed official openspeech endpoint.
"""

import asyncio
import base64
import gzip
import json
import os
import struct
import time
import uuid
from pathlib import Path

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus


class SpeechError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(code)


def api_key():
    path = os.getenv("VOLCENGINE_API_KEY_FILE", "").strip()
    if path:
        try:
            return Path(path).read_text().strip()
        except OSError:
            raise SpeechError(
                "VOICE_KEY_UNREADABLE", "无法读取火山语音 Key 配置，请联系管理员。"
            ) from None
    return (os.getenv("VOLCENGINE_API_KEY") or os.getenv("VOLCENGINE_APP_KEY") or "").strip()


def configured():
    return bool(
        api_key() or (os.getenv("VOLCENGINE_APP_ID") and os.getenv("VOLCENGINE_ACCESS_TOKEN"))
    )


def provider(kind="asr"):
    name = os.getenv("VOICE_ASR_PROVIDER" if kind == "asr" else "VOICE_TTS_PROVIDER", "auto")
    if name not in ("auto", "local", "volcengine"):
        raise SpeechError("VOICE_CONFIG_INVALID", "语音服务配置无效，请联系管理员。")
    return "volcengine" if name == "volcengine" or name == "auto" and configured() else "local"


def headers(resource):
    key = api_key()
    result = {
        "X-Api-Resource-Id": resource,
        "X-Api-Connect-Id": str(uuid.uuid4()),
        "X-Api-Request-Id": str(uuid.uuid4()),
    }
    if key:
        result["X-Api-Key"] = key
    elif os.getenv("VOLCENGINE_APP_ID") and os.getenv("VOLCENGINE_ACCESS_TOKEN"):
        result.update(
            {
                "X-Api-App-Id": os.environ["VOLCENGINE_APP_ID"],
                "X-Api-Access-Key": os.environ["VOLCENGINE_ACCESS_TOKEN"],
            }
        )
    else:
        raise SpeechError(
            "VOICE_NOT_CONFIGURED", "请先在服务端配置火山语音 API Key，再使用实时转写。"
        )
    return result


def request_frame(message_type, payload, *, final=False, serialized=False):
    compressed = gzip.compress(payload)
    return (
        bytes([0x11, (message_type << 4) | (2 if final else 0), (0x10 if serialized else 0) | 1, 0])
        + struct.pack(">I", len(compressed))
        + compressed
    )


def parse_frame(frame):
    if not isinstance(frame, bytes) or len(frame) < 8 or frame[0] >> 4 != 1:
        raise SpeechError("ASR_PROTOCOL_ERROR", "火山转写返回了无效数据，请重试。")
    offset = (frame[0] & 15) * 4
    kind, flags = frame[1] >> 4, frame[1] & 15
    if offset < 4:
        raise SpeechError("ASR_PROTOCOL_ERROR", "火山转写响应头无效。")
    if kind == 15:
        # Never forward an upstream body; it may include diagnostic/request data.
        raise SpeechError(
            "ASR_PROVIDER_ERROR", "火山转写失败，请检查 Key、产品权限、额度和资源 ID。"
        )
    if kind != 9:
        raise SpeechError("ASR_PROTOCOL_ERROR", "火山转写消息类型无效。")
    if flags & 1:
        offset += 4
    if len(frame) < offset + 4:
        raise SpeechError("ASR_PROTOCOL_ERROR", "火山转写响应不完整。")
    size = struct.unpack_from(">I", frame, offset)[0]
    payload = frame[offset + 4 :]
    if len(payload) != size or size > 2 * 1024 * 1024:
        raise SpeechError("ASR_PROTOCOL_ERROR", "火山转写响应长度无效。")
    if frame[2] & 15 == 1:
        # Bound decompression as well as the wire payload.
        import io

        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as source:
            payload = source.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024 or frame[2] >> 4 != 1:
        raise SpeechError("ASR_PROTOCOL_ERROR", "火山转写响应格式无效。")
    data = json.loads(payload)
    if data.get("code") not in (None, 0, 1000, 20000000):
        raise SpeechError("ASR_PROVIDER_ERROR", "火山转写失败，请检查产品权限和额度。")
    return data, bool(flags & 2)


class VolcASR:
    def __init__(self, user_id):
        self.user_id = user_id
        self.socket = None
        self.ended = False
        self.session_id = str(uuid.uuid4())

    async def open(self):
        resource = os.getenv("VOLCENGINE_ASR_RESOURCE_ID", "volc.seedasr.sauc.duration")
        try:
            self.socket = await connect(
                "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
                additional_headers=headers(resource),
                open_timeout=10,
                close_timeout=2,
                max_size=2 * 1024 * 1024,
                ping_interval=20,
                ping_timeout=20,
            )
            audio = {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1}
            language = os.getenv("VOLCENGINE_ASR_LANGUAGE", "").strip()
            if language:
                audio["language"] = language
            body = {
                "user": {"uid": self.user_id},
                "audio": audio,
                "request": {
                    "model_name": "bigmodel",
                    "enable_itn": True,
                    "enable_punc": True,
                    "enable_nonstream": True,
                    "show_utterances": True,
                    "result_type": "full",
                    "end_window_size": min(
                        3000, max(600, int(os.getenv("VOLCENGINE_ASR_END_WINDOW_MS", "800")))
                    ),
                },
            }
            await self.socket.send(request_frame(1, json.dumps(body).encode(), serialized=True))
            # Fail authentication/configuration before starting microphone capture.
            first, last = parse_frame(await asyncio.wait_for(self.socket.recv(), 10))
            if last:
                raise SpeechError("ASR_PROVIDER_ERROR", "火山转写未能建立会话，请检查服务配置。")
            return first
        except SpeechError:
            await self.close()
            raise
        except InvalidStatus as exc:
            await self.close()
            status = exc.response.status_code
            # Inspect only a known error marker; never expose arbitrary upstream diagnostics.
            body = bytes(exc.response.body or b"").decode(errors="replace").lower()
            if status == 403 and "requested resource not granted" in body:
                raise SpeechError(
                    "ASR_RESOURCE_NOT_GRANTED",
                    "火山未授权流式语音识别 2.0 小时版；请在该 Key 所属项目开通服务后重试。",
                ) from None
            if status in (401, 403):
                raise SpeechError(
                    "ASR_AUTH_FAILED", "火山语音鉴权失败，请检查 API Key、项目和服务权限。"
                ) from None
            if status == 429:
                raise SpeechError(
                    "ASR_RATE_LIMITED", "火山语音并发或调用额度受限，请稍后重试或检查配额。"
                ) from None
            raise SpeechError(
                "ASR_UPSTREAM_REJECTED", "火山语音服务拒绝连接，请稍后重试。"
            ) from None
        except Exception:
            await self.close()
            raise SpeechError(
                "ASR_CONNECT_FAILED", "无法连接火山转写，请检查网络、Key 和服务开通状态。"
            ) from None

    async def send(self, pcm):
        if self.socket and not self.ended:
            await asyncio.wait_for(self.socket.send(request_frame(2, pcm)), 10)

    async def finish(self, tail=b""):
        if self.socket and not self.ended:
            self.ended = True
            await asyncio.wait_for(self.socket.send(request_frame(2, tail, final=True)), 10)

    async def results(self):
        while True:
            data, last = parse_frame(await self.socket.recv())
            yield data, last
            if last:
                return

    async def close(self):
        if self.socket:
            await self.socket.close()
            self.socket = None


async def synthesize_stream(text, user_id):
    """Yield MP3 chunks as they arrive; never wait for the complete utterance."""
    resource = os.getenv("VOLCENGINE_TTS_RESOURCE_ID", "seed-tts-2.0")
    request_headers = headers(resource)
    request_headers["X-Api-Request-Id"] = str(uuid.uuid4())
    body = {
        "user": {"uid": user_id},
        "req_params": {
            "text": text,
            "speaker": os.getenv("VOLCENGINE_TTS_SPEAKER", "zh_female_vv_uranus_bigtts"),
            "audio_params": {"format": "mp3", "sample_rate": 24000},
            "additions": json.dumps({"enable_language_detector": True}),
        },
    }
    received = 0
    finished = False
    deadline = time.monotonic() + 45
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            async with client.stream(
                "POST",
                "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                headers=request_headers,
                json=body,
            ) as response:
                if response.status_code in (401, 403):
                    diagnostic = (await response.aread())[:65536].lower()
                    if b"requested resource not granted" in diagnostic:
                        raise SpeechError(
                            "TTS_RESOURCE_NOT_GRANTED",
                            "火山未授权语音合成 2.0；请在该 Key 所属项目开通服务并确认音色可用。",
                        )
                    raise SpeechError(
                        "TTS_AUTH_FAILED", "火山朗读鉴权失败，请检查 API Key、项目和音色权限。"
                    )
                if response.status_code == 429:
                    raise SpeechError("TTS_RATE_LIMITED", "火山朗读额度或并发受限，请稍后重试。")
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if time.monotonic() > deadline:
                        raise SpeechError("TTS_TIMEOUT", "语音生成等待超时，请重新播放。")
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event.get("code") == 20000000:
                        finished = True
                        break
                    if event.get("code") != 0:
                        raise SpeechError(
                            "TTS_PROVIDER_ERROR", "火山朗读失败，请检查音色授权、额度和服务配置。"
                        )
                    if event.get("data"):
                        chunk = base64.b64decode(event["data"], validate=True)
                        received += len(chunk)
                        if received > 20 * 1024 * 1024:
                            raise SpeechError("TTS_TOO_LARGE", "朗读内容过长，请缩短文本。")
                        if chunk:
                            yield chunk
        if not finished or not received:
            raise SpeechError("TTS_INCOMPLETE", "火山朗读未完整返回，请重试；文字回复已保留。")
    except SpeechError:
        raise
    except Exception:
        raise SpeechError(
            "TTS_UNAVAILABLE", "火山朗读暂时不可用，请检查网络、Key 和音色配置。"
        ) from None


async def synthesize(text, user_id):
    """Buffered compatibility path for downloads and older clients."""
    return b"".join([chunk async for chunk in synthesize_stream(text, user_id)])
