"""Home (0.7.0 plan, sections 1 and 2): what GET /api/home answers, from 14 days at most, and Details · Rule alerts.

The agreement with the other views is in test_agreement.py; these are Home's own rules."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from onloop import derive, home
from onloop import events as ev
from onloop.overlay.ledger import Ledger

NOW = "2026-11-09T14:52:00.000Z"
TOOL = Path(__file__).resolve().parent.parent / "tools" / "demo_ledger.py"
_spec = importlib.util.spec_from_file_location("demo_ledger", TOOL)
demo = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("demo_ledger", demo)
_spec.loader.exec_module(demo)


@pytest.fixture(scope="module")
def demo_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("home") / "ledger.db"
    demo.build(path, NOW)
    return path


def _copy(demo_path, tmp_path):
    path = tmp_path / "ledger.db"
    path.write_bytes(demo_path.read_bytes())
    return path


def _at(clock, ts):
    clock[0] = ts


def _turn(led, eid, role, line="outcome: done ref=pr#1"):
    led.append(
        {
            "id": eid,
            "source": "hook",
            "kind": "hook.stop",
            "subject_ref": f"role:{role}",
            "payload": {
                "role": role,
                "session_id": f"s-{role}",
                "line": line,
                "outcome": {"value": "done", "ref": "pr#1", "owner": None, "waker": None},
            },
        }
    )


def _merge(led, eid, pr, at):
    led.append(
        {"id": eid, "source": "github", "kind": "pr.merged", "subject_ref": pr, "at": at, "payload": {}}
    )


def _cost(led, eid, role, n):
    led.append(
        {
            "id": eid,
            "source": "otel",
            "kind": "cost.request",
            "subject_ref": f"session:s-{role}",
            "actor": role,
            "payload": {
                "input_tokens": n,
                "output_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
            },
        }
    )


def test_home_on_the_demo_answers_its_questions(demo_path):
    with Ledger(demo_path, readonly=True) as led:
        h = home.build(led, NOW)
    assert h["from"] == ev.shift_ts(NOW, -7 * 86400) and h["to"] == NOW and not h["frozen"]
    assert h["numbers"]["delivered"]["value"] > 0 and h["numbers"]["delivered"]["recorded"]
    assert h["decisions"]["items"] and all(
        not i["title"].startswith("An ask with no effect") for i in h["decisions"]["items"]
    )
    assert h["decisions"]["rule_asks"] > 0  # the demo's incomplete asks: counted, not listed
    assert h["brief"]["generated"] is None and h["brief"]["facts"]
    assert [lane["role"] for lane in h["lanes"]["lanes"]]


def test_the_brief_says_only_numbers_the_page_shows(demo_path):
    with Ledger(demo_path, readonly=True) as led:
        h = home.build(led, NOW)
    shown = {
        str(h["decisions"]["waiting_on_you"]),
        str(h["decisions"]["count"]),
        str(h["numbers"]["delivered"]["value"]),
        str(h["numbers"]["delivered"]["last_week"]),
        *re.findall(r"\d+", " ".join(i["title"] for i in h["decisions"]["items"])),
        *re.findall(r"\d+", derive.plain_age(h["decisions"]["items"][0]["age_s"])),
        *re.findall(
            r"\d+", " ".join(f"{u['title']} {u['text']}" for u in h["unusual"])
        ),  # Unusual is on the page
    }
    said = set(re.findall(r"\d+", " ".join(h["brief"]["facts"])))
    assert said <= shown | {"7"}, said - shown  # "the last 7 days" is the window, shown in the page's heading


def test_home_reads_14_days_at_most(demo_path, tmp_path):
    """Anything older than two weeks changes nothing Home answers."""
    path = _copy(demo_path, tmp_path)
    with Ledger(path, readonly=True) as led:
        before = home.build(led, NOW)
    old = ev.shift_ts(NOW, -20 * 86400)  # the demo already reaches two weeks back; this is older still
    clock = [old]
    with Ledger(path, clock=lambda: clock[0]) as led:
        _merge(led, "old-merge", "pr#900", old)
        _cost(led, "old-cost", "builder", 10**6)
        _turn(led, "old-turn", "builder")
    with Ledger(path, readonly=True) as led:
        after = home.build(led, NOW)
    assert after["young"] is False and before["young"] is False
    for k in ("numbers", "decisions", "unusual", "lanes", "brief"):
        assert after[k] == before[k], k
    for k in ("delivered", "waited_h"):
        assert after["numbers"][k]["value"] == before["numbers"][k]["value"]
        assert after["numbers"][k]["by_day"] == before["numbers"][k]["by_day"]
    assert after["numbers"]["cost_per_delivery"]["tokens"] == before["numbers"]["cost_per_delivery"]["tokens"]


def test_with_two_weeks_of_data_each_number_has_its_week_before(tmp_path):
    clock = [ev.shift_ts(NOW, -13 * 86400)]
    with Ledger(tmp_path / "l.db", clock=lambda: clock[0]) as led:
        _turn(
            led, "first", "builder"
        )  # the ledger began 13 days ago... and an older event makes it two weeks
        _at(clock, ev.shift_ts(NOW, -15 * 86400))
        _turn(led, "older", "builder")
        for i, d in enumerate((10, 9, 3, 2, 1)):
            _merge(led, f"m{i}", f"pr#{i}", ev.shift_ts(NOW, -d * 86400))
        h = home.build(led, NOW)
    d = h["numbers"]["delivered"]
    assert (d["value"], d["last_week"], d["delta"]) == (3, 2, 1)
    assert h["young"] is False


def test_a_quiet_role_is_unusual_against_its_own_norm(tmp_path):
    """A role that turned every hour for ten days and has been quiet for two days: three times its usual is unusual."""
    clock = [ev.shift_ts(NOW, -12 * 86400)]
    with Ledger(tmp_path / "l.db", clock=lambda: clock[0]) as led:
        for i in range(10 * 24):
            _at(clock, ev.shift_ts(NOW, -12 * 86400 + i * 3600))
            _turn(led, f"t{i}", "builder")
        h = home.build(led, NOW)
    quiet = [u for u in h["unusual"] if u["kind"] == "quiet"]
    assert [u["role"] for u in quiet] == ["builder"]
    assert quiet[0]["norm"] == 3600 and quiet[0]["value"] > 3 * 3600
    assert "builder quiet for 2 days" == quiet[0]["title"]


def test_a_role_within_its_norm_is_not_unusual(tmp_path):
    clock = [ev.shift_ts(NOW, -10 * 86400)]
    with Ledger(tmp_path / "l.db", clock=lambda: clock[0]) as led:
        for i in range(10 * 24):
            _at(clock, ev.shift_ts(NOW, -10 * 86400 + i * 3600))
            _turn(led, f"t{i}", "builder")
        h = home.build(led, ev.shift_ts(NOW, -3600))
    assert h["unusual"] == []


def test_a_frozen_copy_is_read_up_to_its_last_event(demo_path):
    with Ledger(demo_path, readonly=True) as led:
        last = home.last_event(led)
        h = home.build(led, "2030-01-01T00:00:00.000Z", last)
    assert h["frozen"] and h["to"] == last and h["from"] == ev.shift_ts(last, -7 * 86400)
    assert h["numbers"]["delivered"]["value"] > 0  # the copy's own last week, not an empty one


def test_rule_alerts_count_what_home_never_lists(demo_path):
    with Ledger(demo_path, readonly=True) as led:
        r = home.rule_alerts(led, NOW)
        h = home.build(led, NOW)
    assert r["asks_missing"]["count"] == h["decisions"]["rule_asks"] > 0
    assert sum(r["asks_missing"]["by_role"].values()) == r["asks_missing"]["count"]
    # the tidy offers only the asks with no effect: one that lacks `holds` says what it changes, and stays
    with Ledger(demo_path, readonly=True) as led:
        no_effect = {
            t["id"]
            for t in led.select("SELECT id FROM human_tasks WHERE stage <> 'resolved' AND effect IS NULL")
        }
    assert set(r["asks_missing"]["ids"]) == no_effect and 0 < len(no_effect) < r["asks_missing"]["count"]
    words = {a["words"] for a in r["alerts"]}
    assert "turns that ended without a valid outcome line" in words
    assert all(
        not w.startswith(("lead's", "designer's", "builder's")) for w in words
    )  # the rule, not the role
    assert sum(a["count"] for a in r["alerts"]) == sum(
        1
        for a in Ledger(demo_path, readonly=True).alerts()
        if (a.get("detail") or {}).get("detector") != "schedule"
    )
    assert r["constraint"]["on"] == "merge-check" and r["since_visit"]["added"] >= 0


def test_an_empty_ledger_has_a_quiet_home(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: NOW) as led:
        h = home.build(led, NOW)
        r = home.rule_alerts(led, NOW)
    assert h["decisions"] == {"waiting_on_you": 0, "oldest_s": None, "count": 0, "items": [], "rule_asks": 0}
    assert (
        h["numbers"]["delivered"]["recorded"] is False and h["numbers"]["cost_per_delivery"]["value"] is None
    )
    assert h["brief"]["facts"][0] == "Nothing waits on you."
    assert h["unusual"] == [] and r["asks_missing"]["count"] == 0 and r["alerts"] == []
