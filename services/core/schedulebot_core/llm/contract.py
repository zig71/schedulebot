"""The model's job: turn a sentence into structured requests.

Two boundaries, both load-bearing:

1. **The model never chooses a time.** It may say "mornings", "two hours",
   "by Friday", "every Tuesday" — it may not say "09:15". Slot selection is the
   solver's, from real availability, and a model that guessed would produce a
   plan that is wrong in a way nobody can see.
2. **Its reasoning is interpretation, not justification.** "I read this as deep
   work needing one long session before Thursday" is useful. "I placed it at
   09:00 because you're sharpest then" is a lie — it never saw the calendar.
   Placement reasons come from `explain.py`, which saw the actual decision.

Everything here is pure: parsing and validation, no network. The transport that
calls Groq lives in the service, not the core.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from ..categories import UNCATEGORISED, Category
from ..domain import Context, Energy, Kind, Priority, Routine, Task, Window


class CaptureError(ValueError):
    """The payload is not the shape we asked for at all."""


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """What came back, what was ignored, and what the model thought it was doing."""

    tasks: tuple[Task, ...]
    routines: tuple[Routine, ...]
    reasoning: Mapping[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def all_ids(self) -> list[str]:
        return [task.id for task in self.tasks] + [routine.id for routine in self.routines]


TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "category", "minutes", "reasoning"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "category": {"type": "string"},
        "minutes": {"type": "integer", "minimum": 5, "maximum": 2880},
        "min_chunk_minutes": {"type": ["integer", "null"], "minimum": 5, "maximum": 1440},
        "max_chunk_minutes": {"type": ["integer", "null"], "minimum": 5, "maximum": 1440},
        "splittable": {"type": ["boolean", "null"]},
        "priority": {"type": ["integer", "null"], "minimum": 1, "maximum": 4},
        "energy": {"type": ["string", "null"], "enum": ["high", "medium", "low", None]},
        "windows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["start_min", "end_min"],
                "properties": {
                    "start_min": {"type": "integer", "minimum": 0, "maximum": 1440},
                    "end_min": {"type": "integer", "minimum": 0, "maximum": 1440},
                },
            },
        },
        "due_in_days": {"type": ["integer", "null"], "minimum": 0, "maximum": 365},
        "depends_on": {"type": "array", "items": {"type": "string"}},
        "recurrence": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["period_days", "weekdays", "start_min", "end_min"],
            "properties": {
                "period_days": {"type": ["integer", "null"], "minimum": 1, "maximum": 365},
                "weekdays": {
                    "type": ["array", "null"],
                    "items": {"type": "integer", "minimum": 0, "maximum": 6},
                },
                "start_min": {"type": ["integer", "null"], "minimum": 0, "maximum": 1440},
                "end_min": {"type": ["integer", "null"], "minimum": 0, "maximum": 1440},
            },
        },
        "context": {
            "type": "object",
            "additionalProperties": False,
            "required": ["people", "place", "requires", "tags"],
            "properties": {
                "people": {"type": "array", "items": {"type": "string"}},
                "place": {"type": ["string", "null"]},
                "requires": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
        "reasoning": {"type": "string", "minLength": 1, "maxLength": 500},
    },
}

CAPTURE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tasks"],
    "properties": {"tasks": {"type": "array", "items": TASK_SCHEMA}},
}


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "task"


def _unique(slug: str, taken: set[str]) -> str:
    candidate, n = slug, 1
    while candidate in taken:
        n += 1
        candidate = f"{slug}-{n}"
    taken.add(candidate)
    return candidate


def _sequence(raw: object) -> Sequence[Any]:
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return raw
    return ()


def _windows(raw: object) -> tuple[Window, ...]:
    out: list[Window] = []
    for item in _sequence(raw):
        if not isinstance(item, Mapping):
            continue
        try:
            out.append(Window(int(item["start_min"]), int(item["end_min"])))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(out)


def _strings(raw: object) -> tuple[str, ...]:
    return tuple(str(value).strip() for value in _sequence(raw) if str(value).strip())


def _context(raw: object) -> Context:
    if not isinstance(raw, Mapping):
        return Context()
    place = raw.get("place")
    return Context(
        people=_strings(raw.get("people")),
        place=str(place) if place else None,
        requires=_strings(raw.get("requires")),
        tags=_strings(raw.get("tags")),
    )


def _as_int(value: object, *, name: str) -> int:
    """Accept what a JSON parser plausibly produced, refuse the rest.

    A boolean is not a duration, so `True` is rejected rather than silently
    becoming one minute.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, got a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise ValueError(f"{name} must be a number, got {value!r}")


def _int_or(raw: object, fallback: int, *, name: str) -> int:
    return fallback if raw is None else _as_int(raw, name=name)


def _due(raw: object, now: datetime) -> datetime | None:
    """`due_in_days` counts from the user's today and lands at the end of that day.

    A deadline is "by Thursday", not "00:00 Thursday" — a midnight deadline would
    make Thursday itself unusable.
    """
    if raw is None:
        return None
    days = _as_int(raw, name="due_in_days")
    return (now + timedelta(days=days)).replace(hour=23, minute=45, second=0, microsecond=0)


