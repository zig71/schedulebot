"""Storing the plan as a snapshot.

A plan is derived data: given the tasks, the routines and a moment, the solver
produces it. Persisting it anyway buys two things a re-solve cannot:

* **stability** — the next solve needs to know where blocks were, or every
  unrelated edit reshuffles the week;
* **a fixed answer to "what am I doing now"** — the UI must not re-solve on every
  poll, or the answer changes while the user is looking at it.

Slots are stored relative to the plan's own `origin`, which is stored alongside.
That is safe *because* the origin travels with them: reading the plan a week
later still yields the same instants.
"""

from __future__ import annotations

import json
from datetime import datetime

from ..domain import Block, Late, Plan, Priority, Reason, Shortfall
from .db import Store


def _reason_out(reason: Reason | None) -> dict[str, object] | None:
    if reason is None:
        return None
    return {
        "window": list(reason.window) if reason.window else None,
        "kept_previous": reason.kept_previous,
        "after_dependency": reason.after_dependency,
        "rejected": [list(item) for item in reason.rejected],
    }


def _reason_in(raw: object) -> Reason | None:
    if not isinstance(raw, dict):
        return None
    window = raw.get("window")
    rejected = raw.get("rejected") or []
    return Reason(
        window=(int(window[0]), int(window[1])) if window else None,
        kept_previous=bool(raw.get("kept_previous")),
        after_dependency=str(raw["after_dependency"]) if raw.get("after_dependency") else None,
        rejected=tuple(
            (int(item[0]), int(item[1]), str(item[2]))
            for item in rejected
            if isinstance(item, list) and len(item) == 3
        ),
    )


def payload(plan: Plan) -> dict[str, object]:
    return {
        "origin": plan.origin.isoformat(),
        "horizon_slots": plan.horizon_slots,
        "solve_ms": plan.solve_ms,
        "blocks": [
            {
                "task_id": block.task_id,
                "occurrence": block.occurrence,
                "chunk": block.chunk,
                "start_slot": block.start_slot,
                "end_slot": block.end_slot,
                "priority": int(block.priority),
                "moved_from": block.moved_from,
                "reason": _reason_out(block.reason),
            }
            for block in plan.blocks
        ],
        "shortfalls": [
            {
                "task_id": item.task_id,
                "occurrence": item.occurrence,
                "required_slots": item.required_slots,
                "placed_slots": item.placed_slots,
            }
            for item in plan.shortfalls
        ],
        "late": [
            {
                "task_id": item.task_id,
                "occurrence": item.occurrence,
                "due_slot": item.due_slot,
                "placed_end_slot": item.placed_end_slot,
            }
            for item in plan.late
        ],
    }


def _items(value: object) -> list[dict[str, object]]:
    """Read a JSON array of objects, ignoring anything that is not one."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return default


def _float(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def parse(raw: dict[str, object]) -> Plan:
    blocks = [
        Block(
            task_id=_str(item.get("task_id")),
            occurrence=_int(item.get("occurrence")),
            chunk=_int(item.get("chunk")),
            start_slot=_int(item.get("start_slot")),
            end_slot=_int(item.get("end_slot")),
            priority=Priority(_int(item.get("priority"), 3)),
            moved_from=(
                _int(item["moved_from"]) if item.get("moved_from") is not None else None
            ),
            reason=_reason_in(item.get("reason")),
        )
        for item in _items(raw.get("blocks"))
    ]
    shortfalls = [
        Shortfall(
            task_id=_str(item.get("task_id")),
            occurrence=_int(item.get("occurrence")),
            required_slots=_int(item.get("required_slots")),
            placed_slots=_int(item.get("placed_slots")),
        )
        for item in _items(raw.get("shortfalls"))
    ]
    late = [
        Late(
            task_id=_str(item.get("task_id")),
            occurrence=_int(item.get("occurrence")),
            due_slot=_int(item.get("due_slot")),
            placed_end_slot=_int(item.get("placed_end_slot")),
        )
        for item in _items(raw.get("late"))
    ]
    return Plan(
        origin=datetime.fromisoformat(_str(raw.get("origin"))),
        horizon_slots=_int(raw.get("horizon_slots")),
        blocks=blocks,
        shortfalls=shortfalls,
        late=late,
        solve_ms=_float(raw.get("solve_ms")),
    )


def save(store: Store, user_id: str, plan: Plan, *, warnings: list[str] | None = None) -> None:
    store.execute(
        "INSERT INTO plans (user_id, origin, horizon_slots, solved_at, solve_ms, warnings, payload)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET origin=excluded.origin,"
        " horizon_slots=excluded.horizon_slots, solved_at=excluded.solved_at,"
        " solve_ms=excluded.solve_ms, warnings=excluded.warnings, payload=excluded.payload",
        (
            user_id,
            plan.origin.isoformat(),
            plan.horizon_slots,
            datetime.now(plan.origin.tzinfo).isoformat(),
            plan.solve_ms,
            json.dumps(warnings or [], separators=(",", ":")),
            json.dumps(payload(plan), separators=(",", ":")),
        ),
    )


def load(store: Store, user_id: str) -> tuple[Plan | None, list[str], datetime | None]:
    """Return the stored plan, its warnings, and when it was solved."""
    row = store.one("SELECT payload, warnings, solved_at FROM plans WHERE user_id = ?", (user_id,))
    if row is None:
        return None, [], None
    plan = parse(json.loads(str(row["payload"])))
    warnings = [str(item) for item in json.loads(str(row["warnings"]))]
    return plan, warnings, datetime.fromisoformat(str(row["solved_at"]))
