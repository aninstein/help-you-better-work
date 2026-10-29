"""Private administrator controls, evaluated outside the model and Wiki recall.

Uses existing versioned MySQL documents with origin=answer_policy. Only the
dedicated admin API can modify them; ordinary memory and retrieval exclude them.
"""

import hashlib
import re
import unicodedata

from sqlalchemy import func, insert, select, update

from .db import memories, now, records, revisions, threads, transaction, workspaces


class AnswerRestricted(Exception):
    def __init__(self, language="en"):
        self.language = language
        super().__init__("ANSWER_RESTRICTED")


def origin():
    return func.coalesce(func.JSON_UNQUOTE(func.JSON_EXTRACT(memories.c.metadata, "$.origin")), "")


def normalize(text):
    return unicodedata.normalize("NFKC", text).casefold()


def contains(text, alias):
    alias = extract_ban(alias) or alias
    value = "".join(ch for ch in normalize(alias) if ch.isalnum())
    if not value:
        return False
    pattern = r"[\W_]*".join(re.escape(ch) for ch in value)
    if value[0].isascii():
        pattern = r"(?<![a-z0-9])" + pattern
    if value[-1].isascii():
        pattern += r"(?![a-z0-9])"
    return bool(re.search(pattern, normalize(text)))


def active(c, workspace_id):
    values = [
        dict(row)
        for row in c.execute(
            select(memories).where(
                origin() == "answer_policy",
                owner_scope(c, workspace_id),
            )
        ).mappings()
        if row["metadata"].get("enabled", True)
    ]
    result = []
    for row in values:
        sources = row["metadata"].get("source_records", [])
        if sources and not row["metadata"].get("manual"):
            live = list(
                c.execute(
                    select(records.c.text).where(
                        records.c.id.in_(sources),
                        records.c.role == "hinter",
                        records.c.withdrawn.is_(False),
                    )
                ).scalars()
            )
            expected = {
                "".join(ch for ch in normalize(alias) if ch.isalnum())
                for alias in row["metadata"]["aliases"]
            }
            if not any(
                "".join(ch for ch in normalize(extract_ban(value) or "") if ch.isalnum())
                in expected
                for value in live
            ):
                continue
        result.append(row)
    return result


def owner_scope(c, workspace_id):
    owner = c.execute(
        select(workspaces.c.owner_user_id).where(workspaces.c.id == workspace_id)
    ).scalar_one()
    if not owner:
        return memories.c.workspace_id == workspace_id
    return memories.c.workspace_id.in_(
        select(workspaces.c.id).where(workspaces.c.owner_user_id == owner)
    )


def find_policy(c, workspace_id, policy_id, canonical):
    row = c.execute(select(memories).where(memories.c.id == policy_id)).mappings().first()
    if not row:
        for candidate in c.execute(
            select(memories).where(origin() == "answer_policy", owner_scope(c, workspace_id))
        ).mappings():
            first = candidate["metadata"]["aliases"][0]
            first = extract_ban(first) or first
            if "".join(ch for ch in normalize(first) if ch.isalnum()) == canonical:
                row = candidate
                break
    return (
        c.execute(select(memories).where(memories.c.id == row["id"]).with_for_update())
        .mappings()
        .one()
        if row
        else None
    )


def extract_ban(text):
    """Only explicit administrator bans, never inferred from ordinary private facts."""
    text = text.strip().rstrip("。.!！ ")
    patterns = (
        r"^(?:请|帮我)?(?:不准|禁止|不要|不能|不允许)(?:再)?(?:回答|讨论)(?:任何|所有)?(?:关于)?(.+?)(?:相关的?问题|的任何问题|的所有问题|的问题|相关问题|任何问题)?$",
        r"^(?:请|帮我)?限制(?:所有|任何)?关于(.+?)(?:的?相关问题)[，,\s]*禁止回答$",
        r"^(?:do not|don't|never) (?:answer|discuss)(?: any)?(?: questions?)?"
        r"(?: about| regarding| related to)? (.+)$",
    )
    for pattern in patterns:
        found = re.fullmatch(pattern, text, re.I)
        if found and 1 <= len(found[1].strip()) <= 100:
            topic = found[1].strip()
            if topic not in {"错误", "错误的", "不真实", "不准确", "未经证实", "不确定"}:
                return topic
    return None


