"""One count of "waiting on you" on every view (finding #20): Now, the map and the task list read the same projection."""

from __future__ import annotations

import datetime as dt
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from onloop import derive, tasks
from onloop.contract import reader
from onloop.overlay.ledger import Ledger
from onloop.report import views

NOW = "2026-11-09T14:52:00.000Z"
TOOL = Path(__file__).resolve().parent.parent / "tools" / "demo_ledger.py"
_spec = importlib.util.spec_from_file_location("demo_ledger", TOOL)
demo = importlib.util.module_from_spec(_spec)
sys.modules["demo_ledger"] = demo
_spec.loader.exec_module(demo)


def _counts(led, now):
    n = views.now(led, now)
    t = views.tasks_view(led, now)
    z = derive.zoom0(led, now)
    you = next((w for w in z["waits"] if w["holder"] == "you"), None)
    return {
        "map_signal": z["signals"]["waiting_on_you"],
        "map_you_row": you["count"] if you else 0,
        "now": len(n["waiting_on_you"]),
        "tasks_api": t["count"],
        "task_list": len(tasks.open_tasks(led, now)),
    }


def _v1_ask(led, eid, ref):
    led.append(
        {
            "id": eid,
            "source": "hook",
            "kind": "hook.stop",
            "subject_ref": "role:lead",
            "payload": {
                "role": "lead",
                "session_id": "s",
                "line": f"outcome: blocked owner=human ref={ref}",
                "outcome": {"value": "blocked", "ref": ref, "owner": "human", "waker": None},
            },
        }
    )


@pytest.fixture(scope="module")
def demo_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "ledger.db"
    demo.build(path, NOW)
    return path


def test_now_flow_and_the_task_list_agree_on_the_demo_ledger(demo_path):
    with Ledger(demo_path, readonly=True) as led:
        c = _counts(led, NOW)
    assert len(set(c.values())) == 1 and c["now"] > 0, c


def test_they_agree_when_some_asks_are_on_documents_and_decisions(demo_path, tmp_path):
    """The dogfood's shape: asks on documents and decisions as well as changes. The count is the task list's."""
    path = tmp_path / "ledger.db"
    path.write_bytes(demo_path.read_bytes())
    with Ledger(path, clock=lambda: NOW) as led:
        for i, ref in enumerate(("docs/plan.md", "a-decision", "pr#99")):
            _v1_ask(led, f"ask{i}", ref)
        c = _counts(led, NOW)
    assert len(set(c.values())) == 1, c


EXAMPLE = Path(__file__).resolve().parent.parent / "tools" / "contract_example.py"
_espec = importlib.util.spec_from_file_location("contract_example", EXAMPLE)
example = importlib.util.module_from_spec(_espec)
sys.modules.setdefault("contract_example", example)
_espec.loader.exec_module(example)
REPLAY_END = dt.datetime(2026, 11, 17, 2, 0, 0, tzinfo=dt.UTC)
REPLAY_NOW = "2026-11-17T03:00:00.000Z"


@pytest.fixture(scope="module")
def replay_path(tmp_path_factory):
    """The generic replay: two daily snapshots in contract 1.1, made up, imported as a team's export is. It has the
    shapes the demo lacks: imported turns, a wait on the person that opens a task, waits on a role and on the team."""
    root = tmp_path_factory.mktemp("replay")
    example.write(root, REPLAY_END, days=2, n=400, seed=3, contract="1.1")
    (root / "onloop").mkdir()
    results = reader.import_snapshots(root, root / "onloop" / "ledger.db")
    assert [r.status for r in results] == ["imported", "imported"], [r.as_dict() for r in results]
    return root / "onloop" / "ledger.db"


def test_now_the_map_and_the_task_list_agree_on_the_generic_replay(replay_path):
    with Ledger(replay_path, readonly=True) as led:
        c = _counts(led, REPLAY_NOW)
    assert len(set(c.values())) == 1 and c["now"] > 0, c
    with Ledger(replay_path, readonly=True) as led:
        z = derive.zoom0(led, REPLAY_NOW)
        refs = {t["ref"] for t in tasks.open_tasks(led, REPLAY_NOW)}
    leaves = {lf["id"]: lf for b in z["blocks"] for lf in b["leaves"]}
    assert refs & set(leaves) and all(leaves[r]["colour"] == "amber" for r in refs & set(leaves))


