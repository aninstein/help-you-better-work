"""Grounding, retry targets and human corrections replace obsolete search work."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from html import escape
from unittest.mock import AsyncMock
from urllib.parse import quote

import httpx
import pytest

from app.agent.call_policy import CallPolicy
from app.agent.call_reasoner import CallReasoner
from app.agent.tool_config import ToolConfig
from app.agent.web_tools import focus_content, search_web

QUESTION = "What is TikTok's annual revenue?"
HINT = "TikTok年度营收为120亿美元，统计年度是2025年，已核实。"


def plan(text=QUESTION, **extra):
    return dict(
        text=text,
        action="lookup",
        category="data_lookup",
        data_scope="public",
        evidence_status="missing",
        **extra,
    )


def test_scope_is_model_metadata_not_a_product_name():
    for name in ["AICO", "Nimbus", "Orchid"]:
        text = f"What efficiency improvement did {name} achieve?"
        base = {"text": text, "category": "data_lookup", "action": "answer"}
        assert CallPolicy().decide({**base, "data_scope": "internal"}, "")[0] == "await_human"
        assert CallPolicy().decide({**base, "data_scope": "public"}, "")[0] == "lookup"
        assert CallPolicy().decide({**base, "data_scope": "unknown"}, "")[0] == "clarify"


def test_wiki_scope_context_does_not_count_as_the_requested_public_fact():
    assert (
        CallPolicy().decide(
            {
                "text": "What improvement did Nimbus achieve?",
                "category": "data_lookup",
                "data_scope": "public",
                "action": "lookup",
                "uses_wiki_facts": True,
                "evidence_status": "missing",
            },
            "",
        )[0]
        == "lookup"
    )


def test_full_call_hint_is_bound_to_the_active_question(tmp_path):
    from unittest.mock import Mock

    from test_call_reviews import setup_call

    async def scenario():
        _, service, calls, sid, live = setup_call(tmp_path)
        state = service.get(sid)
        state["call"]["active_question_id"] = "q1"
        state["questions"][0].update(text=QUESTION, decision="lookup", status="unavailable")
        service.store.save(state)
        calls.reasoner.plan = AsyncMock(
            return_value=[{**plan(), "evidence_status": "sufficient", "evidence_quotes": [HINT]}]
        )
        calls.queue_response = Mock()
        await calls.intervene(sid, "call1", {"action": "correct_now", "text": HINT})
        records = service.guidance.records(service.get(sid))
        assert records[0]["question_id"] == "q1"
        assert records[0]["binding"]["extracted_question"] == QUESTION
        assert service.get(sid)["questions"][0]["retrieval_mode"] == "provided"
        await asyncio.gather(*list(service.tasks))

    asyncio.run(scenario())


@pytest.mark.parametrize("quote_matches", [True, False])
def test_claimed_wiki_evidence_must_quote_supplied_material(tmp_path, quote_matches):
    from test_live_tools import setup

    async def scenario():
        _, calls, _ = setup(tmp_path)
        text = "What improvement did Nimbus achieve?"
        quote_text = "Nimbus achieved 30%." if quote_matches else "Nimbus achieved 50%."
        calls.reasoner.ask = AsyncMock(
            return_value={
                "questions": [
                    {
                        "text": text,
                        "action": "answer",
                        "data_scope": "internal",
                        "evidence_status": "sufficient",
                        "evidence_quotes": [quote_text],
                        "uses_wiki_facts": True,
                    }
                ]
            }
        )
        plans = await CallReasoner.plan(calls.reasoner, text, "Nimbus achieved 30%.", "", [])
        assert plans[0]["evidence_status"] == ("sufficient" if quote_matches else "missing")
        assert CallPolicy().decide(plans[0], "")[0] == (
            "answer" if quote_matches else "await_human"
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("matching", [True, False])
def test_hinter_fact_replaces_failed_search_or_explains_metric_gap(tmp_path, matching):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        fresh = {
            **plan(),
            "evidence_status": "sufficient" if matching else "irrelevant",
            "evidence_quotes": [HINT] if matching else [],
            "evidence_gap": "" if matching else "DAU/MAU are users, not annual revenue.",
        }
        calls.reasoner.plan.side_effect = [[plan()], [fresh]]
        calls.search.lookup = AsyncMock(side_effect=ValueError("provider failed"))

        async def stream(*args, context=None, **kwargs):
            assert context["retrieval_mode"] == "provided"
            assert (
                context["human_assistance"]["hinter_inputs"][0]["input_kind"] == "authorized_fact"
            )
            yield (
                "The verified 2025 annual revenue is 12 billion dollars."
                if matching
                else "Those are user counts. The annual revenue figure is still missing."
            )

        calls.reasoner.answer_stream = stream
        await service.submit(sid, QUESTION)
        await drain(service)
        qid = service.get(sid)["questions"][0]["id"]
        await service.guidance.submit(
            sid,
            {
                "text": HINT if matching else "DAU是1.2亿，MAU是23亿",
                "kind": "authorized_fact",
                "question_id": qid,
            },
        )
        await drain(service)
        state = service.get(sid)
        q = state["questions"][0]
        assert q["status"] == ("answered" if matching else "unavailable")
        if matching:
            assert q["retrieval_mode"] == "provided" and calls.search.lookup.await_count == 1
        else:
            # Unrelated user counts cannot replace annual revenue evidence.
            assert q["retrieval_mode"] == "public" and calls.search.lookup.await_count == 2
            assert state["answers"][-1]["text"] == "I don't know."
        assert "web search did not return" not in state["answers"][-1]["text"]

    asyncio.run(scenario())


def test_correction_cancels_inflight_lookup_before_using_new_facts(tmp_path):
    from test_live_tools import setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        started, cancelled = asyncio.Event(), asyncio.Event()
        calls.reasoner.plan.side_effect = [[plan()], [{**plan(), "evidence_status": "sufficient"}]]

        async def lookup(query):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def stream(*args, **kwargs):
            yield "The supplied annual revenue is 12 billion dollars."

        calls.search.lookup = AsyncMock(side_effect=lookup)
        calls.reasoner.answer_stream = stream
        await service.submit(sid, QUESTION)
        await asyncio.wait_for(started.wait(), 3)
        qid = service.get(sid)["questions"][0]["id"]
        await service.guidance.submit(
            sid, {"text": HINT, "question_id": qid, "kind": "authorized_fact"}
        )
        # Cancelled obsolete jobs are expected and should not cancel the correction.
        async with asyncio.timeout(5):
            while service.tasks:
                await asyncio.gather(*list(service.tasks), return_exceptions=True)
        state = service.get(sid)
        assert cancelled.is_set() and state["questions"][0]["status"] == "answered"
        assert "12 billion" in state["answers"][-1]["text"]
        assert all("web search did not return" not in a["text"] for a in state["answers"])

    asyncio.run(scenario())


def test_try_again_reuses_the_original_question_and_query(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.plan.return_value = [plan()]
        calls.search.lookup.side_effect = [
            ValueError("failed"),
            ("Shanghai weather has been retrieved", ["https://source.example/report"]),
        ]
        await service.submit(sid, QUESTION)
        await drain(service)
        qid = service.get(sid)["questions"][0]["id"]
        await service.submit(sid, "Try again.")
        await drain(service)
        state = service.get(sid)
        assert len(state["questions"]) == 1 and state["questions"][0]["id"] == qid
        assert state["questions"][0]["status"] == "answered"
        assert calls.reasoner.plan.await_count == 1
        assert [c.args[0] for c in calls.search.lookup.call_args_list] == [QUESTION, QUESTION]

    asyncio.run(scenario())


def test_bing_merges_news_and_ranks_before_truncating(monkeypatch):
    from app.agent import web_tools

    original_client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request.url.path)
        assert "mkt" not in request.url.params
        assert request.url.params["format"] == "RSS"
        news = request.url.path.startswith("/news")
        url = (
            "https://www.bing.com/news/apiclick.aspx?url="
            + quote("https://reports.example/tiktok-revenue", safe="")
            if news
            else "https://www.tiktok.com/login"
        )
        title = "TikTok annual revenue report" if news else "TikTok login"
        return httpx.Response(
            200,
            text=(
                "<rss><channel><item><title>"
                + title
                + "</title><link>"
                + escape(url)
                + "</link><description>"
                + title
                + "</description></item></channel></rss>"
            ),
        )

    monkeypatch.setattr(
        web_tools.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    result = asyncio.run(search_web(ToolConfig(), "TikTok annual revenue", max_results=1))
    assert set(requests) == {"/search", "/news/search"}
    assert result["results"][0]["url"] == "https://reports.example/tiktok-revenue"


def test_long_page_retains_metric_evidence_beyond_navigation():
    content = "navigation menu " * 3000 + "\nNimbus annual revenue in 2025 was 12 billion dollars."
    focused = focus_content(content, "Nimbus annual revenue", limit=3000)
    assert "12 billion" in focused


@pytest.mark.parametrize("age_hours", [0, 24])
def test_weather_requires_fresh_source_even_if_model_accepts_a_climate_page(monkeypatch, age_hours):
    from app.agent import call_search
    from app.agent.call_search import CallSearch, PublicSearchError

    source = "https://api.open-meteo.com/v1/forecast?latitude=1&longitude=2"
    monkeypatch.setattr(
        call_search,
        "search_web",
        AsyncMock(
            return_value={
                "results": [
                    {
                        "title": "City weather today",
                        "snippet": "City weather today",
                        "url": source,
                        "source_kind": "current_weather",
                    },
                    {"title": "City weather climate", "url": "https://example.com/climate"},
                ]
            }
        ),
    )

    async def fetch(config, url):
        return {
            "url": url,
            "content": json.dumps(
                {
                    "timezone": "UTC",
                    "current": {
                        "time": (datetime.now(UTC) - timedelta(hours=age_hours))
                        .replace(tzinfo=None)
                        .isoformat(),
                        "temperature_2m": 18.5,
                    },
                }
            )
            if url == source
            else "Annual climate is mild; average temperature is 18 C.",
        }

    monkeypatch.setattr(call_search, "fetch_web", fetch)
    assessor = AsyncMock(return_value={"answer_supported": True})

    async def scenario():
        search = CallSearch(assess=assessor)
        if age_hours:
            with pytest.raises(PublicSearchError):
                await search.lookup("City weather today")
        else:
            evidence, sources = await search.lookup("City weather today")
            assert sources == [source] and "18.5" in evidence
        assessor.assert_not_awaited()

    asyncio.run(scenario())


def test_partial_public_evidence_keeps_the_missing_metric_warning(monkeypatch):
    from app.agent import call_search
    from app.agent.call_search import CallSearch

    source = "https://reports.example/advertising"
    monkeypatch.setattr(
        call_search,
        "search_web",
        AsyncMock(
            return_value={
                "results": [
                    {"title": "Nimbus revenue statistics", "url": source},
                ]
            }
        ),
    )
    monkeypatch.setattr(
        call_search,
        "fetch_web",
        AsyncMock(
            return_value={
                "url": source,
                "content": "Nimbus advertising revenue was estimated at 12 billion in 2025.",
            }
        ),
    )
    assess = AsyncMock(
        return_value={
            "answer_supported": True,
            "supporting_urls": [source],
            "coverage": "partial",
            "limitations": (
                "Advertising estimate only; total revenue and tax payments are unverified."
            ),
        }
    )

    async def scenario():
        evidence, sources = await CallSearch(assess=assess).lookup("Nimbus revenue statistics")
        assert sources == [source] and "Coverage: partial" in evidence
        assert "total revenue and tax payments are unverified" in evidence
        assert "advertising revenue" in evidence

    asyncio.run(scenario())


@pytest.mark.parametrize("matching", [True, False])
def test_public_evidence_extraction_requires_an_actual_source_quote(tmp_path, matching):
    from test_live_tools import setup

    _, calls, _ = setup(tmp_path)
    url = "https://reports.example/nimbus"
    actual = "Nimbus advertising revenue was estimated at 12 billion dollars in 2025."
    quote = actual if matching else actual.replace("12 billion", "30 billion")
    calls.reasoner.ask = AsyncMock(
        return_value={
            "facts": [{"url": url, "quote": quote}],
            "limitations": "Advertising only; total revenue and tax payments are not established.",
        }
    )

    async def scenario():
        result = await CallReasoner.assess_public_evidence(
            calls.reasoner,
            "Nimbus annual revenue and tax payments",
            [{"url": url, "content": actual}],
        )
        assert result["answer_supported"] is matching
        assert result["supporting_urls"] == ([url] if matching else [])
        assert result["coverage"] == "partial"
        assert "total revenue" in result["limitations"]

    asyncio.run(scenario())


def test_fast_navigation_pages_do_not_cancel_a_supporting_report(monkeypatch):
    from app.agent import call_search
    from app.agent.call_search import CallSearch

    async def fetch(config, url):
        if url.endswith("report"):
            await asyncio.sleep(0.01)
            return {"url": url, "content": "The annual revenue was 12 billion dollars in 2025."}
        return {"url": url, "content": "Log in to TikTok. Navigation menu."}

    async def assess(query, pages):
        assert any("12 billion" in p["content"] for p in pages)
        return {"answer_supported": True, "supporting_urls": ["https://source.example/report"]}

    monkeypatch.setattr(
        call_search,
        "search_web",
        AsyncMock(
            return_value={
                "results": [
                    {"title": "TikTok revenue", "url": "https://source.example/" + path}
                    for path in ["login", "home", "report"]
                ]
            }
        ),
    )
    monkeypatch.setattr(call_search, "fetch_web", fetch)
    body, sources = asyncio.run(CallSearch(assess=assess).lookup("TikTok revenue"))
    assert sources == ["https://source.example/report"] and "12 billion" in body
