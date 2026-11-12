"""Record a session's state from Claude Code's hook payloads, for a reader outside the agents.

`onloop hooks --liveness` prints the hooks that call `onloop liveness record --event E --dir D`. Each call reads the hook's JSON from stdin and
rewrites one small file per session: `<dir>/<session_id>.json`. See docs/design/liveness-and-authority.md.

The recorder must never get in a session's way: it exits 0 whatever it reads, gives up after TIMEOUT_SECONDS, and swallows its own errors (it
appends one line to `<dir>/_errors.log` so a failing recorder is visible to a reader, not silent). It never stores prompt text, only a hash
and a length (of the text with surrounding whitespace removed, so a pasted line matches the hash an ask card carries); a notification's message is kept (cut to MESSAGE_CAP) because it names the dialog, which is the point of recording it.

State, from the event:
    SessionStart                          idle
    UserPromptSubmit                      running (clears blocked_on_prompt)
    Pre/PostToolUse                       running too, if a reader's settings install them; the printed block does not
    Notification of a blocking type       blocked_on_prompt
    Notification of another type          no change; kept as last_notification
    Stop                                  idle (clears blocked_on_prompt)
    SessionEnd                            ended
`since` moves only when the state changes. "Idle with queued work" is never stored: the reader derives it from this file and the ledger.

Scope. User-level hooks fire in every session on the machine. With `--only-under ROOT` (repeatable) the recorder writes only when the
payload's `cwd` is at or under one of the roots; otherwise, and when the payload has no readable cwd, it exits 0 at once: no file, no directory,
no error line, nothing of that session's path anywhere. With no `--only-under` it records every session.

Unverified against a live session (headless runs were not logged in): the exact notification types. BLOCKING_TYPES is the one place to fix.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import traceback
from typing import Any

from . import fsutil
from .events import now_ts

TIMEOUT_SECONDS = 2.0
MESSAGE_CAP = 200
BLOCKING_TYPES = ("permission_prompt", "elicitation_dialog")
EVENT_STATE = {
    "SessionStart": "idle",
    "UserPromptSubmit": "running",
    "PreToolUse": "running",
    "PostToolUse": "running",
    "Stop": "idle",
    "SessionEnd": "ended",
}
_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ERROR_LOG = "_errors.log"
ERROR_LOG_MAX_BYTES = 64 * 1024


def _safe_id(*candidates: Any) -> str | None:
    for c in candidates:
        if isinstance(c, str) and _SAFE.match(c):
            return c
    return None


def build(
    event: str, payload: Any, prior: dict[str, Any] | None, env: dict[str, str], now: str
) -> dict[str, Any]:
    """The new record. Pure: no file, no clock. A payload that is not an object is treated as empty."""
    p = payload if isinstance(payload, dict) else {}
    rec: dict[str, Any] = dict(prior or {})
    rec["session_id"] = (
        _safe_id(p.get("session_id"), env.get("CLAUDE_CODE_SESSION_ID")) or rec.get("session_id") or "unknown"
    )
    host = env.get("CLAUDE_CODE_HOST_SESSION_ID")
    if host:
        rec["host_session_id"] = host
    for key in ("cwd", "transcript_path"):
        if isinstance(p.get(key), str):
            rec[key] = p[key]
    if isinstance(p.get("permission_mode"), str):
        rec["permission_mode"] = p["permission_mode"]
    rec["last_event"] = event
    rec["last_activity"] = now

    state = rec.get("state")
    new_state = state
    if event in EVENT_STATE:
        new_state = EVENT_STATE[event]
    elif event == "Notification":
        ntype = p.get("notification_type")
        msg = p.get("message")
        rec["last_notification"] = {
            "at": now,
            "type": ntype if isinstance(ntype, str) else None,
            "message": msg[:MESSAGE_CAP] if isinstance(msg, str) else None,
        }
        if ntype in BLOCKING_TYPES:
            new_state = "blocked_on_prompt"
    if new_state != state:
        rec["state"] = new_state
        rec["since"] = now
    rec.setdefault("since", now)
    rec.setdefault("state", new_state or "unknown")

    if new_state != "blocked_on_prompt":
        rec.pop("blocked", None)
    elif event == "Notification" and state != "blocked_on_prompt":
        rec["blocked"] = {
            "since": now,
            "tool": p.get("tool_name") if isinstance(p.get("tool_name"), str) else None,
        }

    if event == "UserPromptSubmit":
        prompt = p.get("prompt")
        text = (
            prompt.strip() if isinstance(prompt, str) else ""
        )  # stripped, so a pasted line hashes the same as the card's
        rec["last_prompt"] = {
            "at": now,
            "length": len(text),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        if isinstance(p.get("prompt_id"), str):
            rec["last_prompt"]["prompt_id"] = p["prompt_id"]
    return rec


def _write_atomic(path: str, rec: dict[str, Any]) -> None:
    fsutil.write_atomic(path, json.dumps(rec, indent=2, sort_keys=True) + "\n", newline="\n")


def _log_error(directory: str, event: str) -> None:
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, ERROR_LOG)
        if os.path.exists(path) and os.path.getsize(path) > ERROR_LOG_MAX_BYTES:
            return
        last = traceback.format_exc().strip().splitlines()[-1] if sys.exc_info()[0] else "unknown error"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{now_ts()} {event} {last}\n")
    except Exception:
        pass


def _under(cwd: Any, roots: list[str]) -> bool:
    if not isinstance(cwd, str) or not cwd:
        return False
    here = os.path.normcase(os.path.abspath(cwd))
    for root in roots:
        r = os.path.normcase(os.path.abspath(root))
        if here == r or here.startswith(r.rstrip("\\/") + os.sep):
            return True
    return False


def record(
    event: str,
    directory: str,
    stdin_text: str,
    env: dict[str, str] | None = None,
    now: str | None = None,
    only_under: list[str] | None = None,
) -> str | None:
    """Write the session's record; return its path, or None when the session is outside `only_under`.
    Raises on a failure (the caller swallows it)."""
    env = dict(os.environ) if env is None else env
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
    except ValueError:
        payload = {}
    if only_under and not _under(payload.get("cwd") if isinstance(payload, dict) else None, only_under):
        return None
    sid = (
        _safe_id(
            payload.get("session_id") if isinstance(payload, dict) else None,
            env.get("CLAUDE_CODE_SESSION_ID"),
        )
        or "unknown"
    )
    path = os.path.join(directory, f"{sid}.json")
    prior = None
    try:
        with open(path, encoding="utf-8") as f:
            prior = json.load(f)
    except (OSError, ValueError):
        prior = None
    os.makedirs(directory, exist_ok=True)
    _write_atomic(
        path, build(event, payload, prior if isinstance(prior, dict) else None, env, now or now_ts())
    )
    return path


def main_record(event: str, directory: str, only_under: list[str] | None = None) -> int:
    """The hook entry point: always 0, and gone after TIMEOUT_SECONDS whatever it is doing."""
    guard = threading.Timer(TIMEOUT_SECONDS, lambda: os._exit(0))
    guard.daemon = True
    guard.start()
    try:
        text = ""
        try:
            text = sys.stdin.read()
        except Exception:
            text = ""
        record(event, directory, text, only_under=only_under)
    except BaseException:
        _log_error(directory, event)
    finally:
        guard.cancel()
    return 0
