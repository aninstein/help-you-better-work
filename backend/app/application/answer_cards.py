"""One durable presentation per caller input, with independently replaceable parts.

Transport attempts stay in the audit store. Single answers keep their full
validated text; grouped answers settle into parts and revise the same card.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.application.service import SessionService

logger = logging.getLogger(__name__)
CONTROL_KINDS = {"acknowledgement", "progress"}
KNOWN = {"known", "partial"}
PARTS_VERSION = 2


def multipart_question(question: dict) -> bool:
    return max(len(question.get("subrequests", [])), len(question.get("answer_parts", []))) > 1


def direct_parts(question: dict, answer: dict) -> list[dict]:
    parts = fallback_parts(question, answer)
    previous = question.get("answer_parts", [])
    if len(previous) == 1:
        parts[0].update(id=previous[0]["id"], question=previous[0]["question"])
    elif len(question.get("subrequests", [])) == 1:
        parts[0]["question"] = question["subrequests"][0]
    return parts


def spoken_parts(parts: list[dict], previous: list[dict], language: str = "en-US") -> str:
    """Read a first grouped answer fully; announce only changed items later."""
    old = {p["id"]: p for p in previous}
    changed = [p for p in parts if not old or p["id"] not in old
               or (p["answer"], p["status"]) != (old[p["id"]]["answer"], old[p["id"]]["status"])]
    if not changed:
        return ""
    shared: dict[str, list[str]] = {}
    for part in changed:
        shared.setdefault(part["answer"], []).append(part["question"])
    body = " ".join(f"{', '.join(labels)}: {answer}" for answer, labels in shared.items())
    if not previous:
        return body
    return ("哦，我更新了刚才的信息。" if language == "zh-CN"
            else "Oh, I've updated the information from earlier. ") + body


def text_ready(answer: dict) -> bool:
    """Validated text completion is independent of paced audio delivery.

    Older attempts marked text_done even on cancellation, so that flag alone
    cannot prove that the model finished. model_done_ms is the legacy marker.
    """
    if (answer.get("status") not in {"completed", "streaming", "interrupted"}
            or answer.get("reply_kind") in CONTROL_KINDS):
        return False
    if "text_complete" in answer:
        return bool(answer["text_complete"])
    return (answer.get("status") == "completed"
            or (answer.get("text_done")
                and answer.get("metrics", {}).get("model_done_ms") is not None))


def card_question_ids(state: dict, card_id: str) -> list[str]:
    if not card_id.startswith("reply-"):
        return []
    source = card_id.removeprefix("reply-")
    return [q["id"] for q in state["questions"] if source in q.get("source_message_ids", [])]


def fallback_parts(question: dict, answer: dict) -> list[dict]:
    text = answer.get("text", "").strip()
    status = "known"
    if question.get("decision") == "block" or answer.get("refusal"):
        text, status = "I don't know.", "refused"
    elif answer.get("notice") or not text or text == "I don't know.":
        text, status = "I don't know.", "unknown"
    elif question.get("decision") == "clarify":
        status = "clarify"
    elif question.get("evidence_status") == "partial":
        status = "partial"
    return [{"id": question["id"] + ":1", "question_id": question["id"],
             "question": question["text"], "answer": text, "status": status}]


def merge_parts(previous: list[dict], incoming: list[dict], *, refuse: bool = False) -> list[dict]:
    """An unrelated missing field cannot erase an already supplied answer."""
    if refuse:
        return [{**p, "answer": "I don't know.", "status": "refused", "updated": True}
                for p in previous or incoming]
    old = {p["id"]: p for p in previous}
    updates = {p["id"]: p for p in incoming}
    result = []
    for part in previous:
        new = updates.pop(part["id"], None)
        if new is None or (
            new.get("status") == "unknown" and part.get("status") in KNOWN
            and not new.get("replace")
        ):
            result.append(dict(part))
        else:
            result.append({**new, "question": part["question"],
                           "updated": new["answer"] != part["answer"]})
    for part in incoming:
        if part["id"] not in old:
            result.append({**part, "updated": False})
    return result


class AnswerCards:
    def __init__(self, service: SessionService) -> None:
        self.service = service
        self.formatting: set[tuple[str, str]] = set()
        self.pending: dict[tuple[str, str], asyncio.Future] = {}
        self.limit = asyncio.Semaphore(2)

    def schedule(self, session_id: str, answer_id: str) -> asyncio.Future | None:
        key = session_id, answer_id
        if key in self.formatting:
            return self.pending.get(key)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.formatting.add(key)
        done = loop.create_future()
        self.pending[key] = done

        async def format_and_signal():
            try:
                await self.format_answer(session_id, answer_id)
            finally:
                if not done.done():
                    done.set_result(None)
                self.pending.pop(key, None)

        self.service.spawn(format_and_signal())
        return done

    async def prepare(self, session_id: str, answer_id: str) -> None:
        done = self.schedule(session_id, answer_id)
        if done is not None:
            await asyncio.shield(done)

    def current_card(self, state: dict, question: dict) -> dict | None:
        source = next(iter(question.get("source_message_ids", [])), "")
        result = {"messages": [dict(m) for m in state["messages"]], "answers": []}
        self.project(state, result, schedule_pending=False)
        return next((m for m in result["messages"] if m["id"] == "reply-" + source), None)

    async def format_answer(self, session_id: str, answer_id: str) -> None:
        try:
            state = self.service.get(session_id)
            answer = next((a for a in state["answers"] if a["id"] == answer_id), None)
            if (not answer or not text_ready(answer)
                    or answer.get("reply_kind") in CONTROL_KINDS
                    or (answer.get("parts_status") == "ready"
                        and answer.get("parts_version") == PARTS_VERSION)):
                return
            q = next((q for q in state["questions"] if q["id"] in answer.get("question_ids", [])), None)
            if not q:
                return
            version = q["version"]
            previous = q.get("answer_parts", [])
            parts = fallback_parts(q, answer)
            unknown = answer.get("notice") or answer.get("text", "").strip() == "I don't know."
            labels = q.get("subrequests", [])
            if unknown and previous:
                parts = [{**part, "answer": "I don't know.", "status": "unknown",
                          "replace": False} for part in previous]
            if unknown and labels and not previous:
                parts = [{"id": q["id"] + ":" + hashlib.sha256(str(label).casefold().encode()).hexdigest()[:12],
                          "question_id": q["id"], "question": str(label),
                          "answer": "I don't know.", "status": "unknown"} for label in labels]
            simple = not multipart_question(q)
            if simple:
                parts = direct_parts(q, answer)
            if (not unknown and not simple and q.get("decision") not in {"block", "clarify"}
                    and not answer.get("refusal")):
                try:
                    async with asyncio.timeout(22):
                        async with self.limit:
                            parts = await self.service.calls.reasoner.structure_reply(
                                q["text"], answer.get("text", ""), q["id"], previous,
                                requested_items=q.get("subrequests", []),
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("answer_parts_fallback error=%s", type(exc).__name__)
                    if previous:
                        parts = [{**part, "answer": "I don't know.", "status": "unknown",
                                  "replace": False} for part in previous]
            current = self.service.get(session_id)
            saved = next((a for a in current["answers"] if a["id"] == answer_id), None)
            question = next((item for item in current["questions"] if item["id"] == q["id"]), None)
            attempts = [a for a in current["answers"] if q["id"] in a.get("question_ids", [])
                        and a.get("reply_kind") not in CONTROL_KINDS
                        and a.get("status") != "cancelled"]
            if (not saved or not text_ready(saved) or not question
                    or question["version"] != version or not attempts
                    or attempts[-1]["id"] != answer_id):
                return  # A slow older attempt cannot overwrite a newer correction.
            merged = (parts if simple else
                      merge_parts(previous, parts, refuse=question.get("decision") == "block"))
            question["answer_parts"] = merged
            question["answer_parts_revision"] = version
            saved.update(answer_parts=merged, parts_status="ready", parts_version=PARTS_VERSION)
            self.service.publish(current, "answer.card_updated")
            self.service.spawn(self.service.remember_completed_reply(session_id, answer_id))
        finally:
            self.formatting.discard((session_id, answer_id))

    def project(self, state: dict, result: dict, *, schedule_pending: bool = True) -> None:
        public = [a for a in state["answers"] if a.get("public_stream")]
        if not public and not any(q.get("source_message_ids") for q in state["questions"]):
            return
        groups: dict[str, list[dict]] = {}
        for q in state["questions"]:
            source = next(iter(q.get("source_message_ids", [])), None)
            if source:
                groups.setdefault(source, []).append(q)
        cards, summaries = {}, {}
        handled = set()
        for source, questions in groups.items():
            parts, selected, ready_count, updating = [], [], 0, False
            related_ids = {q["id"] for q in questions}
            handled.update(a["id"] for a in public if related_ids.intersection(a.get("question_ids", [])))
            for q in questions:
                attempts = [a for a in public if q["id"] in a.get("question_ids", [])
                            and a.get("reply_kind") not in CONTROL_KINDS
                            and a.get("status") != "cancelled"]
                latest = attempts[-1] if attempts else None
                direct = not multipart_question(q)
                if (schedule_pending and latest and text_ready(latest)
                        and (latest.get("parts_status") == "pending"
                             or latest.get("parts_version") != PARTS_VERSION)
                        and q in state["questions"][-20:]):
                    self.schedule(state["id"], latest["id"])  # Resume after a service restart.
                if q.get("decision") == "block":
                    parts.extend(fallback_parts(q, {"refusal": True}))
                    ready_count += 1
                    if latest:
                        selected.append(latest)
                    continue
                completed = [a for a in attempts if (text_ready(a) or a.get("status") == "withdrawn")
                             and (direct or a.get("parts_status") != "pending")]
                chosen = completed[-1] if completed else None
                # Initial text must be visible while the optional presentation
                # formatter runs. Corrections keep the prior structured version
                # until its replacement is ready, in the same stable card.
                if not chosen and latest and text_ready(latest):
                    chosen = latest
                if (direct and latest and latest.get("status") == "streaming"
                        and latest.get("text", "").strip()):
                    chosen = latest
                if latest and latest["status"] == "withdrawn":
                    chosen = latest  # Never resurrect an older version after withdrawal.
                if not chosen:
                    if (latest and latest.get("status") in {"failed", "interrupted"}
                            and q.get("status") in {"answered", "unavailable", "failed", "held", "interrupted"}):
                        parts.extend(fallback_parts(q, {"text": "I don't know."}))
                        ready_count += 1
                        selected.append(latest)
                    else:
                        updating = True
                    continue
                selected.append(chosen)
                ready_count += 1
                updating |= bool(
                    q.get("status") == "ready"
                    or (q.get("status") == "generating" and not latest)
                    or (latest and ((latest.get("status") in {"streaming", "generating"}
                                     and not text_ready(latest))
                                    or latest.get("parts_status") == "pending")
                        and latest.get("status") not in {"interrupted", "failed", "withdrawn"})
                )
                if chosen["status"] == "withdrawn" or q.get("status") == "withdrawn":
                    parts.append({"id": q["id"] + ":withdrawn", "question_id": q["id"],
                                  "question": q["text"], "answer": "This answer was withdrawn.",
                                  "status": "withdrawn"})
                else:
                    parts.extend(direct_parts(q, chosen) if direct else
                                 chosen.get("answer_parts") or fallback_parts(q, chosen))
                    if (schedule_pending and not chosen.get("parts_status") and q in state["questions"][-20:]
                            and chosen["status"] == "completed"):
                        self.schedule(state["id"], chosen["id"])
            summaries[source] = {"state": "updating" if updating else "ready",
                                 "completed": ready_count, "total": len(questions)}
            if ready_count != len(questions):
                summaries[source]["state"] = (
                    "stopped" if all(q.get("status") in {"interrupted", "withdrawn", "rejected"}
                                     for q in questions) else "collecting"
                )
                continue  # No first answer bubble until every branch has an outcome.
            if not parts:
                continue
            single = len(parts) == 1
            text = parts[0]["answer"] if single else "\n\n".join(
                f"**{p['question']}**\n\n{p['answer']}" for p in parts
            )
            spoken = parts[0]["answer"] if single else " ".join(
                f"{p['question']} {p['answer']}" for p in parts
            )
            digest = hashlib.sha256(spoken.encode()).hexdigest()[:16]
            audio = {"id": f"reply-{source}-{digest}", "kind": "summary", "status": "browser",
                     "language": state.get("language", "en-US"), "spoken_text": spoken}
            if (single and len(selected) == 1 and selected[0].get("text") == spoken
                    and selected[0].get("speech_text", spoken) == spoken
                    and selected[0].get("tts_audio", {}).get("status") in {"ready", "browser"}):
                audio = selected[0]["tts_audio"]
            original = next((m for m in state["messages"] if m["id"] == source), {})
            withdrawn = all(p["status"] == "withdrawn" for p in parts)
            card = {
                "id": "reply-" + source, "type": "ai", "text": text,
                "status": ("withdrawn" if withdrawn else "streaming"
                           if any(a.get("status") == "streaming" and not text_ready(a) for a in selected)
                           else "completed"), "public_stream": True,
                "question_ids": [q["id"] for q in questions], "answer_parts": parts,
                "is_answer_card": True, "updating": updating,
                "text_done": all(text_ready(a) for a in selected),
                "audio_done": all(a.get("audio_done", True) for a in selected),
                "reply_kind": "answer", "reply_to_message_id": source,
                "created_at": selected[0].get("created_at") if selected else original.get("created_at"),
                "revision": sum(1 for a in public if related_ids.intersection(a.get("question_ids", []))),
                "audio": audio, "tts_audio": audio,
                "delivery": "sent", "human_reviewed": any(a.get("human_reviewed") for a in selected),
                "refusal": all(p["status"] in {"refused", "unknown"} for p in parts),
            }
            if original.get("call_id"):
                card["call_id"] = original["call_id"]
            cards[source] = card
        projected = []
        for message in result["messages"]:
            if message["id"] in handled:
                continue
            if message["type"] == "talker" and message["id"] in summaries:
                message["reply_summary"] = summaries[message["id"]]
            projected.append(message)
            if message["id"] in cards:
                projected.append(cards[message["id"]])
        result["messages"] = projected
        result["answers"] = [a for a in result["answers"] if a["id"] not in handled]
        result["answers"].extend({k: v for k, v in card.items() if k not in {"type", "audio"}}
                                 for card in cards.values())
