"""Persisting tasks, routines and categories.

Rows are the truth; a plan is a snapshot of one solve. Everything here converts
between the domain objects and columns in one place, so the two can never drift
apart by being serialised in two different ways.

Datetimes are stored as ISO strings with their offset. A deadline is an instant,
never a wall-clock reading — the user's timezone is applied when it is authored,
not when it is read.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime

from ..categories import PRESET_CATEGORIES, Category
from ..domain import Context, Energy, Kind, Priority, Routine, Task, Window
from .db import Store


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dump(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _windows_json(windows: tuple[Window, ...]) -> str:
    return _dump([[window.start_min, window.end_min] for window in windows])


def _windows(raw: str) -> tuple[Window, ...]:
    return tuple(Window(int(pair[0]), int(pair[1])) for pair in json.loads(raw))


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    return datetime.fromisoformat(raw)


def _parse_date(raw: object) -> date | None:
    if not isinstance(raw, str) or not raw:
        return None
    return date.fromisoformat(raw)


# --------------------------------------------------------------- categories


def seed_categories(store: Store, user_id: str) -> None:
    """Give a new account the presets, copied — not referenced.

    Copied so the user can rename or repoint any of them without changing what
    the next person sees, and without a preset update silently rewriting their
    settings.
    """
    existing = store.one("SELECT COUNT(*) AS n FROM categories WHERE user_id = ?", (user_id,))
    if existing and int(existing["n"]):
        return
    with store.write() as conn:
        for category in PRESET_CATEGORIES:
            conn.execute(
                "INSERT OR IGNORE INTO categories"
                " (user_id, id, name, colour, energy, windows, blocking, description)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    category.id,
                    category.name,
                    category.colour,
                    category.energy.value if category.energy else None,
                    _windows_json(category.windows),
                    _dump(list(category.blocking)),
                    category.description,
                ),
            )


def save_category(store: Store, user_id: str, category: Category) -> None:
    store.execute(
        "INSERT INTO categories (user_id, id, name, colour, energy, windows, blocking, description)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(user_id, id) DO UPDATE SET name=excluded.name, colour=excluded.colour,"
        " energy=excluded.energy, windows=excluded.windows, blocking=excluded.blocking,"
        " description=excluded.description",
        (
            user_id,
            category.id,
            category.name,
            category.colour,
            category.energy.value if category.energy else None,
            _windows_json(category.windows),
            _dump(list(category.blocking)),
            category.description,
        ),
    )


def load_categories(store: Store, user_id: str) -> dict[str, Category]:
    out: dict[str, Category] = {}
    for row in store.query(
        "SELECT id, name, colour, energy, windows, blocking, description FROM categories"
        " WHERE user_id = ? ORDER BY id",
        (user_id,),
    ):
        energy = row["energy"]
        out[str(row["id"])] = Category(
            id=str(row["id"]),
            name=str(row["name"]),
            colour=str(row["colour"]),
            energy=Energy(str(energy)) if energy else None,
            windows=_windows(str(row["windows"])),
            blocking=tuple(str(name) for name in json.loads(str(row["blocking"]))),
            description=str(row["description"]),
        )
    return out


# -------------------------------------------------------------------- tasks


def save_task(store: Store, user_id: str, task: Task, *, reasoning: str = "") -> None:
    store.execute(
        "INSERT INTO tasks (user_id, id, title, minutes, remaining, min_chunk, max_chunk,"
        " splittable, priority, energy, kind, category, period_days, windows, not_before,"
        " due, depends_on, max_per_day, context, reasoning, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(user_id, id) DO UPDATE SET title=excluded.title, minutes=excluded.minutes,"
        " remaining=excluded.remaining, min_chunk=excluded.min_chunk,"
        " max_chunk=excluded.max_chunk, splittable=excluded.splittable,"
        " priority=excluded.priority, energy=excluded.energy, kind=excluded.kind,"
        " category=excluded.category, period_days=excluded.period_days,"
        " windows=excluded.windows, not_before=excluded.not_before, due=excluded.due,"
        " depends_on=excluded.depends_on, max_per_day=excluded.max_per_day,"
        " context=excluded.context, reasoning=excluded.reasoning, updated_at=excluded.updated_at",
        (
            user_id,
            task.id,
            task.title,
            task.minutes,
            task.remaining,
            task.min_chunk,
            task.max_chunk,
            int(task.splittable),
            int(task.priority),
            task.energy.value if task.energy else None,
            task.kind.value,
            task.category,
            task.period_days,
            _windows_json(task.windows),
            _dt(task.not_before),
            _dt(task.due),
            _dump(list(task.depends_on)),
            task.max_per_day,
            _dump(
                {
                    "people": list(task.context.people),
                    "place": task.context.place,
                    "requires": list(task.context.requires),
                    "tags": list(task.context.tags),
                    "notes": task.context.notes,
                }
            ),
            reasoning,
            _now(),
            _now(),
        ),
    )


def _task_from_row(row: sqlite3.Row) -> Task:
    r = row
    energy = r["energy"]
    context = json.loads(str(r["context"]))
    return Task(
        id=str(r["id"]),
        title=str(r["title"]),
        minutes=int(r["minutes"]),
        priority=Priority(int(r["priority"])),
        energy=Energy(str(energy)) if energy else None,
        kind=Kind(str(r["kind"])),
        min_chunk=int(r["min_chunk"]),
        max_chunk=int(r["max_chunk"]),
        splittable=bool(r["splittable"]),
        windows=_windows(str(r["windows"])),
        not_before=_parse_dt(r["not_before"]),
        due=_parse_dt(r["due"]),
        depends_on=tuple(str(dep) for dep in json.loads(str(r["depends_on"]))),
        max_per_day=int(r["max_per_day"]) if r["max_per_day"] is not None else None,
        period_days=int(r["period_days"]) if r["period_days"] is not None else None,
        remaining=int(r["remaining"]) if r["remaining"] is not None else None,
        category=str(r["category"]),
        context=Context(
            people=tuple(str(p) for p in context.get("people", [])),
            place=context.get("place"),
            requires=tuple(str(p) for p in context.get("requires", [])),
            tags=tuple(str(p) for p in context.get("tags", [])),
            notes=str(context.get("notes", "")),
        ),
    )


def load_tasks(store: Store, user_id: str) -> list[Task]:
    return [
        _task_from_row(row)
        for row in store.query(
            "SELECT * FROM tasks WHERE user_id = ? AND archived = 0 ORDER BY created_at, id",
            (user_id,),
        )
    ]


def load_task(store: Store, user_id: str, task_id: str) -> Task | None:
    row = store.one("SELECT * FROM tasks WHERE user_id = ? AND id = ?", (user_id, task_id))
    return _task_from_row(row) if row else None


def task_reasoning(store: Store, user_id: str) -> dict[str, str]:
    return {
        str(row["id"]): str(row["reasoning"])
        for row in store.query(
            "SELECT id, reasoning FROM tasks WHERE user_id = ? AND reasoning != ''", (user_id,)
        )
    }


def delete_task(store: Store, user_id: str, task_id: str) -> bool:
    """Archive rather than delete: a plan may still reference the block, and the
    user's history of what they did is not something to throw away on a click."""
    with store.write() as conn:
        cursor = conn.execute(
            "UPDATE tasks SET archived = 1, updated_at = ? WHERE user_id = ? AND id = ?",
            (_now(), user_id, task_id),
        )
    return cursor.rowcount > 0


