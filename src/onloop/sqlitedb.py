"""The SQLite plumbing of the overlay ledger (`overlay/ledger.py`): the connection, the schema migrations, the clock and
write transactions.

A store is a subclass (the ledger; the tests' frozen copy of the legacy `onloop.db`). Each names its migrations, how a
migration script splits into statements, its error class and the words of its refusals; the behaviour is the same:

- a file is opened in WAL mode with a five-second busy timeout, and migrated forward to `len(migrations())` (index + 1
  is the version, kept in `PRAGMA user_version`), each migration in its own transaction;
- a database from a newer onloop is refused untouched;
- a read-only open (`mode=ro`) never migrates, and refuses a database at another version;
- `now()` is SQLite's own clock in the one stored format unless a test passes `clock`;
- `tx()` is one `BEGIN IMMEDIATE` transaction, re-entrant: an inner `tx` joins the outer one.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import sqlite3
from collections.abc import Callable, Iterator
from typing import Any, ClassVar

from . import events as ev

NOW_SQL = "SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')"


class SQLiteDB:
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
                uri, uri=True, isolation_level=None, timeout=5.0, check_same_thread=self.CHECK_SAME_THREAD
            )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(
                str(self.path), isolation_level=None, timeout=5.0, check_same_thread=self.CHECK_SAME_THREAD
            )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        if self.FOREIGN_KEYS:
            self.conn.execute("PRAGMA foreign_keys=ON")
        if not readonly:
            self.conn.execute("PRAGMA journal_mode=WAL")
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
        self.conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._depth = 0
            self.conn.execute("ROLLBACK")
            raise
        self._depth = 0
        self.conn.execute("COMMIT")
