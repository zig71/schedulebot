"""Calendar feed tokens and event state.

The token is a bearer credential — anyone with the URL reads the user's schedule —
so it is stored hashed, can be revoked without touching the account, and replaced
on demand. One active token per user keeps "revoke" meaningful.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from ..ics import IcsRow
from .db import Store

TOKEN_TTL_DAYS = 365


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(store: Store, user_id: str) -> str:
    """Mint a feed token, retiring any previous one for that user."""
    token = secrets.token_urlsafe(24)
    now = datetime.now(UTC)
    with store.write() as conn:
        conn.execute(
            "UPDATE ics_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now.isoformat(), user_id),
        )
        conn.execute(
            "INSERT INTO ics_tokens (token_hash, user_id, created_at) VALUES (?, ?, ?)",
            (_hash(token), user_id, now.isoformat()),
        )
    return token


def revoke_tokens(store: Store, user_id: str) -> int:
    with store.write() as conn:
        cursor = conn.execute(
            "UPDATE ics_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (datetime.now(UTC).isoformat(), user_id),
        )
    return cursor.rowcount


def user_id_for_token(store: Store, token: str) -> str | None:
    if not token:
        return None
    row = store.one(
        "SELECT t.user_id, u.disabled FROM ics_tokens t JOIN users u ON u.id = t.user_id"
        " WHERE t.token_hash = ? AND t.revoked_at IS NULL",
        (_hash(token),),
    )
    if row is None or row["disabled"]:
        return None
    return str(row["user_id"])


def active_token_created_at(store: Store, user_id: str) -> datetime | None:
    row = store.one(
        "SELECT created_at FROM ics_tokens WHERE user_id = ? AND revoked_at IS NULL"
        " ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    )
    return datetime.fromisoformat(str(row["created_at"])) if row else None


# ------------------------------------------------------------------- state


def load_state(store: Store, user_id: str) -> dict[str, IcsRow]:
    """What the client was last told, so sequences can be bumped rather than reset."""
    rows = store.query(
        "SELECT uid, sequence, starts_at, cancelled, updated_at FROM ics_state"
        " WHERE user_id = ?",
        (user_id,),
    )
    return {
        str(row["uid"]): IcsRow(
            uid=str(row["uid"]),
            sequence=int(row["sequence"]),
            starts_at=str(row["starts_at"]),
            cancelled=bool(row["cancelled"]),
            updated_at=str(row["updated_at"]),
        )
        for row in rows
    }


def save_state(store: Store, user_id: str, state: dict[str, IcsRow], *, now: datetime) -> None:
    expire_before = (now - timedelta(days=30)).astimezone(UTC).isoformat()
    stamp = now.astimezone(UTC).isoformat()
    with store.write() as conn:
        for row in state.values():
            conn.execute(
                "INSERT INTO ics_state (user_id, uid, sequence, starts_at, cancelled, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id, uid) DO UPDATE SET sequence=excluded.sequence,"
                " starts_at=excluded.starts_at, cancelled=excluded.cancelled,"
                # Keep the original stamp while nothing changed, so the
                # cancellation grace window can actually expire.
                " updated_at=CASE WHEN excluded.sequence = ics_state.sequence"
                " AND excluded.starts_at = ics_state.starts_at"
                " AND excluded.cancelled = ics_state.cancelled"
                " THEN ics_state.updated_at ELSE excluded.updated_at END",
                (user_id, row.uid, row.sequence, row.starts_at, int(row.cancelled), stamp),
            )
        # Cancellations are only re-sent for a while; then forget them.
        conn.execute(
            "DELETE FROM ics_state WHERE user_id = ? AND cancelled = 1 AND updated_at < ?",
            (user_id, expire_before),
        )
