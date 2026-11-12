"""The legacy `onloop.db`, as onloop wrote it before the ledger (its last schema, version 3), frozen for the tests.

onloop no longer writes or reads this file (decision 0009): `onloop overlay import --store` imports one once, and its
tests build the file with this writer, so the upgrade path is tested against the real old format. It also exercises
the SQLite plumbing it shares with the ledger (`onloop.sqlitedb`). What follows is the module's own description.

The store: one SQLite file (WAL) holding the event log, the intake's memory, and the slot tables.

Events, snapshots and deliveries are a cache of GitHub and can be rebuilt by running intake again. The slot tables
(`slot_requests`, `grants`) are authoritative: nothing on GitHub says who holds a scarce thing now.

One clock. Every timestamp is UTC text in the single format of `events.TS_FORMAT`, so comparing text compares time.
The clock defaults to SQLite's own (`strftime('%Y-%m-%dT%H:%M:%fZ','now')`) and a test may replace it. The database
refuses a timestamp in any other format (a CHECK on every timestamp column). The clock decides when a lease expires;
it never decides who holds a slot, which is the job of a unique index.
"""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Callable
from typing import Any

from onloop import events as ev
from onloop.sqlitedb import SQLiteDB

DEFAULT_PATH = pathlib.Path(".onloop") / "onloop.db"
ENV_PATH = "ONLOOP_DB"

_TS = "GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'"


def _ts(column: str, *, nullable: bool = False) -> str:
    """A TEXT column that only accepts the stored timestamp format."""
    check = f"{column} {_TS}"
    if nullable:
        check = f"({column} IS NULL OR {check})"
    return f"{column} TEXT{'' if nullable else ' NOT NULL'} CHECK ({check})"


SCHEMA_1 = f"""
CREATE TABLE events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  id          TEXT NOT NULL UNIQUE,
  repo        TEXT NOT NULL,
  kind        TEXT NOT NULL,
  subject     TEXT NOT NULL,
  actor       TEXT,
  {_ts("at")},
  {_ts("observed_at")},
  payload     TEXT NOT NULL CHECK (json_valid(payload))
);
CREATE INDEX events_kind    ON events(kind, seq);
CREATE INDEX events_subject ON events(subject, seq);

CREATE TABLE cursors (
  name TEXT PRIMARY KEY, value TEXT NOT NULL, etag TEXT, {_ts("updated_at")}
);

CREATE TABLE pr_snapshots (
  number INTEGER PRIMARY KEY, head TEXT NOT NULL, state TEXT NOT NULL, draft INTEGER NOT NULL,
  ready_count INTEGER NOT NULL DEFAULT 0, drafted_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT, reviews_hash TEXT NOT NULL, checks_hash TEXT NOT NULL, patch_id TEXT,
  facts TEXT NOT NULL CHECK (json_valid(facts)),
  {_ts("head_seen_at")},
  {_ts("seen_at")}
);

CREATE TABLE deliveries (
  event_id TEXT NOT NULL REFERENCES events(id), handler TEXT NOT NULL,
  status   TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','done','dead')),
  attempts INTEGER NOT NULL DEFAULT 0,
  {_ts("next_at")},
  {_ts("claimed_until", nullable=True)},
  last_error TEXT,
  PRIMARY KEY (event_id, handler)
);
CREATE INDEX deliveries_due ON deliveries(status, next_at);

CREATE TABLE effects (
  key TEXT PRIMARY KEY, event_id TEXT NOT NULL, handler TEXT NOT NULL, kind TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'done' CHECK (state IN ('started','done')),
  detail TEXT NOT NULL CHECK (json_valid(detail)), {_ts("at")}
);
CREATE INDEX effects_event ON effects(event_id);

CREATE TABLE slot_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT NOT NULL, holder TEXT NOT NULL, role TEXT NOT NULL,
  reason TEXT NOT NULL, ref TEXT NOT NULL,
  paths TEXT NOT NULL CHECK (json_valid(paths)),
  {_ts("requested_at")},
  {_ts("wait_until")},
  status TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting','granted','cancelled','expired'))
);
CREATE UNIQUE INDEX one_waiting_request ON slot_requests(slot, holder, ref) WHERE status = 'waiting';

CREATE TABLE grants (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT NOT NULL, holder TEXT NOT NULL, role TEXT NOT NULL,
  request_id INTEGER NOT NULL REFERENCES slot_requests(id),
  {_ts("granted_at")}, granted_by TEXT NOT NULL,
  {_ts("expires_at")},
  {_ts("renewed_at", nullable=True)},
  {_ts("released_at", nullable=True)},
  release_reason TEXT,
  conditions TEXT NOT NULL CHECK (json_valid(conditions)),
  commands   TEXT NOT NULL CHECK (json_valid(commands))
);
CREATE UNIQUE INDEX one_live_grant ON grants(slot) WHERE released_at IS NULL;

CREATE TABLE runner (
  id INTEGER PRIMARY KEY CHECK (id = 1), pid INTEGER, host TEXT,
  {_ts("started_at", nullable=True)},
  {_ts("heartbeat_at", nullable=True)}
);
"""

