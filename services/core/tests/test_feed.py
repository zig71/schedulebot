"""The calendar feed: identity, cancellation, folding, and not leaking.

The failures these guard against are the ones a user experiences as their real
calendar filling with duplicates, or an event silently moving without notice.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from schedulebot_core import ics
from schedulebot_core.api import create_app
from schedulebot_core.categories import catalog
from schedulebot_core.domain import Block, Plan, Priority, Task
from schedulebot_core.ics import Event, IcsRow, compose, ics_uid, render
from schedulebot_core.store import ics as ics_store
from schedulebot_core.store import users as users_store
from schedulebot_core.store.db import Store

TZ = ZoneInfo("Europe/London")
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
PASSWORD = "correct horse battery"
SLOT = 96


def unfold(text: str) -> str:
    """RFC 5545 unfolding: drop the CRLF and the single leading space."""
    return text.replace("\r\n ", "")


def a_plan(*starts: int, origin: datetime | None = None) -> Plan:
    return Plan(
        origin=origin or datetime(2026, 11, 10, tzinfo=TZ),
        horizon_slots=7 * SLOT,
        blocks=[
            Block(task_id="write-report", occurrence=0, chunk=index,
                  start_slot=start, end_slot=start + 4, priority=Priority.P2)
            for index, start in enumerate(starts)
        ],
    )


def a_task(title: str = "Write the report") -> Task:
    return Task(id="write-report", title=title, minutes=60, category="deep-work")


# ------------------------------------------------------------------ identity


def test_the_uid_is_the_same_across_solves() -> None:
    first = ics_uid("write-report", 0, 0)
    second = ics_uid("write-report", 0, 0)
    assert first == second
    assert first == "write-report.0.0@schedulebot"


def test_different_chunks_get_different_uids() -> None:
    assert ics_uid("t", 0, 0) != ics_uid("t", 0, 1)
    assert ics_uid("t", 0, 0) != ics_uid("t", 1, 0)


def test_resolving_without_any_change_bumps_nothing() -> None:
    plan = a_plan(38)
    events, state = ics.plan_events(
        plan, tasks=[a_task()], categories=catalog(), previous={}
    )
    assert events[0].sequence == 0

    again, state2 = ics.plan_events(
        plan, tasks=[a_task()], categories=catalog(), previous=state
    )
    assert again[0].sequence == 0


def test_moving_a_block_bumps_its_sequence() -> None:
    plan = a_plan(38)
    _, state = ics.plan_events(plan, tasks=[a_task()], categories=catalog(), previous={})

    moved = a_plan(50)
    events, _ = ics.plan_events(
        moved, tasks=[a_task()], categories=catalog(), previous=state
    )
    assert events[0].sequence == 1


def test_a_removed_block_is_cancelled_and_re_sent_without_bumping() -> None:
    """A client that was offline still has to learn, so the cancellation repeats —
    but with the same SEQUENCE, or every poll looks like a fresh change."""
    plan = a_plan(38, 44)
    tasks = [a_task()]
    _, state = ics.plan_events(
        plan, tasks=tasks, categories=catalog(), previous={}, now=NOW
    )

    shrunk = a_plan(38)  # one block disappears
    events, state2 = ics.plan_events(
        shrunk, tasks=tasks, categories=catalog(), previous=state, now=NOW
    )
    cancelled = [event for event in events if event.cancelled]
    assert len(cancelled) == 1
    assert cancelled[0].sequence == 1
    assert state2[cancelled[0].uid].cancelled is True

    again, state3 = ics.plan_events(
        shrunk, tasks=tasks, categories=catalog(), previous=state2,
        now=NOW + timedelta(days=1),
    )
    resent = [event for event in again if event.cancelled]
    assert len(resent) == 1, "the cancellation should be served again, not dropped"
    assert resent[0].sequence == 1, "re-sending must not bump the sequence"

    late, _ = ics.plan_events(
        shrunk, tasks=tasks, categories=catalog(), previous=state3,
        now=NOW + timedelta(days=ics.CANCELLATION_GRACE_DAYS + 1),
    )
    assert not [event for event in late if event.cancelled], "grace window should expire"


def test_an_archived_task_leaves_the_calendar() -> None:
    plan = a_plan(38)
    _, state = ics.plan_events(plan, tasks=[a_task()], categories=catalog(), previous={})
    events, _ = ics.plan_events(plan, tasks=[], categories=catalog(), previous=state)
    assert [event.cancelled for event in events] == [True]


# ------------------------------------------------------------------- output


def test_the_feed_uses_crlf_and_utc_instants() -> None:
    text = compose(
        a_plan(38),
        user="yatt",
        tasks=[a_task()],
        categories=catalog(),
        previous={},
        now=NOW,
    )[0]
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20261110T093000Z" in text
    assert "\n\n" not in text


def test_text_is_escaped_so_a_comma_does_not_break_the_line() -> None:
    text = compose(
        a_plan(38),
        user="yatt",
        tasks=[a_task("Write, review; ship\\done")],
        categories=catalog(),
        previous={},
        now=NOW,
    )[0]
    assert "SUMMARY:Write\\, review\\; ship\\\\done" in text


def test_long_lines_are_folded_at_75_octets() -> None:
    long_title = "Write the quarterly report for the investors including all the numbers"
    text = compose(
        a_plan(38), user="yatt", tasks=[a_task(long_title)],
        categories=catalog(), previous={}, now=NOW,
    )[0]
    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, line
    assert "\r\n " in text, "expected at least one folded continuation"


def test_folding_does_not_split_a_multibyte_character() -> None:
    text = compose(
        a_plan(38), user="yatt", tasks=[a_task("Résumé — review " + "é" * 60)],
        categories=catalog(), previous={}, now=NOW,
    )[0]
    assert "é" in text
    text.encode("utf-8").decode("utf-8")  # would raise if a character were cut


def test_the_description_says_why_and_which_blocks_apply() -> None:
    text = compose(
        a_plan(38), user="yatt", tasks=[a_task()],
        categories=catalog(), previous={}, now=NOW,
    )[0]
    flat = unfold(text)
    assert "DESCRIPTION:" in flat
    # The comma is escaped because a bare comma would split the property value.
    assert "Blocks: Deep work\\, No social." in flat


def test_an_empty_feed_is_still_a_valid_calendar() -> None:
    text, state = compose(
        None, user="yatt", tasks=[], categories=catalog(), previous={}, now=NOW
    )
    assert text.startswith("BEGIN:VCALENDAR")
    assert "BEGIN:VEVENT" not in text
    assert state == {}


def test_rendering_is_stable_for_the_same_input() -> None:
    events = [
        Event(uid="b", start=NOW, end=NOW, summary="B", description="", categories="",
              sequence=0),
        Event(uid="a", start=NOW, end=NOW, summary="A", description="", categories="",
              sequence=0),
    ]
    assert render(events, user="yatt", now=NOW) == render(
        reversed(events), user="yatt", now=NOW
    )


# --------------------------------------------------------------------- feed


@pytest.fixture()
def store(tmp_path):
    store = Store(tmp_path / "feed.db")
    user = users_store.create_user(store, "yatt", PASSWORD, timezone="Europe/London")
    yield store, user
    store.close()


def _client(store: Store) -> TestClient:
    return TestClient(create_app(store))


def _signed_in(store: Store) -> TestClient:
    client = _client(store)
    assert client.post(
        "/api/login", json={"username": "yatt", "password": PASSWORD}
    ).status_code == 204
    return client


def test_a_feed_token_can_be_minted_and_read_without_a_cookie(store) -> None:
    store, user = store
    client = _signed_in(store)
    url = client.post("/api/ics/token").json()["url"]

    anonymous = _client(store)  # no session at all
    response = anonymous.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert response.headers["cache-control"] == "no-store"
    assert "BEGIN:VCALENDAR" in response.text


def test_the_token_is_stored_hashed(store) -> None:
    store, user = store
    url = _signed_in(store).post("/api/ics/token").json()["url"]
    token = url.removeprefix("/ics/").removesuffix(".ics")
    rows = store.query("SELECT token_hash FROM ics_tokens")
    assert token not in [str(row["token_hash"]) for row in rows]


def test_minting_a_new_token_retires_the_old_one(store) -> None:
    store, user = store
    client = _signed_in(store)
    first = client.post("/api/ics/token").json()["url"]
    second = client.post("/api/ics/token").json()["url"]
    anonymous = _client(store)
    assert anonymous.get(first).status_code == 404
    assert anonymous.get(second).status_code == 200


def test_revoking_stops_the_feed(store) -> None:
    store, user = store
    client = _signed_in(store)
    url = client.post("/api/ics/token").json()["url"]
    assert client.delete("/api/ics/token").status_code == 204
    assert _client(store).get(url).status_code == 404


def test_an_unknown_token_is_a_404(store) -> None:
    store, user = store
    assert _client(store).get("/ics/not-a-real-token.ics").status_code == 404


def test_a_feed_reflects_the_plan_and_keeps_its_uids(store) -> None:
    store, user = store
    client = _signed_in(store)
    client.put("/api/tasks/write-report",
               json={"title": "Write the report", "minutes": 120, "category": "deep-work"})
    client.post("/api/plan/solve")
    url = client.post("/api/ics/token").json()["url"]

    anonymous = _client(store)
    first = anonymous.get(url).text
    second = anonymous.get(url).text
    assert "BEGIN:VEVENT" in first
    uids = [line for line in first.split("\r\n") if line.startswith("UID:")]
    assert uids
    assert uids == [line for line in second.split("\r\n") if line.startswith("UID:")]
    # Nothing moved, so nothing was bumped.
    assert "SEQUENCE:1" not in second


def test_one_users_feed_never_contains_another_users_work(store) -> None:
    store, user = store
    users_store.create_user(store, "someone-else", PASSWORD, timezone="Europe/London")

    mine = _signed_in(store)
    mine.put("/api/tasks/mine", json={"title": "My secret task", "minutes": 60})
    mine.post("/api/plan/solve")
    my_url = mine.post("/api/ics/token").json()["url"]

    theirs = _client(store)
    theirs.post("/api/login", json={"username": "someone-else", "password": PASSWORD})
    theirs.put("/api/tasks/theirs", json={"title": "Their task", "minutes": 60})
    theirs.post("/api/plan/solve")
    their_url = theirs.post("/api/ics/token").json()["url"]

    anonymous = _client(store)
    assert "My secret task" in anonymous.get(my_url).text
    assert "Their task" not in anonymous.get(my_url).text
    assert "My secret task" not in anonymous.get(their_url).text


def test_a_feed_before_any_plan_is_empty_but_valid(store) -> None:
    store, user = store
    url = _signed_in(store).post("/api/ics/token").json()["url"]
    response = _client(store).get(url)
    assert response.status_code == 200
    assert response.text.startswith("BEGIN:VCALENDAR")
    assert "BEGIN:VEVENT" not in response.text


def test_the_state_survives_a_restart(store, tmp_path) -> None:
    store, user = store
    client = _signed_in(store)
    client.put("/api/tasks/write-report",
               json={"title": "Write the report", "minutes": 60, "category": "deep-work"})
    client.post("/api/plan/solve")
    url = client.post("/api/ics/token").json()["url"]
    _client(store).get(url)

    reopened = Store(store.path)
    try:
        state = ics_store.load_state(reopened, user.id)
        assert state, "event state should be persisted, not held in memory"
        assert all(isinstance(row, IcsRow) for row in state.values())
    finally:
        reopened.close()
