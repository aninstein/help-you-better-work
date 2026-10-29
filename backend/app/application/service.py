"""Single-service orchestration: intake, review, streaming answers and memory."""

import asyncio
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from app.adapters.speech import SpeechAdapter
from app.adapters.store import Store
from app.adapters.wiki import USER_IDS, WikiStore
from app.agent.model_router import ModelRouter, ModelsUnavailable, load_models
from app.agent.voice_agent import VoiceAgent
from app.application.assistance import assistance_request
from app.application.answer_cards import AnswerCards, card_question_ids
from app.application.guidance import GuidanceService
from app.domain.models import ModelSettings, ReviewRequest, SessionRequest


def uid() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(UTC).isoformat()


def fail(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


class SessionService:
    def __init__(self, store: Store) -> None:
        self.store = store
        # One configured primary model owns every LLM stage. Never silently
        # fall back to a different model for routing, memory or conversation.
        self.agent = VoiceAgent(ModelRouter(load_models()[:1]))
        self.locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.tasks: set[asyncio.Task[Any]] = set()
        self.wiki_version = 0
        self.changed: defaultdict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self.calls: Any = None
        self.speech = SpeechAdapter()
        self.audio_dir = store.path.parent / "audio"
        self.audio_dir.mkdir(exist_ok=True)
        self.wiki = WikiStore(
            store.path.parent / "wiki",
            initialize=not store.talker_deleted(USER_IDS["talker"]),
        )
        self.wikis = {USER_IDS["talker"]: self.wiki}
        self.wiki_locks = defaultdict(asyncio.Lock)
        self.bootstrap_talkers()
        self.guidance = GuidanceService(self)
        self.answer_cards = AnswerCards(self)
        self.replies: Any = None

    def bootstrap_talkers(self) -> None:
        self.store.claim_legacy_guidance(USER_IDS["talker"])
        known = {t["id"] for t in self.store.talkers()}
        if USER_IDS["talker"] not in known and not self.store.talker_deleted(USER_IDS["talker"]):
            self.store.save_talker(
                {"id": USER_IDS["talker"], "name": "默认 Talker", "created_at": now()}
            )
            known.add(USER_IDS["talker"])
        for state in self.store.sessions():
            owner = state.get("talker_id") or state.get("talker_user_id") or USER_IDS["talker"]
            if owner not in known:
                self.store.save_talker(
                    {
                        "id": owner,
                        "name": state.get("talker_name", "Talker"),
                        "created_at": state["created_at"],
                    }
                )
                known.add(owner)
            if not state.get("talker_id"):
                state.update(
                    talker_id=owner, talker_user_id=owner, talker_name=self.talker(owner)["name"]
                )
                self.store.save(state)

    def talker(self, talker_id: str | None = None) -> dict:
        items = self.store.talkers()
        owner = talker_id or USER_IDS["talker"]
        item = next((t for t in items if t["id"] == owner), None)
        if not talker_id and not item and items:
            item = items[0]
        if not item:
            raise fail(404, "TALKER_NOT_FOUND", "Talker 不存在，请返回首页选择。")
        return item

    def create_talker(self, name: str) -> dict:
        name = name.strip()
        if not name:
            raise fail(400, "EMPTY_NAME", "请输入 Talker 名称。")
        item = {"id": "talker-" + uid(), "name": name, "created_at": now()}
        self.store.save_talker(item)
        self.create(SessionRequest(talker_id=item["id"], customer_name=name))
        return next(t for t in self.directory() if t["id"] == item["id"])

    def directory(self) -> list[dict]:
        states = self.store.sessions()
        result = []
        for talker in self.store.talkers():
            sessions = sorted(
                (s for s in states if s["talker_id"] == talker["id"]),
                key=lambda s: s["created_at"],
                reverse=True,
            )
            items = [
                {
                    **{
                        k: s[k]
                        for k in [
                            "id",
                            "talker_id",
                            "title",
                            "created_at",
                            "status",
                            "customer_name",
                            "customer_id",
                            "language",
                        ]
                    },
                    "message_count": len(s["messages"]),
                    "pending_count": sum(
                        q["status"] in ["held", "awaiting_user", "unavailable"]
                        for q in s["questions"]
                    ),
                    "call_active": bool(s.get("call") and s["call"]["status"] != "ended"),
                }
                for s in sessions
            ]
            result.append(
                {
                    **talker,
                    "sessions": items,
                    "session_count": len(items),
                    "latest_session_id": items[0]["id"] if items else None,
                }
            )
        return result

    def wiki_for(self, session: str | dict) -> WikiStore:
        state = self.get(session) if isinstance(session, str) else session
        owner = state["talker_id"]
        if self.store.talker_deleted(owner):
            raise fail(404, "TALKER_NOT_FOUND", "Talker 已删除。")
        if owner not in self.wikis:
            key = hashlib.sha256(owner.encode()).hexdigest()[:32]
            self.wikis[owner] = WikiStore(self.store.path.parent / "wiki" / "talkers" / key)
        return self.wikis[owner]

    def delete_talker(self, talker_id: str) -> dict:
        if not self.store.talker_deleted(talker_id):
            self.talker(talker_id)
        states = [s for s in self.store.sessions() if s["talker_id"] == talker_id]
        if any(s.get("call") and s["call"]["status"] != "ended" for s in states):
            raise fail(409, "CALL_ACTIVE", "此 Talker 正在通话，请先挂断后再删除。")
        wiki = self.wikis.get(talker_id)
        if wiki is None and talker_id == USER_IDS["talker"]:
            wiki = self.wiki
        if wiki is None:
            key = hashlib.sha256(talker_id.encode()).hexdigest()[:32]
            wiki = WikiStore(self.store.path.parent / "wiki" / "talkers" / key, initialize=False)
        removed = self.store.delete_talker(talker_id)
        wiki.destroy()
        self.wikis.pop(talker_id, None)
        self.wiki_version += 1
        self.guidance.cache.clear()
        if self.calls:
            self.calls.context_cache.clear()
        ids = {s["id"] for s in removed}
        if self.replies:
            for (sid, _), task in list(self.replies.jobs.items()):
                if sid in ids:
                    task.cancel()
            for run in list(self.replies.runs.values()):
                if run.session_id in ids:
                    run.cancelled = True
                    for task in [run.task, run.audio_task]:
                        if task and task is not asyncio.current_task():
                            task.cancel()
        for sid in ids:
            self.changed[sid].set()

        def filenames(value: Any) -> set[str]:
            if isinstance(value, dict):
                result = {value["filename"]} if isinstance(value.get("filename"), str) else set()
                return result.union(*(filenames(v) for v in value.values()))
            if isinstance(value, list):
                return set().union(*(filenames(v) for v in value))
            return set()

        unused = filenames(removed) - filenames(self.store.sessions())
        for name in unused:
            path = self.audio_dir / name
            if path.resolve().parent == self.audio_dir.resolve():
                path.unlink(missing_ok=True)
        return {"ok": True, "items": self.directory()}

    def spawn(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def settings(self) -> ModelSettings:
        return self.agent.settings

    def get(self, session_id: str) -> dict[str, Any]:
        state = self.store.load(session_id)
        if not state:
            raise fail(404, "NOT_FOUND", "会话不存在，请返回选择或创建会话。")
        return state

    def require_chat(self, session_id: str) -> None:
        if self.get(session_id).get("interaction_mode") == "realtime_call":
            raise fail(409, "CALL_ACTIVE", "通话进行中，请使用通话提示或先挂断。")

    def publish(self, state: dict[str, Any], event_type: str, **metadata: str) -> None:
        if event_type.startswith("wiki."):
            self.wiki_version += 1
        state["last_sequence"] += 1
        if "data" in metadata:
            raise ValueError("event metadata must not use the reserved data key")
        self.store.save(
            state,
            {
                "event_id": uid(),
                "session_id": state["id"],
                "sequence": state["last_sequence"],
                "type": event_type,
                "occurred_at": now(),
                **metadata,
            },
        )
        self.changed[state["id"]].set()

    def create(self, request: SessionRequest) -> dict[str, Any]:
        talker = self.talker(request.talker_id)
        state = {
            "id": uid(),
            "tenant_id": "local-demo",
            "hinter_user_id": USER_IDS["hinter"],
            **request.model_dump(),
            "talker_id": talker["id"],
            "talker_user_id": talker["id"],
            "talker_name": talker["name"],
            "language": request.language,
            "status": "active",
            "last_sequence": 0,
            "created_at": now(),
            "messages": [],
            "questions": [],
            "answers": [],
            "reviews": [],
            "generations": [],
            "sample_loaded": False,
            "ai_mode": True,
            "response_flow_version": 2,
            "automatic_paused": False,
        }
        self.store.save(state)
        self.wiki_for(state).start_session(state)
        self.store.set_setting("active_session:" + talker["id"], state["id"])
        return state

    def default(self, talker_id: str | None = None) -> dict[str, Any]:
        talker = self.talker(talker_id)
        active = self.store.setting("active_session:" + talker["id"])
        if active and (state := self.store.load(active)) and state.get("talker_id") == talker["id"]:
            return state
        states = [s for s in self.store.sessions() if s["talker_id"] == talker["id"]]
        return (
            max(states, key=lambda s: s["created_at"])
            if states
            else self.create(SessionRequest(talker_id=talker["id"], customer_name=talker["name"]))
        )

    def snapshot(self, session_id: str, role: str) -> dict[str, Any]:
        state = self.get(session_id)
        public_keys = [
            "id",
            "talker_id",
            "talker_name",
            "customer_id",
            "customer_name",
            "title",
            "language",
            "status",
            "last_sequence",
            "created_at",
            "sample_loaded",
        ]
        result = {k: state[k] for k in public_keys}
        result["language"] = state.get("language", "en-US")
        result["role"] = role
        message_keys = [
            "id",
            "type",
            "text",
            "input_mode",
            "status",
            "created_at",
            "question_ids",
            "audio",
            "error",
            "human_reviewed",
            "refusal",
            "published_at",
            "revision",
            "call_id",
            "delivery",
            "turn_id",
            "transcription_status",
            "reply_kind",
            "reply_to_message_id",
            "public_stream",
            "segments",
            "text_done",
            "audio_done",
        ]
        if role == "hinter":
            message_keys += ["transcription"]
        # Retain every attempt in storage, but don't turn empty failed control
        # messages and repeated retries into a wall of identical error bubbles.
        refused_ids = {
            qid
            for a in state["answers"]
            if a.get("reply_kind") == "refusal" and a.get("status") == "completed"
            for qid in a.get("question_ids", [])
        }
        hidden_empty, seen_empty = set(), set()
        for message in reversed(state["messages"]):
            if (
                message["type"] != "ai"
                or not message.get("public_stream")
                or message.get("text", "").strip()
                or message["status"] not in {"interrupted", "failed"}
            ):
                continue
            key = tuple(message.get("question_ids", []))
            if (
                set(key) & refused_ids
                or key in seen_empty
                or message.get("reply_kind") in {"acknowledgement", "progress"}
            ):
                hidden_empty.add(message["id"])
            else:
                seen_empty.add(key)
        result["messages"] = [
            {k: m[k] for k in message_keys if k in m}
            for m in state["messages"]
            if m["status"] != "cancelled"
            and m["id"] not in hidden_empty
            and (
                role == "hinter"
                or (
                    m["type"] != "hinter"
                    and (
                        m["type"] != "ai"
                        or m["status"] in ["completed", "withdrawn"]
                        or m.get("public_stream")
                    )
                )
            )
        ]
        question_keys = [
            "id",
            "display_no",
            "text",
            "status",
            "duplicate_count",
            "human_reviewed",
            "version",
            "created_at",
            "answered_at",
            "call_id",
        ]
        if role == "hinter":
            question_keys += [
                "source_message_ids",
                "dedupe_confidence",
                "source_spans",
                "risk_reason",
                "category",
                "decision",
                "depends_on",
                "work",
                "assistance_request",
                "matched_rule_ids",
                "prohibition_assessment",
                "publication_check",
                "reply_failure",
            ]
        result["questions"] = [
            {k: q[k] for k in question_keys if k in q} for q in state["questions"]
        ]
        if role == "hinter":
            for question in result["questions"]:
                if question["status"] in ["held", "expired", "legacy_pending", "unavailable"]:
                    question.setdefault("assistance_request", assistance_request(question))
        answer_keys = [
            "id",
            "question_ids",
            "text",
            "status",
            "human_reviewed",
            "tts_audio",
            "created_at",
            "error",
            "refusal",
            "published_at",
            "public_stream",
            "segments",
            "text_done",
            "audio_done",
        ]
        if role == "hinter":
            answer_keys += ["wiki_sources", "review_id", "revision"]
        result["answers"] = [
            {k: a[k] for k in answer_keys if k in a}
            for a in state["answers"]
            if a["status"] != "cancelled"
            and a["id"] not in hidden_empty
            and (
                role == "hinter"
                or a["status"] in ["completed", "withdrawn"]
                or a.get("public_stream")
            )
        ]
        fallback_questions = set()
        for item in result["messages"] + result["answers"]:
            if (
                item.get("public_stream")
                and item["status"] in {"failed", "interrupted"}
                and any(term in str(item.get("error", "")) for term in
                        ("回复未完成", "回复中断", "couldn't complete that response"))
            ):
                # Existing failed attempts stay in the audit store, but show a
                # concrete terminal answer rather than an empty error bubble.
                text = item.get("text", "").strip()
                item.update(
                    text=(text + " I don't know.").strip(), status="completed",
                    delivery="sent", text_done=True, audio_done=True,
                )
                for field in ("error", "audio", "tts_audio", "segments"):
                    item.pop(field, None)
                fallback_questions.update(item.get("question_ids", []))
            if item["status"] == "withdrawn":
                item["text"] = "This answer was withdrawn."
                item["delivery"] = "withdrawn"
                for field in ["audio", "tts_audio", "error", "wiki_sources", "transcription"]:
                    item.pop(field, None)
                item.pop("segments", None)
        for question in result["questions"]:
            if question["id"] in fallback_questions and question["status"] == "unavailable":
                question["status"] = "answered"
        result["user_id"] = state["talker_id"] if role == "talker" else USER_IDS[role]
        result["interaction_mode"] = state.get("interaction_mode", "review_chat")
        if state.get("call"):
            call_keys = [
                "id",
                "status",
                "started_at",
                "ended_at",
                "epoch",
                "paused",
                "muted",
                "transport",
                "asr",
                "tts",
                "last_latency_ms",
                "response_id",
                "turn_protocol",
                "endpoint_mode",
                "final_asr",
                "final_asr_ready",
                "activity_state",
                "thinking_started_at",
                "thinking_budget_ms",
                "last_thinking_ms",
                "pending_count",
                "queued_count",
            ]
            if role == "hinter":
                call_keys += [
                    "constraints",
                    "constraints_revision",
                    "recognition_context",
                    "capture_settings",
                    "last_voice_to_send_ms",
                    "alerts",
                    "checks",
                    "error",
                    "active_question_id",
                ]
            result["call"] = {k: state["call"][k] for k in call_keys if k in state["call"]}
        result["wiki"] = {
            "user": role,
            "user_id": state["talker_id"],
            "owner_name": state["talker_name"],
            "documents": self.wiki_for(state).list("talker")
            + (
                [
                    {**d, "path": "private/" + d["path"], "title": "私有 · " + d["title"]}
                    for d in self.wiki_for(state).list("hinter")
                ]
                if role == "hinter"
                else []
            ),
            "context": self.wiki_for(state).read("talker", "context.md")["content"],
        }
        if role == "hinter":
            result["reviews"] = state["reviews"]
            result["ai_mode"] = not state.get("automatic_paused", False)
            result["guidance"] = self.guidance.records(state)
            result["guidance_projection"] = state.get("guidance_projection", "synced")
            check_keys = ["id", "status", "checked_count", "withdrawn_count", "created_at", "error"]
            result["retraction_checks"] = [
                {k: check[k] for k in check_keys if k in check}
                for check in state.get("retraction_checks", [])[-10:]
            ]
        self.answer_cards.project(state, result)
        result["response_flow_version"] = 3
        result["automatic_paused"] = state.get("automatic_paused", False)
        return result

    async def submit(
        self,
        session_id: str,
        text: str,
        mode: str = "text",
        audio: dict[str, Any] | None = None,
        key: str | None = None,
    ) -> dict[str, Any]:
        self.require_chat(session_id)
        text = text.strip()
        if not text and mode == "text":
            raise fail(400, "EMPTY_MESSAGE", "请输入消息。")
        digest = hashlib.sha256(
            json.dumps([text, mode, audio], sort_keys=True).encode()
        ).hexdigest()
        if self.replies:
            await self.replies.cancel_outputs(session_id)
        async with self.locks[session_id]:
            if key and (cached := self.store.command(session_id + ":message:" + key)):
                if cached[0] != digest:
                    raise fail(409, "IDEMPOTENCY_CONFLICT", "同一请求编号对应不同内容。")
                return cached[1]
            state = self.get(session_id)
            message = {
                "id": uid(),
                "type": "talker",
                "text": text,
                "input_mode": mode,
                "status": "intake_pending" if text else "transcribing",
                "created_at": now(),
                "question_ids": [],
                "audio": audio,
            }
            state["messages"].append(message)
            self.wiki_for(state).sync_session(state)
            self.wiki_version += 1
            self.publish(state, "message.created", resource_id=message["id"])
            if key:
                self.store.save_command(session_id + ":message:" + key, digest, message)
        self.spawn(
            self.run_intake(session_id, message["id"])
            if text
            else self.run_transcription(session_id, message["id"])
        )
        return message

    async def run_transcription(self, session_id: str, message_id: str) -> None:
        state = self.get(session_id)
        message = next(m for m in state["messages"] if m["id"] == message_id)
        try:
            text = await self.speech.transcribe(
                self.audio_dir / message["audio"]["filename"], self.settings()
            )
            async with self.locks[session_id]:
                state = self.get(session_id)
                message = next(m for m in state["messages"] if m["id"] == message_id)
                message.update(text=text, status="intake_pending")
                self.publish(state, "transcript.final")
            await self.run_intake(session_id, message_id)
        except Exception:
            async with self.locks[session_id]:
                state = self.get(session_id)
                message = next(m for m in state["messages"] if m["id"] == message_id)
                message.update(
                    status="transcription_failed", error="自动转录不可用，请补充转录文本后重试。"
                )
                self.publish(state, "transcription.failed")

    async def run_intake(self, session_id: str, message_id: str) -> None:
        if self.replies:
            await self.replies.process(session_id, message_id)
            return
        try:
            # Serial intake keeps question numbering and semantic deduplication coherent.
            async with self.locks[session_id]:
                state = self.get(session_id)
                message = next(m for m in state["messages"] if m["id"] == message_id)
                source_text = message["text"]
                existing = list(state["questions"])
                context = self.wiki_for(session_id).bundle("talker", session_id)
            result = await self.agent.intake(source_text, existing, context)
            async with self.locks[session_id]:
                # Refresh in case non-intake metadata was updated while the model was running.
                state = self.get(session_id)
                message = next(m for m in state["messages"] if m["id"] == message_id)
                if message["text"] != source_text or message["status"] != "intake_pending":
                    return
                linked = []
                for item in result.questions:
                    reason = item.blocked_reason
                    if reason:
                        question = {
                            "id": uid(),
                            "display_no": f"Q-{201 + len(state['questions'])}",
                            "text": item.text,
                            "status": "blocked",
                            "version": 1,
                            "duplicate_count": 1,
                            "human_reviewed": False,
                            "source_message_ids": [message_id],
                            "created_at": message["created_at"],
                        }
                        state["questions"].append(question)
                        linked.append(question["id"])
                        state["messages"].append(
                            {
                                "id": uid(),
                                "type": "screening",
                                "text": reason,
                                "status": "completed",
                                "created_at": now(),
                                "question_ids": [question["id"]],
                            }
                        )
                        continue
                    duplicate = next(
                        (
                            q
                            for q in state["questions"]
                            if q["id"] == item.duplicate_of and item.confidence >= 0.8
                        ),
                        None,
                    )
                    if duplicate is None:
                        duplicate = next(
                            (
                                q
                                for q in state["questions"]
                                if SequenceMatcher(
                                    None, q["text"].lower().strip(), item.text.lower().strip()
                                ).ratio()
                                > 0.93
                            ),
                            None,
                        )
                    if duplicate:
                        duplicate["duplicate_count"] += item.duplicate_count
                        duplicate["source_message_ids"].append(message_id)
                        duplicate["dedupe_confidence"] = item.confidence
                        linked.append(duplicate["id"])
                        continue
                    question = {
                        "id": uid(),
                        "display_no": f"Q-{201 + len(state['questions'])}",
                        "text": item.text,
                        "status": "pending_review",
                        "version": 1,
                        "duplicate_count": item.duplicate_count,
                        "human_reviewed": False,
                        "source_message_ids": [message_id],
                        "dedupe_confidence": item.confidence,
                        "source_spans": [item.source_text],
                        "created_at": message["created_at"],
                    }
                    state["questions"].append(question)
                    linked.append(question["id"])
                message.update(status="intake_complete", question_ids=list(dict.fromkeys(linked)))
                message.pop("error", None)
                state["messages"].append(
                    {
                        "id": uid(),
                        "type": "screening",
                        "text": result.summary,
                        "status": "completed",
                        "question_ids": message["question_ids"],
                        "created_at": now(),
                    }
                )
                self.wiki_for(state).sync_session(state)
                self.publish(state, "intake.completed")
                if state.get("ai_mode"):
                    self.spawn(self.prepare_automatically(session_id))
                if not result.questions:
                    self.spawn(
                        self.remember_statement(session_id, message["text"], message["created_at"])
                    )
        except Exception as exc:
            async with self.locks[session_id]:
                state = self.get(session_id)
                message = next(m for m in state["messages"] if m["id"] == message_id)
                message.update(
                    status="intake_failed",
                    error=str(exc)
                    if isinstance(exc, ModelsUnavailable)
                    else "问题识别失败，请重试。",
                )
                self.publish(state, "intake.failed")

    async def review(
        self, session_id: str, request: ReviewRequest, key: str | None = None
    ) -> dict[str, Any]:
        self.require_chat(session_id)
        digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        async with self.locks[session_id]:
            if key and (cached := self.store.command(session_id + ":review:" + key)):
                if cached[0] != digest:
                    raise fail(409, "IDEMPOTENCY_CONFLICT", "请求编号对应不同审核内容。")
                return cached[1]
            state = self.get(session_id)
            selected = []
            for item in request.items:
                question = next(
                    (q for q in state["questions"] if q["id"] == item.question_id), None
                )
                if not question:
                    raise fail(404, "NOT_FOUND", "问题不存在。")
                allowed = (
                    ["pending_review"]
                    if request.action == "approve"
                    else ["pending_review", "generating", "pending_confirmation", "failed"]
                )
                if question["version"] != item.version or question["status"] not in allowed:
                    raise fail(409, "VERSION_CONFLICT", "问题已更新或已经处理，请刷新后再试。")
                if question not in selected:
                    selected.append(question)
            if request.action == "reject":
                selected_ids = {q["id"] for q in selected}
                for generation in state["generations"]:
                    if generation["status"] in [
                        "running",
                        "awaiting_confirmation",
                        "failed",
                    ] and selected_ids.intersection(generation["question_ids"]):
                        self.cancel_draft(state, generation)
            review = {
                "id": uid(),
                "action": request.action,
                "question_ids": [q["id"] for q in selected],
                "reviewer_id": USER_IDS["hinter"],
                "hint": request.hint or "",
                "review_notes": request.review_notes or "",
                "created_at": now(),
            }
            state["reviews"].append(review)
            for question in selected:
                question.pop("auto_suppressed", None)
                question.update(
                    status="generating" if request.action == "approve" else "rejected",
                    human_reviewed=True,
                    version=question["version"] + 1,
                )
            if request.action == "approve":
                generation = {
                    "id": uid(),
                    "review_id": review["id"],
                    "status": "running",
                    "question_ids": review["question_ids"],
                    "fence_token": 1,
                }
                review["generation_id"] = generation["id"]
                state["generations"].append(generation)
                if review["hint"]:
                    state["messages"].append(
                        {
                            "id": uid(),
                            "type": "hinter",
                            "text": review["hint"],
                            "question_ids": review["question_ids"],
                            "status": "completed",
                            "created_at": now(),
                        }
                    )
            if request.action == "reject":
                refused_at = now()
                for question in selected:
                    question["answered_at"] = refused_at
                refusal = {
                    "id": uid(),
                    "question_ids": review["question_ids"],
                    "text": "Sorry, I dont know.",
                    "status": "completed",
                    "human_reviewed": True,
                    "refusal": True,
                    "review_id": review["id"],
                    "created_at": refused_at,
                    "published_at": refused_at,
                    "tts_audio": {"id": uid(), "kind": "tts", "status": "queued"},
                }
                state["answers"].append(refusal)
                state["messages"].append(
                    {k: v for k, v in refusal.items() if k != "tts_audio"}
                    | {"type": "ai", "audio": refusal["tts_audio"]}
                )
                self.wiki_for(state).record_refusal(state, [q["text"] for q in selected])
            self.publish(state, "review.created")
            self.wiki_for(state).sync_session(state)
            if key:
                self.store.save_command(session_id + ":review:" + key, digest, review)
        if request.action == "approve":
            self.spawn(self.generate(session_id, generation["id"], 1))
        else:
            self.spawn(self.synthesize(session_id, refusal["id"]))
        return review

    async def generate(self, session_id: str, generation_id: str, fence: int) -> None:
        answer_id = uid()
        try:
            async with self.locks[session_id]:
                state = self.get(session_id)
                generation = next(g for g in state["generations"] if g["id"] == generation_id)
                review = next(r for r in state["reviews"] if r["id"] == generation["review_id"])
                if (
                    review["action"] != "approve"
                    or generation["fence_token"] != fence
                    or generation["status"] != "running"
                ):
                    return
                context = self.wiki_for(session_id).bundle("talker", session_id)
                questions = [
                    q["text"] for q in state["questions"] if q["id"] in generation["question_ids"]
                ]
                source_ids = {
                    source_id
                    for q in state["questions"]
                    if q["id"] in generation["question_ids"]
                    for source_id in q.get("source_message_ids", [])
                }
                source_texts = [
                    m["text"]
                    for m in state["messages"]
                    if m["id"] in source_ids and m["type"] == "talker"
                ]
                history = [
                    {"type": m["type"], "text": m["text"]}
                    for m in state["messages"]
                    if m["type"] == "talker" or (m["type"] == "ai" and m["status"] == "completed")
                ]
                answer = {
                    "id": answer_id,
                    "question_ids": generation["question_ids"],
                    "text": "",
                    "status": "generating",
                    "human_reviewed": True,
                    "created_at": now(),
                    "review_id": review["id"],
                    "wiki_sources": [d["path"] for d in self.wiki_for(session_id).list("talker")],
                    "revision": 1,
                    "tts_audio": {"id": uid(), "kind": "tts", "status": "queued"},
                }
                state["answers"].append(answer)
                state["messages"].append(
                    {
                        "id": answer_id,
                        "type": "ai",
                        "text": "",
                        "status": "generating",
                        "question_ids": generation["question_ids"],
                        "human_reviewed": True,
                        "revision": 1,
                        "created_at": answer["created_at"],
                    }
                )
                self.publish(state, "answer.started")
            full_text = ""
            last_emit = 0.0
            async for chunk in self.agent.answer(
                questions,
                context,
                history,
                review["hint"],
                state.get("language", "en-US"),
                source_texts=source_texts,
            ):
                full_text = "" if chunk.reset else full_text + chunk.text
                if chunk.reset or time.monotonic() - last_emit > 0.18:
                    if not await self.write_answer(
                        session_id, generation_id, answer_id, fence, full_text
                    ):
                        return
                    last_emit = time.monotonic()
            if not full_text.strip():
                raise RuntimeError("empty answer")
            async with self.locks[session_id]:
                state = self.get(session_id)
                generation = next(g for g in state["generations"] if g["id"] == generation_id)
                if generation["fence_token"] != fence:
                    return
                answer = next(a for a in state["answers"] if a["id"] == answer_id)
                answer.update(text=full_text, status="pending_confirmation")
                message = next(m for m in state["messages"] if m["id"] == answer_id)
                message.update(text=full_text, status="pending_confirmation")
                generation["status"] = "awaiting_confirmation"
                for question in state["questions"]:
                    if question["id"] in generation["question_ids"]:
                        question.update(
                            status="pending_confirmation", version=question["version"] + 1
                        )
                self.publish(state, "answer.draft_ready")
        except Exception as exc:
            async with self.locks[session_id]:
                state = self.get(session_id)
                generation = next(g for g in state["generations"] if g["id"] == generation_id)
                if generation["fence_token"] != fence:
                    return
                generation["status"] = "failed"
                for item in state["answers"] + state["messages"]:
                    if item["id"] == answer_id:
                        item.update(
                            status="failed",
                            error=str(exc)
                            if isinstance(exc, ModelsUnavailable)
                            else "生成失败，请稍后重试。",
                        )
                for question in state["questions"]:
                    if question["id"] in generation["question_ids"]:
                        question["status"] = "failed"
                self.publish(state, "answer.failed")

    @staticmethod
    def cancel_draft(state: dict[str, Any], generation: dict[str, Any]) -> None:
        generation["fence_token"] += 1
        generation["status"] = "cancelled"
        draft_ids = {
            a["id"]
            for a in state["answers"]
            if a.get("review_id") == generation["review_id"] and a["status"] != "completed"
        }
        for item in state["answers"] + state["messages"]:
            if item["id"] in draft_ids:
                item["status"] = "cancelled"
        for question in state["questions"]:
            if question["id"] in generation["question_ids"] and question["status"] not in [
                "answered",
                "rejected",
                "blocked",
            ]:
                question.update(
                    status="pending_review", version=question["version"] + 1, auto_suppressed=True
                )

    async def decide_answer(
        self, session_id: str, answer_id: str, action: str, revision: int | None = None
    ) -> None:
        self.require_chat(session_id)
        async with self.locks[session_id]:
            state = self.get(session_id)
            answer = next((a for a in state["answers"] if a["id"] == answer_id), None)
            if not answer:
                raise fail(404, "NOT_FOUND", "草稿不存在。")
            if action == "confirm" and answer["status"] == "completed":
                return
            if action == "cancel" and answer["status"] == "cancelled":
                return
            if answer["status"] not in [
                "generating",
                "pending_confirmation",
                "polishing",
                "failed",
            ]:
                raise fail(409, "INVALID_STATE", "这条草稿已经处理。")
            generation = next(
                g for g in state["generations"] if g["review_id"] == answer["review_id"]
            )
            if revision is not None and revision != answer.get("revision", 1):
                raise fail(409, "DRAFT_CHANGED", "草稿已更新，请阅读最新内容后再确认。")
            if action == "cancel":
                self.cancel_draft(state, generation)
                self.publish(state, "answer.cancelled")
                return
            if (
                answer["status"] != "pending_confirmation"
                or generation["status"] != "awaiting_confirmation"
            ):
                raise fail(409, "DRAFT_NOT_READY", "草稿还未生成完成，请稍后确认。")
            published_at = now()
            answer.update(status="completed", published_at=published_at)
            message = next(m for m in state["messages"] if m["id"] == answer_id)
            message.update(status="completed", published_at=published_at, audio=answer["tts_audio"])
            generation["status"] = "completed"
            questions = []
            for question in state["questions"]:
                if question["id"] in answer["question_ids"]:
                    question.update(
                        status="answered", answered_at=published_at, version=question["version"] + 1
                    )
                    questions.append(question["text"])
            self.wiki_for(state).sync_session(state)
            self.publish(state, "answer.published")
        self.spawn(self.synthesize(session_id, answer_id))
        self.spawn(self.consolidate(session_id, answer_id, questions, answer["text"]))

    async def set_ai_mode(self, session_id: str, enabled: bool) -> None:
        async with self.locks[session_id]:
            state = self.get(session_id)
            state["ai_mode"] = enabled
            state["automatic_paused"] = not enabled
            self.publish(state, "ai_mode.updated")
        if self.replies:
            await self.replies.set_paused(session_id, not enabled)
            return
        if enabled:
            self.spawn(self.prepare_automatically(session_id))

    async def prepare_automatically(self, session_id: str) -> None:
        state = self.get(session_id)
        if state.get("interaction_mode") == "realtime_call":
            return
        questions = sorted(state["questions"], key=lambda q: q["created_at"])
        for item in questions:
            state = self.get(session_id)
            if not state.get("ai_mode") or state["status"] != "active":
                return
            question = next(q for q in state["questions"] if q["id"] == item["id"])
            if question["status"] != "pending_review" or question.get("auto_suppressed"):
                continue
            try:
                await self.review(
                    session_id,
                    ReviewRequest(
                        items=[{"question_id": question["id"], "version": question["version"]}]
                    ),
                )
            except HTTPException:
                continue

    async def edit_draft(self, session_id: str, answer_id: str, text: str, revision: int) -> None:
        text = text.strip()
        if not text or re.search(r"[\u3400-\u9fff]", text):
            raise fail(400, "ENGLISH_REQUIRED", "草稿请使用英文，内容不能为空。")
        async with self.locks[session_id]:
            state = self.get(session_id)
            answer = next((a for a in state["answers"] if a["id"] == answer_id), None)
            if (
                not answer
                or answer["status"] != "pending_confirmation"
                or answer.get("revision", 1) != revision
            ):
                raise fail(409, "DRAFT_CHANGED", "草稿已更新或已发送，请重新打开后编辑。")
            answer.update(text=text, revision=revision + 1)
            message = next(m for m in state["messages"] if m["id"] == answer_id)
            message.update(text=text, revision=revision + 1)
            message.pop("error", None)
            self.publish(state, "answer.edited")

    async def polish_draft(self, session_id: str, answer_id: str, hint: str, revision: int) -> None:
        async with self.locks[session_id]:
            state = self.get(session_id)
            answer = next((a for a in state["answers"] if a["id"] == answer_id), None)
            if (
                not answer
                or answer["status"] != "pending_confirmation"
                or answer.get("revision", 1) != revision
            ):
                raise fail(409, "DRAFT_CHANGED", "草稿已更新或已发送，请重新打开后润色。")
            text = answer["text"]
            answer["status"] = "polishing"
            next(m for m in state["messages"] if m["id"] == answer_id)["status"] = "polishing"
            self.publish(state, "answer.polishing")
        try:
            result = await self.agent.polish(
                text, hint, context=self.wiki_for(session_id).bundle("talker", session_id)
            )
        except Exception as exc:
            async with self.locks[session_id]:
                state = self.get(session_id)
                answer = next(a for a in state["answers"] if a["id"] == answer_id)
                if answer["status"] == "polishing":
                    answer["status"] = "pending_confirmation"
                    message = next(m for m in state["messages"] if m["id"] == answer_id)
                    message.update(
                        status="pending_confirmation",
                        error=str(exc)
                        if isinstance(exc, ModelsUnavailable)
                        else "润色失败，原草稿已保留。",
                    )
                    self.publish(state, "answer.polish_failed")
            raise fail(
                502,
                "POLISH_FAILED",
                str(exc) if isinstance(exc, ModelsUnavailable) else "润色失败，原草稿已保留。",
            ) from None
        async with self.locks[session_id]:
            state = self.get(session_id)
            answer = next(a for a in state["answers"] if a["id"] == answer_id)
            if answer["status"] != "polishing":
                return
            answer.update(text=result, status="pending_confirmation", revision=revision + 1)
            message = next(m for m in state["messages"] if m["id"] == answer_id)
            message.update(text=result, status="pending_confirmation", revision=revision + 1)
            message.pop("error", None)
            self.publish(state, "answer.polished")

    async def clear_wiki(self, confirmation: str, talker_id: str | None = None) -> None:
        if confirmation != "我已确认删除所有的Wiki信息":
            raise fail(400, "CONFIRMATION_REQUIRED", "请输入：我已确认删除所有的Wiki信息")
        owner = self.talker(talker_id)["id"]
        states = [s for s in self.store.sessions() if s["talker_id"] == owner]
        self.wiki_for({"talker_id": owner}).clear_all()
        self.store.clear_guidance(talker_id=owner)
        self.guidance.epoch += 1
        self.guidance.cache.clear()
        for state in states:
            async with self.locks[state["id"]]:
                self.publish(self.get(state["id"]), "wiki.cleared")

    async def write_answer(
        self, session_id: str, generation_id: str, answer_id: str, fence: int, text: str
    ) -> bool:
        async with self.locks[session_id]:
            state = self.get(session_id)
            generation = next(g for g in state["generations"] if g["id"] == generation_id)
            if generation["fence_token"] != fence:
                return False
            for item in state["answers"] + state["messages"]:
                if item["id"] == answer_id:
                    item["text"] = text
            self.publish(state, "answer.delta")
            return True

    async def synthesize(self, session_id: str, answer_id: str) -> None:
        async with self.locks[session_id]:
            state = self.get(session_id)
            answer = next(a for a in state["answers"] if a["id"] == answer_id)
            answer["tts_audio"]["status"] = "synthesizing"
            self.publish(state, "audio.synthesizing")
        destination = self.audio_dir / (answer["tts_audio"]["id"] + ".wav")
        try:
            audio = await self.speech.synthesize(answer["text"], destination, "en-US")
        except Exception:
            from app.adapters.speech_text import spoken_text

            audio = {
                "status": "browser",
                "filename": None,
                "language": "en-US",
                "spoken_text": spoken_text(answer["text"]),
                "voice": "browser-en-US",
            }
        async with self.locks[session_id]:
            state = self.get(session_id)
            answer = next(a for a in state["answers"] if a["id"] == answer_id)
            answer["tts_audio"].update(audio)
            next(m for m in state["messages"] if m["id"] == answer_id)["audio"] = answer[
                "tts_audio"
            ]
            self.publish(
                state, "audio.ready", resource_id=answer_id, audio_id=answer["tts_audio"]["id"]
            )

    async def consolidate(
        self, session_id: str, answer_id: str, questions: list[str], answer_text: str
    ) -> None:
        wiki = self.wiki_for(session_id)
        content = "Questions: " + " / ".join(questions) + "\nAnswer: " + answer_text
        state = self.get(session_id)
        answer = next(a for a in state["answers"] if a["id"] == answer_id)
        review = next(r for r in state["reviews"] if r["id"] == answer["review_id"])
        if wiki.reset_at and answer.get("published_at", answer["created_at"]) <= wiki.reset_at:
            return
        await asyncio.gather(
            self.organize_wiki("talker", session_id, content),
            self.organize_wiki(
                "hinter", session_id, content + "\nMy private guidance: " + review["hint"]
            ),
        )
        async with self.locks[session_id]:
            self.publish(self.get(session_id), "wiki.updated")

    async def remember_completed_reply(self, session_id: str, answer_id: str) -> None:
        """The automatic answer path also feeds the existing user-owned LLM Wiki."""
        state = self.get(session_id)
        answer = next((a for a in state["answers"] if a["id"] == answer_id), None)
        if (not answer or answer.get("status") != "completed"
                or answer.get("refusal") or answer.get("notice") or answer.get("memory_status")
                or answer.get("reply_kind") in {"acknowledgement", "progress", "refusal"}
                or (answer.get("call_id") and answer.get("delivery") != "delivered")
                or not answer.get("text", "").strip()
                or answer["text"].strip() == "I don't know."):
            return
        if answer.get("parts_status") == "pending":
            return  # Consolidate the merged public answer, not a transient attempt.
        parts = answer.get("answer_parts", [])
        if parts and not any(part.get("status") in {"known", "partial"} for part in parts):
            return
        questions = [q for q in state["questions"] if q["id"] in answer.get("question_ids", [])]
        if any(next((a["id"] for a in reversed(state["answers"])
                     if q["id"] in a.get("question_ids", [])
                     and a.get("reply_kind") not in {"acknowledgement", "progress"}
                     and a.get("status") != "cancelled"), None) != answer_id for q in questions):
            return
        if not questions or any(q.get("decision") == "clarify" or q.get("reply_failure")
                                for q in questions):
            return
        wiki = self.wiki_for(state)
        if wiki.reset_at and answer.get("published_at", "") <= wiki.reset_at:
            return
        # Claim before the first await: completion and audio ACK can both arrive.
        answer["memory_status"] = "processing"
        self.publish(state, "memory.started")
        content = json.dumps({
            "source": {"session_id": session_id, "answer_id": answer_id,
                       "published_at": answer.get("published_at")},
            "caller_questions_not_facts": [q["text"] for q in questions],
            "assistant_public_answer": (
                "\n".join(f"{p['question']}: {p['answer']}" for p in parts)
                if parts else answer.get("delivered_text", answer["text"])
            ),
            "evidence": [{
                **{k: q.get(k) for k in ("evidence_source", "evidence_status", "evidence_gap")},
                "public_sources": q.get("work", {}).get("sources", []),
            } for q in questions],
            "instruction": "Store useful supported conclusions with source, period, scope and "
                           "uncertainty. Supplied human figures are not independently verified. "
                           "Questions and assistant recommendations are not user preferences. "
                           "Do not retain acknowledgements, failures or unknown as facts.",
        }, ensure_ascii=False)
        await asyncio.gather(*(
            self.organize_wiki(user, session_id, content, guard_answer_id=answer_id)
            for user in ("talker", "hinter")
        ))
        current = self.get(session_id)
        saved = next((a for a in current["answers"] if a["id"] == answer_id), None)
        if saved and saved.get("status") == "completed":
            saved["memory_status"] = "processed"
            self.publish(current, "wiki.updated")

    async def organize_wiki(
        self, user: str, session_id: str, content: str, *, guard_answer_id: str | None = None
    ) -> None:
        wiki = self.wiki_for(session_id)
        epoch = wiki.reset_at
        async with self.wiki_locks[(self.get(session_id)["talker_id"], user)]:
            if epoch != wiki.reset_at:
                return
            revisions = {d["path"]: wiki.read(user, d["path"])["revision"] for d in wiki.list(user)}
            try:
                documents = await self.agent.consolidate(
                    user, wiki.bundle(user, session_id), content
                )
                if epoch != wiki.reset_at:
                    return
                for document in documents:
                    if guard_answer_id:
                        source = next((a for a in self.get(session_id)["answers"]
                                       if a["id"] == guard_answer_id), None)
                        if not source or source.get("status") != "completed":
                            return
                        fresh = self.get(session_id)
                        if any(next((a["id"] for a in reversed(fresh["answers"])
                                     if qid in a.get("question_ids", [])
                                     and a.get("reply_kind") not in {"acknowledgement", "progress"}
                                     and a.get("status") != "cancelled"), None) != guard_answer_id
                               for qid in source.get("question_ids", [])):
                            return
                    path, text = document.get("path", ""), document.get("content", "")
                    if path.startswith("sessions/") or not text:
                        continue
                    try:
                        wiki.write(user, path, text, revisions.get(path, wiki.revision("")))
                    except ValueError:
                        continue
                center = wiki.read(user, "context.md")
                content = center["content"]
                for document in documents:
                    path = document.get("path", "")
                    if path and path != "context.md" and not path.startswith("sessions/"):
                        if path not in content and wiki.path(user, path).exists():
                            content += f"\n- [{path.removesuffix('.md')}]({path})\n"
                if content != center["content"]:
                    wiki.write(user, "context.md", content, center["revision"])
            except ModelsUnavailable as exc:
                async with self.locks[session_id]:
                    state = self.get(session_id)
                    state["messages"].append(
                        {
                            "id": uid(),
                            "type": "hinter" if user == "hinter" else "screening",
                            "text": "Wiki 整理未完成：" + str(exc),
                            "status": "completed",
                            "created_at": now(),
                        }
                    )
                    self.publish(state, "wiki.failed")
                return
            except Exception:
                return

    async def remember_statement(self, session_id: str, text: str, created_at: str) -> None:
        wiki = self.wiki_for(session_id)
        if wiki.reset_at and created_at <= wiki.reset_at:
            return
        await asyncio.gather(
            *(
                self.organize_wiki(user, session_id, "New user information: " + text)
                for user in ["talker", "hinter"]
            )
        )
        async with self.locks[session_id]:
            self.publish(self.get(session_id), "wiki.updated")

    async def sample(self, session_id: str) -> None:
        wiki = self.wiki_for(session_id)
        async with self.locks[session_id]:
            state = self.get(session_id)
            if state["sample_loaded"]:
                return
            state["sample_loaded"] = True
            for user in ["talker", "hinter"]:
                document = wiki.read(user, "context.md")
                wiki.write(
                    user,
                    "context.md",
                    document["content"]
                    + "\n\n## 示例背景\n\n客户正在准备 11 月试点发布。发布前需要 P0/P1 事故演练；"
                    "团队跨时区协作，偏好量化、可执行的建议。\n",
                )
            self.publish(state, "wiki.updated")
        await self.submit(
            session_id,
            "We haven't rehearsed incident response. Is that a blocker for the November pilot? "
            "What should the drill include? Does the missing rehearsal mean we should delay?",
        )
        await self.submit(
            session_id, "Who should join the simulation? Can you give me measurable pass criteria?"
        )

    async def retry_message(self, session_id: str, message_id: str, text: str | None) -> None:
        async with self.locks[session_id]:
            state = self.get(session_id)
            message = next((m for m in state["messages"] if m["id"] == message_id), None)
            if not message or message["status"] not in ["intake_failed", "transcription_failed"]:
                raise fail(409, "INVALID_STATE", "当前消息无需重试。")
            if text:
                message["text"] = text.strip()
            message["status"] = "intake_pending" if message["text"] else "transcribing"
            message.pop("error", None)
            self.publish(state, "message.retry")
        self.spawn(
            self.run_intake(session_id, message_id)
            if message["text"]
            else self.run_transcription(session_id, message_id)
        )

    async def retry_answer(self, session_id: str, answer_id: str) -> None:
        if question_ids := card_question_ids(self.get(session_id), answer_id):
            for question_id in question_ids:
                await self.replies.correct(session_id, question_id)
            return
        answer = next((a for a in self.get(session_id)["answers"] if a["id"] == answer_id), None)
        if answer and answer.get("public_stream"):
            for qid in answer["question_ids"]:
                await self.replies.correct(session_id, qid)
            return
        async with self.locks[session_id]:
            state = self.get(session_id)
            answer = next((a for a in state["answers"] if a["id"] == answer_id), None)
            if not answer or answer["status"] != "failed":
                raise fail(409, "INVALID_STATE", "当前回答无需重试。")
            generation = next(
                g for g in state["generations"] if g["review_id"] == answer["review_id"]
            )
            generation["fence_token"] += 1
            generation["status"] = "running"
            state["answers"] = [a for a in state["answers"] if a["id"] != answer_id]
            state["messages"] = [m for m in state["messages"] if m["id"] != answer_id]
            for question in state["questions"]:
                if question["id"] in generation["question_ids"]:
                    question["status"] = "generating"
            self.publish(state, "answer.retry")
        self.spawn(self.generate(session_id, generation["id"], generation["fence_token"]))

    async def recover(self) -> None:
        for state in self.store.sessions():
            withdrawn = [m["id"] for m in state["messages"] if m["status"] == "withdrawn"]
            if withdrawn:
                await asyncio.to_thread(self.wiki_for(state).withdraw_messages, withdrawn)
        for state in self.store.sessions():
            state.setdefault("automatic_paused", False)
            state["response_flow_version"] = 2
            state["ai_mode"] = not state["automatic_paused"]
            for question in state["questions"]:
                if question["status"] in ["held", "expired"] and not question.get("decision"):
                    question["status"] = "legacy_pending"
            if state.get("call") and state["call"].get("status") != "ended":
                state["call"].update(
                    status="ended", ended_at=now(), error="服务已重启，请重新拨打。"
                )
                state["interaction_mode"] = "review_chat"
                for question in state["questions"]:
                    if question.get("call_id") and question["status"] == "generating":
                        question["status"] = "interrupted"
                for item in state["messages"] + state["answers"]:
                    if item.get("call_id") and item.get("delivery") == "speaking":
                        item["delivery"] = "interrupted"
            for answer in state["answers"]:
                if answer["status"] == "polishing":
                    answer["status"] = "pending_confirmation"
                    message = next(m for m in state["messages"] if m["id"] == answer["id"])
                    message.update(
                        status="pending_confirmation", error="润色被服务重启中断，原草稿已保留。"
                    )
            for message in state["messages"]:
                if message["status"] in ["intake_pending", "transcribing"]:
                    self.spawn(
                        self.run_intake(state["id"], message["id"])
                        if message["text"]
                        else self.run_transcription(state["id"], message["id"])
                    )
            for generation in state["generations"]:
                if generation["status"] == "running":
                    generation["fence_token"] += 1
                    state["answers"] = [
                        a for a in state["answers"] if a.get("review_id") != generation["review_id"]
                    ]
                    valid = {a["id"] for a in state["answers"]}
                    state["messages"] = [
                        m for m in state["messages"] if m["type"] != "ai" or m["id"] in valid
                    ]
                    self.spawn(
                        self.generate(state["id"], generation["id"], generation["fence_token"])
                    )
            self.store.save(state)
            if self.replies:
                self.spawn(self.guidance.project(state["id"]))
                for item in state["messages"] + state["answers"]:
                    if item.get("public_stream") and item["status"] == "streaming":
                        item.update(
                            status="interrupted",
                            delivery="interrupted",
                            text_done=True,
                            error="服务重启中断了回复，已发内容保留。",
                        )
                self.store.save(state)
            if not self.replies and state.get("ai_mode") and state["status"] == "active":
                self.spawn(self.prepare_automatically(state["id"]))
            for answer in state["answers"]:
                if answer["status"] == "completed" and answer["tts_audio"]["status"] in [
                    "queued",
                    "synthesizing",
                ]:
                    self.spawn(self.synthesize(state["id"], answer["id"]))