def test_an_empty_ledger_has_nothing_waiting_anywhere(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: NOW) as led:
        assert set(_counts(led, NOW).values()) == {0}


def test_tasks_without_any_item_still_agree_and_now_names_the_constraint(tmp_path):
    """The lead's check on the dogfood ledger: tasks, but no items at all. Every view counts the 3 tasks, and Now names
    the holder of the handoff as the constraint."""
    with Ledger(tmp_path / "l.db", clock=lambda: NOW) as led:
        for i, ref in enumerate(("docs/plan.md", "a-decision", "release-checklist")):
            _v1_ask(led, f"ask{i}", ref)
        led.append(
            {
                "id": "handoff",
                "source": "hook",
                "kind": "hook.stop",
                "subject_ref": "role:builder",
                "payload": {
                    "role": "builder",
                    "session_id": "s2",
                    "line": "outcome: handoff owner=lead ref=docs/plan.md",
                    "outcome": {"value": "handoff", "ref": "docs/plan.md", "owner": "lead", "waker": None},
                },
            }
        )
        c = _counts(led, NOW)
        n = views.now(led, NOW)
    assert len(set(c.values())) == 1 and c["now"] == 3, c
    assert n["constraint"] is not None


def _window(led, now, hours=72):
    """The window Activity used (to the data's end, a replayed export's, else now), which Replay's views read."""
    from onloop import events as ev

    as_of = derive.data_as_of(led)
    end = min(now, as_of) if as_of else now
    return ev.shift_ts(end, -hours * 3600), end


@pytest.mark.parametrize("which", ["demo", "replay"])
def test_replay_counts_each_roles_turns_from_the_one_derivation(which, demo_path, replay_path):
    """Replay's lanes and the map count a role's turns the same way: `onloop.derive`, never their own loop."""
    path, now = (demo_path, NOW) if which == "demo" else (replay_path, REPLAY_NOW)
    with Ledger(path, readonly=True) as led:
        start, end = _window(led, now)
        per_role = derive.turns_per_role(led, start, end)
        t = derive.timeline(led, start, end, now)
    assert sum(per_role.values()) > 0
    assert {L["lane"]: L["turns"] for L in t["lanes"] if L["turns"]} == per_role


_ON_YOU = re.compile(r"(\d+) waits? on you")


@pytest.mark.parametrize("which", ["demo", "replay"])
def test_the_maps_words_never_add_up_to_more_than_the_one_count(which, demo_path, replay_path):
    """The lead's check: a reader sums every "N wait on you" on the page against the header. Only the person's own
    tasks and waits say it; what a team's export marks as theirs is worded as the team's mark."""
    path, now = (demo_path, NOW) if which == "demo" else (replay_path, REPLAY_NOW)
    with Ledger(path, readonly=True) as led:
        z = derive.zoom0(led, now)
    said = sum(int(n) for b in z["blocks"] for n in _ON_YOU.findall(b["tally_text"]))
    assert said <= z["signals"]["waiting_on_you"], (said, [b["tally_text"] for b in z["blocks"]])
    for b in z["blocks"]:
        if b["label"] == "waits on you":
            assert _ON_YOU.search(b["tally_text"]), b["id"]
        marked = [lf for lf in b["leaves"] if lf["colour"] == "amber" and lf.get("who") != "you"]
        said_marked = sum(int(n) for n in re.findall(r"(\d+) marked yours by the team", b["tally_text"]))
        assert said_marked == len(marked) and all(lf.get("reported_by_team") for lf in marked), b["id"]


def test_the_holder_a_reason_names_is_the_holder_zoom_2_lists(tmp_path_factory):
    """Contract 1.2: zoom 0 and 1 name who holds a stuck change, and zoom 2 lists the same holder, on a replay where
    the export says which role opened each change."""
    root = tmp_path_factory.mktemp("replay12")
    example.write(root, REPLAY_END, days=2, n=200, seed=3, contract="1.2")
    (root / "onloop").mkdir()
    reader.import_snapshots(root, root / "onloop" / "ledger.db")
    with Ledger(root / "onloop" / "ledger.db", readonly=True) as led:
        for b in derive.zoom0(led, REPLAY_NOW)["blocks"]:
            for s in derive.zoom1(led, REPLAY_NOW, b["id"])["stages"]:
                if not s["reason"]:
                    continue
                holders = {h["holder"] for h in derive.zoom2(led, REPLAY_NOW, b["id"], s["stage"])["holders"]}
                named = s["reason"]["text"].split(": ", 1)[0]
                assert named in holders or (named == "you" and "you" in holders), (
                    b["id"],
                    s["stage"],
                    named,
                    holders,
                )


