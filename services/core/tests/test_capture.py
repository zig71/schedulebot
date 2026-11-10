"""Capture: the sentence-to-requests boundary, and the categories behind it."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from schedulebot_core import Category, Energy, Task, Window, catalog, resolve, solve
from schedulebot_core.categories import PRESET_CATEGORIES, blocking_for
from schedulebot_core.llm import CaptureError, build_system_prompt, parse_capture

TZ = ZoneInfo("Europe/London")
NOW = datetime(2026, 11, 10, 9, 30, tzinfo=TZ)
CATS = catalog()


def payload(**overrides):
    base = {
        "title": "Finish the parser",
        "category": "deep-work",
        "minutes": 120,
        "reasoning": "Read as one focused sitting of deep work.",
    }
    base.update(overrides)
    return {"tasks": [base]}


# --------------------------------------------------------------- categories


def test_presets_cover_the_common_week() -> None:
    ids = {category.id for category in PRESET_CATEGORIES}
    assert {"deep-work", "learning", "admin", "health", "chores", "social", "recovery"} <= ids


def test_a_category_fills_the_gaps_a_task_left() -> None:
    bare = Task(id="a", title="a", minutes=60, category="deep-work")
    filled = resolve(bare, CATS)
    assert filled.windows == (Window(9 * 60, 12 * 60),)
    assert filled.energy is Energy.HIGH


def test_an_explicit_preference_beats_the_category() -> None:
    explicit = Task(
        id="a", title="a", minutes=60, category="deep-work",
        windows=(Window(20 * 60, 22 * 60),), energy=Energy.LOW,
    )
    resolved = resolve(explicit, CATS)
    assert resolved.windows == (Window(20 * 60, 22 * 60),)
    assert resolved.energy is Energy.LOW


def test_no_category_leaves_a_task_untouched() -> None:
    bare = Task(id="a", title="a", minutes=60)
    assert resolve(bare, CATS) == bare


def test_a_user_category_can_override_a_preset() -> None:
    mine = Category(id="deep-work", name="Flow", colour="#123456", energy=Energy.LOW)
    merged = catalog((mine,))
    assert merged["deep-work"].name == "Flow"


def test_blocking_is_opt_in_and_named_by_the_user() -> None:
    mapped = Category(id="deep-work", name="Deep work", colour="#4C6FFF", blocking="Deep work")
    assert blocking_for(Task(id="a", title="a", minutes=60, category="deep-work"),
                        catalog((mapped,))) == "Deep work"
    assert blocking_for(Task(id="b", title="b", minutes=60, category="recovery"), CATS) is None


def test_an_invalid_category_is_refused_at_construction() -> None:
    with pytest.raises(ValueError):
        Category(id="not a slug!", name="X", colour="#fff")


# ------------------------------------------------------------------ capture


def test_a_simple_note_becomes_a_task() -> None:
    result = parse_capture(payload(), now=NOW, categories=CATS)
    assert len(result.tasks) == 1
    task = result.tasks[0]
    assert task.title == "Finish the parser"
    assert task.category == "deep-work"
    assert task.minutes == 120
    assert task.id == "finish-the-parser"
    assert result.warnings == ()


def test_the_models_reasoning_is_kept() -> None:
    result = parse_capture(payload(), now=NOW, categories=CATS)
    assert "deep work" in result.reasoning["finish-the-parser"]


def test_a_deadline_lands_at_the_end_of_the_day_not_its_start() -> None:
    result = parse_capture(payload(due_in_days=3), now=NOW, categories=CATS)
    due = result.tasks[0].due
    assert due is not None
    assert due.date().isoformat() == "2026-11-13"
    assert (due.hour, due.minute) == (23, 45)


def test_an_unknown_category_warns_but_keeps_the_task() -> None:
    result = parse_capture(payload(category="woodworking"), now=NOW, categories=CATS)
    assert result.tasks[0].category == "uncategorised"
    assert any("unknown category" in warning for warning in result.warnings)


def test_a_fixed_weekly_commitment_becomes_a_routine() -> None:
    result = parse_capture(
        payload(
            title="Lecture",
            recurrence={"weekdays": [1], "start_min": 540, "end_min": 630,
                        "period_days": None},
            category="learning",
        ),
        now=NOW,
        categories=CATS,
    )
    assert result.tasks == ()
    routine = result.routines[0]
    assert routine.weekdays == frozenset({1})
    assert (routine.start_min, routine.end_min) == (540, 630)


def test_a_quota_recurrence_stays_flexible() -> None:
    result = parse_capture(
        payload(
            title="Gym",
            minutes=180,
            recurrence={"period_days": 7, "weekdays": None, "start_min": None,
                        "end_min": None},
            category="health",
        ),
        now=NOW,
        categories=CATS,
    )
    task = result.tasks[0]
    assert task.period_days == 7
    assert task.kind.value == "habit"


def test_a_dependency_by_title_resolves_to_an_id() -> None:
    result = parse_capture(
        {
            "tasks": [
                {"title": "Draft the plan", "category": "deep-work", "minutes": 120,
                 "reasoning": "draft"},
                {"title": "Send the plan", "category": "admin", "minutes": 30,
                 "reasoning": "send", "depends_on": ["Draft the plan"]},
            ]
        },
        now=NOW,
        categories=CATS,
    )
    send = next(task for task in result.tasks if task.title == "Send the plan")
    assert send.depends_on == ("draft-the-plan",)


def test_a_dangling_dependency_is_ignored_with_a_warning() -> None:
    result = parse_capture(
        payload(depends_on=["Something never mentioned"]), now=NOW, categories=CATS
    )
    assert result.tasks[0].depends_on == ()
    assert any("unknown dependency" in warning for warning in result.warnings)


def test_duplicate_titles_get_distinct_ids() -> None:
    result = parse_capture(
        {
            "tasks": [
                {"title": "Email", "category": "admin", "minutes": 15, "reasoning": "a"},
                {"title": "Email", "category": "admin", "minutes": 15, "reasoning": "b"},
            ]
        },
        now=NOW,
        categories=CATS,
    )
    assert [task.id for task in result.tasks] == ["email", "email-2"]


def test_a_bad_task_does_not_take_the_batch_with_it() -> None:
    result = parse_capture(
        {
            "tasks": [
                {"title": "Good", "category": "admin", "minutes": 30, "reasoning": "ok"},
                {"title": "Bad", "category": "admin", "minutes": 0, "reasoning": "nope"},
            ]
        },
        now=NOW,
        categories=CATS,
    )
    assert [task.title for task in result.tasks] == ["Good"]
    assert any("Bad" in warning for warning in result.warnings)


def test_context_survives_the_round_trip() -> None:
    result = parse_capture(
        payload(
            context={
                "people": ["Sam", "Dev"],
                "place": "Office",
                "requires": ["laptop"],
                "tags": ["q4"],
            }
        ),
        now=NOW,
        categories=CATS,
    )
    context = result.tasks[0].context
    assert context.people == ("Sam", "Dev")
    assert context.is_multi_attendee is True
    assert context.place == "Office"


def test_a_payload_without_tasks_is_refused() -> None:
    with pytest.raises(CaptureError):
        parse_capture({"nope": []}, now=NOW, categories=CATS)


def test_idle_minutes_are_rounded_to_the_slot_grid() -> None:
    result = parse_capture(payload(minutes=47), now=NOW, categories=CATS)
    assert result.tasks[0].minutes == 45


# -------------------------------------------------------------- integration


def test_a_capture_with_only_a_category_still_schedules() -> None:
    """The whole point: 'finish the parser, 2h' should land in a sensible slot."""
    result = parse_capture(payload(), now=NOW, categories=CATS)
    plan = solve(
        result.tasks,
        origin=NOW.replace(hour=0, minute=0),
        horizon_slots=96,
        now_slot=38,  # 09:30
        categories=CATS,
    )
    assert plan.blocks
    block = plan.blocks[0]
    # 09:30 is inside the deep-work window (09:00-12:00).
    assert 36 <= block.start_slot <= 48


def test_the_prompt_lists_every_category_and_forbids_picking_times() -> None:
    prompt = build_system_prompt(CATS, now=NOW)
    for category in CATS.values():
        assert category.id in prompt
    assert "Never choose a clock time" in prompt
    assert "Tuesday 10 November 2026" in prompt
