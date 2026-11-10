"""Turning a plan into the shape the API, the notifier and the client all want.

One place, because three consumers describing the same block three ways is how a
schedule starts disagreeing with itself. The rules:

* instants are ISO strings in the plan's own timezone, never bare slots;
* every block carries its **explanation**, written by the solver at placement
  time — if there is none, the text says so rather than inventing one;
* the blocking list comes from the task's category, and is empty unless the user
  mapped one.
"""

from __future__ import annotations

from datetime import datetime

from .categories import Category, blocking_for, categorise
from .domain import Block, Plan, Task
from .explain import explain
from .slots import minutes_for, slot_datetime


def _task_map(tasks: list[Task] | None) -> dict[str, Task]:
    return {task.id: task for task in tasks or []}


def block_view(
    block: Block,
    task: Task | None,
    *,
    origin: datetime,
    categories: dict[str, Category],
    now_slot: int = 0,
) -> dict[str, object]:
    start = slot_datetime(block.start_slot, origin)
    end = slot_datetime(block.end_slot, origin)
    view: dict[str, object] = {
        "task_id": block.task_id,
        "title": task.title if task else block.task_id,
        "category": task.category if task else "uncategorised",
        "category_name": categorise(task, categories) if task else "uncategorised",
        "colour": (
            categories[task.category].colour
            if task and task.category in categories
            else None
        ),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "minutes": minutes_for(block.slots),
        "priority": int(block.priority),
        "explanation": explain(block, task, origin, now_slot) if task else "unknown task",
        "blocking": list(blocking_for(task, categories)) if task else [],
        "is_now": block.start_slot <= now_slot < block.end_slot,
        "is_past": block.end_slot <= now_slot,
        "moved_from": (
            slot_datetime(block.moved_from, origin).isoformat()
            if block.moved_from is not None
            else None
        ),
    }
    if task is not None:
        view["energy"] = task.energy.value if task.energy else None
        view["context"] = {
            "people": list(task.context.people),
            "place": task.context.place,
            "requires": list(task.context.requires),
            "tags": list(task.context.tags),
        }
    return view


def plan_view(
    plan: Plan,
    *,
    tasks: list[Task] | None = None,
    categories: dict[str, Category],
    now_slot: int = 0,
) -> dict[str, object]:
    by_id = _task_map(tasks)
    titles = {task_id: task.title for task_id, task in by_id.items()}
    return {
        "origin": plan.origin.isoformat(),
        "horizon_slots": plan.horizon_slots,
        "solve_ms": round(plan.solve_ms, 2),
        "complete": plan.complete,
        "disturbance_minutes": minutes_for(plan.disturbance_slots),
        "blocks": [
            block_view(block, by_id.get(block.task_id), origin=plan.origin,
                       categories=categories, now_slot=now_slot)
            for block in plan.blocks
        ],
        "shortfalls": [
            {
                "task_id": item.task_id,
                "title": titles.get(item.task_id, item.task_id),
                "occurrence": item.occurrence,
                "required_minutes": minutes_for(item.required_slots),
                "placed_minutes": minutes_for(item.placed_slots),
            }
            for item in plan.shortfalls
        ],
        "late": [
            {
                "task_id": item.task_id,
                "title": titles.get(item.task_id, item.task_id),
                "occurrence": item.occurrence,
                "due": slot_datetime(item.due_slot, plan.origin).isoformat(),
                "finishes": slot_datetime(item.placed_end_slot, plan.origin).isoformat(),
                "minutes_late": minutes_for(max(0, item.placed_end_slot - item.due_slot)),
            }
            for item in plan.late
        ],
    }


def now_view(
    plan: Plan,
    *,
    tasks: list[Task] | None = None,
    categories: dict[str, Category],
    now_slot: int,
) -> dict[str, object] | None:
    """"What should I be doing, and why" — the question the product exists for.

    Returns the block covering now, or the next one if nothing is running. When
    the plan is over it returns None rather than guessing.
    """
    by_id = _task_map(tasks)
    ordered = sorted(plan.blocks, key=lambda block: block.start_slot)
    current = next((b for b in ordered if b.start_slot <= now_slot < b.end_slot), None)
    following = next((b for b in ordered if b.start_slot > now_slot), None)
    chosen = current or following
    if chosen is None:
        return None
    view = block_view(chosen, by_id.get(chosen.task_id), origin=plan.origin,
                      categories=categories, now_slot=now_slot)
    view["state"] = "now" if current else "next"
    view["starts_in_minutes"] = max(0, minutes_for(chosen.start_slot - now_slot))
    view["ends_in_minutes"] = minutes_for(max(0, chosen.end_slot - now_slot))
    return view