# Seats. A slot may have more than one holder at a time (`capacity`); each live grant sits in one seat, and the database,
# not the code, refuses two live grants in the same seat. At capacity 1 there is one seat, so this is exactly the old
# "one live grant per slot". The gate slot may never take a seat but the first. Every row that exists keeps seat 1.
SCHEMA_2 = """
ALTER TABLE grants ADD COLUMN seat INTEGER NOT NULL DEFAULT 1 CHECK (seat BETWEEN 1 AND 8 AND (seat = 1 OR slot <> 'gate'));
DROP INDEX one_live_grant;
CREATE UNIQUE INDEX one_live_seat ON grants(slot, seat) WHERE released_at IS NULL
"""

# An instance id. Request ids count from 1 in every database, so a comment that says "grant request 4" means nothing without
# saying which database it is about. Each database gets a random id when it is created (or migrated to this version) and keeps
# it for life; nothing writes to this table afterwards.
SCHEMA_3 = """
CREATE TABLE instance (
  id  INTEGER PRIMARY KEY CHECK (id = 1),
  uid TEXT NOT NULL CHECK (length(uid) = 16 AND uid NOT GLOB '*[^0-9a-f]*')
);
INSERT INTO instance(id, uid) VALUES (1, lower(hex(randomblob(8))))
"""

MIGRATIONS: list[str] = [
    SCHEMA_1,
    SCHEMA_2,
    SCHEMA_3,
]  # index + 1 is the version; a migration is never edited afterwards


class StoreError(RuntimeError):
    pass


def default_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get(ENV_PATH) or DEFAULT_PATH)


class Store(SQLiteDB):
    """A connection with explicit transactions, one clock, and the few operations everything else builds on."""

    Error = StoreError
    FOREIGN_KEYS = True
    MISSING = "{path} does not exist; nothing has been recorded yet"
    STALE = (
        "{path} is schema version {have}, this onloop expects {want}; "
        "any command that writes (for example `onloop slot status`) migrates it first"
    )
    NEWER = "{path} is schema version {have}, newer than this onloop ({want})"
    READ_ONLY = "this store was opened read-only"

    def __init__(
        self,
        path: str | os.PathLike | None = None,
        *,
        clock: Callable[[], str] | None = None,
        readonly: bool = False,
    ):
        super().__init__(path if path is not None else default_path(), clock=clock, readonly=readonly)

    def migrations(self) -> list[str]:
        return MIGRATIONS  # read at call time, so a test can replace the module's list

    @staticmethod
    def statements(script: str) -> list[str]:
        return _statements(script)

    def __enter__(self) -> Store:
        return self

    def instance(self) -> str:
        """This database's own id: sixteen lowercase hex digits, made when it was created and never changed."""
        return str(self.conn.execute("SELECT uid FROM instance WHERE id = 1").fetchone()[0])

    # -- events --------------------------------------------------------------------------------------------

    def append(self, repo: str, event: ev.Event) -> int | None:
        """Append one event. Returns its `seq`, or None when an event with the same id is already in the log."""
        observed = self.now()
        at = ev.check_ts(event.at) if event.at else observed
        eid = ev.event_id(repo, event.kind, event.subject, event.key)
        with self.tx():
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO events(id, repo, kind, subject, actor, at, observed_at, payload) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    eid,
                    repo,
                    event.kind,
                    event.subject,
                    event.actor,
                    at,
                    observed,
                    json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
                ),
            )
            return int(cur.lastrowid) if cur.rowcount else None

    def events(
        self,
        *,
        kind: str | None = None,
        subject: str | None = None,
        since_seq: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM events WHERE seq > ?", [since_seq]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        if subject:
            sql += " AND subject = ?"
            args.append(subject)
        sql += " ORDER BY seq"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        out = []
        for r in self.conn.execute(sql, args):
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    # -- cursors -------------------------------------------------------------------------------------------

    def cursor(self, name: str) -> tuple[str | None, str | None]:
        r = self.conn.execute("SELECT value, etag FROM cursors WHERE name = ?", (name,)).fetchone()
        return (r["value"], r["etag"]) if r else (None, None)

    def set_cursor(self, name: str, value: str, etag: str | None = None) -> None:
        with self.tx():
            self.conn.execute(
                "INSERT INTO cursors(name, value, etag, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value, etag=excluded.etag, "
                "updated_at=excluded.updated_at",
                (name, value, etag, self.now()),
            )


def _statements(script: str) -> list[str]:
    """Split a migration script on semicolons that end a statement (no statement here contains a ';' in a string)."""
    return [s.strip() for s in script.split(";") if s.strip()]
