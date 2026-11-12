"""Forward-only import of the older stores into the ledger, by cursor (decision 0006).

The older stores are legacy: `onloop.db` (the poll loop's events, including the slots' events) and the work graph's
store (`wg_events`). They are opened read-only and never changed. Each event is appended to the ledger verbatim:
same kind, same subject, same payload, its own time as `at`, and its old sha256 id as the ledger's dedup key, with
`source = "legacy"` and the store and old sequence number added to the payload under `legacy`.

    import_poll_store(ledger, path)   onloop.db events after the cursor `legacy:onloop.db`
    import_work_graph(ledger, path)   wg_events after the cursor `legacy:workgraph`

Each batch and its cursor move are one ledger transaction. A crash mid-batch leaves the batch and the cursor as they
were, and the next run starts from the same place; an event already logged is skipped by its id, so running the import
twice, or after a crash, ends in the same projections.

It is the upgrade path from an install that used them (`onloop overlay import --store/--workgraph`), run once.
Nothing writes them any more, and onloopd does not read them on every tick (decision 0009).
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
from dataclasses import dataclass
from typing import Any

from .ledger import Ledger

POLL_STORE = "legacy:onloop.db"
WORK_GRAPH = "legacy:workgraph"
BATCH = 500


@dataclass
class ImportResult:
    source: str
    read: int = 0
    appended: int = 0
    cursor: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "read": self.read, "appended": self.appended, "cursor": self.cursor}


def _open_readonly(path: str | os.PathLike) -> sqlite3.Connection:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{p} does not exist")
    conn = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _import(
    ledger: Ledger, source: str, conn: sqlite3.Connection, sql: str, convert, batch: int
) -> ImportResult:
    res = ImportResult(source=source, cursor=ledger.cursor(source))
    while True:
        rows = conn.execute(sql, (res.cursor, batch)).fetchall()
        if not rows:
            return res
        events = [convert(dict(r)) for r in rows]
        last = int(rows[-1]["seq"])
        res.appended += len(ledger.append_batch(events, cursor=(source, last)))
        res.read += len(rows)
        res.cursor = last


def _poll_event(row: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(row["payload"])
    payload["legacy"] = {
        "store": "onloop.db",
        "seq": row["seq"],
        "repo": row["repo"],
        "observed_at": row["observed_at"],
    }
    return {
        "id": row["id"],
        "source": "legacy",
        "kind": row["kind"],
        "subject_ref": row["subject"],
        "actor": row["actor"],
        "at": row["at"],
        "payload": payload,
    }


def _work_graph_event(row: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(row["payload"])
    payload["legacy"] = {
        "store": "workgraph",
        "seq": row["seq"],
        "source": row["source"],
        "observed_at": row["observed_at"],
    }
    return {
        "id": row["id"],
        "source": "legacy",
        "kind": row["kind"],
        "subject_ref": row["node"],
        "actor": None,
        "at": row["at"],
        "payload": payload,
    }


def import_poll_store(ledger: Ledger, path: str | os.PathLike, *, batch: int = BATCH) -> ImportResult:
    """Import onloop.db's events after the ledger's cursor for it."""
    conn = _open_readonly(path)
    try:
        sql = "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?"
        return _import(ledger, POLL_STORE, conn, sql, _poll_event, batch)
    finally:
        conn.close()


def import_work_graph(ledger: Ledger, path: str | os.PathLike, *, batch: int = BATCH) -> ImportResult:
    """Import the work graph's events after the ledger's cursor for it."""
    conn = _open_readonly(path)
    try:
        sql = "SELECT * FROM wg_events WHERE seq > ? ORDER BY seq LIMIT ?"
        return _import(ledger, WORK_GRAPH, conn, sql, _work_graph_event, batch)
    finally:
        conn.close()
