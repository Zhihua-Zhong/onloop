"""The map's zoom 1 (a goal's stages) and zoom 2 (who holds what in a stage), and block.turned (decision 0018).
All data here is made up."""

from __future__ import annotations

import datetime as dt
import importlib.util
import re
from pathlib import Path

import pytest

from onloop import derive
from onloop.contract import reader
from onloop.detect import blocks
from onloop.overlay.ledger import Ledger

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("contract_example_lv", ROOT / "tools" / "contract_example.py")
example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(example)
END = dt.datetime(2026, 11, 17, 2, 0, 0, tzinfo=dt.UTC)
NOW = "2026-11-17T03:00:00.000Z"
LATER = "2026-11-17T03:00:10.000Z"
_ON_YOU = re.compile(r"(\d+) waits? on you")


def _replay(root, contract="1.1"):
    example.write(root, END, days=2, n=300, seed=3, contract=contract)
    (root / "onloop").mkdir()
    reader.import_snapshots(root, root / "onloop" / "ledger.db")
    return root / "onloop" / "ledger.db"


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    return _replay(tmp_path_factory.mktemp("levels"))


@pytest.fixture(scope="module")
def replay_1_0(tmp_path_factory):
    return _replay(tmp_path_factory.mktemp("levels10"), "1.0")


def _all(path, now=NOW):
    with Ledger(path, readonly=True) as led:
        z0 = derive.zoom0(led, now)
        z1 = {b["id"]: derive.zoom1(led, now, b["id"]) for b in z0["blocks"]}
        z2 = {
            (bid, s["stage"]): derive.zoom2(led, now, bid, s["stage"])
            for bid, z in z1.items()
            for s in z["stages"]
            if s["count"]
        }
    return z0, z1, z2


def test_zoom_1_shows_each_goals_stages_in_order(replay):
    _, z1, _ = _all(replay)
    names = {bid: [s["stage"] for s in z["stages"]] for bid, z in z1.items()}
    assert names["goal:release-plan"] == ["to do", "in progress", "done"]
    assert names["changes"] == ["opened", "checks", "review", "merged"]
    feedback = next(v for k, v in names.items() if k.startswith("batch:"))
    assert feedback == [*example.STAGES, "accepted"]  # the team's own stages, as its export declares them


def test_a_stage_is_the_worst_of_its_items_and_a_block_the_worst_of_its_stages(replay):
    z0, z1, _ = _all(replay)
    for b in z0["blocks"]:
        stages = z1[b["id"]]["stages"]
        for s in stages:
            if s["count"]:
                assert s["colour"] == derive.worst([lf["colour"] for lf in s["leaves"]]), (
                    b["id"],
                    s["stage"],
                )
        open_stages = [s for s in stages if s["count"] and s["stage"] != "merged"]
        assert b["colour"] == derive.worst([s["colour"] for s in open_stages]), b["id"]


def test_every_item_is_in_exactly_one_stage_and_one_holder(replay):
    z0, z1, z2 = _all(replay)
    for b in z0["blocks"]:
        in_stages = [lf["id"] for s in z1[b["id"]]["stages"] if s["stage"] != "merged" for lf in s["leaves"]]
        assert sorted(in_stages) == sorted(lf["id"] for lf in b["leaves"]), b["id"]
    for (bid, stage), z in z2.items():
        st = next(s for s in z1[bid]["stages"] if s["stage"] == stage)
        assert sum(h["count"] for h in z["holders"]) == st["count"], (bid, stage)


def test_the_words_at_every_level_add_up_to_no_more_than_the_header(replay):
    """The lead's rule, at zoom 1 and 2 too: a reader who sums every "N wait on you" never passes the header."""
    z0, z1, z2 = _all(replay)
    header = z0["signals"]["waiting_on_you"]
    for z in z1.values():
        assert sum(int(n) for s in z["stages"] for n in _ON_YOU.findall(s["tally_text"])) <= header
    for z in z2.values():
        assert sum(int(n) for h in z["holders"] for n in _ON_YOU.findall(h["tally_text"])) <= header
        for h in z["holders"]:
            if h["kind"] == "marked":
                assert not _ON_YOU.search(h["tally_text"]) and h["holder"] == derive.MARKED


