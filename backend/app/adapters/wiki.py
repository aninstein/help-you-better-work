"""Per-user Markdown workspaces. Content organization belongs to the memory prompt."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

USER_IDS = {
    "talker": os.getenv("TALKER_USER_ID", "talker-001"),
    "hinter": os.getenv("HINTER_USER_ID", "hinter-001"),
}
USERS = tuple(USER_IDS)


class WikiStore:
    def __init__(self, root: Path, *, initialize: bool = True) -> None:
        self.root = root
        self.lock = RLock()
        self.deleted = not initialize
        withdrawn = root / ".withdrawn-messages.json"
        self.withdrawn: set[str] = (
            set(json.loads(withdrawn.read_text())) if withdrawn.exists() else set()
        )
        marker = root / ".reset-at"
        self.reset_at = marker.read_text() if marker.exists() else ""
        for user in USERS if initialize else ():
            directory = root / USER_IDS[user]
            if not directory.resolve().is_relative_to(root.resolve()):
                raise ValueError("用户 ID 必须是 Wiki 根目录内的目录名称。")
            legacy = root / user
            if legacy.exists() and not directory.exists():
                legacy.rename(directory)
            directory.mkdir(parents=True, exist_ok=True)
            context = directory / "context.md"
            if not context.exists():
                context.write_text(
                    f"# {user.title()} 的上下文\n\n"
                    "这是我的中心文档，可以自由编辑标题、正文和链接。\n\n"
                    "## 当前对话\n\n尚未开始。\n\n"
                    "## 已知背景与长期记忆\n\n暂无。\n\n"
                    "## 近期任务与短期记忆\n\n暂无。\n\n"
                    "## 沟通偏好（用户可调整）\n\n"
                    "暂无用户明确设置的偏好。助手默认温和、好奇、务实，表达自然简洁，"
                    "直接给出结果或建议。这是助手默认行为，不是用户个人事实；"
                    "助手的观点和推荐不得记成用户的偏好。\n\n"
                    "## 待确认事项\n\n暂无。\n\n"
                    "## 回答边界\n\n不要推测或披露私人密码和密钥。\n",
                    encoding="utf-8",
                )

    def path(self, user: str, name: str) -> Path:
        if user not in USERS:
            raise ValueError("未知用户。")
        path = (self.root / USER_IDS[user] / name).resolve()
        base = (self.root / USER_IDS[user]).resolve()
        if not path.is_relative_to(base) or path.suffix != ".md" or len(name) > 200:
            raise ValueError("请使用 Wiki 内部的 Markdown 文档路径。")
        return path

    @staticmethod
    def revision(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    def read(self, user: str, name: str) -> dict[str, str]:
        path = self.path(user, name)
        if not path.exists():
            raise FileNotFoundError(name)
        content = self.redact_withdrawn(path.read_text(encoding="utf-8"))
        return {"path": name, "content": content, "revision": self.revision(content)}

    def list(self, user: str) -> list[dict[str, str]]:
        base = self.root / USER_IDS[user]
        files = sorted(base.rglob("*.md"), key=lambda p: (p.name != "context.md", str(p)))
        return [
            {
                "path": str(p.relative_to(base)),
                "title": p.read_text(encoding="utf-8").splitlines()[0].lstrip("# ")
                if p.stat().st_size
                else p.stem,
            }
            for p in files
        ]

    def write(
        self, user: str, name: str, content: str, revision: str | None = None
    ) -> dict[str, str]:
        with self.lock:
            if self.deleted:
                raise ValueError("Talker 的 Wiki 已删除。")
            path = self.path(user, name)
            existing = (
                self.redact_withdrawn(path.read_text(encoding="utf-8")) if path.exists() else ""
            )
            if revision is not None and revision != self.revision(existing):
                raise ValueError("文档已被更新，请重新加载后保存；你的编辑内容仍保留在页面。")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(self.redact_withdrawn(content), encoding="utf-8")
            temporary.replace(path)
            return self.read(user, name)

    def destroy(self) -> None:
        """Fence stale writers; keep sibling Talker workspaces under the legacy root."""
        with self.lock:
            self.deleted = True
            for user in USERS:
                directory = self.root / USER_IDS[user]
                if not directory.resolve().is_relative_to(self.root.resolve()):
                    raise ValueError("Wiki 路径必须位于当前 Talker 内。")
                if directory.exists():
                    shutil.rmtree(directory)
            for name in [".reset-at", ".withdrawn-messages.json"]:
                (self.root / name).unlink(missing_ok=True)

    def redact_withdrawn(self, content: str) -> str:
        if not self.withdrawn:
            return content

        # Hint context can quote earlier messages as structured JSON as well as
        # ordinary transcript blocks. Retain source IDs so tombstones cover both.
        def redact_context(value: Any) -> Any:
            if isinstance(value, list):
                return [redact_context(item) for item in value]
            if isinstance(value, dict):
                result = {key: redact_context(item) for key, item in value.items()}
                if result.get("message_id") in self.withdrawn and "text" in result:
                    result["text"] = "This answer was withdrawn."
                return result
            return value

        decoder = json.JSONDecoder()
        for marker in reversed(
            list(re.finditer(r"HINTER INPUT WITH CONVERSATION CONTEXT:\n|```json[ \t]*\n", content))
        ):
            try:
                value, end = decoder.raw_decode(content[marker.end() :])
            except ValueError:
                continue
            redacted = redact_context(value)
            if redacted == value:
                continue
            content = (
                content[: marker.end()]
                + json.dumps(redacted, ensure_ascii=False, indent=2)
                + content[marker.end() + end :]
            )
        pattern = r"(<!-- message:([^>]+) -->).*?(?=\n<!-- (?:message|questions):|\Z)"

        def replace(match: re.Match) -> str:
            if match[2] in self.withdrawn:
                return match[1] + "\n## AI · 已撤回\n\n内容已隐藏，不可作为回答依据。\n"
            return match[0]

        return re.sub(pattern, replace, content, flags=re.S)

    def withdraw_messages(self, message_ids: list[str]) -> None:
        """Persist tombstones before cleaning files, also fencing stale background Wiki writes."""
        with self.lock:
            if self.deleted:
                return
            self.withdrawn.update(message_ids)
            temporary = self.root / ".withdrawn-messages.tmp"
            temporary.write_text(json.dumps(sorted(self.withdrawn)))
            temporary.replace(self.root / ".withdrawn-messages.json")
            for user in USERS:
                for doc in self.list(user):
                    path = self.path(user, doc["path"])
                    content = path.read_text(encoding="utf-8")
                    redacted = self.redact_withdrawn(content)
                    if redacted != content:
                        self.write(user, doc["path"], redacted)

    def bundle(
        self, user: str, session_id: str, *, include_history: bool = True, query: str = ""
    ) -> str:
        paths = [
            f["path"]
            for f in self.list(user)
            if not f["path"].startswith("sessions/") or f["path"] == f"sessions/{session_id}.md"
            if include_history
            or (not f["path"].startswith("sessions/") and f["path"] != "hinter-guidance.md")
        ]
        documents = {name: self.read(user, name)["content"] for name in paths}
        if query:
            # Rank the whole curated Wiki before applying the prompt budget;
            # alphabetical position must not make later topic documents invisible.
            terms = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", query.casefold()))
            terms -= {"the", "what", "which", "would", "like", "know", "please", "how", "and"}
            for phrase in re.findall(r"[\u3400-\u9fff]{2,}", query):
                terms.update(phrase[i : i + 2] for i in range(len(phrase) - 1))
            paths.sort(
                key=lambda name: (
                    name != "context.md",
                    -sum(
                        (name.casefold().count(term) * 4 + documents[name].casefold().count(term))
                        for term in terms
                    ),
                    name,
                )
            )
        return "\n\n".join(f"--- {name} ---\n{documents[name]}" for name in paths[:20])[:26000]

    def sync_session(self, state: dict[str, Any]) -> None:
        with self.lock:
            self._sync_session(state)

    def _sync_session(self, state: dict[str, Any]) -> None:
        """Append new evidence without replacing human edits in the conversation document."""
        for user in USERS:
            path = f"sessions/{state['id']}.md"
            try:
                text = self.read(user, path)["content"]
            except FileNotFoundError:
                text = (
                    f"# Session {state['id']}\n\n"
                    f"状态：{state['status']} · 语言：{state['language']}\n\n"
                )
            for message in state["messages"]:
                if user == "talker" and message["type"] == "hinter":
                    continue
                if not message["text"]:
                    continue
                if message["type"] == "ai" and not (
                    message["status"] == "completed"
                    or (message.get("public_stream") and message["status"] == "interrupted")
                ):
                    continue
                if (
                    message.get("call_id")
                    and message["type"] == "ai"
                    and not message.get("delivered_text")
                    and message.get("delivery") != "delivered"
                ):
                    continue
                if (
                    self.reset_at
                    and message.get("published_at", message["created_at"]) <= self.reset_at
                ):
                    continue
                marker = f"<!-- message:{message['id']} -->"
                if marker not in text:
                    text += (
                        f"\n{marker}\n## {message['type']} · {message['created_at']}\n\n"
                        f"{message.get('delivered_text', message['text'])}\n\n"
                    )
            questions = [
                q
                for q in state["questions"]
                if not self.reset_at or q["created_at"] > self.reset_at
            ]
            if questions:
                marker = f"<!-- questions:{state['last_sequence']} -->"
                if marker not in text:
                    text += (
                        marker
                        + "\n"
                        + (
                            "## 问题状态\n\n"
                            + "\n".join(
                                f"- {q['display_no']} [{q['status']}]: {q['text']}"
                                for q in questions
                            )
                            + "\n"
                        )
                    )
            self.write(user, path, text)

    def start_session(self, state: dict[str, Any]) -> None:
        for user in USERS:
            document = self.read(user, "context.md")
            content = document["content"]
            lines = [line for line in content.splitlines() if not line.startswith("当前轮次文档：")]
            content = (
                "\n".join(lines).rstrip()
                + f"\n\n当前轮次文档：[Session {state['id'][:8]}](sessions/{state['id']}.md)\n"
            )
            self.write(user, "context.md", content)
        self.sync_session(state)

    def record_refusal(self, state: dict[str, Any], questions: list[str]) -> None:
        """Keep human refusal evidence as free Markdown; never treat it as a factual answer."""
        name = "notes/rejections.md"
        for user in USERS:
            try:
                content = self.read(user, name)["content"]
            except FileNotFoundError:
                content = "# 人工拒绝回答记录\n\n这些问题未提供实质答案，不能当作已确认知识。\n"
            content += (
                f"\n## {state['id']} · 人工拒绝\n\n"
                + "\n".join(f"- 问题：{question}" for question in questions)
                + "\n\n回复：Sorry, I dont know.\n\n"
                + f"对话来源：[Session](../sessions/{state['id']}.md)\n"
            )
            self.write(user, name, content)
            center = self.read(user, "context.md")
            if name not in center["content"]:
                self.write(
                    user,
                    "context.md",
                    center["content"] + f"\n- [人工拒绝回答记录]({name})（未形成答案）\n",
                )

    def clear_all(self) -> None:
        with self.lock:
            if self.deleted:
                raise ValueError("Talker 的 Wiki 已删除。")
            self._clear_all()

    def _clear_all(self) -> None:
        for role in USERS:
            directory = self.root / USER_IDS[role]
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir(parents=True)
            (directory / "context.md").write_text(
                f"# {role.title()} 的上下文\n\nWiki 已清空，暂无已保存的信息。\n", encoding="utf-8"
            )
        self.reset_at = datetime.now(UTC).isoformat()
        (self.root / ".reset-at").write_text(self.reset_at, encoding="utf-8")
