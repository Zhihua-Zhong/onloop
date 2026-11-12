"""onloopd's write token: drawn at every start, written next to the ledger, and checked for who else can read it.

The token keeps other local programs and web pages off onloopd's write routes. It is **not** a boundary against the
agents: they run as the same OS user and can read the file (decision 0008). So a write that carries it is evidence of
what a session or a scheduler said, never an authority to act.

    issue(state_dir) -> (token, warning | None)   write a fresh token; warn if the file is readable by others
    read(state_dir) -> token | None               what `onloop hook post` sends
    readable_by_others(path) -> str | None        the warning, or None when only the user (and the system) can read it
"""

from __future__ import annotations

import getpass
import os
import pathlib
import secrets
import stat
import sys
from collections.abc import Callable

from .. import fsutil, procs

TOKEN_FILE = "onloopd.token"
# Principals Windows grants on a user's own files by default; access by any other is worth a warning.
_EXPECTED = ("NT AUTHORITY\\SYSTEM", "BUILTIN\\Administrators", "CREATOR OWNER", "OWNER RIGHTS")


def issue(state_dir: pathlib.Path, *, run: Callable[..., object] = procs.run_text) -> tuple[str, str | None]:
    token = secrets.token_urlsafe(32)
    path = state_dir / TOKEN_FILE
    fsutil.write_atomic(path, token + "\n", newline="\n")
    if sys.platform != "win32":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return token, readable_by_others(path, run=run)


def read(state_dir: pathlib.Path) -> str | None:
    try:
        text = (state_dir / TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def readable_by_others(path: pathlib.Path, *, run: Callable[..., object] = procs.run_text) -> str | None:
    """A warning naming who else can read `path`, or None. A check that cannot run says so; it never raises."""
    if sys.platform != "win32":
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
            return f"{path} is readable by group or others (mode {stat.filemode(mode)}); onloopd's writes are less private"
        return None
    try:
        out = run(["icacls", str(path)])
    except OSError as e:
        return f"could not check who can read {path}: {e}"
    if getattr(out, "returncode", 1) != 0:
        return f"could not check who can read {path}: icacls exited {getattr(out, 'returncode', '?')}"
    user = getpass.getuser().lower()
    others = []
    for line in str(getattr(out, "stdout", "")).splitlines():
        line = line.replace(str(path), "").strip()
        if ":(" not in line:
            continue
        principal = line.split(":(", 1)[0].strip()
        name = principal.split("\\")[-1].lower()
        if name == user or principal in _EXPECTED:
            continue
        others.append(principal)
    if others:
        return f"{path} is readable by {', '.join(others)}; onloopd's writes are less private"
    return None
