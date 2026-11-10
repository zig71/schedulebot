"""Persistence tests: the parts a scheduling bug would hide behind."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from schedulebot_core.categories import PRESET_CATEGORIES
from schedulebot_core.domain import (
    Block,
    Context,
    Energy,
    Plan,
    Priority,
    Reason,
    Routine,
    Shortfall,
    Task,
    Window,
)
from schedulebot_core.store import plans as plans_store
from schedulebot_core.store import tasks as tasks_store
from schedulebot_core.store import users as users_store
from schedulebot_core.store.db import Store

TZ = ZoneInfo("Europe/London")


@pytest.fixture()
def store(tmp_path):
    store = Store(tmp_path / "test.db")
    yield store
    store.close()


@pytest.fixture()
def user(store):
    created = users_store.create_user(store, "yatt", "correct horse battery")
    tasks_store.seed_categories(store, created.id)
    return created


# -------------------------------------------------------------------- users


def test_a_user_can_sign_in_with_the_right_password(store, user) -> None:
    assert users_store.verify_credentials(store, "yatt", "correct horse battery") == user


def test_the_wrong_password_and_an_unknown_user_are_indistinguishable(store, user) -> None:
    assert users_store.verify_credentials(store, "yatt", "wrong password here") is None
    assert users_store.verify_credentials(store, "nobody", "correct horse battery") is None


def test_short_passwords_are_refused(store) -> None:
    with pytest.raises(ValueError, match="at least"):
        users_store.create_user(store, "someone", "short")


def test_usernames_are_unique_regardless_of_case(store, user) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        users_store.create_user(store, "YATT", "another good password")


def test_a_session_resolves_to_its_user_and_can_be_revoked(store, user) -> None:
    token = users_store.create_session(store, user.id)
    assert users_store.user_for_token(store, token) == user
    users_store.revoke_session(store, token)
    assert users_store.user_for_token(store, token) is None


def test_the_raw_session_token_is_not_stored(store, user) -> None:
    token = users_store.create_session(store, user.id)
    rows = store.query("SELECT token_hash FROM sessions")
    assert [str(row["token_hash"]) for row in rows] != [token]


def test_changing_a_password_drops_existing_sessions(store, user) -> None:
    token = users_store.create_session(store, user.id)
    users_store.set_password(store, user.id, "a different good password")
    assert users_store.user_for_token(store, token) is None
    assert users_store.verify_credentials(store, "yatt", "a different good password") == user


# --------------------------------------------------------------- categories


def test_seeding_copies_the_presets_per_user(store, user) -> None:
    loaded = tasks_store.load_categories(store, user.id)
    assert set(loaded) == {preset.id for preset in PRESET_CATEGORIES}


def test_a_categories_blocking_list_round_trips(store, user) -> None:
    loaded = tasks_store.load_categories(store, user.id)
    assert loaded["deep-work"].blocking == ("Deep work", "No social")


def test_a_category_edit_belongs_to_one_user(store) -> None:
    first = users_store.create_user(store, "first", "a long enough password")
    second = users_store.create_user(store, "second", "a long enough password")
    tasks_store.seed_categories(store, first.id)
    tasks_store.seed_categories(store, second.id)

    edited = tasks_store.load_categories(store, first.id)["deep-work"]
    tasks_store.save_category(
        store, first.id,
        type(edited)(id=edited.id, name="Flow", colour="#123456", blocking=("No video",)),
    )
    assert tasks_store.load_categories(store, first.id)["deep-work"].name == "Flow"
    assert tasks_store.load_categories(store, second.id)["deep-work"].name == "Deep work"


# -------------------------------------------------------------------- tasks


def test_a_task_round_trips_with_every_field_intact(store, user) -> None:
    task = Task(
        id="write-report",
        title="Write the report",
        minutes=180,
        priority=Priority.P1,
        energy=Energy.HIGH,
        min_chunk=45,
        max_chunk=90,
        splittable=True,
        windows=(Window(540, 720), Window(1080, 1200)),
        due=datetime(2026, 11, 13, 23, 45, tzinfo=TZ),
        depends_on=("draft",),
        max_per_day=2,
        category="deep-work",
        context=Context(people=("Sam",), place="Office", requires=("laptop",), tags=("q4",)),
    )
    tasks_store.save_task(store, user.id, task, reasoning="read as one long session")

    loaded = tasks_store.load_task(store, user.id, "write-report")
    assert loaded is not None
    assert loaded.windows == task.windows
    assert loaded.due == task.due
    assert loaded.depends_on == ("draft",)
    assert loaded.context.people == ("Sam",)
    assert loaded.priority is Priority.P1
    assert loaded.energy is Energy.HIGH
    assert tasks_store.task_reasoning(store, user.id)["write-report"] == "read as one long session"


def test_deleting_a_task_archives_it_rather_than_destroying_the_record(store, user) -> None:
    tasks_store.save_task(store, user.id, Task(id="t", title="T", minutes=60))
    assert tasks_store.delete_task(store, user.id, "t") is True
    assert tasks_store.load_tasks(store, user.id) == []
    assert store.one("SELECT archived FROM tasks WHERE id = 't'")["archived"] == 1


def test_progress_is_recorded_without_rewriting_the_estimate(store, user) -> None:
    tasks_store.save_task(store, user.id, Task(id="t", title="T", minutes=120))
    updated = tasks_store.record_progress(store, user.id, "t", remaining=45)
    assert updated is not None
    assert updated.remaining == 45
    assert updated.minutes == 120
    assert tasks_store.load_task(store, user.id, "t").remaining == 45  # type: ignore[union-attr]


def test_a_routine_round_trips(store, user) -> None:
    routine = Routine(
        id="lecture",
        name="Lecture",
        weekdays=frozenset({1, 3}),
        start_min=540,
        end_min=630,
        starts_on=date(2026, 11, 10),
        ends_on=date(2026, 12, 15),
        exdates=frozenset({date(2026, 11, 17)}),
    )
    tasks_store.save_routine(store, user.id, routine)
    loaded = tasks_store.load_routines(store, user.id)
    assert loaded == [routine]


# -------------------------------------------------------------------- plans


def test_a_plan_round_trips_including_its_reasons(store, user) -> None:
    origin = datetime(2026, 11, 10, tzinfo=TZ)
    plan = Plan(
        origin=origin,
        horizon_slots=96,
        blocks=[
            Block(
                task_id="t", occurrence=0, chunk=0, start_slot=38, end_slot=46,
                priority=Priority.P2,
                reason=Reason(window=(36, 48), after_dependency="draft",
                              rejected=((0, 4, "busy"),)),
            )
        ],
        shortfalls=[Shortfall(task_id="u", occurrence=0, required_slots=8, placed_slots=4)],
        solve_ms=12.5,
    )
    plans_store.save(store, user.id, plan, warnings=["something did not fit"])

    loaded, warnings, solved_at = plans_store.load(store, user.id)
    assert loaded is not None
    assert loaded.origin == origin
    assert loaded.blocks[0].start_slot == 38
    assert loaded.blocks[0].reason is not None
    assert loaded.blocks[0].reason.after_dependency == "draft"
    assert loaded.blocks[0].reason.rejected == ((0, 4, "busy"),)
    assert loaded.shortfalls[0].required_slots == 8
    assert warnings == ["something did not fit"]
    assert solved_at is not None


def test_the_plan_origin_travels_with_the_slots(store, user) -> None:
    """Reading an old plan must yield the same instants, not a shifted week."""
    origin = datetime(2026, 11, 10, tzinfo=TZ)
    plan = Plan(origin=origin, horizon_slots=96,
                blocks=[Block(task_id="t", occurrence=0, chunk=0, start_slot=38,
                              end_slot=46, priority=Priority.P3)])
    plans_store.save(store, user.id, plan)
    loaded, _, _ = plans_store.load(store, user.id)
    assert loaded is not None
    assert loaded.origin == origin
    assert loaded.blocks[0].start_slot == 38


# ----------------------------------------------------------- single writer


def test_concurrent_writes_do_not_collide(store, user) -> None:
    """The whole reason one process owns the file: no 'database is locked'."""
    errors: list[BaseException] = []

    def writer(index: int) -> None:
        try:
            for n in range(20):
                tasks_store.save_task(
                    store, user.id, Task(id=f"w{index}-{n}", title="T", minutes=30)
                )
        except BaseException as exc:  # noqa: BLE001 - the point is to catch anything
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(tasks_store.load_tasks(store, user.id)) == 120


def test_a_failed_transaction_leaves_nothing_behind(store, user) -> None:
    with pytest.raises(RuntimeError), store.write() as conn:
        conn.execute(
            "INSERT INTO tasks (user_id, id, title, minutes, min_chunk, max_chunk,"
            " created_at, updated_at) VALUES (?, 'x', 'X', 30, 30, 30, '', '')",
            (user.id,),
        )
        raise RuntimeError("boom")
    assert tasks_store.load_task(store, user.id, "x") is None


def test_sessions_expire(store, user) -> None:
    token = users_store.create_session(store, user.id, ttl=timedelta(seconds=-1))
    assert users_store.user_for_token(store, token) is None


def test_timestamps_are_stored_with_an_offset(store, user) -> None:
    tasks_store.save_task(store, user.id, Task(id="t", title="T", minutes=30))
    row = store.one("SELECT created_at FROM tasks WHERE id = 't'")
    stored = datetime.fromisoformat(str(row["created_at"]))
    assert stored.tzinfo is not None
    assert abs(datetime.now(UTC) - stored) < timedelta(minutes=5)
