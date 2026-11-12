"""Who else holds a role's session: `claude agents --json`, filtered to the bound role sessions in **one** function.

`claude agents --json` lists every Claude Code session running on the machine: the role sessions onloop carries, and
also the user's own sessions in other folders, with their folder and their name (spike R4-S2). onloop needs to know
only whether a bound role session is open somewhere, so the raw listing never leaves `held`: nothing about another
session is logged, stored or returned, not even how many there are.

    held(rows, bound) -> {session_id: (Holder, ...)}   the filter; pure; the one place a raw row is read
    read(claude, bound, ...) -> Listing                run the command and filter at once

A `-p` process shows as `kind: "interactive"` with `status` `busy` or `idle`, and its pid is the `claude.exe` under
`cmd.exe` (R4-S2, R4-S3). A listing that cannot be read is reported as unknown, and the carrier spawns nothing
(fail closed): an unread listing must never look like "nobody holds it".
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from .. import procs

TIMEOUT_S = 30.0


@dataclass(frozen=True)
class Holder:
    pid: int | None
    status: str | None  # busy | idle, as the CLI reports it
    kind: str | None  # interactive | background


@dataclass(frozen=True)
class Listing:
    ok: bool
    holders: dict[str, tuple[Holder, ...]] = field(default_factory=dict)
    error: str | None = None  # why it could not be read; never any of the listing's own text

    def held(self, session_id: str, *, besides: Collection[int] = ()) -> tuple[Holder, ...]:
        """The holders of one bound session, leaving out the pids onloop itself started."""
        return tuple(h for h in self.holders.get(session_id, ()) if h.pid not in besides)


def held(rows: Any, bound: Collection[str]) -> dict[str, tuple[Holder, ...]]:
    """The one filter: of the listing's rows, only those whose `sessionId` is a bound role session, and of each such
    row only its pid, status and kind. Every other row is dropped unread."""
    wanted = set(bound)
    out: dict[str, list[Holder]] = {}
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        sid = row.get("sessionId")
        if not isinstance(sid, str) or sid not in wanted:
            continue
        pid = row.get("pid")
        status, kind = row.get("status"), row.get("kind")
        out.setdefault(sid, []).append(
            Holder(
                pid=pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
                status=status if isinstance(status, str) else None,
                kind=kind if isinstance(kind, str) else None,
            )
        )
    return {k: tuple(v) for k, v in out.items()}


def read(
    claude: tuple[str, ...],
    bound: Collection[str],
    *,
    env: Mapping[str, str] | None = None,
    run: Callable[..., Any] = procs.run_text,
) -> Listing:
    """Run `claude agents --json` and keep only what `held` keeps. The raw output is not kept, logged or returned."""
    try:
        p = run(
            [*claude, "agents", "--json"], timeout=TIMEOUT_S, env=dict(env if env is not None else os.environ)
        )
    except (OSError, subprocess.SubprocessError) as e:
        return Listing(ok=False, error=f"claude agents --json did not run: {type(e).__name__}")
    if getattr(p, "returncode", 1) != 0:
        return Listing(ok=False, error=f"claude agents --json exited {getattr(p, 'returncode', '?')}")
    try:
        rows = json.loads(getattr(p, "stdout", "") or "")
    except ValueError:
        return Listing(ok=False, error="claude agents --json printed something that is not JSON")
    if not isinstance(rows, list):
        return Listing(ok=False, error="claude agents --json printed JSON that is not a list")
    return Listing(ok=True, holders=held(rows, bound))
