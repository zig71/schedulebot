# schedulebot

A self-hosted scheduler that decides what you should be doing, tells you, and
keeps telling you until you do it.

Not a to-do list with a calendar view. The point is the loop: **plan → prompt →
acknowledge → record what actually happened → re-plan the remainder.**

## Status

| Piece | State |
|---|---|
| Scheduling engine (`services/core`) | **Built and tested** — 21 tests, pure, no I/O |
| API + auth + users | Not started |
| Calendar sync (ICS/CalDAV/Google) | Not started |
| Notifier + desktop companion | Prototype only (`/prototype` on the old rig) |
| Cold Turkey adapter | Designed, not built |
| Tauri client | Not started |

The engine came first because everything else is plumbing around it, and because
a schedule you cannot trust is worse than no schedule.

## Layout

```
services/
  core/                 pure scheduling engine — no I/O, no clock, no DB
    schedulebot_core/
      slots.py          integer time, and the two different "now"s
      domain.py         Task, Routine, Block, Plan, Reason
      solver/           expand → reserve → fill → report
      explain.py        why a block is where it is
    tests/
docs/
  ARCHITECTURE.md       decisions, boundaries, and what is deliberately absent
```

## Run the engine

```bash
cd services/core
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
TZ=Europe/London .venv/bin/python -m pytest
```

## Design in one paragraph

Time is integer slots from one origin, so nothing is ever read back against a
different midnight. Placement is two passes: reserve every prior placement that
is still legal, then fill the rest most-important-first, deferring anything
whose dependency has not been laid out. Fixed routines are immovable. Nothing is
placed in the past. Demand that does not fit is reported; demand that finishes
after its deadline is reported. Every block carries the reason it was placed
there, recorded at placement time rather than reconstructed afterwards.

## Licence

MIT. Say the word if you would rather have AGPL-3.0 — it is a one-line change
and nothing here depends on the choice yet.
