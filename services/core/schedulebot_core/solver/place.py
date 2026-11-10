"""Placement: two passes, and the reasons why.

Pass one reserves every previous placement that is still legal, before anything
new is allocated. Pass two fills the remaining demand, most important first.

Pass one is not optional. With a single pass, a block displaced by a new meeting
reaches the allocator *before* an untouched lower-priority block does, takes the
slot that block was quietly still holding, and evicts it; that eviction
displaces the next one, and the damage cascades across the week. Reserving first
bounds the churn to the blocks a change actually hit, which is the one property
that makes a schedule worth trusting.

Every placement records a `Reason` as it is made. Reconstructing an explanation
afterwards from a finished plan is guesswork; the placer already knows which
window it chose and what it rejected.
"""

from __future__ import annotations

import contextlib
from bisect import insort
from collections.abc import Sequence

from ..domain import Block, Energy, Reason
from ..slots import day_of, slots_for
from .expand import Requirement, Span


class Occupancy:
    """Sorted disjoint spans with earliest-fit, take and release."""

    def __init__(self, spans: Sequence[tuple[int, int]] = ()) -> None:
        self._spans: list[tuple[int, int]] = sorted(spans)

    def first_fit(self, lo: int, hi: int, size: int) -> int | None:
        """Earliest start in [lo, hi) where `size` slots are free."""
        if hi - lo < size:
            return None
        cursor = lo
        for start, end in self._spans:
            if end <= cursor:
                continue
            if start >= cursor + size:
                break
            cursor = max(cursor, end)
            if cursor + size > hi:
                return None
        return cursor if cursor + size <= hi else None

    def is_free(self, start: int, size: int) -> bool:
        return not any(start < end and begin < start + size for begin, end in self._spans)

    def take(self, start: int, size: int) -> None:
        insort(self._spans, (start, start + size))

    def release(self, start: int, size: int) -> None:
        with contextlib.suppress(ValueError):  # releasing something not held
            self._spans.remove((start, start + size))

    @property
    def spans(self) -> tuple[tuple[int, int], ...]:
        return tuple(self._spans)


def ordered_spans(spans: Sequence[Span], energy: Energy | None) -> list[Span]:
    """Usable spans in preference order.

    Energy chooses *between windows on the same day* rather than only breaking
    ties: a day split into 09:00-12:00 and 18:00-21:00 gives high-energy work the
    morning and low-energy work the evening. Days still run in chronological
    order — sorting by "latest start" overall would push low-energy work to the
    far end of the horizon, which is not what anyone means by an evening task.
    """
    if energy is Energy.LOW:
        return sorted(spans, key=lambda span: (span.day, -span.lo))
    return sorted(spans, key=lambda span: (span.day, span.lo))


