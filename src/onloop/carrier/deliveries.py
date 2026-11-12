"""The outbox (decision 0010, point 4): what onloop has to deliver into each role session, and how far each got.

    create(ledger, ...) -> id | None     a delivery and the text it carries (`message.posted`), in one transaction;
                                         None when the same cause already has one (`one_delivery_per_cause`) or the
                                         turn already had its corrective (`one_corrective_per_turn`)
    due(ledger, role, now)               the role's `created` deliveries whose next attempt is due, oldest first
    compose(ledger, ids) -> str          one user message: each delivery a block led by its id (one per run, R4-S7)
    ack_by_evidence(ledger)              ack each delivered or processing delivery whose session has a Stop-hook turn
                                         that ended at or after its `delivered_at`, whatever that turn said
    fail(...), escalate(...), cancel(...)

**Acknowledgement is not the outcome.** A delivery is acked by any Stop-hook turn on its session after it was
written, valid outcome line or not; whether the turn had a valid outcome stays on the turn (`turns.parse_error`), and
only the corrective path reads it. A missing outcome never fails a delivery.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Sequence

from .. import events as ev
from ..overlay.ledger import Ledger
from . import messages

MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = (60, 300, 900)
# The corrective wake's text, fixed (decision 0007; plan Q7): from the shared role template, never generated.
CORRECTIVE_TEXT = (
    "Your last turn ended without a valid outcome line. End every turn with an outcome line, as the shared role "
    "template says: the last line of your final message, on its own, one of "
    "`outcome: done | idle | in_review | blocked | handoff | continue` with its keys."
)
RESENT_MARK = "[resent after an interruption: you may have seen this message before]"


def _event(kind: str, role: str, payload: dict, source: str = "carrier") -> dict:
    return {
        "id": f"{kind}:{payload['id']}:{uuid.uuid4().hex[:8]}",
        "source": source,
        "kind": kind,
        "subject_ref": f"role:{role}",
        "actor": "carrier",
        "payload": payload,
    }


def create(
    ledger: Ledger,
    *,
    role: str,
    kind: str,
    body: str,
    retention_s: int,
    author: str = "onloop",
    channel: str = "carrier",
    origin: str = "onloop",
    dedupe_key: str | None = None,
    for_turn_id: str | None = None,
    item_id: str | None = None,
    source: str = "carrier",
) -> str | None:
    did = f"dlv-{uuid.uuid4().hex[:16]}"
    try:
        with ledger.tx():
            mid = messages.post(
                ledger,
                thread=role,
                body=body,
                author=author,
                channel=channel,
                retention_s=retention_s,
                delivery_id=did,
                source=source,
            )
            payload = {
                "id": did,
                "to_role": role,
                "kind": kind,
                "origin": origin,
                "message_id": mid,
                "dedupe_key": dedupe_key,
                "for_turn_id": for_turn_id,
                "item_id": item_id,
            }
            ledger.append(
                _event("delivery.created", role, {k: v for k, v in payload.items() if v is not None}, source)
            )
    except sqlite3.IntegrityError:
        return None
    return did


def due(ledger: Ledger, role: str, now: str) -> list[dict]:
    return ledger.select(
        "SELECT * FROM deliveries WHERE to_role = ? AND kind IS NOT NULL AND state = 'created' "
        "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) ORDER BY created_at, id",
        (role, now),
    )


def compose(ledger: Ledger, ids: Sequence[str]) -> str:
    """One message for the run. Each block: `[onloop delivery <id> · <kind>]`, the resent mark when it is a retry,
    then the text. A message whose body has expired is sent as its hash, never as nothing."""
    blocks = []
    for did in ids:
        d = ledger.select(
            "SELECT d.id, d.kind, d.resent, m.body, m.body_sha256, m.author, m.channel FROM deliveries d "
            "LEFT JOIN messages m ON m.id = d.message_id WHERE d.id = ?",
            (did,),
        )[0]
        head = f"[onloop delivery {d['id']} · {d['kind']}"
        if d["author"] == "human":
            head += f" · from the person, by {d['channel']}, local user, not verified"
        head += "]"
        lines = [head]
        if d["resent"]:
            lines.append(RESENT_MARK)
        lines.append(
            d["body"]
            if d["body"] is not None
            else f"(this message expired; its sha256 is {d['body_sha256']})"
        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def mark(ledger: Ledger, kind: str, ids: Sequence[str], role: str, **extra: object) -> None:
    for did in ids:
        ledger.append(_event(kind, role, {"id": did, **extra}))


def ack_by_evidence(ledger: Ledger, *, run_id: str | None = None) -> list[tuple[str, str]]:
    """(delivery, turn) for each delivery now acked: a Stop-hook turn on its role's session ended at or after it was
    written. The turn's outcome is not read."""
    where, args = ("AND d.run_id = ?", (run_id,)) if run_id else ("", ())
    rows = ledger.select(
        "SELECT d.id, d.to_role, d.delivered_at, s.session_id FROM deliveries d "
        "JOIN role_sessions s ON s.id = d.to_role "
        f"WHERE d.state IN ('delivered', 'processing') AND d.delivered_at IS NOT NULL {where} ORDER BY d.id",
        args,
    )
    out = []
    for r in rows:
        turn = ledger.select(
            "SELECT id FROM turns WHERE session_id = ? AND ended_at >= ? ORDER BY ended_at, last_seq LIMIT 1",
            (r["session_id"], r["delivered_at"]),
        )
        if not turn:
            continue
        ledger.append(_event("delivery.acked", r["to_role"], {"id": r["id"], "turn": turn[0]["id"]}))
        out.append((r["id"], turn[0]["id"]))
    return out


def ack_by_result(ledger: Ledger, ids: Sequence[str], role: str) -> None:
    """The turn ended (its result line came) but no Stop hook arrived in time: ack it from the result line."""
    for did in ids:
        state = ledger.select("SELECT state FROM deliveries WHERE id = ?", (did,))
        if state and state[0]["state"] in ("delivered", "processing", "claimed"):
            ledger.append(_event("delivery.acked", role, {"id": did, "turn": None, "by": "result_line"}))


def fail(
    ledger: Ledger,
    ids: Sequence[str],
    role: str,
    *,
    error: str,
    retriable: bool,
    uncount: bool = False,
    resend_mark: bool = False,
    coordinator: str | None = None,
) -> list[str]:
    """Put each back (`created`, with a backoff) or, past MAX_ATTEMPTS or when not retriable, escalate it. Returns
    the escalated ones."""
    escalated = []
    now = ledger.now()
    for did in ids:
        row = ledger.select("SELECT attempts, state FROM deliveries WHERE id = ?", (did,))
        if not row or row[0]["state"] in ("acked", "escalated", "cancelled"):
            continue
        attempts = int(row[0]["attempts"]) - (1 if uncount else 0)
        if retriable and attempts < MAX_ATTEMPTS:
            wait = 0 if uncount else RETRY_BACKOFF_S[min(max(attempts - 1, 0), len(RETRY_BACKOFF_S) - 1)]
            ledger.append(
                _event(
                    "delivery.failed",
                    role,
                    {
                        "id": did,
                        "error": error,
                        "retry": True,
                        "uncount": uncount,
                        "resend_mark": resend_mark,
                        "next_attempt_at": ev.shift_ts(now, wait),
                    },
                )
            )
            continue
        ledger.append(_event("delivery.failed", role, {"id": did, "error": error, "retry": False}))
        ledger.append(
            _event("delivery.escalated", role, {"id": did, "to": coordinator or "human", "error": error})
        )
        escalated.append(did)
    return escalated
