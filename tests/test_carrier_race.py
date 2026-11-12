"""The lease race (the R4 plan's section 8): two carrier **processes** on one ledger and one session id, each trying to
deliver 200 times at random moments. The fake CLI holds a lock file per session while it runs and records every time
it finds the lock taken, so an overlap is seen by the child itself, not inferred from the ledger."""

import json
import os
import pathlib
import subprocess
import sys

import pytest

from onloop.carrier.lease import Ledger  # noqa: F401 - the same ledger class the workers use
from onloop.overlay.ledger import Ledger as L

from . import carrier_helpers as h

N = int(os.environ.get("ONLOOP_RACE_N", "200"))
ROOT = pathlib.Path(__file__).resolve().parent.parent


def race(tmp_path, n, **fake):
    led = tmp_path / "ledger.db"
    L(led).close()  # created once, before the two processes open it
    lockdir = tmp_path / "locks"
    lockdir.mkdir()
    env = {**os.environ, "FAKE_LOCKDIR": str(lockdir), **{k: str(v) for k, v in fake.items()}}
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + str(ROOT)
    for k in [k for k in env if k.upper().startswith(("CLAUDE", "ANTHROPIC"))]:
        env.pop(k)
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "tests.carrier_race_worker", str(led), str(n), str(seed), name],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for seed, name in ((1, "A"), (2, "B"))
    ]
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=1800)
        assert p.returncode == 0, err[-2000:]
        outs.append(json.loads(out.strip().splitlines()[-1]))
    return L(led), lockdir, outs


def test_two_carrier_processes_never_run_one_session_at_once(tmp_path):
    led, lockdir, outs = race(tmp_path, N)
    overlaps = (
        (lockdir / "overlaps.log").read_text(encoding="utf-8") if (lockdir / "overlaps.log").exists() else ""
    )
    assert overlaps == ""  # the children never found the session's lock taken
    runs = (lockdir / "runs.log").read_text(encoding="utf-8").splitlines()
    assert len(runs) == sum(o["acted"] for o in outs) > 0
    assert all(o["acted"] > 0 for o in outs), outs  # both carriers won some of the races
    # From the event log: never two live leases on the session at any instant.
    live = 0
    for e in led.select("SELECT kind FROM events WHERE kind LIKE 'lease.session_%' ORDER BY seq"):
        live += {"lease.session_granted": 1, "lease.session_released": -1}.get(e["kind"], 0)
        assert live in (0, 1)
    assert live == 0
    # Every delivery is acked, or still queued to be retried; none is lost or delivered by two runs.
    states = {
        r["state"]: r["n"] for r in led.select("SELECT state, COUNT(*) AS n FROM deliveries GROUP BY state")
    }
    assert sum(states.values()) == 2 * N and set(states) <= {"acked", "created"}, states
    twice = led.select("SELECT payload FROM events WHERE kind = 'run.started' ORDER BY seq")
    claimed = [d for e in twice for d in e["payload"]["deliveries"]]
    acked_runs = led.select(
        "SELECT run_id, COUNT(*) AS n FROM deliveries WHERE state = 'acked' GROUP BY run_id"
    )
    assert sum(r["n"] for r in acked_runs) == states.get("acked", 0)
    assert len(claimed) >= states.get("acked", 0)
    led.close()


@pytest.mark.parametrize("status", ["idle", "busy"])
def test_a_session_held_by_a_third_process_is_never_spawned(tmp_path, status):
    agents = tmp_path / "agents.json"
    agents.write_text(
        json.dumps([{"pid": 4242, "sessionId": h.SID, "kind": "interactive", "status": status}]),
        encoding="utf-8",
    )
    led, lockdir, outs = race(tmp_path, 10, FAKE_AGENTS=agents)
    assert not (lockdir / "runs.log").exists()  # nothing was spawned
    assert all(o["acted"] == 0 and o["waited"].get("held_elsewhere") for o in outs), outs
    assert led.select("SELECT COUNT(*) AS n FROM session_leases")[0]["n"] == 0
    led.close()
