# Architecture

Decisions, with the reason each one is not something else.

## Shape

```
                    ┌──────────────────────────────┐
   browser ────────▶│  core  (the ONLY DB writer)  │
   Tauri app ──────▶│  auth · users · tasks · plan │
   .ics clients ───▶│  sessions · explanations     │
                    └───────┬───────────────┬──────┘
                            │ HTTP          │ HTTP
                   ┌────────▼──────┐  ┌─────▼─────────┐
                   │ sync          │  │ notifier      │
                   │ ICS/CalDAV/   │  │ reminders,    │
                   │ Google/Outlook│  │ escalation    │
                   └───────────────┘  └───────────────┘
```

**One process owns the SQLite file and does every write.** Services are separate
processes, but only `core` touches the database. SQLite allows many readers and
exactly one writer; splitting writes across services produces `database is
locked` under load, and a second writer is how two components start disagreeing
about what the schedule is.

`sync` and `notifier` are cheap HTTP clients of `core`. They exist because they
have different failure modes — a dead calendar provider or a closed laptop must
not take scheduling down with them.

## Decisions

| Area | Decision | Why |
|---|---|---|
| Engine | Our own, in `services/core`, dependency-free | The engine is the product. A pure module with 21 tests is easier to trust and to replace than an inherited one |
| Time | Integer slots from **one origin per plan** | Day-relative data read on another day is how a Friday deadline becomes Saturday |
| "Now" | A **floor on spans**, never an anchor for periods | Conflating them deletes recurring demand instead of delaying it |
| Placement | Two passes: reserve, then fill | A single pass cascades: one meeting can move the whole week |
| Dependencies | Constrain, and beat priority | A dependency is explicit user intent; priority is a preference |
| Energy | Chooses **between windows on a day** | Otherwise the field does nothing but break ties |
| Explanations | Recorded at placement, rendered deterministically | Reconstructing a reason from a finished plan is guesswork |
| Users | Many users, **no registration endpoint** | Public service, accounts created by the operator |
| Timezone | **Per user**, stored and sent with each request | The server's zone is not the user's zone, and the desktop app travels |
| Secrets | Per-user LLM keys, encrypted at rest | One operator's key must not become everyone's |
| LLM | Groq (OpenAI-compatible), `qwen/qwen3.8-27b`, schema-constrained | The model fills a form. It never picks a time and cannot write to a calendar |
| Categories | Seven presets, overridable per user, id-stable | A new account must be useful on day one; a taxonomy nobody maintains is worse than none |
| Category influence | Supplies defaults only; explicit task settings win | "Deep work is mornings" is a default, not a rule the user cannot override |
| Context | Structured fields (people, place, requires, tags) | Free text cannot answer "can this happen here, with them, today" |
| Reasoning | Two records: model interpretation, solver placement | Merging them lets a guess masquerade as a justification |
| `.ics` | Read-only feed behind a **revocable per-user token** | Calendar clients cannot log in; a feed URL is a bearer secret |
| Cold Turkey | Adapter with a **channel probe**, never locks | See below |
| Blocking scope | **Per category**, a tuple of block names | "No social" and "No video" belong to different categories, and one category can need several |
| Transport failures | Surfaced with status and body, never retried silently | A 401 and a 400 "unsupported schema" need different fixes |
| Desktop | Tauri, one codebase; Windows is the full-featured target | CT is Windows-only, so "shared codebase" is true for the app, not the features |
| Frontend | React + TypeScript on Vite, no meta-framework | The week grid needs drag, series-edit and optimistic mutation; a Tauri bundle expects a compiled SPA. Next.js is rejected: there is no server to render on |

## Cold Turkey

Two channels exist, and neither is unconditionally correct:

1. **CLI** — documented and supported (`-start "Name"`, `-stop "Name"`), but the
   vendor's own guide requires the Blocker **window to be closed** before it can
   be used, and it returns nothing machine-readable.
2. **Database** — `data-app.db` (SQLite) holds a `settings` row whose JSON has a
   `blocks` map keyed by name, each with `"enabled": "true"|"false"`. Toggling is
   a field flip and works whether or not the GUI is open — but the schema is
   undocumented and has changed between versions, and whether the running app
   re-reads the file or overwrites it from memory is unknown.

So the adapter **probes at startup**: read the DB read-only to enumerate block
names, attempt a toggle, read it back, and observe whether the app state agrees.
Whichever channel passes becomes the one it uses on that machine, version and
config; the other stays as fallback. Nothing assumes.

Hard rules, regardless of channel:

- never create a **locked** block, and never pass `-lock` or `-password`;
- only names from the user's own allowlist, never text from a model;
- one active profile at a time, persisted before activation so a crash cannot
  leave an unknown state;
