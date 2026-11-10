"""Turning a recorded placement reason into a sentence a person can act on.

Derived from what the placer actually decided — never from a model, and never
from re-reading a finished plan. If a reason cannot be derived, the honest
output is "no reason recorded", not a plausible story.
"""

from __future__ import annotations

from datetime import datetime

from .domain import Block, Task
from .slots import slot_datetime

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _clock(slot: int, origin: datetime) -> str:
    return slot_datetime(slot, origin).strftime("%a %H:%M")


def explain(block: Block, task: Task, origin: datetime, now_slot: int = 0) -> str:
    """One sentence: why this block is where it is."""
    parts: list[str] = []
    reason = block.reason

    if reason is None:
        return f"{task.title} at {_clock(block.start_slot, origin)} (no reason recorded)."

    if reason.kept_previous:
        parts.append("kept where it already was, so an unrelated change did not move it")
    else:
        if reason.window is not None:
            lo, hi = reason.window
            parts.append(
                f"placed in its {_clock(lo, origin)}–{_clock(hi, origin)} window"
            )
        if reason.after_dependency:
            parts.append(f"after {reason.after_dependency}, which it depends on")

    if task.due is not None:
        parts.append(f"due {task.due:%a %d %b %H:%M}")
    if task.remaining_minutes != task.minutes:
        parts.append(f"{task.remaining_minutes} of {task.minutes} minutes left")
    if task.energy is not None:
        parts.append(f"{task.energy.value}-energy work")

    if reason.rejected:
        first = reason.rejected[0]
        parts.append(f"earlier options rejected: {first[2]}")

    sentence = "; ".join(parts) if parts else "placed by default ordering"
    return f"{task.title} at {_clock(block.start_slot, origin)}: {sentence}."
