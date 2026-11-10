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
| Desktop | Tauri, one codebase; Windows is the full-featured target | CT is Windows-only, so "shared codebase" is true for the app, not the features |

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

## Deliberately absent

- **No CP-SAT.** The scheduling problem here is small and the constraint that
  matters is stability, not optimality. A greedy pass with reservation is fast,
  explainable, and its output does not change shape between runs.
- **No registration.** Accounts are created by the operator.
- **No microservice per table.** Three processes with clear jobs beat eight with
  none.
- **No model in the placement path.** Ever.
