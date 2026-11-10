"""Integer time slots, and the two different "now"s.

Placement is integer arithmetic over one horizon; datetimes exist only at the
edges. Three rules, each of which is a bug we have already paid for:

1. **One origin per plan.** Slots are counted from midnight of the day the plan
   is *for*. Nothing is stored day-relative and read back on a different day —
   that is how a Friday deadline becomes Saturday.
2. **The floor is not the anchor.** "Do not place before now" clips the spans a
   task may use. It must never shift the boundaries of a repeating period, or a
   weekly habit quietly loses a week every time it is re-solved.
3. **Round durations up.** Placing 20 minutes as one 15-minute slot reports work
   as done that never fitted.
"""

from __future__ import annotations

from datetime import datetime, timedelta

SLOT_MINUTES = 15
SLOTS_PER_HOUR = 60 // SLOT_MINUTES
SLOTS_PER_DAY = 24 * SLOTS_PER_HOUR


def slots_for(minutes: int) -> int:
    """Whole slots needed for `minutes`, rounded **up**."""
    if minutes < 0:
        raise ValueError(f"duration must be non-negative, got {minutes}")
    return -(-minutes // SLOT_MINUTES)


def minutes_for(slots: int) -> int:
    return slots * SLOT_MINUTES


def floor_slot(moment: datetime, origin: datetime) -> int:
    """The slot containing `moment`."""
    if moment.tzinfo is None or origin.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    return int((moment - origin).total_seconds() // (SLOT_MINUTES * 60))


def ceil_slot(moment: datetime, origin: datetime) -> int:
    """The first slot that starts at or after `moment`."""
    exact = (moment - origin).total_seconds() / (SLOT_MINUTES * 60)
    return int(exact) if exact == int(exact) else int(exact) + 1


def slot_datetime(slot: int, origin: datetime) -> datetime:
    return origin + timedelta(minutes=slot * SLOT_MINUTES)


def midnight(moment: datetime) -> datetime:
    """Midnight of `moment` in its own zone — the plan origin."""
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def day_of(slot: int) -> int:
    return slot // SLOTS_PER_DAY


def day_start(day: int) -> int:
    return day * SLOTS_PER_DAY
