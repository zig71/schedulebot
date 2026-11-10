# schedulebot

A self-hosted scheduler that decides what you should be doing, tells you, and
keeps telling you until you do it.

Not a to-do list with a calendar view. The point is the loop: **plan → prompt →
acknowledge → record what actually happened → re-plan the remainder.**

## Status

| Piece | State |
|---|---|
| Scheduling engine (`services/core`) | **Built and tested** — pure, no I/O |
| Categories + context + LLM contract | **Built and tested** — pure parsing, no network |
| LLM transport (Groq) | **Built, exercised against the live model** |
| API + auth + users (SQLite, one writer) | **Built and tested** — 90 tests |
| `.ics` read-only feed | **Built and tested** — token URL, stable UIDs, SEQUENCE, cancellations |
| CalDAV / Google two-way sync | Not started |
| Notifier + desktop companion | Prototype only (`/prototype` on the old rig) |
| Cold Turkey adapter | Designed, not built |
| Tauri client | Not started |

The engine came first because everything else is plumbing around it, and because
a schedule you cannot trust is worse than no schedule.

## Run it

There is no registration page. Accounts come from the host:

```bash
cd services/core
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"

.venv/bin/python -m schedulebot_core.cli --db data/schedulebot.db user-add yatt --timezone Europe/London
.venv/bin/python -m schedulebot_core.cli --db data/schedulebot.db serve \
    --port 8000 --llm-key "$SCHEDULEBOT_LLM_API_KEY" --llm-model qwen/qwen3.8-27b
```

Then, over HTTP:

```bash
curl -c jar -X POST localhost:8000/api/login -H 'content-type: application/json' \
     -d '{"username":"yatt","password":"..."}'

curl -b jar -X POST localhost:8000/api/capture -H 'content-type: application/json' \
     -d '{"note":"gym twice this week, an hour each. investor update before friday, 3h"}'

curl -b jar localhost:8000/api/now
```

`/api/now` is the product in one call: the block you should be in, when it ends,
why it is there, and which distraction blocks belong to it.

To subscribe from a phone, mint a feed URL and add it as a calendar:

```bash
curl -b jar -X POST localhost:8000/api/ics/token     # {"url": "/ics/<token>.ics"}
curl -b jar -X DELETE localhost:8000/api/ics/token   # revoke it
```

The token is a password: anyone with the URL can read the schedule. It is stored
hashed and can be revoked without touching the account.

## Layout

```
services/
  core/                 pure scheduling engine — no I/O, no clock, no DB
    schedulebot_core/
      slots.py          integer time, and the two different "now"s
      domain.py         Task, Routine, Block, Plan, Reason
      categories.py     seven presets + user categories, and their defaults
      solver/           expand → reserve → fill → report
      explain.py        why a block is where it is
      llm/              sentence → structured requests (schema, prompt, parser, Groq)
      store/            the single writer: schema, users, tasks, plans
      api/              HTTP surface: auth, tasks, capture, plan, now
      views.py          one description of a block, for API/notifier/client
      cli.py            operator CLI: create accounts, serve
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

Tasks carry a **category** and a **context**. A category is more than a label: it
supplies the defaults that make a task schedulable without repeating yourself —
which windows it belongs in, what energy it takes, and optionally which Cold
Turkey block goes with it. Context is structured (people, place, kit, tags)
because each field changes a decision. Explicit task settings always beat the
category's.

Two kinds of reasoning are kept apart, deliberately: the model records how it
**interpreted** your note, and the solver records why it chose the **slot**. A
model that claims to know where something was placed is lying; it never saw the
calendar.

## Licence

MIT. Say the word if you would rather have AGPL-3.0 — it is a one-line change
and nothing here depends on the choice yet.
