"""Native LangGraph checkpointer and Store backed by MySQL, sync and async.

Stores complete typed checkpoints, parent namespaces, metadata and task writes.
No pickled data and no fallback to memory or a local database.
"""

import asyncio
import json
from datetime import UTC, datetime

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    CheckpointTuple,
    get_checkpoint_metadata,
)
from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    PutOp,
    SearchItem,
    SearchOp,
)
from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert

from .db import checkpoints, kv, now, transaction, writes


class MySQLSaver(BaseCheckpointSaver):
    def __init__(self, *, require_grant=False, **kwargs):
        super().__init__(**kwargs)
        self.require_grant = require_grant
        self._bound_runs = {}

    def bind_run(self, run):
        self._bound_runs[run["id"]] = dict(run)

    def release_run(self, run):
        self._bound_runs.pop(run["id"], None)

    def _guard(self, c, config):
        if not self.require_grant:
            return
        from .db import runs, users

        run = self._bound_runs.get(config.get("configurable", {}).get("duet_app_run_id"))
        if not run:
            raise PermissionError("Checkpoint write requires a bound Run")
        live = (
            c.execute(select(runs).where(runs.c.id == run["id"]).with_for_update()).mappings().one()
        )
        actor = c.execute(select(users).where(users.c.id == run["actor_user_id"])).mappings().one()
        thread_id = config["configurable"]["thread_id"]
        if thread_id != run["thread_id"] and not thread_id.startswith(
            "projection-" + run["record_id"] + "-v"
        ):
            raise PermissionError("Checkpoint scope mismatch")
        if (
            live["lease_token"] != run["lease_token"]
            or live["status"] != "running"
            or not live["lease_until"]
            or live["lease_until"] < now()
            or live["cancel_requested"]
            or actor["auth_version"] != run["auth_version"]
            or actor["status"] != "active"
        ):
            raise PermissionError("Checkpoint writer lease or grant expired")

    def _where(self, config):
        cfg = config["configurable"]
        return (
            checkpoints.c.thread_id == cfg["thread_id"],
            checkpoints.c.namespace == cfg.get("checkpoint_ns", ""),
        )

    def _tuple(self, c, r):
        cfg = {
            "configurable": {
                "thread_id": r["thread_id"],
                "checkpoint_ns": r["namespace"],
                "checkpoint_id": r["checkpoint_id"],
            }
        }
        pending = [
            (w["task_id"], w["channel"], self.serde.loads_typed((w["type"], bytes(w["data"]))))
            for w in c.execute(
                select(writes)
                .where(
                    writes.c.thread_id == r["thread_id"],
                    writes.c.namespace == r["namespace"],
                    writes.c.checkpoint_id == r["checkpoint_id"],
                )
                .order_by(writes.c.task_id, writes.c.idx)
            ).mappings()
        ]
        parent = (
            {"configurable": {**cfg["configurable"], "checkpoint_id": r["parent_id"]}}
            if r["parent_id"]
            else None
        )
        return CheckpointTuple(
            cfg,
            self.serde.loads_typed((r["type"], bytes(r["data"]))),
            self.serde.loads_typed((r["metadata_type"], bytes(r["metadata_data"]))),
            parent,
            pending,
        )

    def get_tuple(self, config):
        q = select(checkpoints).where(*self._where(config))
        checkpoint_id = config["configurable"].get("checkpoint_id")
        if checkpoint_id:
            q = q.where(checkpoints.c.checkpoint_id == checkpoint_id)
        with transaction() as c:
            r = (
                c.execute(q.order_by(checkpoints.c.checkpoint_id.desc()).limit(1))
                .mappings()
                .first()
            )
            return self._tuple(c, r) if r else None

    def list(self, config=None, *, filter=None, before=None, limit=None):
        q = select(checkpoints)
        if config:
            q = q.where(checkpoints.c.thread_id == config["configurable"]["thread_id"])
            if "checkpoint_ns" in config["configurable"]:
                q = q.where(checkpoints.c.namespace == config["configurable"]["checkpoint_ns"])
        if before:
            q = q.where(checkpoints.c.checkpoint_id < before["configurable"]["checkpoint_id"])
        result = []
        with transaction() as c:
            for r in c.execute(q.order_by(checkpoints.c.checkpoint_id.desc())).mappings():
                item = self._tuple(c, r)
                if filter and any(item.metadata.get(k) != v for k, v in filter.items()):
                    continue
                result.append(item)
                if limit is not None and len(result) >= limit:
                    break
        yield from result

    def put(self, config, checkpoint, metadata, new_versions):
        cfg = config["configurable"]
        kind, data = self.serde.dumps_typed(checkpoint)
        mtype, mdata = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        with transaction() as c:
            self._guard(c, config)
            c.execute(
                insert(checkpoints)
                .values(
                    thread_id=cfg["thread_id"],
                    namespace=cfg.get("checkpoint_ns", ""),
                    checkpoint_id=checkpoint["id"],
                    parent_id=cfg.get("checkpoint_id"),
                    type=kind,
                    data=data,
                    metadata_type=mtype,
                    metadata_data=mdata,
                )
                .on_duplicate_key_update(
                    type=kind, data=data, metadata_type=mtype, metadata_data=mdata
                )
            )
        return {
            "configurable": {
                **cfg,
                "thread_id": cfg["thread_id"],
                "checkpoint_ns": cfg.get("checkpoint_ns", ""),
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(self, config, values, task_id, task_path=""):
        cfg = config["configurable"]
        with transaction() as c:
            self._guard(c, config)
            for index, (channel, value) in enumerate(values):
                kind, data = self.serde.dumps_typed(value)
                idx = WRITES_IDX_MAP.get(channel, index)
                statement = insert(writes).values(
                    thread_id=cfg["thread_id"],
                    namespace=cfg.get("checkpoint_ns", ""),
                    checkpoint_id=cfg["checkpoint_id"],
                    task_id=task_id,
                    idx=idx,
                    channel=channel,
                    task_path=task_path,
                    type=kind,
                    data=data,
                )
                # Ordinary task output is first-write-wins; error/interrupt slots update.
                c.execute(
                    statement.on_duplicate_key_update(
                        **(
                            {"channel": channel, "type": kind, "data": data, "task_path": task_path}
                            if idx < 0
                            else {"idx": idx}
                        )
                    )
                )

    def delete_thread(self, thread_id):
        with transaction() as c:
            c.execute(delete(writes).where(writes.c.thread_id == thread_id))
            c.execute(delete(checkpoints).where(checkpoints.c.thread_id == thread_id))

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config=None, *, filter=None, before=None, limit=None):
        items = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for item in items:
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        return await asyncio.to_thread(self.delete_thread, thread_id)


class MySQLStore(BaseStore):
    """Durable namespace/key operations; lexical search is explicit (no fake vectors)."""

    @staticmethod
    def ns(value):
        encoded = json.dumps(list(value), separators=(",", ":"), ensure_ascii=False)
        if len(encoded) > 191:
            raise ValueError("Store namespace too long")
        return encoded

    @staticmethod
    def item(r, *, search=False, score=None):
        values = dict(
            value=r["value"],
            key=r["key"],
            namespace=tuple(json.loads(r["namespace"])),
            created_at=datetime.fromtimestamp(r["created_at"], UTC),
            updated_at=datetime.fromtimestamp(r["updated_at"], UTC),
        )
        return SearchItem(**values, score=score) if search else Item(**values)

    def batch(self, ops):
        result = []
        with transaction() as c:
            for op in ops:
                if isinstance(op, GetOp):
                    r = (
                        c.execute(
                            select(kv).where(
                                kv.c.namespace == self.ns(op.namespace), kv.c.key == op.key
                            )
                        )
                        .mappings()
                        .first()
                    )
                    result.append(self.item(r) if r else None)
                elif isinstance(op, PutOp):
                    condition = (kv.c.namespace == self.ns(op.namespace), kv.c.key == op.key)
                    if op.value is None:
                        c.execute(delete(kv).where(*condition))
                    else:
                        c.execute(
                            insert(kv)
                            .values(
                                namespace=self.ns(op.namespace),
                                key=op.key,
                                value=op.value,
                                created_at=now(),
                                updated_at=now(),
                            )
                            .on_duplicate_key_update(value=op.value, updated_at=now())
                        )
                    result.append(None)
                elif isinstance(op, SearchOp):
                    matches = []
                    # Namespace prefix is structured; JSON wildcard matching would leak scopes.
                    for r in c.execute(select(kv)).mappings():
                        ns = tuple(json.loads(r["namespace"]))
                        if ns[: len(op.namespace_prefix)] != op.namespace_prefix:
                            continue
                        if op.filter and any(r["value"].get(k) != v for k, v in op.filter.items()):
                            continue
                        query = (op.query or "").casefold()
                        if (
                            query
                            and query not in json.dumps(r["value"], ensure_ascii=False).casefold()
                        ):
                            continue
                        matches.append(self.item(r, search=True, score=1.0 if query else None))
                    matches.sort(key=lambda x: x.updated_at, reverse=True)
                    result.append(matches[op.offset : op.offset + op.limit])
                elif isinstance(op, ListNamespacesOp):
                    values = set()
                    for encoded in c.execute(select(kv.c.namespace).distinct()).scalars():
                        ns = tuple(json.loads(encoded))
                        valid = True
                        for cond in op.match_conditions or []:
                            segment = (
                                ns[: len(cond.path)]
                                if cond.match_type == "prefix"
                                else ns[-len(cond.path) :]
                            )
                            valid &= len(segment) == len(cond.path) and all(
                                p == "*" or p == s for p, s in zip(cond.path, segment)
                            )
                        if valid:
                            values.add(ns[: op.max_depth] if op.max_depth else ns)
                    result.append(sorted(values)[op.offset : op.offset + op.limit])
                else:
                    raise NotImplementedError(type(op).__name__)
        return result

    async def abatch(self, ops):
        return await asyncio.to_thread(self.batch, list(ops))
