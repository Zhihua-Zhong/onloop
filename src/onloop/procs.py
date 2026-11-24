"""Running a child process, on any host: the one place that knows the platform differs.

    run(argv, **kw)       subprocess.run with no console window on Windows: every child onloop runs by itself
    popen(argv, **kw)     subprocess.Popen, the same way
    run_text(argv, ...)   run to the end and capture stdout and stderr as UTF-8 text; past `timeout` the child is
                          killed and subprocess.TimeoutExpired raised, as subprocess.run does
    run_in_console(argv)  subprocess.run in the person's own terminal: only for a command the person runs through
                          onloop and works in (`onloop open`'s Claude Code session, `onloop as-app -- <command>`)
    start(argv, ...)      start a long-running child in its own process group, so kill_tree can stop all of it
    kill_tree(proc)       stop a child that start() started, and everything under it, by its own pid

A step is started in its own process group (a new session on POSIX, a new process group on Windows), so that the
whole tree it spawns can be stopped together. It is stopped **by its own pid only**: `os.killpg` of the group whose
leader is that pid, or `taskkill /PID <pid> /T /F` on Windows, which walks the tree from that pid. Nothing here
looks a process up by its image name, and nothing stops a process the supervisor did not start.

**No console window** (0.6.1). On Windows a console program (git, claude, wevtutil, taskkill) started by a process with
no console of its own (onloopd under pythonw, a scheduled task) gets a new console window: with Windows Terminal as the
default terminal it pops up and takes the keyboard. So every child is started with CREATE_NO_WINDOW, here and only
here: the rest of onloop calls run, popen or run_text, and a test fails on a spawn anywhere else.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Callable
from typing import Any

__all__ = [
    "CREATE_NO_WINDOW",
    "kill_tree",
    "no_window",
    "pid_alive",
    "popen",
    "run",
    "run_in_console",
    "run_text",
    "spawn_kwargs",
    "start",
]

CREATE_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW, defined on every platform for the tests


def no_window(kw: dict[str, Any]) -> dict[str, Any]:
    """`kw` with CREATE_NO_WINDOW added to its creationflags on Windows; unchanged elsewhere."""
    if sys.platform != "win32":
        return kw
    return {**kw, "creationflags": int(kw.get("creationflags", 0)) | CREATE_NO_WINDOW}


def run(argv: Any, **kw: Any) -> subprocess.CompletedProcess[Any]:
    """subprocess.run, with no console window on Windows."""
    return subprocess.run(argv, **no_window(kw))  # noqa: S603 - callers pass a fixed argv


def popen(argv: Any, **kw: Any) -> subprocess.Popen[Any]:
    """subprocess.Popen, with no console window on Windows."""
    return subprocess.Popen(argv, **no_window(kw))  # noqa: S603 - callers pass a fixed argv


def run_in_console(argv: Any, **kw: Any) -> subprocess.CompletedProcess[Any]:
    """subprocess.run in the person's own terminal (its console is inherited): only for a command the person runs
    through onloop and works in. Never from onloopd, the carrier or a step."""
    return subprocess.run(argv, **kw)  # noqa: S603 - the person's own command


def pid_alive(pid: int) -> bool:
    """Is a process with this id running on this host? Standard library only: kill(pid, 0), or OpenProcess on Windows.
    On Linux a zombie (exited, not yet reaped by its parent) is not running, so it is not alive."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.GetLastError() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            return (
                bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
            )  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return not _zombie(pid)
    return not _zombie(pid)


def _zombie(pid: int) -> bool:
    """An exited process its parent has not reaped yet: kill(pid, 0) still finds it, but it runs nothing. Read from
    /proc (Linux); where there is no /proc, nothing is known and the process counts as alive."""
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="ascii", errors="replace") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        return False


def run_text(
    argv: list[str] | str,
    *,
    input: str | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    shell: bool = False,
) -> subprocess.CompletedProcess[str]:
    return run(
        argv,
        input=input,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        cwd=cwd,
        shell=shell,  # noqa: S602 - only gate.run_shell passes True, for the base policy's own commands
    )


def start(
    argv: list[str], *, popen: Callable[..., subprocess.Popen[Any]] | None = None, **kw: Any
) -> subprocess.Popen[Any]:
    """`popen(argv, **kw)` in a new process group (a new session on POSIX), with no console window on Windows.
    `popen` is replaceable for tests."""
    return (popen or globals()["popen"])(argv, **kw, **spawn_kwargs())


def spawn_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen[Any]) -> str:
    """Stop the process onloop started, and everything under it. Returns how it was done, for the log."""
    pid = proc.pid
    if proc.poll() is not None:
        return "already exited"
    if sys.platform == "win32":
        r = run_text(["taskkill", "/PID", str(pid), "/T", "/F"])
        how = f"taskkill /PID {pid} /T /F (exit {r.returncode})"
    else:
        try:
            os.killpg(pid, signal.SIGKILL)  # the group whose leader is our child: start_new_session
            how = f"killpg {pid} SIGKILL"
        except ProcessLookupError:
            how = "already exited"
        except PermissionError as e:  # never widen: fall back to the one pid we own
            proc.kill()
            how = f"kill {pid} (killpg refused: {e})"
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        how += "; then kill"
    return how
