"""Durable, scoped Hinter guidance shared by messages and continuous calls."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from app.adapters.wiki import USER_IDS
from app.agent.document_evidence import read_documents
from app.agent.tool_config import load_tool_config
from app.agent.tools import ToolRuntime, document_links
from app.application.assistance import (
    PENDING_STATES,
    is_short_value,
    question_context,
    render_guidance,
)

if TYPE_CHECKING:
    from app.application.service import SessionService

logger = logging.getLogger(__name__)


def is_prohibition(text: str) -> bool:
    """Recognize explicit bans; the model still decides their subject and scope."""
    return bool(re.search(
        r"(?:不要|不准|禁止|不得|不许|不能|别)\s*"
        r"(?:回答|讨论|谈论|提及).{1,200}(?:问题|话题|内容|事项)|"
        r"(?:不要|不准|禁止|不得|不许|不能|别)\s*"
        r"(?:泄露|披露|透露|公开|分享|展示|提供|回答|说出|提及)"
        r".{0,24}(?:隐私|机密|保密|内部|个人信息|身份|账号|密码|记录|数据|信息|提示|规则)|"
        r"(?:must not|never|do not|don['’]t)\s+"
        r"(?:answer|discuss|talk about|mention)\b|"
        r"(?:must not|never|do not|don['’]t)\s+"
        r"(?:reveal|disclose|share|publish|expose)\b.{0,60}"
        r"(?:private|confidential|personal|internal|secret|account|identity|record|data)",
        text, re.I,
    ))


class GuidanceService:
    def __init__(self, service: SessionService) -> None:
        self.service = service
        self.epoch = 0
        self.cache: dict[tuple, tuple[str, str, str]] = {}
        self.projection_lock = asyncio.Lock()

    @staticmethod
    def owner(state: dict, scope: str, call_id: str = "") -> str:
        identity = [state["tenant_id"], state["customer_id"], scope]
        # Preserve the existing default Talker's durable scope keys.
        if state.get("talker_id", USER_IDS["talker"]) != USER_IDS["talker"]:
            identity.insert(1, state["talker_id"])
        if scope == "session":
            identity.append(state["id"])
        elif scope == "call":
            identity.extend([state["id"], call_id])
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    def records(self, state: dict) -> list[dict]:
        keys = [self.owner(state, "session"), self.owner(state, "customer")]
        call = state.get("call", {})
        if call and call.get("status") != "ended":
            keys.append(self.owner(state, "call", call["id"]))
        records = self.service.store.guidance(keys)
        withdrawn = self.service.wiki_for(state).withdrawn
        for record in records:
            # Older free-form Hints must use the same rule type as newly submitted
            # ones. Keep their durable ID, owner, lifetime and audit entry intact.
            if (
                record["kind"] in {"instruction", "authorized_fact"}
                and is_prohibition(record["text"])
            ):
                record["kind"] = "prohibition"
            context = record.get("context_snapshot") or {}
            for message in context.get("recent_messages", []):
                if message.get("message_id") in withdrawn:
                    message["text"] = "This answer was withdrawn."
        return records

    @staticmethod
    def applies_to(
        record: dict, state: dict, question_id: str | None = None,
        message_id: str | None = None,
    ) -> bool:
        if record.get("revoked"):
            return False
        if record.get("question_id") and record["question_id"] != question_id:
            return False
        if record.get("effective_from") != "next_turn":
            return True
        question = next((q for q in state["questions"] if q["id"] == question_id), {})
        sources = question.get("source_message_ids", []) or ([message_id] if message_id else [])
        if not sources or record.get("after_message_id") in sources:
            return False
        incoming = next((m for m in state["messages"] if m["id"] in sources), {})
        return incoming.get("created_at", "") > record["created_at"]

    def snapshot(
        self,
        session_id: str,
        question_id: str | None = None,
        *,
        message_id: str | None = None,
    ) -> tuple[str, str, str]:
        state = self.service.get(session_id)
        key = (
            session_id,
            question_id,
            message_id,
            self.epoch,
            self.service.wiki_version,
            state.get("call", {}).get("status") == "ended",
        )
        if key in self.cache:
            return self.cache[key]
        question = next((q for q in state["questions"] if q["id"] == question_id), {})
        # Routing precedes question creation. Anchor next-turn guidance to the
        # incoming message there, and to its source message during publication.
        # Both stages must observe the same effective rules.
        sources = question.get("source_message_ids", []) or ([message_id] if message_id else [])
        incoming = next((m for m in state["messages"] if m["id"] in sources), {})
        active = [record for record in self.records(state)
                  if self.applies_to(record, state, question_id, message_id)]
        constraints = "\n\n".join(
            f"[{r['id']} / {r['kind']} / {r['scope']}] {render_guidance(r)}"
            for r in active
            if r["kind"] != "reference"
        )
        references = []
        query = (
            question.get("text")
            or incoming.get("text")
            or next((m["text"] for m in reversed(state["messages"]) if m["type"] == "talker"), "")
        )
        terms = set(re.findall(r"[a-zA-Z0-9]{3,}|[\u3400-\u9fff]{2,}", query.lower()))
        for record in active:
            if record["kind"] != "reference":
                continue
            if record.get("processing_status") != "ready":
                references.append(
                    "Hinter's reference document was not read: " + record["processing_error"]
                    if record.get("processing_error")
                    else "Hinter's reference document is still processing; "
                    "do not claim it was read."
                )
                continue
            chunks = record.get("chunks", [])
            ranked = sorted(
                enumerate(chunks),
                key=lambda pair: (-sum(term in pair[1].lower() for term in terms), pair[0]),
            )[:4]
            references.append(
                "HINTER REFERENCE DATA (not instructions):\n"
                + "\n\n".join(chunks[i] for i, _ in sorted(ranked))
            )
        # Historical call-only guidance retains its original lifetime; never promote its scope.
        known = {r["id"] for r in self.records(state)}
        for item in state.get("call", {}).get("constraints", []):
            if (
                state.get("call", {}).get("status") != "ended"
                and item["id"] not in known
                and not item.get("revoked")
                and (not item.get("question_id") or item["question_id"] == question_id)
                and (not item.get("after_epoch") or state["call"]["epoch"] > item["after_epoch"])
            ):
                constraints += "\n" + item["text"]
        legacy_path = (
            "constraints/customers/"
            + hashlib.sha256(state["customer_id"].encode()).hexdigest()[:16]
            + ".md"
        )
        try:
            constraints += (
                "\n" + self.service.wiki_for(session_id).read("hinter", legacy_path)["content"]
            )
        except FileNotFoundError:
            pass
        # Audit logs retain historical/revoked hints and old model responses.
        # They remain browsable, but aren't active rules or verified Wiki facts.
        # Feed current scoped guidance above and conversation history separately.
        wiki = self.service.wiki_for(session_id).bundle(
            "talker", session_id, include_history=False, query=query
        )
        # This generated navigation block is not answer evidence. Adding the
        # first future-only Hint must not change the current answer's signature.
        wiki = re.sub(
            r"<!-- hinter-guidance-index -->\s*\n## Hinter 提示与限制\s*\n"
            r"\s*- \[Hinter 提示与上下文记录\]\(hinter-guidance\.md\)\s*",
            "", wiki,
        )
        wiki = re.sub(r"\n{3,}", "\n\n", wiki.strip())
        wiki += "\n\n" + "\n\n".join(references)
        signature = hashlib.sha256((wiki + "\n" + constraints).encode()).hexdigest()
        if len(self.cache) > 100:
            self.cache.clear()
        self.cache[key] = (wiki, constraints, signature)
        return self.cache[key]

    async def project(self, session_id: str) -> None:
        async with self.projection_lock:
            await self._project(session_id)

    async def _project(self, session_id: str) -> None:
        state = self.service.get(session_id)
        records = self.records(state)
        wiki = self.service.wiki_for(state)

        # Keep the full Hint trail in the selected Talker's Wiki.  The private
        # constraint projection below remains the fast runtime source, while this
        # contextual record makes every Hint inspectable from the Talker workspace.
        contextual_records = sorted(
            (item for item in records if item.get("session_id") == state["id"]),
            key=lambda item: (item["created_at"], item["revision"]),
        )
        lines = [
            f"## Session {state['id']} · {state['talker_name']}",
            "",
        ]
        for record in contextual_records:
            question = next(
                (item for item in state["questions"] if item["id"] == record.get("question_id")),
                None,
            )
            snapshot = record.get("context_snapshot") or self.context_snapshot(
                state, record, question
            )
            snapshot = {
                **snapshot,
                "questions_after_hint": [
                    {"id": item["id"], "question": item["text"], "created_at": item["created_at"]}
                    for item in state["questions"]
                    if item.get("created_at", "") > record.get("created_at", "")
                ][-5:],
            }
            lines.extend(
                [
                    f"## {record['created_at']} · {record['kind']} · {record['scope']} · "
                    f"{'已撤销' if record.get('revoked') else '生效'}",
                    "",
                    (
                        f"提示（已撤销，仅作历史记录，不再生效）：{record['text']}"
                        if record.get("revoked")
                        else f"提示（当前生效）：{record['text']}"
                    ),
                    "",
                    "对话上下文：",
                    "",
                    "```json",
                    json.dumps(snapshot, ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
        try:
            marker = f"<!-- session:{state['id']} -->"
            try:
                existing = wiki.read("talker", "hinter-guidance.md")["content"]
            except FileNotFoundError:
                existing = (
                    "# Hinter 提示记录\n\n"
                    "按 Talker 汇总 Hinter 提交的提示及其对话上下文；撤销项仍保留并标注状态。\n"
                )
            block = marker + "\n" + "\n".join(lines).rstrip() + "\n"
            if marker in existing:
                start = existing.index(marker)
                next_marker = existing.find("<!-- session:", start + len(marker))
                end = next_marker if next_marker >= 0 else len(existing)
                existing = existing[:start] + block + existing[end:]
            else:
                existing = existing.rstrip() + "\n\n" + block
            await asyncio.to_thread(
                wiki.write,
                "talker",
                "hinter-guidance.md",
                existing,
            )
            center = wiki.read("talker", "context.md")
            marker = "<!-- hinter-guidance-index -->"
            if marker not in center["content"]:
                await asyncio.to_thread(
                    wiki.write,
                    "talker",
                    "context.md",
                    center["content"].rstrip()
                    + f"\n\n{marker}\n## Hinter 提示与限制\n\n"
                    + "- [Hinter 提示与上下文记录](hinter-guidance.md)\n",
                    center["revision"],
                )
        except Exception:
            logger.exception("talker_guidance_wiki_projection_failed session=%s", session_id)
            current = self.service.get(session_id)
            current["guidance_projection"] = "sync_pending"
            self.service.publish(current, "guidance.projection")
            return

        for scope in {r["scope"] for r in records}:
            selected = [r for r in records if r["scope"] == scope]
            key = self.owner(state, scope, state.get("call", {}).get("id", ""))
            content = "# Hinter 核心约束\n\n" + "\n\n".join(
                f"## {r['id']} · v{r['revision']} · {r['kind']} · "
                f"{'已撤销' if r.get('revoked') else '有效'}\n\n{render_guidance(r)}"
                for r in selected
            )
            try:
                await asyncio.to_thread(
                    self.service.wiki_for(session_id).write,
                    "hinter",
                    f"constraints/{scope}/{key}.md",
                    content,
                )
            except Exception:
                logger.exception("guidance_projection_failed session=%s", session_id)
                current = self.service.get(session_id)
                current["guidance_projection"] = "sync_pending"
                self.service.publish(current, "guidance.projection")
                return
        current = self.service.get(session_id)
        current["guidance_projection"] = "synced"
        self.service.publish(current, "wiki.updated")
        self.service.publish(current, "guidance.projection")

    @staticmethod
    def context_snapshot(state: dict, record: dict, question: dict | None = None) -> dict:
        """Capture the surrounding conversation without narrowing a general Hint."""
        created_at = record.get("created_at", "")
        messages = [
            {
                "message_id": message["id"],
                "speaker": message["type"],
                "text": message["text"][:1200],
                "created_at": message["created_at"],
            }
            for message in state["messages"]
            if message.get("created_at", "") <= created_at and message.get("text")
        ][-8:]
        nearby_questions = [
            {
                "id": item["id"],
                "question": item["text"][:1200],
                "status": item["status"],
                "created_at": item["created_at"],
            }
            for item in state["questions"]
            if item.get("created_at", "") <= created_at
        ][-5:]
        return {
            "talker": state.get("talker_name", ""),
            "session_id": state["id"],
            "scope": record.get("scope"),
            "related_question": (
                {
                    "id": question["id"],
                    "question": question["text"],
                }
                if question
                else None
            ),
            "recent_questions": nearby_questions,
            "recent_messages": messages,
        }

    async def submit(
        self, session_id: str, request: dict[str, Any], *, trigger_reply: bool = True
    ) -> dict:
        from app.application.service import fail, now, uid

        text = str(request.get("text", "")).strip()
        if not text or len(text) > 40000:
            raise fail(400, "INVALID_HINT", "请输入提示，最多4万字。")
        state = self.service.get(session_id)
        scope = request.get("scope", "customer" if request.get("persistent") else "session")
        if scope not in ["session", "customer", "call"]:
            raise fail(400, "INVALID_SCOPE", "请选择本会话、仅本次通话或客户范围。")
        call = state.get("call", {})
        if scope == "call" and (not call or call.get("status") == "ended"):
            raise fail(409, "CALL_ENDED", "仅通话提示需要活动通话。")
        future_only = request.get("effective_from") == "next_turn"
        target = None if future_only else request.get("question_id")
        inferred_target = False
        record_id = request.get("guidance_id")
        prior = next((r for r in self.records(state) if r["id"] == record_id), None)
        if record_id and (not prior or request.get("expected_revision") != prior["revision"]):
            raise fail(409, "GUIDANCE_CHANGED", "提示已更新，请刷新后重试。")
        if prior:
            scope, target = prior["scope"], None if future_only else prior.get("question_id")
        elif not future_only and not target and is_short_value(text):
            pending = [q for q in state["questions"] if q["status"] in PENDING_STATES]
            value_target = request.get("value_question_id")
            if value_target:
                if not any(q["id"] == value_target for q in pending):
                    raise fail(409, "QUESTION_CHANGED", "原来补充的问题已更新，请核对后再发送。")
                target = value_target
            elif len(pending) == 1 and not request.get("require_explicit_target"):
                target = pending[0]["id"]
            else:
                raise fail(400, "HINT_TARGET_REQUIRED", "请选择这个数值对应的问题，再补充信息。")
            inferred_target = True
        question = next((q for q in state["questions"] if q["id"] == target), None)
        if target and not question:
            raise fail(404, "NOT_FOUND", "问题不存在。")
        target_part = None
        if request.get("part_id") and not future_only:
            target_part = next((part for part in (question or {}).get("answer_parts", [])
                                if part["id"] == request["part_id"]), None)
            if not target_part:
                raise fail(409, "ANSWER_PART_CHANGED", "该项回答已更新，请刷新后再补充。")
        reference = question
        if not reference:
            active_id = call.get("active_question_id")
            reference = next((item for item in state["questions"] if item["id"] == active_id), None)
            if not reference:
                reference = next(
                    (
                        item
                        for item in reversed(state["questions"])
                        if item.get("created_at", "") <= now()
                    ),
                    None,
                )
        if not reference and re.search(
            r"这个|这条|这题|这项|该数据|该问题|"
            r"this (?:one|question|number|value|information)|\b(?:that|it)\b",
            text,
            re.I,
        ):
            pending = [q for q in state["questions"] if q["status"] in PENDING_STATES]
            reference_id = request.get("context_question_id") or call.get("active_question_id")
            reference = next((q for q in state["questions"] if q["id"] == reference_id), None)
            if not reference and len(pending) == 1:
                reference = pending[0]
        kind = request.get("kind", "instruction")
        if kind not in [
            "instruction",
            "prohibition",
            "require_human",
            "authorized_fact",
            "style",
            "reference",
        ]:
            raise fail(400, "INVALID_KIND", "不支持的提示类型。")
        if kind == "instruction" and (len(text) > 6000 or document_links(text)):
            kind = "reference"
        # Keep arbitrary natural-language guidance intact. Classification is only an optimization.
        if kind in ["instruction", "authorized_fact"]:
            if is_prohibition(text):
                kind = "prohibition"
            elif re.search(
                r"必须.*(?:确认|审核)|需.*人工|before.*(?:confirm|approval)", text, re.I
            ):
                kind = "require_human"
            elif re.search(
                r"简短|简洁|两句|一句|语气|口吻|brief|concise|tone|short answer", text, re.I
            ):
                kind = "style"
            elif re.search(r"已核实|已确认|确认为|verified|confirmed", text, re.I):
                kind = "authorized_fact"
            elif is_short_value(text) and re.search(r"\d", text):
                # A short supplied value may be factual; numbers embedded in
                # ordinary instructions (e.g. "ask three questions") are not.
                kind = "authorized_fact"
        record = {
            "id": prior["id"] if prior else uid(),
            "talker_id": state["talker_id"],
            "session_id": state["id"],
            "text": text,
            "kind": kind,
            "scope": scope,
            "question_id": target,
            "binding": question_context(state, reference) if reference else None,
            "context_snapshot": None,
            "created_at": now(),
            "call_id": call.get("id") if scope == "call" else None,
            "revoked": bool(request.get("revoke")),
            "processing_status": "processing" if kind == "reference" else "ready",
            "effective_from": "now"
            if inferred_target
            else request.get(
                "effective_from", "next_turn" if kind == "style" and not target else "now"
            ),
            "after_message_id": call.get("active_message_id")
            if request.get("effective_from") == "next_turn" and not target
            else None,
        }
        if target_part:
            record["part_id"] = target_part["id"]
            record["binding"]["target_part"] = {
                "id": target_part["id"], "question": target_part["question"],
                "previous_answer": target_part["answer"], "previous_status": target_part["status"],
            }
        record["context_snapshot"] = self.context_snapshot(state, record, reference)
        # No await between durable effect and notification/invalidating current publishers.
        record = self.service.store.append_guidance(
            self.owner(state, scope, call.get("id", "")), record
        )
        self.epoch += 1
        self.cache.clear()
        state["guidance_revision"] = record["revision"]
        state["guidance_projection"] = "sync_pending"
        state["messages"].append(
            {
                "id": uid(),
                "type": "hinter",
                "text": text,
                "status": "completed",
                "created_at": record["created_at"],
                "question_ids": [target] if target else [],
            }
        )
        self.service.publish(state, "guidance.effective")
        if getattr(self.service, "replies", None) and not future_only:
            self.service.replies.guidance_changed(session_id, target)
        if self.service.calls:
            self.service.calls.context_cache.clear()
            if (kind in ["prohibition", "instruction"] and not record["revoked"]
                    and not future_only):
                self.service.calls.schedule_retrospection(
                    session_id, render_guidance(record), record["id"]
                )
        await self.project(session_id)
        if trigger_reply and not future_only and getattr(self.service, "replies", None):
            self.service.spawn(self.service.replies.apply_guidance(
                session_id, target, guidance_id=record["id"]
            ))
        if kind == "reference" and not record["revoked"]:
            self.service.spawn(self.index_reference(session_id, record))
        return {
            "status": "effective",
            "id": record["id"],
            "revision": record["revision"],
            "projection": self.service.get(session_id).get("guidance_projection", "sync_pending"),
            "target": target,
        }

    async def index_reference(self, session_id: str, record: dict) -> None:
        source = record["text"]
        processing_error = ""
        if links := document_links(source):
            config = load_tool_config().model_copy(update={"mcp_servers": {}})
            try:
                async with ToolRuntime(config) as runtime:
                    evidence, sources, errors = await read_documents(runtime, links[:5])
                source += "\n" + evidence
                if not sources:
                    processing_error = (
                        errors[0]["message"] if errors else "Document reading failed."
                    )
            except Exception:
                processing_error = "Document reading failed; check Lark CLI configuration."
        chunks = await asyncio.to_thread(
            lambda: [source[i : i + 3000] for i in range(0, len(source), 3000)]
        )
        state = self.service.get(session_id)
        latest = next((r for r in self.records(state) if r["id"] == record["id"]), None)
        if not latest or latest["revision"] != record["revision"] or latest.get("revoked"):
            return
        self.service.store.append_guidance(
            self.owner(state, record["scope"], record.get("call_id") or ""),
            {
                **record,
                "chunks": chunks,
                "processing_status": "failed" if processing_error else "ready",
                "processing_error": processing_error,
            },
        )
        self.epoch += 1
        self.cache.clear()
        self.service.publish(state, "guidance.indexed")
        if self.service.replies:
            self.service.replies.guidance_changed(session_id, record.get("question_id"))
            self.service.spawn(
                self.service.replies.apply_guidance(
                    session_id, record.get("question_id"), guidance_id=record["id"]
                )
            )
        self.service.spawn(self.project(session_id))
