"""The legacy stores onloop no longer writes, made for the tests of the one-shot import (`onloop overlay import`).

The work graph's store (`work_graph`) and the poll loop's `onloop.db` (`poll_store`) were removed with the UI that
read them (decision 0009). An install that has one upgrades by importing it once into the ledger, so the importer
keeps its tests. These helpers write files in the shape those stores had at their last version: the tables the
importer reads, frozen here, filled with plain SQL.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
from typing import Any

# The work graph's log at its last schema (version 2). The importer reads `wg_events` only.
WORK_GRAPH_SCHEMA = """
CREATE TABLE wg_events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  id          TEXT NOT NULL UNIQUE,
  source      TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('node.created','node.changed','decision_recorded','decision_written')),
  node        TEXT NOT NULL,
  at          TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  payload     TEXT NOT NULL CHECK (json_valid(payload))
);
CREATE INDEX wg_events_node ON wg_events(node, seq);
PRAGMA user_version = 2;
"""

# The fields of a node a sync could change (the store's `MUTABLE`).
NODE_MUTABLE = (
    "kind",
    "title",
    "state",
    "class",
    "weight",
    "owner",
    "spawned_by",
    "depends_on",
    "updated",
    "url",
    "default",
    "expires",
)


def _dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class WorkGraph:
    """A work-graph store written the way its `sync` wrote it: a `node.created` for a node seen the first time, a
    `node.changed` with the changed fields after that, and `missing` for a node a complete sync no longer reports."""

    def __init__(self, path: pathlib.Path, now: str):
        self.path, self.now = pathlib.Path(path), now
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(WORK_GRAPH_SCHEMA)
        self.nodes: dict[str, dict[str, Any]] = {}

    def __enter__(self) -> WorkGraph:
        return self

    def __exit__(self, *exc: object) -> None:
        self.conn.commit()
        self.conn.close()

    def _append(self, source: str, kind: str, node: str, key: str, at: str, payload: dict[str, Any]) -> None:
        eid = hashlib.sha256("\x1f".join((source, kind, node, key)).encode("utf-8")).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO wg_events (id, source, kind, node, at, observed_at, payload) VALUES (?,?,?,?,?,?,?)",
            (eid, source, kind, node, at, self.now, _dump(payload)),
        )

    def sync(self, source: str, observed: list[dict[str, Any]], *, complete: bool) -> None:
        reported = set()
        for node in observed:
            reported.add(node["id"])
            have = self.nodes.get(node["id"])
            if have is None:
                self._append(source, "node.created", node["id"], "created", node["created"], {"node": node})
            else:
                fields = {f: node.get(f) for f in NODE_MUTABLE if have.get(f) != node.get(f)}
                if not fields:
                    continue
                at = node.get("updated") or self.now
                key = hashlib.sha256(_dump({"at": at, "fields": fields}).encode("utf-8")).hexdigest()
                self._append(
                    source,
                    "node.changed",
                    node["id"],
                    key,
                    at,
                    {"fields": fields, "was": {f: have.get(f) for f in fields}},
                )
            self.nodes[node["id"]] = dict(node)
        if complete:
            for nid, have in sorted(self.nodes.items()):
                if have["source"] == source and nid not in reported and have["state"] != "missing":
                    fields = {"state": "missing"}
                    key = hashlib.sha256(
                        _dump({"at": self.now, "fields": fields}).encode("utf-8")
                    ).hexdigest()
                    self._append(
                        source,
                        "node.changed",
                        nid,
                        key,
                        self.now,
                        {"fields": fields, "was": {"state": have["state"]}},
                    )
                    have["state"] = "missing"
        self.conn.commit()

    def events(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT seq, kind, node, payload FROM wg_events ORDER BY seq").fetchall()
        return [{"seq": s, "kind": k, "node": n, "payload": json.loads(p)} for s, k, n, p in rows]