- a failure to block is reported and the session continues unblocked.

## The service

```
POST /api/login          session cookie (argon2id, HttpOnly, SameSite=Lax)
POST /api/logout         revoke + clear
GET  /api/me             who am I, my timezone, my categories
PUT  /api/categories/{id} rename, recolour, re-window, map Cold Turkey blocks
GET  /api/tasks          my tasks
PUT  /api/tasks/{id}     create or replace one task
POST /api/tasks/{id}/progress   record what is actually left
POST /api/capture        note -> tasks -> plan
POST /api/plan/solve     re-solve now
GET  /api/plan           the stored plan
GET  /api/now            what should I be doing, and why
GET  /healthz            liveness, unauthenticated
```

**No registration route exists.** The operator CLI is the only way an account
comes into being; a test asserts the obvious paths 404.

**Every route is scoped to the session's user.** The user id comes from the
cookie, never the body, so `PUT /api/tasks/{id}` cannot be pointed at somebody
else's task — also tested.

**Login is throttled** per (username, address) so one attacker cannot lock a real
user out by hammering their account from elsewhere.

**A plan is a snapshot, not a source of truth.** Tasks and routines are the
truth; `/api/plan` returns what the last solve produced, and `/api/now` reads that
snapshot rather than re-solving on every poll — otherwise the answer changes while
the user is reading it.

## The capture boundary

```
note ──▶ capture prompt ──▶ JSON (strict schema) ──▶ parse_capture ──▶ Task/Routine
                                                            │
                          warnings ◀────────────────────────┘
```

`parse_capture` never raises for a single bad item: an unknown category becomes
`uncategorised` with a warning, a dangling dependency is dropped with a warning,
and a malformed task is skipped. One hallucinated field must not cost the user
the other nine tasks they wrote down.

Two things the model may not do, enforced in the schema and the prompt together:
choose a clock time, or claim to know where something was placed. It expresses
*duration, preference, deadline, repetition and category*; the solver turns that
into slots from real availability, and `explain.py` describes the decision it
actually made.

Fixed weekly commitments come back as `Routine` (immovable); quota-style
repetition ("gym twice a week") comes back as a `HABIT` task with a period, which
the solver places. That distinction is the difference between a lecture that must
never move and a workout that may.

## The calendar feed

```
GET /ics/{token}.ics     the feed (token is the credential, no cookie)
POST /api/ics/token      mint, retiring any previous token
DELETE /api/ics/token    revoke
```

Three rules, each of which exists because breaking it damages the user's real
calendar:

1. **One UID per block, forever** — derived by a single shared function from
   (task, occurrence, chunk). Re-solving updates the event in place instead of
   creating a second one.
2. **SEQUENCE tracks the absolute start.** A moved block increments it. Comparing
   day-relative slots instead is how midnight becomes a silent relocation.
3. **A removed block is cancelled, and the cancellation is re-sent for a grace
   window with the same SEQUENCE.** Sent exactly once, a client that was offline
   during that one poll keeps the ghost event forever; bumping on every re-send
   makes every poll look like a new change.

Text is escaped and folded at 75 octets, instants are UTC, and the output is
sorted so the same plan always produces the same bytes — a feed that reshuffles
looks changed to the client even when nothing moved.

## Serving the app

`core` mounts the built SPA at `/`, last, so it cannot shadow an API route, and
only if the build exists — a headless deployment stays API-only, which is what the
tests run against. One process, one port, no separate web server to keep alive.

The client holds no scheduling logic. It renders `/api/now`, `/api/plan` and
`/api/capture` exactly as returned, including the warnings: the explanation on the
Now screen is the solver's recorded reason, not something the UI phrased.

## Session sizing

Sessions are sized to what actually fits, not to `max_chunk` regardless of the
window, and the window is allowed to shrink a session down to `min_chunk` to use a
fragmented gap. Before this, a two-hour task inside a one-hour daily window —
exactly what "don't schedule demanding work after 8pm" produces — placed nothing
at all, and the feed then reported every event as cancelled. Pass one also holds
prior blocks by iterating them directly rather than re-deriving how many there
should be from today's estimate.

## Deliberately absent

- **No CP-SAT.** The scheduling problem here is small and the constraint that
  matters is stability, not optimality. A greedy pass with reservation is fast,
  explainable, and its output does not change shape between runs.
- **No registration.** Accounts are created by the operator.
- **No microservice per table.** Three processes with clear jobs beat eight with
  none.
- **No model in the placement path.** Ever.
- **No retry that hides a failure.** A model response outside the contract is an
  error with the response attached, not something to repair by guessing.
