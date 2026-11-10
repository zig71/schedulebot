-- The whole schema, applied idempotently at startup.
--
-- Tenets: users are created by an operator (there is no registration endpoint),
-- every row belongs to exactly one user, and the plan is a snapshot rather than
-- a source of truth — tasks and routines are the truth, a plan is what we made
-- of them at a moment in time.

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    timezone      TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);

-- A category is the user's own, seeded from the presets on first login. Stored
-- per user because "deep work is my mornings" is a personal claim.
CREATE TABLE IF NOT EXISTS categories (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    id          TEXT NOT NULL,
    name        TEXT NOT NULL,
    colour      TEXT NOT NULL,
    energy      TEXT,
    windows     TEXT NOT NULL DEFAULT '[]',
    blocking    TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, id)
);

CREATE TABLE IF NOT EXISTS tasks (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    id          TEXT NOT NULL,
    title       TEXT NOT NULL,
    minutes     INTEGER NOT NULL,
    remaining   INTEGER,
    min_chunk   INTEGER NOT NULL,
    max_chunk   INTEGER NOT NULL,
    splittable  INTEGER NOT NULL DEFAULT 1,
    priority    INTEGER NOT NULL DEFAULT 3,
    energy      TEXT,
    kind        TEXT NOT NULL DEFAULT 'task',
    category    TEXT NOT NULL DEFAULT 'uncategorised',
    period_days INTEGER,
    windows     TEXT NOT NULL DEFAULT '[]',
    not_before  TEXT,
    due         TEXT,
    depends_on  TEXT NOT NULL DEFAULT '[]',
    max_per_day INTEGER,
    context     TEXT NOT NULL DEFAULT '{}',
    reasoning   TEXT NOT NULL DEFAULT '',
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, id)
);

CREATE TABLE IF NOT EXISTS routines (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    id         TEXT NOT NULL,
    name       TEXT NOT NULL,
    weekdays   TEXT NOT NULL,
    start_min  INTEGER NOT NULL,
    end_min    INTEGER NOT NULL,
    starts_on  TEXT NOT NULL,
    ends_on    TEXT,
    exdates    TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, id)
);

-- One plan per user: a snapshot of a solve, kept so the next solve can be stable
-- and so the UI can answer "what am I doing now" without re-solving.
CREATE TABLE IF NOT EXISTS plans (
    user_id       TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    origin        TEXT NOT NULL,
    horizon_slots INTEGER NOT NULL,
    solved_at     TEXT NOT NULL,
    solve_ms      REAL NOT NULL DEFAULT 0,
    warnings      TEXT NOT NULL DEFAULT '[]',
    payload       TEXT NOT NULL
);

-- Read-only calendar feeds. The token is the credential, so it is stored hashed
-- and can be revoked without touching the account.
-- What we last told a calendar client, per event. Without this the feed cannot
-- bump SEQUENCE on a move or cancel a block that disappeared.
CREATE TABLE IF NOT EXISTS ics_state (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    uid        TEXT NOT NULL,
    sequence   INTEGER NOT NULL DEFAULT 0,
    starts_at  TEXT NOT NULL DEFAULT '',
    cancelled  INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, uid)
);

CREATE TABLE IF NOT EXISTS ics_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
