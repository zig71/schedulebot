"""The domain: what can be scheduled, and what a schedule is.

Deliberately plain dataclasses with validation in `__post_init__` rather than a
validation framework: the solver is the part that must stay pure, fast and
dependency-free, so it can be reasoned about and tested without a stack.
Transport validation belongs at the API edge, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import IntEnum, StrEnum

from .slots import SLOT_MINUTES, slots_for


class Priority(IntEnum):
    """P1 is most important, matching the vocabulary people already use."""

    P1 = 1
    P2 = 2
    P3 = 3
    P4 = 4


class Energy(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Kind(StrEnum):
    TASK = "task"
    HABIT = "habit"
    ROUTINE = "routine"
    MEETING = "meeting"


# Low value = placed earlier when priorities tie. Fixed commitments win first.
KIND_RANK: dict[Kind, int] = {
    Kind.MEETING: 0,
    Kind.ROUTINE: 1,
    Kind.HABIT: 2,
    Kind.TASK: 3,
}


@dataclass(frozen=True, slots=True)
class Window:
    """An allowed time-of-day range, minutes from local midnight."""

    start_min: int
    end_min: int

    def __post_init__(self) -> None:
        if not 0 <= self.start_min < self.end_min <= 24 * 60:
            raise ValueError(f"window {self.start_min}-{self.end_min} is not a valid range")

    @property
    def length(self) -> int:
        return self.end_min - self.start_min


@dataclass(frozen=True, slots=True)
class Context:
    """What the task needs around it.

    Structured rather than a note, because each field changes a decision:
    `people` makes an event multi-attendee, `place` rules out a slot you are not
    at, `requires` is the kit you need before you can start. Free text stays in
    `notes` for everything that only a human needs to read.
    """

    people: tuple[str, ...] = ()
    place: str | None = None
    requires: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    notes: str = ""

    @property
    def is_multi_attendee(self) -> bool:
        return len(self.people) > 0


@dataclass(slots=True)
class Task:
    """A demand for time.

    `minutes` is the *total* the task needs; `remaining` is what is left after
    real work has been recorded. Keeping both is what makes "I did 45 of the 120
    minutes" representable without rewriting history.
    """

    id: str
    title: str
    minutes: int
    priority: Priority = Priority.P3
    energy: Energy | None = None
    kind: Kind = Kind.TASK
    min_chunk: int = 30
    max_chunk: int = 120
    splittable: bool = True
    windows: tuple[Window, ...] = ()
    not_before: datetime | None = None
    due: datetime | None = None
    depends_on: tuple[str, ...] = ()
    max_per_day: int | None = None
    period_days: int | None = None
    remaining: int | None = None
    notes: str = ""
    category: str = "uncategorised"
    context: Context = field(default_factory=Context)

    def __post_init__(self) -> None:
        if self.minutes <= 0:
            raise ValueError(f"{self.id}: minutes must be positive")
        if self.remaining is not None and not 0 <= self.remaining <= self.minutes:
            raise ValueError(f"{self.id}: remaining {self.remaining} outside 0..{self.minutes}")
        if self.min_chunk <= 0 or self.max_chunk < self.min_chunk:
            raise ValueError(
                f"{self.id}: chunk bounds {self.min_chunk}..{self.max_chunk} are invalid"
            )
        if self.minutes < self.min_chunk:
            raise ValueError(f"{self.id}: {self.minutes} min is smaller than one chunk")
        if self.period_days is not None and self.period_days <= 0:
            raise ValueError(f"{self.id}: period_days must be positive")
        for value in (self.minutes, self.min_chunk, self.max_chunk):
            if value % SLOT_MINUTES:
                raise ValueError(f"{self.id}: {value} is not a multiple of {SLOT_MINUTES} minutes")
        if self.not_before and self.due and self.due <= self.not_before:
            raise ValueError(f"{self.id}: due must be after not_before")

    @property
    def remaining_minutes(self) -> int:
        return self.minutes if self.remaining is None else self.remaining

    @property
    def outstanding(self) -> bool:
        return self.remaining_minutes > 0

    @property
    def duration_slots(self) -> int:
        return slots_for(self.remaining_minutes)


@dataclass(frozen=True, slots=True)
class Routine:
    """An immovable weekly commitment.

    `weekdays` uses 0=Monday..6=Sunday. `exdates` are skipped occurrences —
    bank holidays, a cancelled class — which is the one piece of recurrence
    people actually use and therefore the piece that must not be missing.
    """

    id: str
    name: str
    weekdays: frozenset[int]
    start_min: int
    end_min: int
    starts_on: date
    ends_on: date | None = None
    exdates: frozenset[date] = frozenset()

    def __post_init__(self) -> None:
        if not self.weekdays:
            raise ValueError(f"{self.id}: a routine needs at least one weekday")
        if any(not 0 <= day <= 6 for day in self.weekdays):
            raise ValueError(f"{self.id}: weekdays must be 0..6")
        if not 0 <= self.start_min < self.end_min <= 24 * 60:
            raise ValueError(f"{self.id}: time range is invalid")


@dataclass(frozen=True, slots=True)
class Occupied:
    """A span that placement may never use."""

    start_slot: int
    end_slot: int
    label: str
    source: str = "routine"

    def __post_init__(self) -> None:
        if self.end_slot <= self.start_slot:
            raise ValueError("occupied span must have positive length")


@dataclass(frozen=True, slots=True)
class Block:
    """One sitting of one task. `key` is its stable identity, for calendars."""

    task_id: str
    occurrence: int
    chunk: int
    start_slot: int
    end_slot: int
    priority: Priority
    moved_from: int | None = None
    reason: Reason | None = None

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.task_id, self.occurrence, self.chunk)

    @property
    def slots(self) -> int:
        return self.end_slot - self.start_slot


@dataclass(frozen=True, slots=True)
class Reason:
    """Why this block sits where it does — recorded at placement time.

    Deriving this afterwards from the finished plan is guesswork; the placer
    knows which window it chose and what it rejected, so it says so.
    """

    window: tuple[int, int] | None = None
    kept_previous: bool = False
    after_dependency: str | None = None
    rejected: tuple[tuple[int, int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Shortfall:
    """Demand that could not be placed at all."""

    task_id: str
    occurrence: int
    required_slots: int
    placed_slots: int


@dataclass(frozen=True, slots=True)
class Late:
    """Demand that was placed, but finishes after its deadline."""

    task_id: str
    occurrence: int
    due_slot: int
    placed_end_slot: int


@dataclass(slots=True)
class Plan:
    origin: datetime
    horizon_slots: int
    blocks: list[Block] = field(default_factory=list)
    shortfalls: list[Shortfall] = field(default_factory=list)
    late: list[Late] = field(default_factory=list)
    solve_ms: float = 0.0

    @property
    def complete(self) -> bool:
        return not self.shortfalls and not self.late

    def by_key(self) -> dict[tuple[str, int, int], Block]:
        return {block.key: block for block in self.blocks}

    @property
    def moved(self) -> list[Block]:
        """Blocks that actually changed slot.

        `moved_from` is set for any block that existed before, so it is not a
        moved flag; comparing it with the current start is.
        """
        return [b for b in self.blocks if b.moved_from is not None and b.moved_from != b.start_slot]

    @property
    def disturbance_slots(self) -> int:
        return sum(abs(b.start_slot - b.moved_from) for b in self.moved if b.moved_from is not None)
