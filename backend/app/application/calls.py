"""Phone-style conversations on the existing session. Media is continuous PCM over WS."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import wave
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from fastapi import WebSocket

from app.adapters.realtime_speech import RealtimeSpeech, SileroStream
from app.adapters.turn_detection import EndOfTurnAdapter
from app.agent.call_reasoner import CallReasoner
from app.agent.call_search import CallSearch
from app.agent.transcript_refiner import TranscriptRefiner, disagreement, readable
from app.application.assistance import is_short_value
from app.application.service import SessionService, fail, now, uid
from app.application.turns import TurnAssembler
from app.application.voice_control import (
    BACKCHANNELS,
    MediaSequence,
    RecognitionContext,
    TurnController,
    incomplete,
    interruption_decision,
    words,
)

logger = logging.getLogger(__name__)


@dataclass
class LiveCall:
    session_id: str
    call_id: str
    socket: WebSocket | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    work: asyncio.Task | None = None
    stream: Any = None
    last_seen: float = field(default_factory=time.monotonic)
    epoch: int = 1
    paused: bool = False
    constraints_revision: int = 0
    turn: TurnAssembler = field(default_factory=TurnAssembler)
    vad: Any = None
    pending_pcm: bytearray = field(default_factory=bytearray)
    input_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refine_work: asyncio.Task | None = None
    asr_work: asyncio.Task | None = None
    asr_key: tuple = ()
    input_status: str = "idle"
    intake_work: asyncio.Task | None = None
    output_work: asyncio.Task | None = None
    output_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    responses: dict[str, asyncio.Task] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    preparing: set[str] = field(default_factory=set)
    thinking_started: float = 0
    thinking_deadline: float = 0
    audio_damaged: bool = False
    damaged_quiet: int = 0
    was_backchannel: bool = False
    input_during_output: bool = False
    protocol: int = 2
    controller: TurnController = field(default_factory=TurnController)
    endpoint_work: asyncio.Task | None = None
    media_sequence: MediaSequence = field(default_factory=MediaSequence)
    recognition: RecognitionContext = field(default_factory=RecognitionContext)
    turn_glossary: tuple[str, ...] = ()
    glossary_version: int = 1
    warned: set[str] = field(default_factory=set)
    ducked: tuple = ()
    duck_work: asyncio.Task | None = None
    endpoint_reason: str = ""
    endpoint_clarify: bool = False
    last_voice_received_at: float = 0


class CallService:
    def __init__(self, service: SessionService) -> None:
        self.service = service
        self.speech = RealtimeSpeech()
        self.endpoint = EndOfTurnAdapter(self.speech.root)
        self.reasoner = CallReasoner(service.agent.router)
        self.refiner = TranscriptRefiner(service.agent.router)
        self.search = CallSearch(
            self.reasoner.normalize_public_query, self.reasoner.assess_public_evidence
        )
        self.live: dict[str, LiveCall] = {}
        self.context_cache: dict[tuple, tuple[str, str, str]] = {}

    def background(self, coroutine: Any) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self.service.tasks.add(task)
        task.add_done_callback(self.service.tasks.discard)
        return task

    def current(self, session_id: str, call_id: str | None = None) -> tuple[dict, dict]:
        state = self.service.get(session_id)
        call = state.get("call")
        if not call or call["status"] == "ended" or (call_id and call["id"] != call_id):
            raise fail(409, "CALL_ENDED", "通话已结束，请重新拨打。")
        return state, call

    def commit(self, state: dict, event: str = "call.updated") -> None:
        call = state.get("call")
        if call:
            live = self.live.get(call["id"])
            input_busy = live and live.turn.state in ["collecting", "waiting_pause"]
            call["activity_state"] = (
                "thinking" if call["status"] == "thinking" and not input_busy else "listening"
            )
            call["pending_count"] = sum(
                q.get("call_id") == call["id"] and q["status"] in ["held", "expired"]
                for q in state["questions"]
            )
            call["queued_count"] = sum(
                q.get("call_id") == call["id"]
                and q["status"] == "generating"
                and q["id"] != call.get("active_question_id")
                for q in state["questions"]
            )
        # Public WS sees a projected snapshot, never a raw private hint event.
        self.service.publish(state, event)

    @staticmethod
    def begin_thinking(live: LiveCall, call: dict, started: float = 0) -> float:
        live.thinking_started = started or time.monotonic()
        live.thinking_deadline = live.thinking_started + 10
        call.update(status="thinking", thinking_started_at=now(), thinking_budget_ms=10000)
        return live.thinking_deadline

    async def send(self, live: LiveCall, event: str, **data: Any) -> None:
        if live.socket:
            try:
                async with live.send_lock:
                    await live.socket.send_json({"type": event, **data})
            except Exception:
                pass

    async def start(self, session_id: str, protocol: int = 2) -> dict:
        state = self.service.get(session_id)
        if state.get("call", {}).get("status") not in [None, "ended"]:
            raise fail(409, "ACTIVE_CALL_EXISTS", "已有活动通话，请先挂断。")
        if self.service.replies:
            await self.service.replies.cancel_outputs(session_id)
        if not self.speech.available():
            raise fail(
                503, "MEDIA_UNAVAILABLE", "本地通话模型尚未准备，请运行 scripts/prepare-voice.py。"
            )
        if protocol not in (2, 3):
            raise fail(400, "CALL_PROTOCOL", "不支持的通话音频协议。")
        await self.speech.prepare()
        if protocol == 3:
            await asyncio.to_thread(self.endpoint.prepare)
        # Recheck after model loading: two tabs must not become simultaneous owners.
        state = self.service.get(session_id)
        if state.get("call", {}).get("status") not in [None, "ended"]:
            raise fail(409, "ACTIVE_CALL_EXISTS", "已有活动通话。")
        if state.get("call"):
            state.setdefault("call_history", []).append(state["call"])
        call: dict[str, Any] = {
            "id": uid(),
            "status": "connecting",
            "started_at": now(),
            "ended_at": None,
            "epoch": 1,
            "paused": state.get("automatic_paused", False),
            "muted": False,
            "constraints_revision": 0,
            "constraints": [],
            "alerts": [],
            "checks": [],
            "error": "",
            "response_id": None,
            "transport": "pcm-websocket",
            "asr": "sherpa-onnx",
            "tts": "Kokoro",
            "last_latency_ms": None,
            "turn_protocol": protocol,
            "output_protocol": 4,
            "endpoint_mode": "adaptive"
            if protocol == 3 and self.endpoint.session is not None
            else "fixed_silence",
            "final_asr": self.speech.final_name,
            "final_asr_ready": self.speech.final_model is not None,
        }
        state.update(call=call, interaction_mode="realtime_call")
        self.commit(state, "call.started")
        live = LiveCall(
            session_id,
            call["id"],
            stream=self.speech.recognizer.create_stream(),
            vad=SileroStream(self.speech.vad_session),
            protocol=protocol,
            turn=TurnAssembler(adaptive=protocol == 3),
            paused=call["paused"],
        )
        self.live[call["id"]] = live
        self.service.spawn(self.watch(live))
        return {
            "call_id": call["id"],
            "sample_rate": 16000,
            "transport": "pcm-websocket",
            "turn_protocol": protocol,
            "output_protocol": 4,
        }

    async def watch(self, live: LiveCall) -> None:
        while live.call_id in self.live:
            await asyncio.sleep(3)
            if time.monotonic() - live.last_seen > 10:
                await self.end(live.session_id, live.call_id, "连接已中断，请重新拨打。")
                return

    async def attach(self, session_id: str, call_id: str, websocket: WebSocket) -> LiveCall:
        state, call = self.current(session_id, call_id)
        live = self.live.get(call_id)
        if not live or live.socket:
            raise fail(409, "CALL_OWNER_EXISTS", "通话连接不可复用，请重新拨打。")
        live.socket = websocket
        live.last_seen = time.monotonic()
        live.media_sequence.reset()
        call["status"] = "listening"
        self.commit(state)
        await self.send(
            live,
            "ready",
            epoch=call["epoch"],
            sample_rate=16000,
            turn_protocol=live.protocol,
            frame_samples=512,
            output_protocol=4,
            endpoint_mode=call.get("endpoint_mode", "fixed_silence"),
        )
        if live.protocol == 3 and self.endpoint.session is None:
            await self.warn(
                live, "endpoint_unavailable", "自然判停暂不可用，已切换为约三秒静音交接。"
            )
        return live

    async def interrupt(self, session_id: str, call_id: str, reason: str = "interrupted") -> int:
        state, call = self.current(session_id, call_id)
        live = self.live.get(call_id)
        was_thinking = call["status"] == "thinking"
        cancelled_preparations = []
        call["epoch"] += 1
        call["status"] = "listening"
        response_id = call.get("response_id")
        for item in state["messages"] + state["answers"]:
            if item["id"] == response_id and item.get("delivery") == "speaking":
                item["delivery"] = "interrupted"
        for q in state["questions"]:
            # Pending supplements belong to their questions, not the interrupted output.
            cancel_thinking = bool(
                live
                and was_thinking
                and reason == "caller_confirmed"
                and q["id"] in live.preparing
                and not q.get("human_reviewed")
                and call.get("active_message_id")
                and call["active_message_id"] in q.get("source_message_ids", [])
            )
            if (
                q.get("call_id") == call_id
                and q["status"] == "generating"
                and (q["id"] == call.get("active_question_id") or cancel_thinking)
            ):
                q["status"] = "interrupted"
                cancelled_preparations.append(live.responses.get(q["id"]))
        self.commit(state)
        if live:
            live.epoch = call["epoch"]
            for job in [live.intake_work, live.output_work, *cancelled_preparations]:
                if job and job is not asyncio.current_task():
                    job.cancel()
            live.changed.set()
            await self.send(live, "stop", epoch=call["epoch"], reason=reason)
        return call["epoch"]

    async def end(self, session_id: str, call_id: str, reason: str = "通话已结束") -> None:
        try:
            await self.interrupt(session_id, call_id, "hangup")
            state, call = self.current(session_id, call_id)
        except Exception:
            return
        call.update(status="ended", ended_at=now(), error=reason)
        state["interaction_mode"] = "review_chat"
        self.commit(state, "call.ended")
        live = self.live.pop(call_id, None)
        if live:
            if live.refine_work:
                live.refine_work.cancel()
            if live.endpoint_work:
                live.endpoint_work.cancel()
            if live.duck_work:
                live.duck_work.cancel()
            for task in live.responses.values():
                task.cancel()
            live.changed.set()
            await self.send(live, "ended", reason=reason)
            if live.socket:
                try:
                    await live.socket.close()
                except Exception:
                    pass

    def valid(self, live: LiveCall, epoch: int, allow_paused: bool = False) -> bool:
        return (
            self.live.get(live.call_id) is live
            and live.epoch == epoch
            and (allow_paused or not live.paused)
        )

    def context(self, session_id: str, question_id: str | None = None) -> tuple[str, str, str]:
        return self.service.guidance.snapshot(session_id, question_id)

    @staticmethod
    def customer_path(state: dict) -> str:
        key = hashlib.sha256(state["customer_id"].encode()).hexdigest()[:16]
        return f"constraints/customers/{key}.md"

    async def project_constraints(self, state: dict) -> None:
        call = state["call"]
        text = f"# 通话核心约束 {call['id']}\n\n"
        text += "\n\n".join(
            f"## {c['created_at']} · {c['scope']} · "
            f"{'已撤销' if c.get('revoked') else '有效'}\n\n{c['text']}"
            for c in call["constraints"]
        )
        try:
            await asyncio.to_thread(
                self.service.wiki_for(state).write,
                "hinter",
                f"constraints/calls/{call['id']}.md",
                text,
            )
        except Exception:
            logger.exception("call_constraint_projection_failed call=%s", call["id"])
            current = self.service.get(state["id"])
            current["call"]["error"] = "提示已持久保存，Wiki 文件同步失败，稍后重试。"
            self.commit(current)

    def schedule_retrospection(self, session_id: str, hint: str, hint_id: str) -> str:
        state = self.service.get(session_id)
        recent = state["questions"][-5:]
        questions = [
            {
                "id": q["id"],
                "text": q["text"],
                "answers": [
                    a["text"]
                    for a in state["answers"]
                    if q["id"] in a["question_ids"]
                    and a["status"] in ["completed", "streaming", "interrupted"]
                    and not a.get("notice")
                ],
            }
            for q in recent
        ]
        check = {
            "id": uid(),
            "hint_id": hint_id,
            "hint": hint,
            "questions": questions,
            "status": "pending",
            "checked_count": len(recent),
            "withdrawn_count": 0,
            "created_at": now(),
            "error": "",
        }
        state.setdefault("retraction_checks", []).append(check)
        self.commit(state, "retraction.updated")
        self.service.spawn(self.run_retrospection(session_id, check["id"]))
        return check["id"]

    async def run_retrospection(self, session_id: str, check_id: str) -> None:
        state = self.service.get(session_id)
        check = next(c for c in state.get("retraction_checks", []) if c["id"] == check_id)
        try:
            result = (
                await self.reasoner.retrospect(check["hint"], check["questions"])
                if (check["questions"])
                else {"is_prohibition": False, "question_ids": [], "reason": "暂无历史问题"}
            )
            count = 0
            if result["is_prohibition"] and result["question_ids"]:
                count = await self.withdraw_questions(
                    session_id,
                    result["question_ids"],
                    check["hint_id"],
                    str(result.get("reason", "命中封禁原则")),
                )
            state = self.service.get(session_id)
            check = next(c for c in state["retraction_checks"] if c["id"] == check_id)
            check.update(
                status="completed",
                withdrawn_count=count,
                reason=str(result.get("reason", "")),
                completed_at=now(),
                error="",
            )
        except Exception:
            logger.exception("hint_retrospection_failed check=%s", check_id)
            state = self.service.get(session_id)
            check = next(c for c in state["retraction_checks"] if c["id"] == check_id)
            check.update(status="failed", error="最近5题回查未完成，请重试或直接撤回相关回答。")
        self.commit(state, "retraction.updated")

    async def retry_retrospection(self, session_id: str, check_id: str) -> dict:
        state = self.service.get(session_id)
        check = next((c for c in state.get("retraction_checks", []) if c["id"] == check_id), None)
        if not check or check["status"] != "failed":
            raise fail(409, "CHECK_NOT_RETRYABLE", "该回查无需重试。")
        check.update(status="pending", error="")
        self.commit(state, "retraction.updated")
        self.service.spawn(self.run_retrospection(session_id, check_id))
        return {"status": "pending"}

    async def withdraw_questions(
        self,
        session_id: str,
        question_ids: list[str],
        hint_id: str,
        reason: str,
    ) -> int:
        """Hide all responses for the captured questions, including replies published mid-check."""
        state = self.service.get(session_id)
        matched = set(question_ids)
        return await self.withdraw_responses(
            session_id,
            [a["id"] for a in state["answers"] if matched.intersection(a["question_ids"])],
            hint_id,
            reason,
            blocked_question_ids=question_ids,
        )

    async def withdraw_responses(
        self,
        session_id: str,
        response_ids: list[str],
        hint_id: str,
        reason: str,
        *,
        blocked_question_ids: list[str] | None = None,
    ) -> int:
        state = self.service.get(session_id)
        selected = set(response_ids)
        matched = set(blocked_question_ids or [])
        answers = [
            a
            for a in state["answers"]
            if a["id"] in selected
            and a["status"] in ["completed", "withdrawn", "streaming", "interrupted"]
        ]
        answer_ids = {a["id"] for a in answers}
        affected = {qid for a in answers for qid in a["question_ids"]}
        changed = [a["id"] for a in answers if a["status"] != "withdrawn"]
        for item in state["answers"] + state["messages"]:
            if item["id"] in answer_ids:
                item.update(
                    status="withdrawn",
                    delivery="withdrawn",
                    retracted_at=now(),
                    retraction_hint_id=hint_id,
                    retraction_reason=reason,
                )
        for question in state["questions"]:
            if question["id"] in matched:
                question.update(
                    status="withdrawn" if question["id"] in affected else "blocked",
                    version=question["version"] + 1,
                )
            elif (
                question["id"] in affected
                and question["status"] in ["answered", "interrupted"]
                and not any(
                    a["status"] == "completed"
                    and not a.get("notice")
                    and question["id"] in a["question_ids"]
                    for a in state["answers"]
                )
            ):
                question.update(status="withdrawn", version=question["version"] + 1)
        for generation in state["generations"]:
            if matched.intersection(generation["question_ids"]):
                generation["fence_token"] += 1
                generation["status"] = "cancelled"
        # Reads are sanitized immediately; disk tombstones also fence older background writers.
        self.service.wiki_for(session_id).withdrawn.update(answer_ids)
        self.service.wiki_version += 1
        self.context_cache.clear()
        self.commit(state, "answers.withdrawn")
        if self.service.replies:
            for answer_id in answer_ids:
                await self.service.replies.cancel_outputs(session_id, answer_id)
            self.service.replies.broadcast(
                session_id,
                {
                    "type": "response.withdrawn",
                    "response_ids": sorted(answer_ids),
                },
            )
        call = state.get("call", {})
        live = self.live.get(call.get("id", ""))
        if live:
            if (
                call.get("active_question_id") in matched
                or (call["status"] == "speaking" and call.get("response_id") in answer_ids)
            ) and call["status"] in ["thinking", "speaking"]:
                await self.interrupt(session_id, live.call_id, "withdrawn")
            await self.send(live, "retracted", response_ids=sorted(answer_ids))
        await asyncio.to_thread(
            self.service.wiki_for(session_id).withdraw_messages, sorted(answer_ids)
        )
        return len(changed)

    async def withdraw_answer(self, session_id: str, answer_id: str) -> dict:
        from app.application.answer_cards import card_question_ids

        state = self.service.get(session_id)
        if question_ids := card_question_ids(state, answer_id):
            ids = [a["id"] for a in state["answers"]
                   if set(question_ids).intersection(a.get("question_ids", []))]
            count = await self.withdraw_responses(
                session_id, ids, "manual", "Hinter 撤回汇总回答",
                blocked_question_ids=question_ids,
            )
            return {"status": "withdrawn", "withdrawn_count": count}
        answer = next((a for a in state["answers"] if a["id"] == answer_id), None)
        if not answer:
            raise fail(404, "NOT_FOUND", "回答不存在。")
        if answer["status"] not in ["completed", "withdrawn", "streaming", "interrupted"]:
            raise fail(409, "ANSWER_NOT_PUBLISHED", "这条回答尚未发送，请使用草稿的取消操作。")
        count = await self.withdraw_responses(
            session_id,
            [answer_id],
            "manual",
            "Hinter 直接撤回并隐藏",
        )
        return {"status": "withdrawn", "withdrawn_count": count}

    async def intervene(self, session_id: str, call_id: str, request: dict) -> dict:
        state, call = self.current(session_id, call_id)
        action = request.get("action", "correct_now")
        live = self.live.get(call_id)
        if action == "resume_auto":
            call["paused"] = False
            state["automatic_paused"] = False
            self.commit(state)
            if live:
                live.paused = False
                live.changed.set()
            if self.service.replies:
                await self.service.replies.set_paused(session_id, False)
            return {"status": "effective"}
        if action not in ["correct_now", "add_for_next", "block_target", "pause_auto"]:
            raise fail(400, "INVALID_ACTION", "不支持的通话操作。")
        future_only = action == "add_for_next"
        target = None if future_only else (
            request.get("question_id") or call.get("active_question_id")
        )
        pending_message = call.get("active_message_id")
        if action in ["pause_auto", "block_target"]:
            await self.interrupt(session_id, call_id, "supervisor")
            state, call = self.current(session_id, call_id)
            if action == "pause_auto" or not target:
                call["paused"] = True
                state["automatic_paused"] = True
                if live:
                    live.paused = True
            if action == "block_target" and target:
                await self.withdraw_questions(session_id, [target], "manual", "Hinter 禁止回答")
                state, call = self.current(session_id, call_id)
            self.commit(state)
        text = str(request.get("text", "")).strip()
        if not text:
            if action in ["pause_auto", "block_target"]:
                return {"status": "effective", "target": target}
            raise fail(400, "EMPTY_HINT", "请输入要补充的信息。")
        result = await self.service.guidance.submit(
            session_id,
            {
                **request,
                "question_id": None if future_only else request.get("question_id")
                or (
                    target
                    if action == "correct_now"
                    or (
                        is_short_value(text)
                        and not any(q["status"] == "held" for q in state["questions"])
                    )
                    else None
                ),
                "context_question_id": target,
                "effective_from": "next_turn" if future_only else "now",
            },
            trigger_reply=not future_only and not (
                action == "correct_now" and live and not live.paused
            ),
        )
        target = result.get("target") or target
        if action == "correct_now" and live and not live.paused:
            if target and self.service.replies:
                await self.service.replies.correct(session_id, target, live)
            elif pending_message and (not live.intake_work or live.intake_work.done()):
                live.intake_work = self.background(self.process(live, pending_message, live.epoch))
        return result

    async def review(self, session_id: str, call_id: str, request: dict) -> dict:
        state, call = self.current(session_id, call_id)
        q = next(
            (
                q
                for q in state["questions"]
                if q["id"] == request.get("question_id") and q.get("call_id") == call_id
            ),
            None,
        )
        if not q or q["status"] not in ["held", "expired"]:
            raise fail(409, "VERSION_CONFLICT", "该问题已处理，请查看最新状态。")
        if request.get("version") != q["version"]:
            raise fail(409, "VERSION_CONFLICT", "问题已更新，请重新操作。")
        decision = request.get("decision")
        if decision not in ["approve", "auto_answer", "reject"]:
            raise fail(400, "INVALID_DECISION", "请选择 AI 直接回复、按提示回答或跳过。")
        if q.get("decision") and self.service.replies:
            return await self.service.replies.resolve(
                session_id,
                q["id"],
                {
                    "decision": decision,
                    "version": request.get("version"),
                    "hint": request.get("hint", "") if decision == "approve" else "",
                },
            )
        if decision == "reject":
            q.update(status="rejected", version=q["version"] + 1)
            for alert in call["alerts"]:
                if alert["question_id"] == q["id"]:
                    alert["status"] = "resolved"
            self.commit(state)
            return {"ok": True}
        live = self.live.get(call_id)
        if not live or not live.socket:
            raise fail(409, "CALL_DISCONNECTED", "通话连接已断开，请等待重新接通后重试。")
        if call["paused"]:
            raise fail(409, "CALL_PAUSED", "自动回答已暂停，请先恢复回答再处理此问题。")
        hint = str(request.get("hint", "")).strip() if decision == "approve" else ""
        if q.get("category") == "data_lookup" and decision == "approve" and not hint:
            raise fail(400, "DATA_REQUIRED", "请提供已核实的数据或说明，AI 不会自行查询或猜测。")
        if hint:
            qid = q["id"]
            # Approval supplies data for this question; it is not the UI's
            # future-only action and must not be routed through that action.
            await self.service.guidance.submit(
                session_id, {"text": hint, "question_id": q["id"], "effective_from": "now"},
                trigger_reply=False,
            )
            state, call = self.current(session_id, call_id)
            q = next(item for item in state["questions"] if item["id"] == qid)
        q["human_reviewed"] = True
        q["version"] += 1
        q["status"] = "generating"
        for alert in call["alerts"]:
            if alert["question_id"] == q["id"]:
                alert["status"] = "resolved"
        self.commit(state)
        self.queue_response(live, q["id"], supplemental=True)
        return {"ok": True}

    def queue_response(self, live: LiveCall, qid: str, **options: Any) -> asyncio.Task:
        existing = live.responses.get(qid)
        if existing and not existing.done() and not existing.cancelling():
            return existing
        previous = live.intake_work or live.work

        async def run() -> None:
            if previous and previous is not asyncio.current_task() and not previous.done():
                # Cancelling an old intake must never cancel an approved later supplement.
                await asyncio.gather(asyncio.shield(previous), return_exceptions=True)
            if self.live.get(live.call_id) is live:
                await self.respond(live, qid, **options)

        task = self.background(run())
        live.responses[qid] = task
        live.work = task  # Last scheduled operation, retained for observers/legacy callers.

        def finished(_: asyncio.Task) -> None:
            if live.responses.get(qid) is task:
                live.responses.pop(qid, None)
            live.changed.set()

        task.add_done_callback(finished)
        return task

    def settle(self, live: LiveCall) -> None:
        if self.live.get(live.call_id) is not live:
            return
        state, call = self.current(live.session_id, live.call_id)
        if call["status"] not in ["speaking", "reconnecting"]:
            working = any(
                task and not task.done() and not task.cancelling()
                for task in [live.intake_work, live.refine_work]
            ) or bool(live.preparing)
            call["status"] = "thinking" if working and not live.turn.id else "listening"
        self.commit(state)
        live.changed.set()

    async def receive_audio(self, live: LiveCall, pcm: bytes) -> None:
        if live.protocol == 3:
            try:
                pcm, gap = live.media_sequence.unpack(pcm)
            except ValueError:
                await self.cancel_input(live, "audio_gap")
                live.audio_damaged = True
                live.damaged_quiet = 0
                live.media_sequence.reset()
                await self.warn(live, "audio_gap", "音频帧不完整，这段话没有提交，请重说。")
                return
            if gap:
                await self.cancel_input(live, "audio_gap")
                live.audio_damaged = True
                live.damaged_quiet = 0
                await self.warn(live, "audio_gap", "音频有缺失，这段话没有提交，请重说。")
                return
        if len(pcm) > 32768 or len(pcm) % 2:
            return
        live.last_seen = time.monotonic()
        _, call = self.current(live.session_id, live.call_id)
        if call["muted"]:
            return
        if live.turn.last_audio_at and time.monotonic() - live.turn.last_audio_at > 1:
            await self.cancel_input(live, "audio_gap")
            if live.protocol == 3:
                live.audio_damaged = True
                live.damaged_quiet = 0
        live.turn.last_audio_at = time.monotonic()
        async with live.input_lock:
            live.pending_pcm.extend(pcm)
            while len(live.pending_pcm) >= 1024:
                frame = bytes(live.pending_pcm[:1024])
                del live.pending_pcm[:1024]
                if live.audio_damaged:
                    samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768
                    probability = await asyncio.to_thread(live.vad.accept, samples)
                    live.damaged_quiet = live.damaged_quiet + 512 if probability < 0.35 else 0
                    if live.damaged_quiet >= 48000:
                        live.audio_damaged = False
                        live.vad.reset()
                        await self.input_event(
                            live, "input.recovered", message="音频已恢复，可以重新说话。"
                        )
                    continue
                decoding = asyncio.create_task(asyncio.to_thread(self.ingest_frame, live, frame))
                try:
                    signals, changed = await asyncio.shield(decoding)
                except asyncio.CancelledError:
                    await decoding  # Native state must finish before another receiver can use it.
                    raise
                if self.live.get(live.call_id) is not live:
                    return
                turn = live.turn
                if turn.id and turn.last_voiced == turn.sample:
                    live.last_voice_received_at = time.monotonic()
                if signals["started"] or signals["resumed"]:
                    if signals["started"]:
                        live.was_backchannel = False
                        _, starting_call = self.current(live.session_id, live.call_id)
                        live.input_during_output = starting_call["status"] == "speaking"
                        live.turn_glossary = live.recognition.terms
                        live.glossary_version = live.recognition.version
                    elif signals.get("resumed_pause_ms"):
                        live.controller.pause_history.append(signals["resumed_pause_ms"])
                        del live.controller.pause_history[:-8]
                    live.controller.observe(turn.key)
                    live.endpoint_reason = ""
                    live.endpoint_clarify = False
                    if live.refine_work:
                        live.refine_work.cancel()
                    await self.input_event(live, "input.speech_started")
                    state, call = self.current(live.session_id, live.call_id)
                    if (
                        call["status"] == "thinking"
                        and not live.output_work
                        and not live.preparing
                        and not (live.intake_work and not live.intake_work.done())
                    ):
                        # Resuming this still-uncommitted input cancels its refinement,
                        # not an AI answer. Do not report a spurious output interruption.
                        call["status"] = "listening"
                    self.commit(state)
                    live.changed.set()
                if signals.get("cancelled"):
                    await self.input_event(live, "input.cancelled", reason_code="input_limit")
                    continue
                if signals["limit"]:
                    if live.refine_work:
                        live.refine_work.cancel()
                    await self.input_event(
                        live,
                        "input.limit_reached",
                        reason_code="input_limit",
                        message="这段发言较长，请停顿后分段重说；本段尚未提交。",
                    )
                    continue
                if not turn.id or turn.state == "input_limit":
                    continue
                if changed:
                    await self.input_event(
                        live,
                        "input.transcript",
                        text=readable(turn.text),
                        stable_text=readable(" ".join(turn.segments)),
                        unstable_text=readable(turn.partial),
                        segment_id=len(turn.segments),
                        segment_revision=turn.segment_revision,
                        segment_final=signals.get("segment_final", False),
                    )
                current, current_call = self.current(live.session_id, live.call_id)
                speech_ms = turn.voiced_run / 16
                if live.protocol == 3:
                    response = next(
                        (
                            m
                            for m in current["messages"]
                            if m["id"] == current_call.get("response_id")
                        ),
                        {},
                    )
                    expects_confirmation = bool(
                        re.search(
                            r"(?:would you like|shall I|is that (?:right|correct)|"
                            r"do you (?:want|agree)|does that|can you confirm).*\?\s*$",
                            response.get("text", ""),
                            re.I,
                        )
                    )
                    observed_text = turn.text
                    if (
                        turn.state == "waiting_pause"
                        and live.asr_key == turn.key
                        and live.asr_work
                        and live.asr_work.done()
                    ):
                        acoustic = live.asr_work.result()
                        if acoustic.get("status") == "ready" and not acoustic.get("low_quality"):
                            observed_text = acoustic.get("text") or observed_text
                    decision = interruption_decision(
                        observed_text,
                        turn.speaking,
                        speech_ms,
                        expects_confirmation,
                        thinking=current_call["status"] == "thinking",
                    )
                    confirmed = decision == "interrupt"
                    if live.input_during_output and decision == "backchannel":
                        live.was_backchannel = True
                    elif decision == "interrupt" or words(observed_text) not in BACKCHANNELS:
                        live.was_backchannel = False
                    if current_call["status"] == "speaking" and not turn.interrupted:
                        await self.consider_duck(live, current_call, decision)
                    # Pure acknowledgements during playback never become independent questions.
                    if (
                        live.was_backchannel
                        and decision == "backchannel"
                        and not turn.speaking
                        and turn.quiet_ms >= 450
                    ):
                        turn.reset()
                        live.changed.set()
                        await self.input_event(live, "input.cancelled", reason_code="backchannel")
                        continue
                else:
                    normalized_words = words(turn.text)
                    confirmed = (
                        (
                            speech_ms >= 300
                            and bool(normalized_words)
                            and normalized_words not in BACKCHANNELS
                        )
                        or speech_ms >= 800
                        or bool(re.fullmatch(r"(?:stop|wait|hold on)[.! ]*", turn.text, re.I))
                    )
                if (
                    confirmed
                    and (
                        turn.speaking
                        or (live.protocol == 3 and turn.quiet_ms <= 650)
                        or (
                            live.protocol == 3
                            and live.input_during_output
                            and turn.quiet_ms <= 2000
                            and confirmed
                        )
                    )
                    and turn.state in {"collecting", "waiting_pause"}
                    and not turn.interrupted
                    and current_call["status"] in ["thinking", "speaking"]
                ):
                    old_response = current_call.get("response_id")
                    epoch = await self.interrupt(live.session_id, live.call_id, "caller_confirmed")
                    turn.interrupted = True
                    await self.input_event(
                        live,
                        "interruption.confirmed",
                        next_output_epoch=epoch,
                        interrupted_response_id=old_response,
                        detection_ms=round((turn.sample - turn.speech_start_sample) / 16),
                    )
                if (
                    live.protocol == 3
                    and not turn.speaking
                    and turn.quiet_ms >= 450
                    and re.fullmatch(
                        r"(?:please stop|stop(?: talking| speaking| answering)?|don't answer)",
                        words(observed_text),
                    )
                ):
                    # A stop command must not create a new reply saying "Okay".
                    turn.reset()
                    live.changed.set()
                    await self.input_event(live, "input.cancelled", reason_code="explicit_stop")
                    continue
                status = "waiting_pause" if turn.state == "waiting_pause" else turn.state
                if status != live.input_status:
                    live.input_status = status
                    if status == "waiting_pause":
                        await self.input_event(
                            live,
                            "input.waiting_pause",
                            required_silence_ms=turn.silence_ms,
                        )
                if (
                    turn.state == "waiting_pause"
                    and turn.quiet_ms
                    >= int(os.getenv("CALL_FINAL_ASR_SPECULATE_SILENCE_MS", "800"))
                    and live.asr_key != turn.key
                ):
                    self.start_final_asr(live)
                if turn.adaptive and turn.state == "waiting_pause":
                    await self.adaptive_endpoint(live, signals)
                if signals["ready"]:
                    state, call = self.current(live.session_id, live.call_id)
                    frozen_at = time.monotonic()
                    self.begin_thinking(live, call, frozen_at)
                    call.update(active_question_id=None, active_message_id=None)
                    self.commit(state)
                    await self.input_event(
                        live,
                        "input.endpoint_decided",
                        reason_code=live.endpoint_reason or "fixed_silence",
                        decision_id=live.controller.decision_id,
                        effective_pause_ms=round(turn.quiet_ms),
                    )
                    await self.input_event(live, "input.thinking", budget_ms=10000)
                    if (
                        live.asr_key != turn.key
                        or not live.asr_work
                        or (
                            live.asr_work.done()
                            and live.asr_work.result().get("status") == "unavailable"
                        )
                    ):
                        self.start_final_asr(live)
                    frozen = {
                        "raw": turn.text,
                        "audio": turn.speech_audio(),
                        "silence_ms": round(turn.quiet_ms),
                        "first_sample": turn.first_sample,
                        "last_voiced_sample": turn.last_voiced,
                        "frozen_sample": turn.sample,
                        "asr_work": live.asr_work,
                        "thinking_started": frozen_at,
                        "endpoint_reason": live.endpoint_reason or "fixed_silence",
                        "endpoint_clarify": live.endpoint_clarify,
                        "endpoint": dict(live.controller.prediction),
                        "glossary_version": live.glossary_version,
                        "recognition_terms": live.turn_glossary,
                        "voice_end_monotonic": live.last_voice_received_at,
                    }
                    live.refine_work = self.background(self.finalize_turn(live, turn.key, frozen))

    async def warn(self, live: LiveCall, reason: str, message: str) -> None:
        if reason not in live.warned:
            live.warned.add(reason)
            await self.input_event(live, "input.degraded", reason_code=reason, message=message)

    async def consider_duck(self, live: LiveCall, call: dict, decision: str) -> None:
        candidate = (live.turn.key, call.get("response_id"), call["epoch"])
        if decision != "candidate" or live.ducked == candidate:
            return
        live.ducked = candidate
        candidate_id = uid()
        await self.send(
            live,
            "playback.duck",
            response_id=candidate[1],
            epoch=candidate[2],
            candidate_id=candidate_id,
            gain=0.25,
            expires_ms=500,
        )

        async def restore() -> None:
            await asyncio.sleep(0.5)
            if self.live.get(live.call_id) is not live:
                return
            _, current = self.current(live.session_id, live.call_id)
            if current.get("response_id") == candidate[1] and current["epoch"] == candidate[2]:
                await self.send(
                    live,
                    "playback.restore",
                    response_id=candidate[1],
                    epoch=candidate[2],
                    candidate_id=candidate_id,
                )

        live.duck_work = self.background(restore())

    async def adaptive_endpoint(self, live: LiveCall, signals: dict) -> None:
        turn, controller = live.turn, live.controller
        controller.observe(turn.key)
        # One outstanding native prediction per call; never wait on it in capture.
        if (
            turn.quiet_ms >= controller.next_prediction_ms
            and controller.attempts < 4
            and (not live.endpoint_work or live.endpoint_work.done())
        ):
            key = turn.key
            # End prediction needs the REAL trailing pause. Final-ASR trimming
            # would otherwise feed the same 200ms tail to every re-evaluation.
            audio = bytes(turn.audio)
            controller.attempts += 1
            controller.next_prediction_ms = (900, 1600, 2800, 6001)[controller.attempts - 1]
            if controller.attempts == 1:
                await self.input_event(
                    live, "input.endpoint_candidate", decision_id=controller.decision_id
                )

            async def predict() -> None:
                # Do not cancel shared native inference. Late/obsolete results cannot commit.
                native = asyncio.create_task(asyncio.to_thread(self.endpoint.predict, audio))
                try:
                    done, _ = await asyncio.wait([native], timeout=0.2)
                except asyncio.CancelledError:
                    await native
                    raise
                result = native.result() if done else {"status": "timeout"}
                if (
                    self.live.get(live.call_id) is live
                    and turn.key == key
                    and turn.state == "waiting_pause"
                ):
                    controller.prediction = result
                    if result.get("status") != "ready":
                        await self.warn(
                            live,
                            "endpoint_unavailable",
                            "自然判停暂不可用，当前发言按约三秒静音交接。",
                        )
                    elif "endpoint_unavailable" in live.warned:
                        live.warned.discard("endpoint_unavailable")
                        await self.input_event(
                            live, "input.recovered", reason_code="endpoint_unavailable"
                        )
                if not done:
                    await native  # Retain the worker slot until actual completion.

            live.endpoint_work = self.background(predict())
        final = {}
        if live.asr_key == turn.key and live.asr_work and live.asr_work.done():
            final = live.asr_work.result()
        decision, reason = controller.decide(turn.text, turn.quiet_ms, final)
        if decision in {"commit", "clarify"}:
            turn.state = "finalizing"
            signals["ready"] = True
            live.endpoint_reason = reason
            live.endpoint_clarify = decision == "clarify"

    async def recognition_context(self, session_id: str, call_id: str, terms: object) -> dict:
        state, _ = self.current(session_id, call_id)
        live = self.live[call_id]
        try:
            live.recognition.update(terms)
        except ValueError as exc:
            raise fail(400, "INVALID_RECOGNITION_CONTEXT", str(exc)) from exc
        state["call"]["recognition_context"] = {
            "version": live.recognition.version,
            "terms": list(live.recognition.terms),
        }
        self.commit(state)
        return {"version": live.recognition.version, "effective": "next_turn"}

    def ingest_frame(self, live: LiveCall, pcm: bytes) -> tuple[dict, bool]:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        signals = live.turn.accept(pcm, live.vad.accept(samples))
        changed = False
        if signals["started"]:
            self.speech.recognizer.reset(live.stream)
        if live.turn.id and live.turn.state in ["collecting", "waiting_pause"]:
            text, final = self.speech.accept(
                live.stream,
                bytes(live.turn.audio) if signals["started"] else pcm,
            )
            signals["segment_final"] = final
            changed = live.turn.transcript(text, final)
        return signals, changed

    async def input_event(self, live: LiveCall, event: str, **data: Any) -> None:
        await self.send(
            live,
            event,
            call_id=live.call_id,
            turn_id=live.turn.id,
            revision=live.turn.revision,
            sample=live.turn.sample,
            **data,
        )

    async def cancel_input(self, live: LiveCall, reason: str) -> None:
        if live.refine_work:
            live.refine_work.cancel()
        if live.endpoint_work:
            live.endpoint_work.cancel()
        async with live.input_lock:
            await self.input_event(live, "input.cancelled", reason_code=reason)
            live.turn.reset()
            live.turn.last_audio_at = 0
            if reason != "audio_gap":
                live.audio_damaged = False
                live.damaged_quiet = 0
            live.pending_pcm.clear()
            live.input_status = "idle"
            live.was_backchannel = False
            live.changed.set()
            live.vad.reset()
            self.speech.recognizer.reset(live.stream)
        self.settle(live)

    def start_final_asr(self, live: LiveCall) -> None:
        if live.asr_work and not live.asr_work.done():
            live.asr_work.cancel()
        live.asr_key = live.turn.key
        live.asr_work = self.background(
            self.speech.transcribe_turn(live.turn.speech_audio(), live.turn_glossary)
            if live.protocol == 3
            else self.speech.transcribe_turn(live.turn.speech_audio())
        )

    def input_valid(self, live: LiveCall, key: tuple) -> bool:
        return (
            self.live.get(live.call_id) is live
            and live.turn.key == key
            and live.turn.state in ["finalizing", "refining"]
        )

    async def finalize_turn(self, live: LiveCall, key: tuple, frozen: dict) -> None:
        try:
            if not self.input_valid(live, key):
                return
            frozen_at = frozen.get("thinking_started", time.monotonic())
            raw, audio = frozen["raw"], frozen["audio"]
            audio_range = [frozen["first_sample"], frozen["first_sample"] + len(audio) // 2]
            job = frozen["asr_work"]
            done, _ = await asyncio.wait(
                [job],
                timeout=float(os.getenv("CALL_FINAL_ASR_WAIT_MS", "2500")) / 1000,
            )
            final_asr = job.result() if done else {"text": "", "status": "timeout"}
            if not self.input_valid(live, key):
                return
            if (
                live.protocol == 3
                and not frozen.get("endpoint_clarify")
                and (incomplete(raw) or incomplete(final_asr.get("text") or raw))
            ):
                # Acoustic completeness can be wrong. Second-pass unfinished syntax
                # returns the SAME expression to listening instead of committing it.
                live.turn.state = "waiting_pause"
                state, call = self.current(live.session_id, live.call_id)
                call["status"] = "listening"
                self.commit(state)
                await self.input_event(live, "input.waiting_pause", reason_code="unfinished")
                live.changed.set()
                return
            if not raw and not final_asr.get("text"):
                await self.input_event(live, "input.cancelled", reason_code="no_speech")
                live.turn.reset()
                self.settle(live)
                return
            live.turn.state = "refining"
            await self.input_event(live, "input.refining")
            state = self.service.get(live.session_id)
            recent = [
                m["text"]
                for m in state["messages"]
                if m["type"] == "talker"
                and m["status"] not in {"withdrawn", "cancelled", "transcribing"}
                and m.get("call_id") == live.call_id
            ][-2:]
            # Leave room inside the same ten-second budget for planning, the final
            # Wiki check and first audio. Refiner's own fallback stays conservative.
            try:
                async with asyncio.timeout(float(getattr(self.refiner, "timeout", 12)) + 0.5):
                    recent_text = [t[:200] for t in recent]
                    result = await (
                        self.refiner.refine(
                            raw, final_asr, recent_text, frozen.get("recognition_terms", ())
                        )
                        if live.protocol == 3
                        else self.refiner.refine(raw, final_asr, recent_text)
                    )
            except TimeoutError:
                result = {
                    "text": readable(final_asr.get("text") or raw),
                    "status": "needs_clarification"
                    if disagreement(raw, final_asr.get("text", ""))
                    else "degraded",
                    "reason": "thinking_budget",
                    "unclear_spans": [],
                    "route": "direct",
                }
            if not self.input_valid(live, key):
                return
            if result["status"] == "no_content":
                await self.input_event(live, "input.cancelled", reason_code="no_content")
                live.turn.reset()
                self.settle(live)
                return
            # Endpoint timing is not a language-understanding verdict. Its
            # diagnostics stay in metadata; the primary LLM owns clarification.
            if final_asr.get("low_quality") or (
                live.protocol == 3
                and final_asr.get("status") != "ready"
                and result["status"] not in {"corrected", "verified_passthrough"}
            ):
                # Missing/weak second-pass evidence is a diagnostic, not proof
                # that the caller's intent is unclear. Preserve genuine conflicts.
                if result["status"] != "needs_clarification":
                    result.update(status="degraded", reason="low_asr_quality")
            metadata = {
                "turn_id": key[0],
                "revision": key[1],
                "raw_transcript": raw,
                "final_asr_text": final_asr.get("text", ""),
                "final_asr_status": final_asr["status"],
                "final_asr_model": self.speech.final_name,
                "audio_range": audio_range,
                "last_voiced_sample": frozen["last_voiced_sample"],
                "frozen_sample": frozen["frozen_sample"],
                **result,
                "silence_ms": frozen["silence_ms"],
                "final_asr_ms": final_asr.get("final_asr_ms"),
                "endpoint_reason": frozen.get("endpoint_reason", "fixed_silence"),
                "endpoint": frozen.get("endpoint", {}),
                "glossary_version": frozen.get("glossary_version", 1),
                "final_asr_quality": final_asr.get("quality", {}),
                "voice_end_monotonic": frozen.get("voice_end_monotonic", 0),
                "finalization_ms": round((time.monotonic() - frozen_at) * 1000),
            }
            if result["status"] not in {"corrected", "verified_passthrough"}:
                await self.input_event(
                    live,
                    "input.degraded",
                    reason_code=result["reason"],
                    message="转写有歧义，将请你确认。"
                    if result["status"] == "needs_clarification"
                    else "转写校正未完整完成，已保留原话。",
                )
            message_id = await self.submit(
                live, result["text"], audio, metadata, key, thinking_started=frozen_at
            )
            if message_id:
                await self.send(
                    live,
                    "input.committed",
                    call_id=live.call_id,
                    turn_id=key[0],
                    revision=key[1],
                    message_id=message_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("turn_finalize_failed error=%s", type(exc).__name__)
            if self.input_valid(live, key):
                await self.input_event(
                    live,
                    "input.degraded",
                    reason_code="finalize_failed",
                    message="这段话没有提交成功，请重说一次。",
                )
                live.turn.reset()
                state, call = self.current(live.session_id, live.call_id)
                call.update(status="listening", error="这段话未能及时整理，请重说一次。")
                self.commit(state)
                live.changed.set()

    async def submit(
        self,
        live: LiveCall,
        text: str,
        audio: bytes = b"",
        transcription: dict | None = None,
        input_key: tuple | None = None,
        thinking_started: float = 0,
    ) -> str | None:
        if input_key and not self.input_valid(live, input_key):
            return None
        await self.interrupt(live.session_id, live.call_id, "new_turn")
        state, call = self.current(live.session_id, live.call_id)
        message_id = uid()
        message: dict = {
            "id": message_id,
            "type": "talker",
            "text": text,
            "input_mode": "voice" if audio else "text",
            "status": "completed",
            "created_at": now(),
            "call_id": live.call_id,
            "question_ids": [],
        }
        if transcription:
            message.update(
                turn_id=transcription["turn_id"],
                transcription_status=transcription["status"],
                transcription=transcription,
            )
        if audio:
            filename = message_id + ".wav"

            def archive() -> None:
                with wave.open(str(self.service.audio_dir / filename), "wb") as out:
                    out.setnchannels(1)
                    out.setsampwidth(2)
                    out.setframerate(16000)
                    out.writeframes(audio)

            try:
                await asyncio.to_thread(archive)
                message["audio"] = {
                    "id": message_id,
                    "kind": "original",
                    "status": "ready",
                    "filename": filename,
                }
            except Exception:
                logger.warning("call_archive_failed")
        if input_key and not self.input_valid(live, input_key):
            return None
        state, call = self.current(live.session_id, live.call_id)
        state["messages"].append(message)
        call["final_asr_ready"] = self.speech.final_model is not None
        call.update(
            status="thinking", error="", active_question_id=None, active_message_id=message_id
        )
        started = thinking_started or time.monotonic()
        deadline = self.begin_thinking(live, call, started)
        self.commit(state)
        if input_key:
            live.turn.reset()
        live.work = live.intake_work = asyncio.create_task(
            self.process(live, message_id, call["epoch"], started, deadline)
        )
        live.intake_work.add_done_callback(lambda _: self.settle(live))
        live.changed.set()
        return message_id

    @staticmethod
    def history(state: dict) -> list[dict]:
        return [
            {"role": m["type"], "text": m.get("delivered_text", m["text"])}
            for m in state["messages"][-20:]
            if m["type"] == "talker" or (m["type"] == "ai" and m.get("delivery") == "delivered")
        ]

    async def process(
        self,
        live: LiveCall,
        message_id: str,
        epoch: int,
        started: float = 0,
        deadline: float = 0,
    ) -> None:
        if self.valid(live, epoch, allow_paused=True):
            await self.service.replies.process(live.session_id, message_id, live, started, deadline)

    async def respond(
        self,
        live: LiveCall,
        qid: str,
        *,
        candidate: str = "",
        notice: bool = False,
        started: float = 0,
        deadline: float = 0,
        supplemental: bool = False,
    ) -> None:
        await self.service.replies.respond(
            live.session_id,
            qid,
            live,
            candidate=candidate,
            notice=notice,
            started=started,
            deadline=deadline,
            supplemental=supplemental,
        )

    async def delivered(self, live: LiveCall, data: dict) -> None:
        state, call = self.current(live.session_id, live.call_id)
        if data.get("epoch") != call["epoch"]:
            return
        if any(
            a["id"] == data.get("response_id") and a["status"] == "withdrawn"
            for a in state["answers"]
        ):
            return
        for item in state["messages"] + state["answers"]:
            if item["id"] == data.get("response_id"):
                item["delivery"] = "delivered"
        for q in state["questions"]:
            answer = next((a for a in state["answers"] if a["id"] == data.get("response_id")), None)
            if answer and not answer.get("notice") and q["id"] in answer["question_ids"]:
                q.update(
                    status=q.get("completion_status")
                    or ("answered" if q.get("category") != "harmful" else "blocked"),
                    answered_at=now(),
                )
                for alert in call["alerts"]:
                    if alert["question_id"] == q["id"] and alert["status"] != "denied":
                        alert["status"] = "resolved"
        released = []
        answered_ids = {q["id"] for q in state["questions"] if q["status"] == "answered"}
        for q in state["questions"]:
            if (
                q.get("call_id") == live.call_id
                and q["status"] == "held"
                and q.get("awaiting_dependencies")
                and q.get("depends_on")
                and set(q["depends_on"]).issubset(answered_ids)
            ):
                q.update(status="generating", awaiting_dependencies=False, version=q["version"] + 1)
                released.append(q["id"])
                for alert in call["alerts"]:
                    if alert["question_id"] == q["id"]:
                        alert["status"] = "resolved"
        if call.get("response_id") == data.get("response_id") and call["status"] == "speaking":
            call["status"] = "listening"
        self.commit(state)
        for qid in released:
            self.queue_response(live, qid, supplemental=True)
        live.changed.set()
        self.service.spawn(asyncio.to_thread(self.service.wiki_for(state).sync_session, state))

    async def error(self, live: LiveCall, exc: Exception) -> None:
        logger.warning("call_failed call=%s error=%s", live.call_id, type(exc).__name__)
        try:
            state, call = self.current(live.session_id, live.call_id)
            call.update(status="listening", error="本轮回答未完成，请重说或由 Hinter 补充提示。")
            self.commit(state)
            await self.send(live, "error", message=call["error"])
        except Exception:
            pass
