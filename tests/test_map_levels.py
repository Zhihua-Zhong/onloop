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
                assert not _ON_YOU.search(h["tally_text"]) and h["holder"] == "you, as the team marks it"


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
    assert [h["holder"] for h in review["holders"]][0] == "team"  # the worst first: the red one


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
