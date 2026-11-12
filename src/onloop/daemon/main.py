"""onloopd, the process: the HTTP surface on the loopback address, and an ingest tick into the ledger.

    run(config)            the foreground process (`onloop daemon run`; Task Scheduler will start this)
    request_stop(state)    ask a running onloopd to stop (`onloop daemon stop`), then wait for its pid to exit

On each tick (every `tick_s`, 10 s by default), when `onloopd.yaml` names a repository, onloopd reads GitHub into
the ledger, which is the only record it serves, every `github.every`. It probes the wakers, runs the detectors, then
rewrites its heartbeat file. (The older stores are imported once, by `onloop overlay import`, not on every tick.) The HTTP server reads the ledger through its own read-only connection.

Files, next to the ledger: `onloopd.token` (drawn at every start; see token.py), `onloopd.pid` (written at start,
removed at a clean stop), `onloopd.heartbeat.json`
(rewritten every tick: pid, port, time, last ledger seq) and `onloopd.stop` (a stop request). A clean stop finishes
the current tick, shuts the server down, closes the ledger and removes the pid file. A console's Ctrl+C or Ctrl+Break
stops it the same way. If it does not exit within the timeout, `request_stop` stops that pid's process tree.
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .. import __version__, fsutil, gitutil, procs
from .. import events as ev
from ..detect import core as detect
from ..detect import no_outcome, schedules, wakers
from ..ingest import github as github_ingest
from ..overlay.ledger import Ledger
from . import hook_receiver, sources, token
from . import server as srv
from .config import OnloopdConfig
from .routes import Routes

# The write routes onloopd serves: (prefix, handler).
ROUTES: list[tuple[str, Callable[..., dict[str, Any]]]] = [
    ("/hooks/", hook_receiver.handle),
    ("/sources/", sources.handle),
]

# The detectors run on every tick, after the ingest: (name, detector). They record; nothing acts in R2.
DETECTORS: list[tuple[str, detect.Detector]] = [
    (no_outcome.NAME, no_outcome.detect),
    (wakers.NAME, wakers.detect),
    (schedules.NAME, schedules.detect),
]

PID_FILE = "onloopd.pid"
STOP_FILE = "onloopd.stop"
HEARTBEAT_FILE = "onloopd.heartbeat.json"


@dataclass
class DaemonConfig:
    ledger: pathlib.Path
    port: int = srv.DEFAULT_PORT
    host: str = srv.HOST
    tick_s: float = 10.0
    max_ticks: int | None = None  # for tests: stop after this many ticks
    watch: OnloopdConfig = field(default_factory=OnloopdConfig)  # onloopd.yaml: what it watches
    detectors: list[tuple[str, Any]] | None = None  # for tests: in place of DETECTORS
    probes: dict[str, Any] | None = None  # for tests: in place of the default waker probes

    @property
    def state_dir(self) -> pathlib.Path:
        return self.ledger.resolve().parent


@dataclass
class TickResult:
    imported: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    detected: dict[str, Any] = field(default_factory=dict)


class LockedReader:
    """The server's view of the ledger: its own read-only connection, one call at a time."""

    def __init__(self, path: pathlib.Path):
        self._ledger = Ledger(path, readonly=True)
        self._lock = threading.Lock()

    def __getattr__(self, name: str) -> Callable[..., Any]:
        fn = getattr(self._ledger, name)

        def call(*a: Any, **kw: Any) -> Any:
            with self._lock:
                return fn(*a, **kw)

        return call

    def close(self) -> None:
        with self._lock:
            self._ledger.close()


class _NoClone:
    """The git side of the GitHub ingest when no clone is configured: every patch id is unknown (fail closed)."""

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def unavailable(*_a: Any, **_kw: Any) -> Any:
            raise gitutil.GitError("no github.workdir in onloopd.yaml")

        return unavailable


class GitHubSource:
    """The GitHub ingest on onloopd's tick, when it is due."""

    def __init__(self, watch: Any, *, client: Any = None, git: Any = None, policy: Any = None):
        from .. import github
        from .. import policy as pol

        self.watch = watch
        self.client = client or github.GitHub(watch.repo)
        self.git = git or (gitutil.Git(watch.workdir) if watch.workdir else _NoClone())
        self.policy = policy if policy is not None else (pol.load(watch.policy) if watch.policy else None)
        self.next_at = 0.0

    def __repr__(self) -> str:
        return f"github:{self.watch.repo}"

    def __call__(self, ledger: Ledger) -> dict[str, Any] | None:
        now = time.monotonic()
        if now < self.next_at:
            return None
        self.next_at = now + self.watch.every_s
        r = github_ingest.tick(
            ledger, self.watch.repo, self.client, self.git, policy=self.policy, base=self.watch.base
        )
        return {"source": repr(self), "appended": len(r.appended), "not_modified": r.not_modified}


def tick(ledger: Ledger, cfg: DaemonConfig, sources: tuple[Callable[[Ledger], Any], ...] = ()) -> TickResult:
    """Run each due source. A source that cannot be read is reported, not fatal."""
    res = TickResult()
    for source in sources:
        try:
            got = source(ledger)
        except Exception as e:  # noqa: BLE001 - as above
            res.errors.append(f"{source!r}: {type(e).__name__}: {e}"[:300])
            continue
        if got is not None:
            res.imported.append(got)
    now = ledger.now()
    probes = cfg.probes if cfg.probes is not None else wakers.default_probes(dict(cfg.watch.monitors))
    try:
        res.detected["probed"] = wakers.probe(ledger, probes, now)
    except Exception as e:  # noqa: BLE001 - the probes failing must not stop the detectors
        res.errors.append(f"waker probes: {type(e).__name__}: {e}"[:300])
    rep = detect.run(ledger, cfg.detectors if cfg.detectors is not None else DETECTORS, now)
    res.errors += [f"detector {e}" for e in rep.errors]
    res.detected.update({k: v for k, v in rep.as_dict().items() if k != "errors"})
    res.detected = {k: v for k, v in res.detected.items() if v}
    return res


