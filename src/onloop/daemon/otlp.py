"""onloopd's OTLP receiver: Claude Code's own telemetry, OTLP/HTTP with JSON encoding (decision 0019, §1 and §5).

    POST /v1/logs       Claude Code's events. Three of them become ledger events with `source = "otel"`:
                        api_request -> `cost.request`, user_prompt -> `turn.prompted`, tool_result -> `tool.ended`
    POST /v1/metrics    answered and stored nowhere: the counters repeat what `api_request` already carries

Both come through the server's write path, so they need onloopd's token (`OTEL_EXPORTER_OTLP_HEADERS` carries it as
`Authorization=Bearer <token>` for a session onloop starts), `Content-Type: application/json` and a body of at most
1 MiB. A compressed body is refused (415). A body that is not an OTLP export request is answered (400) and stored
nowhere. The answer to a good one is OTLP's own: `{"partialSuccess": {}}`.

**Identity is dropped on arrival.** Claude Code sends `user.email`, `organization.id`, `user.account_uuid`,
`user.account_id` and `user.id` when signed in. They are removed from every attribute list of the parsed body before
anything else reads it. Beyond that, each kind keeps only the fields it names (`FIELDS`), so a prompt's text, a
response, a tool's parameters or any attribute Claude Code adds later is never written. Strings are capped.

**Time.** An event's time is the ledger's own receive time. The time Claude Code claims (`event.timestamp`) is kept in
the payload as `claimed_at`, never used as the event's time (story 6). Claude Code exports in batches, every few
seconds, so the two are seconds apart.

**Subject and actor.** The subject is the session, `session:<id>` (decision 0019's OCEL object id). The actor is the
onloop role, when the session's resource attributes name one as `onloop.role` (onloop sets it when it starts the
session); otherwise none. A record without a session id cannot be placed and is skipped.

**Redelivery.** An exporter retries a batch it thinks failed, so each event's id is derived from what is unique to it,
and a batch delivered twice is logged once. A model request is keyed on its `request_id`. Any other record is keyed
on its session, its `prompt.id` (else the carried run's id, `onloop.run`) and `event.sequence`: Claude Code numbers events per process, and a carried turn is a
new process resuming the same session (`claude -p --resume`), so the sequence starts again at each run; without the
prompt id a resumed run's events took an earlier run's ids and were dropped as redelivered (the dogfood's round 2,
2026-11-22). With neither, the session and sequence are the key, as before.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from .. import telemetry
from ..overlay.ledger import Ledger
from . import receipts
from .server import WriteRefused

# Identity attributes Claude Code sends when signed in with a Claude account (S1); dropped before anything reads them.
DROP = frozenset({"user.email", "organization.id", "user.account_uuid", "user.account_id", "user.id"})

KIND_OF = {"api_request": "cost.request", "user_prompt": "turn.prompted", "tool_result": "tool.ended"}

# Per kind, the payload field and the OTLP attribute it comes from, with its type. Nothing else is kept.
FIELDS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "cost.request": (
        ("prompt_id", "prompt.id", "str"),
        ("model", "model", "str"),
        ("input_tokens", "input_tokens", "int"),
        ("output_tokens", "output_tokens", "int"),
        ("cache_read_tokens", "cache_read_tokens", "int"),
        ("cache_creation_tokens", "cache_creation_tokens", "int"),
        ("duration_ms", "duration_ms", "int"),
        ("request_id", "request_id", "str"),
        ("query_source", "query_source", "str"),
        (
            "cost_usd",
            "cost_usd",
            "float",
        ),  # Claude Code's estimate at list price; on a subscription, not billing
    ),
    "turn.prompted": (
        ("prompt_id", "prompt.id", "str"),
        ("length", "prompt_length", "int"),  # its length only, never its text
    ),
    "tool.ended": (
        ("prompt_id", "prompt.id", "str"),
        ("tool_name", "tool_name", "str"),
        ("success", "success", "bool"),
        ("duration_ms", "duration_ms", "int"),  # never its parameters
    ),
}
STR_CAP = 120
_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_SESSION = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_INT = re.compile(r"^-?\d{1,18}$")


def scrub(value: Any) -> int:
    """Remove every attribute whose key is in DROP from every `attributes` list, at any depth. Returns how many."""
    dropped = 0
    if isinstance(value, dict):
        attrs = value.get("attributes")
        if isinstance(attrs, list):
            kept = [a for a in attrs if not (isinstance(a, dict) and a.get("key") in DROP)]
            dropped += len(attrs) - len(kept)
            value["attributes"] = kept
        for v in value.values():
            dropped += scrub(v)
    elif isinstance(value, list):
        for v in value:
            dropped += scrub(v)
    return dropped


def _attrs(node: Any) -> dict[str, Any]:
    """An OTLP attribute list as {key: plain value}; arrays, maps and bytes are left out."""
    out: dict[str, Any] = {}
    attrs = node.get("attributes") if isinstance(node, dict) else None
    for a in attrs if isinstance(attrs, list) else ():
        if (
            not isinstance(a, dict)
            or not isinstance(a.get("key"), str)
            or not isinstance(a.get("value"), dict)
        ):
            continue
        v = a["value"]
        for t in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if t in v:
                out[a["key"]] = v[t]
                break
    return out


def _typed(v: Any, typ: str) -> Any:
    if typ == "str":
        return v[:STR_CAP] if isinstance(v, str) else None
    if typ == "int":
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str) and _INT.match(v):
            return int(
                v
            )  # OTLP/JSON writes a 64-bit integer as a string; Claude Code writes some counts as text
        return None
    if typ == "float":
        if isinstance(v, bool):
            return None
        if isinstance(v, int | float):
            return float(v)
        try:
            return float(v) if isinstance(v, str) else None
        except ValueError:
            return None
    if isinstance(v, bool):
        return v
    return {"true": True, "false": False}.get(v) if isinstance(v, str) else None


def _list(node: Any, key: str) -> list[Any]:
    v = node.get(key) if isinstance(node, dict) else None
    if v is None:
        return []
    if not isinstance(v, list):
        raise WriteRefused(400, f"not an OTLP export request: `{key}` is not a list")
    return v


def events_for(signal: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    """The ledger events one OTLP export request carries. Pure: scrubs `body` in place first. Raises WriteRefused."""
    if signal == "metrics":
        if "resourceMetrics" not in body:
            raise WriteRefused(400, "not an OTLP metrics export request: no `resourceMetrics`")
        _list(body, "resourceMetrics")
        scrub(body)
        return []
    if signal != "logs":
        raise WriteRefused(404, f"no OTLP signal {signal!r}; onloopd takes /v1/logs and /v1/metrics")
    if "resourceLogs" not in body:
        raise WriteRefused(400, "not an OTLP logs export request: no `resourceLogs`")
    resources = _list(body, "resourceLogs")
    scrub(body)
    out: list[dict[str, Any]] = []
    for rl in resources:
        res = _attrs(rl.get("resource") if isinstance(rl, dict) else None)
        role = res.get("onloop.role")
        role = role if isinstance(role, str) and _ROLE.match(role) else None
        run = res.get("onloop.run")  # a carried run's id, set by onloop in the run's environment
        run = run if isinstance(run, str) and telemetry.RUN_ID.match(run) else None
        for sl in _list(rl, "scopeLogs"):
            for rec in _list(sl, "logRecords"):
                event = _event(rec, role, run)
                if event is not None:
                    out.append(event)
    return out


def _event(rec: Any, role: str | None, run: str | None = None) -> dict[str, Any] | None:
    if not isinstance(rec, dict):
        return None
    a = _attrs(rec)
    name = a.get("event.name")
    if not isinstance(name, str):
        body = rec.get("body")
        name = body.get("stringValue") if isinstance(body, dict) else None
    if not isinstance(name, str):
        return None
    kind = KIND_OF.get(name.removeprefix("claude_code."))
    session = a.get("session.id")
    if kind is None or not isinstance(session, str) or not _SESSION.match(session):
        return None
    payload: dict[str, Any] = {"session_id": session, "role": role}
    if run:
        payload["run_id"] = run
    for field, attr, typ in FIELDS[kind]:
        payload[field] = _typed(a.get(attr), typ)
    claimed = _typed(a.get("event.timestamp"), "str")
    if claimed:
        payload["claimed_at"] = claimed  # kept apart; the event's time is the ledger's own
    seq = _typed(a.get("event.sequence"), "int")
    if seq is not None:
        payload["sequence"] = seq
    eid = _event_id(kind, session, payload, seq)
    return {
        "id": eid,
        "source": "otel",
        "kind": kind,
        "subject_ref": f"session:{session}",
        "actor": role,
        "payload": payload,
    }


def _event_id(kind: str, session: str, payload: dict[str, Any], seq: int | None) -> str:
    """What is unique to one record, so a redelivered batch is logged once and a resumed run's records are not taken for
    an earlier run's (the sequence starts again in each process; see "Redelivery")."""
    if kind == "cost.request" and payload.get("request_id"):
        parts: tuple[str, ...] = ("otel", kind, "request", str(payload["request_id"]))
    elif seq is not None and payload.get("prompt_id"):
        parts = ("otel", kind, session, "prompt", str(payload["prompt_id"]), str(seq))
    elif seq is not None and payload.get("run_id"):
        parts = ("otel", kind, session, "run", str(payload["run_id"]), str(seq))
    elif seq is not None:
        parts = ("otel", kind, session, str(seq))
    else:
        return f"otel-{uuid.uuid4().hex}"  # nothing stable to key it on: each record is its own event
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _records(signal: str, body: dict[str, Any]) -> int:
    """How many log records the body holds, kept or not."""
    if signal != "logs":
        return 0
    return sum(
        len(_list(sl, "logRecords")) for rl in _list(body, "resourceLogs") for sl in _list(rl, "scopeLogs")
    )


def handle(ledger: Ledger, rest: str, body: dict[str, Any]) -> dict[str, Any]:
    """The `/v1/` write route: `rest` is `logs` or `metrics`. Logs every event of the request in one transaction, and
    reports how many records came, were kept, were stored, and were already logged (`receipts`)."""
    signal = rest.strip("/")
    events = events_for(signal, body)
    stored = len(ledger.append_batch(events)) if events else 0
    counts = {"received": _records(signal, body), "kept": len(events), "stored": stored}
    counts["duplicate"] = len(events) - stored
    return {"partialSuccess": {}, receipts.RECEIPT: counts}