@pytest.mark.parametrize("which", ["demo", "replay", "replay12"])
def test_the_stage_by_role_grid_counts_what_zoom_1_and_zoom_2_count(
    which, demo_path, replay_path, tmp_path_factory
):
    """Mock-up v2's grid in the product: one table per goal, a column per stage and a row per holder. Every cell is
    the same leaves zoom 1 counts and zoom 2 groups, so the grid, the stage blocks and the holder list never differ:
    a stage's cells (with Idle, for Opened) add up to its zoom 1 count, and each holder's cell to zoom 2's count."""
    if which == "replay12":
        root = tmp_path_factory.mktemp("grid12")
        example.write(root, REPLAY_END, days=2, n=200, seed=3, contract="1.2")
        (root / "onloop").mkdir()
        reader.import_snapshots(root, root / "onloop" / "ledger.db")
        path, now = root / "onloop" / "ledger.db", REPLAY_NOW
    else:
        path, now = (demo_path, NOW) if which == "demo" else (replay_path, REPLAY_NOW)
    checked = idle_total = red_total = 0
    with Ledger(path, readonly=True) as led:
        z0 = derive.zoom0(led, now)
        for b in z0["blocks"]:
            stages = derive.zoom1(led, now, b["id"])["stages"]
            g = derive.zoom2(led, now, b["id"], stages[0]["stage"])["grid"]
            col = {c["stage"]: c for c in g["columns"]}
            for s in stages:
                if not s["reported"]:
                    assert col[s["stage"]]["count"] is None, (
                        b["id"],
                        s["stage"],
                    )  # "not reported", never zero
                    continue
                in_grid = sum(r["cells"].get(s["stage"], {}).get("count", 0) for r in g["rows"])
                if s["stage"] == "opened" and "idle" in col:
                    in_grid += col["idle"]["count"]
                assert in_grid == s["count"], (which, b["id"], s["stage"], in_grid, s["count"])
                if s["stage"] == "merged" or not s["count"]:
                    continue
                z2 = {h["holder"]: h["count"] for h in derive.zoom2(led, now, b["id"], s["stage"])["holders"]}
                cells = {
                    r["holder"]: r["cells"].get(s["stage"], {}).get("count", 0)
                    + (r["cells"].get("idle", {}).get("count", 0) if s["stage"] == "opened" else 0)
                    for r in g["rows"]
                }
                assert {h: n for h, n in cells.items() if n} == z2, (which, b["id"], s["stage"])
                checked += 1
            rows = g["rows"]
            assert rows[1:] == sorted(
                rows[1:], key=lambda r: (-r["live_red"], -r["idle"], -r["open"], r["holder"])
            )
            # the glance and the grid name the same holder: zoom 0's reason line holder is the grid's first row
            if b["reason"]:
                child = b["reason"]["child"]
                first = rows[0]
                assert any(i["id"] == child for c, v in first["cells"].items() for i in v["items"]), (
                    which,
                    b["id"],
                )
                assert b["reason"]["text"].split(": ", 1)[0] == first["holder"], (
                    which,
                    b["id"],
                    first["holder"],
                )
                assert not any(
                    i["id"] == child and i["idle"] for v in first["cells"].values() for i in v["items"]
                )
            # idle is set apart at every level: the block's tally, the grid's Idle column and the header agree
            idle_col = col["idle"]["count"] if "idle" in col else 0
            assert b["tally"]["idle"] == idle_col, (which, b["id"])
            if idle_col:
                assert f"{idle_col} idle" in b["tally_text"], b["tally_text"]
            live_red = sum(r["live_red"] for r in rows)
            assert b["tally"]["red"] == live_red, (which, b["id"], b["tally"], live_red)
            idle_total += idle_col
            red_total += b["tally"]["red"]
        assert z0["signals"]["idle"] == idle_total and z0["signals"]["stuck"] == red_total
    assert checked > 0


