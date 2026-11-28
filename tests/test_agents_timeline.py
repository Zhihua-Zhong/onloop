"""Agents and the Timeline (0.7.0 plan, sections 1, 2 and 7.3): each role's state in plain words, with "stopped" the only
stuck, never a format-rule break; and the Timeline's window figures summed from a per-day cache, never from the
ledger's start. The agreement with Home is in test_agreement.py."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

from onloop import agents, derive, timeline_page
from onloop import events as ev
from onloop.overlay.ledger import Ledger

NOW = "2026-11-09T14:52:00.000Z"
TOOL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "demo_ledger.py"
_spec = importlib.util.spec_from_file_location("demo_ledger", TOOL)
demo = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("demo_ledger", demo)
_spec.loader.exec_module(demo)
FIX = pathlib.Path(__file__).parent / "fixtures" / "hooks"


@pytest.fixture(scope="module")
def demo_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("agents") / "ledger.db"
    demo.build(path, NOW)
    return path


def _card(cards, role):
    return next(c for c in cards["agents"] if c["role"] == role)


def test_each_role_has_a_state_in_plain_words(demo_path):
    with Ledger(demo_path, readonly=True) as led:
        c = agents.cards(led, NOW)
    states = {a["role"]: a["state"] for a in c["agents"]}
    assert states["builder"] == "working"  # a recorded step is open
    assert states["reviewer"] == "paused" and _card(c, "reviewer")["command"] == "onloop role resume reviewer"
    assert states["operator"] == "waiting" and _card(c, "operator")["open_asks"] == 3
    assert states["designer"] == "idle"
    assert _card(c, "builder")["on_now"] == "on task t-cred"


def test_a_format_rule_break_never_makes_a_role_look_stuck(demo_path):
    """The demo's lead, builder and reviewer each ended a turn without a valid outcome line (Rule alerts counts them):
    none is "stopped", and the lead's card says when its last turn ended, nothing about the rule."""
    with Ledger(demo_path, readonly=True) as led:
        c = agents.cards(led, NOW)
    assert all(a["state"] != "stopped" for a in c["agents"])
    lead = _card(c, "lead")
    assert lead["on_now"].startswith("its last turn ended") and "outcome" not in lead["on_now"]


def test_a_run_that_started_and_did_nothing_for_an_hour_is_stopped(tmp_path):
    clock = ["2026-11-09T10:00:00.000Z"]
    with Ledger(tmp_path / "l.db", clock=lambda: clock[0]) as led:
        led.append(
            {
                "id": "run-1-start",
                "source": "carrier",
                "kind": "run.started",
                "subject_ref": "run:1",
                "actor": "builder",
                "payload": {"id": "run:1", "role": "builder", "session_id": "s1", "lease_id": "l1"},
            }
        )
        early = agents.cards(led, "2026-11-09T10:30:00.000Z")
        late = agents.cards(led, "2026-11-09T11:30:00.000Z")
    assert _card(early, "builder")["state"] == "working"
    stopped = _card(late, "builder")
    assert stopped["state"] == "stopped" and "has done nothing since" in stopped["words"]


def test_an_agents_page_holds_its_week_its_decisions_and_its_lane(demo_path):
    with Ledger(demo_path, readonly=True) as led:
        d = agents.detail(led, "operator", NOW)
        assert agents.detail(led, "nobody", NOW) is None
    assert len(d["by_day"]) == 7 and d["card"]["role"] == "operator"
    assert any(x["title"].startswith("approve the schema change") for x in d["decisions"]["open"])
    with Ledger(demo_path, readonly=True) as led:
        assert sum(x["turns"] for x in d["by_day"]) == derive.turns_per_role(led, d["from"], d["to"]).get(
            "operator", 0
        )


def test_the_timeline_reads_the_window_only_and_caches_past_days(demo_path, monkeypatch):
    with Ledger(demo_path, readonly=True) as led:
        timeline_page._CACHE.clear()
        first = timeline_page.build(led, NOW, 7)
        calls = []
        real = derive.timeline
        monkeypatch.setattr(derive, "timeline", lambda *a, **k: calls.append(a[1:3]) or real(*a, **k))
        again = timeline_page.build(led, NOW, 7)
    assert again["figures"] == first["figures"]
    start = ev.shift_ts(NOW, -7 * 86400)
    assert all(a >= start for a, _ in calls)  # nothing before the window is read
    assert len(calls) == 1  # the lanes; every day's figures came from the cache
    with pytest.raises(ValueError, match="days is one of"):
        with Ledger(demo_path, readonly=True) as led:
            timeline_page.build(led, NOW, 3)


def test_a_day_that_gains_an_event_is_computed_again(demo_path, tmp_path):
    path = tmp_path / "l.db"
    path.write_bytes(demo_path.read_bytes())
    timeline_page._CACHE.clear()
    with Ledger(path, readonly=True) as led:
        before = {f["role"]: f["tokens"] for f in timeline_page.build(led, NOW, 1)["figures"]}
    with Ledger(path, clock=lambda: ev.shift_ts(NOW, -3600)) as led:
        led.append(
            {
                "id": "late-cost",
                "source": "otel",
                "kind": "cost.request",
                "subject_ref": "session:s-designer",
                "actor": "designer",
                "payload": {
                    "input_tokens": 1000,
                    "output_tokens": 0,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 0,
                },
            }
        )
    with Ledger(path, readonly=True) as led:
        after = {f["role"]: f["tokens"] for f in timeline_page.build(led, NOW, 1)["figures"]}
    assert after["designer"] == before.get("designer", 0) + 1000


def test_the_routes_answer(demo_path):
    import http.client
    import threading

    from onloop.daemon import main as daemon
    from onloop.daemon import server as srv

    reader = daemon.LockedReader(demo_path)
    s = srv.make_server(reader, port=0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    try:
        port = s.server_address[1]

        def get(path):
            c = http.client.HTTPConnection(srv.HOST, port, timeout=10)
            c.request("GET", path, headers={"Host": f"localhost:{port}"})
            r = c.getresponse()
            body = json.loads(r.read())
            c.close()
            return r.status, body

        code, body = get("/api/agents")
        assert code == 200 and body["agents"]
        code, body = get("/api/agents/builder")
        assert code == 200 and body["card"]["role"] == "builder"
        assert get("/api/agents/nobody")[0] == 404
        code, body = get("/api/timeline?days=2")
        assert code == 200 and body["days"] == 2
        assert get("/api/timeline?days=3")[0] == 400
    finally:
        s.shutdown()
        s.server_close()
        reader.close()
