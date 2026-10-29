"""Durable classical Chinese display-name pool; account names may repeat."""

import json
import logging
import re
import secrets

from sqlalchemy import func, insert, select, text, update

from .db import engine, flower_names, now, transaction, uid

log = logging.getLogger(__name__)
BATCH_SIZE = 1000
REFILL_BELOW = 100
VALID_NAME = re.compile(r"^[\u4e00-\u9fff]{2,5}$")

PREFIXES = (
    "清", "云", "长", "秋", "星", "竹", "青", "明", "怀", "知", "若", "听", "疏",
    "南", "望", "归", "映", "临", "予", "安", "兰", "沐", "和", "遥", "静", "书",
    "风", "雪", "月", "松", "溪", "栖", "昭", "晚", "初", "含", "渺", "寻", "砚", "墨",
)
ENDINGS = (
    "山", "川", "月", "雨", "岚", "舟", "歌", "禾", "溪", "松", "风", "雪", "霁",
    "墨", "砚", "宁", "棠", "汀", "澜", "笙", "亭", "荷", "鸿", "竹", "微", "瑾",
    "清和", "听雨", "观云", "知秋", "望月", "临风", "归舟", "怀瑾", "竹影", "星河",
    "归雁", "松声", "云岚", "山月", "明溪", "疏影", "清欢", "南枝", "晚晴", "流萤",
)


def curated_names(exclude=()):
    """Offline reserve for first boot and an unavailable model provider."""
    excluded = set(exclude)
    values = []
    for prefix in PREFIXES:
        for ending in ENDINGS:
            name = prefix + ending
            if prefix not in ending and name not in excluded and VALID_NAME.fullmatch(name):
                values.append(name)
    for first in PREFIXES:
        for second in PREFIXES:
            for ending in ENDINGS[:26]:
                name = first + second + ending
                if first != second and name not in excluded and VALID_NAME.fullmatch(name):
                    values.append(name)
    for prefix in PREFIXES:
        for middle in ENDINGS[26:]:
            for ending in ENDINGS[:26]:
                name = prefix + middle + ending
                if prefix not in middle and name not in excluded and VALID_NAME.fullmatch(name):
                    values.append(name)
    for prefix in PREFIXES:
        for middle in ENDINGS[26:]:
            for ending in ENDINGS[26:]:
                name = prefix + middle + ending
                if prefix not in middle and name not in excluded and VALID_NAME.fullmatch(name):
                    values.append(name)
    return list(dict.fromkeys(values))


def add_batch(c, batch, values):
    timestamp = now()
    c.execute(insert(flower_names), [
        {"id": uid("flower"), "batch": batch, "name": value,
         "used_at": None, "created_at": timestamp}
        for value in values
    ])


def seed(c):
    if c.execute(select(func.count()).select_from(flower_names)).scalar_one():
        return
    values = curated_names()
    secrets.SystemRandom().shuffle(values)
    values = values[:BATCH_SIZE]
    if len(values) != BATCH_SIZE:
        raise RuntimeError("Initial flower-name pool is too small")
    add_batch(c, 1, values)


def claim(c):
    """Atomically mark one suggestion used across concurrent API workers."""
    row = c.execute(
        select(flower_names.c.id, flower_names.c.name)
        .where(flower_names.c.used_at.is_(None))
        .order_by(flower_names.c.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).one_or_none()
    if row is None:
        return None
    c.execute(update(flower_names).where(flower_names.c.id == row.id).values(used_at=now()))
    return row.name


def remaining(c):
    return c.execute(
        select(func.count()).select_from(flower_names).where(flower_names.c.used_at.is_(None))
    ).scalar_one()


def _model_names(previous):
    """One bounded model request per refill; invalid output is discarded."""
    from .runtime import selected_model

    model = selected_model()
    if model.get("provider") == "ollama":
        from langchain_ollama import ChatOllama

        client = ChatOllama(model=model["model"], base_url=model["base_url"],
                            temperature=0.7, num_predict=4096, format="json")
    else:
        from langchain_openai import ChatOpenAI

        deepseek = "deepseek.com" in model["base_url"]
        client = ChatOpenAI(model=model["model"], base_url=model["base_url"],
                            api_key=model.get("api_key") or "local",
                            temperature=0.7, max_tokens=4096, timeout=90, max_retries=1,
                            **({"extra_body": {"thinking": {"type": "disabled"}},
                                "response_format": {"type": "json_object"}} if deepseek else {}))
    prompt = (
        "请创作 200 个雅致、易读的中文古典风格花名，每个 2 到 5 个汉字。"
        "只返回 JSON 对象，格式为 {\"names\":[\"花名\",...]}，不要解释。"
        "避免常见真实姓名、敏感词、数字和标点。上一批已有这些名字，不要重复："
        + "、".join(previous)
    )
    response = client.invoke(prompt)
    raw = json.loads(response.content if isinstance(response.content, str) else "")
    values = raw.get("names", [])
    return [value for value in values if isinstance(value, str) and VALID_NAME.fullmatch(value)]


def refill_if_low():
    """Worker-side refill, serialized by a MySQL advisory lock."""
    with transaction() as c:
        if remaining(c) >= REFILL_BELOW:
            return False
    with engine().connect() as c:
        if c.execute(text("SELECT GET_LOCK('duet_flower_name_refill', 0)")).scalar() != 1:
            return False
        c.commit()
        try:
            with transaction() as db:
                if remaining(db) >= REFILL_BELOW:
                    return False
                last = db.execute(select(func.max(flower_names.c.batch))).scalar_one() or 0
                previous = set(db.execute(
                    select(flower_names.c.name).where(flower_names.c.batch == last)
                ).scalars())
            generated = []
            try:
                generated = _model_names(sorted(previous))
            except Exception as exc:
                log.warning("Flower-name model refill unavailable: %s", type(exc).__name__)
            available = list(dict.fromkeys(
                value for value in generated if value not in previous
            ))
            reserve = curated_names(previous | set(available))
            secrets.SystemRandom().shuffle(reserve)
            available.extend(reserve[:BATCH_SIZE - len(available)])
            if len(available) < BATCH_SIZE:
                raise RuntimeError("Flower-name reserve exhausted")
            with transaction() as db:
                if remaining(db) >= REFILL_BELOW:
                    return False
                add_batch(db, last + 1, available[:BATCH_SIZE])
            return True
        finally:
            c.execute(text("SELECT RELEASE_LOCK('duet_flower_name_refill')"))
            c.commit()
