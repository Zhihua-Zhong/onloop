"""`onloop daemon hook`: what a role's Claude Code hook runs. It posts the hook's payload to onloopd and never fails.

    onloop daemon hook --event stop|session-start|notification --role ROLE [--ledger FILE] [--timeout 2]

It reads the hook's JSON from stdin, adds the role, and posts it to onloopd on the loopback address, using the port in
onloopd's heartbeat and the token in `onloopd.token`, both next to the ledger. Whatever happens (onloopd down, a
refusal, a payload it cannot read) it exits 0 within the timeout and appends one line to `hook-errors.log` there, so
a broken onloop can never stop or slow a session beyond that.

A payload that is not valid JSON is still posted, as `{"unreadable": "<why>"}`: the Stop then records a turn without
a valid outcome (story 8), instead of nothing.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
from typing import Any, BinaryIO

from .. import events as ev
from . import server as srv
from . import token as token_mod

ERROR_LOG = "hook-errors.log"
ERROR_LOG_MAX_BYTES = 64 * 1024


def _payload(raw: bytes) -> dict[str, Any]:
    try:
        obj = json.loads(raw.decode("utf-8", errors="strict"))
    except UnicodeDecodeError:
        return {"unreadable": "the hook's payload is not valid UTF-8"}
    except ValueError:
        return {"unreadable": "the hook's payload is not JSON"}
    return obj if isinstance(obj, dict) else {"unreadable": "the hook's payload is not a JSON object"}


def _log(state: pathlib.Path, line: str) -> None:
    try:
        path = state / ERROR_LOG
        if path.exists() and path.stat().st_size > ERROR_LOG_MAX_BYTES:
            path.replace(path.with_suffix(".log.1"))
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{ev.now_ts()} {line}\n")
    except OSError:
        pass


def post(event: str, role: str, state: pathlib.Path, stdin: BinaryIO, timeout: float = 2.0) -> int:
    """Post one hook event. Always returns 0."""
    start = time.monotonic()
    try:
        raw = stdin.read()
        body = json.dumps({"role": role, "payload": _payload(raw)}).encode("utf-8")
        beat = json.loads((state / "onloopd.heartbeat.json").read_text(encoding="utf-8"))
        secret = token_mod.read(state)
        if not secret:
            raise RuntimeError(f"no token in {state}; is onloopd running?")
        req = urllib.request.Request(
            f"http://{srv.HOST}:{int(beat['port'])}/hooks/{event}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {secret}"},
        )
        left = max(0.1, timeout - (time.monotonic() - start))
        with urllib.request.urlopen(req, timeout=left) as r:  # noqa: S310 - the loopback address only
            r.read()
    except urllib.error.HTTPError as e:
        _log(state, f"{event} {role}: onloopd answered {e.code}: {e.read()[:200]!r}")
    except Exception as e:  # noqa: BLE001 - a hook must never fail a session
        _log(state, f"{event} {role}: {type(e).__name__}: {e}"[:300])
    return 0


def main(event: str, role: str, ledger: pathlib.Path, timeout: float) -> int:
    return post(event, role, ledger.resolve().parent, sys.stdin.buffer, timeout)
