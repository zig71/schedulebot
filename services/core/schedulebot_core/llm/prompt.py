"""The prompt, and the reasoning it is allowed to produce.

Kept next to the schema so the two cannot drift: a prompt that asks for a field
the schema forbids is a bug that shows up as a validation warning nobody reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..categories import Category
from .contract import CAPTURE_SCHEMA

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def build_system_prompt(
    categories: dict[str, Category], *, now: datetime
) -> str:
    """The system message for capture.

    `now` is passed in so "tomorrow" resolves against the *user's* local date,
    not the server's — the two are different for most of the day.
    """
    lines = [
        "You turn a person's notes about their work into scheduling requests.",
        "",
        "HARD RULES",
        "- Never choose a clock time for a task. You may describe preferences "
        "(\"mornings\", \"after lunch\"), durations, deadlines and repetition. "
        "A separate scheduler picks the actual slot from real availability.",
        "- `reasoning` explains how you INTERPRETED the note: what you inferred, "
        "what you were unsure about, why you picked that category. It must not "
        "claim to know where the task will be placed.",
        "- Never invent a category. Choose one id from the list below, or use "
        "\"uncategorised\" when nothing fits.",
        "- If the note is ambiguous, prefer fewer assumptions: leave a field null "
        "rather than guessing a duration or a deadline.",
        "",
        f"Today is {WEEKDAYS[now.weekday()]} {now:%d %B %Y}, {now:%H:%M} local time.",
        "",
        "CATEGORIES",
    ]
    for category in categories.values():
        description = f" — {category.description}" if category.description else ""
        lines.append(f"- {category.id}: {category.name}{description}")

    lines += [
        "",
        "FIELDS",
        "- minutes: total time needed, not the length of one sitting.",
        "- min_chunk_minutes / max_chunk_minutes: bounds on a single sitting. "
        "Set them equal AND splittable=false only when the activity cannot be "
        "interrupted at all (a class, a call).",
        "- due_in_days: whole days from today. Null when no deadline is stated.",
        "- depends_on: titles of other tasks in this same response that must "
        "happen first.",
        "- recurrence: for repeating work. `period_days` for a repeating quota; "
        "`weekdays` plus `start_min`/`end_min` only when the note gives a fixed "
        "time (a Tuesday 09:00 lecture).",
        "",
        "FREQUENCY",
        "A quota such as \"gym twice this week\" is ONE task, not two: set "
        "`minutes` to the total for the period, `max_chunk_minutes` to the length "
        "of a single session, and `max_per_day` to 1. The scheduler then spreads "
        "it over separate days. Do not create duplicate tasks to express "
        "frequency, and do not put the whole quota in one sitting.",
        "- context: people involved, place, kit required, tags.",
        "",
        "Return one object matching the schema. No prose outside it.",
    ]
    return "\n".join(lines)


def build_user_message(note: str) -> str:
    return f"Notes to schedule:\n\n{note}"


def response_format(model_name: str = "capture") -> dict[str, Any]:
    """OpenAI-compatible strict structured-output request body fragment."""
    return {
        "type": "json_schema",
        "json_schema": {"name": model_name, "schema": CAPTURE_SCHEMA, "strict": True},
    }
