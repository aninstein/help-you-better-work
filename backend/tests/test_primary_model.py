"""Every LLM stage uses the same configured primary, without small-model fallback."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.call_reasoner import CallReasoner
from app.agent.transcript_refiner import TranscriptRefiner


def test_default_graph_routes_a_request_through_the_primary_model(monkeypatch):
    monkeypatch.delenv("CALL_REASONER_DECISION_GRAPH", raising=False)
    text = "Explain how a rainbow forms."
    primary = SimpleNamespace(base_url="https://example.invalid")
    client = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(
        content=json.dumps({"questions": [{
            "text": text, "category": "routine", "action": "answer",
            "data_scope": "none", "matched_rule_ids": [],
        }]}),
    )))
    client.bind = lambda **options: client
    reasoner = CallReasoner(SimpleNamespace(clients=[(primary, client)]))

    result = asyncio.run(reasoner.plan(text, "", "", []))

    assert len(result) == 1
    assert result[0]["text"] == text
    assert result[0]["action"] == "answer"
    client.ainvoke.assert_awaited_once()


def test_refinement_routing_and_answer_ignore_legacy_small_model_overrides(monkeypatch):
    primary = SimpleNamespace(model="deepseek-primary", model_label="DeepSeek main", base_url="https://api.deepseek.com/v1")
    small = SimpleNamespace(model="seed-flash", model_label="Seed flash", base_url="https://example.invalid")
    main_client, other_client = object(), object()
    router = SimpleNamespace(clients=[(primary, main_client), (small, other_client)])
    for key in ["CALL_REASONER_MODEL", "CALL_TRANSCRIPT_MODEL", "CALL_RESPONSE_MODEL"]:
        monkeypatch.setenv(key, "seed-flash")
    planner, editor = CallReasoner(router), TranscriptRefiner(router)
    assert planner.model is planner.response_model is editor.model is main_client
    assert planner.settings is planner.response_settings is editor.settings is primary


def test_application_pins_all_llm_consumers_to_one_primary_client(tmp_path):
    from test_live_tools import setup

    service, calls, _ = setup(tmp_path)
    assert len(service.agent.router.clients) == 1
    settings, client = service.agent.router.clients[0]
    assert calls.reasoner.settings is calls.reasoner.response_settings is calls.refiner.settings is settings
    assert calls.reasoner.model is calls.reasoner.response_model is calls.refiner.model is client
