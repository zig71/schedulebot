"""iCalendar output: a read-only feed for the calendar you already use.

The hard part is not writing VEVENTs, it is not wrecking the user's calendar. A
naive feed regenerates events with new timestamps or new identifiers every solve,
and the client responds by duplicating, or silently relocating, everything the
user has already seen.

So three rules, each of which exists because breaking it is a real failure:

1. **One UID per block, forever.** Derived from (task, occurrence, chunk) by a
   single shared function. Re-solving does not rename a block, so the client
   updates the event in place instead of creating a second one.
2. **SEQUENCE tracks the absolute start.** Move a block and its `SEQUENCE` goes
   up, which is how a client knows the change is real rather than a stale copy.
   Comparing day-relative slots instead is how midnight becomes a silent move.
3. **A removed block is cancelled, not forgotten.** The feed emits the event one
   more time with `STATUS:CANCELLED`, or the ghost stays in the calendar for
   weeks because nothing told the client to drop it.

Everything here is pure: strings in, strings out, with previous state passed in
rather than read from a database.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from .categories import Category, categorise
from .domain import Plan, Task
from .explain import explain
from .slots import slot_datetime

PRODID = "-//schedulebot//schedulebot 0.1//EN"
UID_DOMAIN = "schedulebot"
#: Cancelled events are re-sent for this long, then dropped from the feed.
CANCELLATION_DAYS = 30
#: A client that missed the cancel is served it again for this many days.
CANCELLATION_GRACE_DAYS = 14
FOLD_OCTETS = 75


@dataclass(frozen=True, slots=True)
class IcsRow:
    """What we told the client last time, for one event."""

    uid: str
    sequence: int
    starts_at: str  # UTC ISO 8601, or "" once cancelled
    cancelled: bool = False
    updated_at: str = ""  # when this row last actually changed


@dataclass(frozen=True, slots=True)
class Event:
    uid: str
    start: datetime
    end: datetime
    summary: str
    description: str
    categories: str
    sequence: int
    cancelled: bool = False


def ics_uid(task_id: str, occurrence: int, chunk: int) -> str:
    """The one place an event identity is decided.

    Production and tests must both call this. A test that rebuilds the same
    f-string locally will keep passing after production changes, which is exactly
    the bug it was meant to catch.
    """
    return f"{task_id}.{occurrence}.{chunk}@{UID_DOMAIN}"


def _escape(text: str) -> str:
    """RFC 5545 TEXT escaping. Backslash first, or it escapes its own output."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold to 75 octets, continuing with a space, as RFC 5545 requires.

    Measured in octets of UTF-8, not characters: a folded line with an em dash in
    it is longer than it looks.
    """
    encoded = line.encode("utf-8")
    if len(encoded) <= FOLD_OCTETS:
        return line
    parts: list[bytes] = []
    remaining = encoded
    limit = FOLD_OCTETS
    while len(remaining) > limit:
        cut = limit
        # Do not split a multi-byte character in half.
        while cut > 0 and (remaining[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(remaining[:cut])
        remaining = remaining[cut:]
        limit = FOLD_OCTETS - 1  # continuation lines start with a space
    parts.append(remaining)
    return "\r\n ".join(part.decode("utf-8") for part in parts)


def _parse_stamp(raw: str, fallback: datetime) -> datetime:
    if not raw:
        return fallback
    try:
        return datetime.fromisoformat(raw).astimezone(UTC)
    except ValueError:
        return fallback


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _event_lines(event: Event, stamp: datetime) -> list[str]:
    status = "CANCELLED" if event.cancelled else "CONFIRMED"
    lines = [
        "BEGIN:VEVENT",
        f"UID:{_escape(event.uid)}",
        f"DTSTAMP:{_utc(stamp)}",
        f"DTSTART:{_utc(event.start)}",
        f"DTEND:{_utc(event.end)}",
        f"SEQUENCE:{event.sequence}",
        f"STATUS:{status}",
        f"SUMMARY:{_escape(event.summary)}",
    ]
    if event.description:
        lines.append(f"DESCRIPTION:{_escape(event.description)}")
    if event.categories:
        lines.append(f"CATEGORIES:{_escape(event.categories)}")
    # Busy, because the point of the block is that the time is taken.
    lines.append("TRANSP:OPAQUE")
    lines.append("END:VEVENT")
    return lines


def plan_events(
    plan: Plan,
    *,
    tasks: Iterable[Task],
    categories: Mapping[str, Category],
    previous: Mapping[str, IcsRow],
    now_slot: int = 0,
    now: datetime | None = None,
) -> tuple[list[Event], dict[str, IcsRow]]:
    """Current events with their sequences, plus the state to store next time."""
    by_id = {task.id: task for task in tasks}
    events: list[Event] = []
    state: dict[str, IcsRow] = {}
    seen: set[str] = set()

    for block in plan.blocks:
        task = by_id.get(block.task_id)
        if task is None:  # archived or deleted: the calendar should not keep it
            continue
        uid = ics_uid(block.task_id, block.occurrence, block.chunk)
        seen.add(uid)
        start = slot_datetime(block.start_slot, plan.origin)
        end = slot_datetime(block.end_slot, plan.origin)
        prior = previous.get(uid)
        if prior is None:
            sequence = 0
        elif prior.starts_at == start.astimezone(UTC).isoformat():
            sequence = prior.sequence
        else:
            # The block moved. A sequence bump is the only way a client learns
            # that the new time is authoritative rather than a stale duplicate.
            sequence = prior.sequence + 1

        why = explain(block, task, plan.origin, now_slot)
        blocking = categories[task.category].blocking if task.category in categories else ()
        description = why
        if blocking:
            description = f"{why} Blocks: {', '.join(blocking)}."
        events.append(
            Event(
                uid=uid,
                start=start,
                end=end,
                summary=task.title,
                description=description,
                categories=categorise(task, dict(categories)),
                sequence=sequence,
            )
        )
        state[uid] = IcsRow(
            uid=uid,
            sequence=sequence,
            starts_at=start.astimezone(UTC).isoformat(),
            updated_at=(
                (prior.updated_at or datetime.now(UTC).isoformat())
                if prior is not None and sequence == prior.sequence
                else datetime.now(UTC).isoformat()
            ),
        )

    # Blocks that vanished get a VEVENT with STATUS:CANCELLED.
    #
    # A cancellation is re-sent for a grace window, with the *same* sequence, so a
    # client that was offline when the block disappeared still learns about it.
    # Sending it exactly once meant a client that missed that one poll kept the
    # ghost event forever; bumping the sequence each time would instead make every
    # poll look like a new change.
    stamp = (now or datetime.now(UTC)).astimezone(UTC)
    for uid, row in previous.items():
        if uid in seen:
            continue
        if row.cancelled:
            age = stamp - _parse_stamp(row.updated_at, stamp)
            if row.starts_at and age.days <= CANCELLATION_GRACE_DAYS:
                state[uid] = row
                events.append(
                    Event(
                        uid=uid,
                        start=_parse_stamp(row.starts_at, stamp),
                        end=_parse_stamp(row.starts_at, stamp),
                        summary="Removed from schedule",
                        description="This block no longer exists in the plan.",
                        categories="",
                        sequence=row.sequence,
                        cancelled=True,
                    )
                )
            else:
                state[uid] = row
            continue
        if not row.starts_at:
            continue
        try:
            start = datetime.fromisoformat(row.starts_at)
        except ValueError:
            continue
        events.append(
            Event(
                uid=uid,
                start=start,
                end=start,
                summary="Removed from schedule",
                description="This block no longer exists in the plan.",
                categories="",
                sequence=row.sequence + 1,
                cancelled=True,
            )
        )
        state[uid] = IcsRow(
            uid=uid,
            # Keep the last known start: a cancelled VEVENT still needs a DTSTART
            # to be valid, and it is what lets the cancellation be re-sent.
            starts_at=row.starts_at,
            sequence=row.sequence + 1,
            cancelled=True,
            updated_at=stamp.isoformat(),
        )

    return events, state


def render(
    events: Iterable[Event], *, user: str, now: datetime, calendar_name: str = "schedulebot"
) -> str:
    """The whole calendar, CRLF-terminated, folded, in a fixed order."""
    stamp = now.astimezone(UTC)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
        f"X-WR-CALDESC:{_escape(f'Schedule for {user}')}",
    ]
    # Sorted so the output is stable: a feed that reshuffles looks changed to the
    # client even when nothing moved.
    for event in sorted(events, key=lambda item: (item.start, item.uid)):
        lines.extend(_event_lines(event, stamp))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def compose(
    plan: Plan | None,
    *,
    user: str,
    tasks: Iterable[Task],
    categories: Mapping[str, Category],
    previous: Mapping[str, IcsRow],
    now: datetime,
    now_slot: int = 0,
) -> tuple[str, dict[str, IcsRow]]:
    """Plan → (calendar text, state to persist). Handles "no plan yet" as empty."""
    if plan is None:
        events: list[Event] = []
        state: dict[str, IcsRow] = {}
    else:
        events, state = plan_events(
            plan,
            tasks=tasks,
            categories=categories,
            previous=previous,
            now_slot=now_slot,
            now=now,
        )
    return render(events, user=user, now=now), state
