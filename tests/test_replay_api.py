"""GET /api/replay (decision 0019, §3): a frame at any time in the window, built from the window alone, equals the
ledger's own timeline up to that time; a frame at `from` is the ledger's state at `from`."""

import bisect
import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from onloop import derive
from onloop import events as ev
from onloop.daemon import server as srv
from onloop.overlay.ledger import Ledger
from onloop.report import replay
from onloop.report.views import QueryError

from .test_timeline import at
from .test_timeline import led as led  # noqa: F401 - the fixture

NOW = "2026-11-09T14:52:00.000Z"
TOOL = Path(__file__).resolve().parent.parent / "tools" / "demo_ledger.py"


def frame(w, t_ms):
    """What the page computes: at_from, plus each lane's changes and requests up to t. Uses nothing before `from`."""
    kinds = w["kinds"]
    out = []
    for i, _name in enumerate(w["lanes"]):
        base = w["at_from"][i]
        time = dict(base["time_s"])
        state, since = base["state"], w["from_ms"]
        ct = [
            (t, s)
            for t, ln, s in zip(w["changes"]["t"], w["changes"]["lane"], w["changes"]["state"], strict=True)
            if ln == i
        ]
        for t, s in ct[: bisect.bisect_right([c[0] for c in ct], t_ms)]:
            if state >= 0:
                time[kinds[state]] += (t - since) / 1000
            state, since = s, t
        if state >= 0:
            time[kinds[state]] += (t_ms - since) / 1000
        tokens = base["tokens"] + sum(
            tot
            for t, ln, tot in zip(
                w["requests"]["t"], w["requests"]["lane"], w["requests"]["total"], strict=True
            )
            if ln == i and t <= t_ms
        )
        out.append({"state": state, "time_s": time, "tokens": tokens})
    return out


def truth(reader, origin, t_iso, now, names):
    lanes = {L["lane"]: L for L in derive.timeline(reader, origin, t_iso, now)["lanes"]}
    return [
        {
            "time_s": dict(lanes[n]["time_s"])
            if n in lanes
            else dict.fromkeys(("working", "harness", "waiting", "queued"), 0.0),
            "tokens": lanes[n]["tokens"]["total"] if n in lanes else 0,
        }
        for n in names
    ]


def iso(ms):
    import datetime as dt

    return ev.norm_ts(dt.datetime.fromtimestamp(ms / 1000, dt.UTC))


def check(reader, w, now, samples):
    origin = replay.ORIGIN
    for t_ms in samples:
        got, want = frame(w, t_ms), truth(reader, origin, iso(t_ms), now, w["lanes"])
        for name, g, x in zip(w["lanes"], got, want, strict=True):
            assert g["tokens"] == x["tokens"], (name, iso(t_ms))
            for k in w["kinds"]:
                assert abs(g["time_s"][k] - x["time_s"][k]) < 0.01, (name, k, iso(t_ms), g, x)


def test_every_frame_in_the_window_equals_the_ledgers_timeline_to_that_time(led):  # noqa: F811
    now = at("10:50")
    w = replay.window(led, now, at("10:02"), at("11:00"))
    assert w["lanes"] == ["builder"]
    assert w["at_from"][0]["time_s"] == {
        "working": 0.0,
        "harness": 60.0,
        "waiting": 1800.0,
        "queued": 60.0,
    }  # t0, 09:00-09:30
    assert w["kinds"][w["at_from"][0]["state"]] == "harness"  # the state at `from` is the ledger's
    step = (w["to_ms"] - w["from_ms"]) // 97
    check(led, w, now, [w["from_ms"], *range(w["from_ms"] + 1, w["to_ms"], step), w["to_ms"]])
    assert w["requests"]["step"] == [0, -1] and w["steps"] == ["step:task:t-12"]
    assert frame(w, w["to_ms"])[0]["time_s"] == pytest.approx(w["at_to"][0]["time_s"])


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("demo_ledger", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["demo_ledger"] = mod
    spec.loader.exec_module(mod)
    path = tmp_path_factory.mktemp("demo") / "ledger.db"
    mod.build(path, NOW)
    return path


@pytest.mark.parametrize(
    ("frm", "to"),
    [("2026-11-09T11:00:00.000Z", NOW), ("2026-11-09T14:00:00.000Z", "2026-11-09T14:10:00.000Z")],
)
def test_on_the_demo_a_window_never_needs_earlier_data(demo, frm, to):
    with Ledger(demo, readonly=True) as reader:
        w = replay.window(reader, NOW, frm, to)
        step = max(1, (w["to_ms"] - w["from_ms"]) // 61)
        check(reader, w, NOW, [*range(w["from_ms"], w["to_ms"], step), w["to_ms"]])
        assert set(replay.window(reader, NOW, frm, to, "builder,designer")["lanes"]) <= {
            "builder",
            "designer",
        }


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        (None, NOW),
        (NOW, None),
        ("yesterday", NOW),
        (NOW, "2026-11-09T14:00:00.000Z"),
        ("2026-10-01T00:00:00Z", NOW),
    ],
)
def test_a_window_that_is_not_one_is_refused(demo, frm, to):
    with Ledger(demo, readonly=True) as reader, pytest.raises(QueryError):
        replay.window(reader, NOW, frm, to)


def test_onloopd_serves_the_window_and_answers_400_for_a_bad_one(demo):
    with Ledger(demo, readonly=True) as reader:
        s = srv.make_server(reader, port=0)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        try:
            base = f"http://{srv.HOST}:{s.server_address[1]}/api/replay"
            with urllib.request.urlopen(
                f"{base}?from=2026-11-09T11:00:00Z&to=2026-11-09T14:52:00Z", timeout=10
            ) as r:
                body = json.loads(r.read())
            assert body["lanes"] and len(body["at_from"]) == len(body["lanes"]) == len(body["at_to"])
            assert set(body["changes"]) == {"t", "lane", "state"} and set(body["requests"]) >= {
                "t",
                "lane",
                "total",
            }
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(f"{base}?from=2026-11-09T11:00:00Z", timeout=10)
            assert e.value.code == 400
        finally:
            s.shutdown()
            s.server_close()