class Placer:
    """Stateful allocator. One instance per solve."""

    def __init__(self, occupancy: Occupancy, now_slot: int) -> None:
        self.occupancy = occupancy
        self.now_slot = now_slot
        self._per_day: dict[tuple[str, int], dict[int, int]] = {}

    def _counts(self, req: Requirement) -> dict[int, int]:
        return self._per_day.setdefault(req.key_prefix, {})

    def _at_cap(self, req: Requirement, day: int) -> bool:
        if req.task.max_per_day is None:
            return False
        return self._counts(req).get(day, 0) >= req.task.max_per_day

    def _count(self, req: Requirement, day: int) -> None:
        counts = self._counts(req)
        counts[day] = counts.get(day, 0) + 1

    # -- pass one --------------------------------------------------------

    def reserve(
        self, req: Requirement, previous: dict[tuple[str, int, int], Block]
    ) -> list[Block]:
        """Hold whatever the previous plan already had, where it still fits.

        Iterates the previous blocks themselves rather than re-deriving how many
        sessions there should be: what was on the calendar is the fact worth
        preserving, and re-deriving it from today's estimate is how a block
        silently changes size overnight.
        """
        priors = sorted(
            (
                block
                for (task_id, occurrence, _chunk), block in previous.items()
                if task_id == req.task.id and occurrence == req.occurrence
            ),
            key=lambda block: (block.start_slot, block.chunk),
        )
        kept: list[Block] = []
        for prior in priors:
            size = prior.slots
            running = prior.start_slot <= self.now_slot < prior.end_slot
            end = prior.end_slot
            span = next(
                (s for s in req.spans if s.lo <= prior.start_slot and end <= s.hi),
                None,
            )
            if span is None and running:
                # Today's span can disappear entirely when too little of it is
                # left to hold a session. The block is still happening.
                span = next(
                    (s for s in req.spans if s.day == day_of(prior.start_slot)), None
                ) or Span(
                    lo=prior.start_slot,
                    hi=end,
                    day=day_of(prior.start_slot),
                    window=None,
                )
            if span is None:
                continue
            if self._at_cap(req, span.day):
                continue
            if not self.occupancy.is_free(prior.start_slot, size):
                continue
            self.occupancy.take(prior.start_slot, size)
            self._count(req, span.day)
            kept.append(
                Block(
                    task_id=req.task.id,
                    occurrence=req.occurrence,
                    chunk=prior.chunk,
                    start_slot=prior.start_slot,
                    end_slot=prior.start_slot + size,
                    priority=req.task.priority,
                    moved_from=prior.start_slot,
                    reason=Reason(window=(span.lo, span.hi), kept_previous=True),
                )
            )
        return kept

    # -- pass two --------------------------------------------------------

    def fill(
        self,
        req: Requirement,
        kept: list[Block],
        after_slot: int = 0,
        after_task: str | None = None,
    ) -> tuple[list[Block], int]:
        """Place the demand pass one did not hold. Returns blocks and placed slots.

        Sessions are sized to what actually fits, not to `max_chunk` regardless of
        the window. A two-hour task inside a one-hour daily window is two
        one-hour sessions on two days; sizing every session from the ceiling
        alone produced nothing at all for such a task, and the calendar feed then
        reported every event as cancelled.
        """
        placed = sum(block.slots for block in kept)
        remaining = req.required_slots - placed
        blocks: list[Block] = []
        next_chunk = max((block.chunk for block in kept), default=-1) + 1
        if remaining <= 0:
            return blocks, placed

        if not req.task.splittable:
            # One session or none. `max_chunk` is not a ceiling for this task, and
            # dicing work the user said must not be interrupted is worse than
            # reporting plainly that it did not fit.
            rejected: list[tuple[int, int, str]] = []
            for span in ordered_spans(req.spans, req.task.energy):
                if self._at_cap(req, span.day):
                    rejected.append((span.lo, span.hi, "daily cap reached"))
                    continue
                lo = max(span.lo, after_slot)
                if lo + remaining > span.hi:
                    rejected.append(
                        (
                            span.lo,
                            span.hi,
                            f"starts after {after_task}" if after_task else "too short",
                        )
                    )
                    continue
                start = self.occupancy.first_fit(lo, span.hi, remaining)
                if start is None:
                    rejected.append(
                        (span.lo, span.hi, f"no free {remaining * 15}-minute gap")
                    )
                    continue
                self.occupancy.take(start, remaining)
                self._count(req, span.day)
                blocks.append(
                    Block(
                        task_id=req.task.id,
                        occurrence=req.occurrence,
                        chunk=next_chunk,
                        start_slot=start,
                        end_slot=start + remaining,
                        priority=req.task.priority,
                        reason=Reason(
                            window=(span.lo, span.hi),
                            after_dependency=after_task if after_slot else None,
                            rejected=tuple(rejected),
                        ),
                    )
                )
                placed += remaining
                break
            return blocks, placed

        for span in ordered_spans(req.spans, req.task.energy):
            if remaining < req.min_chunk or len(blocks) >= req.max_chunks:
                break
            if self._at_cap(req, span.day):
                continue
            lo = max(span.lo, after_slot)
            room = span.hi - lo
            if room < req.min_chunk:
                continue

            size = min(req.max_chunk, room, remaining)
            # Never leave a stub too small to stand alone as its own session.
            if (
                0 < remaining - size < req.min_chunk
                and remaining - req.min_chunk >= req.min_chunk
            ):
                size = remaining - req.min_chunk

            rejected_here: list[tuple[int, int, str]] = []
            start = self.occupancy.first_fit(lo, span.hi, size)
            while start is None and size > req.min_chunk:
                # The window has room but it is fragmented: shrink to the largest
                # session that fits rather than abandoning the window entirely.
                size -= 1
                start = self.occupancy.first_fit(lo, span.hi, size)
            if start is None:
                rejected_here.append((span.lo, span.hi, f"no free {size * 15}-minute gap"))
                continue

            self.occupancy.take(start, size)
            self._count(req, span.day)
            remaining -= size
            placed += size
            blocks.append(
                Block(
                    task_id=req.task.id,
                    occurrence=req.occurrence,
                    chunk=next_chunk,
                    start_slot=start,
                    end_slot=start + size,
                    priority=req.task.priority,
                    reason=Reason(
                        window=(span.lo, span.hi),
                        after_dependency=after_task if after_slot else None,
                        rejected=tuple(rejected_here),
                    ),
                )
            )
            next_chunk += 1

        return blocks, placed


def slots_for_minutes(minutes: int) -> int:
    return slots_for(minutes)
