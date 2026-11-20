"""Telemetry for the sessions onloop starts (decision 0019, §1 and §2): their own OTel environment, and their steps.

    env(base, port=, token=, role=)      the environment of a session onloop starts: `base` with every inherited OTel
                                         selector removed, and onloop's own set: OTLP/HTTP JSON to onloopd on the
                                         loopback address, with its token, onloop's role as the one resource attribute,
                                         and prompt, response and tool content off
    step_event(...) / opened_event(...)  `step.started`, `step.ended` and `session.opened`, as ledger events

**Per process.** Nothing here touches a settings file. The environment reaches only the process onloop starts. With
`--setting-sources project,local` the user file (`~/.claude/settings.json`) and its OTel keys never load. Spike S4
found that a local file's `none` does not stop a session whose own environment selects `otlp`, and that it still stops
a session without it. So onloop's own folders, which set `none`, export only from the sessions onloop starts.

**Steps.** A step is what a session works on: a task, a PR, a branch or a node, as `step:<kind>:<ref>` (decision 0019's
OCEL object id). onloop records it when it dispatches the work, so no rule has to guess it (§2).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from collections.abc import Mapping
from typing import Any

HOST = "127.0.0.1"  # scan:allow - onloopd's loopback address
HEARTBEAT_FILE = (
    "onloopd.heartbeat.json"  # the daemon's own names (daemon/main.py, daemon/token.py), tested equal
)
TOKEN_FILE = "onloopd.token"
SETTING_SOURCES = "project,local"
STEP_KINDS = ("task", "pr", "branch", "node", "item", "run")
_STEP_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._#/:-]{0,119}$")
_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_SESSION = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")

# Removed before onloop's own are set, with every OTEL_* name: an endpoint, a header or a content switch from the
# parent never reaches the child.
_CLEARED = ("CLAUDE_CODE_ENABLE_TELEMETRY",)


def target(state: pathlib.Path) -> tuple[int, str] | None:
    """The running onloopd's port and token, from the files it keeps next to its ledger; None when either is missing."""
    try:
        port = int(json.loads((state / HEARTBEAT_FILE).read_text(encoding="utf-8"))["port"])
        token = (state / TOKEN_FILE).read_text(encoding="utf-8").strip()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return (port, token) if token else None


def for_session(base: Mapping[str, str], state: pathlib.Path | None, role: str) -> dict[str, str]:
    """A started session's environment: onloop's OTel environment toward the onloopd at `state`, or, when none runs
    there (or `state` is None), `base` with no OTel variable at all, so the session exports nothing."""
    to = target(state) if state is not None else None
    if to is None or not _ROLE.match(role):
        return without(base)
    return env(base, port=to[0], token=to[1], role=role)


def without(base: Mapping[str, str]) -> dict[str, str]:
    """`base` less every OTel variable: a session started with it and no OTel key in its setting sources exports
    nothing."""
    return {k: v for k, v in base.items() if not k.upper().startswith("OTEL_") and k.upper() not in _CLEARED}


def env(base: Mapping[str, str], *, port: int, token: str, role: str) -> dict[str, str]:
    """`base`, less every OTel variable, plus onloop's own: logs to onloopd only, metrics and traces off."""
    if not _ROLE.match(role):
        raise ValueError(f"not a role name: {role!r}")
    out = without(base)
    out.update(
        {
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_METRICS_EXPORTER": "none",  # onloopd stores no metrics: api_request carries the same counts
            "OTEL_TRACES_EXPORTER": "none",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
            "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://{HOST}:{int(port)}",
            "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Bearer {token}",
            "OTEL_RESOURCE_ATTRIBUTES": f"onloop.role={role}",
            "OTEL_LOGS_EXPORT_INTERVAL": "5000",
            "OTEL_LOG_USER_PROMPTS": "0",
            "OTEL_LOG_TOOL_DETAILS": "0",
            "OTEL_METRICS_INCLUDE_ACCOUNT_UUID": "false",
        }
    )
    return out


def parse_step(text: str) -> tuple[str, str]:
    """`kind:ref`, as `task:t-12`, `pr:7`, `branch:feat/x` or `node:a1`. Raises ValueError."""
    kind, sep, ref = (text or "").partition(":")
    if not sep or kind not in STEP_KINDS or not _STEP_REF.match(ref):
        raise ValueError(
            f"a step is KIND:REF with KIND one of {', '.join(STEP_KINDS)}, as task:t-12, pr:7 or branch:feat/x; "
            f"not {text!r}"
        )
    return kind, ref


def _eid(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _check(role: str, session_id: str) -> None:
    if not _ROLE.match(role or ""):
        raise ValueError(f"not a role name: {role!r}")
    if not _SESSION.match(session_id or ""):
        raise ValueError(f"not a session id: {session_id!r}")


def step_event(
    which: str, *, source: str, role: str, session_id: str, step: tuple[str, str], **more: Any
) -> dict[str, Any]:
    """`step.started` or `step.ended` (`which`), on the step's own object. Its id is the step, the session and which,
    so the same report twice is logged once."""
    if which not in ("started", "ended"):
        raise ValueError(f"a step is started or ended, not {which!r}")
    _check(role, session_id)
    kind, ref = parse_step(f"{step[0]}:{step[1]}")
    subject = f"step:{kind}:{ref}"
    return {
        "id": _eid("step", which, subject, session_id),
        "source": source,
        "kind": f"step.{which}",
        "subject_ref": subject,
        "actor": role,
        "payload": {"role": role, "session_id": session_id, "step": {"kind": kind, "ref": ref}, **more},
    }


def opened_event(*, source: str, role: str, session_id: str, cwd: str | None = None) -> dict[str, Any]:
    """`session.opened`: `onloop open <role>` started an interactive session for the role."""
    _check(role, session_id)
    return {
        "id": _eid("session.opened", session_id),
        "source": source,
        "kind": "session.opened",
        "subject_ref": f"session:{session_id}",
        "actor": role,
        "payload": {"role": role, "session_id": session_id, "cwd": cwd},
    }
