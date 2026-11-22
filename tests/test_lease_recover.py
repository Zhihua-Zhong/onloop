"""The lease's grant window (R7a's round 2, option 2): a lease is granted before its run has a pid, and a carrier that
boots in that moment must not release it as an orphan while the carrier that granted it still runs. The cases are the
carried round's test design (`r2-tests`), and the process test makes the window deterministic with marker files, not
timing."""

import json
import os
import pathlib
import subprocess
import sys
import time

from onloop.carrier import lease
from onloop.overlay.ledger import Ledger

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEAD = lambda pid, created: False  # noqa: E731 - no pid is alive
LIVE = lambda pid, created: True  # noqa: E731


def grant(led, instance, ttl_s=60):
    c = lease.acquire(led, role="builder", session_id="s1", deliveries=(), instance=instance, ttl_s=ttl_s)
    assert c is not None
    return c


def released(led):
    return led.select(
        "SELECT json_extract(payload, '$.id') AS id, json_extract(payload, '$.reason') AS why FROM events "
        "WHERE kind = 'lease.session_released' ORDER BY seq"
    )


def test_a_no_pid_lease_of_a_running_instance_is_kept(tmp_path):
    with Ledger(tmp_path / "l.db") as led:
        c = grant(led, "A-1")
        still, freed, pending = lease.recover(
            led, instance="B-1", alive=DEAD, is_running=lambda i: i == "A-1"
        )
        assert (still, freed, [h.lease_id for h in pending]) == ([], [], [c.lease_id])
        assert (
            released(led) == []
            and lease.acquire(led, role="builder", session_id="s1", deliveries=(), instance="B-1", ttl_s=60)
            is None
        )
        lease.release(led, c.lease_id, "s1", "done")  # the owner releases it, once
        assert released(led) == [{"id": c.lease_id, "why": "done"}]


def test_a_no_pid_lease_of_a_gone_instance_is_released_as_orphaned(tmp_path):
    with Ledger(tmp_path / "l.db") as led:
        c = grant(led, "A-1")
        _, freed, pending = lease.recover(led, instance="B-1", alive=DEAD, is_running=lambda i: False)
        assert [h.lease_id for h in freed] == [c.lease_id] and pending == []
        assert released(led) == [{"id": c.lease_id, "why": "orphaned"}]


def test_its_own_lease_from_an_earlier_boot_is_released(tmp_path):
    with Ledger(tmp_path / "l.db") as led:
        c = grant(led, "X-1")
        _, freed, _ = lease.recover(led, instance="X-2", alive=DEAD, is_running=lambda i: i == "X-2")
        assert [h.lease_id for h in freed] == [c.lease_id]


def test_a_live_pid_of_a_running_instance_is_left_to_its_owner_not_waited_on(tmp_path):
    """The 20 race runs under load (2026-11-22), run 18: a carrier booting while another's turn ran waited on that pid
    and released the lease when it exited, and so did its owner: two releases of one lease."""
    with Ledger(tmp_path / "l.db") as led:
        c = grant(led, "A-1")
        led.conn.execute("UPDATE session_leases SET holder_pid = 4242 WHERE id = ?", (c.lease_id,))
        still, freed, pending = lease.recover(
            led, instance="B-1", alive=LIVE, is_running=lambda i: i == "A-1"
        )
        assert still == freed == [] and [h.lease_id for h in pending] == [c.lease_id]


def test_a_live_pid_of_a_gone_instance_is_waited_on(tmp_path):
    with Ledger(tmp_path / "l.db") as led:
        c = grant(led, "A-1")
        led.conn.execute("UPDATE session_leases SET holder_pid = 4242 WHERE id = ?", (c.lease_id,))
        still, freed, pending = lease.recover(led, instance="B-1", alive=LIVE, is_running=lambda i: False)
        assert [h.lease_id for h in still] == [c.lease_id] and freed == pending == []


def test_a_dead_pid_of_a_running_instance_is_left_to_its_owner(tmp_path):
    """The owner sees its child exit and releases the lease; a second release here is the bug's double release."""
    with Ledger(tmp_path / "l.db") as led:
        c = grant(led, "A-1")
        led.conn.execute("UPDATE session_leases SET holder_pid = 4242 WHERE id = ?", (c.lease_id,))
        _, freed, pending = lease.recover(led, instance="B-1", alive=DEAD, is_running=lambda i: i == "A-1")
        assert freed == [] and [h.lease_id for h in pending] == [c.lease_id]


def test_a_hung_instances_lease_is_taken_over_after_it_expires(tmp_path):
    with Ledger(tmp_path / "l.db") as led:
        c = grant(led, "A-1", ttl_s=-1)  # already past its expiry
        _, freed, pending = lease.recover(led, instance="B-1", alive=DEAD, is_running=lambda i: True)
        assert [h.lease_id for h in freed] == [c.lease_id] and pending == []


def test_an_instance_is_running_only_as_the_process_it_registered(tmp_path):
    with Ledger(tmp_path / "l.db") as led:
        lease.register(led, "me-1", os.getpid())
        lease.register(led, "gone-1", os.getpid(), created=1)  # this pid, but another process's creation time
        assert (
            lease.running(led, "me-1")
            and not lease.running(led, "gone-1")
            and not lease.running(led, "nobody")
        )


HOLDER = """
import json, pathlib, sys, time
from onloop.carrier import lease
from onloop.overlay.ledger import Ledger
import os
led = Ledger(pathlib.Path(sys.argv[1]))
lease.register(led, "A-1", os.getpid())
c = lease.acquire(led, role="builder", session_id="s1", deliveries=(), instance="A-1", ttl_s=600)
d = pathlib.Path(sys.argv[2])
(d / "granted").write_text(json.dumps({"lease": c.lease_id}), encoding="utf-8")
end = time.time() + 60
while not (d / "go").exists() and time.time() < end:
    time.sleep(0.05)
led.close()  # exits without releasing: as a carrier that dies before it spawns
"""


def test_a_carrier_booting_in_another_carriers_grant_window_leaves_its_lease_alone(tmp_path):
    """The process test: carrier A has granted the lease and not yet spawned (it holds there until told). A second
    carrier's boot keeps A's lease while A runs; once A is gone, the next boot releases it as orphaned."""
    from onloop.carrier.loop import Carrier

    from . import carrier_helpers as h

    path = tmp_path / "l.db"
    Ledger(path).close()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    a = subprocess.Popen([sys.executable, "-c", HOLDER, str(path), str(tmp_path)], env=env)
    try:
        end = time.time() + 60
        while not (tmp_path / "granted").exists():
            assert time.time() < end and a.poll() is None, "the holder never granted"
            time.sleep(0.05)
        held = json.loads((tmp_path / "granted").read_text(encoding="utf-8"))["lease"]
        cfg = h.config(tmp_path, roles={"builder": h.role(cwd=tmp_path)})
        with Ledger(path) as led:
            b = Carrier(led, cfg, tmp_path, environ=h.env(tmp_path), login=h.LoggedIn())
            notes = b.boot()
            assert any("belongs to a carrier still running" in n for n in notes), notes
            assert released(led) == []
        (tmp_path / "go").write_text("", encoding="utf-8")
        a.wait(timeout=60)
        with Ledger(path) as led:
            notes = Carrier(led, cfg, tmp_path, environ=h.env(tmp_path), login=h.LoggedIn()).boot()
            assert released(led) == [{"id": held, "why": "orphaned"}], notes
    finally:
        if a.poll() is None:
            a.kill()
