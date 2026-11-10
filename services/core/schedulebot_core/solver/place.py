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


def chunk_sizes(
    required: int, min_chunk: int, max_chunk: int, splittable: bool, limit: int
) -> list[int]:
    """Split demand into sessions, largest first.

    `splittable=False` returns the demand whole: `max_chunk` is then not a
    ceiling, and a demand that does not fit should be reported as unfitted
    rather than diced into sessions the user said they did not want.
    """
    if not splittable:
        return [required]
    sizes: list[int] = []
    remaining = required
    while remaining >= min_chunk and len(sizes) < limit:
        take = min(max_chunk, remaining)
        # Never leave a stub too small to stand alone.
        if 0 < remaining - take < min_chunk and remaining >= 2 * min_chunk:
            take = remaining - min_chunk
        sizes.append(take)
        remaining -= take
    return sizes


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

    def reserve(
        self, req: Requirement, previous: dict[tuple[str, int, int], Block]
    ) -> list[Block]:
        """Hold whatever the previous plan already had, where it still fits."""
        sizes = chunk_sizes(
            req.required_slots,
            req.min_chunk,
            req.max_chunk,
            req.task.splittable,
            req.max_chunks,
        )
        kept: list[Block] = []
        for chunk, size in enumerate(sizes):
            prior = previous.get((req.task.id, req.occurrence, chunk))
            if prior is None:
                continue
            start = prior.start_slot
            running = start <= self.now_slot < prior.end_slot
            if running:
                # A session already underway is a fact, not a plan: never let a
                # changed estimate grow it past what it already occupies.
                size = min(size, prior.end_slot - prior.start_slot)
            span = next((s for s in req.spans if s.lo <= start and start + size <= s.hi), None)
            if span is None and running:
                # Today's span can disappear entirely when too little of it is
                # left to hold a chunk. The block is still happening.
                span = next((s for s in req.spans if s.day == day_of(start)), None) or Span(
                    lo=start, hi=start + size, day=day_of(start), window=None
                )
            if span is None:
                continue
            if self._at_cap(req, span.day):
                continue
            if not self.occupancy.is_free(start, size):
                continue
            self.occupancy.take(start, size)
            self._count(req, span.day)
            kept.append(
                Block(
                    task_id=req.task.id,
                    occurrence=req.occurrence,
                    chunk=chunk,
                    start_slot=start,
                    end_slot=start + size,
                    priority=req.task.priority,
                    moved_from=prior.start_slot,
                    reason=Reason(window=(span.lo, span.hi), kept_previous=True),
                )
            )
        return kept

    def fill(
        self,
        req: Requirement,
        kept: list[Block],
        after_slot: int = 0,
        after_task: str | None = None,
    ) -> tuple[list[Block], int]:
        """Place the demand pass one did not hold. Returns blocks and placed slots."""
        sizes = chunk_sizes(
            req.required_slots,
            req.min_chunk,
            req.max_chunk,
            req.task.splittable,
            req.max_chunks,
        )
        held = {block.chunk for block in kept}
        placed = sum(block.slots for block in kept)
        blocks: list[Block] = []

        for chunk, size in enumerate(sizes):
            if chunk in held:
                continue
            rejected: list[tuple[int, int, str]] = []
            for span in ordered_spans(req.spans, req.task.energy):
                if self._at_cap(req, span.day):
                    rejected.append((span.lo, span.hi, "daily cap reached"))
                    continue
                lo = max(span.lo, after_slot)
                if lo + size > span.hi:
                    rejected.append(
                        (
                            span.lo,
                            span.hi,
                            f"starts after {after_task}" if after_task else "too short",
                        )
                    )
                    continue
                start = self.occupancy.first_fit(lo, span.hi, size)
                if start is None:
                    rejected.append((span.lo, span.hi, f"no free {size * 15}-minute gap"))
                    continue
                self.occupancy.take(start, size)
                self._count(req, span.day)
                placed += size
                blocks.append(
                    Block(
                        task_id=req.task.id,
                        occurrence=req.occurrence,
                        chunk=chunk,
                        start_slot=start,
                        end_slot=start + size,
                        priority=req.task.priority,
                        reason=Reason(
                            window=(span.lo, span.hi),
                            after_dependency=after_task if after_slot else None,
                            rejected=tuple(rejected),
                        ),
                    )
                )
                break
        return blocks, placed



def slots_for_minutes(minutes: int) -> int:
    return slots_for(minutes)
