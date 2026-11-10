"""Solve: expand, reserve, fill, report.

The orchestration, and the only place that knows about all the parts. Ordering
here encodes two product decisions:

* **Dependencies constrain, priority sorts.** A task that depends on another is
  laid out after it, always — user intent beats priority. Among tasks that are
  free to run, the most important gets first refusal of the good slots.
* **Nothing is silently dropped.** Demand that could not be placed is a
  `Shortfall`; demand that finished after its deadline is a `Late`. A plan that
  is wrong in either way still says so.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from ..categories import Category, catalog, resolve
from ..domain import Block, Late, Occupied, Plan, Priority, Routine, Shortfall, Task
from ..slots import SLOTS_PER_DAY
from .expand import Requirement, expand_all, expand_routines
from .place import Occupancy, Placer

__all__ = ["solve", "topological_order"]


def topological_order(tasks: Sequence[Task]) -> list[Task]:
    """Dependency-first order. Raises on a cycle rather than looping forever."""
    by_id = {task.id: task for task in tasks}
    order: list[Task] = []
    state: dict[str, int] = {}  # 1 = visiting, 2 = done

    def visit(task: Task, path: tuple[str, ...]) -> None:
        mark = state.get(task.id, 0)
        if mark == 2:
            return
        if mark == 1:
            raise ValueError(f"dependency cycle: {' -> '.join((*path, task.id))}")
        state[task.id] = 1
        for dep_id in task.depends_on:
            dep = by_id.get(dep_id)
            if dep is not None:
                visit(dep, (*path, task.id))
        state[task.id] = 2
        order.append(task)

    for task in tasks:
        visit(task, ())
    return order


def solve(
    tasks: Sequence[Task],
    routines: Sequence[Routine] = (),
    busy: Sequence[Occupied] = (),
    *,
    origin: object,
    horizon_slots: int,
    now_slot: int = 0,
    previous: Plan | None = None,
    categories: dict[str, Category] | None = None,
) -> Plan:
    started = time.perf_counter()
    if horizon_slots <= 0:
        raise ValueError("horizon_slots must be positive")

    # Category defaults fill only what the task left blank.
    cat = catalog() if categories is None else categories
    tasks = [resolve(task, cat) for task in tasks]

    requirements = expand_all(list(tasks), origin, horizon_slots, now_slot)  # type: ignore[arg-type]
    fixed = expand_routines(list(routines), origin, horizon_slots)  # type: ignore[arg-type]
    occupancy = Occupancy([(span.start_slot, span.end_slot) for span in (*fixed, *busy)])
    placer = Placer(occupancy, now_slot)
    prior = previous.by_key() if previous else {}

    by_task: dict[str, list[Requirement]] = {}
    for req in requirements:
        by_task.setdefault(req.task.id, []).append(req)

    order = topological_order([task for task in tasks if task.outstanding])
    blocks: list[Block] = []
    placed_slots: dict[tuple[str, int], int] = {}
    kept: dict[tuple[str, int], list[Block]] = {}

    # -- pass one: hold what we already had -------------------------------
    for task in order:
        for req in by_task.get(task.id, ()):
            held = placer.reserve(req, prior)
            kept[req.key_prefix] = held
            placed_slots[req.key_prefix] = sum(block.slots for block in held)
            blocks.extend(held)

    # -- dependency audit on the held blocks ------------------------------
    ends = _ends_by_task(blocks)
    for task in order:  # dependency order, so a dep's end is already known
        for req in by_task.get(task.id, ()):
            floor = _dependency_floor(task, ends)
            still_held: list[Block] = []
            for block in kept.get(req.key_prefix, []):
                if block.start_slot < floor:
                    occupancy.release(block.start_slot, block.slots)
                    placed_slots[req.key_prefix] -= block.slots
                    blocks.remove(block)
                    continue
                still_held.append(block)
            kept[req.key_prefix] = still_held

    # -- pass two: fill the rest, most important first ---------------------
    # Priority decides who gets first refusal of the good slots; the loop below
    # defers any task whose dependencies have not been laid out yet, so a
    # dependency always wins over an urgent dependent.
    pending = sorted(
        (task for task in order if by_task.get(task.id)),
        key=lambda task: (task.priority, task.id),
    )
    progress = True
    while pending and progress:
        progress = False
        for task in list(pending):
            if not _dependencies_settled(task, ends):
                continue
            floor = _dependency_floor(task, ends)
            for req in by_task.get(task.id, ()):
                after = task.depends_on[0] if task.depends_on else None
                new_blocks, placed = placer.fill(req, kept.get(req.key_prefix, []), floor, after)
                blocks.extend(new_blocks)
                if req.key_prefix in kept:
                    kept[req.key_prefix] = [*kept[req.key_prefix], *new_blocks]
                placed_slots[req.key_prefix] = placed_slots.get(req.key_prefix, 0) + placed
            pending.remove(task)
            progress = True
            ends = _ends_by_task(blocks)

    blocks.sort(key=lambda block: (block.start_slot, block.task_id))
    return _report(blocks, requirements, placed_slots, origin, horizon_slots, started)


def _ends_by_task(blocks: Sequence[Block]) -> dict[str, int]:
    """Latest end slot per task, across every occurrence."""
    ends: dict[str, int] = {}
    for block in blocks:
        ends[block.task_id] = max(ends.get(block.task_id, 0), block.end_slot)
    return ends


def _dependencies_settled(task: Task, ends: dict[str, int]) -> bool:
    """True when every dependency has been laid out (or does not exist)."""
    return all(dep in ends for dep in task.depends_on)


def _dependency_floor(task: Task, ends: dict[str, int]) -> int:
    """No block may start before the last block of anything it depends on."""
    return max((ends.get(dep, 0) for dep in task.depends_on), default=0)


def _report(
    blocks: list[Block],
    requirements: Sequence[Requirement],
    placed_slots: dict[tuple[str, int], int],
    origin: object,
    horizon_slots: int,
    started: float,
) -> Plan:
    shortfalls: list[Shortfall] = []
    late: list[Late] = []
    for req in requirements:
        placed = placed_slots.get(req.key_prefix, 0)
        if placed < req.required_slots:
            shortfalls.append(
                Shortfall(
                    task_id=req.task.id,
                    occurrence=req.occurrence,
                    required_slots=req.required_slots,
                    placed_slots=placed,
                )
            )
        end = max(
            (
                block.end_slot
                for block in blocks
                if block.task_id == req.task.id and block.occurrence == req.occurrence
            ),
            default=0,
        )
        if placed and req.due_slot is not None and end > req.due_slot:
            late.append(
                Late(
                    task_id=req.task.id,
                    occurrence=req.occurrence,
                    due_slot=req.due_slot,
                    placed_end_slot=end,
                )
            )
    return Plan(
        origin=origin,  # type: ignore[arg-type]
        horizon_slots=horizon_slots,
        blocks=blocks,
        shortfalls=shortfalls,
        late=late,
        solve_ms=(time.perf_counter() - started) * 1000,
    )


def horizon_for(days: int) -> int:
    return days * SLOTS_PER_DAY


def default_priority() -> Priority:
    return Priority.P3