def record_progress(
    store: Store, user_id: str, task_id: str, *, remaining: int | None
) -> Task | None:
    """Record what is actually left. Never rewrites the original estimate."""
    task = load_task(store, user_id, task_id)
    if task is None:
        return None
    store.execute(
        "UPDATE tasks SET remaining = ?, updated_at = ? WHERE user_id = ? AND id = ?",
        (remaining, _now(), user_id, task_id),
    )
    task.remaining = remaining
    return task


# ----------------------------------------------------------------- routines


def save_routine(store: Store, user_id: str, routine: Routine) -> None:
    store.execute(
        "INSERT INTO routines (user_id, id, name, weekdays, start_min, end_min, starts_on,"
        " ends_on, exdates, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(user_id, id) DO UPDATE SET name=excluded.name,"
        " weekdays=excluded.weekdays, start_min=excluded.start_min, end_min=excluded.end_min,"
        " starts_on=excluded.starts_on, ends_on=excluded.ends_on, exdates=excluded.exdates,"
        " updated_at=excluded.updated_at",
        (
            user_id,
            routine.id,
            routine.name,
            _dump(sorted(routine.weekdays)),
            routine.start_min,
            routine.end_min,
            routine.starts_on.isoformat(),
            routine.ends_on.isoformat() if routine.ends_on else None,
            _dump([day.isoformat() for day in sorted(routine.exdates)]),
            _now(),
            _now(),
        ),
    )


def load_routines(store: Store, user_id: str) -> list[Routine]:
    out: list[Routine] = []
    rows = store.query(
        "SELECT * FROM routines WHERE user_id = ? ORDER BY start_min", (user_id,)
    )
    for row in rows:
        ends_on = _parse_date(row["ends_on"])
        out.append(
            Routine(
                id=str(row["id"]),
                name=str(row["name"]),
                weekdays=frozenset(int(day) for day in json.loads(str(row["weekdays"]))),
                start_min=int(row["start_min"]),
                end_min=int(row["end_min"]),
                starts_on=date.fromisoformat(str(row["starts_on"])),
                ends_on=ends_on,
                exdates=frozenset(
                    date.fromisoformat(day) for day in json.loads(str(row["exdates"]))
                ),
            )
        )
    return out


def delete_routine(store: Store, user_id: str, routine_id: str) -> bool:
    with store.write() as conn:
        cursor = conn.execute(
            "DELETE FROM routines WHERE user_id = ? AND id = ?", (user_id, routine_id)
        )
    return cursor.rowcount > 0