def test_a_stage_with_no_evidence_is_not_reported_at_zoom_1_never_zero(replay_1_0):
    _, z1, _ = _all(replay_1_0)
    by = {s["stage"]: s for s in z1["changes"]["stages"]}
    for name in ("checks", "review"):
        assert by[name]["reported"] is False and by[name]["count"] is None and by[name]["colour"] is None
        assert by[name]["tally_text"] == "not reported by this team's export"


def test_a_red_or_amber_stage_says_which_item_and_why(replay):
    _, z1, _ = _all(replay)
    for z in z1.values():
        for s in z["stages"]:
            if s["colour"] in ("red", "amber"):
                assert s["reason"] and s["reason"]["child"] in {lf["id"] for lf in s["leaves"]}
            else:
                assert s["reason"] is None


def _with_checks(tmp_path, runs):
    path = tmp_path / "l.db"
    with Ledger(path, clock=lambda: NOW) as led:
        for n, (name, conclusion) in enumerate(runs):
            led.append(
                {
                    "id": f"c{n}",
                    "source": "github",
                    "kind": "check.completed",
                    "subject_ref": "pr#7",
                    "at": f"2026-11-17T02:0{n}:00.000Z",
                    "payload": {"name": name, "conclusion": conclusion, "head": "a" * 40},
                }
            )
        return derive.check_summary(led, "pr#7")


def test_skipped_runs_are_counted_never_named_and_a_failed_check_is_named_as_recorded(tmp_path):
    """The lead's ruling: a skipped run's name can be GitHub's unexpanded `${{ … }}`; it is counted, never shown."""
    skipped = "lint (${{ matrix.os || 'linux' }})"  # the name GitHub records when a run's `if` is false
    c = _with_checks(
        tmp_path,
        [("unit tests", "success"), ("build", "success"), (skipped, "skipped"), ("e2e (chrome)", "failure")],
    )
    assert c["text"] == "checks: 1 failed (e2e (chrome)) · 2 passed · 1 skipped"
    assert skipped not in str(c) and c["failed"] == ["e2e (chrome)"]


def test_the_latest_run_of_a_check_counts(tmp_path):
    c = _with_checks(tmp_path, [("unit tests", "failure"), ("unit tests", "success")])
    assert c["text"] == "checks: 1 passed" and c["failed"] == []


def test_zoom_2_links_each_item_to_where_a_person_acts(replay):
    _, _, z2 = _all(replay)
    review = z2[("changes", "review")]
    links = {i["id"]: i["link"] for h in review["holders"] for i in h["items"]}
    assert links["pr#3"]["kind"] == "your task" and links["pr#5"] == {
        "kind": "the change",
        "view": "audit",
        "ref": "pr#5",
    }
    assert [h["holder"] for h in review["holders"]][0] == "the team"  # the worst first: the red one


