"""An adapter's asks into the ledger (decision 0013): the tracker is read, never written, and onloop's list is the record.

    asks(ledger, adapter, now) -> dict       open, amend or resolve one task per external ask; log the open set

- A new open ask opens a task (`origin=adapter`, `origin_ref=<adapter>:<id>`), its fields read from the item and
  checked and dated as an outcome line's are (a field that does not pass is left out, and the task says what it
  lacks).
- An ask edited at its source amends the task, as evidence: "edited on the tracker".
- An ask closed at its source resolves the task as `done`, with the close as evidence and its source's time, by
  `adapter`; the digest shows it under "resolved without you" unless the person closed it themself.
- The set of open asks is logged (`adapter.asks_listed`) when it changes, so reconciliation can say which of them is
  missing from the list.
The cursor is the adapter's own, kept in `ingest_state`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .. import disposition
from ..overlay.human_tasks import task_id
from ..overlay.ledger import Ledger
from . import Adapter, ExternalAsk

SOURCE = "adapter"


def _ask_fields(ext: ExternalAsk, now: str) -> tuple[dict[str, Any], list[str]]:
    """The fields that pass the line's rules, dated as the line's are, and the names of those that did not."""
    good: dict[str, str] = {}
    dropped: list[str] = []
    for k, v in ext.fields.items():
        if k not in disposition.ASK_KEYS:
            continue
        trial = {**good, k: v}
        if disposition._check_ask(trial, "") is None:
            good = trial
        else:
            dropped.append(k)
    got = disposition.Outcome(
        "blocked", owner="human", ref="x", **{k: good.get(k) for k in disposition.ASK_KEYS}
    )
    return disposition.frozen_ask(got, now) or {}, dropped


def _fingerprint(ext: ExternalAsk) -> str:
    raw = json.dumps([ext.title, dict(ext.fields), ext.state], sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _event(eid: str, kind: str, subject: str, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": eid,
        "source": SOURCE,
        "kind": kind,
        "subject_ref": subject,
        "actor": actor,
        "payload": payload,
    }


def asks(ledger: Ledger, adapter: Adapter, now: str) -> dict[str, Any]:
    if "asks" not in adapter.capabilities:
        return {"adapter": adapter.id, "skipped": "no asks capability"}
    state = ledger.ingest_state(f"adapter:{adapter.id}")
    seen: dict[str, str] = dict(state.get("seen") or {})
    open_set: list[str] = list(state.get("open") or [])
    items, cursor = adapter.asks(state.get("cursor"))
    actor = f"adapter:{adapter.id}"
    events: list[dict[str, Any]] = []
    for ext in items:
        origin_ref = f"{adapter.id}:{ext.id}"
        tid = task_id("adapter", origin_ref)
        fp = _fingerprint(ext)
        if seen.get(ext.id) == fp:
            continue
        exists = ledger.select("SELECT stage FROM human_tasks WHERE id = ?", (tid,))
        ask, dropped = _ask_fields(ext, now)
        if ext.state == "open":
            payload = {
                "id": tid,
                "origin": "adapter",
                "origin_ref": origin_ref,
                "author_role": None,
                "ref": f"{adapter.id}#{ext.id}",
                "ask": ask,
                "title": ext.title,
                "url": ext.url,
                "dropped": dropped,
                "source_at": ext.source_at,
                "observed_at": now,
            }
            kind = "task.amended" if exists else "task.opened"
            if kind == "task.amended":
                payload["note"] = "edited on the tracker"
            events.append(_event(f"{actor}:{ext.id}:{fp}", kind, f"task:{tid}", actor, payload))
            if ext.id not in open_set:
                open_set.append(ext.id)
        else:
            if exists and exists[0]["stage"] != "resolved":
                evidence = f"adapter:{adapter.id}/{ext.id}/closed@{ext.closed_at or ext.source_at or now}"
                if ext.closed_by:
                    evidence += f" by {ext.closed_by}"
                events.append(
                    _event(
                        f"{actor}:{ext.id}:{fp}",
                        "task.resolved",
                        f"task:{tid}",
                        actor,
                        {
                            "id": tid,
                            "resolution": "done",
                            "evidence": evidence,
                            "by": "adapter",
                            "source_at": ext.closed_at,
                            "observed_at": now,
                        },
                    )
                )
            open_set = [x for x in open_set if x != ext.id]
        seen[ext.id] = fp
    before = sorted(state.get("open") or [])
    if sorted(open_set) != before or (items and not state):
        events.append(
            _event(
                f"{actor}:listed:{hashlib.sha256(json.dumps(sorted(open_set)).encode()).hexdigest()[:16]}:{now}",
                "adapter.asks_listed",
                f"adapter:{adapter.id}",
                actor,
                {"adapter": adapter.id, "open": sorted(open_set, key=lambda x: (len(x), x))},
            )
        )
    with ledger.tx():
        logged = ledger.append_batch(events)
        ledger.put_ingest_state(f"adapter:{adapter.id}", {"cursor": cursor, "seen": seen, "open": open_set})
    return {
        "adapter": adapter.id,
        "events": len(logged),
        "requests": getattr(getattr(adapter, "client", None), "requests", None),
    }
