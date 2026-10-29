"""Automatic question routing and a shared, cancellable text / speech publisher."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import struct
import time
import wave
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent.call_search import CallSearch, PublicSearchError
from app.agent.document_evidence import read_documents
from app.agent.provided_evidence import evidence_material, external_verification_requested
from app.agent.request_groups import coalesce_requests
from app.agent.tool_config import load_tool_config
from app.agent.tools import ToolRuntime, document_links
from app.agent.transcript_refiner import disagreement
from app.application.assistance import assistance_request, question_context, render_guidance
from app.application.answer_cards import multipart_question, spoken_parts, text_ready
from app.application.segments import SentenceBuffer
from app.application.service import fail, now, uid

if TYPE_CHECKING:
    from app.application.service import SessionService

logger = logging.getLogger(__name__)
UNANSWERABLE_REPLY = "I don't know."
PROHIBITION_REPLY = UNANSWERABLE_REPLY
CONFIRMED_QUESTION = (
    "\nCONTROL STATE: Hinter explicitly confirmed answering THIS question only. "
    "Its require_human confirmation is satisfied. All prohibitions, privacy "
    "constraints and factual evidence requirements still apply."
)


class ContextChanged(Exception):
    pass


class InvalidSegment(Exception):
    pass


@dataclass
class ReplyRun:
    session_id: str
    question_id: str
    response_id: str
    generation: int
    stream_id: int
    live: Any = None
    signature: str = ""
    marker: tuple = ()
    text: str = ""
    segment_seq: int = 0
    cancelled: bool = False
    context_changed: bool = False
    task: asyncio.Task | None = None
    audio_task: asyncio.Task | None = None
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=2))
    spoken_samples: int = 0
    audio_rate: int = 0
    pcm: bytearray = field(default_factory=bytearray)
    first_audio_at: float = 0
    first_substantive_at: float = 0
    audio_clock: float = 0
    sent_audio_seconds: float = 0
    started: float = field(default_factory=time.monotonic)
    deadline: float = 0
    began: asyncio.Event | None = None
    defer_audio: bool = False
    previous_parts: list[dict] = field(default_factory=list)


class ResponseService:
    def __init__(self, service: SessionService) -> None:
        self.service = service
        self.runs: dict[str, ReplyRun] = {}
        self.jobs: dict[tuple[str, str], asyncio.Task] = {}
        self.output_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.subscribers: defaultdict[str, set[asyncio.Queue]] = defaultdict(set)
        self.stream_id = 0

    @property
    def calls(self):
        return self.service.calls

    def marker(self) -> tuple:
        return self.service.guidance.epoch, self.service.wiki_version

    def history(self, state: dict) -> list[dict]:
        return [
            {"role": m["type"], "text": m.get("delivered_text", m["text"])}
            for m in state["messages"][-20:]
            if m["type"] == "talker"
            or (
                m["type"] == "ai"
                and m.get("public_stream")
                and m["status"] == "completed"
                and not m.get("refusal")
                and not m.get("notice")
                and m.get("text", "").strip() != UNANSWERABLE_REPLY
                and m.get("reply_kind") not in {"acknowledgement", "progress", "refusal"}
                and (not m.get("call_id") or m.get("delivered_text"))
            )
        ]

    def scoped_constraints(self, session_id: str, constraints: str, matched_ids: list) -> str:
        """Remove only known, unmatched topic rules; preserve every other constraint."""
        for record in self.service.guidance.records(self.service.get(session_id)):
            if record["kind"] == "prohibition" and record["id"] not in matched_ids:
                rendered = (
                    f"[{record['id']} / prohibition / {record['scope']}] "
                    + render_guidance(record)
                )
                constraints = constraints.replace(rendered, "", 1)
        return constraints

    @staticmethod
    def speech_context(message: dict) -> dict:
        transcript = message.get("transcription", {})
        if not transcript:
            return {}
        raw, final = transcript.get("raw_transcript", ""), transcript.get("final_asr_text", "")
        return {
            "status": message.get("transcription_status"),
            "reason": transcript.get("reason", ""),
            "raw_transcript": raw[:1500],
            "final_asr_text": final[:1500],
            "unclear_spans": transcript.get("unclear_spans", [])[:5],
            "missing_slots": transcript.get("missing_slots", [])[:5],
            "effective_text": message["text"],
            "removed_spans": transcript.get("removed_spans", [])[:8],
            "protected_conflict": transcript.get("protected_conflict", disagreement(raw, final))
            or transcript.get("reason") in {"asr_disagreement", "protected_content_changed"},
        }

    def reply_context(self, state: dict, question: dict) -> dict:
        previous = []
        for answer in reversed(state["answers"]):
            if answer.get("status") == "withdrawn" or answer.get("notice"):
                continue
            text = (
                answer.get("delivered_text", "")
                if answer.get("call_id")
                else answer.get("text", "")
            )
            if not text or answer.get("reply_kind") in {"acknowledgement", "progress"}:
                continue
            source = next(
                (q for q in state["questions"] if q["id"] in answer.get("question_ids", [])), {}
            )
            if source.get("decision") != "clarify":
                break
            previous.append(text[:300])
            if len(previous) == 3:
                break
        context = {
            "language": state.get("language", "en-US"),
            "decision": question.get("decision", "answer"),
            "use_tools": question.get("use_tools", False),
            "speech_recognition": question.get("speech_context", {}),
            "previous_clarifications": list(reversed(previous)),
            **{
                k: question.get(k)
                for k in [
                    "data_scope",
                    "scope_reason",
                    "evidence_status",
                    "evidence_gap",
                    "evidence_quotes",
                    "evidence_source",
                    "retrieval_mode",
                    "subrequests",
                ]
            },
        }
        if question.get("id"):
            persisted = next((q for q in state["questions"] if q["id"] == question["id"]), {})
            context["previous_answer_parts"] = [
                part for part in persisted.get("answer_parts", [])
                if part.get("status") in {"known", "partial"}
            ]
            inputs = [
                {
                    "guidance_id": r["id"],
                    "text": r["text"]
                    if r["kind"] != "reference"
                    else "Reference material is supplied separately in wiki_facts.",
                    "input_kind": r["kind"],
                    "target_part": (r.get("binding") or {}).get("target_part"),
                }
                for r in self.service.guidance.records(state)
                if r.get("question_id") == question["id"]
                and not r.get("revoked")
                and r.get("effective_from") != "next_turn"
            ]
            if inputs:
                # Use the persisted question, never the supplemental delivery prefix.
                original = next(q for q in state["questions"] if q["id"] == question["id"])
                context["human_assistance"] = {
                    **question_context(state, original),
                    "hinter_inputs": inputs,
                }
        return context

    def clarification(self, state: dict, message: dict, plan: dict) -> str:
        slots = self.speech_context(message).get("missing_slots", [])
        previous = self.reply_context(state, {"decision": "clarify"})["previous_clarifications"]
        proposed = str(plan.get("clarification_question", "")).strip()
        generic = re.search(
            r"clarify that|could you clarify\?|what part.*unclear|names, numbers or IDs|"
            r"(?:clarify|rephrase|repeat|explain) (?:your |the |this |that )?"
            r"(?:statement|request|message|question|sentence|utterance)\b|"
            r"^what do you mean\?$",
            proposed,
            re.I,
        )
        quoted = re.search(r'["“](.*?)["”]', proposed)
        if quoted and quoted[1].casefold() not in message["text"].casefold():
            # A discarded acoustic fragment is not a caller entity to define.
            heard = " ".join(str(slot.get("heard", "")) for slot in slots
                             if isinstance(slot, dict))
            if quoted[1].casefold() not in heard.casefold():
                generic = True
        if quoted and re.fullmatch(
            r"(?:i['’]d|i would|i) (?:like|want|need) to know(?: what)?|"
            r"(?:can|could|would) you (?:please )?tell me|please tell me|tell me",
            quoted[1].strip(), re.I,
        ):
            generic = True  # A normal request frame is never an unclear noun.
        if (
            proposed.endswith("?")
            and proposed.count("?") == 1
            and len(proposed.split()) <= 26
            and not generic
            and proposed.casefold() not in [text.casefold() for text in previous]
        ):
            return proposed
        if plan.get("data_scope") == "unknown":
            return "Are you asking about your team's internal results or a public report?"
        if any(str(slot.get("slot", "")).casefold() in {"city", "location", "城市", "地点"}
               for slot in slots if isinstance(slot, dict)):
            return ("Which city would you like the weather for?"
                    if re.search(r"weather|天气", message["text"], re.I)
                    else "Which location do you mean?")
        # Grounded recovery when the router cannot produce a useful question.
        # Do not fabricate a recognized name or silently choose a disputed ID.
        text = message["text"]
        speech = self.speech_context(message)
        if speech.get("protected_conflict") and re.search(
            r"\b(?:id|identifier|account number)\b",
            speech.get("raw_transcript", "") + " " + speech.get("final_asr_text", ""),
            re.I,
        ):
            slot = "ID"
        elif re.search(r"\b(?:my name|name is)\b", text, re.I):
            slot = "name"
        elif re.search(r"\b(?:id|identifier|account number)\b", text, re.I):
            slot = "ID"
        elif re.search(r"\b(?:number|amount|price|quantity)\b", text, re.I):
            slot = "number"
        else:
            slot = ""
        if slot:
            repeated = sum(
                bool(re.search(r"\b" + re.escape(slot) + r"\b", p, re.I)) for p in previous
            )
            if repeated >= 2:
                return "Let's come back to that detail. What else would you like help with?"
            if slot == "name":
                return (
                    "Could you spell just your name, one letter at a time?"
                    if repeated
                    else "Could you tell me just your name?"
                )
            return f"Could you repeat just the {slot}, one character at a time?"
        # Raw ASR spans are diagnostics, not nouns selected by the primary LLM.
        # Never manufacture a meaning question by quoting the first such span.
        assessment = plan.get("speech_assessment") or {}
        if assessment.get("kind") == "entity_spelling":
            return "Which name or term do you mean?"
        return (
            "I'm still missing that detail. Could you say it a different way?"
            if previous
            else "I missed the last part. Could you repeat just that part?"
        )

    def answer_context(self, state: dict, question: dict, evidence: str) -> dict:
        context = self.reply_context(state, question)
        if evidence:
            context["use_tools"] = True
            document = bool(question.get("document_urls"))
            context["tool_evidence"] = {
                "name": "lark_doc_read" if document else "web_search",
                "arguments": {"url": question["document_urls"][0]}
                if document
                else {
                    "query": question.get("web_query")
                    or CallSearch.requested_query(question["text"])
                },
                "content": evidence,
            }
        return context

    def record_tool_trace(self, session_id: str, question_id: str):
        async def record(name: str, arguments: dict, output: str) -> None:
            state = self.service.get(session_id)
            question = next(q for q in state["questions"] if q["id"] == question_id)
            try:
                payload = json.loads(output)
            except (TypeError, ValueError):
                payload = {}
            record = {}
            for block in payload.get("content", []) if isinstance(payload, dict) else []:
                if block.get("type") == "text":
                    try:
                        record = json.loads(block.get("text", ""))
                    except (TypeError, ValueError):
                        record = {}
                    break
            success = not payload.get("isError", False) and bool(record)
            work = question.setdefault(
                "work", {"id": uid(), "state": "running", "tool_calls": [], "sources": []}
            )
            work["tool"] = name
            work.setdefault("tool_calls", []).append(
                {
                    "tool": name,
                    "arguments": arguments,
                    "record_id": record.get("project_id", ""),
                    "ok": success,
                }
            )
            source = record.get("app_name")
            if source and source not in work.setdefault("sources", []):
                work["sources"].append(source)
            work["state"] = "result_ready" if success else "failed"
            self.calls.commit(state, "work.finished" if success else "work.failed")

        return record

    async def emit(self, session_id: str, event: str, live: Any = None, **data: Any) -> None:
        if live:
            await self.calls.send(live, event, **data)
        else:
            self.broadcast(session_id, {"type": event, **data})

    def broadcast(self, session_id: str, data: Any) -> None:
        for queue in tuple(self.subscribers[session_id]):
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                # A slow subscriber must never block capture or grow an audio backlog.
                self.subscribers[session_id].discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)

    def guidance_changed(self, session_id: str, target: str | None) -> None:
        # Publishers compare the applicable signature at every await / frame boundary.
        for run in list(self.runs.values()):
            if run.session_id == session_id and (not target or target == run.question_id):
                if self.service.guidance.snapshot(session_id, run.question_id)[2] != run.signature:
                    run.context_changed = True
                    if run.task and run.task is not asyncio.current_task() and not run.task.done():
                        run.task.cancel()
                    elif run.audio_task and not run.audio_task.done():
                        run.audio_task.cancel()
                        state = self.service.get(session_id)
                        changed = False
                        for item in state["messages"] + state["answers"]:
                            if (
                                item.get("id") == run.response_id
                                and item.get("status") == "completed"
                            ):
                                item.update(status="interrupted", delivery="interrupted")
                                changed = True
                        question = next(
                            (q for q in state["questions"] if q["id"] == run.question_id),
                            None,
                        )
                        if question and question.get("status") == "answered":
                            question["status"] = "ready"
                            changed = True
                        if changed:
                            self.calls.commit(state, "response.interrupted")
                if run.live:
                    run.live.changed.set()

    async def apply_guidance(
        self, session_id: str, target: str | None, *, guidance_id: str | None = None
    ) -> None:
        """New evidence can resolve only questions to which this Hint applies."""
        state = self.service.get(session_id)
        triggering_rule = next((r for r in self.service.guidance.records(state)
                                if r["id"] == guidance_id), None) if guidance_id else None
        if guidance_id and (not triggering_rule or triggering_rule.get("revoked")):
            return
        blocking_rules = any(
            r["kind"] == "prohibition" and not r.get("revoked")
            for r in self.service.guidance.records(state)
        )
        pending = [
            q
            for q in state["questions"]
            if (
                q["status"] in {"held", "unavailable", "awaiting_user"}
                or (
                    q["status"] == "answered"
                    and (target == q["id"] or (
                        q == state["questions"][-1] and (
                            q.get("decision") == "lookup"
                            or q.get("evidence_status") == "partial"
                            or any(p.get("status") in {"unknown", "partial"}
                                   for p in q.get("answer_parts", []))
                            or q.get("work", {}).get("state") == "failed"
                        )
                    ))
                )
                or q["status"] == "ready"
                or ((target == q["id"] or blocking_rules) and q["status"] == "generating")
            )
            and (not target or target == q["id"])
            and (not triggering_rule
                 or self.service.guidance.applies_to(triggering_rule, state, q["id"]))
        ]
        for question in pending:
            try:
                bound_facts = [
                    r
                    for r in self.service.guidance.records(state)
                    if r.get("question_id") == question["id"]
                    and r["kind"] == "authorized_fact"
                    and not r.get("revoked")
                    and r.get("binding")
                    and r.get("effective_from") != "next_turn"
                ]
                if (
                    target
                    and bound_facts
                    and question.get("risk_reason") == "human_evidence_required"
                ):
                    # Bound evidence uses the explicit supplement path without replanning.
                    await self.correct(session_id, question["id"], confirm=False)
                    continue
                wiki, constraints, _ = self.service.guidance.snapshot(session_id, question["id"])
                plans = await self.calls.reasoner.plan(
                    question["text"], wiki, constraints, self.history(state)
                )
                if not plans:
                    continue
                action, _ = self.calls.reasoner.policy.decide(plans[0], constraints)
                fresh = self.service.get(session_id)
                current = next(q for q in fresh["questions"] if q["id"] == question["id"])
                if current["version"] != question["version"] or current["status"] in {
                    "withdrawn",
                    "rejected",
                    "blocked",
                }:
                    continue
                if action == "block":
                    await self.correct(session_id, question["id"], plan=plans[0], confirm=False)
                elif (
                    plans[0].get("evidence_status") == "sufficient"
                    or (plans[0].get("evidence_source") == "provided"
                        and plans[0].get("evidence_quotes"))
                    or (target and bound_facts)
                ):
                    if triggering_rule and not target:
                        def normalized(value):
                            return re.sub(r"\s+", "", str(value).casefold())
                        supplied = normalized(triggering_rule["text"])
                        if not any(normalized(quote) and normalized(quote) in supplied
                                   for quote in plans[0].get("evidence_quotes", [])):
                            continue
                    await self.correct(session_id, question["id"], plan=plans[0], confirm=False)
                elif action != "await_human" and current["status"] == "held":
                    await self.correct(session_id, question["id"], plan=plans[0], confirm=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.info("pending_evidence_not_ready question=%s", question["id"])

    def valid(self, run: ReplyRun) -> bool:
        if run.cancelled:
            return False
        state = self.service.get(run.session_id)
        q = next((q for q in state["questions"] if q["id"] == run.question_id), {})
        answer = next((a for a in state["answers"] if a["id"] == run.response_id), {})
        if q.get("status") in ["withdrawn", "rejected", "interrupted", "blocked"]:
            return False
        if answer.get("status") == "withdrawn" or state.get("automatic_paused"):
            return False
        if run.live:
            if not self.calls.valid(run.live, run.generation):
                return False
        elif state.get("interaction_mode") == "realtime_call":
            return False
        if self.marker() != run.marker:
            signature = self.service.guidance.snapshot(run.session_id, run.question_id)[2]
            if signature != run.signature:
                raise ContextChanged()
            run.marker = self.marker()
        return True

    def ensure(self, run: ReplyRun) -> None:
        if not self.valid(run):
            raise asyncio.CancelledError()

    async def cancel_outputs(self, session_id: str, response_id: str | None = None) -> None:
        if response_id is None:
            state = self.service.get(session_id)
            changed = False
            for (sid, qid), task in list(self.jobs.items()):
                if sid != session_id or task.done() or task is asyncio.current_task():
                    continue
                task.cancel()
                question = next((q for q in state["questions"] if q["id"] == qid), None)
                if question and question["status"] not in ["held", "withdrawn", "rejected"]:
                    question["status"] = "interrupted"
                    changed = True
            if changed:
                self.calls.commit(state, "response.stopped")
        for run in list(self.runs.values()):
            if run.session_id != session_id or (response_id and run.response_id != response_id):
                continue
            run.cancelled = True
            for task in [run.task, run.audio_task]:
                if task and task is not asyncio.current_task():
                    task.cancel()
            await self.emit(
                session_id,
                "response.interrupted",
                run.live,
                response_id=run.response_id,
                output_generation=run.generation,
                reason_code="stopped",
            )

    async def set_paused(self, session_id: str, paused: bool) -> None:
        state = self.service.get(session_id)
        state["automatic_paused"] = paused
        state["ai_mode"] = not paused
        call = state.get("call", {})
        if call.get("status") not in [None, "ended"]:
            call["paused"] = paused
            live = self.calls.live.get(call["id"])
            if live:
                live.paused = paused
                live.changed.set()
        self.service.publish(state, "automatic.updated")
        if paused:
            await self.cancel_outputs(session_id)
        else:
            for q in state["questions"]:
                if q.get("pause_pending") and q["status"] == "ready":
                    q.pop("pause_pending", None)
                    live = self.calls.live.get(state.get("call", {}).get("id"))
                    if live:
                        self.calls.queue_response(live, q["id"])
                    else:
                        self.schedule(session_id, q["id"])

    async def process(
        self,
        session_id: str,
        message_id: str,
        live: Any = None,
        started: float = 0,
        deadline: float = 0,
    ) -> None:
        started = started or time.monotonic()
        deadline = deadline or started + 10
        state = self.service.get(session_id)
        message = next(m for m in state["messages"] if m["id"] == message_id)
        if message.get("routing_done"):
            return
        # Every Talker input passes semantic cleanup once, before routing. Phone
        # turns already carry the refiner's metadata and are not processed twice.
        if not message.get("transcription") and not message.get("input_refinement_done"):
            original = message["text"]
            recent = [
                m["text"]
                for m in state["messages"]
                if m["type"] == "talker" and m["id"] != message_id
            ][-2:]
            refined = await self.calls.refiner.refine(
                original,
                {"text": original, "status": "typed"}
                if message.get("input_mode") == "text"
                else {},
                recent,
            )
            state = self.service.get(session_id)
            message = next(m for m in state["messages"] if m["id"] == message_id)
            if message["text"] != original or message.get("routing_done"):
                return
            message.update(
                original_text=original,
                text=refined["text"],
                transcription_status=refined["status"],
                input_refinement_done=True,
                transcription={"raw_transcript": original, "final_asr_text": "", **refined},
            )
            self.calls.commit(state, "input.refined")
        if message.get("transcription_status") == "no_content":
            message.update(status="cancelled", routing_done=True)
            self.calls.commit(state, "input.ignored")
            return
        if re.fullmatch(
            r"(?:try again|retry|search again|再试一次|重试|再查一次)[.!?。！\s]*",
            message["text"],
            re.I,
        ):
            previous = next(
                (
                    q
                    for q in reversed(state["questions"])
                    if q.get("decision") == "lookup" and q["status"] == "unavailable"
                ),
                None,
            )
            if previous:
                previous.update(
                    status="ready",
                    version=previous["version"] + 1,
                    retrieval_mode="public",
                    evidence_status="missing",
                )
                message.update(
                    question_ids=[previous["id"]], status="intake_complete", routing_done=True
                )
                self.calls.commit(state, "question.retried")
                if not state.get("automatic_paused"):
                    if live:
                        self.calls.queue_response(live, previous["id"], started=started)
                    else:
                        self.schedule(session_id, previous["id"], started=started)
                return
        if not live and state.get("interaction_mode") == "realtime_call":
            return
        epoch = live.epoch if live else None
        wiki, constraints, _ = self.service.guidance.snapshot(session_id, message_id=message_id)
        # The semantic scope review uses the answer model, independently of the
        # short routing/first-response budget. Expiry must not become a false ban.
        routing_deadline = max(deadline, time.monotonic() + 30)
        try:
            speech_context = self.speech_context(message)
            # The primary LLM sees every meaningful utterance, including a
            # recognizer's suspected conflict. Diagnostics cannot bypass it.
            async with asyncio.timeout(max(0.01, routing_deadline - time.monotonic())):
                plans = await self.calls.reasoner.plan(
                    message["text"], wiki, constraints, self.history(state),
                    transcription=speech_context,
                )
            if speech_context.get("protected_conflict"):
                for plan in plans:
                    if self.calls.reasoner.policy.decide(plan, constraints)[0] == "block":
                        continue
                    assessment = plan.get("speech_assessment")
                    assessment = assessment if isinstance(assessment, dict) else {}
                    resolved = (assessment.get("resolution") == "resolved"
                                and assessment.get("kind") in {"entity_spelling", "grammar", "none"})
                    if not resolved:
                        plan.update(action="clarify", use_tools=False, web_query="")
            if live and not self.calls.valid(live, epoch, allow_paused=True):
                return
            state = self.service.get(session_id)
            if not live and state.get("interaction_mode") == "realtime_call":
                return
            message = next(m for m in state["messages"] if m["id"] == message_id)
            if message.get("routing_done"):
                return
            # Guard the actual publishing path too (including imported/older
            # routing implementations), before creating any questions or jobs.
            plans = coalesce_requests(message["text"], plans, constraints)
            if (plans
                    and message.get("transcription_status") == "needs_clarification"
                    and all(self.calls.reasoner.policy.decide(plan, constraints)[0] != "clarify"
                            for plan in plans)):
                # Keep the editor's original diagnostics, but stop displaying an
                # unresolved transcript after semantic routing understood it.
                transcription = message.get("transcription", {})
                message["transcription"] = {
                    **transcription,
                    "refiner_status": transcription.get("status"),
                    "refiner_unclear_spans": transcription.get("unclear_spans", []),
                    "refiner_missing_slots": transcription.get("missing_slots", []),
                    "refiner_protected_conflict": transcription.get("protected_conflict", False),
                    "protected_conflict": False,
                    "status": "corrected", "resolution": "semantic_routing",
                    "unclear_spans": [], "missing_slots": [],
                }
                message["transcription_status"] = "corrected"
                speech_context = {**speech_context, "status": "corrected",
                                  "resolution": "semantic_routing", "unclear_spans": [],
                                  "missing_slots": [], "protected_conflict": False}
            ids = []
            scheduled = []
            for plan in plans:
                if not isinstance(plan, dict):
                    continue
                source = str(plan.get("text", "")).strip() or message["text"]
                action, reason = self.calls.reasoner.policy.decide(plan, constraints)
                category = self.calls.reasoner.policy.category(
                    source, plan.get("category", "routine")
                )
                dependencies = [
                    ids[d]
                    for d in plan.get("depends_on", [])
                    if isinstance(d, int) and 0 <= d < len(ids)
                ]
                q = {
                    "id": uid(),
                    "display_no": f"Q-{201 + len(state['questions'])}",
                    "text": source,
                    "status": "held" if action == "await_human" else "ready",
                    "version": 1,
                    "duplicate_count": 1,
                    "human_reviewed": False,
                    "created_at": now(),
                    "source_message_ids": [message_id],
                    "category": category,
                    "subrequests": plan.get("subrequests", []),
                    "decision": action,
                    "speech_context": speech_context,
                    "speech_assessment": plan.get("speech_assessment"),
                    "risk_reason": reason,
                    "matched_rule_ids": plan.get("matched_rule_ids", []),
                    "prohibition_assessment": plan.get("prohibition_assessment"),
                    "depends_on": dependencies,
                    "uses_wiki_facts": bool(plan.get("uses_wiki_facts")),
                    "use_tools": bool(plan.get("use_tools")),
                    **{
                        k: plan.get(k)
                        for k in [
                            "data_scope",
                            "scope_reason",
                            "evidence_status",
                            "evidence_quotes",
                            "evidence_source",
                            "evidence_gap",
                        ]
                    },
                    "web_query": CallSearch.plan_query(plan) if action == "lookup" else "",
                    "document_urls": [
                        url
                        for url in plan.get("document_urls", [])
                        if url in document_links(message["text"])
                    ],
                    "pause_pending": state.get("automatic_paused", False),
                }
                if (action == "answer" and plan.get("evidence_source") == "provided"
                        and plan.get("evidence_quotes")):
                    q["retrieval_mode"] = "provided"
                    q["use_tools"] = False
                if action == "await_human":
                    q["assistance_request"] = assistance_request(q, plan)
                if live:
                    q["call_id"] = live.call_id
                if action == "clarify":
                    q["clarification_question"] = self.clarification(state, message, plan)
                state["questions"].append(q)
                ids.append(q["id"])
                scheduled.append(q)
                if action == "await_human" and live:
                    state["call"]["alerts"].append(
                        {
                            "id": uid(),
                            "question_id": q["id"],
                            "text": source,
                            "reason": "需要已核实的人工信息"
                            if reason == "human_evidence_required"
                            else "命中明确人工确认规则",
                            "category": reason,
                            "status": "open",
                            "created_at": now(),
                        }
                    )
            message.update(question_ids=ids, status="intake_complete", routing_done=True)
            self.calls.commit(state, "questions.routed")
            if any(
                record.get("created_at", "") < q.get("created_at", "")
                for record in self.service.guidance.records(state)
                for q in scheduled
            ):
                self.service.spawn(self.service.guidance.project(session_id))
            for q in scheduled:
                if state.get("automatic_paused"):
                    continue
                candidate = ""
                notice = q["decision"] == "await_human"
                if q["decision"] == "block":
                    candidate = PROHIBITION_REPLY
                elif notice:
                    candidate = (
                        "I can't verify that information directly. "
                        "I'll follow up when I have a verified answer."
                        if q["risk_reason"] == "human_evidence_required"
                        else "That part needs confirmation. We can keep talking about your other "
                        "questions."
                    )
                elif q["decision"] == "refuse":
                    candidate = (
                        "I can't help with instructions for harm or crime. I can help with a safe"
                        " alternative."
                    )
                elif q["decision"] == "clarify":
                    # The same routing call already produced the specific
                    # question; don't add a second generation that can loop.
                    candidate = q["clarification_question"]
                # Evidence-dependent questions are released when their actual predecessors resolve.
                if q["depends_on"]:
                    current = self.service.get(session_id)
                    item = next(x for x in current["questions"] if x["id"] == q["id"])
                    item["awaiting_dependencies"] = True
                    self.calls.commit(current)
                    continue
                options = dict(
                    candidate=candidate, notice=notice, started=started,
                    deadline=max(deadline, time.monotonic() + 10),
                )
                if live:
                    self.calls.queue_response(live, q["id"], **options)
                else:
                    self.schedule(session_id, q["id"], **options)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("automatic_route_failed")
            state = self.service.get(session_id)
            message = next(m for m in state["messages"] if m["id"] == message_id)
            if message.get("routing_done"):
                return
            q = {
                "id": uid(),
                "display_no": f"Q-{201 + len(state['questions'])}",
                "text": message["text"],
                "status": "ready",
                "version": 1,
                "duplicate_count": 1,
                "human_reviewed": False,
                "created_at": now(),
                "source_message_ids": [message_id],
                "decision": "clarify",
                "category": "ambiguous",
            }
            if live:
                q["call_id"] = live.call_id
            state["questions"].append(q)
            message.update(status="intake_complete", question_ids=[q["id"]], routing_done=True)
            self.calls.commit(state)
            if any(
                record.get("created_at", "") < q.get("created_at", "")
                for record in self.service.guidance.records(state)
            ):
                self.service.spawn(self.service.guidance.project(session_id))
            options = {
                "candidate": UNANSWERABLE_REPLY,
                "started": time.monotonic(),
                "deadline": time.monotonic() + 5,
            }
            if live:
                self.calls.queue_response(live, q["id"], **options)
            else:
                self.schedule(session_id, q["id"], **options)

    def schedule(self, session_id: str, qid: str, **options: Any) -> asyncio.Task:
        key = session_id, qid
        task = self.jobs.get(key)
        if task and not task.done() and not task.cancelling():
            return task
        task = self.calls.background(self.respond(session_id, qid, **options))
        self.jobs[key] = task
        task.add_done_callback(
            lambda finished: self.jobs.pop(key, None) if self.jobs.get(key) is finished else None
        )
        return task

    async def resolve(self, session_id: str, qid: str, request: dict) -> dict:
        state = self.service.get(session_id)
        q = next((q for q in state["questions"] if q["id"] == qid), None)
        if not q or q["status"] not in [
            "held",
            "ready",
            "failed",
            "unavailable",
            "awaiting_user",
            "legacy_pending",
        ]:
            raise fail(409, "QUESTION_CHANGED", "问题已处理，请刷新。")
        if request.get("version", q["version"]) != q["version"]:
            raise fail(409, "QUESTION_CHANGED", "问题已更新，请刷新。")
        text = str(request.get("hint", "")).strip()
        if request.get("decision") == "reject":
            q.update(status="rejected", version=q["version"] + 1)
            message = (
                "这项请求目前不能处理。如需继续，请通过获得授权的渠道核实信息。"
                if state.get("language") == "zh-CN"
                else "I can't help with this request. "
                "Please use an authorized channel if you need to continue."
            )
            state["messages"].append(
                {
                    "id": uid(),
                    "type": "screening",
                    "text": message,
                    "status": "completed",
                    "created_at": now(),
                    "question_ids": [qid],
                }
            )
            self.calls.commit(state, "response.refused")
            return {"ok": True}
        if (
            q.get("risk_reason") == "human_evidence_required"
            and not text
            and not CallSearch.requested_query(q["text"])
        ):
            raise fail(400, "DATA_REQUIRED", "请补充已核实的信息，AI 不会猜测真实数据。")
        if text:
            await self.service.guidance.submit(
                session_id,
                {
                    "text": text,
                    "question_id": qid,
                    "kind": "instruction",
                },
                trigger_reply=False,
            )
        await self.correct(session_id, qid)
        return {"ok": True}

    async def correct(
        self,
        session_id: str,
        qid: str,
        live: Any = None,
        *,
        plan: dict | None = None,
        confirm: bool = True,
    ) -> None:
        state = self.service.get(session_id)
        q = next(q for q in state["questions"] if q["id"] == qid)
        if q["status"] in ["withdrawn", "rejected"]:
            return
        live = live or self.calls.live.get(state.get("call", {}).get("id"))
        # Search preparation has no active publisher yet; cancel it too, so an
        # obsolete failure cannot overwrite a reply using newly supplied facts.
        old = live.responses.get(qid) if live else self.jobs.get((session_id, qid))
        if old and old is not asyncio.current_task() and not old.done():
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)
        for run in list(self.runs.values()):
            if run.session_id == session_id and run.question_id == qid:
                await self.cancel_outputs(session_id, run.response_id)
        state = self.service.get(session_id)
        q = next(q for q in state["questions"] if q["id"] == qid)
        expected_version, guidance_epoch = q["version"], self.service.guidance.epoch
        if plan is None:
            wiki, constraints, _ = self.service.guidance.snapshot(session_id, qid)
            try:
                plans = await self.calls.reasoner.plan(
                    q["text"],
                    wiki,
                    constraints + (CONFIRMED_QUESTION if confirm else ""),
                    self.history(state),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state = self.service.get(session_id)
                q = next(q for q in state["questions"] if q["id"] == qid)
                if q["version"] == expected_version:
                    q.update(
                        status="unavailable",
                        version=q["version"] + 1,
                        work={
                            "state": "failed",
                            "tool": "router",
                            "error_code": "routing_unavailable",
                            "error": type(exc).__name__,
                        },
                    )
                    self.calls.commit(state, "response.routing_failed")
                    completed_unknown = any(
                        qid in a.get("question_ids", [])
                        and a.get("status") == "completed"
                        and a.get("text") == UNANSWERABLE_REPLY
                        for a in state["answers"]
                    )
                    if completed_unknown:
                        q["status"] = "answered"
                        self.calls.commit(state, "response.fallback_retained")
                    elif live:
                        self.calls.queue_response(live, qid, candidate=UNANSWERABLE_REPLY)
                    else:
                        self.schedule(session_id, qid, candidate=UNANSWERABLE_REPLY)
                return
            # A correction belongs to the already established question, even
            # if a fresh planner splits its attributes/entities differently.
            # Fold compatible parts back into this scope instead of dropping
            # every plan after the first; policy/approval boundaries stay separate.
            if len(plans) > 1:
                plans = coalesce_requests(q["text"], [
                    {**part, "answer_group": "existing-question", "entity_key": "existing-question"}
                    for part in plans
                ], constraints)
            plan = next(
                (item for item in plans if str(item.get("text", "")).strip() == q["text"]),
                plans[0] if plans else {"text": q["text"], "action": "limited_answer"},
            )
        state = self.service.get(session_id)
        q = next(q for q in state["questions"] if q["id"] == qid)
        if q["version"] != expected_version or self.service.guidance.epoch != guidance_epoch:
            return
        _, constraints, _ = self.service.guidance.snapshot(session_id, qid)
        if confirm and q.get("risk_reason") == "human_confirmation_required":
            plan = {
                **plan,
                "matched_rule_ids": [
                    rule
                    for rule in plan.get("matched_rule_ids", [])
                    if f"[{rule} / require_human" not in constraints
                ],
            }
        action, reason = self.calls.reasoner.policy.decide(plan, constraints)
        if action == "block":
            completed = any(
                qid in a.get("question_ids", [])
                and a.get("reply_kind") == "refusal"
                and a.get("status") == "completed"
                and a.get("text") == PROHIBITION_REPLY
                for a in state["answers"]
            )
            q.update(
                status="answered" if completed else "ready",
                decision="block",
                risk_reason=reason,
                version=q["version"] + 1,
                web_query="",
                use_tools=False,
                retrieval_mode="none",
                work={"state": "finished", "outcome": "refused"},
            )
            q.pop("completion_status", None)
            self.calls.commit(state, "response.refused")
            if not completed and not state.get("automatic_paused"):
                if live:
                    self.calls.queue_response(live, qid, candidate=PROHIBITION_REPLY)
                else:
                    self.schedule(session_id, qid, candidate=PROHIBITION_REPLY)
            return
        facts = [
            r
            for r in self.service.guidance.records(state)
            if r.get("question_id") == qid
            and r["kind"] == "authorized_fact"
            and not r.get("revoked")
            and r.get("effective_from") != "next_turn"
        ]
        source_required = CallSearch.explicit_filing_request(q["text"]) or (
            plan.get("data_scope") == "public" and external_verification_requested(q["text"])
        )
        provided = (
            bool(facts) or plan.get("evidence_status") == "sufficient"
            or (plan.get("evidence_source") == "provided" and plan.get("evidence_quotes"))
        ) and not source_required and action != "lookup"
        q.update(
            status="ready",
            decision=action,
            human_reviewed=confirm or q.get("human_reviewed", False),
            version=q["version"] + 1,
            **{
                k: plan.get(k)
                for k in [
                    "data_scope",
                    "scope_reason",
                    "evidence_status",
                    "evidence_quotes",
                    "evidence_source",
                    "evidence_gap",
                ]
            },
        )
        q.pop("reply_failure", None)
        q.pop("publication_check", None)
        q["retrieval_mode"] = "provided" if provided else "public" if action == "lookup" else "none"
        q["web_query"] = CallSearch.plan_query(plan) if action == "lookup" and not provided else ""
        q["use_tools"] = bool(plan.get("use_tools")) and not provided
        q.pop("completion_status", None)
        if (provided and plan.get("evidence_status") != "sufficient"
                and plan.get("evidence_source") != "provided"):
            q["completion_status"] = "held" if action == "await_human" else "unavailable"
            q["decision"] = "limited_answer"
        if q.get("risk_reason") != "human_confirmation_required":
            q["risk_reason"] = reason
        q["pause_pending"] = state.get("automatic_paused", False)
        call = state.get("call", {})
        live = live or self.calls.live.get(call.get("id"))
        for alert in call.get("alerts", []):
            if alert["question_id"] == qid and q.get("completion_status") != "held":
                alert["status"] = "resolved"
        self.calls.commit(state)
        if q["pause_pending"]:
            return
        options = {"supplemental": True}
        if action == "await_human" and (
            not provided or (reason == "human_confirmation_required" and not confirm)
        ):
            q.update(status="held", assistance_request=assistance_request(q, plan))
            self.calls.commit(state)
            options.update(
                candidate="I still need verified information before I can answer that.", notice=True
            )
        if live:
            self.calls.queue_response(live, qid, **options)
        else:
            self.schedule(session_id, qid, **options)

    async def validate(
        self, run: ReplyRun, unit: str, wiki: str, constraints: str, q: dict
    ) -> None:
        self.ensure(run)
        language = self.service.get(run.session_id).get("language", "en-US")
        if (
            not unit.strip()
            or (language != "zh-CN" and re.search(r"[\u3400-\u9fff]", unit))
            or len(unit) > 1500
        ):
            raise InvalidSegment()
        # A content-free terminal outcome reveals no prohibited information and
        # must never be vetoed by the model that rejected the substantive answer.
        if unit == UNANSWERABLE_REPLY:
            return
        if q.get("decision") == "block" and q.get("risk_reason") == "operator_prohibition":
            # This exact, content-free policy outcome is explicitly allowed even
            # when all discussion of the topic is prohibited. No generated text,
            # facts, progress, or disclosure of the private rule may pass here.
            if unit != PROHIBITION_REPLY or run.text:
                raise InvalidSegment("constraint_rejected")
            return
        if q.get("prohibition_assessment") is not None and " / prohibition / " in constraints:
            # Recheck actual outgoing content: an allowed question can still have
            # a generated answer that introduces a forbidden subject. Conversely,
            # an unrelated rule must not be reconsidered as a blanket veto by the
            # general publication checker after scope was established explicitly.
            content_scope = await self.calls.reasoner.assess_prohibition(
                run.text + " " + unit,
                self.service.guidance.snapshot(run.session_id, run.question_id)[0],
                constraints,
                [{"role": "talker", "text": q["text"]}], publication=True,
            )
            self.ensure(run)
            constraints = self.scoped_constraints(
                run.session_id, constraints, content_scope["matched_rule_ids"]
            )
        restricted = bool(re.search(r"/ (?:prohibition|require_human|instruction) /", constraints))
        legacy_restriction = bool(constraints.strip()) and " / " not in constraints
        if restricted or legacy_restriction:
            # Only explicit/free-form constraints need a targeted semantic check.
            # Ordinary facts, style and public searches do not add a second audit per sentence.
            try:
                async with asyncio.timeout(20):
                    checked = await self.calls.reasoner.check(
                        q["text"], run.text + " " + unit, wiki, constraints
                    )
            except TimeoutError as exc:
                raise InvalidSegment("constraint_check_timeout") from exc
            if checked.get("approved") is not True:
                state = self.service.get(run.session_id)
                current = next(q for q in state["questions"] if q["id"] == run.question_id)
                current["publication_check"] = {
                    "approved": False,
                    "reason": str(checked.get("reason", ""))[:600],
                    "candidate": unit,
                }
                self.calls.commit(state, "response.validation_rejected")
                raise InvalidSegment("constraint_rejected")
        self.ensure(run)

    @staticmethod
    async def deltas(stream, feedback: bool):
        iterator = aiter(stream)
        pending = None
        ticked = not feedback
        try:
            while True:
                pending = asyncio.create_task(anext(iterator))
                if not ticked:
                    done, _ = await asyncio.wait([pending], timeout=1.2)
                    if not done:
                        ticked = True
                        yield None
                try:
                    yield await pending
                except StopAsyncIteration:
                    return
        finally:
            if pending and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            if hasattr(iterator, "aclose"):
                await iterator.aclose()

    async def commit_segment(self, run: ReplyRun, unit: str, kind: str) -> None:
        self.ensure(run)
        state = self.service.get(run.session_id)
        answer = next(a for a in state["answers"] if a["id"] == run.response_id)
        start = len(answer["text"])
        delta = (" " if answer["text"] else "") + unit.strip()
        segment = {
            "segment_id": uid(),
            "segment_seq": run.segment_seq,
            "text": unit.strip(),
            "char_start": start,
            "char_end": start + len(delta),
            "reply_kind": kind,
            "audio_status": "deferred" if run.defer_audio else "queued",
        }
        answer["text"] += delta
        answer["segments"].append(segment)
        message = next(m for m in state["messages"] if m["id"] == run.response_id)
        message.update(text=answer["text"], segments=answer["segments"])
        if kind in ["acknowledgement", "progress"]:
            for source in state["messages"]:
                if source["id"] in next(
                    q for q in state["questions"] if q["id"] == run.question_id
                ).get("source_message_ids", []):
                    source["acknowledged" if kind == "acknowledgement" else "progress_reported"] = (
                        True
                    )
        run.text = answer["text"]
        # No await between latest-version check and durable publication.
        self.calls.commit(state, "response.segment")
        await self.emit(
            run.session_id,
            "response.segment",
            run.live,
            response_id=run.response_id,
            output_generation=run.generation,
            segment_seq=run.segment_seq,
            text_delta=delta,
            reply_kind=kind,
        )
        self.ensure(run)
        if not run.defer_audio:
            await self.enqueue(run, (run.segment_seq, unit, kind))
        run.segment_seq += 1

    async def prepare_group_audio(self, run: ReplyRun) -> None:
        """Publish the canonical grouped card before speaking its matching delta."""
        await self.service.answer_cards.prepare(run.session_id, run.response_id)
        self.ensure(run)
        state = self.service.get(run.session_id)
        question = next(q for q in state["questions"] if q["id"] == run.question_id)
        card = self.service.answer_cards.current_card(state, question)
        parts = card.get("answer_parts", []) if card else []
        speech = spoken_parts(parts, run.previous_parts, state.get("language", "en-US"))
        buffer, units = SentenceBuffer(), []
        for offset in range(0, len(speech), 256):
            units.extend(buffer.push(speech[offset:offset + 256]))
        units.extend(buffer.push("", final=True))
        segments, offset = [], 0
        for seq, unit in enumerate(units):
            segments.append({"segment_id": uid(), "segment_seq": seq, "text": unit,
                             "char_start": offset, "char_end": offset + len(unit),
                             "reply_kind": "supplement" if run.previous_parts else "answer",
                             "audio_status": "queued"})
            offset += len(unit) + 1
        answer = next(a for a in state["answers"] if a["id"] == run.response_id)
        answer.update(generated_segments=answer["segments"], segments=segments, speech_text=speech)
        if card and speech and not run.previous_parts:
            qids = set(card["question_ids"])
            latest = {qid: a for a in state["answers"] for qid in a.get("question_ids", [])
                      if qid in qids and text_ready(a)}
            answer["speech_answer_ids"] = list(dict.fromkeys(a["id"] for a in latest.values()))
        message = next(m for m in state["messages"] if m["id"] == run.response_id)
        message.update(segments=segments, speech_text=speech)
        run.segment_seq = len(segments)
        self.calls.commit(state, "response.speech_ready")
        for segment in segments:
            self.ensure(run)
            await self.enqueue(run, (segment["segment_seq"], segment["text"], segment["reply_kind"]))

    async def enqueue(self, run: ReplyRun, item: Any) -> None:
        if run.audio_task and run.audio_task.done():
            await run.audio_task
            raise InvalidSegment()
        put = asyncio.create_task(run.queue.put(item))
        try:
            finished, _ = await asyncio.wait(
                [put, run.audio_task], return_when=asyncio.FIRST_COMPLETED
            )
            if run.audio_task in finished:
                await run.audio_task
                if not put.done():
                    raise InvalidSegment()
            await put
        finally:
            if not put.done():
                put.cancel()
                await asyncio.gather(put, return_exceptions=True)

    async def audio_worker(self, run: ReplyRun) -> None:
        speech = self.calls.speech
        if not speech.available():
            if run.began:
                run.began.set()
            # Text remains usable; do not block every question behind absent local models.
            while (item := await run.queue.get()) is not None:
                self.audio_state(run, item[0], "unavailable")
            return
        await speech.prepare()
        # Synthesis and paced transmission are separate bounded stages. The next
        # sentence is prepared while the current one is still being played.
        ready: asyncio.Queue = asyncio.Queue(maxsize=1)

        async def synthesize() -> None:
            while (item := await run.queue.get()) is not None:
                seq, text, kind = item
                self.ensure(run)
                self.audio_state(run, seq, "synthesizing")
                try:
                    # Validated text is already available. TTS gets its own
                    # bounded preparation budget, even if routing/search used
                    # the foreground target. Latency is still measured honestly.
                    async with asyncio.timeout(10):
                        try:
                            pcm, rate = await speech.synthesize(text)
                        except (ValueError, RuntimeError):
                            self.ensure(run)
                            pcm, rate = await speech.synthesize(text)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.audio_state(run, seq, "failed")
                    await self.emit(
                        run.session_id,
                        "audio.failed",
                        run.live,
                        response_id=run.response_id,
                        message="本段语音未完成，文字已保留。",
                    )
                    continue
                self.ensure(run)
                await ready.put((seq, kind, pcm, rate))
            await ready.put(None)

        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(synthesize())
                while (item := await ready.get()) is not None:
                    await self.send_audio_segment(run, *item)
                await asyncio.sleep(
                    max(0, run.audio_clock + run.sent_audio_seconds - time.monotonic())
                )
        except* ContextChanged:
            raise ContextChanged() from None

    async def send_audio_segment(
        self, run: ReplyRun, seq: int, kind: str, pcm: bytes, rate: int
    ) -> None:
        self.ensure(run)
        run.audio_rate = rate
        await self.emit(
            run.session_id,
            "audio.segment_start",
            run.live,
            response_id=run.response_id,
            output_generation=run.generation,
            stream_id=run.stream_id,
            segment_seq=seq,
            sample_rate=rate,
        )
        self.audio_state(run, seq, "sending")
        if run.live:
            state, call = self.calls.current(run.session_id, run.live.call_id)
            call.update(
                status="speaking",
                response_id=run.response_id,
                active_question_id=run.question_id,
            )
            self.calls.commit(state)
            run.live.preparing.discard(run.question_id)
        frame_bytes = int(rate * 0.04) * 2
        now = time.monotonic()
        # One clock spans sentence boundaries. A small lead covers scheduling and
        # network jitter without accumulating another buffer per sentence.
        if not run.audio_clock or now > run.audio_clock + run.sent_audio_seconds + 0.15:
            run.audio_clock = now - run.sent_audio_seconds
        for frame, offset in enumerate(range(0, len(pcm), frame_bytes)):
            self.ensure(run)
            data = pcm[offset : offset + frame_bytes]
            await asyncio.sleep(
                max(0, run.audio_clock + run.sent_audio_seconds - 0.12 - time.monotonic())
            )
            self.ensure(run)
            header = struct.pack(
                "<4sIIIIQHH",
                b"DVA4",
                run.generation,
                run.stream_id,
                seq,
                frame,
                offset // 2,
                len(data) // 2,
                0,
            )
            if run.live and run.live.socket:
                async with run.live.send_lock:
                    self.ensure(run)
                    await run.live.socket.send_bytes(header + data)
            else:
                self.broadcast(run.session_id, header + data)
            run.sent_audio_seconds += len(data) / (rate * 2)
            if not run.first_audio_at:
                run.first_audio_at = time.monotonic()
                if run.began:
                    run.began.set()
                if run.live:
                    current, call = self.calls.current(run.session_id, run.live.call_id)
                    call["last_latency_ms"] = round((run.first_audio_at - run.started) * 1000)
                    self.calls.commit(current)
            if kind not in ["acknowledgement", "progress"] and not run.first_substantive_at:
                run.first_substantive_at = time.monotonic()
        run.pcm.extend(pcm)
        self.audio_state(run, seq, "sent", len(pcm) // 2)
        await self.emit(
            run.session_id,
            "audio.segment_end",
            run.live,
            response_id=run.response_id,
            output_generation=run.generation,
            stream_id=run.stream_id,
            segment_seq=seq,
            final_sample_count=len(pcm) // 2,
        )

    def audio_state(self, run: ReplyRun, seq: int, status: str, samples: int = 0) -> None:
        state = self.service.get(run.session_id)
        for item in state["messages"] + state["answers"]:
            if item["id"] != run.response_id or item["status"] == "withdrawn":
                continue
            for segment in item.get("segments", []):
                if segment["segment_seq"] == seq:
                    segment["audio_status"] = status
                    if samples:
                        segment["sample_count"] = samples
        self.calls.commit(state, "audio.segment_state")

    async def respond(
        self,
        session_id: str,
        qid: str,
        live: Any = None,
        *,
        candidate: str = "",
        notice: bool = False,
        supplemental: bool = False,
        started: float = 0,
        deadline: float = 0,
    ) -> None:
        started = started or time.monotonic()
        deadline = deadline or started + 10
        lock = live.output_lock if live else self.output_locks[session_id]
        evidence = ""
        if live:
            live.preparing.add(qid)

        async def publish(text="", informational=False, kind="answer", began=None):
            nonlocal deadline
            queued = time.monotonic()
            initial = None
            first = None
            prepared_signature = ""
            if not text and not informational:
                w, c, prepared_signature = self.service.guidance.snapshot(session_id, qid)
                question = next(
                    q for q in self.service.get(session_id)["questions"] if q["id"] == qid
                )
                if question.get("prohibition_assessment") is not None:
                    c = self.scoped_constraints(
                        session_id, c,
                        question["prohibition_assessment"]["matched_rule_ids"],
                    )
                if (
                    question.get("human_reviewed")
                    and question.get("risk_reason") == "human_confirmation_required"
                ):
                    c += CONFIRMED_QUESTION
                prompt = (
                    "Briefly follow up on this earlier question: " if supplemental else ""
                ) + question["text"]
                try:
                    initial = self.calls.reasoner.answer_stream(
                        prompt,
                        w + evidence,
                        c,
                        self.history(self.service.get(session_id)),
                        context=self.answer_context(
                            self.service.get(session_id), question, evidence
                        ),
                        on_tool=self.record_tool_trace(session_id, qid),
                    )
                except TypeError as error:
                    if "unexpected keyword" not in str(error):
                        raise
                    initial = self.calls.reasoner.answer_stream(
                        prompt,
                        w + evidence,
                        c,
                        self.history(self.service.get(session_id)),
                    )
                first = asyncio.create_task(anext(initial))

            async def prepared():
                try:
                    yield await first
                    async for delta in initial:
                        yield delta
                except StopAsyncIteration:
                    return

            try:
                async with lock:
                    if live:
                        while self.calls.live.get(live.call_id) is live and (
                            live.turn.id
                            or live.paused
                            or (live.intake_work and not live.intake_work.done())
                        ):
                            live.changed.clear()
                            await live.changed.wait()
                        if self.calls.live.get(live.call_id) is not live:
                            return
                        live.output_work = asyncio.current_task()
                    deadline += time.monotonic() - queued
                    for attempt in range(3):
                        try:
                            stream = (
                                prepared()
                                if first
                                and prepared_signature
                                == self.service.guidance.snapshot(session_id, qid)[2]
                                and not attempt
                                else None
                            )
                            await self._respond(
                                session_id,
                                qid,
                                live,
                                text,
                                informational,
                                supplemental,
                                started,
                                deadline,
                                evidence,
                                kind,
                                began,
                                stream,
                            )
                            return
                        except ContextChanged:
                            if attempt:
                                return
                            text = "" if not informational else text
                            deadline = time.monotonic() + max(
                                0.1, min(5, deadline - time.monotonic())
                            )
            finally:
                if first and not first.done():
                    first.cancel()
                if first:
                    await asyncio.gather(first, return_exceptions=True)
                if initial and hasattr(initial, "aclose"):
                    await initial.aclose()

        cue_task = None
        try:
            state = self.service.get(session_id)
            question = next(q for q in state["questions"] if q["id"] == qid)
            if question.get("decision") == "block":
                if question["status"] == "blocked":
                    question["status"] = "ready"
                    self.calls.commit(state, "response.refusal_ready")
                if question["status"] not in {"withdrawn", "rejected", "interrupted"}:
                    await publish(PROHIBITION_REPLY, False, "refusal")
                return
            if question["status"] in {"blocked", "withdrawn", "rejected", "interrupted"}:
                return
            query = (
                (question.get("web_query")
                 or (CallSearch.plan_query({**question, "action": "lookup"})
                     if CallSearch.needs_public_completion(question)
                     else CallSearch.requested_query(question["text"])))
                if not candidate
                and (
                    CallSearch.explicit_filing_request(question["text"])
                    or (question.get("data_scope") == "public"
                        and external_verification_requested(question["text"]))
                    or CallSearch.needs_public_completion(question)
                    or (
                        question.get("retrieval_mode") != "provided"
                        and question.get("data_scope") not in {"internal", "unknown"}
                        and question.get("evidence_status") != "sufficient"
                    )
                )
                else ""
            )
            documents = question.get("document_urls", []) if not candidate else []
            if question.get("use_tools") and not candidate:
                deadline = max(deadline, time.monotonic() + 45)
            if query or documents:
                caller = next(
                    (
                        m["text"]
                        for m in state["messages"]
                        if m["id"] in question.get("source_message_ids", [])
                    ),
                    "",
                )
                if query and not self.calls.search.allowed(query, caller, "routine"):
                    candidate = "I can't look up private identifiers through a public search."
                else:
                    if live:
                        began = asyncio.Event()
                        cue_task = asyncio.create_task(
                            publish("Let me check that.", True, "acknowledgement", began)
                        )
                        ready = asyncio.create_task(began.wait())
                        try:
                            await asyncio.wait(
                                [ready, cue_task], return_when=asyncio.FIRST_COMPLETED
                            )
                        finally:
                            ready.cancel()
                        if cue_task.done():
                            await cue_task
                    # Review-chat lookups publish an evidence-grounded reply directly;
                    # live calls still acknowledge before their slower tool turn.
                    current = self.service.get(session_id)
                    q = next(q for q in current["questions"] if q["id"] == qid)
                    q["work"] = {
                        "id": uid(),
                        "state": "running",
                        "started_at": now(),
                        "tool": "lark_doc_read" if documents else "web_search",
                    }
                    if not notice:
                        q["status"] = "generating"
                    self.calls.commit(current, "work.started")

                    async def lookup():
                        if not documents:
                            return await self.calls.search.lookup(query)
                        config = load_tool_config().model_copy(
                            update={
                                "bash_enabled": False,
                                "timeout_seconds": 25,
                                "max_output_chars": 12000,
                            }
                        )
                        async with ToolRuntime(config) as runtime:
                            body, sources, errors = await read_documents(runtime, documents)
                        if not sources:
                            raise ValueError(
                                errors[0]["message"] if errors else "Document reading failed."
                            )
                        return body, sources

                    job = asyncio.create_task(lookup())
                    lookup_deadline = time.monotonic() + 75
                    try:
                        evidence, sources = await asyncio.wait_for(
                            job, max(0.01, lookup_deadline - time.monotonic())
                        )
                        current = self.service.get(session_id)
                        q = next(q for q in current["questions"] if q["id"] == qid)
                        q["work"]["state"] = "result_ready"
                        q["work"]["sources"] = sources
                        if CallSearch.needs_public_completion(q):
                            # Receiving sources is not proof that every metric
                            # was found. Preserve supplied facts and their gaps.
                            q["retrieval_mode"] = "mixed"
                        else:
                            q.update(evidence_status="sufficient", evidence_source="tool")
                        self.calls.commit(current, "work.finished")
                    except asyncio.CancelledError:
                        job.cancel()
                        await asyncio.gather(job, return_exceptions=True)
                        raise
                    except Exception as exc:
                        job.cancel()
                        await asyncio.gather(job, return_exceptions=True)
                        current = self.service.get(session_id)
                        q = next(q for q in current["questions"] if q["id"] == qid)
                        q["work"].update(state="failed", error=type(exc).__name__)
                        if isinstance(exc, PublicSearchError):
                            q["work"].update(error_code=exc.code, attempts=exc.attempts)
                        has_supplied_facts = bool(q.get("evidence_source") == "provided"
                                                  and q.get("evidence_quotes"))
                        q["status"] = "generating" if has_supplied_facts else "unavailable"
                        notice = not has_supplied_facts
                        if has_supplied_facts:
                            q["retrieval_mode"] = "provided"
                        self.calls.commit(current, "work.failed")
                        candidate = "" if has_supplied_facts else UNANSWERABLE_REPLY
                    if cue_task:
                        await cue_task
                    if time.monotonic() >= deadline:
                        # The lookup gets a preparation budget; latency origin stays fixed.
                        deadline = time.monotonic() + 45
                        supplemental = True
                    else:
                        deadline = max(deadline, time.monotonic() + 45)
            if candidate == UNANSWERABLE_REPLY:
                current = self.service.get(session_id)
                item = next(q for q in current["questions"] if q["id"] == qid)
                item.pop("completion_status", None)
                self.calls.commit(current, "response.fallback_ready")
                notice = False
            await publish(candidate, notice)
        except asyncio.CancelledError:
            raise
        finally:
            if cue_task and not cue_task.done():
                cue_task.cancel()
                await asyncio.gather(cue_task, return_exceptions=True)
            if live:
                live.preparing.discard(qid)
                if live.output_work is asyncio.current_task():
                    live.output_work = None
                self.calls.settle(live)

    async def _respond(
        self,
        sid: str,
        qid: str,
        live: Any,
        candidate: str,
        notice: bool,
        supplemental: bool,
        started: float,
        deadline: float,
        evidence: str = "",
        kind: str = "answer",
        began: asyncio.Event | None = None,
        prepared_stream: Any = None,
    ) -> None:
        state = self.service.get(sid)
        q = next(q for q in state["questions"] if q["id"] == qid)
        if q.get("decision") == "block":
            if any(
                qid in a.get("question_ids", [])
                and a.get("reply_kind") == "refusal"
                and a.get("status") == "completed"
                and a.get("text") == PROHIBITION_REPLY
                for a in state["answers"]
            ):
                return
            candidate, notice, supplemental, kind = PROHIBITION_REPLY, False, False, "refusal"
        sources = [m for m in state["messages"] if m["id"] in q.get("source_message_ids", [])]
        acknowledged = any(m.get("acknowledged") for m in sources)
        if kind == "acknowledgement" and acknowledged:
            return
        if kind == "progress" and any(m.get("progress_reported") for m in sources):
            return
        if q["status"] in ["withdrawn", "blocked", "rejected", "interrupted"] or state.get(
            "automatic_paused"
        ):
            return
        if not live and state.get("interaction_mode") == "realtime_call":
            return
        wiki, constraints, signature = self.service.guidance.snapshot(sid, qid)
        wiki += evidence
        if q.get("human_reviewed") and q.get("risk_reason") == "human_confirmation_required":
            constraints += CONFIRMED_QUESTION
        source_ids = set(q.get("source_message_ids", []))
        siblings = [item for item in state["questions"]
                    if source_ids.intersection(item.get("source_message_ids", []))]
        grouped = (kind == "answer" and q.get("decision") not in {"block", "clarify"}
                   and (multipart_question(q) or len(siblings) > 1))
        prior_card = self.service.answer_cards.current_card(state, q) if grouped else None
        self.stream_id += 1
        run = ReplyRun(
            sid,
            qid,
            uid(),
            live.epoch if live else self.stream_id,
            self.stream_id,
            live=live,
            signature=signature,
            marker=self.marker(),
            started=started,
            task=asyncio.current_task(),
            deadline=deadline,
            began=began,
            defer_audio=grouped,
            previous_parts=prior_card.get("answer_parts", []) if prior_card else [],
        )
        self.runs[run.response_id] = run
        answer = {
            "id": run.response_id,
            "question_ids": [qid],
            "text": "",
            "status": "streaming",
            "public_stream": True,
            "output_generation": run.generation,
            "segments": [],
            "created_at": now(),
            "published_at": now(),
            "revision": 1,
            "human_reviewed": q.get("human_reviewed", False),
            "refusal": kind == "refusal",
            "delivery": "speaking",
            "reply_kind": kind if kind != "answer" else "supplement" if supplemental else "answer",
            "reply_to_message_id": next(iter(q.get("source_message_ids", [])), None),
            "notice": notice,
            "text_done": False,
            "text_complete": False,
            "parts_status": "pending" if kind not in {"acknowledgement", "progress"} else "control",
            "audio_done": False,
            "tts_audio": {"id": run.response_id, "kind": "stream", "status": "streaming"},
        }
        if live:
            answer["call_id"] = live.call_id
            state["call"].update(
                status="thinking", response_id=run.response_id, active_question_id=qid
            )
        state["answers"].append(answer)
        state["messages"].append({**answer, "type": "ai"})
        if not notice and q["status"] != "unavailable":
            q["status"] = "generating"
        self.calls.commit(state, "response.started")
        await self.emit(
            sid,
            "response.started",
            live,
            response_id=run.response_id,
            output_generation=run.generation,
            question_ids=[qid],
        )
        run.audio_task = asyncio.create_task(self.audio_worker(run))
        failed = False
        generated_at = 0.0
        try:
            if kind in ["acknowledgement", "progress"]:
                q = {**q, "web_query": "", "uses_wiki_facts": False}
            if supplemental:
                q = {**q, "text": "Briefly follow up on this earlier question: " + q["text"]}
            buffer = SentenceBuffer()
            produced = False
            if candidate:

                async def content():
                    yield candidate

                stream = content()
            else:
                if prepared_stream:
                    stream = prepared_stream
                else:
                    stream = await self.calls.reasoner.answer_graph.stream(
                        self,
                        session_id=sid,
                        question_id=qid,
                        question=q,
                        wiki=wiki,
                        constraints=constraints,
                        session_state=state,
                        evidence=evidence,
                        on_tool=self.record_tool_trace(sid, qid),
                    )
            async with asyncio.timeout(max(0.01, deadline - time.monotonic())) as timeout:

                async def publish_segment(unit: str, segment_kind: str, question: dict) -> None:
                    # This timer bounds model waiting, not a validated reply's
                    # paced audio queue. Checks and TTS have their own bounds.
                    loop = asyncio.get_running_loop()
                    remaining = max(0.01, timeout.when() - loop.time())
                    timeout.reschedule(None)
                    try:
                        await self.validate(run, unit, wiki, constraints, question)
                        await self.commit_segment(run, unit, segment_kind)
                    finally:
                        timeout.reschedule(loop.time() + remaining)

                ack_sent = False
                generation_started = time.monotonic()
                async for delta in self.deltas(
                    stream, bool(live and not candidate and q.get("decision") != "clarify")
                ):
                    self.ensure(run)
                    units = buffer.push(delta) if delta is not None else []
                    if (
                        not produced
                        and not units
                        and buffer.pending
                        and (live or self.subscribers[sid])
                    ):
                        units = [buffer.pending]
                        buffer.pending = ""
                    if (
                        not produced
                        and not units
                        and not ack_sent
                        and not acknowledged
                        and live
                        and not run.defer_audio
                        and not buffer.pending
                        and (delta is None or time.monotonic() - generation_started > 1.4)
                    ):
                        cue = "One moment."
                        await publish_segment(
                            cue,
                            "acknowledgement",
                            {**q, "web_query": "", "uses_wiki_facts": False},
                        )
                        ack_sent = True
                    if delta is None:
                        continue
                    for unit in units:
                        await publish_segment(
                            unit,
                            kind
                            if kind != "answer"
                            else "supplement"
                            if supplemental
                            else "answer",
                            q,
                        )
                        if not produced:
                            # First useful segment ends foreground thinking, not the whole answer.
                            timeout.reschedule(asyncio.get_running_loop().time() + 15)
                        produced = True
                for unit in buffer.push("", final=True):
                    await publish_segment(
                        unit,
                        kind if kind != "answer" else "supplement" if supplemental else "answer",
                        q,
                    )
                    produced = True
            if not produced:
                raise InvalidSegment()
            generated_at = time.monotonic()
            current = self.service.get(sid)
            for item in current["messages"] + current["answers"]:
                if item["id"] == run.response_id and item["status"] != "withdrawn":
                    item.update(text_done=True, text_complete=True)
            self.calls.commit(current, "response.text_done")
            if run.defer_audio:
                await self.prepare_group_audio(run)
            elif kind not in {"acknowledgement", "progress"}:
                self.service.answer_cards.schedule(sid, run.response_id)
            await self.emit(
                sid,
                "response.text_done",
                live,
                response_id=run.response_id,
                output_generation=run.generation,
            )
            await self.enqueue(run, None)
            # Text replies with active UI subscribers can finish before speech playback;
            # unattended/native synthesis waits so guidance can interrupt stale audio.
            if live or not self.subscribers[sid]:
                await run.audio_task
                self.ensure(run)
        except ContextChanged:
            failed = True
            await self.emit(
                sid,
                "response.interrupted",
                live,
                response_id=run.response_id,
                output_generation=run.generation,
                reason_code="guidance_changed",
            )
            raise
        except asyncio.CancelledError:
            failed = True
            if run.context_changed:
                asyncio.current_task().uncancel()
                await self.emit(
                    sid,
                    "response.interrupted",
                    live,
                    response_id=run.response_id,
                    output_generation=run.generation,
                    reason_code="guidance_changed",
                )
                raise ContextChanged() from None
            await self.emit(
                sid,
                "response.interrupted",
                live,
                response_id=run.response_id,
                output_generation=run.generation,
                reason_code="stopped",
            )
            raise
        except Exception as exc:
            failed = True
            logger.warning(
                "response_partial_failure error=%s reason=%s",
                type(exc).__name__,
                str(exc) if isinstance(exc, (InvalidSegment, ValueError)) else "",
            )
            current = self.service.get(sid)
            question = next(x for x in current["questions"] if x["id"] == qid)
            question["reply_failure"] = {"error": type(exc).__name__, "at": now()}
            for audit in current["answers"]:
                if audit["id"] == run.response_id:
                    audit["reply_failure"] = dict(question["reply_failure"])
            self.calls.commit(current, "response.fallback")
            if generated_at:
                # A transport/audio failure after validated text completion must
                # not append a contradictory unknown or discard that answer.
                failed = False
            elif kind not in {"acknowledgement", "progress"}:
                self.ensure(run)
                # Complete the same response once, without generating another
                # answer or asking the rejecting model to approve the fallback.
                # Preserve any text already delivered before the failure.
                try:
                    if run.audio_task.done():
                        await asyncio.gather(run.audio_task, return_exceptions=True)
                        run.audio_task = asyncio.create_task(self.audio_worker(run))
                    await self.commit_segment(run, UNANSWERABLE_REPLY, "limitation")
                    if run.defer_audio:
                        completed_text = self.service.get(sid)
                        for item in completed_text["messages"] + completed_text["answers"]:
                            if item["id"] == run.response_id and item["status"] != "withdrawn":
                                item.update(text_done=True, text_complete=True)
                        self.calls.commit(completed_text, "response.text_done")
                        await self.prepare_group_audio(run)
                    await self.enqueue(run, None)
                    await run.audio_task
                except (ContextChanged, asyncio.CancelledError):
                    raise
                except Exception as audio_exc:
                    self.ensure(run)
                    logger.warning("fallback_audio_unavailable error=%s", type(audio_exc).__name__)
                    # Text is a completed outcome even when speech is unavailable.
                    if not run.text.endswith(UNANSWERABLE_REPLY):
                        for item in current["answers"] + current["messages"]:
                            if item["id"] == run.response_id:
                                item["text"] = (item["text"] + " " + UNANSWERABLE_REPLY).strip()
                                run.text = item["text"]
                failed = False
                notice = False
                question.pop("completion_status", None)
                generated_at = time.monotonic()
        finally:
            if run.audio_task and not run.audio_task.done() and not (not failed and not live):
                run.audio_task.cancel()
                # Native TTS retains its own lock until inference exits; capture never awaits it.
                self.calls.background(self._discard(run.audio_task))
            elif run.audio_task and not run.audio_task.done():
                self.calls.background(self._discard(run.audio_task))
            current = self.service.get(sid)
            for item in current["messages"] + current["answers"]:
                if item["id"] == run.response_id and item["status"] != "withdrawn":
                    item.update(
                        status="interrupted" if failed else "completed",
                        text_done=True,
                        text_complete=bool(generated_at),
                        audio_done=not failed,
                        delivery="interrupted" if failed else "sent",
                    )
                    if failed:
                        item["error"] = (
                            "回复已停止。" if run.text else UNANSWERABLE_REPLY
                        )
                        if not run.text and kind in {"acknowledgement", "progress"}:
                            item.update(status="cancelled", error=None)
                    item["metrics"] = {
                        "first_audio_ms": round((run.first_audio_at - started) * 1000)
                        if run.first_audio_at
                        else None,
                        "first_substantive_ms": round((run.first_substantive_at - started) * 1000)
                        if run.first_substantive_at
                        else None,
                        "model_done_ms": round((generated_at - started) * 1000)
                        if generated_at
                        else None,
                    }
            question = next(x for x in current["questions"] if x["id"] == qid)
            if not notice and question["status"] not in [
                "withdrawn",
                "blocked",
                "rejected",
                "unavailable",
                "held",
                "interrupted",
            ]:
                question["status"] = (
                    question.get("completion_status") or "unavailable"
                    if failed
                    else question.get("completion_status")
                    or ("awaiting_user" if q.get("decision") == "clarify" else "answered")
                )
            if (
                live
                and self.calls.live.get(live.call_id) is live
                and current["call"].get("response_id") == run.response_id
                and current["call"].get("active_question_id") == qid
            ):
                current["call"]["status"] = "listening"
            self.calls.commit(current, "response.finished")
            completed = next(a for a in current["answers"] if a["id"] == run.response_id)
            audio_complete = all(
                s.get("audio_status") in ["sent", "played"] for s in completed.get("segments", [])
            )
            if run.pcm and run.audio_rate and not failed and audio_complete:
                self.archive(run)
            elif not failed:
                state = self.service.get(sid)
                for item in state["answers"] + state["messages"]:
                    if item["id"] == run.response_id and item["status"] != "withdrawn":
                        asset = {
                            "id": run.response_id,
                            "kind": "stream",
                            "status": "browser",
                            "language": "en-US",
                            "spoken_text": item.get("speech_text", item["text"]),
                        }
                        item["tts_audio" if "type" not in item else "audio"] = asset
                self.calls.commit(state, "audio.unavailable")
            await self.emit(
                sid,
                "audio.response_end",
                live,
                response_id=run.response_id,
                output_generation=run.generation,
                last_segment_seq=run.segment_seq - 1,
            )
            self.runs.pop(run.response_id, None)
            if generated_at and kind not in {"acknowledgement", "progress"}:
                self.service.answer_cards.schedule(sid, run.response_id)
            self.service.spawn(
                asyncio.to_thread(self.service.wiki_for(current).sync_session, current)
            )
            if not failed and not notice:
                self.release_dependencies(sid, live)
                self.service.spawn(self.service.remember_completed_reply(sid, run.response_id))

    @staticmethod
    async def _discard(task: asyncio.Task) -> None:
        await asyncio.gather(task, return_exceptions=True)

    def archive(self, run: ReplyRun) -> None:
        destination = self.service.audio_dir / (run.response_id + ".wav")
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(run.audio_rate)
            output.writeframes(run.pcm)
        state = self.service.get(run.session_id)
        for item in state["answers"] + state["messages"]:
            if item["id"] == run.response_id and item["status"] != "withdrawn":
                audio = {
                    "id": run.response_id,
                    "kind": "stream",
                    "status": "ready",
                    "filename": destination.name,
                    "language": "en-US",
                }
                item["tts_audio" if "type" not in item else "audio"] = audio
        self.calls.commit(state, "audio.archived")

    async def ack(self, session_id: str, data: dict) -> None:
        state = self.service.get(session_id)
        answer = next((a for a in state["answers"] if a["id"] == data.get("response_id")), None)
        if not answer or answer["status"] == "withdrawn":
            return
        if data.get("output_generation") != answer.get("output_generation"):
            return
        if data.get("type") == "playout.started":
            answer.setdefault("playback_started_at", now())
            self.calls.commit(state, "response.playback_started")
            return
        seq = data.get("segment_seq")
        segment = next((s for s in answer.get("segments", []) if s["segment_seq"] == seq), None)
        if (
            not segment
            or not data.get("complete")
            or segment.get("audio_status") not in ["sent", "played"]
        ):
            return
        if data.get("played_sample_offset") != segment.get("sample_count"):
            return
        segment["audio_status"] = "played"
        spoken = []
        for item in answer["segments"]:
            if item.get("audio_status") != "played":
                break
            spoken.append(item["text"])
        delivered = " ".join(spoken)
        answer["delivered_text"] = delivered
        message = next(m for m in state["messages"] if m["id"] == answer["id"])
        message.update(delivered_text=delivered, segments=answer["segments"])
        if answer.get("text_done") and len(spoken) == len(answer["segments"]):
            answer["delivery"] = message["delivery"] = "delivered"
            # A first grouped narration may include several previously buffered
            # question attempts. Credit delivery only after that full narration
            # has actually played, and never credit a superseded/withdrawn one.
            for source_id in answer.get("speech_answer_ids", []):
                source = next((a for a in state["answers"] if a["id"] == source_id), None)
                if not source or not text_ready(source) or source.get("status") != "completed":
                    continue
                qids = set(source.get("question_ids", []))
                latest = [a for a in state["answers"] if qids.intersection(a.get("question_ids", []))
                          and a.get("reply_kind") not in {"acknowledgement", "progress"}
                          and a.get("status") != "cancelled"]
                if not latest or latest[-1]["id"] != source_id or source_id == answer["id"]:
                    continue
                source.update(delivery="delivered", delivered_text=" ".join(
                    p["answer"] for p in source.get("answer_parts", [])
                ) or source["text"])
                source_message = next((m for m in state["messages"] if m["id"] == source_id), None)
                if source_message:
                    source_message.update(delivery="delivered", delivered_text=source["delivered_text"])
                self.service.spawn(self.service.remember_completed_reply(session_id, source_id))
        self.calls.commit(state, "response.played")
        if answer.get("call_id"):
            self.service.spawn(
                asyncio.to_thread(self.service.wiki_for(session_id).sync_session, state)
            )
            if answer.get("delivery") == "delivered" and answer.get("status") == "completed":
                self.service.spawn(self.service.remember_completed_reply(session_id, answer["id"]))

    def release_dependencies(self, session_id: str, live: Any) -> None:
        state = self.service.get(session_id)
        done = {q["id"] for q in state["questions"] if q["status"] == "answered"}
        ready = []
        for q in state["questions"]:
            if q.get("awaiting_dependencies") and set(q.get("depends_on", [])).issubset(done):
                q["awaiting_dependencies"] = False
                if q["status"] != "held":
                    ready.append(q["id"])
        if ready:
            self.calls.commit(state)
        for qid in ready:
            if live:
                self.calls.queue_response(live, qid, supplemental=True)
            else:
                self.schedule(session_id, qid, supplemental=True)
