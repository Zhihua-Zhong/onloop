"""The `carrier:` block of onloopd.yaml (decision 0010). Off by default: without it, or with no role opted in to an
action, the carrier only logs what it would deliver.

    carrier:
      claude: claude.cmd            optional: the CLI to run (default: `claude.cmd` on Windows, else `claude`)
      wall_per_day: 4h              optional, default 4h: carrier wall time across all roles per day
      message_retention: 30d        optional, default 30d: how long the text onloop stores is kept
      roles:
        builder:                    a role name: lowercase letters, digits, '_' or '-'
          session_id: <uuid>        the role's existing Claude Code session (adopted); onloop never creates one
          cwd: path                 the folder the session runs in (file tools are confined to it)
          tools: [Read, Grep, Edit] the built-in tools the run may see (`--tools`); never a shell
          allowed_tools: [Read]     optional: the patterns pre-approved (`--allowedTools`); default: `tools`
          model: haiku              optional
          max_turns: 20             optional, default 20 (`--max-turns`)
          wall: 20m                 optional, default 20m: one run's wall budget
          silence: 5m               optional, default 5m: the longest stretch with no output
          runs_per_hour: 4          optional, default 4
          runs_per_day: 24          optional, default 24
          wall_per_day: 2h          optional, default 2h
          opt_in: [message]         optional, default none: what onloop may do, from `message` (a person's
                                    message), `event` (a waker fired, a handoff), `corrective` (the one corrective
                                    wake per turn), `dead_waker` (tell the coordinator), `dead_waker_wake` (also tell
                                    the role itself)
      coordinator: lead             optional: the role escalations go to (default: the human)

**No shell.** A role whose `tools` or `allowed_tools` name Bash, PowerShell or any other shell is refused, and
onloopd does not start. With a shell named, read-only commands run without the allowlist even under `--restricted`
(spike R4-S5), so a shell cannot be made safe by the allowlist alone. A future decision will define a safe shell.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Any

from .. import events as ev

ACTIONS = ("message", "event", "corrective", "dead_waker", "dead_waker_wake")
_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TOOL = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\(.*\))?$")
_TOP = ("claude", "wall_per_day", "message_retention", "roles", "coordinator")
_ROLE_KEYS = (
    "session_id",
    "cwd",
    "tools",
    "allowed_tools",
    "model",
    "max_turns",
    "wall",
    "silence",
    "runs_per_hour",
    "runs_per_day",
    "wall_per_day",
    "opt_in",
)
# Tools that run commands: Claude Code's own shells, and the names a shell goes by elsewhere. Compared without case.
SHELL_TOOLS = ("bash", "powershell", "pwsh", "shell", "cmd", "sh", "zsh", "terminal", "localshell", "repl")
SHELL_REFUSAL = (
    "role {role}: {where} names a shell ({tool}). A carried role gets no shell: with a shell named, read-only "
    "commands run without the allowlist even under --restricted (spike R4-S5). A future ADR will define a safe "
    "shell; until then, give this role no shell tool (decision 0010, point 12)"
)


class ShellRefused(ValueError):
    """A carried role names a shell tool."""


def tool_base(pattern: str) -> str:
    return pattern.split("(", 1)[0].strip()


def shell_in(tools: tuple[str, ...]) -> str | None:
    """The first tool in `tools` that is a shell, or None."""
    for t in tools:
        base = tool_base(t)
        if base.lower() in SHELL_TOOLS:
            return base
    return None


def refuse_shell(role: str, tools: tuple[str, ...], allowed: tuple[str, ...]) -> None:
    """Raise ShellRefused when either list names a shell. Called when the config is read and again before a spawn."""
    for where, names in (("tools", tools), ("allowed_tools", allowed)):
        found = shell_in(names)
        if found:
            raise ShellRefused(SHELL_REFUSAL.format(role=role, where=where, tool=found))


@dataclass(frozen=True)
class CarriedRole:
    name: str
    session_id: str
    cwd: str
    tools: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    model: str | None = None
    max_turns: int = 20
    wall_s: int = 1200
    silence_s: int = 300
    runs_per_hour: int = 4
    runs_per_day: int = 24
    wall_per_day_s: int = 7200
    opt_in: tuple[str, ...] = ()


@dataclass(frozen=True)
class CarrierConfig:
    roles: tuple[CarriedRole, ...] = field(default_factory=tuple)
    claude: tuple[str, ...] = ("claude.cmd",) if sys.platform == "win32" else ("claude",)
    wall_per_day_s: int = 4 * 3600
    message_retention_s: int = 30 * 86400
    coordinator: str | None = None

    def role(self, name: str) -> CarriedRole | None:
        return next((r for r in self.roles if r.name == name), None)

    def by_session(self) -> dict[str, CarriedRole]:
        return {r.session_id: r for r in self.roles}


def _duration(raw: Any, default: str, where: str, problems: list[str], minimum: int = 1) -> int:
    try:
        s = ev.parse_duration(raw if raw is not None else default)
    except ev.EventError as e:
        problems.append(f"{where}: {e}")
        return ev.parse_duration(default)
    if s < minimum:
        problems.append(f"{where} must be at least {minimum}s")
    return s


def _count(raw: Any, default: int, where: str, problems: list[str], hi: int = 1000) -> int:
    if raw is None:
        return default
    if not isinstance(raw, int) or isinstance(raw, bool) or not 1 <= raw <= hi:
        problems.append(f"{where} must be a whole number from 1 to {hi}")
        return default
    return raw


def _tools(raw: Any, where: str, problems: list[str]) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(t, str) and _TOOL.match(t.strip()) for t in raw):
        problems.append(f"{where} must be a list of tool names, as Read or Edit(src/**)")
        return ()
    return tuple(t.strip() for t in raw)


def _role(name: Any, raw: Any, problems: list[str]) -> CarriedRole | None:
    where = f"carrier.roles.{name}"
    if not isinstance(name, str) or not _ROLE.match(name):
        problems.append(f"carrier.roles: {name!r} is not a role name (lowercase letters, digits, '_' or '-')")
        return None
    if not isinstance(raw, dict):
        problems.append(f"{where} must be a mapping with session_id, cwd and tools")
        return None
    for key in sorted(set(raw) - set(_ROLE_KEYS)):
        problems.append(f"{where} has an unknown key {key!r}; the keys are {', '.join(_ROLE_KEYS)}")
    sid = raw.get("session_id")
    if not isinstance(sid, str) or not _UUID.match(sid):
        problems.append(f"{where}.session_id must be a session id (a lowercase UUID)")
    cwd = raw.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        problems.append(f"{where}.cwd must be the folder the session runs in")
    tools = _tools(raw.get("tools"), f"{where}.tools", problems)
    allowed = (
        _tools(raw["allowed_tools"], f"{where}.allowed_tools", problems) if "allowed_tools" in raw else tools
    )
    try:
        refuse_shell(name, tools, allowed)
    except ShellRefused as e:
        problems.append(str(e))
    model = raw.get("model")
    if model is not None and (not isinstance(model, str) or not re.match(r"^[A-Za-z0-9._-]{1,60}$", model)):
        problems.append(f"{where}.model must be a model name")
    opt = raw.get("opt_in") or []
    if not isinstance(opt, list) or any(a not in ACTIONS for a in opt):
        problems.append(f"{where}.opt_in must be a list of {', '.join(ACTIONS)}")
        opt = []
    if "dead_waker_wake" in opt and "dead_waker" not in opt:
        problems.append(f"{where}.opt_in: dead_waker_wake needs dead_waker (escalate first)")
    if not isinstance(sid, str) or not _UUID.match(sid) or not isinstance(cwd, str):
        return None
    return CarriedRole(
        name=name,
        session_id=sid,
        cwd=cwd,
        tools=tools,
        allowed_tools=allowed,
        model=model,
        max_turns=_count(raw.get("max_turns"), 20, f"{where}.max_turns", problems, 200),
        wall_s=_duration(raw.get("wall"), "20m", f"{where}.wall", problems, 30),
        silence_s=_duration(raw.get("silence"), "5m", f"{where}.silence", problems, 10),
        runs_per_hour=_count(raw.get("runs_per_hour"), 4, f"{where}.runs_per_hour", problems, 1000),
        runs_per_day=_count(raw.get("runs_per_day"), 24, f"{where}.runs_per_day", problems, 10000),
        wall_per_day_s=_duration(raw.get("wall_per_day"), "2h", f"{where}.wall_per_day", problems, 60),
        opt_in=tuple(sorted(set(opt))),
    )


def parse(raw: Any, problems: list[str]) -> CarrierConfig | None:
    """Check a `carrier:` mapping; append every problem to `problems` (onloopd.yaml is checked whole)."""
    if not isinstance(raw, dict):
        problems.append("carrier must be a mapping with roles")
        return None
    for key in sorted(set(raw) - set(_TOP)):
        problems.append(f"carrier has an unknown key {key!r}; the keys are {', '.join(_TOP)}")
    roles_raw = raw.get("roles") or {}
    if not isinstance(roles_raw, dict):
        problems.append("carrier.roles must be a mapping of role name to its settings")
        roles_raw = {}
    roles = [r for r in (_role(n, v, problems) for n, v in roles_raw.items()) if r]
    seen: dict[str, str] = {}
    for r in roles:
        if r.session_id in seen:
            problems.append(f"carrier.roles: {r.name} and {seen[r.session_id]} name the same session id")
        seen[r.session_id] = r.name
    claude = raw.get("claude")
    if claude is not None and (not isinstance(claude, str) or not claude.strip()):
        problems.append("carrier.claude must be the command that runs Claude Code")
    coordinator = raw.get("coordinator")
    if coordinator is not None and (not isinstance(coordinator, str) or not _ROLE.match(coordinator)):
        problems.append("carrier.coordinator must be a role name")
    out = CarrierConfig(
        roles=tuple(roles),
        wall_per_day_s=_duration(raw.get("wall_per_day"), "4h", "carrier.wall_per_day", problems, 60),
        message_retention_s=_duration(
            raw.get("message_retention"), "30d", "carrier.message_retention", problems, 3600
        ),
        coordinator=coordinator,
    )
    if claude:
        out = CarrierConfig(**{**out.__dict__, "claude": (claude.strip(),)})
    return out
