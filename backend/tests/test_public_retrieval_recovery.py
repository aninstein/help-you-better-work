"""Public retrieval recovers transient live-source failures without using login pages."""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from app.agent import call_search
from app.agent.call_search import CallSearch, relevant_results


def test_transient_weather_timeout_retries_the_live_source(monkeypatch):
    source = "https://api.open-meteo.com/v1/forecast?latitude=31&longitude=121"
    monkeypatch.setattr(call_search, "search_web", AsyncMock(return_value={"results": [{
        "title": "Shanghai weather today", "url": source,
        "source_kind": "current_weather",
    }]}))
    fetch = AsyncMock(side_effect=[TimeoutError(), {
        "url": source, "content": json.dumps({
            "timezone": "UTC", "current": {
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "temperature_2m": 18.5,
            },
        }),
    }])
    monkeypatch.setattr(call_search, "fetch_web", fetch)
    assessor = AsyncMock()
    evidence, sources = asyncio.run(CallSearch(assess=assessor).lookup("Shanghai weather today"))
    assert sources == [source] and "18.5" in evidence
    assert fetch.await_count == 2
    assessor.assert_not_awaited()


def test_login_pages_do_not_consume_the_public_source_read_budget():
    login = {"title": "Example daily active users login", "url": "https://example.com/login/email"}
    report = {"title": "Example daily active users report", "url": "https://example.com/reports/users"}
    assert relevant_results("Example daily active users", [login, report]) == [report]
