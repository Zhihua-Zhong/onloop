"""Running a child process, on any host: the one place that knows the platform differs.

    run_text(argv, ...)   run to the end and capture stdout and stderr as UTF-8 text; past `timeout` the child is
                          killed and subprocess.TimeoutExpired raised, as subprocess.run does
    start(argv, ...)      start a long-running child in its own process group, so kill_tree can stop all of it
    kill_tree(proc)       stop a child that start() started, and everything under it, by its own pid

A step is started in its own process group (a new session on POSIX, a new process group on Windows), so that the
whole tree it spawns can be stopped together. It is stopped **by its own pid only**: `os.killpg` of the group whose
leader is that pid, or `taskkill /PID <pid> /T /F` on Windows, which walks the tree from that pid. Nothing here
looks a process up by its image name, and nothing stops a process the supervisor did not start.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Callable
from typing import Any

__all__ = ["kill_tree", "pid_alive", "run_text", "spawn_kwargs", "start"]


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
    return subprocess.run(
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
    argv: list[str], *, popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen, **kw: Any
) -> subprocess.Popen[Any]:
    """`popen(argv, **kw)` in a new process group (a new session on POSIX). `popen` is replaceable for tests."""
    return popen(argv, **kw, **spawn_kwargs())


def spawn_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
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