@pytest.mark.parametrize("which", ["demo", "replay"])
def test_a_wait_on_team_and_a_change_with_no_opener_are_one_holder_the_team(which, demo_path, replay_path):
    """The lead's finding on bebf0b9: "team" held a jammed change in Review and "the team" ten in Merged, two rows of
    the grid for what a person reads as one holder. `team` and no name at all are "the team" on every view."""
    path, now = (demo_path, NOW) if which == "demo" else (replay_path, REPLAY_NOW)
    seen = set()
    with Ledger(path, readonly=True) as led:
        z = derive.zoom0(led, now)
        seen |= {w["holder"] for w in z["waits"]}
        for b in z["blocks"]:
            seen |= {lf.get("who") for lf in b["leaves"] if lf.get("who")}
            for s in derive.zoom1(led, now, b["id"])["stages"]:
                if s["reason"]:
                    seen.add(s["reason"]["text"].split(": ", 1)[0])
                if not s["count"]:
                    continue
                z2 = derive.zoom2(led, now, b["id"], s["stage"])
                seen |= {h["holder"] for h in z2["holders"]}
                rows = [r["holder"] for r in z2["grid"]["rows"]]
                assert len(rows) == len(set(rows)), (b["id"], rows)  # one row per holder
                seen |= set(rows)
    assert "team" not in seen and "" not in seen, sorted(seen)
    assert derive.TEAM in seen  # both ledgers have work the team holds as a whole
    assert derive.holder_name("team") == derive.holder_name(None) == derive.holder_name("") == derive.TEAM
    assert derive.holder_name("reviewer") == "reviewer"


@pytest.mark.parametrize("which", ["demo", "replay"])
def test_the_timeline_counts_turns_from_the_one_derivation_and_each_second_once(
    which, demo_path, replay_path
):
    """The replay's timeline (decision 0019) reads turns from the one derivation, as Activity does; within a lane its
    segments never overlap, their times are its four kinds of time, and its tokens are its steps plus between steps."""
    from onloop import events as ev

    path, now = (demo_path, NOW) if which == "demo" else (replay_path, REPLAY_NOW)
    with Ledger(path, readonly=True) as led:
        start, end = _window(led, now)
        per_role = derive.turns_per_role(led, start, end)
        t = derive.timeline(led, start, end, now)
    assert {L["lane"]: L["turns"] for L in t["lanes"] if L["turns"]} == per_role
    window = (ev.parse_ts(end) - ev.parse_ts(start)).total_seconds()
    for L in t["lanes"]:
        segs = L["segments"]
        assert all(s[0] < s[1] for s in segs) and all(
            x[1] <= y[0] for x, y in zip(segs, segs[1:], strict=False)
        )
        for k in t["kinds"]:
            got = sum((ev.parse_ts(s[1]) - ev.parse_ts(s[0])).total_seconds() for s in segs if s[2] == k)
            assert abs(got - L["time_s"][k]) < 0.01, (L["lane"], k)
        assert sum(L["time_s"].values()) <= window + 0.01
        assert L["tokens"]["total"] == L["between_steps"]["total"] + sum(
            s["tokens"]["total"] for s in L["steps"]
        )
    assert t["totals"]["turns"] == sum(per_role.values())


@pytest.mark.parametrize("which", ["demo", "replay"])
def test_the_persons_wait_is_one_count_on_now_the_map_and_replay(which, demo_path, replay_path):
    """Decision 0019, a7: Now's line, the replay's window and the map count what waits on you from one derivation."""
    from onloop.report import replay

    path, now = (demo_path, NOW) if which == "demo" else (replay_path, REPLAY_NOW)
    with Ledger(path, readonly=True) as led:
        n = views.now(led, now)
        z = derive.zoom0(led, now)
        w = replay.window(led, now, ev_shift(now, -6 * 3600), now)
    counts = {
        "now_line": n["your_wait"]["count"],
        "now_list": len(n["waiting_on_you"]),
        "map": z["signals"]["waiting_on_you"],
        "replay": w["your_wait"]["count"],
    }
    assert len(set(counts.values())) == 1, counts
    assert 0.0 <= w["your_wait"]["share"] <= 1.0 and 0.0 <= n["your_wait"]["share"] <= 1.0


def ev_shift(ts, s):
    from onloop import events as ev

    return ev.shift_ts(ts, s)
