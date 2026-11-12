"""D2: a continue whose waker is not alive. Story 11 replayed with a real process, a cron that misses its window, and a
monitor that answers 500."""

import http.server
import subprocess
import sys
import threading

import pytest

from onloop import procs
from onloop.daemon import config as cfg_mod
from onloop.daemon import hook_receiver as hr
from onloop.daemon import main as daemon
from onloop.daemon import server as srv_mod
from onloop.daemon import sources
from onloop.detect import wakers
from onloop.overlay.ledger import Ledger

from .test_hook_receiver import payload

T0 = "2026-11-09T09:00:00.000Z"


@pytest.fixture
def ledger(tmp_path):
    clock = {"now": T0}
    led = Ledger(tmp_path / "ledger.db", clock=lambda: clock["now"])
    led.clock = clock
    yield led
    led.close()


def cont(led, ref, waker, role="builder", prompt=None):
    message = f"Pushed; CI is running.\n\noutcome: continue ref={ref} waker={waker}"
    body = {"role": role, "payload": payload("stop", last_assistant_message=message, prompt_id=prompt or ref)}
    hr.handle(led, "stop", body)


def tick(led, tmp_path, probes=None, monitors=()):
    cfg = daemon.DaemonConfig(
        ledger=tmp_path / "ledger.db",
        probes=probes,
        watch=cfg_mod.OnloopdConfig(monitors=tuple(monitors)),
    )
    return daemon.tick(led, cfg)


def alerts(led, state="open"):
    return led.select(
        "SELECT * FROM items WHERE kind = 'alert' AND id LIKE 'alert:dead_waker:%' AND state = ? ORDER BY id",
        (state,),
    )


def test_story_11_a_stopped_waker_is_one_alert_naming_both_items(ledger, tmp_path):
    sleeper = subprocess.Popen(
        [sys._base_executable, "-I", "-c", "import time; time.sleep(120)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        pid = sleeper.pid
        cont(ledger, "pr#7", f"pid:{pid}", role="builder")
        cont(ledger, "pr#9", f"pid:{pid}", role="author")
        first = tick(ledger, tmp_path)
        assert first.detected.get("probed") == [f"waker:pid:{pid}"] and alerts(ledger) == []
        assert ledger.select("SELECT state FROM resources")[0]["state"] == "alive"
    finally:
        sleeper.kill()  # the one process this test started, by its own handle
        sleeper.wait(timeout=10)
    assert not procs.pid_alive(pid)
    res = tick(ledger, tmp_path)
    [a] = alerts(ledger)
    assert res.detected["opened"] == [f"alert:dead_waker:waker:pid:{pid}"]
    assert a["detail"]["items"] == ["pr#7", "pr#9"] and a["detail"]["roles"] == ["author", "builder"]
    assert f"process {pid} is not running" in a["detail"]["reason"]
    assert ledger.select("SELECT state FROM resources")[0]["state"] == "dead"
    top = ledger.constraint("2026-11-09T09:30:00.000Z")["constraint"]
    assert top["on_ref"] == f"waker:pid:{pid}" and sorted(top["waiters"]) == ["author", "builder"]
    for _ in range(5):
        assert tick(ledger, tmp_path).detected == {}  # one alert, and the state change logged once
    assert [e["kind"] for e in ledger.events_after(0, 100)].count("resource.checked") == 2


def test_a_cron_that_misses_its_window_is_a_dead_waker(ledger, tmp_path):
    decl = cfg_mod.parse({"schedules": [{"id": "nightly", "period": "1d", "grace": "1h", "owner": "ops"}]})
    sources.declare(ledger, [s.declaration() for s in decl.schedules])
    sources.handle(ledger, "schedules/nightly/ping", {})
    cont(ledger, "pr#3", "cron:nightly")
    ledger.clock["now"] = (
        "2026-11-10T09:59:00.000Z"  # 1 d + 59 min after the last success: still in its grace
    )
    assert tick(ledger, tmp_path).detected.get("probed") == ["waker:cron:nightly"]
    assert alerts(ledger) == []
    ledger.clock["now"] = "2026-11-10T10:01:00.000Z"
    tick(ledger, tmp_path)
    [a] = alerts(ledger)
    assert "overdue by 60 s" in a["detail"]["reason"] and a["owner_role"] == "builder"


def test_a_cron_that_is_not_declared_is_dead_at_once(ledger, tmp_path):
    cont(ledger, "pr#3", "cron:no-such-job")
    tick(ledger, tmp_path)
    assert "no schedule 'no-such-job' is declared" in alerts(ledger)[0]["detail"]["reason"]


class Answer(http.server.BaseHTTPRequestHandler):
    code = 500

    def do_GET(self):
        self.send_response(type(self).code)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def monitor():
    srv = http.server.ThreadingHTTPServer((srv_mod.HOST, 0), Answer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://{srv_mod.HOST}:{srv.server_address[1]}/health"
    srv.shutdown()
    srv.server_close()


def test_a_monitor_answering_500_is_dead_and_200_alive_again(ledger, tmp_path, monitor):
    cont(ledger, "pr#5", "monitor:build-watch")
    Answer.code = 500
    tick(ledger, tmp_path, monitors=[("build-watch", monitor)])
    [a] = alerts(ledger)
    assert "answered 500" in a["detail"]["reason"]
    Answer.code = 200
    res = tick(ledger, tmp_path, monitors=[("build-watch", monitor)])
    assert res.detected["closed"] == [a["id"]] and alerts(ledger) == []
    assert ledger.select("SELECT state FROM resources")[0]["state"] == "alive"


def test_a_monitor_not_named_is_dead_and_one_off_the_loopback_is_refused(ledger, tmp_path):
    cont(ledger, "pr#5", "monitor:unnamed")
    tick(ledger, tmp_path)
    assert "no monitor 'unnamed'" in alerts(ledger)[0]["detail"]["reason"]
    with pytest.raises(cfg_mod.ConfigError, match="loopback"):
        cfg_mod.parse(
            {"monitors": [{"id": "far", "url": "http://203.0.113.5/health"}]}  # scan:allow
        )  # scan:allow documentation IP
    assert wakers.loopback("http://[::1]:9000/x") and wakers.loopback("http://localhost/x")
    assert not wakers.loopback("http://localhost.example/x") and not wakers.loopback("file:///etc")


def test_a_probe_that_cannot_tell_raises_nothing(ledger, tmp_path):
    class Broken:
        kind = "pid"

        def check(self, *a):
            raise OSError("access denied")

    cont(ledger, "pr#1", "pid:4242")
    res = tick(ledger, tmp_path, probes={"pid": Broken()})
    assert alerts(ledger) == [] and "probed" not in res.detected  # unknown was already its state
    assert ledger.select("SELECT state FROM resources")[0]["state"] == "unknown"


def test_the_next_turn_clears_the_wait_and_closes_the_alert(ledger, tmp_path):
    cont(ledger, "pr#1", "pid:999999", prompt="p1")
    tick(ledger, tmp_path, probes={"pid": wakers.PidProbe(alive=lambda pid: False)})
    assert len(alerts(ledger)) == 1
    hr.handle(
        ledger,
        "stop",
        {
            "role": "builder",
            "payload": payload("stop", last_assistant_message="outcome: done ref=pr#1", prompt_id="p2"),
        },
    )
    res = tick(ledger, tmp_path, probes={"pid": wakers.PidProbe(alive=lambda pid: False)})
    assert len(res.detected["closed"]) == 1
    assert ledger.rebuild_matches()[0]
