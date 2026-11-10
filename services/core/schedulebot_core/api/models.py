"""Request bodies. Validation lives here so the handlers stay thin.

Bounds are deliberately tight: a task that claims 10,000 minutes or a colour that
is not a colour is a client bug, and it should fail at the edge rather than
become a row nobody can explain later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..categories import Category
from ..domain import Context, Energy, Kind, Priority, Task, Window


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ContextIn(BaseModel):
    people: list[str] = Field(default_factory=list)
    place: str | None = Field(default=None, max_length=120)
    requires: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=2000)

    def to_context(self) -> Context:
        return Context(
            people=tuple(self.people),
            place=self.place,
            requires=tuple(self.requires),
            tags=tuple(self.tags),
            notes=self.notes,
        )


class WindowIn(BaseModel):
    start_min: int = Field(ge=0, le=1440)
    end_min: int = Field(ge=0, le=1440)

    def to_window(self) -> Window:
        return Window(self.start_min, self.end_min)


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    minutes: int = Field(ge=5, le=2880)
    category: str = Field(default="uncategorised", max_length=64)
    priority: int = Field(default=3, ge=1, le=4)
    energy: Literal["high", "medium", "low"] | None = None
    kind: Literal["task", "habit", "routine", "meeting"] = "task"
    min_chunk: int | None = Field(default=None, ge=5, le=1440)
    max_chunk: int | None = Field(default=None, ge=5, le=1440)
    splittable: bool = True
    windows: list[WindowIn] = Field(default_factory=list)
    not_before: datetime | None = None
    due: datetime | None = None
    depends_on: list[str] = Field(default_factory=list)
    max_per_day: int | None = Field(default=None, ge=1, le=8)
    period_days: int | None = Field(default=None, ge=1, le=365)
    remaining: int | None = Field(default=None, ge=0)
    context: ContextIn = Field(default_factory=ContextIn)

    def to_task(self, task_id: str) -> Task:
        return Task(
            id=task_id,
            title=self.title,
            minutes=self.minutes,
            priority=Priority(self.priority),
            energy=Energy(self.energy) if self.energy else None,
            kind=Kind(self.kind),
            min_chunk=self.min_chunk or min(30, self.minutes),
            max_chunk=self.max_chunk or max(self.min_chunk or 0, min(120, self.minutes)),
            splittable=self.splittable,
            windows=tuple(window.to_window() for window in self.windows),
            not_before=self.not_before,
            due=self.due,
            depends_on=tuple(self.depends_on),
            max_per_day=self.max_per_day,
            period_days=self.period_days,
            remaining=self.remaining,
            category=self.category,
            context=self.context.to_context(),
        )


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    colour: str = Field(pattern=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
    energy: Literal["high", "medium", "low"] | None = None
    windows: list[WindowIn] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list, max_length=12)
    description: str = Field(default="", max_length=300)

    def to_category(self, category_id: str) -> Category:
        return Category(
            id=category_id,
            name=self.name,
            colour=self.colour,
            energy=Energy(self.energy) if self.energy else None,
            windows=tuple(window.to_window() for window in self.windows),
            blocking=tuple(block.strip() for block in self.blocking if block.strip()),
            description=self.description,
        )


class CaptureIn(BaseModel):
    note: str = Field(min_length=1, max_length=4000)


class ProgressIn(BaseModel):
    remaining: int | None = Field(default=None, ge=0, le=2880)