def sync_from_hint(c, record):
    if record["role"] != "hinter" or record["kind"] not in ("guidance", "legacy_record"):
        return
    topic = extract_ban(record["text"])
    if not topic or record["withdrawn"]:
        return
    canonical = "".join(ch for ch in normalize(topic) if ch.isalnum())
    workspace_id = c.execute(
        select(threads.c.workspace_id).where(threads.c.id == record["thread_id"])
    ).scalar_one()
    owner = c.execute(
        select(workspaces.c.owner_user_id).where(workspaces.c.id == workspace_id)
    ).scalar_one()
    policy_id = (
        "memory_policy_"
        + hashlib.sha256(f"{owner or workspace_id}:{canonical}".encode()).hexdigest()[:32]
    )
    previous = find_policy(c, workspace_id, policy_id, canonical)
    if previous:
        policy_id = previous["id"]
        sources = list(
            dict.fromkeys([*previous["metadata"].get("source_records", []), record["id"]])
        )
        if sources != previous["metadata"].get("source_records"):
            c.execute(
                update(memories)
                .where(memories.c.id == policy_id)
                .values(
                    metadata={**previous["metadata"], "source_records": sources},
                    revision=previous["revision"] + 1,
                )
            )
        return
    workspace_id = c.execute(
        select(threads.c.workspace_id).where(threads.c.id == record["thread_id"])
    ).scalar_one()
    aliases = [topic]
    digit_words = dict(
        zip("0123456789", "zero one two three four five six seven eight nine".split())
    )
    if re.search(r"[a-z].*\d", topic, re.I):
        aliases.append(
            re.sub(r"\d", lambda match: " " + digit_words[match[0]] + " ", topic).strip()
        )
    c.execute(
        insert(memories).values(
            id=policy_id,
            workspace_id=workspace_id,
            name="回答限制 · " + canonical[:80],
            text="禁止回答：" + topic,
            visibility="admin_private",
            revision=1,
            created_at=now(),
            metadata={
                "origin": "answer_policy",
                "memory_layer": "prohibition",
                "aliases": aliases,
                "scope": "talker",
                "enabled": True,
                "source_records": [record["id"]],
            },
        )
    )


def backfill():
    with transaction() as c:
        for row in list(c.execute(select(memories).where(origin() == "answer_policy")).mappings()):
            aliases = list(
                dict.fromkeys(
                    " ".join((extract_ban(alias) or alias).split())
                    for alias in row["metadata"]["aliases"]
                )
            )
            if aliases != row["metadata"]["aliases"]:
                c.execute(
                    update(memories)
                    .where(memories.c.id == row["id"])
                    .values(
                        text="禁止回答：" + "、".join(aliases),
                        metadata={**row["metadata"], "aliases": aliases},
                        revision=row["revision"] + 1,
                    )
                )
        hints = list(c.execute(select(records).where(records.c.role == "hinter")).mappings())
        for hint in hints:
            sync_from_hint(c, hint)
        return c.execute(
            select(func.count()).select_from(memories).where(origin() == "answer_policy")
        ).scalar_one()


def match(c, workspace_id, text):
    return next(
        (
            rule
            for rule in active(c, workspace_id)
            if any(contains(text, alias) for alias in rule["metadata"]["aliases"])
        ),
        None,
    )


