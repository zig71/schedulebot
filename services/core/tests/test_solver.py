"""The properties the engine must never lose.

Each test states the behaviour a user would notice, not the mechanism.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from schedulebot_core import (
    Block,
    Energy,
    Kind,
    Occupied,
    Priority,
    Routine,
    Task,
    Window,
    explain,
    solve,
    topological_order,
)
from schedulebot_core.slots import SLOTS_PER_DAY, slots_for

TZ = ZoneInfo("Europe/London")
ORIGIN = datetime(2026, 11, 9, tzinfo=TZ)  # a Monday
HORIZON = 21 * SLOTS_PER_DAY


def at(day: int, hour: int, minute: int = 0) -> int:
    return day * SLOTS_PER_DAY + hour * 4 + minute // 15


def workday(start: int = 9, end: int = 17) -> tuple[Window, ...]:
    return (Window(start * 60, end * 60),)


def task(**kw) -> Task:
    base = dict(
        id="t1",
        title="Task",
        minutes=120,
        priority=Priority.P3,
        min_chunk=60,
        max_chunk=120,
        windows=workday(),
    )
    base.update(kw)
    return Task(**base)


def run(tasks, **kw):
    return solve(tasks, kw.pop("routines", ()), kw.pop("busy", ()), origin=ORIGIN,
                 horizon_slots=kw.pop("horizon_slots", HORIZON), **kw)


# ---------------------------------------------------------------- routines


def test_a_routine_occupies_every_matching_weekday() -> None:
    routine = Routine(
        id="class", name="Lecture", weekdays=frozenset({0}), start_min=9 * 60,
        end_min=10 * 60, starts_on=ORIGIN.date(),
    )
    plan = run([], routines=[routine])
    mondays = {b.start_slot for b in plan.blocks}
    assert mondays == set(), "a routine is occupied time, not placed work"

    # The routine must block placement: a 2h task cannot land on Monday 09:00.
    plan = run([task(minutes=60, min_chunk=60, max_chunk=60)], routines=[routine])
    assert all(b.start_slot != at(0, 9) for b in plan.blocks)


def test_a_routine_exdate_is_skipped() -> None:
    monday = ORIGIN.date()
    routine = Routine(
        id="class", name="Lecture", weekdays=frozenset({0}), start_min=9 * 60,
        end_min=17 * 60, starts_on=monday, exdates=frozenset({monday}),
    )
    plan = run([task(minutes=120, min_chunk=60, max_chunk=120)], routines=[routine])
    assert plan.blocks[0].start_slot == at(0, 9), "the excluded Monday should be usable"


def test_nothing_is_placed_before_now() -> None:
    plan = run([task(minutes=120)], now_slot=at(0, 15))
    assert plan.blocks
    assert all(b.start_slot >= at(0, 15) for b in plan.blocks)


def test_a_weekly_habit_keeps_every_period_whatever_the_time_of_day() -> None:
    habit = task(
        id="habit", kind=Kind.HABIT, minutes=600, period_days=7,
        min_chunk=90, max_chunk=180, windows=workday(9, 18),
    )
    counts = {
        len(run([habit], now_slot=slot).blocks) for slot in (0, 1, 68, 300)
    }
    assert len(counts) == 1, f"block count changed with the solve time: {counts}"
    assert run([habit], now_slot=at(0, 12)).blocks, "the habit must still be placed"


# ------------------------------------------------------------------- tasks


def test_a_non_splittable_task_is_one_block() -> None:
    plan = run([task(minutes=180, min_chunk=60, max_chunk=60, splittable=False)])
    assert len(plan.blocks) == 1
    assert plan.blocks[0].slots == 12


def test_a_non_splittable_task_that_cannot_fit_is_reported() -> None:
    plan = run([task(minutes=180, min_chunk=60, max_chunk=60, splittable=False,
                     windows=workday(9, 10))])
    assert plan.blocks == []
    assert plan.shortfalls and plan.shortfalls[0].required_slots == 12


def test_remaining_minutes_are_what_gets_placed() -> None:
    plan = run([task(minutes=240, remaining=60, min_chunk=60, max_chunk=60)])
    assert sum(b.slots for b in plan.blocks) == slots_for(60)


def test_a_completed_task_demands_nothing() -> None:
    assert run([task(minutes=120, remaining=0)]).blocks == []


def test_max_per_day_is_respected() -> None:
    plan = run([task(minutes=240, min_chunk=60, max_chunk=60, max_per_day=1)])
    days = [b.start_slot // SLOTS_PER_DAY for b in plan.blocks]
    assert len(days) == len(set(days)), f"more than one block in a day: {days}"


# --------------------------------------------------------------- deadlines


def test_work_that_cannot_meet_its_deadline_is_reported() -> None:
    plan = run([task(minutes=600, min_chunk=60, max_chunk=180,
                     due=ORIGIN + timedelta(days=1, hours=9))])
    assert plan.blocks
    assert plan.late
    assert plan.complete is False


def test_work_that_meets_its_deadline_is_not_reported() -> None:
    plan = run([task(minutes=120, due=ORIGIN + timedelta(days=4, hours=17))])
    assert plan.late == []
    assert plan.complete is True


# ------------------------------------------------------------ dependencies


def test_a_dependent_task_starts_after_its_dependency() -> None:
    first = task(id="draft", title="Draft", minutes=120, min_chunk=120, max_chunk=120,
                 priority=Priority.P2)
    second = task(id="review", title="Review", minutes=60, min_chunk=60, max_chunk=60,
                  priority=Priority.P1, depends_on=("draft",))
    plan = run([first, second])
    draft_end = max(b.end_slot for b in plan.blocks if b.task_id == "draft")
    review_start = min(b.start_slot for b in plan.blocks if b.task_id == "review")
    assert review_start >= draft_end, "the review was scheduled before the draft existed"


def test_a_dependency_cycle_is_refused() -> None:
    a = task(id="a", depends_on=("b",))
    b = task(id="b", depends_on=("a",))
    with pytest.raises(ValueError, match="cycle"):
        topological_order([a, b])


# ------------------------------------------------------------------ energy


def test_energy_chooses_between_windows() -> None:
    split = (Window(9 * 60, 12 * 60), Window(18 * 60, 21 * 60))
    morning = run([task(minutes=60, min_chunk=60, max_chunk=60, windows=split,
                        energy=Energy.HIGH)])
    evening = run([task(minutes=60, min_chunk=60, max_chunk=60, windows=split,
                        energy=Energy.LOW)])
    assert morning.blocks[0].start_slot == at(0, 9)
    assert evening.blocks[0].start_slot == at(0, 18)


# --------------------------------------------------------------- stability


def test_only_the_blocks_a_change_hit_are_moved() -> None:
    spread = [
        task(id=f"t{i}", minutes=60, min_chunk=60, max_chunk=60,
             not_before=ORIGIN + timedelta(days=i))
        for i in range(10)
    ]
    before = run(spread)
    assert len(before.blocks) == 10

    # A meeting lands squarely on day 5's only block.
    day5 = [b for b in before.blocks if b.start_slot // SLOTS_PER_DAY == 5][0]
    busy = [Occupied(day5.start_slot, day5.end_slot, "Meeting")]
    after = run(spread, busy=busy, previous=before)

    assert len(after.moved) < len(after.blocks), (
        f"{len(after.moved)} of {len(after.blocks)} blocks moved for one meeting"
    )
    assert all(b.moved_from is None or b.moved_from == b.start_slot
               for b in after.blocks if b.task_id != day5.task_id)


def test_an_unchanged_replan_moves_nothing() -> None:
    tasks = [task(id=f"t{i}", minutes=60, min_chunk=60, max_chunk=60,
                  not_before=ORIGIN + timedelta(days=i)) for i in range(5)]
    first = run(tasks)
    again = run(tasks, previous=first)
    assert again.moved == []
    assert again.disturbance_slots == 0


# ------------------------------------------------------------- explanation


def test_an_explanation_cites_the_window_it_used() -> None:
    plan = run([task(minutes=60, min_chunk=60, max_chunk=60)])
    text = explain(plan.blocks[0], task(), ORIGIN)
    assert "window" in text
    assert "09:00" in text


def test_an_explanation_says_when_a_block_was_simply_kept() -> None:
    t = task(id="keep", minutes=60, min_chunk=60, max_chunk=60)
    first = run([t])
    again = run([t], previous=first)
    text = explain(again.blocks[0], t, ORIGIN)
    assert "kept where it already was" in text


def test_no_reason_is_admitted_rather_than_invented() -> None:
    block = Block(task_id="t1", occurrence=0, chunk=0, start_slot=at(0, 9),
                  end_slot=at(0, 10), priority=Priority.P3, reason=None)
    assert "no reason recorded" in explain(block, task(), ORIGIN)


def test_priority_decides_who_gets_the_first_slot() -> None:
    """Ordering must come from priority, not from input order or dependency order."""
    relaxed = task(id="relaxed", minutes=120, min_chunk=120, max_chunk=120,
                   priority=Priority.P4)
    urgent = task(id="urgent", minutes=120, min_chunk=120, max_chunk=120,
                  priority=Priority.P1)
    plan = run([relaxed, urgent])  # relaxed is first in the input on purpose
    assert plan.blocks[0].task_id == "urgent"


def test_an_urgent_dependent_still_waits_for_its_dependency() -> None:
    """Priority loses to an explicit dependency: the user said so."""
    draft = task(id="draft", minutes=240, min_chunk=120, max_chunk=120,
                 priority=Priority.P4)
    review = task(id="review", minutes=60, min_chunk=60, max_chunk=60,
                  priority=Priority.P1, depends_on=("draft",))
    plan = run([draft, review])
    draft_end = max(b.end_slot for b in plan.blocks if b.task_id == "draft")
    review_start = min(b.start_slot for b in plan.blocks if b.task_id == "review")
    assert review_start >= draft_end


def test_a_task_bigger_than_its_window_is_split_across_days() -> None:
    """Two hours of work in a one-hour daily window is two sessions, not nothing.

    This is the shape of a real instruction: "don't schedule demanding work after
    8pm" narrows the window, and a task sized only from `max_chunk` then vanishes
    while the plan still reports itself complete.
    """
    plan = run([task(minutes=120, min_chunk=60, max_chunk=120,
                     windows=(Window(20 * 60, 21 * 60),))])
    assert len(plan.blocks) == 2, plan.blocks
    assert all(block.slots == 4 for block in plan.blocks)
    assert len({block.start_slot // SLOTS_PER_DAY for block in plan.blocks}) == 2
    assert plan.shortfalls == []


def test_a_fragmented_window_takes_the_largest_session_that_fits() -> None:
    """The window is 09:00-11:00 and a meeting eats the first 90 minutes, so the
    only room today is a 30-minute gap. Take it rather than skipping the day."""
    busy = [Occupied(at(0, 9), at(0, 10, 30), "Meeting")]
    plan = run([task(minutes=60, min_chunk=30, max_chunk=60,
                     windows=workday(9, 11))], busy=busy)
    ordered = sorted(plan.blocks, key=lambda block: block.start_slot)
    assert ordered[0].start_slot == at(0, 10, 30), "should use the 30-minute gap"
    assert ordered[0].slots == 2
    assert ordered[1].start_slot == at(1, 9), "the rest goes tomorrow"


def test_an_uninterruptible_task_is_still_never_diced() -> None:
    plan = run([task(minutes=180, min_chunk=60, max_chunk=60, splittable=False,
                     windows=(Window(9 * 60, 10 * 60),))])
    assert plan.blocks == []
    assert plan.shortfalls
