"""Official DeerFlow MemoryManager port with MySQL owner-bound documents."""

from typing import ClassVar

from deerflow.agents.memory.manager import MemoryManager
from sqlalchemy import func, or_, select

from .db import memories, threads, transaction, workspaces
from .wiki import public_source_guard


class MySQLMemoryManager(MemoryManager):
    supports_search: ClassVar[bool] = True

    @classmethod
    def from_config(cls, backend_config, *, mode="middleware", **host_hooks):
        return cls(backend_config=backend_config, mode=mode, callbacks=host_hooks.get("callbacks"))

    @classmethod
    def read_failures_are_fatal_for_config(cls, backend_config):
        return True

    def add(self, thread_id, messages, *, agent_name=None, user_id=None, trace_id=None):
        # Inputs/outputs have already been committed as canonical conversation_records.
        # Durable semantic facts are written by the native agent's remember tool.
        # Do not store an unfiltered transcript (which can contain private/control state).
        with transaction() as c:
            owner = c.execute(
                select(workspaces.c.owner_user_id)
                .join(threads, threads.c.workspace_id == workspaces.c.id)
                .where(threads.c.id == thread_id)
            ).scalar()
            if owner and owner != user_id:
                raise PermissionError("Memory owner mismatch")

    def _documents(self, user_id, query=None, limit=40):
        if not user_id:
            raise PermissionError("Memory requires server-bound user_id")
        with transaction() as c:
            q = (
                select(memories)
                .join(workspaces, workspaces.c.id == memories.c.workspace_id)
                .where(memories.c.visibility == "user_shared")
            )
            q = q.where(workspaces.c.owner_user_id == user_id)
            q = q.where(
                func.coalesce(func.JSON_CONTAINS(memories.c.metadata, "true", "$.archived"), 0) == 0
            )
            q = q.where(public_source_guard())
            from .answer_policy import origin

            q = q.where(origin() != "answer_policy")
            from .retrieval import terms

            tokens = terms(query or "")
            if tokens:
                q = q.where(
                    or_(
                        *(memories.c.text.contains(t, autoescape=True) for t in tokens),
                        *(memories.c.name.contains(t, autoescape=True) for t in tokens),
                    )
                )
            from .retrieval import sql_score

            ordering = (
                [sql_score(func.concat(memories.c.name, memories.c.text), query).desc()]
                if query
                else []
            )
            values = [
                dict(r)
                for r in c.execute(
                    q.order_by(*ordering, memories.c.created_at.desc()).limit(200)
                ).mappings()
            ]
        values = [value for value in values if not value["metadata"].get("archived")]
        if query:
            from .retrieval import score

            values.sort(key=lambda d: score(d["name"] + d["text"], query), reverse=True)
        return values[:limit]

    def get_context(self, user_id, *, agent_name=None, thread_id=None, query=None):
        values = self._documents(user_id, query)
        principles = [
            row
            for row in self._documents(user_id, limit=200)
            if row["metadata"].get("origin") == "hinter" and row["metadata"].get("principle")
        ][:40]
        values = list({row["id"]: row for row in [*principles, *values]}.values())
        return "\n".join(
            f"[Wiki：{r['metadata'].get('title') or r['name']}，修订 {r['revision']}] {r['text']}"
            for r in values
        )[:24000]

    def get_memory(self, *, agent_name=None, user_id=None):
        return {"documents": self._documents(user_id)}

    def search(self, query, top_k=10, *, user_id=None, agent_name=None, category=None):
        from .retrieval import excerpt

        return [
            {
                "id": r["id"],
                "content": excerpt(r["text"], query),
                "name": "Hinter Wiki：" + r["metadata"].get("title", r["name"])
                if r["metadata"].get("origin") == "hinter"
                else r["name"],
                "revision": r["revision"],
                "source_characters": len(r["text"]),
            }
            for r in self._documents(user_id, query, min(max(top_k, 1), 40))
        ]