def _make_task(ident: str, title: str, raw: Mapping[str, Any], now: datetime) -> Task | Routine:
    """Build one domain object, or raise with a message fit to warn about."""
    minutes = _int_or(raw.get("minutes"), 0, name="minutes")
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    if minutes % 5:
        minutes = int(round(minutes / 5.0) * 5)

    min_chunk_raw = _int_or(
        raw.get("min_chunk_minutes"), min(30, minutes), name="min_chunk_minutes"
    )
    min_chunk = min(min_chunk_raw, minutes)
    max_chunk_raw = _int_or(
        raw.get("max_chunk_minutes"), min(120, minutes), name="max_chunk_minutes"
    )
    max_chunk = max(min_chunk, max_chunk_raw)

    energy_raw = raw.get("energy")
    energy = Energy(str(energy_raw)) if energy_raw else None
    priority = Priority(_int_or(raw.get("priority"), 3, name="priority"))

    recurrence = raw.get("recurrence")
    recurrence = recurrence if isinstance(recurrence, Mapping) else {}
    weekdays = recurrence.get("weekdays")
    days = [_as_int(day, name="weekday") % 7 for day in _sequence(weekdays)]
    start_min = recurrence.get("start_min")
    end_min = recurrence.get("end_min")
    period_days = recurrence.get("period_days")

    category = str(raw.get("category") or UNCATEGORISED)

    if days and start_min is not None and end_min is not None:
        # A fixed weekly commitment is immovable: a Routine, not a task.
        return Routine(
            id=ident,
            name=title,
            weekdays=frozenset(days),
            start_min=_as_int(start_min, name="start_min"),
            end_min=_as_int(end_min, name="end_min"),
            starts_on=now.date(),
        )

    return Task(
        id=ident,
        title=title,
        kind=Kind.HABIT if (days or period_days) else Kind.TASK,
        minutes=minutes,
        min_chunk=min_chunk,
        max_chunk=max_chunk,
        splittable=bool(raw.get("splittable", True)),
        priority=priority,
        energy=energy,
        windows=_windows(raw.get("windows")),
        due=_due(raw.get("due_in_days"), now),
        period_days=7 if days else (_int_or(period_days, 0, name="period_days") or None),
        category=category,
        context=_context(raw.get("context")),
    )


def parse_capture(
    payload: Mapping[str, Any],
    *,
    now: datetime,
    categories: Mapping[str, Category],
    horizon_days: int = 90,
) -> CaptureResult:
    """Validate a model response and turn it into domain objects.

    Unknown categories and dangling dependencies are warnings, not failures: the
    user's sentence should not be lost because the model invented a bucket.
    """
    if not isinstance(payload, Mapping) or not isinstance(payload.get("tasks"), Sequence):
        raise CaptureError("expected an object with a 'tasks' array")

    tasks: list[Task] = []
    routines: list[Routine] = []
    reasoning: dict[str, str] = {}
    warnings: list[str] = []
    taken: set[str] = set()
    by_title: dict[str, str] = {}
    deps_by_title: dict[str, Any] = {}

    for index, raw in enumerate(payload["tasks"]):
        if not isinstance(raw, Mapping):
            warnings.append(f"task {index}: not an object, skipped")
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            warnings.append(f"task {index}: missing title, skipped")
            continue

        category_id = str(raw.get("category") or UNCATEGORISED)
        if category_id not in categories and category_id != UNCATEGORISED:
            warnings.append(f"{title}: unknown category {category_id!r}, kept uncategorised")
            raw = {**raw, "category": UNCATEGORISED}

        ident = _unique(_slug(title), taken)
        by_title[title.strip().lower()] = ident
        deps_by_title[title.strip().lower()] = raw.get("depends_on")
        reasoning[ident] = str(raw.get("reasoning") or "")

        try:
            built = _make_task(ident, title, raw, now)
        except (TypeError, ValueError) as exc:
            warnings.append(f"{title}: {exc}")
            continue
        if isinstance(built, Routine):
            routines.append(built)
        else:
            tasks.append(built)

    # Dependencies are expressed by title because that is what the model read.
    resolved: list[Task] = []
    for task in tasks:
        deps: list[str] = []
        for dep_title in deps_by_title.get(task.title.strip().lower()) or ():
            dep_id = by_title.get(str(dep_title).strip().lower())
            if dep_id is None or dep_id == task.id:
                warnings.append(f"{task.title}: unknown dependency {dep_title!r}, ignored")
                continue
            deps.append(dep_id)
        resolved.append(task if not deps else replace(task, depends_on=tuple(deps)))

    horizon = now + timedelta(days=horizon_days)
    for task in resolved:
        if task.due and task.due > horizon:
            warnings.append(
                f"{task.title}: deadline beyond the {horizon_days}-day planning horizon"
            )

    return CaptureResult(
        tasks=tuple(resolved),
        routines=tuple(routines),
        reasoning=reasoning,
        warnings=tuple(warnings),
    )
