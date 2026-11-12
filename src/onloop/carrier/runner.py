"""One carried run: spawn the CLI, write one composed message, watch it, read it (decision 0010).

    run(spec, hooks) -> RunResult

1. spawn the argv in its own job (jobs.spawn), and report the pids (`hooks.spawned`);
2. write the composed message as one stream-json user line and flush: `delivered` (`hooks.delivered`);
3. read stdout line by line: the replay of that message is `processing` (`hooks.processing`); the first `result`
   ends the turn, and stdin is closed right after it, so the process exits and is never kept open across turns
   (spike R4-S1: a process kept open forks the session on its next turn);
4. while it runs: the wall budget and the silence limit (the monotonic clock, so a machine's sleep is not silence),
   the kill switch (`hooks.stop_requested`, checked every 0.5 s), and the lease renewal (`hooks.renew`, every 30 s).
   Any of them ends the whole tree by its job.

It never raises for a failure of the run: a failure is a status. It writes nothing to the ledger itself; the hooks
do, under the carrier's writer.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import jobs
from .stream import Stream, digest

RENEW_EVERY_S = 30.0
EXIT_GRACE_S = 30.0
POLL_S = 0.5


@dataclass(frozen=True)
class RunSpec:
    argv: list[str]
    cwd: str
    env: dict[str, str]
    message: str
    wall_s: float
    silence_s: float


@dataclass
class RunHooks:
    spawned: Callable[[int, int | None, int | None], None] = lambda pid, claude_pid, created: None
    delivered: Callable[[], None] = lambda: None
    processing: Callable[[], None] = lambda: None
    stop_requested: Callable[[], str | None] = lambda: None
    renew: Callable[[], None] = lambda: None


@dataclass
class RunResult:
    ended: str  # result | exited | timeout | silent | killed | spawn_failed | malformed
    stream: Stream
    exit_code: int | None = None
    wall_s: float = 0.0
    killed: str | None = None
    pid: int | None = None
    claude_pid: int | None = None
    claude_created: int | None = None
    stderr_tail: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


def user_line(text: str) -> bytes:
    return (
        json.dumps({"type": "user", "message": {"role": "user", "content": text}}, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def run(
    spec: RunSpec,
    hooks: RunHooks,
    *,
    spawn: Callable[..., jobs.Child] = jobs.spawn,
    clock: Callable[[], float] = time.monotonic,
) -> RunResult:
    stream = Stream(expect=digest(spec.message))
    t0 = clock()
    try:
        child = spawn(spec.argv, cwd=spec.cwd, env=spec.env)
    except OSError as e:
        return RunResult(
            ended="spawn_failed", stream=stream, detail={"error": f"{type(e).__name__}: {e}"[:300]}
        )
    lines: queue.Queue[bytes | None] = queue.Queue()
    err: list[bytes] = []

    def pump_out() -> None:
        try:
            for raw in child.proc.stdout:
                lines.put(raw)
        except (OSError, ValueError):
            pass
        lines.put(None)

    def pump_err() -> None:
        try:
            for raw in child.proc.stderr:
                err.append(raw)
                del err[:-20]
        except (OSError, ValueError):
            pass

    threading.Thread(target=pump_out, daemon=True, name="carrier-stdout").start()
    threading.Thread(target=pump_err, daemon=True, name="carrier-stderr").start()
    claude_pid = child.claude_pid()
    created = jobs.creation_time(claude_pid)
    hooks.spawned(child.pid, claude_pid, created)
    ended, killed = None, None
    try:
        try:
            child.proc.stdin.write(user_line(spec.message))
            child.proc.stdin.flush()
        except (OSError, ValueError) as e:
            ended = "exited"
            killed = None
            stream.malformed = stream.malformed or f"could not write the message: {type(e).__name__}"
        else:
            hooks.delivered()
        last_output, last_renew, stdin_open, eof = clock(), clock(), True, False
        while ended is None:
            now = clock()
            why = hooks.stop_requested()
            if why:
                killed, ended = child.terminate(), "killed"
                break
            if now - t0 >= spec.wall_s:
                killed, ended = child.terminate(), "timeout"
                break
            if now - last_output >= spec.silence_s and not eof:
                killed, ended = child.terminate(), "silent"
                break
            if now - last_renew >= RENEW_EVERY_S:
                hooks.renew()
                last_renew = now
            try:
                raw = lines.get(timeout=POLL_S)
            except queue.Empty:
                if eof and child.poll() is not None:
                    ended = "result" if stream.result is not None else "exited"
                continue
            if raw is None:
                eof = True
                continue
            last_output = clock()
            what = stream.feed(raw)
            if what == "malformed":
                killed, ended = child.terminate(), "malformed"
                break
            if what == "replay":
                hooks.processing()
            if what == "result" and stdin_open:
                stdin_open = False
                try:
                    child.proc.stdin.close()
                except (OSError, ValueError):
                    pass
                end = clock() + EXIT_GRACE_S
                while child.poll() is None and clock() < end:
                    time.sleep(0.05)
                    while True:  # keep reading what it prints on the way out
                        try:
                            more = lines.get_nowait()
                        except queue.Empty:
                            break
                        if more is None:
                            eof = True
                            break
                        stream.feed(more)
                if child.poll() is None:
                    killed = child.terminate()
                ended = "result"
        try:
            child.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 - the tree is ended below in any case
            killed = killed or child.terminate()
    finally:
        child.close()
    return RunResult(
        ended=ended or "exited",
        stream=stream,
        exit_code=child.proc.returncode,
        wall_s=round(clock() - t0, 3),
        killed=killed,
        pid=child.pid,
        claude_pid=claude_pid,
        claude_created=created,
        stderr_tail=b"".join(err).decode("utf-8", errors="replace")[-400:],
    )