def _write_heartbeat(cfg: DaemonConfig, port: int, ledger: Ledger, res: TickResult) -> None:
    beat = {
        "pid": os.getpid(),
        "port": port,
        "at": ev.now_ts(),
        "onloop": __version__,
        "last_seq": ledger.last_seq(),
        "imported": res.imported,
        "detected": res.detected,
        "errors": res.errors,
    }
    fsutil.write_atomic(cfg.state_dir / HEARTBEAT_FILE, json.dumps(beat, sort_keys=True) + "\n", newline="\n")


def run(cfg: DaemonConfig, *, say: Callable[[str], None] = print) -> int:
    """Run onloopd in the foreground until a stop request, a console interrupt or `max_ticks`. Returns an exit code."""
    host = srv.check_host(cfg.host)
    state = cfg.state_dir
    state.mkdir(parents=True, exist_ok=True)
    pid_path, stop_path = state / PID_FILE, state / STOP_FILE
    if pid_path.exists():
        other = _read_pid(pid_path)
        if other and other != os.getpid() and procs.pid_alive(other):
            say(f"onloopd: already running as pid {other} ({pid_path})")
            return 2
    stop_path.unlink(missing_ok=True)
    stopping = threading.Event()

    def on_signal(signum: int, _frame: Any) -> None:
        stopping.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), on_signal)

    try:
        ingest_sources = (GitHubSource(cfg.watch.github),) if cfg.watch.github else ()
    except Exception as e:  # noqa: BLE001 - a policy that does not load stops onloopd before it starts (fail closed)
        say(f"onloopd: does not start: github: {type(e).__name__}: {e}"[:300])
        return 2
    ledger = Ledger(cfg.ledger)  # creates or migrates the file; the one writer
    declared = sources.declare(ledger, [s.declaration() for s in cfg.watch.schedules])
    if declared:
        say(f"onloopd: declared {len(declared)} new or changed schedule(s) from onloopd.yaml")
    if sources.declare_setting(ledger, "heartbeat_schedule", cfg.watch.heartbeat_schedule):
        say(f"onloopd: heartbeat_schedule is now {cfg.watch.heartbeat_schedule or 'not set'}")
    reader = LockedReader(cfg.ledger)
    write_lock = threading.Lock()  # the tick and the write routes take turns on the one writer
    routes = Routes(ledger, write_lock)
    for prefix, handler in ROUTES:
        routes.add(prefix, handler)
    secret, warning = token.issue(state)
    if warning:
        say(f"onloopd: warning: {warning}")
    try:
        server = srv.make_server(
            reader, port=cfg.port, host=host, writer=routes, token=secret, heartbeat=state / HEARTBEAT_FILE
        )
    except srv.PortInUse as e:
        say(f"onloopd: does not start: {e}")
        reader.close()
        ledger.close()
        return 2
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, name="onloopd-http", daemon=True)
    thread.start()
    fsutil.write_atomic(pid_path, f"{os.getpid()}\n", newline="\n")
    say(f"onloopd {__version__}: listening on http://{host}:{port}, ledger {cfg.ledger}, pid {os.getpid()}")
    ticks = 0
    try:
        while not stopping.is_set():
            with write_lock:
                res = tick(ledger, cfg, ingest_sources)
            for err in res.errors:
                say(f"onloopd: ingest: {err}")
            with write_lock:
                _write_heartbeat(cfg, port, ledger, res)
            ticks += 1
            if cfg.max_ticks is not None and ticks >= cfg.max_ticks:
                break
            deadline = time.monotonic() + cfg.tick_s
            while not stopping.is_set() and time.monotonic() < deadline:
                if stop_path.exists():
                    stopping.set()
                    break
                stopping.wait(min(0.5, max(0.0, deadline - time.monotonic())))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        reader.close()
        ledger.close()
        stop_path.unlink(missing_ok=True)
        if _read_pid(pid_path) == os.getpid():
            pid_path.unlink(missing_ok=True)
        say("onloopd: stopped")
    return 0


def _read_pid(path: pathlib.Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def request_stop(state_dir: pathlib.Path, *, timeout_s: float = 30.0) -> tuple[int, str]:
    """Ask the onloopd whose pid file is in `state_dir` to stop, and wait for that pid to exit.

    Returns (exit code, message): 0 stopped cleanly, 1 not running, 3 had to be stopped by its process tree."""
    pid = _read_pid(state_dir / PID_FILE)
    if not pid or not procs.pid_alive(pid):
        return 1, f"onloopd is not running (no live pid in {state_dir / PID_FILE})"
    fsutil.write_atomic(state_dir / STOP_FILE, f"{pid}\n", newline="\n")
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if not procs.pid_alive(pid):
            return 0, f"onloopd (pid {pid}) stopped"
        time.sleep(0.2)
    how = _kill_tree(pid)
    return 3, f"onloopd (pid {pid}) did not stop within {timeout_s:.0f} s; stopped by its pid: {how}"


def _kill_tree(pid: int) -> str:
    if sys.platform == "win32":
        r = procs.run_text(["taskkill", "/PID", str(pid), "/T", "/F"])
        return f"taskkill /PID {pid} /T /F (exit {r.returncode})"
    try:
        os.killpg(pid, signal.SIGKILL)
        return f"killpg {pid} SIGKILL"
    except OSError:
        os.kill(pid, signal.SIGKILL)
        return f"kill {pid} SIGKILL"