def match_input(c, thread_id, text):
    workspace_id = c.execute(
        select(threads.c.workspace_id).where(threads.c.id == thread_id)
    ).scalar_one()
    result = match(c, workspace_id, text)
    if result:
        return result
    # A short anaphoric follow-up cannot bypass a restriction by omitting the name.
    if len(text) <= 300 and re.search(
        r"它|该产品|这个产品|继续|再说|更多|\b(?:it|its|they|their|that|more|continue)\b",
        text,
        re.I,
    ):
        previous = (
            c.execute(
                select(records)
                .where(
                    records.c.thread_id == thread_id,
                    records.c.role == "user",
                    records.c.visibility == "user_shared",
                    records.c.withdrawn.is_(False),
                )
                .order_by(records.c.seq.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if previous:
            return match(c, workspace_id, previous["text"]) or next(
                (
                    rule
                    for rule in active(c, workspace_id)
                    if rule["id"] == previous["metadata"].get("answer_policy_id")
                ),
                None,
            )
    return None


def run_input(c, run):
    source = c.execute(select(records).where(records.c.id == run["record_id"])).mappings().one()
    target = source
    if source["role"] == "hinter" and source["source_record_id"]:
        target = (
            c.execute(select(records).where(records.c.id == source["source_record_id"]))
            .mappings()
            .one()
        )
    text = target["text"]
    if target["id"] == run["record_id"] and target["revision"] != run["source_revision"]:
        text = c.execute(
            select(revisions.c.text).where(
                revisions.c.record_id == target["id"],
                revisions.c.revision == run["source_revision"],
            )
        ).scalar_one()
    from .input_preprocessing import detect_language

    language = target["metadata"].get("input_preprocessing", {}).get(
        "reply_language"
    ) or detect_language(text)
    workspace_id = c.execute(
        select(threads.c.workspace_id).where(threads.c.id == run["thread_id"])
    ).scalar_one()
    return text, language, workspace_id, target


def ensure_run_allowed(c, run, output=""):
    text, language, workspace_id, source = run_input(c, run)
    rule = match(c, workspace_id, text) or (match(c, workspace_id, output) if output else None)
    if (
        not rule
        and len(text) <= 300
        and re.search(
            r"它|该产品|这个产品|继续|再说|更多|\b(?:it|its|they|their|that|more|continue)\b",
            text,
            re.I,
        )
    ):
        previous = (
            c.execute(
                select(records)
                .where(
                    records.c.thread_id == run["thread_id"],
                    records.c.seq < source["seq"],
                    records.c.role == "user",
                    records.c.visibility == "user_shared",
                    records.c.withdrawn.is_(False),
                )
                .order_by(records.c.seq.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        rule = (
            (
                match(c, workspace_id, previous["text"])
                or next(
                    (
                        entry
                        for entry in active(c, workspace_id)
                        if entry["id"] == previous["metadata"].get("answer_policy_id")
                    ),
                    None,
                )
            )
            if previous
            else None
        )
    if rule:
        raise AnswerRestricted(language)


def refusal(language):
    return {
        "zh": "我暂时无法回答这个问题，可以换一个问题提问。",
        "en": "I can’t answer that right now. Please ask a different question.",
        "ja": "この質問には今お答えできません。別の質問をお願いします。",
        "ko": "지금은 이 질문에 답할 수 없습니다. 다른 질문을 해 주세요.",
        "fr": "Je ne peux pas répondre sur ce sujet.",
        "es": "No puedo responder sobre este tema.",
        "ru": "Я не могу отвечать на эту тему.",
        "ar": "لا يمكنني الإجابة عن هذا الموضوع.",
    }.get(language, "I can’t discuss this topic.")


def create(c, actor, workspace_id, aliases, scope="talker", *, source_record_id=None):
    from .auth import audit_event, fail, require
    from .repository import workspace_access

    require(actor, "guidance.write")
    workspace = workspace_access(c, actor, workspace_id, write=True)
    aliases = list(
        dict.fromkeys((extract_ban(value) or value).strip() for value in aliases if value.strip())
    )
    if (
        not aliases
        or len(aliases) > 20
        or any(len(v) > 100 or not any(ch.isalnum() for ch in v) for v in aliases)
    ):
        fail(422, "VALIDATION_ERROR", "请填写 1–20 个有效主题或别名，每项最多 100 字。")
    if scope != "talker":
        fail(422, "VALIDATION_ERROR", "回答限制的作用范围无效。")
    canonical = "".join(ch for ch in normalize(aliases[0]) if ch.isalnum())
    owner = workspace["owner_user_id"] or workspace_id
    policy_id = "memory_policy_" + hashlib.sha256(f"{owner}:{canonical}".encode()).hexdigest()[:32]
    previous = find_policy(c, workspace_id, policy_id, canonical)
    if previous:
        policy_id = previous["id"]
        metadata = {
            **previous["metadata"],
            "aliases": list(dict.fromkeys([*previous["metadata"]["aliases"], *aliases])),
            "manual": True,
            "enabled": True,
        }
        c.execute(
            update(memories)
            .where(memories.c.id == policy_id)
            .values(metadata=metadata, revision=previous["revision"] + 1)
        )
        audit_event(c, actor["id"], "answer_policy.updated", policy_id, {"enabled": True})
        return public({**previous, "metadata": metadata, "revision": previous["revision"] + 1})
    item = {
        "id": policy_id,
        "workspace_id": workspace_id,
        "name": "回答限制 · " + policy_id[-32:],
        "text": "禁止回答：" + "、".join(aliases),
        "visibility": "admin_private",
        "revision": 1,
        "created_at": now(),
        "metadata": {
            "origin": "answer_policy",
            "memory_layer": "prohibition",
            "aliases": aliases,
            "scope": scope,
            "enabled": True,
            "manual": True,
            "source_record_id": source_record_id,
        },
    }
    c.execute(insert(memories).values(**item))
    audit_event(
        c,
        actor["id"],
        "answer_policy.created",
        policy_id,
        {"scope": scope, "workspace_id": workspace_id},
    )
    return public(item)


def public(row):
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "version": row["revision"],
        "aliases": row["metadata"]["aliases"],
        "scope": row["metadata"]["scope"],
        "enabled": row["metadata"].get("enabled", True),
    }


def list_rules(actor, workspace_id):
    from .auth import require
    from .repository import workspace_access

    require(actor, "guidance.write")
    with transaction() as c:
        workspace_access(c, actor, workspace_id)
        return [
            public(row)
            for row in c.execute(
                select(memories)
                .where(
                    origin() == "answer_policy",
                    owner_scope(c, workspace_id),
                )
                .order_by(memories.c.created_at.desc())
            ).mappings()
        ]


def set_enabled(actor, policy_id, enabled, version):
    from .auth import audit_event, fail, require
    from .repository import workspace_access

    require(actor, "guidance.write")
    with transaction() as c:
        row = (
            c.execute(
                select(memories).where(memories.c.id == policy_id, origin() == "answer_policy")
            )
            .mappings()
            .first()
        )
        if not row:
            fail(404, "NOT_FOUND", "回答限制不存在。")
        workspace_access(c, actor, row["workspace_id"], write=True)
        row = (
            c.execute(select(memories).where(memories.c.id == policy_id).with_for_update())
            .mappings()
            .one()
        )
        if row["revision"] != version:
            fail(409, "VERSION_CONFLICT", "规则已更新，请重新加载。")
        metadata = {**row["metadata"], "enabled": enabled}
        c.execute(
            update(memories)
            .where(memories.c.id == policy_id)
            .values(metadata=metadata, revision=version + 1)
        )
        audit_event(c, actor["id"], "answer_policy.updated", policy_id, {"enabled": enabled})
        return public({**row, "metadata": metadata, "revision": version + 1})


if __name__ == "__main__":
    print(f"Shared prohibition memories: {backfill()}")
