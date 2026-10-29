"""HTTP and realtime endpoints for the local web application."""

import asyncio
import json
import time
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, File, Form, Header, UploadFile, WebSocket
from fastapi.responses import FileResponse, StreamingResponse

from app.adapters.wiki import USER_IDS
from app.agent.extensions import inspect as inspect_extensions
from app.agent.tool_config import load_tool_config
from app.api.auth import identity, require, sign, verify
from app.application.service import SessionService, fail, uid
from app.domain.models import (
    AiModeRequest,
    DraftEdit,
    DraftPolish,
    LoginRequest,
    MessageRequest,
    PublishDecision,
    ReviewRequest,
    SessionRequest,
    TalkerRequest,
    WikiClearRequest,
    WikiRequest,
)

Actor = Annotated[dict, Depends(identity)]
Key = Annotated[str | None, Header(alias="Idempotency-Key")]


def make_router(service: SessionService) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/agent/tools")
    async def agent_tools(actor: Actor, probe: bool = False) -> dict:
        require(actor, "hinter")
        config = load_tool_config()
        return {
            **await inspect_extensions(config, probe),
            "web_search_provider": config.web_search_provider,
            "response_path": "ResponseService / CallReasoner",
            "tool_calling_enabled": True,
        }

    @router.post("/auth/demo")
    async def login(request: LoginRequest) -> dict:
        payload = {
            "role": request.role,
            "user_id": service.talker(request.talker_id)["id"]
            if request.role == "talker"
            else USER_IDS["hinter"],
            "tenant_id": "local-demo",
            "expires": time.time() + 86400,
        }
        return {"token": sign(payload), "role": request.role, "user_id": payload["user_id"]}

    @router.get("/talkers")
    async def list_talkers(actor: Actor) -> dict:
        return {"items": service.directory()}

    @router.post("/talkers", status_code=201)
    async def create_talker(request: TalkerRequest, actor: Actor) -> dict:
        return service.create_talker(request.name)

    @router.delete("/talkers/{talker_id}")
    async def delete_talker(talker_id: str, actor: Actor) -> dict:
        require(actor, "hinter")
        return service.delete_talker(talker_id)

    @router.get("/sessions")
    async def list_sessions(actor: Actor, talker_id: str | None = None) -> dict:
        owner = talker_id or (actor["user_id"] if actor["role"] == "talker" else None)
        return {
            "items": [
                s
                for t in service.directory()
                if owner is None or t["id"] == owner
                for s in t["sessions"]
            ]
        }

    @router.get("/sessions/default")
    async def default_session(actor: Actor, talker_id: str | None = None) -> dict:
        owner = talker_id or (actor["user_id"] if actor["role"] == "talker" else None)
        return service.snapshot(service.default(owner)["id"], actor["role"])

    @router.post("/sessions", status_code=201)
    async def create_session(request: SessionRequest, actor: Actor) -> dict:
        if not request.talker_id and actor["role"] == "talker":
            request.talker_id = actor["user_id"]
        return service.snapshot(service.create(request)["id"], actor["role"])

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str, actor: Actor) -> dict:
        return service.snapshot(session_id, actor["role"])

    @router.post("/sessions/{session_id}/reset", status_code=201)
    async def reset_session(
        session_id: str, actor: Actor, request: dict = Body(default={})
    ) -> dict:
        active = service.get(session_id).get("call")
        if active and active.get("status") != "ended":
            await service.calls.end(session_id, active["id"])
        async with service.locks[session_id]:
            old = service.get(session_id)
            if old.get("next_session_id"):
                return service.snapshot(old["next_session_id"], actor["role"])
            fields = {
                k: old[k]
                for k in ["talker_id", "customer_id", "customer_name", "language", "title"]
            }
            created = service.create(SessionRequest(**fields))
            created["ai_mode"] = old.get("ai_mode", False)
            service.store.save(created)
            old["next_session_id"] = created["id"]
            old["status"] = "ended"
            service.wiki_for(old).sync_session(old)
            service.publish(old, "session.reset")
            service.store.set_setting("active_session:" + old["talker_id"], created["id"])
        return service.snapshot(created["id"], actor["role"])

    @router.post("/sessions/{session_id}/sample", status_code=202)
    async def sample(session_id: str, actor: Actor) -> dict:
        require(actor, "talker")
        await service.sample(session_id)
        return {"ok": True}

    @router.get("/sessions/{session_id}/messages")
    async def messages(session_id: str, actor: Actor) -> dict:
        return {
            "items": service.snapshot(session_id, actor["role"])["messages"],
            "page": {"next_cursor": None},
        }

    @router.post("/sessions/{session_id}/messages", status_code=202)
    async def create_message(
        session_id: str, request: MessageRequest, actor: Actor, idempotency_key: Key = None
    ) -> dict:
        require(actor, "talker")
        return await service.submit(session_id, request.text, key=idempotency_key)

    @router.post("/sessions/{session_id}/messages/{message_id}/retry", status_code=202)
    async def retry_message(
        session_id: str, message_id: str, actor: Actor, request: dict = Body(default={})
    ) -> dict:
        require(actor, "talker")
        await service.retry_message(session_id, message_id, request.get("text"))
        return {"ok": True}

    @router.get("/sessions/{session_id}/questions")
    async def questions(
        session_id: str, actor: Actor, status: str | None = None, cursor: int = 0, limit: int = 50
    ) -> dict:
        items = service.snapshot(session_id, actor["role"])["questions"]
        if status:
            items = [q for q in items if q["status"] == status]
        end = cursor + max(1, min(limit, 100))
        return {
            "items": items[cursor:end],
            "page": {"next_cursor": end if end < len(items) else None},
        }

    @router.post("/sessions/{session_id}/reviews", status_code=201)
    async def review(
        session_id: str, request: ReviewRequest, actor: Actor, idempotency_key: Key = None
    ) -> dict:
        require(actor, "hinter")
        return await service.review(session_id, request, idempotency_key)

    @router.post("/sessions/{session_id}/guidance", status_code=201)
    async def guidance(session_id: str, actor: Actor, request: dict = Body()) -> dict:
        require(actor, "hinter")
        return await service.guidance.submit(session_id, request)

    @router.post("/sessions/{session_id}/questions/{question_id}/resolve", status_code=202)
    async def resolve_question(
        session_id: str, question_id: str, actor: Actor, request: dict = Body(default={})
    ) -> dict:
        require(actor, "hinter")
        return await service.replies.resolve(session_id, question_id, request)

    @router.post("/sessions/{session_id}/responses/stop")
    async def stop_response(session_id: str, actor: Actor) -> dict:
        await service.replies.cancel_outputs(session_id)
        return {"ok": True}

    @router.get("/sessions/{session_id}/answers")
    async def answers(session_id: str, actor: Actor) -> dict:
        return {
            "items": service.snapshot(session_id, actor["role"])["answers"],
            "page": {"next_cursor": None},
        }

    @router.post("/sessions/{session_id}/answers/{answer_id}/parts/{part_id}", status_code=202)
    async def correct_answer_part(
        session_id: str, answer_id: str, part_id: str, actor: Actor, request: dict = Body()
    ) -> dict:
        from app.application.answer_cards import card_question_ids

        require(actor, "hinter")
        state = service.get(session_id)
        question_ids = card_question_ids(state, answer_id)
        question = next((q for q in state["questions"] if q["id"] in question_ids
                         and any(p["id"] == part_id for p in q.get("answer_parts", []))), None)
        if not question or question["status"] in {"withdrawn", "rejected", "blocked"}:
            raise fail(409, "ANSWER_PART_CHANGED", "该项回答已更新或撤回，请刷新后再补充。")
        part = next(p for p in question["answer_parts"] if p["id"] == part_id)
        if "expected_answer" in request and request["expected_answer"] != part["answer"]:
            raise fail(409, "ANSWER_PART_CHANGED", "该项已被更新，请核对当前内容后再提交。")
        return await service.guidance.submit(session_id, {
            "text": str(request.get("text", "")), "question_id": question["id"],
            "part_id": part_id, "kind": "instruction", "effective_from": "now",
        })

    @router.post("/sessions/{session_id}/answers/{answer_id}/retry", status_code=202)
    async def retry_answer(session_id: str, answer_id: str, actor: Actor) -> dict:
        require(actor, "hinter")
        await service.retry_answer(session_id, answer_id)
        return {"ok": True}

    @router.post("/sessions/{session_id}/answers/{answer_id}/decision")
    async def decide_answer(
        session_id: str, answer_id: str, request: PublishDecision, actor: Actor
    ) -> dict:
        require(actor, "hinter")
        await service.decide_answer(session_id, answer_id, request.action, request.revision)
        return {"ok": True, "action": request.action}

    @router.put("/sessions/{session_id}/ai-mode")
    async def ai_mode(session_id: str, request: AiModeRequest, actor: Actor) -> dict:
        require(actor, "hinter")
        await service.set_ai_mode(session_id, request.enabled)
        return {"ok": True, "enabled": request.enabled}

    @router.put("/sessions/{session_id}/answers/{answer_id}/draft")
    async def edit_draft(session_id: str, answer_id: str, request: DraftEdit, actor: Actor) -> dict:
        require(actor, "hinter")
        await service.edit_draft(session_id, answer_id, request.text, request.revision)
        return {"ok": True}

    @router.post("/sessions/{session_id}/answers/{answer_id}/polish")
    async def polish_draft(
        session_id: str, answer_id: str, request: DraftPolish, actor: Actor
    ) -> dict:
        require(actor, "hinter")
        await service.polish_draft(session_id, answer_id, request.hint, request.revision)
        return {"ok": True}

    @router.post("/sessions/{session_id}/answers/{answer_id}/audio/retry", status_code=202)
    async def retry_tts(session_id: str, answer_id: str, actor: Actor) -> dict:
        answer = next((a for a in service.get(session_id)["answers"] if a["id"] == answer_id), None)
        if not answer or answer["status"] != "completed":
            raise fail(409, "INVALID_STATE", "当前回答尚未完成。")
        service.spawn(service.synthesize(session_id, answer_id))
        return {"ok": True}

    def wiki_target(
        actor: dict, session_id: str | None, talker_id: str | None, path: str = "context.md"
    ) -> tuple:
        if session_id:
            state = service.get(session_id)
            if talker_id and state["talker_id"] != talker_id:
                raise fail(400, "WORKSPACE_MISMATCH", "会话与 Talker 不匹配。")
        else:
            owner = service.talker(
                talker_id or (actor["user_id"] if actor["role"] == "talker" else None)
            )["id"]
            state = {"talker_id": owner}
        private = path.startswith("private/")
        if private:
            require(actor, "hinter")
        return (
            service.wiki_for(state),
            "hinter" if private else "talker",
            path.removeprefix("private/") if private else path,
            state["talker_id"],
        )

    async def notify_wiki(owner: str) -> None:
        for state in service.store.sessions():
            if state["talker_id"] == owner:
                async with service.locks[state["id"]]:
                    service.publish(service.get(state["id"]), "wiki.updated")

    @router.get("/wiki")
    async def wiki_documents(
        actor: Actor, session_id: str | None = None, talker_id: str | None = None
    ) -> dict:
        wiki, lane, _, owner = wiki_target(actor, session_id, talker_id)
        documents = wiki.list(lane)
        if actor["role"] == "hinter":
            documents += [{**d, "path": "private/" + d["path"]} for d in wiki.list("hinter")]
        return {"user_id": owner, "documents": documents}

    @router.post("/wiki/clear")
    async def clear_wiki(
        request: WikiClearRequest,
        actor: Actor,
        session_id: str | None = None,
        talker_id: str | None = None,
    ) -> dict:
        require(actor, "hinter")
        _, _, _, owner = wiki_target(actor, session_id, talker_id)
        if any(
            s["talker_id"] == owner and s.get("call") and s["call"]["status"] != "ended"
            for s in service.store.sessions()
        ):
            raise fail(409, "ACTIVE_CALL_CONSTRAINTS", "请先挂断此 Talker 的通话，再清空 Wiki。")
        await service.clear_wiki(request.confirmation, owner)
        return {"ok": True}

    @router.get("/wiki/document")
    async def wiki_document(
        actor: Actor,
        path: str = "context.md",
        session_id: str | None = None,
        talker_id: str | None = None,
    ) -> dict:
        wiki, lane, name, _ = wiki_target(actor, session_id, talker_id, path)
        try:
            return {**wiki.read(lane, name), "path": path}
        except (FileNotFoundError, ValueError):
            raise fail(404, "NOT_FOUND", "Wiki 文档不存在。") from None

    @router.put("/wiki/document")
    async def save_wiki(
        request: WikiRequest,
        actor: Actor,
        path: str = "context.md",
        session_id: str | None = None,
        talker_id: str | None = None,
    ) -> dict:
        wiki, lane, name, owner = wiki_target(actor, session_id, talker_id, path)
        if name.startswith("constraints/"):
            raise fail(409, "CORE_CONSTRAINT", "核心约束请通过提示的修订或撤销操作修改。")
        try:
            result = wiki.write(lane, name, request.content, request.revision)
        except ValueError as exc:
            raise fail(409, "DOCUMENT_CONFLICT", str(exc)) from None
        await notify_wiki(owner)
        return {**result, "path": path}

    @router.delete("/wiki/document")
    async def delete_wiki(
        actor: Actor, path: str, session_id: str | None = None, talker_id: str | None = None
    ) -> dict:
        wiki, lane, name, owner = wiki_target(actor, session_id, talker_id, path)
        if name.startswith("constraints/"):
            raise fail(409, "CORE_CONSTRAINT", "核心约束请通过提示的撤销操作修改。")
        if name == "context.md":
            raise fail(400, "CORE_DOCUMENT", "context.md 是中心文档，可以编辑正文。")
        try:
            wiki.path(lane, name).unlink()
        except (ValueError, FileNotFoundError):
            raise fail(404, "NOT_FOUND", "文档不存在。") from None
        await notify_wiki(owner)
        return {"ok": True}

    @router.post("/sessions/{session_id}/audio", status_code=202)
    async def upload_audio(
        session_id: str,
        actor: Actor,
        file: UploadFile = File(),
        transcript: str = Form(default=""),
        idempotency_key: Key = None,
    ) -> dict:
        require(actor, "talker")
        service.get(session_id)
        data = await file.read()
        if not data:
            raise fail(400, "EMPTY_AUDIO", "录音为空，请重新录制。")
        audio_id = uid()
        filename = audio_id + (".wav" if "wav" in (file.content_type or "") else ".webm")
        (service.audio_dir / filename).write_bytes(data)
        audio = {"id": audio_id, "kind": "original", "status": "ready", "filename": filename}
        return await service.submit(session_id, transcript, "voice", audio, idempotency_key)

    @router.post("/sessions/{session_id}/audio/{audio_id}/playback", status_code=201)
    async def playback(session_id: str, audio_id: str, actor: Actor) -> dict:
        state = service.get(session_id)
        if any(
            a["status"] == "withdrawn" and (a.get("tts_audio", {}).get("id") == audio_id)
            for a in state["answers"]
        ):
            raise fail(410, "ANSWER_WITHDRAWN", "该回答已撤回，音频不可播放。")
        assets = [m.get("audio") for m in state["messages"]] + [
            a.get("tts_audio") for a in state["answers"]
        ]
        audio = next((a for a in assets if a and a["id"] == audio_id), None)
        if not audio or not audio.get("filename"):
            raise fail(404, "NOT_FOUND", "音频尚未就绪。")
        token = sign({"session_id": session_id, "audio_id": audio_id, "expires": time.time() + 300})
        return {
            "audio_id": audio_id,
            "playback_url": f"/api/v1/sessions/{session_id}/audio/{audio_id}?token={token}",
            "expires_at": time.time() + 300,
        }

    @router.get("/sessions/{session_id}/audio/{audio_id}")
    async def download_audio(session_id: str, audio_id: str, token: str) -> FileResponse:
        payload = verify(token)
        if payload.get("session_id") != session_id or payload.get("audio_id") != audio_id:
            raise fail(403, "FORBIDDEN", "音频授权无效。")
        state = service.get(session_id)
        if any(
            a["status"] == "withdrawn" and (a.get("tts_audio", {}).get("id") == audio_id)
            for a in state["answers"]
        ):
            raise fail(410, "ANSWER_WITHDRAWN", "该回答已撤回，音频不可播放。")
        assets = [m.get("audio") for m in state["messages"]] + [
            a.get("tts_audio") for a in state["answers"]
        ]
        audio = next((a for a in assets if a and a["id"] == audio_id), None)
        if not audio or not audio.get("filename"):
            raise fail(404, "NOT_FOUND", "音频不存在。")
        return FileResponse(service.audio_dir / audio["filename"])

    @router.websocket("/ws/sessions/{session_id}")
    async def realtime(
        websocket: WebSocket, session_id: str, token: str, after_sequence: int = 0
    ) -> None:
        try:
            actor = verify(token)
            if actor.get("role") not in ["talker", "hinter"]:
                raise ValueError
            service.get(session_id)
        except Exception:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        sequence = after_sequence
        heartbeat = time.monotonic()
        try:
            while True:
                if service.store.session_deleted(session_id):
                    await websocket.send_json({"type": "session.deleted"})
                    await websocket.close(code=4404)
                    return
                wake = service.changed[session_id]
                wake.clear()
                events = service.store.events(session_id, sequence)
                if events:
                    # Snapshots are projected before serialization, including private events.
                    event = events[-1]
                    sequence = event["sequence"]
                    await websocket.send_json(
                        {
                            **event,
                            "data": service.snapshot(
                                (
                                    service.get(session_id).get("next_session_id", session_id)
                                    if actor["role"] == "talker"
                                    else session_id
                                ),
                                actor["role"],
                            ),
                        }
                    )
                elif time.monotonic() - heartbeat > 15:
                    await websocket.send_json({"type": "ping"})
                    heartbeat = time.monotonic()
                try:
                    await asyncio.wait_for(wake.wait(), timeout=0.5)
                except TimeoutError:
                    pass
        except Exception:
            return

    @router.get("/sessions/{session_id}/events")
    async def events(session_id: str, token: str, after_sequence: int = 0) -> StreamingResponse:
        actor = verify(token)
        service.get(session_id)

        async def stream() -> Any:
            sequence = after_sequence
            while True:
                if service.store.session_deleted(session_id):
                    yield 'data: {"type":"session.deleted"}\n\n'
                    return
                rows = service.store.events(session_id, sequence)
                if rows:
                    event = rows[-1]
                    sequence = event["sequence"]
                    data = {
                        **event,
                        "data": service.snapshot(
                            (
                                service.get(session_id).get("next_session_id", session_id)
                                if actor["role"] == "talker"
                                else session_id
                            ),
                            actor["role"],
                        ),
                    }
                    yield "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.3)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return router
