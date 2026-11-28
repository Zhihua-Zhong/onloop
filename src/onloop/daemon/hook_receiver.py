"""The hook receiver: Claude Code's Stop, SessionStart, UserPromptSubmit and Notification hooks, posted by
`onloop daemon hook`.

    POST /hooks/stop | /hooks/session-start | /hooks/prompt-submit | /hooks/notification
    body: {"role": "<role name>", "payload": <the hook's JSON, as Claude Code passed it on stdin>}
    answer: {"logged", "seq", "id", "hook_output"?}; `onloop daemon hook` prints `hook_output` for Claude Code

**What the agent is told (0.7.0, one decision, one ask; the hook contract verified in the 0.7.0 spike).** At a
SessionStart or a UserPromptSubmit, `hook_output` adds the role's context: the person's answers that have not reached
it yet (the same event marks them delivered) and its decisions still open, so it does not ask again. At a Stop whose
outcome line asks the person something already open with no new fact, or asks without saying what it changes,
`hook_output` sends the turn back once (`decision: block`) with the reason; a Stop that already carries
`stop_hook_active` never is. The ask is logged either way. A UserPromptSubmit keeps the prompt's length, never its
text.

Each becomes one ledger event (`hook.stop`, `hook.session_start`, `hook.notification`) with `source = "hook"`, the role
as its subject, and the ledger's own time. A Stop is parsed for its outcome line (decision 0007) here, once: the event
keeps the parsed outcome or the parse error, and the outcome line itself, but **never the message text** (the same rule
as the liveness recorder: onloop keeps what it needs to judge a turn, not what was said).

Version 2 of the line (decision 0012) adds the ask keys. They are frozen in the event as `ask`, with `days=<n>` turned
into `cost_from`, a date: the calendar day of the ledger's own time at ingest, in `kpi.timezone`, plus n. The key as
written is kept as `cost_given`. Replay reads the frozen date and never converts again, so a later day or a changed
zone cannot move it.

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


def ev_now() -> str:
    from ..events import now_ts

    return now_ts()


EVENTS = {
    "stop": "hook.stop",
    "session-start": "hook.session_start",
    "prompt-submit": "hook.prompt_submit",
    "notification": "hook.notification",
}
HOOK_NAMES = {"session-start": "SessionStart", "prompt-submit": "UserPromptSubmit"}
BLOCKING_NOTIFICATIONS = ("permission_prompt", "elicitation_dialog")
MESSAGE_CAP = 200
_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_CLAIMED = ("timestamp", "at", "time", "ts")


def _text(payload: dict[str, Any], key: str, cap: int = 300) -> str | None:
    v = payload.get(key)
    return v[:cap] if isinstance(v, str) else None


def event_for(name: str, body: dict[str, Any], now: str | None = None) -> dict[str, Any]:
    """The ledger event for one hook post. Pure but for `now` (the ledger's time, which dates a `days=<n>`): raises
    WriteRefused for a body it cannot take."""
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
        out["grammar"] = disposition.GRAMMAR
        if isinstance(got, disposition.Outcome):
            out["outcome"] = {"value": got.value, "ref": got.ref, "owner": got.owner, "waker": got.waker}
            if got.goal is not None and got.value in ("done", "continue"):
                out["outcome"]["goal"] = got.goal
            if got.resolves is not None:
                out["outcome"]["resolves"] = got.resolves
            if got.value in disposition.ASK_VALUES:
                ask = disposition.frozen_ask(got, now or ev_now())
                if ask:
                    out["ask"] = ask
            out["line"] = got.line
        else:
            out["parse_error"] = got.reason
            out["line"] = got.line if got.line and got.line.startswith("outcome:") else None
    elif name == "session-start":
        out["start_source"] = _text(payload, "source", 40)
    elif name == "prompt-submit":
        prompt_text = payload.get("prompt")
        out["prompt_id"] = prompt
        out["prompt_chars"] = len(prompt_text) if isinstance(prompt_text, str) else None  # never the text
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


def ingest_spool(ledger: Ledger, state: Any) -> int:
    """Log what `onloop daemon hook` spooled while onloopd was away, then empty the spool. Each line is logged under
    its hook's own event id, so a line logged before (a crash between logging and emptying) is skipped. A line that
    cannot be read is kept in `hook-spool.bad.jsonl`, never dropped silently. Returns the events logged."""
    import json
    import pathlib

    from .hook_client import SPOOL

    path = pathlib.Path(state) / SPOOL
    if not path.exists():
        return 0
    work = path.with_suffix(".ingesting")
    if not work.exists():
        path.replace(work)
    logged, bad = 0, []
    for line in work.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            event = event_for(rec["event"], rec["body"], ledger.now())
            event.setdefault("payload", {})["spooled_at"] = rec.get("at")
            if ledger.append(event) is not None:
                logged += 1
        except (ValueError, KeyError, TypeError, WriteRefused):
            bad.append(line)
    if bad:
        with open(work.with_name("hook-spool.bad.jsonl"), "a", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(bad) + "\n")
    work.unlink()
    return logged


def handle(ledger: Ledger, rest: str, body: dict[str, Any]) -> dict[str, Any]:
    """The `/hooks/` route. Inside onloopd's writer lock: what the agent is told is read just before its event is
    logged, so the answers named in its context are the ones that event marks delivered."""
    from .. import decisions

    event = event_for(rest, body, ledger.now())
    role = event["payload"]["role"]
    hook_output: dict[str, Any] | None = None
    try:
        if rest == "stop":
            reason = decisions.stop_feedback(ledger, event)
            if reason:
                hook_output = {"decision": "block", "reason": reason}
        elif rest in HOOK_NAMES:
            text = decisions.context(ledger, role, ledger.now())
            if text:
                hook_output = {
                    "hookSpecificOutput": {"hookEventName": HOOK_NAMES[rest], "additionalContext": text}
                }
    except Exception:  # noqa: BLE001 - what the agent is told is advice; the hook's event is logged regardless
        hook_output = None
    seq = ledger.append(event)  # one transaction, inside onloopd's writer lock
    out: dict[str, Any] = {"logged": seq is not None, "seq": seq, "id": event["id"]}
    if hook_output and seq is not None:
        out["hook_output"] = hook_output
    return out
