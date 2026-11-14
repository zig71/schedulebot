"""The transport, exercised with no network.

The interesting behaviour is what happens when the endpoint misbehaves: a schema
rejection is retried once, and anything else is surfaced with its body rather than
swallowed.
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from schedulebot_core.categories import catalog
from schedulebot_core.llm.groq import GroqClient, GroqConfig, LlmError

NOW = datetime(2026, 11, 14, 17, 0, tzinfo=ZoneInfo("Europe/London"))
CATS = catalog()

GOOD = {
    "tasks": [
        {
            "title": "Write the report",
            "category": "deep-work",
            "minutes": 90,
            "reasoning": "Read as one focused sitting.",
        }
    ]
}


def completion(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(payload)}}]},
    )


def client_for(handler) -> GroqClient:
    transport = httpx.MockTransport(handler)
    return GroqClient(
        GroqConfig(api_key="test-key", base_url="https://example.invalid"),
        client=httpx.Client(transport=transport, base_url="https://example.invalid"),
    )


def test_a_good_response_is_parsed_into_tasks() -> None:
    client = client_for(lambda request: completion(GOOD))
    result = client.capture("write the report, 90m", now=NOW, categories=CATS)
    assert [task.id for task in result.tasks] == ["write-the-report"]
    assert result.tasks[0].minutes == 90


def test_the_request_asks_for_a_strict_schema_and_sends_the_key() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return completion(GOOD)

    client_for(handler).capture("anything", now=NOW, categories=CATS)
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["response_format"]["json_schema"]["strict"] is True
    assert seen["body"]["temperature"] == 0


def test_a_schema_rejection_is_retried_once() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "json_validate_failed",
                        "message": "Failed to generate JSON.",
                        "failed_generation": "```markdown",
                    }
                },
            )
        return completion(GOOD)

    result = client_for(handler).capture("write the report", now=NOW, categories=CATS)
    assert calls["n"] == 2
    assert len(result.tasks) == 1


def test_a_second_schema_rejection_is_surfaced() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"code": "json_validate_failed"}})

    with pytest.raises(LlmError, match="400"):
        client_for(handler).capture("write the report", now=NOW, categories=CATS)
    assert calls["n"] == 2, "one retry, not an unbounded loop"


def test_an_auth_failure_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(LlmError, match="401"):
        client_for(handler).capture("anything", now=NOW, categories=CATS)
    assert calls["n"] == 1, "a bad key will never fix itself by trying again"


def test_invalid_json_inside_a_200_is_reported_with_the_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    with pytest.raises(LlmError, match="invalid JSON"):
        client_for(handler).capture("anything", now=NOW, categories=CATS)


def test_an_empty_message_is_an_error_not_an_empty_plan() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

    with pytest.raises(LlmError, match="empty message"):
        client_for(handler).capture("anything", now=NOW, categories=CATS)
