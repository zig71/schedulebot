"""Turning tasks and routines into concrete spans.

The one rule that matters: **the floor is not the anchor.** `now` clips the
spans a task may use; it never moves the boundary of a repeating period. Get
that wrong and a weekly habit loses a week every time it is re-solved, then
reports itself complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..domain import Occupied, Routine, Task, Window
from ..slots import (
    SLOTS_PER_DAY,
    ceil_slot,
    day_of,
    day_start,
    slots_for,
)

#: How far ahead a single requirement may be placed from its own start.
#: Bounds the search without pretending to know what next month looks like.
SCHEDULING_RANGE_DAYS = 14

#: A pathological intent (20h in 15-minute chunks) must not explode the search.
MAX_CHUNKS = 16


@dataclass(frozen=True, slots=True)
class Span:
    """A contiguous absolute span, entirely within one day, that may be used."""

    lo: int
    hi: int
    day: int
    window: Window | None


@dataclass(frozen=True, slots=True)
class Requirement:
    """One period's worth of demand from one task."""

    task: Task
    occurrence: int
    required_slots: int
    min_chunk: int
    max_chunk: int
    spans: tuple[Span, ...]
    due_slot: int | None
    after_task: str | None = None

    @property
    def key_prefix(self) -> tuple[str, int]:
        return (self.task.id, self.occurrence)

    @property
    def max_chunks(self) -> int:
        if not self.task.splittable:
            return 1
        return max(1, min(self.required_slots // self.min_chunk, MAX_CHUNKS))


def expand_routines(
    routines: list[Routine], origin: datetime, horizon_slots: int
) -> list[Occupied]:
    """Every occurrence of every immovable routine, as occupied spans."""
    first_day = origin.date()
    out: list[Occupied] = []
    for routine in routines:
        for day_index in range(horizon_slots // SLOTS_PER_DAY):
            day = first_day + timedelta(days=day_index)
            if day < routine.starts_on or (routine.ends_on and day > routine.ends_on):
                continue
            if day.weekday() not in routine.weekdays or day in routine.exdates:
                continue
            lo = day_start(day_index) + routine.start_min // 15
            hi = day_start(day_index) + routine.end_min // 15
            if lo >= horizon_slots:
                continue
            out.append(
                Occupied(
                    start_slot=lo,
                    end_slot=min(hi, horizon_slots),
                    label=routine.name,
                    source="routine",
                )
            )
    return out


def _spans(task: Task, lo: int, hi: int) -> tuple[Span, ...]:
    """Absolute usable spans between `lo` and `hi`, one per (day, window)."""
    if hi <= lo:
        return ()
    windows = task.windows or (Window(0, 24 * 60),)
    min_slots = slots_for(task.min_chunk)
    spans: list[Span] = []
    for day_index in range(day_of(lo), day_of(hi - 1) + 1):
        base = day_start(day_index)
        for window in windows:
            span_lo = max(base + window.start_min // 15, lo)
            span_hi = min(base + window.end_min // 15, hi)
            if span_hi - span_lo >= min_slots:
                spans.append(Span(lo=span_lo, hi=span_hi, day=day_index, window=window))
    return tuple(spans)


def expand_task(
    task: Task, origin: datetime, horizon_slots: int, now_slot: int = 0
) -> list[Requirement]:
    """Expand one task into its per-period requirements."""
    anchor = ceil_slot(task.not_before, origin) if task.not_before else 0
    floor = max(anchor, now_slot)
    due_slot = ceil_slot(task.due, origin) if task.due else None
    required = task.duration_slots

    if not task.outstanding:
        return []

    def build(occurrence: int, p_lo: int, p_hi: int, *, allow_empty: bool) -> Requirement | None:
        reach = min(p_hi, p_lo + SCHEDULING_RANGE_DAYS * SLOTS_PER_DAY)
        spans = _spans(task, p_lo, reach)
        if not spans and not allow_empty:
            return None
        return Requirement(
            task=task,
            occurrence=occurrence,
            required_slots=required,
            min_chunk=slots_for(task.min_chunk),
            max_chunk=slots_for(task.max_chunk),
            spans=spans,
            due_slot=due_slot,
            after_task=task.depends_on[0] if task.depends_on else None,
        )

    if task.period_days is None:
        # A one-shot whose window is already gone must still be reported, not
        # dropped — "nothing scheduled" and "no room left" are different facts.
        one = build(0, floor, horizon_slots, allow_empty=True)
        return [one] if one else []

    period = task.period_days * SLOTS_PER_DAY
    out: list[Requirement] = []
    cursor, occurrence = anchor, 0
    while cursor + period <= horizon_slots:
        if cursor + period <= floor:
            # A period entirely behind us is not owed; count it so block keys
            # stay stable, then move on.
            cursor += period
            occurrence += 1
            continue
        req = build(occurrence, max(cursor, floor), cursor + period, allow_empty=False)
        if req:
            out.append(req)
        cursor += period
        occurrence += 1
    return out


def expand_all(
    tasks: list[Task], origin: datetime, horizon_slots: int, now_slot: int = 0
) -> list[Requirement]:
    return [
        req
        for task in tasks
        if task.outstanding
        for req in expand_task(task, origin, horizon_slots, now_slot)
    ]
