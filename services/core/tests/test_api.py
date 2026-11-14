"""HTTP-level tests.

These assert the promises the product makes to a person: you cannot sign up, you
cannot see anyone else's tasks, and the answer to "what am I doing now" comes from
a stored plan rather than a fresh guess.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from schedulebot_core.api import COOKIE, create_app
from schedulebot_core.domain import Task
from schedulebot_core.llm.contract import CaptureResult
from schedulebot_core.store import tasks as tasks_store
from schedulebot_core.store import users as users_store
from schedulebot_core.store.db import Store

PASSWORD = "correct horse battery"


@pytest.fixture()
def store(tmp_path) -> Iterator[Store]:
    store = Store(tmp_path / "api.db")
    users_store.create_user(store, "yatt", PASSWORD, timezone="Europe/London")
    yield store
    store.close()


def make_client(store: Store, capture=None) -> TestClient:
    return TestClient(create_app(store, capture=capture))


def sign_in(client: TestClient, username: str = "yatt", password: str = PASSWORD) -> TestClient:
    response = client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 204, response.text
    return client


def fake_capture(note: str, *, now: datetime, categories: dict) -> CaptureResult:
    return CaptureResult(
        tasks=(
            Task(id="write-report", title="Write the report", minutes=120,
                 category="deep-work"),
        ),
        routines=(),
        reasoning={"write-report": "Read as focused work that needs a clear head."},
        warnings=("note was short, guessed the duration",),
    )


# --------------------------------------------------------------------- auth


def test_there_is_no_registration_endpoint(store: Store) -> None:
    client = make_client(store)
    for path in ("/api/register", "/api/signup", "/api/users"):
        assert client.post(path, json={"username": "x", "password": "y" * 20}).status_code in (
            404,
            405,
        ), path


def test_the_api_refuses_anonymous_reads(store: Store) -> None:
    client = make_client(store)
    for path in ("/api/me", "/api/tasks", "/api/plan", "/api/now"):
        assert client.get(path).status_code == 401, path


def test_login_sets_a_httponly_session_cookie(store: Store) -> None:
    client = make_client(store)
    response = client.post("/api/login", json={"username": "yatt", "password": PASSWORD})
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert COOKIE in cookie


def test_a_wrong_password_is_rejected(store: Store) -> None:
    client = make_client(store)
    response = client.post("/api/login", json={"username": "yatt", "password": "nope"})
    assert response.status_code == 401
    assert COOKIE not in response.cookies


def test_repeated_failures_are_throttled(store: Store) -> None:
    client = make_client(store)
    for _ in range(5):
        assert client.post(
            "/api/login", json={"username": "yatt", "password": "wrong"}
        ).status_code == 401
    blocked = client.post("/api/login", json={"username": "yatt", "password": PASSWORD})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_logging_out_invalidates_the_session(store: Store) -> None:
    client = sign_in(make_client(store))
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/logout").status_code == 204
    assert client.get("/api/me").status_code == 401


def test_the_first_login_seeds_the_category_presets(store: Store) -> None:
    client = sign_in(make_client(store))
    categories = client.get("/api/me").json()["categories"]
    assert {category["id"] for category in categories} >= {"deep-work", "recovery"}
    deep_work = next(category for category in categories if category["id"] == "deep-work")
    assert deep_work["blocking"] == ["Deep work", "No social"]


# -------------------------------------------------------------------- tasks


def test_a_task_can_be_created_and_listed(store: Store) -> None:
    client = sign_in(make_client(store))
    response = client.put(
        "/api/tasks/write-report",
        json={"title": "Write the report", "minutes": 120, "category": "deep-work"},
    )
    assert response.status_code == 200, response.text
    listed = client.get("/api/tasks").json()
    assert [task["id"] for task in listed] == ["write-report"]
    assert listed[0]["category"] == "deep-work"


def test_one_users_tasks_are_invisible_to_another(store: Store) -> None:
    users_store.create_user(store, "someone-else", PASSWORD, timezone="Europe/London")
    first = sign_in(make_client(store))
    first.put("/api/tasks/secret", json={"title": "Secret", "minutes": 60})

    second = sign_in(make_client(store), username="someone-else")
    assert second.get("/api/tasks").json() == []


def test_a_task_id_in_the_body_cannot_redirect_the_write(store: Store) -> None:
    users_store.create_user(store, "someone-else", PASSWORD, timezone="Europe/London")
    other = sign_in(make_client(store), username="someone-else")
    other.put("/api/tasks/theirs", json={"title": "Theirs", "minutes": 60})

    mine = sign_in(make_client(store))
    mine.put("/api/tasks/theirs", json={"title": "Mine now", "minutes": 30})
    assert [task["title"] for task in other.get("/api/tasks").json()] == ["Theirs"]


def test_a_nonsense_task_is_refused_at_the_edge(store: Store) -> None:
    client = sign_in(make_client(store))
    assert client.put("/api/tasks/t", json={"title": "", "minutes": 60}).status_code == 422
    assert client.put("/api/tasks/t", json={"title": "T", "minutes": 0}).status_code == 422


def test_progress_records_what_is_left(store: Store) -> None:
    client = sign_in(make_client(store))
    client.put("/api/tasks/t", json={"title": "T", "minutes": 120})
    response = client.post("/api/tasks/t/progress", json={"remaining": 45})
    assert response.status_code == 200
    assert response.json()["remaining"] == 45
    assert response.json()["minutes"] == 120


def test_deleting_an_unknown_task_is_a_404(store: Store) -> None:
    client = sign_in(make_client(store))
    assert client.delete("/api/tasks/ghost").status_code == 404


# ------------------------------------------------------------------ capture


def test_capture_stores_tasks_and_plans_them(store: Store) -> None:
    client = sign_in(make_client(store, capture=fake_capture))
    response = client.post("/api/capture", json={"note": "write the report, 2h"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["created"] == ["write-report"]
    assert body["reasoning"]["write-report"].startswith("Read as focused work")
    assert any("note was short" in warning for warning in body["warnings"])
    assert body["plan"]["blocks"], "capture should produce a scheduled block"

    block = body["plan"]["blocks"][0]
    assert block["title"] == "Write the report"
    assert block["blocking"] == ["Deep work", "No social"]
    assert block["explanation"]


def test_capture_without_a_model_configured_is_a_503(store: Store) -> None:
    client = sign_in(make_client(store))
    assert client.post("/api/capture", json={"note": "anything"}).status_code == 503


def test_a_model_failure_is_a_502_with_the_reason(store: Store) -> None:
    def broken(note: str, *, now: datetime, categories: dict) -> CaptureResult:
        raise RuntimeError("model endpoint returned 401: bad key")

    client = sign_in(make_client(store, capture=broken))
    response = client.post("/api/capture", json={"note": "anything"})
    assert response.status_code == 502
    assert "401" in response.json()["detail"]


def test_the_capture_note_is_bounded(store: Store) -> None:
    client = sign_in(make_client(store, capture=fake_capture))
    assert client.post("/api/capture", json={"note": ""}).status_code == 422
    assert client.post("/api/capture", json={"note": "x" * 5000}).status_code == 422


# --------------------------------------------------------------------- plan


def test_solving_again_keeps_blocks_where_they_were(store: Store) -> None:
    client = sign_in(make_client(store, capture=fake_capture))
    client.put("/api/tasks/write-report",
               json={"title": "Write the report", "minutes": 120, "category": "deep-work"})
    first = client.post("/api/plan/solve").json()
    second = client.post("/api/plan/solve").json()

    assert first["blocks"], "nothing was scheduled"
    assert [block["start"] for block in first["blocks"]] == [
        block["start"] for block in second["blocks"]
    ]


def test_an_unsolved_plan_is_a_404_rather_than_an_empty_lie(store: Store) -> None:
    client = sign_in(make_client(store))
    assert client.get("/api/plan").status_code == 404
    assert client.get("/api/now").status_code == 404


def test_now_returns_the_current_or_next_block(store: Store) -> None:
    client = sign_in(make_client(store, capture=fake_capture))
    client.put("/api/tasks/write-report",
               json={"title": "Write the report", "minutes": 120, "category": "deep-work"})
    client.post("/api/plan/solve")

    response = client.get("/api/now")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Write the report"
    assert body["state"] in {"now", "next"}
    assert body["explanation"]
    assert body["ends_in_minutes"] >= 0


def test_a_plan_carries_its_warnings(store: Store) -> None:
    client = sign_in(make_client(store))
    tasks_store.save_task(
        store,
        store.one("SELECT id FROM users WHERE username = 'yatt'")["id"],
        Task(id="impossible", title="Impossible", minutes=600, min_chunk=600,
             max_chunk=600, splittable=False,
             windows=(), due=None),
    )
    response = client.post("/api/plan/solve")
    assert response.status_code == 200
    assert "warnings" in response.json()


def test_the_built_frontend_is_served_without_shadowing_the_api(tmp_path) -> None:
    """One process serves the app and the API: "/" is the app, /api is still JSON."""
    store = Store(tmp_path / "web.db")
    users_store.create_user(store, "yatt", PASSWORD, timezone="Europe/London")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>schedulebot</title>ok")

    client = TestClient(create_app(store, web_dir=dist))
    root = client.get("/")
    assert root.status_code == 200
    assert "schedulebot" in root.text
    assert client.get("/api/me").status_code == 401
    store.close()


def test_without_a_build_there_is_no_frontend(tmp_path) -> None:
    store = Store(tmp_path / "nobuild.db")
    users_store.create_user(store, "yatt", PASSWORD, timezone="Europe/London")
    client = TestClient(create_app(store, web_dir=tmp_path / "missing"))
    assert client.get("/").status_code == 404
    assert client.get("/healthz").json() == {"status": "ok"}
    store.close()
