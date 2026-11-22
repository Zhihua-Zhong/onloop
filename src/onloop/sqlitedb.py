"""The SQLite plumbing of the overlay ledger (`overlay/ledger.py`): the connection, the schema migrations, the clock and
write transactions.

A store is a subclass (the ledger; the tests' frozen copy of the legacy `onloop.db`). Each names its migrations, how a
migration script splits into statements, its error class and the words of its refusals; the behaviour is the same:

- a file is opened in WAL mode (`synchronous=NORMAL`, so readers never block the writer and a commit does not wait
  for a full sync) with a busy timeout of `BUSY_TIMEOUT_S`, and migrated forward to `len(migrations())` (index + 1 is
  the version, kept in `PRAGMA user_version`), each migration in its own transaction;
- a database from a newer onloop is refused untouched;
- a read-only open (`mode=ro`) never migrates, and refuses a database at another version;
- `now()` is SQLite's own clock in the one stored format unless a test passes `clock`;
- `tx()` is one `BEGIN IMMEDIATE` transaction, re-entrant: an inner `tx` joins the outer one. When another process
  holds the write lock past the busy timeout, it is tried again `LOCK_RETRIES` times after a short backoff, then
  raises `Locked` (an `sqlite3.OperationalError`): that one operation fails, cleanly, rolled back, and the caller
  goes on. `LOCKS` counts, for the process, the attempts retried, the operations failed, and the longest write
  transaction, for onloopd's `/health`.

The longest a write can wait is `LOCK_RETRIES + 1` busy timeouts plus the backoffs: 3 × 15 + 0.5 + 1 = 46.5 s. That
stays under the heartbeat's 60 s staleness less one 10 s tick, so a blocked tick never reads as a dead onloopd; the
dead-man switch is a schedule hours apart.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any, ClassVar

from . import events as ev

NOW_SQL = "SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')"
BUSY_TIMEOUT_S = 15.0  # one attempt's wait for another process's write lock
LOCK_RETRIES = 2  # attempts after the first
LOCK_BACKOFF_S = (0.5, 1.0)  # the pause before each retry


class Locked(sqlite3.OperationalError):
    """Another process held the write lock through every attempt: this one write is not made."""


_locks_lock = threading.Lock()
LOCKS: dict[str, float] = {"retried": 0, "failed": 0, "longest_tx_s": 0.0}


def locks() -> dict[str, float]:
    with _locks_lock:
        return dict(LOCKS)


def _count(key: str, value: float = 1) -> None:
    with _locks_lock:
        if key == "longest_tx_s":
            LOCKS[key] = max(LOCKS[key], round(value, 3))
        else:
            LOCKS[key] += value


def _is_lock(e: sqlite3.OperationalError) -> bool:
    msg = str(e).lower()
    return "locked" in msg or "busy" in msg


class SQLiteDB:
    BUSY_TIMEOUT_S: ClassVar[float] = BUSY_TIMEOUT_S
    Error: ClassVar[type[Exception]] = RuntimeError
    FOREIGN_KEYS: ClassVar[bool] = False
    CHECK_SAME_THREAD: ClassVar[bool] = True
    # The words of the refusals, with {path}, {have} and {want}.
    MISSING: ClassVar[str] = "{path} does not exist"
    STALE: ClassVar[str] = "{path} is schema version {have}, this onloop expects {want}"
    NEWER: ClassVar[str] = "{path} is schema version {have}, newer than this onloop ({want})"
    READ_ONLY: ClassVar[str] = "this database was opened read-only"

    def migrations(self) -> list[str]:
        """The schema scripts, oldest first; index + 1 is the version."""
        raise NotImplementedError

    @staticmethod
    def statements(script: str) -> list[str]:
        raise NotImplementedError

    def __init__(
        self, path: str | os.PathLike, *, clock: Callable[[], str] | None = None, readonly: bool = False
    ):
        self.path = pathlib.Path(path)
        self.readonly = readonly
        self._clock = clock
        self._depth = 0
        if readonly:
            if not self.path.exists():
                raise self.Error(self.MISSING.format(path=self.path))
            uri = self.path.resolve().as_uri() + "?mode=ro"
            self.conn = sqlite3.connect(
                uri,
                uri=True,
                isolation_level=None,
                timeout=self.BUSY_TIMEOUT_S,
                check_same_thread=self.CHECK_SAME_THREAD,
            )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(
                str(self.path),
                isolation_level=None,
                timeout=self.BUSY_TIMEOUT_S,
                check_same_thread=self.CHECK_SAME_THREAD,
            )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(f"PRAGMA busy_timeout={int(self.BUSY_TIMEOUT_S * 1000)}")
        if self.FOREIGN_KEYS:
            self.conn.execute("PRAGMA foreign_keys=ON")
        if not readonly:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute(
                "PRAGMA synchronous=NORMAL"
            )  # with WAL: durable at each checkpoint, safe against corruption
            self._migrate()
        elif self.version() != len(self.migrations()):
            raise self.Error(
                self.STALE.format(path=self.path, have=self.version(), want=len(self.migrations()))
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Any:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def _migrate(self) -> None:
        have, migrations = self.version(), self.migrations()
        if have > len(migrations):
            raise self.Error(self.NEWER.format(path=self.path, have=have, want=len(self.migrations())))
        for n in range(have, len(migrations)):
            with self.tx():
                for stmt in self.statements(migrations[n]):
                    self.conn.execute(stmt)
                self.conn.execute(f"PRAGMA user_version={n + 1}")

    def now(self) -> str:
        """The current time in the one stored format: SQLite's own clock unless a test replaced it."""
        text = self._clock() if self._clock else self.conn.execute(NOW_SQL).fetchone()[0]
        return ev.check_ts(text)

    @contextlib.contextmanager
    def tx(self) -> Iterator[None]:
        """One write transaction (BEGIN IMMEDIATE). Re-entrant: an inner `tx` joins the outer one."""
        if self.readonly:
            raise self.Error(self.READ_ONLY)
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self._begin()
        self._depth = 1
        started = time.monotonic()
        try:
            yield
        except BaseException:
            self._depth = 0
            self.conn.execute("ROLLBACK")
            raise
        finally:
            _count("longest_tx_s", time.monotonic() - started)
        self._depth = 0
        self.conn.execute("COMMIT")

    def _begin(self) -> None:
        """BEGIN IMMEDIATE, tried again after a backoff while another process holds the write lock; `Locked` when it
        held it through every attempt."""
        for attempt in range(LOCK_RETRIES + 1):
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as e:
                if not _is_lock(e):
                    raise
                if attempt == LOCK_RETRIES:
                    _count("failed")
                    raise Locked(
                        f"the write lock was held by another process through {attempt + 1} attempts"
                    ) from e
                _count("retried")
                time.sleep(LOCK_BACKOFF_S[min(attempt, len(LOCK_BACKOFF_S) - 1)])
