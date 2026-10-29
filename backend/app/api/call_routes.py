"""Call lifecycle, supervisor controls, and a continuous binary audio socket."""

import asyncio
import json
import time

from fastapi import APIRouter, Body, Depends, WebSocket

from app.api.auth import identity, require, verify
from app.application.calls import CallService
from app.application.service import fail


def make_call_router(calls: CallService) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/sessions/{session_id}/calls")
    async def start(
        session_id: str, body: dict = Body(default={}), actor: dict = Depends(identity)
    ) -> dict:
        require(actor, "talker")
        # An already-open page can retain the old, incompatible PCM player after
        # a backend update. Do not start a call whose replies it cannot decode.
        if body.get("output_protocol") != 4:
            raise fail(409, "CALL_PLAYER_UPDATE_REQUIRED", "通话播放器已更新，请刷新页面后重新拨打。")
        return await calls.start(session_id, body.get("turn_protocol", 2))

    @router.post("/sessions/{session_id}/calls/{call_id}/hangup")
    async def hangup(session_id: str, call_id: str, actor: dict = Depends(identity)) -> dict:
        await calls.end(session_id, call_id)
        return {"ok": True}

    @router.post("/sessions/{session_id}/calls/{call_id}/interventions")
    async def intervene(
        session_id: str, call_id: str, body: dict = Body(), actor: dict = Depends(identity)
    ) -> dict:
        require(actor, "hinter")
        if len(str(body.get("text", ""))) > 40000:
            raise fail(400, "HINT_TOO_LONG", "提示请控制在4万字内。")
        return await calls.intervene(session_id, call_id, body)

    @router.post("/sessions/{session_id}/calls/{call_id}/recognition-context")
    async def recognition_context(
        session_id: str, call_id: str, body: dict = Body(), actor: dict = Depends(identity)
    ) -> dict:
        require(actor, "hinter")
        return await calls.recognition_context(session_id, call_id, body.get("terms"))

    @router.post("/sessions/{session_id}/calls/{call_id}/reviews")
    async def review(
        session_id: str, call_id: str, body: dict = Body(), actor: dict = Depends(identity)
    ) -> dict:
        require(actor, "hinter")
        return await calls.review(session_id, call_id, body)

    @router.post("/sessions/{session_id}/answers/{answer_id}/withdraw")
    async def withdraw(
        session_id: str,
        answer_id: str,
        actor: dict = Depends(identity),
    ) -> dict:
        require(actor, "hinter")
        return await calls.withdraw_answer(session_id, answer_id)

    @router.post("/sessions/{session_id}/retraction-checks/{check_id}/retry")
    async def retry_check(
        session_id: str,
        check_id: str,
        actor: dict = Depends(identity),
    ) -> dict:
        require(actor, "hinter")
        return await calls.retry_retrospection(session_id, check_id)

    @router.websocket("/ws/sessions/{session_id}/calls/{call_id}")
    async def media(socket: WebSocket, session_id: str, call_id: str) -> None:
        await socket.accept()
        live = None
        try:
            auth = await asyncio.wait_for(socket.receive_json(), 5)
            actor = verify(auth.get("token", ""))
            if actor.get("role") != "talker":
                await socket.close(code=4403)
                return
            live = await calls.attach(session_id, call_id, socket)
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                live.last_seen = time.monotonic()
                if message.get("bytes") is not None:
                    await calls.receive_audio(live, message["bytes"])
                    continue
                text = message.get("text", "")
                if len(text) > 50000:
                    continue
                data = json.loads(text)
                event = data.get("type")
                if event == "ping":
                    await calls.send(live, "pong")
                elif event == "media.settings":
                    state, call = calls.current(session_id, call_id)
                    call["capture_settings"] = {
                        k: v
                        for k, v in data.items()
                        if k
                        in {
                            "sampleRate",
                            "contextRate",
                            "channelCount",
                            "echoCancellation",
                            "noiseSuppression",
                            "autoGainControl",
                        }
                        and isinstance(v, (bool, int, float))
                    }
                    calls.commit(state)
                    if data.get("echoCancellation") is False:
                        await calls.warn(
                            live,
                            "echo_cancellation_unavailable",
                            "当前设备未启用回声消除，建议佩戴耳机以减少误插话。",
                        )
                elif event == "speech_start":
                    # Protocol 2: only server-confirmed speech may cancel output.
                    pass
                elif event == "mute":
                    live.media_sequence.reset()
                    muted = bool(data.get("muted"))
                    if muted:
                        await calls.cancel_input(live, "muted")
                    state, call = calls.current(session_id, call_id)
                    call["muted"] = muted
                    calls.commit(state)
                elif event == "text" and str(data.get("text", "")).strip():
                    await calls.cancel_input(live, "text_input")
                    await calls.submit(live, str(data["text"])[:10000])
                elif event == "delivered":
                    await calls.delivered(live, data)
                elif event in ["playout.ack", "playout.started"]:
                    await calls.service.replies.ack(session_id, data)
                elif event == "hangup":
                    await calls.end(session_id, call_id)
                    return
        except Exception:
            try:
                await socket.close(code=1011)
            except Exception:
                pass
        finally:
            if live and live.socket is socket and call_id in calls.live:
                live.socket = None
                try:
                    await calls.cancel_input(live, "connection_lost")
                    await calls.interrupt(session_id, call_id, "connection_lost")
                    state, call = calls.current(session_id, call_id)
                    call["status"] = "reconnecting"
                    calls.commit(state)
                except Exception:
                    pass

    @router.websocket("/ws/sessions/{session_id}/reply-audio")
    async def reply_audio(socket: WebSocket, session_id: str) -> None:
        await socket.accept()
        queue = None
        sender = None
        try:
            auth = await asyncio.wait_for(socket.receive_json(), 5)
            actor = verify(auth.get("token", ""))
            require(actor, "talker")
            calls.service.get(session_id)
            queue = asyncio.Queue(maxsize=64)
            calls.service.replies.subscribers[session_id].add(queue)
            await socket.send_json({"type": "ready", "output_protocol": 4})

            async def send_audio() -> None:
                while True:
                    data = await queue.get()
                    if data is None:
                        await socket.close(code=1013)
                        return
                    if isinstance(data, bytes):
                        await socket.send_bytes(data)
                    else:
                        await socket.send_json(data)

            sender = asyncio.create_task(send_audio())
            while True:
                data = await socket.receive_json()
                if data.get("type") in ["playout.ack", "playout.started"]:
                    await calls.service.replies.ack(session_id, data)
                elif data.get("type") == "stop":
                    await calls.service.replies.cancel_outputs(session_id)
        except Exception:
            pass
        finally:
            if queue is not None:
                calls.service.replies.subscribers[session_id].discard(queue)
            if sender:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)

    return router
