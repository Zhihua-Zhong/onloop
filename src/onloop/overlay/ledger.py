"""The overlay ledger: append an event, apply it to the projections, replay, and read (decision 0006).

    Ledger(path)                      open (and migrate) the ledger file; `.onloop/ledger.db` or `$ONLOOP_LEDGER`
    append(event) -> seq | None       log one event and apply it, in one transaction; None if its id is already logged
    append_batch(events, cursor=...)  log and apply many, and move an ingest cursor, all in one transaction
    cursor(source) -> int             how far a source has been read
    replay() -> str                   clear the projections and rebuild them from the log; returns the canonical dump
    rebuild_matches() -> (bool, ...)  rebuild into a scratch copy and compare it with the live projections, byte for byte

`ts_system` is the ledger's own clock at the moment of appending; the event's own time, when it has one, is `at`.
An event's `id` is its dedup key: an event already in the log is skipped, so ingesting the same batch twice changes
nothing. Every write is one transaction, so a crash leaves either all of a batch (and its cursor) or none of it.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import tempfile
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .. import __version__
from .. import events as ev
from ..sqlitedb import SQLiteDB
from . import constraint as constraint_mod
from . import projections, schema

DEFAULT_PATH = pathlib.Path(".onloop") / "ledger.db"
ENV_PATH = "ONLOOP_LEDGER"


class LedgerError(RuntimeError):
    pass


def default_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get(ENV_PATH) or DEFAULT_PATH)


def _dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Ledger(SQLiteDB):
    Error = LedgerError
    CHECK_SAME_THREAD = False  # onloopd reads it from the HTTP server's threads; writes stay on one thread
    MISSING = "{path} does not exist; run `onloop overlay import` or start onloopd first"
    STALE = (
        "{path} is ledger schema {have}, this onloop expects {want}; open it once for writing to migrate it"
    )
    NEWER = "{path} is ledger schema {have}, newer than this onloop ({want})"
    READ_ONLY = "this ledger was opened read-only"

    def __init__(
        self,
        path: str | os.PathLike | None = None,
        *,
        clock: Callable[[], str] | None = None,
        readonly: bool = False,
    ):
        super().__init__(path if path is not None else default_path(), clock=clock, readonly=readonly)

    def migrations(self) -> list[str]:
        return schema.MIGRATIONS  # read at call time, so a test can replace the module's list

    @staticmethod
    def statements(script: str) -> list[str]:
        return schema.statements(script)

    def _migrate(self) -> None:
        """Migrate forward, then, if an older file was migrated, rebuild the projections from the log in one
        transaction: they are a function of the log under the current rules, and a new version may add a column that a
        rule now fills (a schedule's last result time) or a rule that did not exist."""
        have = self.version()
        super()._migrate()
        if 0 < have < self.version():
            with self.tx():
                self._rebuild(self.conn)

    def __enter__(self) -> Ledger:
        return self

    # -- writing -------------------------------------------------------------------------------------------------------

    def _append_one(self, event: Mapping[str, Any]) -> int | None:
        for key in ("id", "source", "kind", "subject_ref"):
            if not event.get(key):
                raise LedgerError(f"an event needs {key!r}: {dict(event)!r}"[:300])
        at = ev.check_ts(event["at"]) if event.get("at") else None
        payload = event.get("payload") or {}
        cur = self.conn.execute(
            "INSERT INTO events(id, ts_system, source, kind, subject_ref, actor, at, payload) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",  # only a duplicate id is skipped; OR IGNORE would
            # also swallow a CHECK violation
            (
                event["id"],
                self.now(),
                event["source"],
                event["kind"],
                event["subject_ref"],
                event.get("actor"),
                at,
                _dump(payload),
            ),
        )
        if not cur.rowcount:
            return None
        seq = int(cur.lastrowid)
        row = self.conn.execute("SELECT * FROM events WHERE seq = ?", (seq,)).fetchone()
        projections.apply(self.conn, dict(row))
        return seq

    def append(self, event: Mapping[str, Any]) -> int | None:
        """Log one event and apply it. Returns its seq, or None when an event with the same id is already logged."""
        with self.tx():
            return self._append_one(event)

    def append_batch(
        self, events: Iterable[Mapping[str, Any]], *, cursor: tuple[str, int] | None = None
    ) -> list[int]:
        """Log and apply each event, then move `cursor` (source, position), all in one transaction."""
        with self.tx():
            out = [s for s in (self._append_one(e) for e in events) if s is not None]
            if cursor is not None:
                self.conn.execute(
                    "INSERT INTO ingest_cursors(source, position, updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(source) DO UPDATE SET position = excluded.position, updated_at = excluded.updated_at",
                    (cursor[0], int(cursor[1]), self.now()),
                )
            return out

    def ingest_state(self, source: str) -> dict[str, Any]:
        """A source's own state (not a projection): key -> the JSON value it stored."""
        rows = self.conn.execute("SELECT key, value FROM ingest_state WHERE source = ?", (source,)).fetchall()
        return {r[0]: json.loads(r[1]) for r in rows}

    def put_ingest_state(self, source: str, values: Mapping[str, Any]) -> None:
        """Store each key's JSON value for a source, in one transaction (or the caller's)."""
        with self.tx():
            now = self.now()
            for key, value in values.items():
                self.conn.execute(
                    "INSERT INTO ingest_state(source, key, value, updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(source, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (source, key, _dump(value), now),
                )

    def cursor(self, source: str) -> int:
        row = self.conn.execute("SELECT position FROM ingest_cursors WHERE source = ?", (source,)).fetchone()
        return int(row[0]) if row else 0

    # -- replay --------------------------------------------------------------------------------------------------------

    def _rebuild(self, conn: sqlite3.Connection) -> None:
        for table in schema.PROJECTIONS:
            conn.execute(f"DELETE FROM {table}")
        for row in conn.execute("SELECT * FROM events ORDER BY seq").fetchall():
            projections.apply(conn, dict(row))

    def replay(self) -> str:
        """Clear every projection and rebuild it from the log, in one transaction. Returns the canonical dump."""
        with self.tx():
            self._rebuild(self.conn)
        return self.dump()

    def dump(self) -> str:
        return projections.dump(self.conn)

    def rebuild_matches(self) -> tuple[bool, str, str]:
        """Rebuild the projections in a scratch copy of the file and compare with the live ones, byte for byte.

        The live file is not changed. Returns (equal, live dump, rebuilt dump)."""
        live = self.dump()
        with tempfile.TemporaryDirectory(prefix="onloop-ledger-check-") as tmp:
            copy = sqlite3.connect(str(pathlib.Path(tmp) / "copy.db"), isolation_level=None)
            try:
                copy.row_factory = sqlite3.Row
                self.conn.backup(copy)
                copy.execute("BEGIN")
                self._rebuild(copy)
                copy.execute("COMMIT")
                rebuilt = projections.dump(copy)
            finally:
                copy.close()
        return live == rebuilt, live, rebuilt

    # -- reading (onloopd's Reader) ------------------------------------------------------------------------------------

    def _rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        out = []
        for r in self.conn.execute(sql, args).fetchall():
            d = dict(r)
            for k in ("detail", "budget", "payload"):
                if isinstance(d.get(k), str):
                    d[k] = json.loads(d[k])
            out.append(d)
        return out

    def select(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Rows of a read-only query over the projections, as dicts (JSON columns decoded). For the detectors."""
        return self._rows(sql, args)

    def last_seq(self) -> int:
        return int(self.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0])

    def health(self) -> dict[str, Any]:
        cursors = {
            r["source"]: r["position"] for r in self._rows("SELECT * FROM ingest_cursors ORDER BY source")
        }
        return {
            "path": str(self.path),
            "schema_version": self.version(),
            "onloop": __version__,
            "last_seq": self.last_seq(),
            "cursors": cursors,
        }

    def items(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM items ORDER BY id")

    def waits(self, *, include_cleared: bool = False) -> list[dict[str, Any]]:
        where = "" if include_cleared else " WHERE cleared_at IS NULL"
        return self._rows(f"SELECT * FROM waits{where} ORDER BY since, id")

    def leases(self, *, include_released: bool = False) -> list[dict[str, Any]]:
        where = "" if include_released else " WHERE released_at IS NULL"
        return self._rows(f"SELECT * FROM leases{where} ORDER BY granted_at, id")

    def instance(self) -> str:
        """This ledger file's own id, drawn once when it was created (or migrated to version 4)."""
        return str(self.conn.execute("SELECT uid FROM instance WHERE id = 1").fetchone()[0])

    def schedules(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM schedules ORDER BY id")

    def constraint(self, now: str) -> dict[str, Any]:
        states = {r["id"]: r["state"] for r in self._rows("SELECT id, state FROM resources")}
        return constraint_mod.compute(self.waits(), now, states).as_dict()

    def turns(self, *, role: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """The most recent turns first, of one role or all."""
        where, args = ("WHERE role = ?", (role,)) if role else ("", ())
        return self._rows(
            f"SELECT * FROM turns {where} ORDER BY ended_at DESC, last_seq DESC LIMIT ?", (*args, limit)
        )

    def alerts(self, *, include_closed: bool = False) -> list[dict[str, Any]]:
        where = "" if include_closed else " AND state != 'done'"
        return self._rows(f"SELECT * FROM items WHERE kind = 'alert'{where} ORDER BY opened_at, id")

    def roles(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM roles ORDER BY id")

    def events_after(self, seq: int, limit: int) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?", (int(seq), int(limit)))
