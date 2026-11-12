"""What stops the carrier before it spends anything (decision 0010; plan section 5).

    kill_switch(state_dir) -> bool          the `carrier.off` file next to the ledger: works even when the ledger is
                                            locked or onloopd is wedged; checked before each spawn and every 5 s of a run
    child_env(environ) -> dict              the run's environment: every CLAUDE* and ANTHROPIC* variable removed
    refuse_metered(environ)                 an API key or auth token in onloopd's environment stops the carrier at start
    Login(claude).check() -> str | None     `claude auth status`, cached 5 min: None when logged in on a subscription,
                                            else why not
    budget_left(ledger, role, cfg, now)     None when the role may run now, else why not (runs per hour and day, wall
                                            per day, and the carrier's wall per day across roles)

onloop never asks for, stores or passes a Claude.ai token (the provider's terms): the child runs on the user's own
login of the unmodified CLI, and `claude auth status` is the only check of it.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
from collections.abc import Callable, Mapping
from typing import Any

from .. import events as ev
from .. import procs
from ..supervisor.step import METERED_KEYS, MeteredKeyRefused
from .config import CarriedRole, CarrierConfig

KILL_FILE = "carrier.off"
LOGIN_CACHE_S = 300.0
_PREFIXES = ("CLAUDE", "ANTHROPIC")

__all__ = [
    "KILL_FILE",
    "Login",
    "MeteredKeyRefused",
    "budget_left",
    "child_env",
    "kill_switch",
    "refuse_metered",
]


def kill_switch(state_dir: pathlib.Path) -> bool:
    return (state_dir / KILL_FILE).exists()


def child_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The environment a run starts with: the parent's, less every CLAUDE* and ANTHROPIC* variable. A desktop or agent
    session exports about 35 of them, and a child that inherits them attaches to that session's machinery."""
    return {k: v for k, v in environ.items() if not k.upper().startswith(_PREFIXES)}


def refuse_metered(environ: Mapping[str, str]) -> None:
    for key in METERED_KEYS:
        if key in environ:
            raise MeteredKeyRefused(
                f"the carrier does not start: {key} is set in onloopd's environment. Roles run on the subscription "
                "only; unset it and start onloopd again."
            )


class Login:
    """`claude auth status` (no model call), asked at most every 5 min."""

    def __init__(
        self,
        claude: tuple[str, ...],
        *,
        env: Mapping[str, str] | None = None,
        run: Callable[..., Any] = procs.run_text,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.claude, self.env, self.run, self.clock = claude, env, run, clock
        self._at: float | None = None
        self._why: str | None = None

    def forget(self) -> None:
        self._at = None

    def check(self) -> str | None:
        now = self.clock()
        if self._at is not None and now - self._at < LOGIN_CACHE_S:
            return self._why
        self._at, self._why = now, self._ask()
        return self._why

    def _ask(self) -> str | None:
        env = child_env(self.env if self.env is not None else os.environ)
        try:
            p = self.run([*self.claude, "auth", "status"], timeout=60, env=env)
            got = json.loads((p.stdout or "").strip() or "{}")
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            return f"`claude auth status` could not be read ({type(e).__name__})"
        if not isinstance(got, dict) or got.get("loggedIn") is not True:
            return "`claude auth status` says the CLI is not logged in; log in with `claude` then `/login`"
        method = str(got.get("authMethod") or "").lower()
        if method in ("api_key", "apikey", "none", ""):
            return f"`claude auth status` reports the login method {method or 'none'!r}, not a subscription login"
        return None


def _since(ledger: Any, role: str | None, since: str) -> tuple[int, float]:
    where, args = ("AND role = ?", (role,)) if role else ("", ())
    row = ledger.select(
        f"SELECT COUNT(*) AS n, COALESCE(SUM(wall_s), 0) AS wall FROM runs WHERE started_at >= ? {where}",
        (since, *args),
    )[0]
    return int(row["n"]), float(row["wall"])


def budget_left(
    ledger: Any, role: CarriedRole, cfg: CarrierConfig, now: str, *, rate_factor: float = 1.0
) -> str | None:
    """None when `role` may start a run now; else the reason, with when it frees up in the reason's words."""
    hour, _ = _since(ledger, role.name, ev.shift_ts(now, -3600))
    per_hour = max(1, int(role.runs_per_hour * rate_factor))
    if hour >= per_hour:
        return f"budget: {hour} of {per_hour} runs in the last hour"
    day, wall = _since(ledger, role.name, ev.shift_ts(now, -86400))
    if day >= role.runs_per_day:
        return f"budget: {day} of {role.runs_per_day} runs in the last day"
    if wall >= role.wall_per_day_s:
        return f"budget: {int(wall)} s of {role.wall_per_day_s} s of wall time in the last day"
    _, total = _since(ledger, None, ev.shift_ts(now, -86400))
    if total >= cfg.wall_per_day_s:
        return f"budget: the carrier used {int(total)} s of its {cfg.wall_per_day_s} s across roles in the last day"
    return None
