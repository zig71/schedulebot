"""The open handle to the database file, and the rule that goes with it.

**One process writes.** SQLite allows many readers and exactly one writer;
splitting writes between services produces `database is locked` under load, and
— worse — two components that each believe they know the current plan.

This module makes that literal: a single `Store` per process, a re-entrant lock
so threads inside that process serialise rather than collide, and every write in
an explicit `BEGIN IMMEDIATE` transaction so a partial write cannot be observed.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 1


class Store:
    """A single, locked connection to one SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    # -- lifecycle -------------------------------------------------------

    def _migrate(self) -> None:
        with self._lock:
            version: int = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version >= SCHEMA_VERSION:
                return
            self._conn.executescript(SCHEMA_PATH.read_text())
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- access ----------------------------------------------------------

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """An exclusive transaction. Nested use joins the outer one."""
        with self._lock:
            if self._conn.in_transaction:
                yield self._conn
                return
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def read(self) -> sqlite3.Connection:
        """A connection for queries. Callers must not write through it."""
        return self._conn

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Cursor:
        with self.write() as conn:
            return conn.execute(sql, params)

    def query(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def one(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None