def test_a_block_that_changes_colour_turns_once(tmp_path):
    path = _replay(tmp_path / "x")
    with Ledger(path, clock=lambda: NOW) as led:
        first = blocks.turn(led, NOW)
        again = blocks.turn(led, LATER)
        assert first and again == []  # the same colours on the next tick: nothing new
        assert "block:changes:red:" + NOW in first
        # the team's stage turns red when it may only go 30 minutes without a move
        derive.set_stuck({"declared": 1800})
        try:
            turned = blocks.turn(led, LATER)
        finally:
            derive.set_stuck({})
        marks = ",".join("?" * len(turned))
        rows = led.select(
            f"SELECT payload FROM events WHERE kind = 'block.turned' AND id IN ({marks})", turned
        )
        same, *_ = led.rebuild_matches()
    import json

    payloads = [r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"]) for r in rows]
    moved = {p["block"]: p for p in payloads}
    batch = next(k for k in moved if k.startswith("batch:") and "/" not in k)
    assert moved[batch]["from"] == "amber" and moved[batch]["to"] == "red" and moved[batch]["reason"]
    assert same  # block.turned feeds no projection: the replay check still matches


def test_onloopds_tick_runs_block_turned(tmp_path):
    from onloop.daemon import main as daemon

    path = _replay(tmp_path / "y")
    cfg = daemon.DaemonConfig(ledger=path, detectors=[], probes={})
    with Ledger(path) as led:
        res = daemon.tick(led, cfg)
    assert res.detected.get("block_turned"), res.errors


def test_within_a_holder_the_worst_and_oldest_item_comes_first(tmp_path):
    """The lead's finding on 8793: a holder's items came in no order, so the oldest, the one zoom 0's reason names,
    was sixth. Worst colour first, then oldest first."""
    path = tmp_path / "l.db"
    with Ledger(path, clock=lambda: NOW) as led:
        for n, day in (
            (1, 10),
            (2, 1),
            (3, 12),
            (4, 16),
            (5, 2),
        ):  # opened on 11-<day>; pr#5 has a check later
            led.append(
                {
                    "id": f"o{n}",
                    "source": "import",
                    "kind": "pr.opened",
                    "subject_ref": f"pr#{n}",
                    "at": f"2026-11-{day:02d}T03:00:00.000Z",
                    "payload": {"number": n},
                }
            )
            led.append(
                {
                    "id": f"a{n}",
                    "source": "import",
                    "kind": "pr.author",
                    "subject_ref": f"pr#{n}",
                    "at": f"2026-11-{day:02d}T03:00:00.000Z",
                    "payload": {"number": n, "by": "builder"},
                }
            )
        led.append(
            {
                "id": "c5",
                "source": "github",
                "kind": "check.completed",
                "subject_ref": "pr#5",
                "at": "2026-11-16T23:00:00.000Z",
                "payload": {"name": "unit tests", "conclusion": "success", "head": "a" * 40},
            }
        )
        z1 = derive.zoom1(led, NOW, "changes")
        z2 = {s["stage"]: derive.zoom2(led, NOW, "changes", s["stage"]) for s in z1["stages"] if s["count"]}
    opened = next(h for h in z2["opened"]["holders"] if h["holder"] == "builder")["items"]
    assert len(opened) == 4  # pr#1-4 wait on nobody in "opened"; pr#5 has a check, so it is in "checks"
    order = [(bool(i.get("idle")), i["colour"], i["age_s"]) for i in opened]
    rank = {c: k for k, c in enumerate(derive.ITEM_ORDER)}
    assert order == sorted(order, key=lambda x: (x[0], rank[x[1]], -x[2])), order
    # live first: red 5 days, then moving 1 day (opened 11-12 and 11-16); then the idle ones, 16 and 7 days old
    # (11-01 and 11-10), set apart at every level by the lead's ruling on e320db4
    assert [i["id"] for i in opened] == ["pr#3", "pr#4", "pr#2", "pr#1"]
    reason = next(s for s in z1["stages"] if s["stage"] == "opened")["reason"]
    assert opened[0]["id"] == reason["child"] == "pr#3"


def test_the_grid_splits_idle_changes_from_the_queue_and_names_the_bottleneck(tmp_path):
    """Mock-up v2: an open change with no move for a week or more is idle, parked or abandoned, and stands in its own
    column; the rest of Opened is the live queue. The bottleneck is the live stage with the most jammed changes."""
    path = tmp_path / "l.db"
    with Ledger(path, clock=lambda: NOW) as led:
        for n, day in ((1, 1), (2, 9), (3, 12), (4, 16), (5, 11), (6, 11)):  # opened on 11-<day>
            for kind, payload in (
                ("pr.opened", {"number": n}),
                ("pr.author", {"number": n, "by": "builder"}),
            ):
                led.append(
                    {
                        "id": f"{kind}{n}",
                        "source": "import",
                        "kind": kind,
                        "subject_ref": f"pr#{n}",
                        "at": f"2026-11-{day:02d}T03:00:00.000Z",
                        "payload": payload,
                    }
                )
        for n in (5, 6):  # two changes in checks, both last moved five days ago
            led.append(
                {
                    "id": f"c{n}",
                    "source": "github",
                    "kind": "check.completed",
                    "subject_ref": f"pr#{n}",
                    "at": "2026-11-12T03:00:00.000Z",
                    "payload": {"name": "unit tests", "conclusion": "success", "head": "a" * 40},
                }
            )
        g = derive.zoom2(led, NOW, "changes", "opened")["grid"]
        z0 = derive.zoom0(led, NOW)
        z1 = derive.zoom1(led, NOW, "changes")
    # the lead's ruling on e320db4: idle is set apart at every level, not only in the grid
    block = next(b for b in z0["blocks"] if b["id"] == "changes")
    assert block["tally_text"].startswith(
        "3 jammed · 2 idle"
    )  # pr#3 opened, pr#5 and pr#6 in checks; pr#1, pr#2
    assert z0["signals"]["stuck"] == 3 and z0["signals"]["idle"] == 2
    assert block["reason"]["child"] not in (
        "pr#1",
        "pr#2",
    )  # the reason line names live work, never an idle change
    opened = next(s for s in z1["stages"] if s["stage"] == "opened")
    assert opened["reason"]["child"] == "pr#3" and opened["colour"] == "red"
    assert [c["stage"] for c in g["columns"]] == ["idle", "opened", "checks", "review", "merged"]
    row = next(r for r in g["rows"] if r["holder"] == "builder")
    # pr#1 (16 days) and pr#2 (8 days) are idle; pr#3 (5 days) and pr#4 (1 day) wait in the queue
    assert [i["id"] for i in row["cells"]["idle"]["items"]] == ["pr#1", "pr#2"]
    assert [i["id"] for i in row["cells"]["opened"]["items"]] == ["pr#3", "pr#4"]
    assert row["cells"]["checks"]["count"] == 2 and row["idle"] == 2 and row["open"] == 6
    col = {c["stage"]: c for c in g["columns"]}
    assert (
        col["opened"]["flow"] == 4 and col["checks"]["flow"] == 2
    )  # in the week to 11-17: not pr#1 (11-01) or pr#2 (11-09)
    assert (
        g["bottleneck"] == "checks"
    )  # 2 jammed in checks against 1 in the live queue (pr#3); idle is not live
    assert g["idle_after_s"] == 7 * 86400


def test_a_stage_or_holder_with_only_idle_work_is_idle_not_jammed(tmp_path):
    """A change parked for a week is not stuck work: a stage, a holder or a block holding nothing else reads idle."""
    path = tmp_path / "l.db"
    with Ledger(path, clock=lambda: NOW) as led:
        for kind, payload in (("pr.opened", {"number": 1}), ("pr.author", {"number": 1, "by": "builder"})):
            led.append(
                {
                    "id": f"{kind}1",
                    "source": "import",
                    "kind": kind,
                    "subject_ref": "pr#1",
                    "at": "2026-11-01T03:00:00.000Z",
                    "payload": payload,
                }
            )
        z0 = derive.zoom0(led, NOW)
        z2 = derive.zoom2(led, NOW, "changes", "opened")
    block = next(b for b in z0["blocks"] if b["id"] == "changes")
    assert (block["colour"], block["label"], block["tally_text"]) == ("grey", "idle", "1 idle")
    assert block["reason"] is None and z0["signals"]["stuck"] == 0 and z0["signals"]["idle"] == 1
    assert z2["stage"]["colour"] == "grey" and z2["holders"][0]["colour"] == "grey"
    assert z2["grid"]["rows"][0]["cells"]["idle"]["colour"] == "grey" and z2["grid"]["bottleneck"] is None


def test_the_grid_leads_with_the_holder_the_reason_line_names_even_against_more_jams(tmp_path):
    """The reason line names the oldest jam; the grid ranks by how many. One old jam with one role and three younger
    ones with another would put them apart, so the grid leads with the row the reason line names (the lead's ruling
    on e320db4: the glance and the grid point at the same place)."""
    path = tmp_path / "l.db"
    with Ledger(path, clock=lambda: NOW) as led:
        for n, day, by in ((1, 11, "builder"), (2, 13, "reviewer"), (3, 13, "reviewer"), (4, 13, "reviewer")):
            for kind, payload in (("pr.opened", {"number": n}), ("pr.author", {"number": n, "by": by})):
                led.append(
                    {
                        "id": f"{kind}{n}",
                        "source": "import",
                        "kind": kind,
                        "subject_ref": f"pr#{n}",
                        "at": f"2026-11-{day:02d}T03:00:00.000Z",
                        "payload": payload,
                    }
                )
        z0 = derive.zoom0(led, NOW)
        g = derive.zoom2(led, NOW, "changes", "opened")["grid"]
    block = next(b for b in z0["blocks"] if b["id"] == "changes")
    assert (
        block["tally_text"].startswith("4 jammed") and block["reason"]["child"] == "pr#1"
    )  # 6 days, the oldest
    assert [r["holder"] for r in g["rows"]] == [
        "builder",
        "reviewer",
    ]  # not reviewer first, for its three jams
    assert [r["live_red"] for r in g["rows"]] == [1, 3]
