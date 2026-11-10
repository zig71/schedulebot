"""Accounts and sessions.

There is **no registration endpoint**, by design: this is a service you run for
yourself and people you know, and an open signup on a private scheduler is an
invitation to abuse. Accounts are created by an operator with `schedulebot user
add` on the host.

Passwords are argon2id. Session tokens are random, stored only as a SHA-256 hash,
and sent as `HttpOnly` cookies — so a database leak does not hand anyone a live
session, and a token cannot be recovered from the file.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from .db import Store

MIN_PASSWORD_LENGTH = 12
SESSION_TTL = timedelta(days=30)

#: argon2id with a deliberate cost: ~50 ms per login on a laptop, which is
#: slow enough to make guessing expensive and fast enough to be invisible.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


@dataclass(frozen=True, slots=True)
class User:
    id: str
    username: str
    timezone: str
    is_admin: bool


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(
    store: Store,
    username: str,
    password: str,
    *,
    timezone: str = "Europe/London",
    is_admin: bool = False,
) -> User:
    """Create an account. The only way a user comes into existence."""
    username = username.strip()
    if not username:
        raise ValueError("username must not be empty")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")

    user = User(
        id=uuid.uuid4().hex,
        username=username,
        timezone=timezone,
        is_admin=is_admin,
    )
    with store.write() as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, timezone, is_admin, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                user.id,
                user.username,
                _hasher.hash(password),
                user.timezone,
                int(user.is_admin),
                datetime.now(UTC).isoformat(),
            ),
        )
    return user


def verify_credentials(store: Store, username: str, password: str) -> User | None:
    """Return the user when the password is right, otherwise None.

    Never says which half was wrong: "no such user" and "wrong password" are the
    same answer, and the same amount of work happens either way.
    """
    row = store.one(
        "SELECT id, username, password_hash, timezone, is_admin, disabled FROM users"
        " WHERE username = ?",
        (username.strip(),),
    )
    stored_hash = row["password_hash"] if row else _hasher.hash("no such user")
    try:
        _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return None
    if row is None or row["disabled"]:
        return None
    return User(
        id=str(row["id"]),
        username=str(row["username"]),
        timezone=str(row["timezone"]),
        is_admin=bool(row["is_admin"]),
    )


def create_session(store: Store, user_id: str, *, ttl: timedelta = SESSION_TTL) -> str:
    """Mint a session token. The plaintext is returned once and never stored."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    with store.write() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at)"
            " VALUES (?, ?, ?, ?)",
            (_hash_token(token), user_id, now.isoformat(), (now + ttl).isoformat()),
        )
    return token


def user_for_token(store: Store, token: str) -> User | None:
    if not token:
        return None
    row = store.one(
        "SELECT u.id, u.username, u.timezone, u.is_admin FROM sessions s"
        " JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0",
        (_hash_token(token), datetime.now(UTC).isoformat()),
    )
    if row is None:
        return None
    return User(
        id=str(row["id"]),
        username=str(row["username"]),
        timezone=str(row["timezone"]),
        is_admin=bool(row["is_admin"]),
    )


def load_user(store: Store, user_id: str) -> User | None:
    row = store.one(
        "SELECT id, username, timezone, is_admin FROM users WHERE id = ? AND disabled = 0",
        (user_id,),
    )
    if row is None:
        return None
    return User(
        id=str(row["id"]),
        username=str(row["username"]),
        timezone=str(row["timezone"]),
        is_admin=bool(row["is_admin"]),
    )


def revoke_session(store: Store, token: str) -> None:
    store.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


def set_password(store: Store, user_id: str, password: str) -> None:
    """Change a password and drop every existing session for that user."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    with store.write() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (_hasher.hash(password), user_id)
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def list_users(store: Store) -> list[User]:
    return [
        User(
            id=str(row["id"]),
            username=str(row["username"]),
            timezone=str(row["timezone"]),
            is_admin=bool(row["is_admin"]),
        )
        for row in store.query(
            "SELECT id, username, timezone, is_admin FROM users ORDER BY username"
        )
    ]
