"""onloopd's message route: a person's message to a carried role (decision 0011).

    POST /messages/<role>     body {"text": "...", "step": "node:a1"?}; from `onloop say` (the write token) or the
                              compose box
                              (`/ui/messages`, the page's session cookie and CSRF token; server.py checks those)

A message is evidence, not authority: it is a request to the role, stored as the person's (author `human`) with its
channel (`cli` or `ui`) and shown as "local user, not verified". It becomes one delivery of kind `message`. Whether
it is delivered or only logged as `would.deliver` is the carrier's decision: a role that has not opted in to
`message`, or a carrier that is not started, sends nothing.

A message may name the step it is about (`onloop say ROLE --step node:a1`, as `onloop open --step` does): the carried
turn that delivers it is then recorded as that step, so its cost is the step's by record, not by rule E.
"""

from __future__ import annotations

import re
from typing import Any

from .. import telemetry
from ..carrier import deliveries, messages
from ..overlay.ledger import Ledger
from .server import WriteRefused

_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
CHANNELS = ("cli", "ui")
DEFAULT_RETENTION_S = 30 * 86400


def make(retention_s: int = DEFAULT_RETENTION_S):
    def handle(ledger: Ledger, rest: str, body: dict[str, Any]) -> dict[str, Any]:
        role = rest.strip("/")
        if not _ROLE.match(role):
            raise WriteRefused(404, "a message goes to /messages/<role>")
        if not ledger.select("SELECT 1 FROM role_sessions WHERE id = ?", (role,)):
            raise WriteRefused(404, f"{role} is not a carried role (no carrier.roles.{role} in onloopd.yaml)")
        channel = body.get("channel", "cli")
        if channel not in CHANNELS:
            raise WriteRefused(422, "channel is cli or ui")
        step = body.get("step")
        if step is not None:
            try:
                kind, ref = telemetry.parse_step(step if isinstance(step, str) else "")
            except ValueError as e:
                raise WriteRefused(422, str(e)) from e
            step = f"{kind}:{ref}"
        try:
            text = messages.check(body.get("text"))
        except messages.MessageRefused as e:
            raise WriteRefused(422, str(e)) from e
        did = deliveries.create(
            ledger,
            role=role,
            kind="message",
            body=text,
            retention_s=retention_s,
            author="human",
            channel=channel,
            origin="human",
            source="cli" if channel == "cli" else "ui",
            step=step,
        )
        opted = ledger.select("SELECT opted_in FROM role_sessions WHERE id = ?", (role,))[0]["opted_in"]
        return {"delivery": did, "role": role, "opted_in": "message" in opted, "verified": False}

    return handle
