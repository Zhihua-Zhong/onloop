"""A run's child process: in its own Windows job object, so the whole tree dies with onloopd (spike R4-S3).

    spawn(argv, cwd, env) -> Child     start suspended, assign to a KILL_ON_JOB_CLOSE job, then resume
    Child.terminate() -> str           end the whole tree (TerminateJobObject; `taskkill /T /F` by pid as the fallback)
    Child.claude_pid() -> int | None   the `claude.exe` under `cmd.exe`: the pid `claude agents --json` reports
    creation_time(pid) -> int | None   a process's creation time, so a reused pid is never taken for the holder
    alive(pid, created) -> bool        that exact process is still running

Without a job, a child that is mid-turn outlives a killed parent by seconds and finishes its turn; with one, the tree
is gone within 0.5 s of the parent (R4-S3). If the job cannot be made, the child still runs, `Child.job` is None, and
the recovery pass at boot waits for it by pid and creation time. On POSIX the child is a new session and the tree is
its process group. Everything here acts by the pid onloop started, never by an image name.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Mapping
from typing import Any

from .. import procs

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_EXTENDED_LIMIT_INFORMATION = 9

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes as W

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _k32.CreateJobObjectW.restype = W.HANDLE
    _k32.OpenProcess.restype = W.HANDLE
    _k32.CreateToolhelp32Snapshot.restype = W.HANDLE

    class _Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", W.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", W.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", W.DWORD),
            ("SchedulingClass", W.DWORD),
        ]

    class _Io(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("R", "W", "O", "RT", "WT", "OT")]

    class _Extended(ctypes.Structure):
        _fields_ = [
            ("Basic", _Basic),
            ("Io", _Io),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", W.DWORD),
            ("cntUsage", W.DWORD),
            ("th32ProcessID", W.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", W.DWORD),
            ("cntThreads", W.DWORD),
            ("th32ParentProcessID", W.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", W.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]


def creation_time(pid: int | None) -> int | None:
    """The process's creation time (a FILETIME on Windows, clock ticks since boot on Linux), or None."""
    if not pid:
        return None
    if sys.platform == "win32":
        h = _k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            c, e, k, u = W.FILETIME(), W.FILETIME(), W.FILETIME(), W.FILETIME()
            if not _k32.GetProcessTimes(
                h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)
            ):
                return None
            return (c.dwHighDateTime << 32) | c.dwLowDateTime
        finally:
            _k32.CloseHandle(h)
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="ascii") as f:
            return int(f.read().rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def alive(pid: int | None, created: int | None) -> bool:
    """That exact process is running: its pid is alive and, when a creation time was recorded, it matches."""
    if not pid or not procs.pid_alive(int(pid)):
        return False
    if created is None:
        return True
    now = creation_time(int(pid))
    return now is None or now == int(created)


def _children(pid: int) -> list[tuple[int, str]]:
    """(pid, image name) of the direct children of `pid` (Windows: a Toolhelp snapshot)."""
    if sys.platform != "win32":
        return []
    snap = _k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == W.HANDLE(-1).value:
        return []
    out = []
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ParentProcessID == pid:
                out.append((int(entry.th32ProcessID), entry.szExeFile))
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    return out


class Child:
    """A started run. `pid` is the process onloop started (`cmd.exe` for `claude.cmd`); `job` its job object."""

    def __init__(self, proc: subprocess.Popen[Any], job: Any = None, shim: bool = False):
        self.proc = proc
        self.shim = shim  # started through a .cmd shim: the CLI is a child of cmd.exe
        self.pid = proc.pid
        self.job = job
        self.created = creation_time(proc.pid)

    def claude_pid(self, wait_s: float = 3.0) -> int | None:
        """The CLI's own process: the first descendant whose image is not a shell shim, else the child itself. Through
        a `.cmd` shim, the CLI appears a moment after the shim starts, so it is looked for up to `wait_s`."""
        import time

        end = time.monotonic() + (wait_s if self.shim else 0.0)
        while True:
            found = self._descendant()
            if found is not None or time.monotonic() >= end or self.proc.poll() is not None:
                return found if found is not None else self.pid
            time.sleep(0.05)

    def _descendant(self) -> int | None:
        frontier, seen = [self.pid], set()
        while frontier:
            p = frontier.pop(0)
            for cpid, name in _children(p):
                if cpid in seen:
                    continue
                seen.add(cpid)
                if name.lower() not in ("cmd.exe", "conhost.exe"):
                    return cpid
                frontier.append(cpid)
        return None

    def poll(self) -> int | None:
        return self.proc.poll()

    def terminate(self) -> str:
        """End the whole tree. Returns how, for the run's detail."""
        if self.proc.poll() is not None and self.job is None:
            return "already exited"
        how = ""
        if sys.platform == "win32" and self.job is not None:
            if _k32.TerminateJobObject(W.HANDLE(self.job), 1):
                how = "TerminateJobObject"
        if not how:
            how = procs.kill_tree(self.proc) if self.proc.poll() is None else "already exited"
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
            how += "; then kill"
        return how

    def close(self) -> None:
        """Drop the job handle. With KILL_ON_JOB_CLOSE this also ends anything the run left behind."""
        if sys.platform == "win32" and self.job is not None:
            _k32.CloseHandle(W.HANDLE(self.job))
            self.job = None


def _make_job() -> Any:
    job = _k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _Extended()
    info.Basic.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _k32.SetInformationJobObject(
        W.HANDLE(job), _EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
    ):
        _k32.CloseHandle(W.HANDLE(job))
        return None
    return job


def spawn(argv: list[str], *, cwd: str, env: Mapping[str, str], **kw: Any) -> Child:
    """Start `argv` with pipes for stdin and stdout. On Windows: suspended, in a new job, then resumed."""
    common = dict(
        cwd=cwd, env=dict(env), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw
    )
    if sys.platform != "win32":
        return Child(subprocess.Popen(argv, start_new_session=True, **common))  # noqa: S603
    shim = str(argv[0]).lower().endswith((".cmd", ".bat"))
    job = _make_job()
    flags = CREATE_NEW_PROCESS_GROUP | (CREATE_SUSPENDED if job else 0)
    proc = subprocess.Popen(argv, creationflags=flags, **common)  # noqa: S603
    if job:
        handle = W.HANDLE(int(proc._handle))  # type: ignore[attr-defined]
        if not _k32.AssignProcessToJobObject(W.HANDLE(job), handle):
            _k32.CloseHandle(W.HANDLE(job))
            job = None
        _ntdll.NtResumeProcess(handle)
    return Child(proc, job, shim)


def kill_pid_tree(pid: int, created: int | None) -> str:
    """Stop a holder found at boot (no job handle survives a crash): only that exact process and its tree."""
    if not alive(pid, created):
        return "not running"
    if sys.platform == "win32":
        r = procs.run_text(["taskkill", "/PID", str(pid), "/T", "/F"])
        return f"taskkill /PID {pid} /T /F (exit {r.returncode})"
    try:
        os.killpg(pid, signal.SIGKILL)
        return f"killpg {pid}"
    except OSError:
        os.kill(pid, signal.SIGKILL)
        return f"kill {pid}"
