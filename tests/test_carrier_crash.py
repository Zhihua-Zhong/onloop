"""A carrier killed mid-run (spike R4-S3; the R4 plan's section 8): its child dies with it, by its job object, and the
next start releases the lease once and resends the delivery that no Stop hook answered, with the mark."""

import os
import pathlib
import subprocess
import sys
import time

import pytest

from onloop import procs
from onloop.carrier import deliveries
from onloop.overlay.ledger import Ledger

from . import carrier_helpers as h

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.mark.skipif(
    sys.platform != "win32", reason="the job object is Windows'; POSIX uses the process group"
)
def test_a_killed_carrier_takes_its_run_with_it_and_the_next_start_resends(tmp_path):
    led_path = tmp_path / "ledger.db"
    Ledger(led_path).close()
    env = {**os.environ, "FAKE_MODE": "hang", "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT)}
    for k in [k for k in env if k.upper().startswith(("CLAUDE", "ANTHROPIC"))]:
        env.pop(k)
    worker = subprocess.Popen(
        [sys.executable, "-m", "tests.carrier_crash_worker", str(led_path)], cwd=ROOT, env=env
    )
    try:
        child = None
        end = time.monotonic() + 30
        while child is None and time.monotonic() < end:
            time.sleep(0.2)
            with Ledger(led_path) as led:  # a fresh connection each time: it sees the worker's last commit
                rows = led.select("SELECT claude_pid FROM runs WHERE claude_pid IS NOT NULL")
            child = rows[0]["claude_pid"] if rows else None
        assert child and procs.pid_alive(child), (child, worker.poll())
        assert worker.poll() is None
        subprocess.run(["taskkill", "/PID", str(worker.pid), "/F"], capture_output=True, check=True)  # not /T
        worker.wait(10)
        t0 = time.monotonic()
        while procs.pid_alive(child) and time.monotonic() - t0 < 5:
            time.sleep(0.05)
        gone = time.monotonic() - t0
        assert not procs.pid_alive(child), "the run outlived the carrier"
        assert gone < 1.0
    finally:
        if worker.poll() is None:
            worker.kill()
    c = h.carrier(tmp_path)  # the next start: recover and settle
    assert c.led.select("SELECT release_reason FROM session_leases") == [{"release_reason": "orphaned"}]
    assert c.led.select("SELECT status FROM runs") == [{"status": "orphaned"}]
    d = c.led.select("SELECT * FROM deliveries")[0]
    assert (d["state"], d["resent"], d["attempts"]) == ("created", 1, 1)
    with c.w() as led:
        assert deliveries.RESENT_MARK in deliveries.compose(led, [d["id"]])
    released = c.led.select("SELECT COUNT(*) AS n FROM events WHERE kind = 'lease.session_released'")[0]["n"]
    c.led.close()
    again = h.carrier(tmp_path)  # a second start changes nothing: released once
    assert (
        again.led.select("SELECT COUNT(*) AS n FROM events WHERE kind = 'lease.session_released'")[0]["n"]
        == released
    )
