"""The goal tables' projection rules (decision 0014): goals, their items by number, the denominator at each edit, the
ticks by edit, and the links to goal items. Times come from the events (an edit's own time), never from a clock."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

Event = Mapping[str, Any]


def when(e: Event) -> str:
    return e.get("at") or e["ts_system"]


def _goal_declared(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    conn.execute(
        "INSERT INTO goals (id, adapter, locator, last_seq) VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
        "adapter = excluded.adapter, locator = excluded.locator, last_seq = excluded.last_seq",
        (p["goal_id"], p["adapter"], p["locator"], e["seq"]),
    )


def _goal_read(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    conn.execute(
        "UPDATE goals SET title = COALESCE(?, title), read_at = ?, last_edit_at = COALESCE(?, last_edit_at), "
        "last_seq = ? WHERE id = ?",
        (p.get("title"), when(e), p.get("last_edit_at"), e["seq"], p["goal_id"]),
    )


def _item_id(p: Mapping[str, Any]) -> str:
    return f"{p['goal_id']}/{p['number']}"


def _item_seen(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    conn.execute(
        "INSERT INTO goal_items (id, goal_id, number, text_sha256, first_seen_at, last_seq) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET text_sha256 = excluded.text_sha256, removed_at = NULL, last_seq = excluded.last_seq",
        (_item_id(p), p["goal_id"], p["number"], p["text_sha256"], when(e), e["seq"]),
    )


def _item_removed(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    conn.execute(
        "UPDATE goal_items SET removed_at = ?, last_seq = ? WHERE id = ?", (when(e), e["seq"], _item_id(p))
    )


def _tick(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    kind = "tick" if e["kind"] == "goal.ticked" else "untick"
    conn.execute(
        "INSERT INTO goal_ticks (id, goal_id, number, kind, at, edit_id, evidence_url, last_seq) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (e["id"], p["goal_id"], p["number"], kind, when(e), p["edit_id"], p.get("evidence_url"), e["seq"]),
    )
    conn.execute(
        "UPDATE goal_items SET checked = ?, checked_at = ?, last_seq = ? WHERE id = ?",
        (1 if kind == "tick" else 0, when(e) if kind == "tick" else None, e["seq"], _item_id(p)),
    )


def _denominator(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    conn.execute(
        "INSERT INTO goal_denominators (id, goal_id, at, count, last_seq) VALUES (?, ?, ?, ?, ?)",
        (e["id"], p["goal_id"], when(e), p["count"], e["seq"]),
    )


def _link_seen(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    conn.execute(
        "INSERT INTO links (id, from_kind, from_ref, goal_id, goal_item, via, seen_at, last_seq) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            p["id"],
            p["from_kind"],
            p["from_ref"],
            p.get("goal_id"),
            p.get("goal_item"),
            p["via"],
            when(e),
            e["seq"],
        ),
    )


def _link_withdrawn(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    conn.execute(
        "UPDATE links SET withdrawn_at = ?, last_seq = ? WHERE id = ? AND withdrawn_at IS NULL",
        (when(e), e["seq"], p["id"]),
    )


RULES = {
    "goal.declared": _goal_declared,
    "goal.read": _goal_read,
    "goal.item_seen": _item_seen,
    "goal.item_removed": _item_removed,
    "goal.ticked": _tick,
    "goal.unticked": _tick,
    "goal.denominator": _denominator,
    "link.seen": _link_seen,
    "link.withdrawn": _link_withdrawn,
}
