"""Print the hook configuration that weaves anchors into an interactive session (Claude Code's `settings.json` hooks).

`onloop hooks --role R` only prints. It writes no file: the person who owns the settings pastes it, so the hooks are
reviewed like any other change to what an agent is told. Each hook calls `onloop anchor`, which reads files, so the
anchor a session sees is always the current one.

| Moment | Hook | Anchor |
|---|---|---|
| session start | `SessionStart` | full |
| every incoming message | `UserPromptSubmit`, only if the role's `anchor` is `every_message` | short |
| before a write or a merge | `PreToolUse` on write-like tools | hardstop |
| before ending a turn | `Stop` | stop |

`liveness_config` is separate: it prints the hooks that record a session's state (running, idle, blocked on a prompt)
for a reader outside the agents. It carries no anchor and sends nothing; see docs/design/liveness-and-authority.md.
"""

from __future__ import annotations

import shlex
from typing import Any

from . import anchor as anchor_mod
from . import policy as pol

WRITE_TOOLS = "Bash|Write|Edit|MultiEdit|NotebookEdit"
MODES = ("session", "every_message")


def _hook(command: str) -> list[dict[str, Any]]:
    return [{"hooks": [{"type": "command", "command": command}]}]


def config(
    policy: pol.Policy,
    role: str,
    *,
    command: str = "onloop",
    policy_path: str = pol.POLICY_FILE,
    ledger_path: str = "ledger.yaml",
) -> dict[str, Any]:
    spec = anchor_mod.role_spec(policy, role)
    mode = spec.get("anchor", "session")
    if mode not in MODES:
        raise ValueError(f"role {role!r} has anchor {mode!r}; use {' or '.join(MODES)}")

    def call(kind: str) -> str:
        return " ".join(
            [
                command,
                "anchor",
                "--role",
                shlex.quote(role),
                "--kind",
                kind,
                "--policy",
                shlex.quote(policy_path),
                "--ledger",
                shlex.quote(ledger_path),
            ]
        )

    hooks: dict[str, Any] = {"SessionStart": _hook(call("full"))}
    if mode == "every_message":
        hooks["UserPromptSubmit"] = _hook(call("short"))
    hooks["PreToolUse"] = [
        {"matcher": WRITE_TOOLS, "hooks": [{"type": "command", "command": call("hardstop")}]}
    ]
    hooks["Stop"] = _hook(call("stop"))
    return {"hooks": hooks}


# No per-tool-call hook (Lead, 2026-10-29): a process spawn is about half a second on this host, which across ten sessions costs more than it buys.
# A blocked prompt therefore clears at the next UserPromptSubmit or Stop, not at the next tool call. SessionStart and SessionEnd fire once per session.
LIVENESS_EVENTS = ("SessionStart", "UserPromptSubmit", "Notification", "Stop", "SessionEnd")
LIVENESS_DIR = "liveness"


def liveness_config(
    *, command: str = "onloop", directory: str = LIVENESS_DIR, only_under: list[str] | None = None
) -> dict[str, Any]:
    """The hooks block that records session state. Every hook calls the same reader of stdin, `liveness record`, which
    takes the session id and the event from the payload and the app's own id from $CLAUDE_CODE_HOST_SESSION_ID."""

    def call(event: str) -> str:
        parts = [command, "liveness", "record", "--event", event, "--dir", shlex.quote(directory)]
        for root in only_under or []:
            parts += ["--only-under", shlex.quote(root)]
        return " ".join(parts)

    return {"hooks": {event: _hook(call(event)) for event in LIVENESS_EVENTS}}
