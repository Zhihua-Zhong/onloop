"""The hook receiver: Claude Code's Stop, SessionStart and Notification hooks, posted by `onloop daemon hook`.

    POST /hooks/stop | /hooks/session-start | /hooks/notification
    body: {"role": "<role name>", "payload": <the hook's JSON, as Claude Code passed it on stdin>}

Each becomes one ledger event (`hook.stop`, `hook.session_start`, `hook.notification`) with `source = "hook"`, the role
as its subject, and the ledger's own time. A Stop is parsed for its outcome line (decision 0007) here, once: the event
keeps the parsed outcome or the parse error, and the outcome line itself, but **never the message text** (the same rule
as the liveness recorder: onloop keeps what it needs to judge a turn, not what was said).

The role comes from the hook command (each role's settings name it), not from the payload. The event id is derived
from the role, the event, the session and the prompt id, so a hook delivered twice is logged once.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from .. import disposition
from ..overlay.ledger import Ledger
from .server import WriteRefused

EVENTS = {"stop": "hook.stop", "session-start": "hook.session_start", "notification": "hook.notification"}
BLOCKING_NOTIFICATIONS = ("permission_prompt", "elicitation_dialog")
MESSAGE_CAP = 200
_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_CLAIMED = ("timestamp", "at", "time", "ts")


def _text(payload: dict[str, Any], key: str, cap: int = 300) -> str | None:
    v = payload.get(key)
    return v[:cap] if isinstance(v, str) else None


def event_for(name: str, body: dict[str, Any]) -> dict[str, Any]:
    """The ledger event for one hook post. Pure: raises WriteRefused for a body it cannot take."""
    if name not in EVENTS:
        raise WriteRefused(404, f"no hook {name!r}; expected one of {', '.join(EVENTS)}")
    role = body.get("role")
    if not isinstance(role, str) or not _ROLE.match(role):
        raise WriteRefused(422, "a hook post names its role: lowercase letters, digits, '_' or '-'")
    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise WriteRefused(422, "a hook post carries the hook's JSON object as `payload`")
    session = _text(payload, "session_id", 80)
    prompt = _text(payload, "prompt_id", 80)
    out: dict[str, Any] = {"role": role, "session_id": session}
    claimed = {k: payload[k] for k in _CLAIMED if isinstance(payload.get(k), str)}
    if claimed:
        out["claimed_at"] = claimed  # kept apart; the event's time is the ledger's own
    if name == "stop":
        got = disposition.parse(payload.get("last_assistant_message"))
        out.update(
            prompt_id=prompt,
            permission_mode=_text(payload, "permission_mode", 40),
            stop_hook_active=bool(payload.get("stop_hook_active")),
        )
        if isinstance(got, disposition.Outcome):
            out["outcome"] = {"value": got.value, "ref": got.ref, "owner": got.owner, "waker": got.waker}
            out["line"] = got.line
        else:
            out["parse_error"] = got.reason
            out["line"] = got.line if got.line and got.line.startswith("outcome:") else None
    elif name == "session-start":
        out["start_source"] = _text(payload, "source", 40)
    else:
        out["notification_type"] = _text(payload, "notification_type", 60)
        out["message"] = _text(payload, "message", MESSAGE_CAP)
        out["blocking"] = out["notification_type"] in BLOCKING_NOTIFICATIONS
    if session and (prompt or name == "session-start"):
        key = "\x1f".join(("hook", name, role, session, prompt or str(payload.get("source") or "")))
        eid = hashlib.sha256(key.encode("utf-8")).hexdigest()
    else:
        eid = f"hook-{uuid.uuid4().hex}"  # nothing stable to key it on: each post is its own event
    return {
        "id": eid,
        "source": "hook",
        "kind": EVENTS[name],
        "subject_ref": f"role:{role}",
        "actor": role,
        "payload": out,
    }


def handle(ledger: Ledger, rest: str, body: dict[str, Any]) -> dict[str, Any]:
    """The `/hooks/` route."""
    event = event_for(rest, body)
    seq = ledger.append(event)  # one transaction, inside onloopd's writer lock
    return {"logged": seq is not None, "seq": seq, "id": event["id"]}
