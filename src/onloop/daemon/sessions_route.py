"""The `/sessions/` write route: what `onloop open <role>` reports (decision 0019, a8).

    POST /sessions/opened   {"role", "session_id", "cwd"?, "step"?: "KIND:REF"}   -> session.opened, and step.started
    POST /sessions/ended    {"role", "session_id", "step"?: "KIND:REF", "exit_code"?}  -> step.ended

Each event has `source = "cli"`, the role as its actor, and the ledger's own time. Its id is derived from the session
(and the step), so a report sent twice is logged once. Like every post to onloopd, it is evidence from a local
program, never authority.
"""

from __future__ import annotations

from typing import Any

from .. import telemetry
from ..overlay.ledger import Ledger
from .server import WriteRefused


def _str(body: dict[str, Any], key: str, required: bool = True) -> str | None:
    v = body.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v:
        raise WriteRefused(422, f"`{key}` must be a non-empty string")
    return v


def events_for(rest: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    which = rest.strip("/")
    if which not in ("opened", "ended"):
        raise WriteRefused(404, "a session report is POST /sessions/opened or /sessions/ended")
    role, sid = _str(body, "role"), _str(body, "session_id")
    text = _str(body, "step", required=False)
    try:
        step = telemetry.parse_step(text) if text else None
        out = []
        if which == "opened":
            cwd = body.get("cwd") if isinstance(body.get("cwd"), str) else None
            out.append(telemetry.opened_event(source="cli", role=role or "", session_id=sid or "", cwd=cwd))
        if step is not None:
            more: dict[str, Any] = {}
            code = body.get("exit_code")
            if which == "ended" and isinstance(code, int) and not isinstance(code, bool):
                more["exit_code"] = code
            out.append(
                telemetry.step_event(
                    "started" if which == "opened" else "ended",
                    source="cli",
                    role=role or "",
                    session_id=sid or "",
                    step=step,
                    **more,
                )
            )
    except ValueError as e:
        raise WriteRefused(422, str(e)) from e
    return out


def handle(ledger: Ledger, rest: str, body: dict[str, Any]) -> dict[str, Any]:
    events = events_for(rest, body)
    seqs = ledger.append_batch(events) if events else []
    return {"logged": len(seqs), "kinds": [e["kind"] for e in events]}
