"""Feedback's projection rules (schema 9; the overview plan's §2 and §12.6), from both channels.

- **The person's channel:** `onloop feedback` logs `feedback.given`, one batch of items with one origin and one time.
- **A team's export** (decision 0017): `feedback.batch.reported`, `feedback.item.reported` (one stamp per export) and
  `feedback.link.reported`. An item's stage is the team's declaration, kept in `declared_*` with `declared_by = 'team'`;
  `declared_since` is the first stamp at that stage, so a stage's age counts the exports it did not move.

Ids are content-derived, so a replay rebuilds the same rows: a batch is `b-` and 10 hex of its origin and origin ref,
an item `f-` and 10 hex of its batch and position (the person's) or its batch and the team's item id (an export).
Times come from the events, never from a clock.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any

Event = Mapping[str, Any]
SEP = "\x1f"
TITLE_MAX = 120


def _hex(*parts: object) -> str:
    return hashlib.sha256(SEP.join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


def batch_id(origin: str, origin_ref: str) -> str:
    return f"b-{_hex(origin, origin_ref)}"


def item_id(batch: str, key: object) -> str:
    return f"f-{_hex(batch, key)}"


def _when(e: Event) -> str:
    return e.get("at") or e["ts_system"]


def _has(conn: sqlite3.Connection) -> bool:
    return bool(
        conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'feedback'").fetchone()
    )


def _title(text: Any) -> str | None:
    return str(text)[:TITLE_MAX] if text else None


def _upsert_batch(conn: sqlite3.Connection, e: Event, row: Mapping[str, Any]) -> None:
    conn.execute(
        "INSERT INTO feedback_batches (id, origin, origin_ref, given_by, given_at, title, items, stages, last_seq) "
        "VALUES (:id, :origin, :origin_ref, :given_by, :given_at, :title, :items, :stages, :seq) "
        "ON CONFLICT(id) DO UPDATE SET given_by = excluded.given_by, title = COALESCE(excluded.title, title), "
        "items = excluded.items, stages = excluded.stages, last_seq = excluded.last_seq",
        {**row, "seq": e["seq"]},
    )


def _given(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """The person's batch: several items given in one sitting, in the order given."""
    if not _has(conn):
        return
    b = p["batch"]
    bid = batch_id(b["origin"], b["origin_ref"])
    items = p.get("items") or []
    _upsert_batch(
        conn,
        e,
        {
            "id": bid,
            "origin": b["origin"],
            "origin_ref": b["origin_ref"],
            "given_by": b.get("given_by"),
            "given_at": b.get("given_at") or _when(e),
            "title": _title(b.get("title")),
            "items": len(items),
            "stages": None,
        },
    )
    for pos, it in enumerate(items, 1):
        conn.execute(
            "INSERT INTO feedback (id, batch_id, position, kind, title, body_sha256, goal_id, goal_item, last_seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
            (
                item_id(bid, pos),
                bid,
                pos,
                it.get("kind") or "request",
                _title(it.get("title")),
                it.get("body_sha256"),
                it.get("goal_id"),
                it.get("goal_item"),
                e["seq"],
            ),
        )


def _export_batch_id(p: Mapping[str, Any]) -> str:
    producer = (p.get("imported") or {}).get("producer") or ""
    return batch_id("import", f"{producer}:{p['batch']}")


def _batch_reported(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    if not _has(conn):
        return
    producer = (p.get("imported") or {}).get("producer") or ""
    _upsert_batch(
        conn,
        e,
        {
            "id": _export_batch_id(p),
            "origin": "import",
            "origin_ref": f"{producer}:{p['batch']}",
            "given_by": p.get("from"),
            "given_at": _when(e),
            "title": None,
            "items": int(p.get("items") or 0),
            "stages": json.dumps(list(p.get("stages") or []), ensure_ascii=False),
        },
    )


def _item_reported(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """One export's stamp of an item: the stage the team declares, and since when it has been at that stage."""
    if not _has(conn):
        return
    bid = _export_batch_id(p)
    fid = item_id(bid, p["item"])
    at = _when(e)
    old = conn.execute(
        "SELECT declared_stage_index, declared_stage, declared_since, declared_at FROM feedback WHERE id = ?",
        (fid,),
    ).fetchone()
    skipped = json.dumps(list(p.get("skipped_stages") or []), ensure_ascii=False)
    if old is None:
        pos = conn.execute("SELECT count(*) FROM feedback WHERE batch_id = ?", (bid,)).fetchone()[0] + 1
        conn.execute(
            "INSERT INTO feedback (id, batch_id, position, origin_ref, kind, title, declared_stage, "
            "declared_stage_index, declared_waits_on, declared_skipped, declared_by, declared_at, declared_since, "
            "last_seq) VALUES (?, ?, ?, ?, 'request', ?, ?, ?, ?, ?, 'team', ?, ?, ?)",
            (
                fid,
                bid,
                pos,
                p["item"],
                _title(p.get("label")),
                p["stage"],
                p["stage_index"],
                p["waits_on"],
                skipped,
                at,
                at,
                e["seq"],
            ),
        )
        return
    if old[3] and at < old[3]:
        return  # an older export's stamp, read after a newer one: the newer declaration stands
    moved = (old[0], old[1]) != (p["stage_index"], p["stage"])
    conn.execute(
        "UPDATE feedback SET title = COALESCE(?, title), declared_stage = ?, declared_stage_index = ?, "
        "declared_waits_on = ?, declared_skipped = ?, declared_by = 'team', declared_at = ?, declared_since = ?, "
        "last_seq = ? WHERE id = ?",
        (
            _title(p.get("label")),
            p["stage"],
            p["stage_index"],
            p["waits_on"],
            skipped,
            at,
            at if moved else old[2],
            e["seq"],
            fid,
        ),
    )


def _link_reported(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """An item's link to an issue, as the team reports it. A link to an item not seen yet is kept (invariant 7)."""
    if not _has(conn):
        return
    fid = item_id(_export_batch_id(p), p["item"])
    ref = f"issue#{p['issue']}"
    conn.execute(
        "INSERT INTO feedback_links (id, feedback_id, kind, via, ref, seen_at, last_seq) "
        "VALUES (?, ?, 'issue', 'direct', ?, ?, ?) ON CONFLICT(id) DO UPDATE SET last_seq = excluded.last_seq",
        (f"l-{_hex(fid, 'issue', ref)}", fid, ref, _when(e), e["seq"]),
    )


RULES = {
    "feedback.given": _given,
    "feedback.batch.reported": _batch_reported,
    "feedback.item.reported": _item_reported,
    "feedback.link.reported": _link_reported,
}
