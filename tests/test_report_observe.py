"""E2: the observe-only report. A scripted day of events gives a fixed report."""

import json
import threading
import urllib.request

import pytest

from onloop import cli
from onloop.daemon import config as cfg_mod
from onloop.daemon import hook_receiver as hr
from onloop.daemon import main as daemon
from onloop.daemon import server as srv
from onloop.daemon import sources
from onloop.overlay.ledger import Ledger
from onloop.report import observe

from .test_hook_receiver import payload


def at(hhmm):
    return f"2026-11-09T{hhmm}:00.000Z"


@pytest.fixture
def day(tmp_path):
    """09:00 builder ends on an intention; 09:42 its next turn, in review. 10:00 author continues on a dead pid; no
    next turn. The nightly job missed (declared the day before); 12:30 it succeeds."""
    clock = {"now": "2026-11-08T03:00:00.000Z"}
    led = Ledger(tmp_path / "ledger.db", clock=lambda: clock["now"])
    cfg = cfg_mod.parse({"schedules": [{"id": "nightly", "period": "1d", "grace": "1h", "owner": "ops"}]})
    sources.declare(led, [s.declaration() for s in cfg.schedules])
    dcfg = daemon.DaemonConfig(ledger=tmp_path / "ledger.db", probes={"pid": DeadPids()})

    def stop(t, role, message, prompt):
        clock["now"] = at(t)
        body = {"role": role, "payload": payload("stop", last_assistant_message=message, prompt_id=prompt)}
        hr.handle(led, "stop", body)
        daemon.tick(led, dcfg)

    stop("09:00", "builder", "I will open the PR next.", "p1")
    stop("09:42", "builder", "Opened it.\n\noutcome: in_review ref=pr#12", "p2")
    stop("10:00", "author", "outcome: continue ref=pr#9 waker=pid:4242", "p3")
    clock["now"] = at("12:30")
    sources.handle(led, "schedules/nightly/ping", {})
    daemon.tick(led, dcfg)
    yield led, tmp_path / "ledger.db"
    led.close()


class DeadPids:
    kind = "pid"

    def check(self, ref, ledger, now, since):
        from onloop.detect.wakers import Probed

        return Probed("dead", f"process {ref} is not running")


EXPECTED = {
    "since": "2026-11-09T00:00:00.000Z",
    "now": "2026-11-09T23:59:00.000Z",
    "times": "ledger",
    "would": [
        {
            "would": "wake",
            "target": "builder",
            "detector": "no_outcome",
            "then": {
                "kind": "turn",
                "role": "builder",
                "at": at("09:42"),
                "after_s": 2520,
                "outcome": "in_review",
            },
        },
        {
            "would": "escalate",
            "target": "ops",
            "detector": "schedule",
            "then": {"kind": "success", "at": at("12:30"), "after_s": 12600},
        },
        {"would": "wake", "target": "author", "detector": "dead_waker", "then": None},
    ],
    "roles": {
        "author": {"turns": 1, "without_valid_outcome": 0, "outcomes": {"continue": 1}},
        "builder": {"turns": 2, "without_valid_outcome": 1, "outcomes": {"in_review": 1}},
    },
}


def shape(report):
    keep = {"would", "target", "detector", "then"}
    then_keep = {"kind", "role", "at", "after_s", "outcome"}
    out = {**report, "would": []}
    for w in report["would"]:
        t = w["then"]
        out["would"].append(
            {**{k: w[k] for k in keep}, "then": {k: v for k, v in t.items() if k in then_keep} if t else None}
        )
    return out


def test_a_scripted_day_gives_a_fixed_report(day):
    led, _ = day
    report = observe.build(led, "2026-11-09T00:00:00.000Z", "2026-11-09T23:59:00.000Z")
    assert [w["at"] for w in report["would"]] == [at("09:00"), at("09:00"), at("10:00")]
    assert shape(report) == EXPECTED
    lines = observe.text(report)
    assert "    then: builder's next turn at 2026-11-09T09:42:00.000Z, 42 min later, in_review" in lines[1]
    assert lines[2].endswith("then: nightly succeeded at 2026-11-09T12:30:00.000Z, 3 h 30 min later")
    assert lines[3].endswith("then: nothing yet")
    assert lines[-1] == "builder: 2 turn(s), 1 without a valid outcome; in_review 1"


def test_the_command_and_the_endpoint_say_the_same(day, capsys):
    led, path = day
    assert (
        cli.main(
            [
                "report",
                "observe",
                "--since",
                "2026-11-09",
                "--until",
                "2026-11-09T23:59:00Z",
                "--json",
                "--ledger",
                str(path),
            ]
        )
        == 0
    )
    printed = json.loads(capsys.readouterr().out)
    assert len(printed["would"]) == 3 and printed["roles"]["builder"]["turns"] == 2
    reader = daemon.LockedReader(path)
    s = srv.make_server(reader, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        url = f"http://{srv.HOST}:{s.server_address[1]}/api/report/observe?since=2026-11-09T00:00:00Z&until=2026-11-09T23:59:00Z"
        with urllib.request.urlopen(url, timeout=5) as r:
            served = json.loads(r.read())
        assert served["would"] == printed["would"] and served["roles"] == printed["roles"]
        bad = f"http://{srv.HOST}:{s.server_address[1]}/api/report/observe?since=yesterday"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad, timeout=5)
        assert e.value.code == 400
    finally:
        s.shutdown()
        s.server_close()
        reader.close()
    assert cli.main(["report", "observe", "--since", "last week", "--ledger", str(path)]) == 2
