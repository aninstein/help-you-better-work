"""Bounded lexical retrieval for owner-scoped records and memory documents."""

import re


def terms(query):
    values = []
    for part in re.findall(r"[a-zA-Z0-9_-]+|[\u4e00-\u9fff]+", query.casefold()[:300]):
        values.append(part)
        if len(part) > 2 and re.fullmatch(r"[\u4e00-\u9fff]+", part):
            values.extend(part[i : i + 2] for i in range(len(part) - 1))
    return list(dict.fromkeys(values))[:24]


def score(text, query):
    text = text.casefold()
    return (20 if query.casefold() in text else 0) + sum(len(t) for t in terms(query) if t in text)


def excerpt(text, query, length=4000):
    if len(text) <= length:
        return text
    positions = [text.casefold().find(t) for t in terms(query) if t in text.casefold()]
    start = max(0, min(positions, default=0) - 200)
    return text[start : start + length]


def sql_score(column, query):
    from sqlalchemy import case

    value = case((column.contains(query, autoescape=True), 20), else_=0)
    for token in terms(query):
        value += case((column.contains(token, autoescape=True), len(token)), else_=0)
    return value
